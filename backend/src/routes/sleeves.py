"""Sleeves management routes: upload images, associate with decks."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.config import get_settings
from src.db import get_session
from src.models import Card, Sleeve
from src.templating import templates


router = APIRouter(tags=["sleeves"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


def _upload_path() -> Path:
    """Get the path to upload sleeve images to."""
    settings = get_settings()
    return Path(settings.data_dir) / "sleeves"


def _save_image(file: UploadFile, filename: str) -> None:
    """Save an uploaded file to the sleeves directory."""
    path = _upload_path()
    path.mkdir(parents=True, exist_ok=True)
    
    # Read and write in chunks to handle large files properly
    with open(path / filename, "wb") as f:
        while chunk := file.file.read(8192):
            f.write(chunk)


@router.post("/sleeves/new", response_class=HTMLResponse)
async def create_sleeve(
    request: Request,
    deck_id: str = Form(""),
    name: str = Form(""),
    image: UploadFile | None = File(None),
    color: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Add a new sleeve to the collection."""
    _guard_writable()
    
    # Handle image upload or color selection
    filename = ""
    if image and image.filename and hasattr(image, 'file'):
        # Generate unique filename to avoid conflicts
        ext = os.path.splitext(image.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        _save_image(image, filename)
    
    # Create sleeve entry - use either image or color
    deck_id_int = int(deck_id) if deck_id.isdigit() else None
    
    sleeve = Sleeve(
        deck_id=deck_id_int,
        name=name.strip() if name else None,
        image=filename if filename else None,
        color=color if color else None,
    )
    
    session.add(sleeve)
    await session.commit()
    
    # Redirect back to collection sleeves tab
    return RedirectResponse(url="/collection/sleeves", status_code=303)


@router.post("/sleeves/{sleeve_id}/unassociate", response_class=HTMLResponse)
async def remove_deck_sleeve(
    sleeve_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove sleeve association from a deck."""
    _guard_writable()
    sleeve = await session.get(Sleeve, sleeve_id)
    sleeve.deck_id = None
            
    await session.commit()
    
    # Redirect back to the sleeves tab
    return Response(status_code=200, headers={})



@router.post("/sleeves/{sleeve_id}/delete", response_class=HTMLResponse)
async def delete_sleeve(
    request: Request,
    sleeve_id: int,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Delete a sleeve."""
    _guard_writable()
    
    sleeve = await session.get(Sleeve, sleeve_id)
    if not sleeve:
        raise HTTPException(status_code=404, detail="Sleeve not found.")
    
    # Remove image file if exists
    if sleeve.image:
        path = _upload_path() / sleeve.image
        if path.exists():
            path.unlink()
            
    await session.delete(sleeve)
    await session.commit()
    
    return RedirectResponse(url="/collection/sleeves", status_code=303)


@router.get("/sleeves/{sleeve_id}/image")
async def serve_sleeve_image(
    sleeve_id: int,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve a sleeve image."""
    base_path = _upload_path().resolve()
    sleeve = await session.get(Sleeve, sleeve_id)
    if not sleeve or not sleeve.image:
        raise HTTPException(status_code=404, detail="Sleeve not found.")
    path = (base_path / Path(sleeve.image)).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sleeve image not found.")
    return FileResponse(path)