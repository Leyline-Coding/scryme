"""Search route.

Serves the full search page on a normal request and just the results partial for HTMX
requests (live search as you type). Card images use the local cache when present and fall back
to the Scryfall CDN otherwise, so results never show broken images before the cache warms.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.background_service import queue_service
from src.binder_service import all_binders
from src.box_service import (
    all_boxes,
)
from src.config import get_settings
from src.db import get_session
from src.facets import compute_facets
from src.llm import get_config
from src.locations import find_or_create_grouped_locations
from src.models import Card, Deck, NLQCache
from src.perfcache import memoize
from src.prices import biggest_movers
from src.routes.saved import list_saved
from src.scryfall.images import ImageCache
from src.search import SearchError, SearchScope
from src.search.engine import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT,
    SORT_KEYS,
    name_suggestions,
    run_search,
)
from src.templating import templates
from src.trade_pool import open_pools

router = APIRouter(tags=["search"])
_cache = ImageCache()

# Cards-per-page choices offered in the UI (#10); anything else falls back to the default.
PAGE_SIZES = [30, 60, 120, 240]


def _resolve_page_size(request) -> int:
    try:
        n = int(request.cookies.get("scryme_page_size", ""))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return n if n in PAGE_SIZES else DEFAULT_PAGE_SIZE


@dataclass
class _DisplayPrefs:
    view: str
    page_size: int
    infinite: bool
    search_filter: str
    movers: bool
    lang: str
    facet_row_cap: int


def _display_prefs(request) -> _DisplayPrefs:
    """View / page-size / infinite / universal-filter / movers for the search page. Prefers the
    resolved preferences on ``request.state.prefs`` (singleton + cookie merge); falls back to the
    raw cookies when the prefs middleware didn't run (e.g. a bare unit-test request)."""
    p = getattr(getattr(request, "state", None), "prefs", None)
    if p is not None:
        return _DisplayPrefs(
            view=p.view,
            page_size=p.page_size,
            infinite=p.infinite,
            search_filter=p.search_filter,
            movers=p.movers,
            lang=p.lang,
            facet_row_cap=p.facet_row_cap,
        )
    c = request.cookies
    return _DisplayPrefs(
        view="list" if c.get("scryme_view") == "list" else "grid",
        page_size=_resolve_page_size(request),
        infinite=c.get("scryme_infinite") == "1",
        search_filter=c.get("scryme_search_filter") or "",
        movers=c.get("scryme_movers") == "1",
        lang='en',
        facet_row_cap=4000
    )


@dataclass
class CardView:
    card: Card
    quantity: int
    scryfall_uri: str
    tags: list[str]
    flip_back: str | None = None  # back-face image for double-faced cards (grid hover-flip)
    shimmer: str | None = None    # 'foil'/'etched' for a foil-only or etched treatment (#9)
    card_languages: str = "en"     # the specific language of this printing in collection


def _treatment(card) -> str | None:
    """'etched'/'foil' when this printing is a foil-only or etched treatment (grid shimmer, #9)."""
    finishes = [f.lower() for f in (card.raw.get("finishes") or [])]
    if "etched" in finishes:
        return "etched"
    if "foil" in finishes and "nonfoil" not in finishes:
        return "foil"
    return None


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

        # Get the languages for this specific card in the collection
        # result.owned_languages is a dict of sid -> "en,fr"
        card_langs = result.owned_languages.get(sid, "en")

        views.append(

            CardView(
                card=card,
                quantity=result.quantities.get(sid, 0),
                scryfall_uri=card.raw.get("scryfall_uri", "#"),
                tags=result.tags.get(sid, []),
                shimmer=_treatment(card),
                # Store the raw comma-separated languages
                card_languages=card_langs,
            )
        )
    return views



@router.get("/advanced", response_class=HTMLResponse)
async def advanced(
    request: Request, q: str = "", session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Form-based query builder for users who don't know Scryfall syntax.

    The form assembles a Scryfall query string client-side (Alpine) and navigates to /search, so
    there's a single search-engine path and the generated query is visible/editable afterward.

    If a query parameter 'q' is provided, this will prefill the appropriate fields in the advanced
    form.
    """
    from src.locations import find_or_create_grouped_locations
    grouped_locations = await find_or_create_grouped_locations(
        session, binders=True, boxes=True, decks=True
    )

    # Parse the query to prefill the form fields if present
    prefilled_data = {}

    if q:
        try:
            # Import here to avoid circular dependencies
            from src.search.parser import parse

            ast = parse(q)
            if ast is not None:
                prefilled_data = _extract_prefill_data(ast)
        except Exception:
            # If parsing fails, just proceed with empty form
            pass

    return templates.TemplateResponse(
        request, "advanced.html", {
            "read_only": get_settings().read_only,
            "grouped_locations": grouped_locations,
            "pre_q": q,
            "pre_d": prefilled_data
        }
    )


@router.get("/search/nl/status")
async def search_nl_status(
    nl: str = "",
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Check if an NL query has been processed and return the resolved query.

    Always ``text/plain``: the resolved query is model output, and the poller only ever reads it
    as ``responseText``, so it must never be served as a document a browser would render.
    """
    if not nl:
        return PlainTextResponse(status_code=400, content="No NL query provided")
    # Check if there's a cached result
    matching_nl_to_q = (await session.execute(
        select(NLQCache)
        .where(func.lower(NLQCache.nl_text) == func.lower(nl))
    )).scalars().first()
    if not matching_nl_to_q:
        return PlainTextResponse(status_code=404, content="No NL query being processed.")
    if matching_nl_to_q and matching_nl_to_q.q_text:
        return PlainTextResponse(status_code=200, content=matching_nl_to_q.q_text)
    else:
        return PlainTextResponse(status_code=202, content="Processing...")


@router.get("/search/nl/suggestions")
async def search_nl_suggestions(
    request: Request,
    nl: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Get NL query suggestions based on current input or recent queries."""
    if not nl:
        # Return 10 most recent NL queries
        result = await session.execute(
            select(NLQCache)
            .order_by(NLQCache.created_at.desc())
            .limit(10)
        )
    else:
        # Return up to 5 most relevant results matching the partial query
        result = await session.execute(
            select(NLQCache)
            .where(func.lower(NLQCache.nl_text).contains(func.lower(nl)))
            .order_by(NLQCache.created_at.desc())
            .limit(5)
        )

    return templates.TemplateResponse(
        request, "_search_nl_suggestions.html", {"nl":nl, "rows": result.scalars().all()}
    )


async def _run_into_ctx(
    ctx: dict, session, query: str, scope_enum, page: int, sort: str, descending: bool,
    *, with_facets: bool, page_size: int = DEFAULT_PAGE_SIZE, facet_row_cap: int,
) -> None:
    """Run the search and populate ctx with result/views, plus facets or name suggestions."""
    result = await run_search(
        session, query, scope=scope_enum, page=page, sort=sort, descending=descending,
        page_size=page_size,
    )
    ctx["result"] = result
    ctx["views"] = _to_views(result)
    if result.total and with_facets:
        ctx["facets"] = await memoize(
            ("facets", query, scope_enum.value),
            lambda: compute_facets(session, query, scope_enum, facet_row_cap),
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
    append: int = 0,
    ai_error: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    # If nl is passed one of two things happens
    nl_from_cache_and_preloaded = False
    if(nl!=""):
      # If it has already been processed and cached, simply load that cache and inject into q
      matching_nl_to_q = (
          await session.execute(
              select(NLQCache).where(func.lower(NLQCache.nl_text) == func.lower(nl))
          )
      ).scalars().first()
      if(matching_nl_to_q and matching_nl_to_q.q_text is not None):
          q = matching_nl_to_q.q_text
          nl_from_cache_and_preloaded = True
      # If this is a new search query, add it to the queue, start a background process, then
      # initialize the front end when it is ready
      # Only when AI is actually available: otherwise every visitor's text would be stored and a
      # job queued for a model that is switched off (always the case on a read-only instance).
      elif(not matching_nl_to_q and (await get_config(session)).ready):
          new_matching_nl_to_q = NLQCache(nl_text = nl.strip(), q_text=None)
          session.add(new_matching_nl_to_q)
          await session.commit()
          # Start background processing task for the AI nl to q
          queue_service(
              f"nl_to_q_{new_matching_nl_to_q.id}", 'nl_to_q', nlqcache_id=new_matching_nl_to_q.id
          )

    scope_enum = SearchScope.ALL if scope == SearchScope.ALL.value else SearchScope.COLLECTION
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    descending = dir == "desc"
    dp = _display_prefs(request)
    view, page_size, infinite = dp.view, dp.page_size, dp.infinite
    # Universal search filter (#143): extra Scryfall syntax the user always wants applied
    # (e.g. -is:ub, legal:commander), ANDed into every query.
    universal = dp.search_filter.strip()
    effective_q = _apply_universal(q, universal)

    ctx: dict = {"q": q, "scope": scope_enum.value, "sort": sort,
                  # Normalised: the template emits this into an Alpine expression.
                  "dir": "desc" if descending else "asc",
                  "read_only": get_settings().read_only, "view": view, "nl": nl,
                  "nl_from_cache_and_preloaded": nl_from_cache_and_preloaded,
                  "universal": universal, "page_sizes": PAGE_SIZES, "page_size": page_size,
                  "infinite": infinite,
                  "prefs": dp, "ai_error": ai_error}

    try:
        await _run_into_ctx(
            ctx, session, effective_q, scope_enum, page, sort, descending,
            with_facets=True, page_size=page_size, facet_row_cap=ctx["prefs"].facet_row_cap,
        )
    except SearchError as exc:
        # If the universal filter is what broke the query, fall back to the bare query so a bad
        # saved filter can't wedge every search — and flag it.
        if universal:
            try:
                await _run_into_ctx(
                    ctx, session, q, scope_enum, page, sort, descending,
                    with_facets=False, page_size=page_size,
                    facet_row_cap=ctx["prefs"].facet_row_cap,
                )
                ctx["universal_error"] = True
            except SearchError:
                ctx["error"] = str(exc)
        else:
            ctx["error"] = str(exc)

    # Infinite-scroll "load more": append just the next batch of cards + a fresh sentinel.
    if append and request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(request, "_search_more.html", ctx)

    # HTMX swaps just the results; a normal navigation gets the whole page (with the saved-search
    # menu + read-only flag, which the partial doesn't render).
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "search_results.html", ctx)



    ctx["saved_searches"] = await list_saved(session)
    ctx["read_only"] = get_settings().read_only
    ctx["ai_ready"] = (await get_config(session)).ready
    ctx["binders"] = await all_binders(session)
    ctx["boxes"] = await all_boxes(session)
    ctx["decks"] = await session.execute(select(Deck).order_by(Deck.name))
    ctx["decks"] = list(ctx["decks"].scalars().all())
    ctx["grouped_locations"] = await find_or_create_grouped_locations(session)
    ctx["trade_pools"] = await open_pools(session)
    # Optional biggest-movers panel (opt-in via Settings).
    if dp.movers:
        ctx["movers"] = await memoize("movers", lambda: biggest_movers(session, limit=5))
    return templates.TemplateResponse(request, "search.html", ctx)


def _extract_prefill_data(ast) -> dict:
    """Extract prefilled data from parsed AST for advanced search form."""
    from src.search.ast import Term
    from src.search.parser import FIELD_ALIASES

    prefilled = {}

    def _traverse(node):
        if isinstance(node, Term):
            field_name = FIELD_ALIASES.get(node.field, node.field)
            value = node.value

            # Handle different field types and map them to form fields
            if field_name == "name":
                prefilled["name"] = value
            elif field_name == "oracle":
                prefilled["oracle"] = value
            elif field_name == "type":
                prefilled["type"] = value
            elif field_name == "color":
                # For color, we need to map the letters back to checkboxes
                if node.op == ":":
                    # Handle c:rg format for color
                    colors = []
                    for char in value.lower():
                        if char in ['w', 'u', 'b', 'r', 'g']:
                            colors.append(char.upper())
                    if colors:
                        prefilled["colors"] = colors
                elif node.op == ">=" or node.op == "<=":
                    # Handle color filters like c>=wub
                    if node.op == ">=":
                        prefilled["colorMode"] = ">="
                    else:  # node.op == "<="
                        prefilled["colorMode"] = "<="
            elif field_name == "identity":
                # For identity, we need to map the letters back to checkboxes
                if node.op == ":":
                    # Handle id:rg format for identity
                    colors = []
                    for char in value.lower():
                        if char in ['w', 'u', 'b', 'r', 'g']:
                            colors.append(char.upper())
                    if colors:
                        prefilled["identity"] = colors
                elif node.op == ">=" or node.op == "<=":
                    # Handle identity filters like id>=wub
                    if node.op == ">=":
                        prefilled["idMode"] = ">="
                    else:  # node.op == "<="
                        prefilled["idMode"] = "<="
            elif field_name == "mv":
                if node.op == ">=":
                    prefilled["mvMin"] = value
                elif node.op == "<=":
                    prefilled["mvMax"] = value
                elif node.op == "=":
                    prefilled["mvMin"] = value
                    prefilled["mvMax"] = value
            elif field_name == "power":
                if node.op == ">=":
                    prefilled["powMin"] = value
                elif node.op == "<=":
                    prefilled["powMax"] = value
                elif node.op == "=":
                    prefilled["powMin"] = value
                    prefilled["powMax"] = value
            elif field_name == "toughness":
                if node.op == ">=":
                    prefilled["touMin"] = value
                elif node.op == "<=":
                    prefilled["touMax"] = value
                elif node.op == "=":
                    prefilled["touMin"] = value
                    prefilled["touMax"] = value
            elif field_name == "loyalty":
                if node.op == ">=":
                    prefilled["loyMin"] = value
                elif node.op == "<=":
                    prefilled["loyMax"] = value
                elif node.op == "=":
                    prefilled["loyMin"] = value
                    prefilled["loyMax"] = value
            elif field_name == "rarity":
                if node.op == ":":
                    # Handle r:rare format
                    prefilled["rarity"] = [value]
            elif field_name == "set":
                prefilled["set"] = value
            elif field_name == "format":
                prefilled["format"] = value
            elif field_name == "keyword":
                if node.op == ":":
                    prefilled["keywords"] = value
            elif field_name == "usd":
                if node.op == ">=":
                    prefilled["usdMin"] = value
                elif node.op == "<=":
                    prefilled["usdMax"] = value
            elif field_name == "year":
                if node.op == ">=":
                    prefilled["yearMin"] = value
                elif node.op == "<=":
                    prefilled["yearMax"] = value
            elif field_name == "lang":
                prefilled["lang"] = value
        else:
            # Handle complex expressions by traversing operands
            if hasattr(node, 'operands'):
                for operand in node.operands:
                    _traverse(operand)

    _traverse(ast)
    return prefilled
