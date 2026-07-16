"""AI settings + grounded deck features (#163).

`/ai` configures the OpenAI-compatible endpoint (stored in-app, key encrypted). The deck endpoints
analyze a deck and suggest cards to add from the owned collection, grounded in real data.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.llm import (
    ChatClient,
    analyze_deck,
    answer_card_question,
    build_from_prompt,
    deck_chat,
    find_commanders,
    get_config,
    plan_upgrades,
    save_config,
    suggest_from_collection,
    test_connection,
)
from src.models import Deck, DeckChatMessage
from src.routes.card import _load_card, fetch_rulings
from src.rules_rag import rules_for_question
from src.templating import templates

router = APIRouter(tags=["ai"])

_NOT_CONFIGURED = "AI isn't configured — set it up in Settings → AI."
_UNREACHABLE = "Couldn't reach the AI endpoint. Check Settings → AI."
_EMPTY = "The model returned an empty response — try again or use a larger token limit / model."
_DECK_AI_ERROR_TMPL = "_deck_ai_error.html"
_DECK_CHAT_LOG_TMPL = "_deck_chat_log.html"
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
         "saved": request.query_params.get("saved") == "1"},
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
    await save_config(
        session, base_url=base_url, api_key=api_key, chat_model=chat_model,
        embed_model=embed_model, enabled=enabled is not None,
    )
    return RedirectResponse(url="/ai?saved=1", status_code=303)


@router.post("/ai/test", response_class=HTMLResponse)
async def ai_test(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    ok, message = await test_connection(await get_config(session))
    return templates.TemplateResponse(request, "_ai_test.html", {"ok": ok, "message": message})


async def _load_deck(session: AsyncSession, deck_id: int) -> Deck:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    return deck


@router.post("/decks/{deck_id}/analyze", response_class=HTMLResponse)
async def deck_analyze(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    if not cfg.ready:
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _NOT_CONFIGURED})
    try:
        analysis = await analyze_deck(session, deck, ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _UNREACHABLE})
    if not analysis.strip():
        return templates.TemplateResponse(request, _DECK_AI_ERROR_TMPL, {"message": _EMPTY})
    return templates.TemplateResponse(request, "_deck_analysis.html", {"analysis": analysis})


@router.post("/decks/{deck_id}/suggest", response_class=HTMLResponse)
async def deck_suggest(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    if not cfg.ready:
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _NOT_CONFIGURED})
    try:
        result = await suggest_from_collection(session, deck, ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return templates.TemplateResponse(
            request, _DECK_AI_ERROR_TMPL, {"message": _UNREACHABLE})
    if result.empty:
        return templates.TemplateResponse(request, _DECK_AI_ERROR_TMPL, {"message": _EMPTY})
    return templates.TemplateResponse(request, "_deck_suggest.html", {"result": result})


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


# --- deck coaching chat (#172) ------------------------------------------------------------------

async def _chat_history(session: AsyncSession, deck_id: int) -> list[DeckChatMessage]:
    return list((await session.execute(
        select(DeckChatMessage).where(DeckChatMessage.deck_id == deck_id)
        .order_by(DeckChatMessage.id)
    )).scalars().all())


@router.get("/decks/{deck_id}/chat", response_class=HTMLResponse)
async def deck_chat_page(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    return templates.TemplateResponse(
        request, "deck_chat.html",
        {"deck": deck, "messages": await _chat_history(session, deck_id), "ai_ready": cfg.ready},
    )


@router.post("/decks/{deck_id}/chat", response_class=HTMLResponse)
async def deck_chat_send(
    request: Request, deck_id: int, message: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    deck = await _load_deck(session, deck_id)
    cfg = await get_config(session)
    text = message.strip()
    if not cfg.ready or not text:
        return templates.TemplateResponse(
            request, _DECK_CHAT_LOG_TMPL, {"messages": await _chat_history(session, deck_id)})
    prior = await _chat_history(session, deck_id)
    history = [{"role": m.role, "content": m.content} for m in prior]
    try:
        reply = await deck_chat(session, deck, history, text, ChatClient(cfg))
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        reply = ""
    session.add(DeckChatMessage(deck_id=deck_id, role="user", content=text))
    session.add(DeckChatMessage(deck_id=deck_id, role="assistant",
                                content=reply or "(the AI endpoint didn't respond — try again)"))
    await session.commit()
    return templates.TemplateResponse(
        request, _DECK_CHAT_LOG_TMPL, {"messages": await _chat_history(session, deck_id)})


@router.post("/decks/{deck_id}/chat/clear", response_class=HTMLResponse)
async def deck_chat_clear(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    await _load_deck(session, deck_id)
    await session.execute(delete(DeckChatMessage).where(DeckChatMessage.deck_id == deck_id))
    await session.commit()
    return templates.TemplateResponse(request, _DECK_CHAT_LOG_TMPL, {"messages": []})


# --- card rules Q&A (#175) ----------------------------------------------------------------------

@router.post("/card/{scryfall_id}/ask", response_class=HTMLResponse)
async def card_ask(
    request: Request, scryfall_id: str, question: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    card = await _load_card(session, scryfall_id)
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
