"""Collection upload routes (two-phase: preview, then confirm).

Disabled when the instance is in read-only (demo) mode.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.importers.base import UnknownFormatError
from src.importers.mapping import MAP_FIELDS, csv_headers, guess_mapping
from src.importers.merge import MergeStrategy
from src.importers.service import confirm_upload, stage_mapped_upload, stage_upload
from src.templating import templates

router = APIRouter(tags=["upload"])

MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    from src.import_undo import latest_snapshot

    snap = None if get_settings().read_only else await latest_snapshot(session)
    return templates.TemplateResponse(
        request, "upload.html", {"read_only": get_settings().read_only, "undo": snap}
    )


@router.post("/upload/undo")
async def upload_undo(session: AsyncSession = Depends(get_session)):
    _guard_writable()
    from src.import_undo import undo_last

    await undo_last(session)
    return RedirectResponse(url="/collection?tab=stats", status_code=303)


@router.post("/upload", response_class=HTMLResponse)
async def upload_preview(
    request: Request,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return templates.TemplateResponse(
            request, "upload.html", {"error": "File is too large (32 MB max)."}
        )
    text = raw.decode("utf-8-sig", errors="replace")

    try:
        preview = await stage_upload(session, text)
    except UnknownFormatError as exc:
        # No known parser — but if it's a CSV, offer the column-mapping wizard instead of erroring.
        headers = csv_headers(text)
        if headers:
            return templates.TemplateResponse(
                request, "upload_map.html",
                {"headers": headers, "fields": MAP_FIELDS,
                 "guess": guess_mapping(headers), "csv": text},
            )
        return templates.TemplateResponse(request, "upload.html", {"error": str(exc)})

    return templates.TemplateResponse(request, "upload_preview.html", {"preview": preview})


@router.post("/upload/mapped", response_class=HTMLResponse)
async def upload_mapped(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    form = await request.form()
    text = str(form.get("csv") or "")
    mapping = {
        f.key: str(form[f"map_{f.key}"])
        for f in MAP_FIELDS
        if form.get(f"map_{f.key}")
    }
    try:
        preview = await stage_mapped_upload(session, text, mapping)
    except UnknownFormatError as exc:
        headers = csv_headers(text) or []
        return templates.TemplateResponse(
            request, "upload_map.html",
            {"headers": headers, "fields": MAP_FIELDS, "guess": mapping, "csv": text,
             "error": str(exc)},
        )
    return templates.TemplateResponse(request, "upload_preview.html", {"preview": preview})


@router.post("/upload/confirm")
async def upload_confirm(
    request: Request,
    token: str = Form(...),
    strategy: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    try:
        strat = MergeStrategy(strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown merge strategy.") from exc

    # Per-card decisions arrive as form fields decision_<index>=increment|replace.
    form = await request.form()
    decisions = {
        int(k.removeprefix("decision_")): str(v)
        for k, v in form.items()
        if k.startswith("decision_")
    }

    try:
        await confirm_upload(session, token, strat, decisions)
    except UnknownFormatError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return RedirectResponse(url="/search", status_code=303)
