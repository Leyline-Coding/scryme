
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.box_service import (
    rename_box,
)
from src.collection_edit import locate_stack, _STACK_NOT_FOUND
from src.config import get_settings
from src.db import get_session
from src.models import Box

router = APIRouter(tags=["boxes"])

_BOX_NOT_FOUND = "Box not found."

def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.post("/box/{box_id}/rename")
async def rename_box_route(
    box_id: int, name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    _guard_writable()
    box = await session.get(Box, box_id)
    if box is None: raise HTTPException(status_code=404, detail=_BOX_NOT_FOUND)
    new_box_name = await rename_box(session, box, name)
    return PlainTextResponse(new_box_name)

