# Desktop App

Prefer a native app to Docker? The **scryme desktop app** bundles its own PostgreSQL and the
backend, so there's nothing to install or configure — download it, open it, and your collection is
right there.

## Download

Grab the installer for your OS from the
[latest release](https://github.com/Leyline-Coding/scryme/releases/latest):

| OS | File |
| --- | --- |
| **Linux (x64 / arm64)** | `scryme-<version>.AppImage` / `scryme-<version>-arm64.AppImage` |
| **Debian / Ubuntu (x64)** | `scryme-<version>-amd64.deb` |
| **Windows** | `scryme-Setup-<version>.exe` (x64; runs on Windows-on-ARM via emulation) |
| **macOS (Apple Silicon)** | `scryme-<version>-arm64-mac.zip` or `scryme-<version>-arm64.dmg` |

!!! note "Linux: AppImage or .deb"
    The **AppImage** (x64 and arm64) is the universal, distro-agnostic format: mark it executable
    (`chmod +x`) and run it; no install or package manager needed.

    On **Ubuntu 24.04+** (and other distros that restrict unprivileged user namespaces through
    AppArmor) prefer the **`.deb`**: it installs an AppArmor profile so the browser engine's
    sandbox stays on. An AppImage cannot ship such a profile, so on those systems it has to start
    with that sandbox disabled.

!!! note "Unsigned installers"
    Builds are currently **unsigned**, so Windows SmartScreen and macOS Gatekeeper warn on first
    open (on macOS: right-click → Open). Signing/notarization is on the
    [roadmap](../roadmap.md#desktop-app).

## First launch

On first run the app downloads the Scryfall card database **once** (a progress screen shows the
import). After that it's the same app as the web version — import a collection and search away.

Only **one copy** of scryme runs at a time (they'd share one embedded database) — launching it again
just focuses the window that's already open.

All state lives in a single folder, so you can back it up or put it on a synced drive:

- macOS: `~/Library/Application Support/scryme/scryme-data`
- Linux: `~/.config/scryme/scryme-data`
- Windows: `%APPDATA%\scryme\scryme-data`

## Desktop-only features

- **Drag-and-drop import** — drop a collection CSV anywhere on the window to start an import.
- **Global quick-search hotkey** — `Ctrl/Cmd+Shift+S` raises the window and focuses search from
  anywhere.
- **System notifications** — saved-search alerts (cards that newly match after a card-data update)
  pop a native notification.
- **LAN sharing** — opt in to browse your collection from your phone or tablet on the home network,
  with a QR code and an optional access code. Off by default: the app listens on `127.0.0.1` only
  until you enable it. Switching it on or off restarts the app's built-in server, which takes a few
  seconds (the window shows "Applying the LAN sharing change…" and then comes back).
- **Update notifications** — the app checks GitHub Releases on launch (and from *Help → Check for
  Updates…*). When a newer version exists it tells you and opens the download page in your browser;
  it never downloads or installs anything by itself. Because the installers aren't code-signed yet,
  the app has no trustworthy way to verify an update on its own, so installing one stays a
  deliberate step.

## Privacy & local security

- The built-in database listens on `127.0.0.1` only and is protected by a random password generated
  on first launch (stored in `pg-credentials.json` inside the data folder, readable only by you).
  Installs from 0.28.0 or earlier are switched over automatically the next time the app starts.
  If you copy or restore the data folder by hand, keep that file together with the `pg` folder.
- The data folder is private to your user account.
- Links to other sites open in your browser (`https` only); the app window itself only ever shows
  scryme.
- `SCRYME_*` environment variables from your shell are not passed to the desktop app's server,
  except the documented AI (`SCRYME_LLM_*`), backup (`SCRYME_BACKUP_*`), `SCRYME_DEFAULT_CURRENCY`,
  `SCRYME_DEFAULT_PRICE_SOURCE` and `SCRYME_API_TOKEN` settings.

## Build from source

The app is built from the [`desktop/`](https://github.com/Leyline-Coding/scryme/tree/main/desktop)
directory (Electron + a PyInstaller-frozen backend + `embedded-postgres`). See its README for the
build steps and the release/signing setup.
