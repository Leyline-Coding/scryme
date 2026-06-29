"""Backup & restore routes: download a JSON backup of your data, preview/apply a restore."""

from __future__ import annotations

import datetime
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.backup import export_backup, restore_backup
from src.config import get_settings
from src.db import get_session
from src.templating import templates

router = APIRouter(tags=["backup"])


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "backup.html", {"read_only": get_settings().read_only}
    )


@router.get("/backup/download")
async def download(session: AsyncSession = Depends(get_session)) -> Response:
    data = await export_backup(session)
    body = json.dumps(data, separators=(",", ":"))
    today = datetime.date.today().isoformat()
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="scryme-backup-{today}.json"'},
    )


@router.post("/backup/restore", response_class=HTMLResponse)
async def restore(
    request: Request,
    mode: str = Form("preview"),
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
        result = await restore_backup(session, data, dry_run=not applying)
        error = None

    return templates.TemplateResponse(
        request,
        "_restore_result.html",
        {"result": result, "error": error, "applied": applying,
         "read_only": get_settings().read_only},
    )
