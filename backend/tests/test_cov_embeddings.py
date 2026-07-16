"""Coverage for src/embeddings.py: EmbeddingClient (config + HTTP over a mock), counts, backfill.

The real HTTP path is exercised via an httpx MockTransport — no network calls."""

import httpx
import pytest
from src import embeddings as emb
from src.embeddings import EmbeddingClient, embedding_count, run_backfill


def test_client_resolves_config_from_settings(monkeypatch):
    from src.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "llm_base_url", "http://env/v1/")
    monkeypatch.setattr(s, "llm_api_key", "envkey")
    monkeypatch.setattr(s, "llm_embed_model", "envmodel")
    # Explicit args win; base_url gets its trailing slash stripped.
    c = EmbeddingClient(base_url="http://x/v1/", api_key="k", model="m")
    assert c.base_url == "http://x/v1" and c.api_key == "k" and c.model == "m"
    # Falls back to settings when args omitted.
    d = EmbeddingClient()
    assert d.base_url == "http://env/v1" and d.api_key == "envkey" and d.model == "envmodel"


@pytest.mark.asyncio
async def test_embed_empty_returns_empty():
    assert await EmbeddingClient(base_url="http://x").embed([]) == []


@pytest.mark.asyncio
async def test_embed_posts_and_sorts_by_index(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        # Return out of order to verify the client re-sorts by index.
        return httpx.Response(200, json={"data": [
            {"embedding": [0.9, 0.9], "index": 1},
            {"embedding": [0.1, 0.2], "index": 0},
        ]})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(emb.httpx, "AsyncClient", lambda **kw: orig(transport=transport, **kw))

    client = EmbeddingClient(base_url="http://x/v1", api_key="key", model="m")
    vecs = await client.embed(["a", "b"])
    assert vecs == [[0.1, 0.2], [0.9, 0.9]]  # re-sorted by index
    assert captured["url"] == "http://x/v1/embeddings" and captured["auth"] == "Bearer key"


@pytest.mark.asyncio
async def test_embedding_count(session):
    assert await embedding_count(session) == 0


@pytest.mark.asyncio
async def test_run_backfill_configured_no_cards(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_base_url", "http://x/v1")
    # No owned cards -> nothing to embed, so the real client is never called (no network).
    assert await run_backfill("owned") == 0
