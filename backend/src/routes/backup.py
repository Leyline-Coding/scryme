"""Backup & restore routes: download a JSON backup of your data, preview/apply a restore."""

from __future__ import annotations

import datetime
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.backup import (
    export_backup,
    list_backups,
    resolve_backup,
    restore_backup,
    restore_from_path,
    write_backup,
)
from src.config import get_settings
from src.cryptobackup import encrypt_backup
from src.db import get_session
from src.templating import templates

router = APIRouter(tags=["backup"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    backups = list_backups(settings.backup_dir) if settings.backup_dir else []
    return templates.TemplateResponse(
        request, "backup.html",
        {"read_only": settings.read_only, "backup_dir": settings.backup_dir,
         "backups": backups, "interval": settings.backup_interval_hours,
         "keep": settings.backup_keep},
    )


@router.post("/backup/download")
async def download(
    passphrase: str = Form(""), session: AsyncSession = Depends(get_session)
) -> Response:
    data = await export_backup(session)
    suffix = ".json"
    if passphrase:
        data = encrypt_backup(data, passphrase)
        suffix = ".enc.json"
    body = json.dumps(data, separators=(",", ":"))
    today = datetime.date.today().isoformat()
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="scryme-backup-{today}{suffix}"'},
    )


@router.post("/backup/restore", response_class=HTMLResponse)
async def restore(
    request: Request,
    mode: str = Form("preview"),
    passphrase: str = Form(""),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    applying = mode == "apply"
    if applying and get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")

    try:
        data = json.loads((await file.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = None
        error = "Couldn't read that file — it isn't valid JSON."
    else:
        result = await restore_backup(session, data, dry_run=not applying, passphrase=passphrase)
        error = None

    return templates.TemplateResponse(
        request,
        "_restore_result.html",
        {"result": result, "error": error, "applied": applying,
         "read_only": get_settings().read_only},
    )


# --- on-disk backups ----------------------------------------------------------------------------

def _backup_dir():
    directory = get_settings().backup_dir
    if directory is None:
        raise HTTPException(status_code=404, detail="No backup directory configured.")
    return directory


@router.post("/backup/disk")
async def backup_now(session: AsyncSession = Depends(get_session)) -> Response:
    _guard_writable()
    settings = get_settings()
    await write_backup(session, _backup_dir(), keep=settings.backup_keep,
                       passphrase=settings.backup_passphrase)
    return Response(status_code=303, headers={"Location": "/backup"})


@router.get("/backup/disk/download")
async def disk_download(name: str) -> FileResponse:
    path = resolve_backup(_backup_dir(), name)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found.")
    return FileResponse(path, media_type="application/json", filename=name)


@router.post("/backup/disk/restore", response_class=HTMLResponse)
async def disk_restore(
    request: Request,
    name: str = Form(...),
    mode: str = Form("preview"),
    passphrase: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    applying = mode == "apply"
    if applying:
        _guard_writable()
    path = resolve_backup(_backup_dir(), name)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found.")
    # Fall back to the configured passphrase for scheduled (encrypted) disk backups.
    passphrase = passphrase or get_settings().backup_passphrase
    result = await restore_from_path(session, path, dry_run=not applying, passphrase=passphrase)
    return templates.TemplateResponse(
        request,
        "_restore_result.html",
        {"result": result, "error": None, "applied": applying,
         "read_only": get_settings().read_only},
    )
