#!/usr/bin/env bash
# Freeze the scryme backend into a self-contained binary with PyInstaller.
#
# Run from desktop/:  npm run build:backend
# Produces desktop/dist/scryme-backend/ (the exe + its _internal libs), which electron-builder
# bundles into the app under resources/backend/. Build this on the OS you're packaging for —
# PyInstaller output is platform-specific (no cross-compilation).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$(cd "$HERE/../backend" && pwd)"
VENV="$HERE/.build-venv"

echo "==> Creating build venv at $VENV"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Installing backend requirements + PyInstaller"
pip install --upgrade pip wheel >/dev/null
pip install -r "$BACKEND/requirements.txt"
pip install pyinstaller==6.11.1

echo "==> Freezing backend (this can take a minute)"
cd "$HERE"
pyinstaller --noconfirm --clean backend.spec

echo "==> Done: $HERE/dist/scryme-backend/"
