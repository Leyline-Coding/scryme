# scryme desktop

A native desktop wrapper around scryme. It bundles a portable PostgreSQL and the scryme backend, so
there's nothing to install and no Docker required — double-click and your collection opens in a
window. The same FastAPI app the web/Docker build serves runs locally on `127.0.0.1`.

## How it works

```
Electron (src/main.js)
  ├─ boots embedded PostgreSQL  ──► <userData>/scryme-data/pg
  ├─ spawns the backend sidecar ──► alembic upgrade head, then uvicorn on a free port
  │     dev:  python -m src.desktop_entry   (from ../backend)
  │     prod: resources/backend/scryme-backend   (PyInstaller binary)
  ├─ waits for GET /health
  └─ opens a BrowserWindow at http://127.0.0.1:<port>/
```

All state lives under one data directory (Postgres cluster, cached images, backups):

- macOS: `~/Library/Application Support/scryme/scryme-data`
- Linux: `~/.config/scryme/scryme-data`
- Windows: `%APPDATA%\scryme\scryme-data`

Override it with `SCRYME_DESKTOP_DATA_DIR` (e.g. point it at a synced folder).

## Develop

Runs the real Python backend from `../backend` — no freeze needed. You need a working backend dev
environment (its dependencies installed) and Node 18+.

```bash
cd desktop
npm install
# Point at the interpreter that has the backend deps (a venv is fine):
export SCRYME_PYTHON=../backend/.venv/bin/python   # or any python3 with requirements installed
npm start
```

The embedded PostgreSQL downloads its binaries on first `npm install` (via `embedded-postgres`).

## Build a distributable

PyInstaller output is platform-specific, so build on the OS you're packaging for (no
cross-compilation). Two steps: freeze the backend, then package the app.

```bash
cd desktop
npm install
npm run build:backend     # → dist/scryme-backend/  (PyInstaller, uses a throwaway venv)
npm run dist              # → release/  (electron-builder: dmg/zip, nsis, AppImage/deb)
```

`npm run pack` produces an unpacked app under `release/` for quick local testing without building
installers.

## Notes & caveats

- **Not yet runnable/verifiable in CI here** — the GUI, the PyInstaller freeze, and electron-builder
  all need a real desktop OS. Build and smoke-test locally.
- **Hidden imports:** FastAPI/uvicorn/asyncpg import some modules dynamically. If the frozen backend
  dies with `ModuleNotFoundError`, add the module to `extra_hidden` in `backend.spec`.
- **Icons:** `build/icon.png` (1024×1024) is the source; electron-builder derives `.icns`/`.ico`.
- Roadmap for the desktop epic: native integrations (#83), LAN sharing mode (#84), auto-update +
  signed installers (#85).
