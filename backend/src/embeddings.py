"""Semantic card similarity via oracle-text embeddings (#176).

Points at any OpenAI-API-compatible ``/embeddings`` endpoint (OpenAI, OpenRouter, or a local
Ollama / LM Studio server — see ``SCRYME_LLM_BASE_URL``). Each oracle's ``name / type / oracle
text`` is embedded once and stored L2-normalized (:class:`~src.models.CardEmbedding`), so cosine
similarity is a dot product computed in Python — no pgvector dependency, fine at personal scale.

The HTTP client is injectable so tests can supply a deterministic fake (no network).
"""

from __future__ import annotations

import math

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import __version__
from src.config import get_settings
from src.db import SessionLocal
from src.models import Card, CardEmbedding, CollectionCard

_UA = f"scryme/{__version__} (+https://github.com/Leyline-Coding/scryme)"


def is_configured() -> bool:
    """True when an embeddings endpoint is configured (AI features are opt-in)."""
    return bool(get_settings().llm_base_url)


def _text_for(name: str, type_line: str | None, oracle_text: str | None) -> str:
    """The document embedded for a card: name, type line, then rules text."""
    return "\n".join(p for p in (name, type_line, oracle_text) if p).strip()


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class EmbeddingClient:
    """Minimal OpenAI-compatible embeddings client."""

    def __init__(self, base_url: str = "", api_key: str = "", model: str = ""):
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/")
        self.api_key = api_key or s.llm_api_key
        self.model = model or s.llm_embed_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"User-Agent": _UA, "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()["data"]
        # OpenAI returns items in input order, but sort by index to be safe.
        return [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]


async def _representative_rows(session: AsyncSession, scope: str):
    """One (oracle_id, name, type_line, oracle_text) per oracle in scope (owned | all)."""
    stmt = (
        select(Card.oracle_id, Card.name, Card.type_line, Card.oracle_text)
        .where(Card.oracle_id.is_not(None))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )
    if scope == "owned":
        stmt = stmt.where(
            Card.scryfall_id.in_(select(CollectionCard.scryfall_id))
        )
    return (await session.execute(stmt)).all()


async def backfill_embeddings(
    session: AsyncSession,
    scope: str = "owned",
    client: EmbeddingClient | None = None,
    batch_size: int = 64,
) -> int:
    """Embed oracles in *scope* that lack a current-model vector. Returns how many were embedded."""
    client = client or EmbeddingClient()
    model = client.model
    have = set(
        (await session.execute(
            select(CardEmbedding.oracle_id).where(CardEmbedding.model == model)
        )).scalars().all()
    )
    todo = [r for r in await _representative_rows(session, scope) if r[0] not in have]
    embedded = 0
    for start in range(0, len(todo), batch_size):
        chunk = todo[start : start + batch_size]
        vectors = await client.embed([_text_for(r[1], r[2], r[3]) for r in chunk])
        for (oracle_id, *_), vec in zip(chunk, vectors, strict=False):
            norm = _normalize([float(x) for x in vec])
            await session.merge(
                CardEmbedding(oracle_id=oracle_id, model=model, dim=len(norm), vector=norm)
            )
        await session.commit()
        embedded += len(chunk)
    return embedded


async def similar_to_oracle(
    session: AsyncSession, oracle_id, limit: int = 12, scope: str = "owned"
) -> list[tuple]:
    """Nearest oracles to *oracle_id* by cosine similarity. Returns [(oracle_id, score), ...]."""
    query = await session.get(CardEmbedding, oracle_id)
    if query is None:
        return []
    stmt = select(CardEmbedding.oracle_id, CardEmbedding.vector).where(
        CardEmbedding.oracle_id != oracle_id, CardEmbedding.model == query.model
    )
    if scope == "owned":
        owned = select(Card.oracle_id).where(
            Card.scryfall_id.in_(select(CollectionCard.scryfall_id))
        )
        stmt = stmt.where(CardEmbedding.oracle_id.in_(owned))
    rows = (await session.execute(stmt)).all()
    scored = [(oid, _dot(query.vector, vec)) for oid, vec in rows]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:limit]


async def embedding_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(CardEmbedding)) or 0


async def run_backfill(scope: str = "owned") -> int:
    """CLI/admin entry point: open a session and backfill embeddings."""
    if not is_configured():
        raise RuntimeError("No embeddings endpoint configured (set SCRYME_LLM_BASE_URL).")
    async with SessionLocal() as session:
        return await backfill_embeddings(session, scope=scope)
