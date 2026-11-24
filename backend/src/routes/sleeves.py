"""Sleeves management routes: upload images, associate with decks."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.models import Sleeve
from src.sanitize import hex_color

router = APIRouter(tags=["sleeves"])

_BAD_IMAGE = "Sleeve image must be a JPEG, PNG, WebP or GIF file."


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


def _upload_path() -> Path:
    """Get the path to upload sleeve images to."""
    settings = get_settings()
    return Path(settings.data_dir) / "sleeves"


# Raster formats only. SVG is deliberately absent: it is a script-capable document, not an image.
# The value is the extension a stored file gets, and ``_MEDIA_TYPES`` is the reverse map used when
# serving — so a file can only ever be served as the image type its bytes were verified to be.
_ALLOWED_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
}
_MEDIA_TYPES = {ext: media for media, ext in _ALLOWED_EXT.items()}
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB — matches the grading-photo cap


def _sniff_image(data: bytes) -> str | None:
    """The image media type *data*'s magic bytes identify, or None if it is not one we accept."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _store_image(upload: UploadFile) -> str:
    """Validate an uploaded sleeve image and write it to disk; returns the stored filename.

    The client's filename and extension are ignored entirely. The declared content type must be
    an allowed image type *and* the bytes must carry an image signature; the stored extension
    comes from the signature, so what is on disk is what will be served.
    """
    if (upload.content_type or "").lower() not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=_BAD_IMAGE)
    data = await upload.read(_MAX_IMAGE_BYTES + 1)
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Sleeve image is too large (8 MB max).")
    detected = _sniff_image(data)
    if detected is None:
        raise HTTPException(status_code=400, detail=_BAD_IMAGE)
    filename = f"{uuid.uuid4().hex}{_ALLOWED_EXT[detected]}"
    path = _upload_path()
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_bytes(data)
    return filename


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

    # The colour ends up inside a ``style`` attribute, so only a hex colour (which is all the
    # form's ``<input type="color">`` can submit) is ever stored.
    safe_color = None
    if color:
        safe_color = hex_color(color)
        if safe_color is None:
            raise HTTPException(status_code=400, detail="Sleeve colour must be a hex colour.")

    # Handle image upload or color selection
    filename = ""
    if image and image.filename and hasattr(image, 'file'):
        filename = await _store_image(image)

    # Create sleeve entry - use either image or color
    deck_id_int = int(deck_id) if deck_id.isdigit() else None

    sleeve = Sleeve(
        deck_id=deck_id_int,
        name=name.strip() if name else None,
        image=filename if filename else None,
        color=safe_color,
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
    # Only ever serve a known raster type, and say exactly which one: a file stored before
    # uploads were validated may be HTML/SVG, and guessing its type from the name would run it
    # in this origin. Those are refused rather than served.
    media_type = _MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None or not path.is_relative_to(base_path) or not path.is_file():
        raise HTTPException(status_code=404, detail="Sleeve image not found.")
    return FileResponse(
        path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
    )
