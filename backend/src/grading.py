"""Graded/slabbed card metadata + condition photos (#179).

Grade fields (company / grade / cert #) and a manual value live on the ``collection_card`` stack;
an optional photo is stored on disk under ``<data_dir>/grades`` and served by a route.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models import CollectionCard

GRADE_COMPANIES = ["PSA", "BGS", "CGC", "SGC"]
_ALLOWED_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB


def grades_dir() -> Path:
    d = get_settings().data_dir / "grades"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_photo_path(filename: str) -> Path | None:
    """Resolve a stored photo filename to a path, rejecting anything with a directory component."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    path = grades_dir() / filename
    return path if path.is_file() else None


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:32] or None


async def set_grade(
    session: AsyncSession, stack_id: int, *, company: str | None, grade: str | None,
    cert: str | None, value_override: float | None,
) -> CollectionCard | None:
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    stack.grade_company = _clean(company)
    stack.grade = _clean(grade)
    stack.cert_number = _clean(cert)
    stack.value_override = value_override if (value_override and value_override > 0) else None
    await session.commit()
    return stack


async def save_grade_photo(session: AsyncSession, stack_id: int, upload) -> bool:
    """Persist an uploaded photo for a stack (replaces any prior). False if the file is unusable."""
    stack = await session.get(CollectionCard, stack_id)
    if stack is None or upload is None or not getattr(upload, "filename", ""):
        return False
    ext = _ALLOWED_EXT.get(upload.content_type or "")
    if ext is None:
        return False
    data = await upload.read()
    if not data or len(data) > _MAX_PHOTO_BYTES:
        return False
    _delete_photo(stack.grade_photo)
    filename = f"{stack_id}-{uuid.uuid4().hex}{ext}"
    (grades_dir() / filename).write_bytes(data)
    stack.grade_photo = filename
    await session.commit()
    return True


def _delete_photo(filename: str | None) -> None:
    if filename:
        path = safe_photo_path(filename)
        if path is not None:
            path.unlink(missing_ok=True)


async def clear_grade(session: AsyncSession, stack_id: int) -> CollectionCard | None:
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    _delete_photo(stack.grade_photo)
    stack.grade_company = stack.grade = stack.cert_number = stack.grade_photo = None
    stack.value_override = None
    await session.commit()
    return stack
