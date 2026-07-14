"""Retrieval over the MTG Comprehensive Rules for grounded rules Q&A (#196).

Chunks the rules by top-level rule (``NNN.N`` + its lettered subrules + examples) plus glossary
entries, embeds each chunk (reusing the #176 embedding client), and retrieves the most relevant
chunks for a question. Vectors are L2-normalized ``float8[]`` (cosine = dot product in Python).

Graceful: if the index isn't built or no endpoint is configured, retrieval returns ``[]`` and rules
Q&A falls back to oracle text + rulings only.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal
from src.embeddings import EmbeddingClient, _dot, _normalize
from src.models import RulesChunk

_TOP_RULE = re.compile(r"^(\d{3}\.\d+)\.\s+(.*)$")   # "702.19. Trample"
_SECTION = re.compile(r"^\d{3}\.\s+\S")               # "702. Keyword Abilities" (a section header)
_DEFAULT_RULES_FILE = Path(__file__).resolve().parent / "data" / "comprehensive_rules.txt"


def chunk_rules(text: str) -> list[tuple[str, str]]:
    """Split the rules into (ref, chunk_text): one per top-level rule + one per glossary term."""
    lines = text.splitlines()
    gloss_positions = [i for i, ln in enumerate(lines) if ln.strip() == "Glossary"]
    gloss_at = gloss_positions[-1] if len(gloss_positions) >= 2 else None
    rule_lines = lines[:gloss_at] if gloss_at is not None else lines

    chunks: list[tuple[str, str]] = []
    ref: str | None = None
    title = ""
    buf: list[str] = []

    def flush() -> None:
        if ref and buf:
            body = "\n".join(buf).strip()
            chunks.append((f"{ref} {title}".strip()[:64], f"{ref}. {title}\n{body}".strip()))

    for ln in rule_lines:
        m = _TOP_RULE.match(ln)
        if m:
            flush()
            ref, title, buf = m.group(1), m.group(2).strip(), []
        elif _SECTION.match(ln):  # bare section header — don't fold into the previous rule
            flush()
            ref, buf = None, []
        elif ref and ln.strip():
            buf.append(ln.strip())
    flush()

    if gloss_at is not None:
        entry: list[str] = []
        for ln in lines[gloss_at + 1:]:
            if not ln.strip():
                if entry:
                    chunks.append((f"Glossary: {entry[0].strip()}"[:64], "\n".join(entry).strip()))
                    entry = []
            else:
                entry.append(ln)
        if entry:
            chunks.append((f"Glossary: {entry[0].strip()}"[:64], "\n".join(entry).strip()))

    return [(r, t) for r, t in chunks if len(t) > 20]


async def backfill_rules(
    session: AsyncSession, chunks: list[tuple[str, str]], client: EmbeddingClient,
    batch_size: int = 64,
) -> int:
    """Embed and store chunks not already embedded for the current model. Returns count embedded."""
    model = client.model
    have = set((await session.execute(
        select(RulesChunk.ref).where(RulesChunk.model == model)
    )).scalars().all())
    todo = [(ref, txt) for ref, txt in chunks if ref not in have]
    done = 0
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        vectors = await client.embed([txt for _ref, txt in batch])
        for (ref, txt), vec in zip(batch, vectors, strict=False):
            session.add(RulesChunk(ref=ref, text=txt, model=model,
                                   vector=_normalize([float(x) for x in vec])))
        await session.commit()
        done += len(batch)
    return done


async def retrieve_rules(
    session: AsyncSession, question: str, client: EmbeddingClient, k: int = 4,
) -> list[tuple[str, str, float]]:
    """Top-k rules chunks nearest the question by cosine. Returns [(ref, text, score), ...]."""
    qvec = _normalize([float(x) for x in (await client.embed([question]))[0]])
    rows = (await session.execute(select(RulesChunk.ref, RulesChunk.text, RulesChunk.vector))).all()
    scored = [(ref, text, _dot(qvec, vec)) for ref, text, vec in rows]
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:k]


async def rules_for_question(session: AsyncSession, question: str, k: int = 4) -> list[str]:
    """Convenience for the route: retrieve rule excerpts for a question, or [] if unavailable."""
    from src.llm import get_config

    if not question.strip():
        return []
    if not await session.scalar(select(func.count()).select_from(RulesChunk)):
        return []  # index not built
    cfg = await get_config(session)
    if not cfg.ready:
        return []
    client = EmbeddingClient(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.embed_model)
    try:
        top = await retrieve_rules(session, question, client, k)
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return []
    return [f"{ref}\n{text}" for ref, text, _score in top]


async def run_backfill_rules(file_path: str | None = None) -> int:
    """CLI entry point: chunk the rules file and embed it (needs a configured endpoint)."""
    from src.llm import get_config

    path = Path(file_path) if file_path else _DEFAULT_RULES_FILE
    if not path.exists():
        raise RuntimeError(f"Rules file not found: {path} (pass --file).")
    chunks = chunk_rules(path.read_text(encoding="utf-8"))
    async with SessionLocal() as session:
        cfg = await get_config(session)
        if not cfg.base_url:
            raise RuntimeError("No embeddings endpoint configured (Settings -> AI or env).")
        client = EmbeddingClient(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.embed_model)
        return await backfill_rules(session, chunks, client)
