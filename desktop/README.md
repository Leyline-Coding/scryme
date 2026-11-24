# scryme desktop

A native desktop wrapper around scryme. It bundles a portable PostgreSQL and the scryme backend, so
there's nothing to install and no Docker required — double-click and your collection opens in a
window. The same FastAPI app the web/Docker build serves runs locally on `127.0.0.1`.

## How it works

```
Electron (src/main.js)
  ├─ boots embedded PostgreSQL  ──► <userData>/scryme-data/pg   (127.0.0.1 only, random password)
  ├─ spawns the backend sidecar ──► alembic upgrade head, then uvicorn on a free port
  │     dev:  python -m src.desktop_entry   (from ../backend)
  │     prod: resources/backend/scryme-backend   (PyInstaller binary)
  │     bound to 127.0.0.1 — or 0.0.0.0 while LAN sharing is switched on
  ├─ waits for GET /health
  └─ opens a BrowserWindow at http://127.0.0.1:<port>/
```

All state lives under one data directory (Postgres cluster, cached images, backups):

- macOS: `~/Library/Application Support/scryme/scryme-data`
- Linux: `~/.config/scryme/scryme-data`
- Windows: `%APPDATA%\scryme\scryme-data`

Override it with `SCRYME_DESKTOP_DATA_DIR` (e.g. point it at a synced folder).

## Security model

The pure policy lives in `src/security.js` and `src/pgcluster.js` (no Electron imports, covered by
`npm test`); `src/main.js` wires it into Electron.

- **The window only shows the local backend.** Navigation, redirects, `window.open` and sub-frames
  are limited to the app's own origin (`http://127.0.0.1:<port>`) for every `webContents`. `https:`
  links open in the user's browser; every other scheme (`file:`, `smb:`, `ms-*:`, plain `http:`,
  custom protocol handlers…) is dropped — nothing but `https:` ever reaches `shell.openExternal`.
  `<webview>` is denied. The app's own `target="_blank"` pages (printable proxies, grade photos)
  open in a child window with the same restrictions.
- **Renderer:** `sandbox: true`, `contextIsolation: true`, no Node integration. Web permissions are
  deny-by-default; only `clipboard-sanitized-write` (copy buttons) and `notifications` (alerts) are
  granted, and only to the app's origin.
- **Backend environment is explicit.** `SCRYME_*` variables in the launching shell are *not*
  forwarded to the backend, except this allow-list: `SCRYME_LLM_BASE_URL`, `SCRYME_LLM_API_KEY`,
  `SCRYME_LLM_CHAT_MODEL`, `SCRYME_LLM_EMBED_MODEL`, `SCRYME_BACKUP_DIR`, `SCRYME_BACKUP_KEEP`,
  `SCRYME_BACKUP_INTERVAL_HOURS`, `SCRYME_BACKUP_PASSPHRASE`, `SCRYME_DEFAULT_CURRENCY`,
  `SCRYME_DEFAULT_PRICE_SOURCE`, `SCRYME_API_TOKEN`. In particular `SCRYME_LAN_GUARD` cannot be
  switched off from the environment; `desktop_entry.py` forces it on as well. The packaged backend
  runs with the data folder as its working directory, so a `.env` in the launch directory is not
  read.
- **Database:** the embedded PostgreSQL listens on `127.0.0.1` only, has no unix socket, and uses a
  random per-install superuser password kept in `<data dir>/pg-credentials.json` (mode `0600`).
  The data directories are `0700`. Installs created by ≤ 0.28.0 (fixed password `scryme`) are
  migrated on the next launch: the new password is written to the file as *pending*, `ALTER USER`
  runs, the server is asked whether it accepts the new password, and only then is the file
  finalised. If any step fails the old password stays in use and the attempt is repeated on the
  next launch — a half-finished migration never locks the cluster. Keep `pg-credentials.json`
  together with the `pg/` folder when moving or restoring a data directory by hand.
- **Packaging:** the app's code ships in `app.asar` with Electron fuses set (`runAsNode`,
  `NODE_OPTIONS` and `--inspect` disabled; load only from `app.asar`; asar integrity validation,
  which Electron enforces on macOS and Windows). `embedded-postgres` and its dependencies are
  asar-unpacked because the library spawns binaries by on-disk path.

### LAN sharing and the bind address

A listening socket can't be moved between interfaces, so `desktop_entry.py` picks the bind address
once at startup from `lan.json` (`<data dir>/files/lan.json`, written by `POST /lan/toggle`):
`127.0.0.1` when sharing is off, `0.0.0.0` when it is on. `main.js` polls that file once a second;
when its `enabled` flag no longer matches what the running backend was started with, it shows a
short "applying" page, stops the backend, starts it again on the **same port** (so the window's
origin, cookies and localStorage survive) and reloads the page. PostgreSQL keeps running throughout.

Consequences worth knowing:

- Toggling LAN sharing restarts the backend (a few seconds). Anything it was doing — notably a
  first-run card-data download — is interrupted and resumes/retries as it would after an app
  restart.
- Between the toggle and the restart the old listener is still in place. That is safe both ways:
  switching **on** just isn't reachable yet; switching **off** is enforced at once by the backend's
  LAN guard (it reads `lan.json` per request) even though the socket is still on `0.0.0.0` for a
  moment.
- If the poll never fires (or the file is edited to something unreadable, which counts as *off*),
  the worst case is "sharing shows as on but isn't reachable until the app is restarted" — it fails
  closed. If the backend can't be restarted, the app says so and quits rather than run half-configured.

### Linux sandbox (AppImage vs .deb)

Ubuntu 24.04+ (and other distros with `kernel.apparmor_restrict_unprivileged_userns=1`) only let a
program create the user namespace Chromium's sandbox needs if an AppArmor profile grants it.

- The **`.deb`** installs to `/opt/scryme` and its post-install script loads a profile for exactly
  that binary (`/etc/apparmor.d/scryme`, shipped as `resources/apparmor-profile`: `profile "scryme"
  "/opt/scryme/scryme" flags=(unconfined) { userns, }` — electron-builder's deb template). The
  sandbox stays **on**.
- An **AppImage** runs from an arbitrary path and cannot ship a profile. On those systems, and only
  there (restriction on *and* no profile attached to the process), `main.js` adds `--no-sandbox` so
  the app can start at all. Residual risk: a renderer exploit would not be contained by the OS
  sandbox. The window is locked to the local backend and loads nothing from third parties, but the
  data it renders (card text, imported files, deck names) is not fully trusted. Use the `.deb` on
  those distros if that matters to you.

The profile is generated by electron-builder at package time; its presence in the built `.deb` was
checked, but running the `.deb` sandboxed has **not** been
tested on an actual Ubuntu 24.04 machine.

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

### Tests

```bash
npm test                  # unit tests (node:test, no Electron): URL/navigation policy, permission
                          # policy, backend env filtering, password migration logic, asarUnpack list
npm run test:integration  # real embedded Postgres in a temp dir: legacy-password migration,
                          # loopback-only listener, credentials file mode
```

If your npm blocks install scripts, the Postgres package's symlink step has to run once before the
integration test: `(cd node_modules/@embedded-postgres/<platform> && node scripts/hydrate-symlinks.js)`.

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

## Releases & updates

The `.github/workflows/desktop-release.yml` workflow builds installers on macOS/Windows/Linux and
publishes them — with the `electron-updater` `latest*.yml` manifests — to the GitHub Release. It runs
when a release is **published** (or manually via *workflow_dispatch*).

**The app notifies about updates; it does not install them.** On launch (packaged builds only) and
from *Help → Check for Updates…* it reads the release feed. If a newer version exists it shows the
version and an **Open download page** button, which opens that release on GitHub in the browser;
the user downloads and installs it deliberately. `autoDownload` and `autoInstallOnAppQuit` are off
and `quitAndInstall()` is never called.

Why: the installers are **not code-signed**. Without a signature, the only check `electron-updater`
can make on a downloaded update is the SHA-512 in `latest*.yml` — a file that sits in the same
GitHub release as the binary. Anyone able to replace the binary can replace the hash too, so a
silent download-and-install would run whatever is in the release with no independent verification
(and on macOS an unsigned app cannot self-update at all — Squirrel.Mac refuses). The trade-off is
convenience: updating takes a click and a manual install instead of a restart.

To restore in-app updates, the builds have to be signed so the updater can verify the *publisher*
rather than a co-located hash:

- **Windows:** an Authenticode certificate. `electron-updater` then checks the downloaded
  installer's signature against the running app's publisher name before running it.
- **macOS:** a Developer ID Application certificate plus notarization. Squirrel.Mac only applies
  updates signed by the same identity as the running app.
- **Linux:** AppImages have no enforced signature; in-app update there would remain
  hash-only, so it should stay notify-only (or move to a signed package repository).

With that in place, re-enable `autoDownload` (or call `downloadUpdate()` from the dialog) and
`quitAndInstall()` in `src/main.js` for the signed platforms only.

### Code signing (optional)

Unsigned builds work but warn on first open (Windows SmartScreen / macOS Gatekeeper). To sign, set
repo secrets and electron-builder picks them up:

- **Windows / macOS:** `CSC_LINK` (base64 cert), `CSC_KEY_PASSWORD`
- **macOS notarization:** `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`

The workflow sets `CSC_IDENTITY_AUTO_DISCOVERY=false` so unsigned builds stay green until then.

`build/entitlements.mac.plist` (hardened runtime, only effective once signed) grants `allow-jit` and
`disable-library-validation` only. `allow-dyld-environment-variables` and
`allow-unsigned-executable-memory` were removed; the first signed macOS build needs a smoke test of
the bundled backend and Postgres under the hardened runtime (see the comments in that file).

### Store distribution

Starter manifests live in [`packaging/`](packaging/) — Homebrew cask, winget, AUR, and a Flatpak
skeleton. They consume the published GitHub Release artifacts, so each needs the release's real
SHA-256 sums filled in and submission to its external repo. See `packaging/README.md`. Tracked under
#85.

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
