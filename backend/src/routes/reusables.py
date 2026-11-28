"""Reusable inline-edit form fragments.

``/edit-name`` and ``/edit-description`` return a small form that htmx swaps into the page; the
form then posts to ``action`` and swaps the reply into ``target``. Both of those arrive from the
caller, so they are held to an allowlist: ``action`` must be a plain same-site path and ``target``
a bare element id. Without that, a crafted request could aim the returned form at another origin
or at an arbitrary selector.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from src.sanitize import local_path, target_selector
from src.templating import templates

router = APIRouter()


def _checked(action: str, target: str) -> tuple[str, str]:
    """Return (action, target) if both are safe to echo into ``hx-post``/``hx-target``; else 400."""
    if local_path(action) is None:
        raise HTTPException(status_code=400, detail="action must be a same-site path.")
    if target_selector(target) is None:
        raise HTTPException(status_code=400, detail="target must be an element id.")
    return action, target


## Deprecating this route
@router.post("/edit-name", response_class=HTMLResponse)
async def edit_name_form(
    request: Request,
    placeholder: str = Form(...),
    action: str = Form(...),
    target: str = Form(...),
) -> HTMLResponse:
    """Return a form for editing a generic name."""
    action, target = _checked(action, target)
    return templates.TemplateResponse(
        request, "forms/edit_name.html",
        {"placeholder": placeholder, "action": action, "target": target}
    )


## Deprecating this route
@router.post("/edit-description", response_class=HTMLResponse)
async def edit_description_form(
    request: Request,
    placeholder: str = Form(''),
    action: str = Form(...),
    target: str = Form(...),
) -> HTMLResponse:
    """Return the form for editing the deck description."""
    action, target = _checked(action, target)
    return templates.TemplateResponse(
        request, "forms/edit_description.html",
        {"placeholder": placeholder, "action": action, "target": target}
    )
