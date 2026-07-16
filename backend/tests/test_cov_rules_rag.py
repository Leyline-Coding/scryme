"""Coverage for src/rules_rag.py: rules_for_question ready-path + run_backfill_rules success.

The embedding client is faked (deterministic, no network)."""

import httpx
import pytest
from src import rules_rag
from src.rules_rag import chunk_rules, rules_for_question, run_backfill_rules

_SAMPLE = """Magic Comprehensive Rules

Contents
702. Keyword Abilities
Glossary

702. Keyword Abilities
702.19. Trample
702.19a Trample lets excess combat damage carry over.
702.20. Shroud
702.20a Shroud means a permanent can't be targeted.

Glossary

Trample
A keyword ability about excess combat damage.

Vigilance
Attacking doesn't cause the creature to tap.
"""


class FakeEmbed:
    model = "fake-embed"

    def __init__(self, *a, raise_exc=None, **kw):
        self.raise_exc = raise_exc

    async def embed(self, texts):
        if self.raise_exc:
            raise self.raise_exc
        return [[1.0, 0.0, 0.0] for _ in texts]


async def _ready_config(session):
    from src.llm import save_config
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)


@pytest.mark.asyncio
async def test_rules_for_question_returns_excerpts(session, monkeypatch):
    from src.rules_rag import backfill_rules
    await backfill_rules(session, chunk_rules(_SAMPLE), FakeEmbed())
    await _ready_config(session)
    monkeypatch.setattr(rules_rag, "EmbeddingClient", lambda **kw: FakeEmbed())
    out = await rules_for_question(session, "how does trample work?", k=2)
    assert out and any("702.19" in chunk for chunk in out)  # ready-path retrieval (128-136)


@pytest.mark.asyncio
async def test_rules_for_question_not_ready(session, monkeypatch):
    from src.rules_rag import backfill_rules
    await backfill_rules(session, chunk_rules(_SAMPLE), FakeEmbed())
    # Index built but no config -> cfg.ready is False -> [] (line 129-130).
    assert await rules_for_question(session, "trample?") == []


@pytest.mark.asyncio
async def test_rules_for_question_swallows_embed_error(session, monkeypatch):
    from src.rules_rag import backfill_rules
    await backfill_rules(session, chunk_rules(_SAMPLE), FakeEmbed())
    await _ready_config(session)
    monkeypatch.setattr(rules_rag, "EmbeddingClient",
                        lambda **kw: FakeEmbed(raise_exc=httpx.ConnectError("x")))
    assert await rules_for_question(session, "trample?") == []  # except branch (134-135)


@pytest.mark.asyncio
async def test_rules_for_question_blank(session):
    assert await rules_for_question(session, "   ") == []  # line 125-126: blank question


@pytest.mark.asyncio
async def test_run_backfill_rules_no_endpoint(session, tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_base_url", "")  # no configured endpoint
    rules_file = tmp_path / "rules.txt"
    rules_file.write_text(_SAMPLE, encoding="utf-8")
    with pytest.raises(RuntimeError, match="No embeddings endpoint"):
        await run_backfill_rules(str(rules_file))  # line 150-151


@pytest.mark.asyncio
async def test_run_backfill_rules_success(session, tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_base_url", "http://x/v1")
    monkeypatch.setattr(rules_rag, "EmbeddingClient", lambda **kw: FakeEmbed())
    rules_file = tmp_path / "rules.txt"
    rules_file.write_text(_SAMPLE, encoding="utf-8")
    embedded = await run_backfill_rules(str(rules_file))  # lines 147-153
    assert embedded >= 2
