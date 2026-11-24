// Electron main process for the scryme desktop app.
//
// On launch it: resolves a data directory, boots an embedded PostgreSQL on it, starts the backend
// sidecar (a PyInstaller binary in production, or `python -m src.desktop_entry` in dev) wired to
// that database, waits for the backend to be healthy, then opens a window pointed at it. On quit it
// stops the backend and the database. Everything lives under the user's data dir, so it can sit in
// a synced folder (Dropbox/Drive) and be backed up.

const {
  app, BrowserWindow, dialog, shell, Menu, globalShortcut, Tray, nativeImage, session,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const policy = require("./security");
const pgcluster = require("./pgcluster");

// Find a free TCP port on 127.0.0.1: prefer `preferred` if it's free, otherwise take any free port.
// A tiny built-in replacement for the get-port dependency (ESM-only, which forced a fragile dynamic
// import() from this CommonJS main process).
function getPort(preferred) {
  return new Promise((resolve, reject) => {
    const listen = (candidate, canFallback) => {
      const server = net.createServer();
      server.once("error", () => {
        if (canFallback) listen(0, false);   // preferred port busy → fall back to any free port
        else reject(new Error("could not find a free TCP port"));
      });
      server.listen(candidate, "127.0.0.1", () => {
        const { port } = server.address();
        server.close(() => resolve(port));
      });
    };
    const pref = Number(preferred) || 0;
    listen(pref, pref !== 0);
  });
}

// Ubuntu 24.04+ / Zorin 18 (and other AppArmor distros) restrict unprivileged user namespaces to
// programs with an AppArmor profile that grants `userns`. Chromium builds its sandbox from a user
// namespace, and an AppImage has neither a fixed install path to attach a profile to nor a SUID
// sandbox helper, so on those systems it cannot start sandboxed at all. Only in that case — the
// restriction is on AND this process is not running under a named profile — the sandbox is switched
// off so the app still launches. The .deb installs a profile (/etc/apparmor.d/scryme), runs
// under it, and keeps the sandbox.
//
// Residual risk when the switch is applied: a renderer compromise is no longer contained by the OS
// sandbox. What limits exposure is that the window can only ever show the local backend
// (navigation, redirects, window.open and <webview> are locked to the app's own origin below), the
// renderer has no Node integration and runs context-isolated, and web permissions are denied by
// default. The content is still not fully trusted — card text, imported files and deck names come
// from outside — so a rendering-engine bug reachable from that data would run unsandboxed there.
// Prefer the .deb on affected distros.
const _USERNS_FLAG = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns";
function readProc(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return null;   // absent, or AppArmor not enabled (EINVAL on /proc/self/attr/current)
  }
}
if (process.platform === "linux"
    && policy.needsNoSandbox(readProc(_USERNS_FLAG), readProc("/proc/self/attr/current"))) {
  process.stderr.write("[sandbox] unprivileged user namespaces are restricted and no AppArmor "
    + "profile is attached; starting without the Chromium sandbox\n");
  app.commandLine.appendSwitch("no-sandbox");
}

// embedded-postgres is ESM-only ("type": "module") too, so it can't be require()'d from this
// CommonJS file — it's loaded lazily via dynamic import() in startPostgres().

const isDev = !app.isPackaged;
const DB_USER = "scryme";
const DB_NAME = "scryme";

let pg = null;
let dbPassword = null;      // per-install random password (see pgcluster.js)
let backend = null;
let backendExited = false;  // the backend child has fired its "exit" event
let backendRestarting = false;  // a deliberate restart is in progress (LAN sharing toggled)
let backendLanEnabled = false;  // the LAN-sharing state the running backend was started with
let appOrigin = null;       // http://127.0.0.1:<backend port>, once known
let mainWindow = null;
let tray = null;
let didShutdown = false;  // PG + backend already stopped (cleanup is idempotent)

function dataDir() {
  // Override with SCRYME_DESKTOP_DATA_DIR to point at e.g. a synced folder.
  const dir = process.env.SCRYME_DESKTOP_DATA_DIR || path.join(app.getPath("userData"), "scryme-data");
  // Owner-only (0700), tightening directories created by earlier versions: the folder holds the
  // database, its credentials file and the LAN-sharing state.
  for (const sub of ["", "pg", "images", "files"]) {
    pgcluster.ensurePrivateDir(path.join(dir, sub), (m) => process.stderr.write(`${m}\n`));
  }
  return dir;
}

// The window loads http://127.0.0.1:<backendPort>, and theme/currency settings live in the
// renderer's localStorage/cookies — which are keyed by origin (and thus port). A fresh random port
// each launch would silently wipe those settings, so we persist the port and reuse it when free.
function backendPortFor(dir) {
  const file = path.join(dir, "backend-port");
  let saved;
  if (fs.existsSync(file)) {
    saved = Number.parseInt(fs.readFileSync(file, "utf8").trim(), 10);
  }
  // getPort returns the preferred port if available, else any free port.
  return getPort(saved).then((port) => {
    try {
      fs.writeFileSync(file, String(port));
    } catch (err) {
      process.stderr.write(`[port] could not persist backend port: ${err}\n`);
    }
    return port;
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isAlive(pid) {
  try {
    process.kill(pid, 0);       // signal 0 just probes; it doesn't actually kill
    return true;
  } catch (err) {
    return err.code === "EPERM";  // EPERM = exists but not ours (alive); ESRCH = gone
  }
}

// Confirm a PID is actually a Postgres serving this data dir, to guard against killing an unrelated
// process that happens to have reused the recorded PID. (Linux: check /proc; trust the pidfile
// elsewhere — PID reuse on Windows/macOS in this window is negligible.)
function isOurPostgres(pid, databaseDir) {
  if (process.platform !== "linux") return true;
  try {
    const cmdline = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8");
    return cmdline.includes("postgres") && cmdline.includes(databaseDir);
  } catch (err) {
    return err.code !== "ENOENT";  // ENOENT = process already gone
  }
}

// SIGTERM an orphaned postgres, wait for it to exit, then SIGKILL if it won't.
async function stopOrphanedPostgres(pid) {
  process.stderr.write(`[db] reclaiming data dir: stopping orphaned postgres (pid ${pid})\n`);
  try {
    process.kill(pid, "SIGTERM");
  } catch (err) {
    process.stderr.write(`[db] SIGTERM pid ${pid}: ${err}\n`);
  }
  for (let i = 0; i < 40 && isAlive(pid); i++) await delay(250);   // wait up to ~10s to exit
  if (!isAlive(pid)) return;
  try {
    process.kill(pid, "SIGKILL");
  } catch (err) {
    process.stderr.write(`[db] SIGKILL pid ${pid}: ${err}\n`);
  }
  for (let i = 0; i < 20 && isAlive(pid); i++) await delay(250);   // let SIGKILL take effect
}

// We only reach here holding the single-instance lock, so no other scryme owns this data dir. A
// leftover postmaster.pid therefore belongs to a previous run that crashed or was force-killed
// without cleaning up — stop that orphaned Postgres, then clear the lock so we can start.
async function reclaimDataDir(databaseDir) {
  const pidFile = path.join(databaseDir, "postmaster.pid");
  if (!fs.existsSync(pidFile)) return;
  const pid = Number.parseInt((fs.readFileSync(pidFile, "utf8").split("\n")[0] || "").trim(), 10);
  if (pid > 0 && isAlive(pid) && isOurPostgres(pid, databaseDir)) {
    await stopOrphanedPostgres(pid);
  }
  // Only clear the lock once no live postmaster remains. Never start a second postmaster on a data
  // dir another one still holds — that corrupts it; leave the lock so Postgres fails loudly instead.
  if (pid <= 0 || !isAlive(pid)) {
    try {
      fs.rmSync(pidFile, { force: true });
    } catch (err) {
      process.stderr.write(`[db] could not remove stale postmaster.pid: ${err}\n`);
    }
  }
}

// embedded-postgres spawns its bundled binaries by a path derived from its own location, and a
// path inside app.asar is not a real file. In a packaged app the library (and its dependencies) are
// therefore asar-unpacked and imported from app.asar.unpacked on the real filesystem.
function embeddedPostgresSpecifier() {
  if (isDev) return "embedded-postgres";
  return pathToFileURL(path.join(
    process.resourcesPath, "app.asar.unpacked", "node_modules", "embedded-postgres", "dist", "index.js",
  )).href;
}

async function startPostgres(dir, port) {
  // ESM-only module — dynamic import() works from CommonJS; `.default` is the class.
  const { default: EmbeddedPostgres } = await import(embeddedPostgresSpecifier());
  const databaseDir = path.join(dir, "pg");
  await reclaimDataDir(databaseDir);   // self-heal a Postgres left running by a crashed run
  // Creates the cluster on first run, replaces the old fixed password with a per-install random
  // one (stored 0600 next to the cluster), listens on 127.0.0.1 only, and creates the database.
  ({ pg, password: dbPassword } = await pgcluster.startCluster({
    EmbeddedPostgres,
    databaseDir,
    credentialsFile: path.join(dir, pgcluster.CREDENTIALS_FILE),
    port,
    user: DB_USER,
    dbName: DB_NAME,
    log: (m) => process.stderr.write(`${m}\n`),
  }));
}

function lanFile(dir) {
  return path.join(dir, "files", "lan.json");   // SCRYME_DATA_DIR/lan.json — see backend/src/lan.py
}

function backendCommand(dir, backendPort, pgPort) {
  // An explicit environment: SCRYME_* settings from the launching shell are not forwarded (bar a
  // short allow-list in security.js), so e.g. an exported SCRYME_LAN_GUARD=0 cannot switch the
  // guard off. desktop_entry.py forces the guard on as well and picks the bind address itself.
  const env = policy.backendEnv(process.env, {
    SCRYME_ENVIRONMENT: "production",
    SCRYME_PORT: String(backendPort),
    SCRYME_DATABASE_URL: `postgresql+asyncpg://${DB_USER}:${dbPassword}@127.0.0.1:${pgPort}/${DB_NAME}`,
    SCRYME_DATA_DIR: path.join(dir, "files"),
    SCRYME_IMAGE_CACHE_DIR: path.join(dir, "images"),
    SCRYME_LAN_GUARD: "1",
  });
  if (isDev) {
    // Dev: run the Python backend from ../backend (set SCRYME_PYTHON to a venv interpreter).
    // Resolve a relative SCRYME_PYTHON against the launch dir so spawn (which runs with the backend
    // as cwd) still finds it; a bare "python3" is left to PATH lookup.
    let python = process.env.SCRYME_PYTHON || "python3";
    if (python.includes("/") && !path.isAbsolute(python)) {
      python = path.resolve(process.cwd(), python);
    }
    return { cmd: python, args: ["-m", "src.desktop_entry"],
             opts: { cwd: path.resolve(__dirname, "../../backend"), env } };
  }
  // Production: the frozen single-binary backend bundled as an extra resource.
  const exe = process.platform === "win32" ? "scryme-backend.exe" : "scryme-backend";
  const bin = path.join(process.resourcesPath, "backend", exe);
  // Run from the private data folder rather than wherever the app was launched, so a stray `.env`
  // in the launch directory is not picked up by the backend's settings loader.
  return { cmd: bin, args: [], opts: { env, cwd: path.join(dir, "files") } };
}

function startBackend(dir, backendPort, pgPort) {
  const { cmd, args, opts } = backendCommand(dir, backendPort, pgPort);
  // Own process group on POSIX (detached) so shutdown can signal the whole tree at once — a
  // PyInstaller onefile backend re-execs a child, which a plain backend.kill() would leave running.
  // The backend binds 0.0.0.0 only when LAN sharing is on at startup (desktop_entry.py reads the
  // same file); remember what this instance was started with so a later toggle can be detected.
  backendLanEnabled = policy.readLanEnabled(lanFile(dir));
  const child = spawn(cmd, args, {
    ...opts,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  backend = child;
  backendExited = false;
  child.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  child.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  child.on("error", (err) => {
    // e.g. the frozen backend binary is missing/not executable — surface it instead of hanging.
    if (backend === child && !didShutdown) {
      dialog.showErrorBox("scryme", `The backend failed to launch: ${err?.message || err}`);
    }
  });
  child.on("exit", (code) => {
    if (backend !== child) return;   // an earlier instance that has already been replaced
    backendExited = true;
    if (code !== 0 && code !== null && !didShutdown && !backendRestarting) {
      dialog.showErrorBox("scryme", `The backend exited unexpectedly (code ${code}).`);
    }
  });
}

// Stop the running backend and wait for it to be gone (SIGTERM, then SIGKILL after ~3s).
async function stopBackend() {
  killBackend("SIGTERM");
  for (let i = 0; i < 12 && !backendExited; i++) await delay(250);
  if (!backendExited) killBackend("SIGKILL");
  for (let i = 0; i < 20 && !backendExited; i++) await delay(250);
}

// Windows has no process groups: kill the whole tree with taskkill /T. Resolve taskkill by its
// absolute path in System32 so a writable PATH entry can't shadow it with a malicious binary.
function taskkillTree(pid) {
  const exe = path.join(process.env.SystemRoot || "C:/Windows", "System32", "taskkill.exe");
  spawn(exe, ["/pid", String(pid), "/T", "/F"], { stdio: "ignore" });
}

// Stop the backend and every process it spawned. POSIX: signal the detached child's whole process
// group (negative PID). Windows has no groups, so kill the tree with taskkill /T.
function killBackend(signal) {
  if (!backend || backendExited || !backend.pid) return;
  try {
    if (process.platform === "win32") {
      taskkillTree(backend.pid);
    } else {
      process.kill(-backend.pid, signal);
    }
  } catch (err) {
    process.stderr.write(`[backend] kill (${signal}): ${err}\n`);
  }
}

async function waitForHealth(port, timeoutMs = 60000) {
  const url = `http://127.0.0.1:${port}/health`;
  const deadline = Date.now() + timeoutMs;
  let lastErr;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch (err) {
      lastErr = err;  // connection refused until the backend binds its port
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  if (lastErr) process.stderr.write(`[backend] health check never succeeded: ${lastErr}\n`);
  return false;
}

// Hand a URL to the OS — https: only. Every external open in this file goes through here; file:,
// smb:, ms-*: and other protocol handlers are never passed to shell.openExternal.
function openExternalSafe(url) {
  if (!policy.isSafeExternalUrl(url)) {
    process.stderr.write(`[nav] refused to open a non-https URL externally: ${String(url).slice(0, 200)}\n`);
    return;
  }
  shell.openExternal(url).catch((err) => process.stderr.write(`[nav] openExternal failed: ${err}\n`));
}

// Applied to every webContents the app ever creates (main window, child windows, anything else):
// the app's own origin stays in the app, https: links go to the user's browser, the rest is dropped.
function lockDownWebContents(contents) {
  const guard = (event, url) => {
    const decision = policy.navigationDecision(url, appOrigin);
    if (decision === "allow") return;
    event.preventDefault();
    if (decision === "external") openExternalSafe(url);
    else process.stderr.write(`[nav] blocked navigation to ${String(url).slice(0, 200)}\n`);
  };
  contents.on("will-navigate", (event, url) => guard(event, event.url || url));
  contents.on("will-redirect", (event, url) => guard(event, event.url || url));
  // Sub-frames (the main frame is covered above): only the app itself, never the browser.
  contents.on("will-frame-navigate", (event) => {
    if (event.isMainFrame) return;
    if (!policy.frameNavigationAllowed(event.url, appOrigin)) event.preventDefault();
  });
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    const decision = policy.navigationDecision(url, appOrigin);
    if (decision === "allow") {
      // The app's own pages opened with target="_blank" (printable proxies, grade photos) get a
      // plain child window with the same locked-down preferences.
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          width: 1024,
          height: 800,
          backgroundColor: "#020617",
          autoHideMenuBar: true,
          webPreferences: {
            contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: false,
          },
        },
      };
    }
    if (decision === "external") openExternalSafe(url);
    else process.stderr.write(`[nav] blocked window.open for ${String(url).slice(0, 200)}\n`);
    return { action: "deny" };
  });
}
app.on("web-contents-created", (_event, contents) => lockDownWebContents(contents));

// Web permissions are denied unless security.js allows them for the app's own origin (clipboard
// write for the copy buttons, notifications for alerts). No camera, microphone, geolocation,
// device access, or protocol-handler launches from page content.
function lockDownPermissions(ses) {
  ses.setPermissionRequestHandler((contents, permission, callback, details) => {
    const origin = (details && details.requestingUrl) || (contents && contents.getURL()) || "";
    callback(policy.permissionAllowed(permission, origin, appOrigin));
  });
  ses.setPermissionCheckHandler((contents, permission, requestingOrigin) =>
    policy.permissionAllowed(permission, requestingOrigin || (contents && contents.getURL()) || "", appOrigin));
  ses.setDevicePermissionHandler(() => false);
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    title: "scryme",
    icon: path.join(__dirname, "..", "build", "icon.png"),
    backgroundColor: "#020617",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  // Link handling, navigation limits and <webview> denial are installed for every webContents in
  // lockDownWebContents() (see the web-contents-created hook below).
  mainWindow.loadURL(`${appOrigin}/`);
  // Closing the window quits the app so the embedded Postgres + backend are always torn down —
  // leaving them running orphans the data-dir lock and blocks the next launch.
  mainWindow.on("closed", () => { mainWindow = null; });
  buildMenu(port);
}

function showWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createTray(port) {
  try {
    let icon = nativeImage.createFromPath(path.join(__dirname, "..", "build", "icon.png"));
    if (!icon.isEmpty()) icon = icon.resize({ width: 18, height: 18 });
    tray = new Tray(icon);
    tray.setToolTip("scryme");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "Show scryme", click: showWindow },
      {
        label: "Share on LAN…",
        click: () => { showWindow(); if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${port}/lan`); },
      },
      { type: "separator" },
      { label: "Quit", click: () => app.quit() },
    ]));
    tray.on("click", showWindow);
  } catch (err) {
    // No system tray (some Linux DEs) — app simply quits on window close instead.
    tray = null;
    process.stderr.write(`[tray] unavailable: ${err}\n`);
  }
}

// Update *notification* from GitHub Releases (packaged builds only).
//
// scryme's installers are not code-signed, so the only check electron-updater could make on a
// downloaded update is a SHA-512 from latest*.yml — a file stored in the same GitHub release as the
// binary, which proves nothing if that release is tampered with. The app therefore never downloads
// or installs an update itself (autoDownload / autoInstallOnAppQuit off, quitAndInstall never
// called). It only reads the feed to learn that a newer version exists and offers to open the
// release page in the browser, where the user downloads it deliberately. That is also the only
// thing that can work on macOS, where an unsigned app cannot self-update at all. See
// desktop/README.md ("Releases & updates") for what restoring in-app updates would take.
let updater = null;
let manualUpdateCheck = false;   // the check in flight was started from the menu → report every outcome

function showMessage(options) {
  return mainWindow ? dialog.showMessageBox(mainWindow, options) : dialog.showMessageBox(options);
}

async function offerUpdate(version) {
  const { response } = await showMessage({
    type: "info",
    buttons: ["Open download page", "Later"],
    defaultId: 0,
    cancelId: 1,
    message: `scryme ${version} is available.`,
    detail: `You have ${app.getVersion()}. scryme does not install updates by itself: the download `
      + "page opens in your browser, and you install the new version from there.",
  });
  if (response === 0) openExternalSafe(policy.releaseUrlFor(version));
}

async function reportUpdateProblem(reason) {
  const { response } = await showMessage({
    type: "warning",
    buttons: ["Open releases page", "Close"],
    defaultId: 0,
    cancelId: 1,
    message: "Couldn't check for updates.",
    detail: `${reason}\n\nYou can look for a newer version on the releases page.`,
  });
  if (response === 0) openExternalSafe(policy.releaseUrlFor(null));
}

function getUpdater() {
  if (updater) return updater;
  const { autoUpdater } = require("electron-updater");
  autoUpdater.autoDownload = false;            // never fetch an installer in the background
  autoUpdater.autoInstallOnAppQuit = false;    // never install one on quit
  autoUpdater.on("update-available", (info) => {
    manualUpdateCheck = false;
    offerUpdate(String(info && info.version)).catch((err) => process.stderr.write(`[updater] ${err}\n`));
  });
  autoUpdater.on("update-not-available", () => {
    if (!manualUpdateCheck) return;
    manualUpdateCheck = false;
    showMessage({ type: "info", message: `scryme ${app.getVersion()} is the latest version.` });
  });
  autoUpdater.on("error", (err) => {
    const reason = err?.message ? err.message : String(err);
    process.stderr.write(`[updater] ${reason}\n`);
    if (!manualUpdateCheck) return;
    manualUpdateCheck = false;
    reportUpdateProblem(reason.split("\n")[0].slice(0, 300));
  });
  updater = autoUpdater;
  return updater;
}

// `manual` = started from the menu: say so when there is nothing new or the check fails. The
// launch-time check stays quiet unless an update exists.
async function checkForUpdates({ manual = false } = {}) {
  if (isDev) {
    if (manual) await reportUpdateProblem("Update checks only run in packaged builds.");
    return;
  }
  try {
    manualUpdateCheck = manual;
    const result = await getUpdater().checkForUpdates();
    // null = the updater considers itself inactive for this kind of install (e.g. an unpacked
    // Linux build); no event fires in that case.
    if (result == null && manualUpdateCheck) {
      manualUpdateCheck = false;
      await reportUpdateProblem("This build can't read the update feed.");
    }
  } catch (err) {
    // electron-updater also emits "error" for a failed check, which reports a manual one.
    process.stderr.write(`[updater] check failed: ${err?.message ? err.message : err}\n`);
    if (manualUpdateCheck) {
      manualUpdateCheck = false;
      await reportUpdateProblem(String(err?.message || err).split("\n")[0].slice(0, 300));
    }
  }
}

// LAN sharing rebinding. The backend's listener cannot be moved between 127.0.0.1 and 0.0.0.0 while
// it runs, so desktop_entry.py picks the bind address once at startup from lan.json. The /lan page
// (POST /lan/toggle) only rewrites that file; this poll notices that the stored state no longer
// matches what the running backend was started with, restarts the sidecar on the same port (so the
// window's origin, and with it localStorage/cookies, is unchanged) and reloads the window.
// Until the restart lands the old listener is still in place, which is safe in both directions:
// turning sharing on just isn't reachable yet, and turning it off is enforced immediately by the
// backend's LAN guard (it reads lan.json per request) even while the socket is still on 0.0.0.0.
const LAN_SPLASH = "data:text/html;charset=utf-8," + encodeURIComponent(
  "<!doctype html><meta charset=utf-8><title>scryme</title>"
  + "<body style=\"margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
  + "background:#020617;color:#cbd5e1;font:16px system-ui,sans-serif\">"
  + "Applying the LAN sharing change…</body>");
let lanTimer = null;
let lanApplying = false;

async function applyLanChange(dir, backendPort, pgPort) {
  const current = mainWindow ? mainWindow.webContents.getURL() : "";
  const resume = policy.isAppUrl(current, appOrigin) && !current.includes("/lan/")
    ? current : `${appOrigin}/lan`;
  backendRestarting = true;
  try {
    if (mainWindow) await mainWindow.loadURL(LAN_SPLASH).catch(() => {});
    await stopBackend();
    if (!backendExited) throw new Error("the backend did not stop");
    if (didShutdown) return;   // the app is quitting; don't start a backend nobody will stop
    startBackend(dir, backendPort, pgPort);   // re-reads lan.json and records the new state
  } finally {
    backendRestarting = false;
  }
  if (didShutdown) return;
  if (!(await waitForHealth(backendPort))) throw new Error("the backend did not come back up");
  process.stderr.write(`[lan] backend restarted; LAN sharing is ${backendLanEnabled ? "on" : "off"}\n`);
  if (mainWindow) mainWindow.loadURL(resume).catch(() => {});
}

function watchLanState(dir, backendPort, pgPort) {
  const file = lanFile(dir);
  lanTimer = setInterval(() => {
    if (lanApplying || didShutdown) return;
    if (policy.readLanEnabled(file) === backendLanEnabled) return;
    lanApplying = true;
    applyLanChange(dir, backendPort, pgPort)
      .catch((err) => {
        if (didShutdown) return;
        process.stderr.write(`[lan] restart failed: ${err?.stack || err}\n`);
        dialog.showErrorBox("scryme",
          `scryme couldn't restart its backend to apply the LAN sharing change (${err?.message || err}). `
          + "The app will close; start it again to continue.");
        app.quit();
      })
      .finally(() => { lanApplying = false; });
  }, 1000);
}

function buildMenu(port) {
  const go = (p) => () => { if (mainWindow) mainWindow.loadURL(`http://127.0.0.1:${port}${p}`); };
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: "appMenu" },
    {
      label: "File",
      submenu: [
        { label: "Home", accelerator: "CommandOrControl+H", click: go("/") },
        { label: "Import collection…", click: go("/upload") },
        { label: "Share on LAN…", click: go("/lan") },
        { type: "separator" },
        { role: "quit" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        { label: "Check for Updates…", click: () => { checkForUpdates({ manual: true }); } },
        { label: "Releases on GitHub", click: () => openExternalSafe(policy.releaseUrlFor(null)) },
      ],
    },
  ]));
}

// Global quick-search: a system-wide hotkey raises the window and focuses the search box.
const QUICK_SEARCH_ACCELERATOR = "CommandOrControl+Shift+S";
function registerShortcuts() {
  globalShortcut.register(QUICK_SEARCH_ACCELERATOR, () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    mainWindow.webContents.send("scryme:focus-search");
  });
}

async function boot() {
  let step = "preparing the data folder";
  try {
    const dir = dataDir();
    // Backend port is stable across launches (keeps renderer settings); the PG port is not.
    step = "resolving ports";
    const [backendPort, pgPort] = await Promise.all([backendPortFor(dir), getPort()]);
    appOrigin = policy.appOriginFor(backendPort);
    lockDownPermissions(session.defaultSession);
    step = "starting the embedded database";
    await startPostgres(dir, pgPort);
    step = "starting the backend";
    startBackend(dir, backendPort, pgPort);
    step = "waiting for the backend to become healthy";
    const healthy = await waitForHealth(backendPort);
    if (!healthy) {
      dialog.showErrorBox("scryme", "The backend didn't start in time. See the logs for details.");
      app.quit();
      return;
    }
    createWindow(backendPort);
    createTray(backendPort);
    registerShortcuts();
    watchLanState(dir, backendPort, pgPort);
    checkForUpdates();
  } catch (err) {
    // Attribute the failure to a step and keep the stack, so it's never reported as bare "undefined".
    throw new Error(`while ${step}: ${(err && (err.stack || err.message)) || err}`);
  }
}

async function shutdown() {
  if (didShutdown) return;
  didShutdown = true;
  if (lanTimer) { clearInterval(lanTimer); lanTimer = null; }
  globalShortcut.unregisterAll();
  if (tray) { tray.destroy(); tray = null; }
  // Ask the backend to exit, then escalate to SIGKILL if it lingers, so it never outlives the app.
  killBackend("SIGTERM");
  for (let i = 0; i < 12 && !backendExited; i++) await delay(250);   // up to ~3s to exit cleanly
  if (!backendExited) killBackend("SIGKILL");
  if (pg) {
    // Bound pg.stop() so a hung shutdown can't wedge quitting. If it doesn't finish, we quit anyway
    // — reclaimDataDir() clears any leftover lock on the next launch.
    try {
      await Promise.race([pg.stop(), delay(8000)]);
    } catch (err) {
      process.stderr.write(`[pg] stop failed: ${err}\n`);
    }
  }
}

// Single-instance guard: two scryme processes would fight over the embedded Postgres data
// directory (its postmaster.pid lock), so the second launch fails to start. Instead, hand off to
// the already-running instance — focus its window — and exit immediately.
if (app.requestSingleInstanceLock()) {
  app.on("second-instance", () => showWindow());

  app.whenReady().then(() => {
    // The application menu is built in createWindow() once the backend port is known.
    boot().catch((err) => {
      const detail = (err && (err.stack || err.message)) || String(err);
      try {
        fs.writeFileSync(path.join(dataDir(), "startup-error.log"),
          `${new Date().toISOString()}\n${detail}\n`);
      } catch (logErr) {
        process.stderr.write(`[scryme] could not write startup-error.log: ${logErr}\n`);
      }
      process.stderr.write(`[scryme] startup failed: ${detail}\n`);
      dialog.showErrorBox(
        "scryme — couldn't start",
        `scryme couldn't start:\n\n${err?.message || detail}\n\n` +
        "Full details were saved to startup-error.log in the app's data folder.",
      );
      app.quit();
    });
    app.on("activate", () => {
      // macOS dock click: re-show the hidden window if it still exists.
      if (mainWindow) showWindow();
    });
  });
} else {
  // Another scryme already holds the lock; hand off to it (via second-instance) and exit.
  app.quit();
}

app.on("window-all-closed", () => {
  // Quit on every platform (before-quit → shutdown() stops PG + backend) so nothing is orphaned.
  app.quit();
});

app.on("before-quit", (e) => {
  if (didShutdown) return;
  e.preventDefault();
  shutdown().then(() => app.quit());
});

// Route OS termination signals (terminal close, `kill`, logout) through the same graceful teardown
// as a window close — otherwise Electron exits without firing before-quit and orphans the backend.
for (const sig of ["SIGTERM", "SIGINT", "SIGHUP"]) {
  process.on(sig, () => app.quit());
}

// Last-resort synchronous cleanup: if we ever exit without shutdown() completing (an uncaught
// fault), still hard-kill the backend's process group so it can't outlive the app.
process.on("exit", () => {
  if (!backend || backendExited || !backend.pid) return;
  try {
    if (process.platform === "win32") {
      taskkillTree(backend.pid);
    } else {
      process.kill(-backend.pid, "SIGKILL");
    }
  } catch (err) {
    process.stderr.write(`[backend] exit cleanup: ${err}\n`);
  }
});
