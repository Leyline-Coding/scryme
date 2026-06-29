#!/usr/bin/env bash
# Freeze the scryme backend into a self-contained binary with PyInstaller.
#
# Run from desktop/:  npm run build:backend
# Produces desktop/dist/scryme-backend/ (the exe + its _internal libs), which electron-builder
# bundles into the app under resources/backend/. Build this on the OS you're packaging for —
# PyInstaller output is platform-specific (no cross-compilation).
#
# Interpreter selection (first that works):
#   1. $SCRYME_BUILD_PYTHON                     — explicit interpreter, used as-is
#   2. a throwaway venv at desktop/.build-venv  — when `python3 -m venv` works (clean, isolated)
#   3. $SCRYME_PYTHON, else python3             — fallback when venv creation isn't available
#      (e.g. Debian/Ubuntu without the python3-venv package); must already have the backend deps.
# In every case backend requirements + PyInstaller are ensured via pip before freezing.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$(cd "$HERE/../backend" && pwd)"
VENV="$HERE/.build-venv"
PYINSTALLER_VERSION="6.11.1"

if [ -n "${SCRYME_BUILD_PYTHON:-}" ]; then
  PYTHON="$SCRYME_BUILD_PYTHON"
  echo "==> Using SCRYME_BUILD_PYTHON: $PYTHON"
elif python3 -m venv "$VENV" 2>/dev/null; then
  PYTHON="$VENV/bin/python"
  echo "==> Created build venv at $VENV"
else
  PYTHON="${SCRYME_PYTHON:-python3}"
  echo "==> venv unavailable (install python3-venv for an isolated build);"
  echo "    falling back to existing interpreter: $PYTHON"
  echo "    (it must already have the backend requirements installed)"
fi

echo "==> Ensuring backend requirements + PyInstaller in $PYTHON"
"$PYTHON" -m pip install --upgrade pip wheel >/dev/null
"$PYTHON" -m pip install -r "$BACKEND/requirements.txt"
"$PYTHON" -m pip install "pyinstaller==$PYINSTALLER_VERSION"

echo "==> Freezing backend (this can take a minute)"
cd "$HERE"
"$PYTHON" -m PyInstaller --noconfirm --clean backend.spec

echo "==> Done: $HERE/dist/scryme-backend/"
