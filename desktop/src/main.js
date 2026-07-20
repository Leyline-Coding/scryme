// Electron main process for the scryme desktop app.
//
// On launch it: resolves a data directory, boots an embedded PostgreSQL on it, starts the backend
// sidecar (a PyInstaller binary in production, or `python -m src.desktop_entry` in dev) wired to
// that database, waits for the backend to be healthy, then opens a window pointed at it. On quit it
// stops the backend and the database. Everything lives under the user's data dir, so it can sit in
// a synced folder (Dropbox/Drive) and be backed up.

const {
  app, BrowserWindow, dialog, shell, Menu, globalShortcut, Tray, nativeImage,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const net = require("node:net");
const { spawn } = require("node:child_process");

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

// Ubuntu 24.04 / Zorin 18 (and other recent AppArmor distros) restrict unprivileged user namespaces,
// which breaks Chromium's sandbox for AppImages — there's no SUID sandbox helper to fall back to, so
// the app fails to launch. When that restriction is on, disable the sandbox so it still starts;
// scryme only ever loads its own localhost backend, so the renderer sees no untrusted web content.
const _USERNS_FLAG = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns";
if (process.platform === "linux" && fs.existsSync(_USERNS_FLAG)) {
  try {
    if (fs.readFileSync(_USERNS_FLAG, "utf8").trim() === "1") {
      app.commandLine.appendSwitch("no-sandbox");
    }
  } catch (err) {
    process.stderr.write(`[sandbox] userns check failed: ${err}\n`);
  }
}

// embedded-postgres is ESM-only ("type": "module") too, so it can't be require()'d from this
// CommonJS file — it's loaded lazily via dynamic import() in startPostgres().

const isDev = !app.isPackaged;
const DB_USER = "scryme";
const DB_PASSWORD = "scryme";
const DB_NAME = "scryme";

let pg = null;
let backend = null;
let backendExited = false;  // the backend child has fired its "exit" event
let mainWindow = null;
let tray = null;
let didShutdown = false;  // PG + backend already stopped (cleanup is idempotent)

function dataDir() {
  // Override with SCRYME_DESKTOP_DATA_DIR to point at e.g. a synced folder.
  const dir = process.env.SCRYME_DESKTOP_DATA_DIR || path.join(app.getPath("userData"), "scryme-data");
  for (const sub of ["", "pg", "images", "files"]) {
    fs.mkdirSync(path.join(dir, sub), { recursive: true });
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

async function startPostgres(dir, port) {
  // ESM-only module — dynamic import() works from CommonJS; `.default` is the class.
  const { default: EmbeddedPostgres } = await import("embedded-postgres");
  const databaseDir = path.join(dir, "pg");
  await reclaimDataDir(databaseDir);   // self-heal a Postgres left running by a crashed run
  pg = new EmbeddedPostgres({
    databaseDir,
    user: DB_USER,
    password: DB_PASSWORD,
    port,
    persistent: true,
  });
  // initialise() creates the cluster on first run; PG_VERSION marks an existing one.
  if (!fs.existsSync(path.join(databaseDir, "PG_VERSION"))) {
    await pg.initialise();
  }
  await pg.start();
  try {
    await pg.createDatabase(DB_NAME);
  } catch (err) {
    // "Database already exists" is expected on every launch after the first; surface anything else.
    if (!/exist/i.test(String(err))) {
      process.stderr.write(`[db] createDatabase failed: ${err}\n`);
    }
  }
}

function backendCommand(dir, backendPort, pgPort) {
  const env = {
    ...process.env,
    SCRYME_ENVIRONMENT: "production",
    SCRYME_PORT: String(backendPort),
    SCRYME_DATABASE_URL: `postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${pgPort}/${DB_NAME}`,
    SCRYME_DATA_DIR: path.join(dir, "files"),
    SCRYME_IMAGE_CACHE_DIR: path.join(dir, "images"),
  };
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
  return { cmd: bin, args: [], opts: { env } };
}

function startBackend(dir, backendPort, pgPort) {
  const { cmd, args, opts } = backendCommand(dir, backendPort, pgPort);
  // Own process group on POSIX (detached) so shutdown can signal the whole tree at once — a
  // PyInstaller onefile backend re-execs a child, which a plain backend.kill() would leave running.
  backend = spawn(cmd, args, {
    ...opts,
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  backendExited = false;
  backend.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backend.on("error", (err) => {
    // e.g. the frozen backend binary is missing/not executable — surface it instead of hanging.
    if (!didShutdown) {
      dialog.showErrorBox("scryme", `The backend failed to launch: ${err?.message || err}`);
    }
  });
  backend.on("exit", (code) => {
    backendExited = true;
    if (code !== 0 && code !== null && !didShutdown) {
      dialog.showErrorBox("scryme", `The backend exited unexpectedly (code ${code}).`);
    }
  });
}

// Windows has no process groups: kill the whole tree with taskkill /T. Resolve taskkill by its
// absolute path in System32 so a writable PATH entry can't shadow it with a malicious binary.
function taskkillTree(pid) {
  const exe = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe");
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
      preload: path.join(__dirname, "preload.js"),
    },
  });
  // Open external links in the user's browser, not a new app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.loadURL(`http://127.0.0.1:${port}/`);
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

// In-app updates from GitHub Releases (production builds only). Best-effort: unpublished or
// unsigned builds just log and carry on.
function checkForUpdates() {
  if (isDev) return;
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (err) {
    process.stderr.write(`[updater] unavailable: ${err}\n`);
    return;
  }
  autoUpdater.on("update-downloaded", async (info) => {
    const { response } = await dialog.showMessageBox(mainWindow, {
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      message: `scryme ${info.version} is ready to install.`,
      detail: "Restart to finish updating.",
    });
    if (response === 0) {
      await shutdown();        // stop PG + backend cleanly before the updater relaunches
      autoUpdater.quitAndInstall();
    }
  });
  autoUpdater.on("error", (err) => {
    process.stderr.write(`[updater] ${err?.message ? err.message : err}\n`);
  });
  autoUpdater.checkForUpdates().catch(() => { /* best-effort */ });
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
    checkForUpdates();
  } catch (err) {
    // Attribute the failure to a step and keep the stack, so it's never reported as bare "undefined".
    throw new Error(`while ${step}: ${(err && (err.stack || err.message)) || err}`);
  }
}

async function shutdown() {
  if (didShutdown) return;
  didShutdown = true;
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
