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

from src.binder_service import all_binders
from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.facets import compute_facets
from src.llm import ChatClient, get_config, nl_to_query
from src.models import Card
from src.perfcache import memoize
from src.prices import biggest_movers
from src.routes._safe import local_redirect
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
    flip_back: str | None = None  # back-face image for double-faced cards (grid hover-flip)
    shimmer: str | None = None    # 'foil'/'etched' for a foil-only or etched treatment (#9)


def _treatment(card) -> str | None:
    """'etched'/'foil' when this printing is a foil-only or etched treatment (grid shimmer, #9)."""
    finishes = [f.lower() for f in (card.raw.get("finishes") or [])]
    if "etched" in finishes:
        return "etched"
    if "foil" in finishes and "nonfoil" not in finishes:
        return "foil"
    return None


def _back_face_image(card) -> str | None:
    """Back-face image URL for a double-faced card (both faces must have their own image)."""
    faces = card.raw.get("card_faces") or []
    uris = [f.get("image_uris") or {} for f in faces]
    if len(uris) < 2:
        return None
    front = uris[0].get("normal") or uris[0].get("large") or uris[0].get("small")
    back = uris[1].get("normal") or uris[1].get("large") or uris[1].get("small")
    return back if front and back else None


def _apply_universal(q: str, universal: str) -> str:
    """AND a universal filter into a query. Scryfall syntax is implicitly AND, so both are wrapped
    in parentheses to keep top-level ``OR`` in either side from leaking across."""
    q = (q or "").strip()
    universal = (universal or "").strip()
    if not universal:
        return q
    if not q:
        return universal
    return f"({universal}) ({q})"


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
                flip_back=_back_face_image(card),
                shimmer=_treatment(card),
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
    return local_redirect(url)


async def _run_into_ctx(
    ctx: dict, session, query: str, scope_enum, page: int, sort: str, descending: bool,
    *, with_facets: bool,
) -> None:
    """Run the search and populate ctx with result/views, plus facets or name suggestions."""
    result = await run_search(
        session, query, scope=scope_enum, page=page, sort=sort, descending=descending
    )
    ctx["result"] = result
    ctx["views"] = _to_views(result)
    if result.total and with_facets:
        ctx["facets"] = await memoize(
            ("facets", query, scope_enum.value),
            lambda: compute_facets(session, query, scope_enum),
        )
    elif not result.total:
        ctx["suggestions"] = await name_suggestions(session, ctx["q"], scope_enum)


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
    # Universal search filter (#143): extra Scryfall syntax the user always wants applied
    # (e.g. -is:ub, legal:commander), saved in a browser cookie and ANDed into every query.
    universal = (request.cookies.get("scryme_search_filter") or "").strip()
    effective_q = _apply_universal(q, universal)
    ctx: dict = {"q": q, "scope": scope_enum.value, "sort": sort, "dir": dir,
                 "read_only": get_settings().read_only, "view": view, "nl": nl,
                 "universal": universal, "cur": info(get_currency(request))}
    try:
        await _run_into_ctx(
            ctx, session, effective_q, scope_enum, page, sort, descending, with_facets=True
        )
    except SearchError as exc:
        # If the universal filter is what broke the query, fall back to the bare query so a bad
        # saved filter can't wedge every search — and flag it.
        if universal:
            try:
                await _run_into_ctx(
                    ctx, session, q, scope_enum, page, sort, descending, with_facets=False
                )
                ctx["universal_error"] = True
            except SearchError:
                ctx["error"] = str(exc)
        else:
            ctx["error"] = str(exc)

    # HTMX swaps just the results; a normal navigation gets the whole page (with the saved-search
    # menu + read-only flag, which the partial doesn't render).
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "search_results.html", ctx)

    ctx["saved_searches"] = await list_saved(session)
    ctx["read_only"] = get_settings().read_only
    ctx["ai_ready"] = (await get_config(session)).ready
    ctx["binders"] = await all_binders(session)
    # Optional biggest-movers panel (opt-in via a Settings cookie).
    if request.cookies.get("scryme_movers") == "1":
        ctx["movers"] = await memoize("movers", lambda: biggest_movers(session, limit=5))
    return templates.TemplateResponse(request, "search.html", ctx)
