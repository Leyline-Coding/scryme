// Electron main process for the scryme desktop app.
//
// On launch it: resolves a data directory, boots an embedded PostgreSQL on it, starts the backend
// sidecar (a PyInstaller binary in production, or `python -m src.desktop_entry` in dev) wired to
// that database, waits for the backend to be healthy, then opens a window pointed at it. On quit it
// stops the backend and the database. Everything lives under the user's data dir, so it can sit in
// a synced folder (Dropbox/Drive) and be backed up.

const { app, BrowserWindow, dialog, shell, Menu } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const getPort = require("get-port");

// embedded-postgres is ESM-only ("type": "module"), so it can't be require()'d from this CommonJS
// file — it's loaded lazily via dynamic import() in startPostgres().

const isDev = !app.isPackaged;
const DB_USER = "scryme";
const DB_PASSWORD = "scryme";
const DB_NAME = "scryme";

let pg = null;
let backend = null;
let mainWindow = null;

function dataDir() {
  // Override with SCRYME_DESKTOP_DATA_DIR to point at e.g. a synced folder.
  const dir = process.env.SCRYME_DESKTOP_DATA_DIR || path.join(app.getPath("userData"), "scryme-data");
  for (const sub of ["", "pg", "images", "files"]) {
    fs.mkdirSync(path.join(dir, sub), { recursive: true });
  }
  return dir;
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
    if (code !== 0 && code !== null && !app.isQuitting) {
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
  mainWindow.on("closed", () => { mainWindow = null; });
}

async function boot() {
  const dir = dataDir();
  const [backendPort, pgPort] = await Promise.all([getPort(), getPort()]);

  await startPostgres(dir, pgPort);
  startBackend(dir, backendPort, pgPort);

  const healthy = await waitForHealth(backendPort);
  if (!healthy) {
    dialog.showErrorBox("scryme", "The backend didn't start in time. See the logs for details.");
    app.quit();
    return;
  }
  createWindow(backendPort);
}

async function shutdown() {
  app.isQuitting = true;
  if (backend && !backend.killed) {
    backend.kill();
  }
  if (pg) {
    try { await pg.stop(); } catch (_) { /* ignore */ }
  }
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: "appMenu" },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ]));
  boot().catch((err) => {
    dialog.showErrorBox("scryme", `Failed to start: ${err && err.message ? err.message : err}`);
    app.quit();
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0 && mainWindow === null) {
      // Window was closed but app still running; nothing to re-create without the port — quit.
    }
  });
});

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", async (e) => {
  if (!app.isQuitting) {
    e.preventDefault();
    await shutdown();
    app.quit();
  }
});
