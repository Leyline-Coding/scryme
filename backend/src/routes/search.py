"""Search route.

Serves the full search page on a normal request and just the results partial for HTMX
requests (live search as you type). Card images use the local cache when present and fall back
to the Scryfall CDN otherwise, so results never show broken images before the cache warms.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.facets import compute_facets
from src.llm import ChatClient, get_config, nl_to_query
from src.models import Card
from src.routes.saved import list_saved
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.search import SearchError, SearchScope
from src.search.engine import DEFAULT_SORT, SORT_KEYS, name_suggestions, run_search
from src.templating import templates

router = APIRouter(tags=["search"])
_cache = ImageCache()


@dataclass
class CardView:
    card: Card
    quantity: int
    image: str
    scryfall_uri: str
    tags: list[str]


def _to_views(result) -> list[CardView]:
    views = []
    for card in result.cards:
        sid = str(card.scryfall_id)
        image = _cache.url_path(sid) if _cache.is_cached(sid) else cdn_image_url(card.raw)
        views.append(
            CardView(
                card=card,
                quantity=result.quantities.get(sid, 0),
                image=image or "",
                scryfall_uri=card.raw.get("scryfall_uri", "#"),
                tags=result.tags.get(sid, []),
            )
        )
    return views


@router.get("/advanced", response_class=HTMLResponse)
async def advanced(request: Request) -> HTMLResponse:
    """Form-based query builder for users who don't know Scryfall syntax.

    The form assembles a Scryfall query string client-side (Alpine) and navigates to /search, so
    there's a single search-engine path and the generated query is visible/editable afterward.
    """
    return templates.TemplateResponse(
        request, "advanced.html", {"read_only": get_settings().read_only}
    )


@router.post("/search/nl")
async def search_nl(
    q: str = Form(""),
    scope: str = Form(SearchScope.COLLECTION.value),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Translate a plain-English request to a Scryfall query and run it (#171).

    Falls back to searching the request text as-is when AI is off or translation fails, so the box
    always does *something*. The generated query is shown editable on the results page.
    """
    text = q.strip()
    generated = ""
    cfg = await get_config(session)
    if cfg.ready and text:
        try:
            generated = await nl_to_query(text, ChatClient(cfg))
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            generated = ""
    url = f"/search?q={quote(generated or text)}&scope={quote(scope)}"
    if generated:
        url += f"&nl={quote(text)}"
    return RedirectResponse(url, status_code=303)


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    scope: str = SearchScope.COLLECTION.value,
    page: int = 1,
    sort: str = DEFAULT_SORT,
    dir: str = "asc",
    nl: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    scope_enum = SearchScope.ALL if scope == SearchScope.ALL.value else SearchScope.COLLECTION
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    descending = dir == "desc"
    view = "list" if request.cookies.get("scryme_view") == "list" else "grid"
    ctx: dict = {"q": q, "scope": scope_enum.value, "sort": sort, "dir": dir,
                 "read_only": get_settings().read_only, "view": view, "nl": nl,
                 "cur": info(get_currency(request))}
    try:
        result = await run_search(
            session, q, scope=scope_enum, page=page, sort=sort, descending=descending
        )
        ctx["result"] = result
        ctx["views"] = _to_views(result)
        if result.total:
            ctx["facets"] = await compute_facets(session, q, scope_enum)
        else:
            ctx["suggestions"] = await name_suggestions(session, q, scope_enum)
    except SearchError as exc:
        ctx["error"] = str(exc)

    # HTMX swaps just the results; a normal navigation gets the whole page (with the saved-search
    # menu + read-only flag, which the partial doesn't render).
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "search_results.html", ctx)

    ctx["saved_searches"] = await list_saved(session)
    ctx["read_only"] = get_settings().read_only
    ctx["ai_ready"] = (await get_config(session)).ready
    return templates.TemplateResponse(request, "search.html", ctx)
