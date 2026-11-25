"""AI settings + grounded deck features (#163).

`/ai` configures the OpenAI-compatible endpoint (stored in-app, key encrypted). The deck endpoints
analyze a deck and suggest cards to add from the owned collection, grounded in real data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.background_service import queue_service
from src.card import linkify_names
from src.config import get_settings
from src.db import get_session
from src.llm import (
    ChatClient,
    answer_card_question,
    build_from_prompt,
    find_commanders,
    get_config,
    plan_upgrades,
    save_config,
    test_connection,
)
from src.llm_endpoint import EndpointError, endpoint_error_message
from src.models import Card, Deck, DeckChatMessage
from src.routes.card import fetch_rulings
from src.rules_rag import rules_for_question
from src.templating import templates

router = APIRouter(tags=["ai"])

_NOT_CONFIGURED = "AI isn't configured — set it up in Settings → AI."
_UNREACHABLE = "Couldn't reach the AI endpoint. Check Settings → AI."
_EMPTY = "The model returned an empty response — try again or use a larger token limit / model."
_DECK_AI_ERROR_TMPL = "_deck_ai_error.html"
_DECK_CHAT_LOG_PLACEHOLDER = (
    "Ask about this deck — e.g. “make it more aggressive”, “what should I cut?”, “how's my mana "
    "base?”"
)
_CARD_ANSWER_TMPL = "_card_answer.html"


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/ai", response_class=HTMLResponse)
async def ai_settings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    cfg = await get_config(session)
    return templates.TemplateResponse(
        request, "ai_settings.html",
        {"cfg": cfg, "has_key": bool(cfg.api_key), "read_only": get_settings().read_only,
         "saved": request.query_params.get("saved") == "1",
         "key_cleared": request.query_params.get("key_cleared") == "1",
         # A fixed message looked up by code — the query string is never echoed into the page.
         "error": endpoint_error_message(request.query_params.get("error"))},
    )


@router.post("/ai")
async def ai_save(
    base_url: str = Form(""),
    api_key: str = Form(""),
    chat_model: str = Form(""),
    embed_model: str = Form(""),
    enabled: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    try:
        key_cleared = await save_config(
            session, base_url=base_url, api_key=api_key, chat_model=chat_model,
            embed_model=embed_model, enabled=enabled is not None,
        )
    except EndpointError as exc:
        # Nothing was saved. Redirect with a code from a fixed set, never the submitted text.
        return RedirectResponse(url=f"/ai?error={exc.code}", status_code=303)
    # The stored key belonged to the previous endpoint, so it was dropped rather than sent to
    # the new one — tell the owner it has to be entered again.
    url = "/ai?saved=1&key_cleared=1" if key_cleared else "/ai?saved=1"
    return RedirectResponse(url=url, status_code=303)


@router.post("/ai/test", response_class=HTMLResponse)
async def ai_test(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    ok, message = await test_connection(await get_config(session))
    return templates.TemplateResponse(request, "_ai_test.html", {"ok": ok, "message": message})


async def _load_deck(session: AsyncSession, deck_id: int) -> Deck:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    return deck


@router.post("/decks/build/prompt", response_class=HTMLResponse)
async def build_prompt(
    request: Request, prompt: str = Form(""), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Build a deck from a natural-language request, using owned cards (#170)."""
    _guard_writable()
    cfg = await get_config(session)
    if not cfg.ready:
        return templates.TemplateResponse(
            request, "deck_build.html",
            {"commanders": [], "error": _NOT_CONFIGURED, "ai_ready": False})
    try:
        built = await build_from_prompt(session, prompt.strip(), ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, "deck_build.html", {"commanders": [], "error": _UNREACHABLE, "ai_ready": True})
    return templates.TemplateResponse(request, "deck_build_prompt.html",
                                      {"built": built, "prompt": prompt})


@router.get("/ai/commanders", response_class=HTMLResponse)
async def commander_finder(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Rank owned legendary creatures by how buildable they are from the collection (#173)."""
    cfg = await get_config(session)
    picks = await find_commanders(session, ChatClient(cfg) if cfg.ready else None)
    return templates.TemplateResponse(
        request, "commander_finder.html", {"picks": picks, "ai_ready": cfg.ready})


@router.post("/decks/{deck_id}/upgrade", response_class=HTMLResponse)
async def deck_upgrade(
    request: Request, deck_id: int, budget: float = Form(50.0),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Suggest cards to buy (validated + priced) to improve a deck, within a budget (#174)."""
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    if not cfg.ready:
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _NOT_CONFIGURED})
    try:
        plan = await plan_upgrades(session, deck, max(0.0, budget), ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _UNREACHABLE})
    if plan.empty:
        return templates.TemplateResponse(request, _DECK_AI_ERROR_TMPL, {"message": _EMPTY})
    return templates.TemplateResponse(request, "_deck_upgrade.html", {"plan": plan})


async def _deck_chat_history(request, session: AsyncSession, deck_id: int) -> list[DeckChatMessage]:
    messages = list((await session.execute(
        select(DeckChatMessage).where(DeckChatMessage.deck_id == deck_id)
        .order_by(DeckChatMessage.id)
    )).scalars().all())
    ## Inject urls
    for m in messages:
        if(m.role=='user'):
            continue
        ## card_name_text_search_matcher loads slowly asyncronously on startup. It may not yet be
        ## available
        if(
            not hasattr(request.app.state, 'card_name_text_search_matcher')
            or not request.app.state.card_name_text_search_matcher
        ):
            continue
        m.content = linkify_names(
            m.content,
            request.app.state.card_name_text_search_matcher,
            useMarkdown=True
        )
    return messages



# --- deck AI modal  -----------------------------------------------------------------------
'''


@router.post("/decks/{deck_id}/ai/upgrade", response_class=HTMLResponse)
async def deck_ai_upgrade(
    request: Request, deck_id: int, budget: float = Form(50.0),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Plan upgrades via AI."""
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    if not cfg.ready:
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _NOT_CONFIGURED})
    try:
        plan = await plan_upgrades(session, deck, max(0.0, budget), ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _UNREACHABLE})
    if plan.empty:
        return templates.TemplateResponse(request, _DECK_AI_ERROR_TMPL, {"message": _EMPTY})
    return templates.TemplateResponse(request, "_deck_upgrade.html", {"plan": plan})
'''


@router.post("/decks/{deck_id}/ai/chat", response_class=HTMLResponse)
async def deck_ai_modal_chat(
    request: Request,
    deck_id: int,
    message: str = Form(""),
    include_deck: bool = Form(True),
    include_collection: bool = Form(False),
    collection_cards_filter: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Handle chat messages in the Deck AI modal."""
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    text = message.strip()
    if not cfg.ready or not text:
        # Return just the chat log without adding new message
        return templates.TemplateResponse(
            request,
            "_ai_chat_log.html",
            {
                "messages": await _deck_chat_history(request, session, deck_id),
                'now': datetime.now(UTC),
                'placeholder':_DECK_CHAT_LOG_PLACEHOLDER,
            },
        )

    # Immediately add the user's message to the chat log
    new_message = DeckChatMessage(deck_id=deck_id, role="user", content=text)
    session.add(new_message)
    await session.commit()

    # Start background processing task for the AI response
    # We pass the text as a unique identifier for this task
    queue_service(
        f"deck_{deck.id}_chat_{new_message.id}", 'ai_chat', deck.id, include_deck,
        include_collection, collection_cards_filter=collection_cards_filter
    )

    # Return updated chat log with user message and temporary assistant message
    return templates.TemplateResponse(
        request, "_ai_chat_log.html",
        {
            "messages": await _deck_chat_history(request, session, deck_id),
            'now': datetime.now(UTC), 'placeholder':_DECK_CHAT_LOG_PLACEHOLDER
        }
    )


@router.get("/decks/{deck_id}/ai/chat/status", response_class=HTMLResponse)
async def deck_ai_modal_chat_status(
    request: Request,
    deck_id: int,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Check the status of the last AI message for a deck."""
    # This endpoint is meant to be polled by frontend
    # Returns information about whether the last assistant message is ready
    messages = await _deck_chat_history(request, session, deck_id)
    if not messages:
        raise HTTPException(status_code=286, detail="No messages")

    # Check if the last message is an assistant message
    if messages[-1].role == "assistant":
        # Assistant message
        return templates.TemplateResponse(
            request, "_ai_chat_log.html",
            {
                "messages": messages, 'now': datetime.now(UTC),
                'placeholder':_DECK_CHAT_LOG_PLACEHOLDER
            }
        )
        #HTMLResponse(status_code=286, content="Response complete")
    else:
        # Still processing
        return HTMLResponse(status_code=202, content="Processing...")


@router.post("/decks/{deck_id}/ai/modal/open", response_class=HTMLResponse)
async def deck_chat_modal_open(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Open the deck chat modal."""
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    return templates.TemplateResponse(
        request, "_ai_chat_modal.html",
        {
            "title": '✨ AI Deck Assistant', "deck": deck,
            "messages": await _deck_chat_history(request, session, deck_id),
            'now': datetime.now(UTC), 'placeholder':_DECK_CHAT_LOG_PLACEHOLDER,
            "ai_ready": cfg.ready, 'include_deck': True
        },
    )


@router.post("/decks/{deck_id}/ai/chat/clear", response_class=HTMLResponse)
async def deck_ai_chat_clear(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Clear chat history in the AI chat."""
    await _load_deck(session, deck_id)
    await session.execute(delete(DeckChatMessage).where(DeckChatMessage.deck_id == deck_id))
    await session.commit()
    return templates.TemplateResponse(
        request, "_ai_chat_log.html",
        {"messages": [], 'now': datetime.now(UTC), 'placeholder':_DECK_CHAT_LOG_PLACEHOLDER}
    )


@router.post("/card/{scryfall_id}/ask", response_class=HTMLResponse)
async def card_ask(
    request: Request, scryfall_id: str, question: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    card = await session.get(Card, scryfall_id)
    if(card is None):
        raise HTTPException(status_code=404, detail="Card not found.")
    cfg = await get_config(session)
    if not cfg.ready or not question.strip():
        return templates.TemplateResponse(
            request, _CARD_ANSWER_TMPL, {"answer": "", "message": _NOT_CONFIGURED})
    raw_rulings = await fetch_rulings(scryfall_id, card) or []
    rulings = [r.get("comment", "") for r in raw_rulings if r.get("comment")]
    rules_context = await rules_for_question(session, question.strip())
    try:
        answer = await answer_card_question(
            card, rulings, question.strip(), ChatClient(cfg), rules_context=rules_context)
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, _CARD_ANSWER_TMPL, {"answer": "", "message": _UNREACHABLE})
    if not answer.strip():
        return templates.TemplateResponse(
            request, _CARD_ANSWER_TMPL, {"answer": "", "message": _EMPTY})
    return templates.TemplateResponse(request, _CARD_ANSWER_TMPL, {"answer": answer})
