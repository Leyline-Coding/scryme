# Desktop App

Prefer a native app to Docker? The **scryme desktop app** bundles its own PostgreSQL and the
backend, so there's nothing to install or configure — download it, open it, and your collection is
right there.

## Download

Grab the installer for your OS from the
[latest release](https://github.com/Leyline-Coding/scryme/releases/latest):

| OS | File |
| --- | --- |
| **Linux** | `scryme-<version>.AppImage` (portable) or `scryme-desktop_<version>_amd64.deb` |
| **Windows** | `scryme-Setup-<version>.exe` |
| **macOS** | `scryme-<version>-arm64-mac.zip` or `scryme-<version>-arm64.dmg` (Apple Silicon) |

!!! note "Unsigned installers"
    Builds are currently **unsigned**, so Windows SmartScreen and macOS Gatekeeper warn on first
    open (on macOS: right-click → Open). Signing/notarization is on the
    [roadmap](../roadmap.md#desktop-app).

## First launch

On first run the app downloads the Scryfall card database **once** (a progress screen shows the
import). After that it's the same app as the web version — import a collection and search away.

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
  with a QR code and an optional access code. Off by default, and loopback-only until you enable it.
- **Auto-update** — the app checks GitHub Releases on launch and offers to update.

## Build from source

The app is built from the [`desktop/`](https://github.com/Leyline-Coding/scryme/tree/main/desktop)
directory (Electron + a PyInstaller-frozen backend + `embedded-postgres`). See its README for the
build steps and the release/signing setup.
