# PyInstaller spec for the scryme backend sidecar (one-dir bundle).
#
# Run from the desktop/ directory:  pyinstaller backend.spec
# Produces dist/scryme-backend/scryme-backend(.exe) + its _internal libs, which electron-builder
# copies into the app's resources/backend/. The Electron main process spawns that binary.
#
# Note: FastAPI/uvicorn/asyncpg pull in modules dynamically, so hidden imports + bundled data files
# (templates, static, alembic migrations) are collected explicitly. If a runtime ImportError shows
# up, add the missing module to `extra_hidden` below.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BACKEND = os.path.abspath(os.path.join(SPECPATH, "..", "backend"))

datas = [
    (os.path.join(BACKEND, "src", "templates"), "src/templates"),
    (os.path.join(BACKEND, "src", "static"), "src/static"),
    (os.path.join(BACKEND, "alembic"), "alembic"),
]
datas += collect_data_files("alembic")

hiddenimports = []
for pkg in ("uvicorn", "asyncpg", "src", "alembic", "fastapi", "starlette", "anyio",
            "cryptography", "ijson", "structlog", "pydantic", "pydantic_settings"):
    hiddenimports += collect_submodules(pkg)
extra_hidden = [
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
    "uvicorn.loops.auto",
]
hiddenimports += extra_hidden

a = Analysis(
    [os.path.join(BACKEND, "src", "desktop_entry.py")],
    pathex=[BACKEND],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="scryme-backend",
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="scryme-backend")
