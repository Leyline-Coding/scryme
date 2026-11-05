"""Card detail page.

A dedicated page for a single printing: large art, full oracle text, prices, format
legalities, the stacks you own, and the card's other printings. Rulings are loaded lazily
from Scryfall (HTMX) so the page renders instantly and stays useful offline if the fetch fails.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.templating import templates

router = APIRouter()

@router.post("/edit-name-form", response_class=HTMLResponse)
async def edit_name_form(
    request: Request,
    placeholder: str = Form(...),
    action: str = Form(...),
    target: str = Form(...),
) -> HTMLResponse:
    """Return the form for editing the binder name."""
    return templates.TemplateResponse(
        request, "reusables/_edit_name_form.html", {"placeholder": placeholder, "action": action, "target": target}
    )