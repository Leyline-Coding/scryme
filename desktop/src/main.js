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
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");

// get-port is ESM-only (v7+, "type": "module"), so it can't be require()'d from this CommonJS
// file. Load it lazily via dynamic import() and cache it, then expose the same callable API.
let _getPortImpl;
async function getPort(options) {
  if (!_getPortImpl) _getPortImpl = (await import("get-port")).default;
  return _getPortImpl(options);
}

// embedded-postgres is ESM-only ("type": "module") too, so it can't be require()'d from this
// CommonJS file — it's loaded lazily via dynamic import() in startPostgres().

const isDev = !app.isPackaged;
const DB_USER = "scryme";
const DB_PASSWORD = "scryme";
const DB_NAME = "scryme";

let pg = null;
let backend = null;
let mainWindow = null;
let tray = null;
let quitting = false;     // the user actually chose to quit (vs. closing to the tray)
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
  try {
    saved = parseInt(fs.readFileSync(file, "utf8").trim(), 10);
  } catch (_) {
    saved = undefined;
  }
  // getPort returns the preferred port if available, else any free port.
  return getPort(saved ? { port: saved } : undefined).then((port) => {
    try { fs.writeFileSync(file, String(port)); } catch (_) { /* best-effort */ }
    return port;
  });
}

async function startPostgres(dir, port) {
  // ESM-only module — dynamic import() works from CommonJS; `.default` is the class.
  const { default: EmbeddedPostgres } = await import("embedded-postgres");
  const databaseDir = path.join(dir, "pg");
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
    // Database already exists — fine.
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
  backend = spawn(cmd, args, { ...opts, stdio: ["ignore", "pipe", "pipe"] });
  backend.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backend.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backend.on("exit", (code) => {
    if (code !== 0 && code !== null && !didShutdown) {
      dialog.showErrorBox("scryme", `The backend exited unexpectedly (code ${code}).`);
    }
  });
}

async function waitForHealth(port, timeoutMs = 60000) {
  const url = `http://127.0.0.1:${port}/health`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch (_) {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
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
  // Closing the window hides to the tray (so Postgres + backend keep running for a fast reopen)
  // when a tray exists; otherwise let it close so the app stays quittable.
  mainWindow.on("close", (e) => {
    if (!quitting && tray) { e.preventDefault(); mainWindow.hide(); }
  });
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
      { label: "Quit", click: () => { quitting = true; app.quit(); } },
    ]));
    tray.on("click", showWindow);
  } catch (_) {
    tray = null; // no system tray (some Linux DEs) — app simply quits on window close instead
  }
}

// In-app updates from GitHub Releases (production builds only). Best-effort: unpublished or
// unsigned builds just log and carry on.
function checkForUpdates() {
  if (isDev) return;
  let autoUpdater;
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (_) {
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
    process.stderr.write(`[updater] ${err && err.message ? err.message : err}\n`);
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
  const dir = dataDir();
  // Backend port is stable across launches (keeps renderer settings); the internal PG port is not.
  const [backendPort, pgPort] = await Promise.all([backendPortFor(dir), getPort()]);

  await startPostgres(dir, pgPort);
  startBackend(dir, backendPort, pgPort);

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
}

async function shutdown() {
  if (didShutdown) return;
  didShutdown = true;
  globalShortcut.unregisterAll();
  if (tray) { tray.destroy(); tray = null; }
  if (backend && !backend.killed) {
    backend.kill();
  }
  if (pg) {
    try { await pg.stop(); } catch (_) { /* ignore */ }
  }
}

app.whenReady().then(() => {
  // The application menu is built in createWindow() once the backend port is known.
  boot().catch((err) => {
    dialog.showErrorBox("scryme", `Failed to start: ${err && err.message ? err.message : err}`);
    app.quit();
  });
  app.on("activate", () => {
    // macOS dock click: re-show the hidden window if it still exists.
    if (mainWindow) showWindow();
  });
});

app.on("window-all-closed", () => {
  // With a tray the app keeps running in the background; without one, closing quits.
  if (!tray) app.quit();
});

app.on("before-quit", (e) => {
  quitting = true;
  if (didShutdown) return;
  e.preventDefault();
  shutdown().then(() => app.quit());
});
