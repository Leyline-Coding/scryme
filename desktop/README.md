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

Two steps: freeze the backend, then package the app.

```bash
cd desktop
npm install
npm run build:backend     # → dist/scryme-backend/  (the frozen backend)
npm run dist              # → release/  (electron-builder: dmg/zip, nsis, AppImage/deb)
```

`npm run pack` produces an unpacked app under `release/<platform>-unpacked/` for quick local testing
without building installers — launch the `scryme` executable inside it.

### How the backend gets frozen

`build:backend` auto-selects one of two paths:

- **Docker (preferred, default when Docker is running)** — freezes inside a `python:3.12-slim`
  container, so **no host Python, venv, or sudo** is needed and the Linux binary is portable
  (glibc ≥ 2.36). Set `SCRYME_BUILD_NO_DOCKER=1` to skip it.
- **Local interpreter** — used when Docker isn't available. Needs a Python env with the backend
  requirements; it tries a throwaway venv at `.build-venv`, then `$SCRYME_BUILD_PYTHON` /
  `$SCRYME_PYTHON` / `python3`.

PyInstaller output is platform-specific (no cross-compilation): the Docker path produces a **Linux**
binary; for **macOS/Windows** installers, run `build:backend` via the local path on a Mac/Windows
host, then `npm run dist`.

## Releases & auto-update

The `.github/workflows/desktop-release.yml` workflow builds installers on macOS/Windows/Linux and
publishes them — with the `electron-updater` `latest*.yml` manifests — to the GitHub Release. It runs
when a release is **published** (or manually via *workflow_dispatch*). The app checks for updates on
launch (production builds only) and offers to restart when one is downloaded; the bundled Postgres +
backend are stopped cleanly before it relaunches.

### Code signing (optional)

Unsigned builds work but warn on first open (Windows SmartScreen / macOS Gatekeeper). To sign, set
repo secrets and electron-builder picks them up:

- **Windows / macOS:** `CSC_LINK` (base64 cert), `CSC_KEY_PASSWORD`
- **macOS notarization:** `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`

The workflow sets `CSC_IDENTITY_AUTO_DISCOVERY=false` so unsigned builds stay green until then.

### Store distribution (follow-ups)

Homebrew cask, winget, Flatpak, and AUR are not wired yet — they consume the published GitHub
Release artifacts, so they can be added once releases are flowing. Tracked under #85.

## Notes & caveats

- **The GUI must be smoke-tested on a real desktop** (it needs a display). The Linux build chain is
  verified end-to-end otherwise: `npm install`, the embedded Postgres boot, the Docker freeze, and
  `electron-builder` packaging all succeed, and the frozen backend applies migrations + serves
  `GET /health` standalone.
- **Hidden imports:** FastAPI/uvicorn/asyncpg import some modules dynamically. If the frozen backend
  dies with `ModuleNotFoundError`, add the module to `extra_hidden` in `backend.spec`.
- **Icons:** `build/icon.png` (1024×1024) is the source; electron-builder derives `.icns`/`.ico`.
- Roadmap for the desktop epic: native integrations (#83), LAN sharing mode (#84), auto-update +
  signed installers (#85).
