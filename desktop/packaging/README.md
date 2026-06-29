# Store distribution scaffolds

Starter manifests for getting the desktop app into package managers. They consume the artifacts the
`desktop-release` workflow publishes to each GitHub Release, so they can only be finished **after** a
release exists. Each needs the release's real **SHA-256 sums** filled in and submission to an
external repo you control.

> Status: these are **scaffolds**, not yet submitted. Tracked under #85.

Get the SHA-256s for a release:

```bash
gh release download v0.10.0 --repo Leyline-Coding/scryme --pattern '*.exe' --pattern '*.dmg' --pattern '*.AppImage'
sha256sum scryme-Setup-0.10.0.exe scryme-0.10.0-arm64.dmg scryme-0.10.0.AppImage
```

## Homebrew cask (`homebrew/scryme.rb`)

macOS, arm64 (our only mac build today). Submit to a tap you own (e.g.
`Leyline-Coding/homebrew-tap`) or to `homebrew/homebrew-cask`. Update `version` + `sha256` each
release. **Unsigned builds**: `brew install` works but Gatekeeper still prompts on first launch until
the app is signed + notarized (see the main desktop README).

## winget (`winget/`)

Windows. Three manifests (version / installer / locale) per the winget schema. Submit a PR to
`microsoft/winget-pkgs` under `manifests/l/Leyline-Coding/scryme/<version>/`. Update `PackageVersion`
+ `InstallerSha256` each release. Easiest via `wingetcreate update`.

## AUR (`aur/PKGBUILD`)

Arch Linux. Installs the published AppImage as `/usr/bin/scryme`. Push to the `scryme-bin` AUR repo.
Update `pkgver` + `sha256sums` each release (`updpkgsums`).

## Flatpak (`flatpak/`)

⚠️ **Largest effort.** A proper Flatpak can't just run the AppImage — the sandbox blocks the bundled
PostgreSQL and the backend's behaviour, and Electron apps build against the Flatpak Electron BaseApp.
The manifest here is a **skeleton** that unpacks the AppImage; expect real work on sandbox holes
(`--share=network`, `--filesystem`) and possibly bundling Postgres differently. Submit to Flathub
once it actually runs sandboxed.

## Signing (prerequisite for a good install UX)

Until the installers are signed/notarized, Windows SmartScreen and macOS Gatekeeper warn on first
open. The hooks are in place — see "Code signing" in `../README.md`. Signing needs a Windows code
-signing cert and an Apple Developer account; those are the blocker, not the config.
