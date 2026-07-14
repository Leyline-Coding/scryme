"""Comprehensive-rules RAG (#196): chunking, embed backfill, and retrieval."""

import pytest
from src.rules_rag import (
    backfill_rules,
    chunk_rules,
    retrieve_rules,
    rules_for_question,
    run_backfill_rules,
)

_SAMPLE = """Magic Comprehensive Rules

Contents
702. Keyword Abilities
Glossary

702. Keyword Abilities
702.19. Trample
702.19a Trample lets excess combat damage carry over.
702.19b The controller assigns lethal damage to blockers first.
702.20. Shroud
702.20a Shroud means a permanent can't be the target of spells or abilities.

Glossary

Trample
A keyword ability about excess combat damage.

Vigilance
A keyword ability; attacking doesn't cause the creature to tap.
"""


class FakeEmbed:
    """Deterministic 3-d embeddings keyed by a substring, for retrieval tests."""

    model = "fake-embed"
    _VECS = {"trample": [1.0, 0.0, 0.0], "shroud": [0.0, 1.0, 0.0],
             "vigilance": [0.0, 0.0, 1.0]}

    async def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            out.append(next((v for k, v in self._VECS.items() if k in low), [0.1, 0.1, 0.1]))
        return out


def test_chunk_rules_groups_subrules_and_glossary():
    chunks = chunk_rules(_SAMPLE)
    refs = {ref for ref, _ in chunks}
    assert "702.19 Trample" in refs and "702.20 Shroud" in refs
    trample = next(t for r, t in chunks if r == "702.19 Trample")
    assert "carry over" in trample and "lethal damage" in trample  # both subrules folded in
    assert "702.20" not in trample                                 # stops at the next rule
    assert any(r.startswith("Glossary: Trample") for r, _ in chunks)


@pytest.mark.asyncio
async def test_backfill_and_retrieve(session):
    embedded = await backfill_rules(session, chunk_rules(_SAMPLE), FakeEmbed())
    assert embedded >= 4
    # Idempotent: nothing new the second time.
    assert await backfill_rules(session, chunk_rules(_SAMPLE), FakeEmbed()) == 0

    top = await retrieve_rules(session, "how does trample work?", FakeEmbed(), k=1)
    assert top and "trample" in top[0][1].lower()


@pytest.mark.asyncio
async def test_rules_for_question_empty_without_index(session):
    # No chunks + no config -> graceful empty (Q&A falls back to oracle text + rulings).
    assert await rules_for_question(session, "does trample work?") == []


@pytest.mark.asyncio
async def test_run_backfill_rules_missing_file():
    with pytest.raises(RuntimeError):
        await run_backfill_rules("/no/such/rules.txt")
