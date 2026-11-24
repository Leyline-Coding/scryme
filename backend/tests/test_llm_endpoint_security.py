"""Security regressions for the configurable LLM / embeddings endpoint.

Covers: a stored API key never follows the config to a different origin; only plain http(s)
endpoints are accepted; link-local / metadata addresses are refused (also when a host name
resolves there); redirects are not followed; and the connection test gives uniform messages.

All HTTP goes through an httpx MockTransport and the resolver is monkeypatched — no network, no DNS.
"""

import socket

import httpx
import pytest
from src import embeddings as emb
from src import llm, llm_endpoint
from src.config import get_settings
from src.embeddings import EmbeddingClient
from src.llm import ChatClient, LLMConfig, get_config, save_config
from src.llm import test_connection as check_connection
from src.llm_endpoint import (
    EndpointError,
    endpoint_origin,
    ensure_endpoint_allowed,
    parse_endpoint,
    same_origin,
)
from src.models import LLMSettings
from src.routes import ai as ai_routes
from starlette.requests import Request

_CHAT_OK = {"choices": [{"message": {"content": "ok"}}]}
_PUBLIC_IP = "93.184.216.34"


def _req(query_string=b"") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": query_string})


def _body(resp) -> str:
    return resp.body.decode()


@pytest.fixture
def resolver(monkeypatch):
    """Replace DNS: every host resolves to a public address unless listed in ``table``."""
    table: dict[str, list[str]] = {}
    seen: list[str] = []

    async def fake_resolve(host, port):
        seen.append(host)
        answer = table.get(host, [_PUBLIC_IP])
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(llm_endpoint, "_resolve", fake_resolve)
    fake_resolve.table = table
    fake_resolve.seen = seen
    return fake_resolve


@pytest.fixture
def wire(monkeypatch):
    """Route every httpx.AsyncClient through a MockTransport, recording requests + client kwargs."""
    state = {"requests": [], "kwargs": [], "handler": lambda request: httpx.Response(
        200, json=_CHAT_OK)}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["handler"](request)

    orig = httpx.AsyncClient

    def factory(**kw):
        state["kwargs"].append(kw)
        return orig(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(llm.httpx, "AsyncClient", factory)
    return state


async def _save(session, base_url, api_key=""):
    return await ai_routes.ai_save(base_url=base_url, api_key=api_key, chat_model="m",
                                   embed_model="e", enabled="1", session=session)


# --- A. a stored key belongs to the endpoint it was saved for ------------------------------------

@pytest.mark.asyncio
async def test_key_is_not_sent_to_a_different_host(session, resolver, wire):
    """Owner saves host A + key; a blank-key save repoints to host B; B must never see the key."""
    await _save(session, "https://api.owner.example/v1", "sk-owner-secret")
    resp = await _save(session, "https://attacker.example/v1", "")
    assert resp.status_code == 303 and "key_cleared=1" in resp.headers["location"]

    row = await session.get(LLMSettings, 1)
    assert row.api_key_enc is None  # cleared at rest, not merely hidden
    assert (await get_config(session)).api_key == ""

    test = await ai_routes.ai_test(_req(), session)  # what an attacker would trigger next
    assert test.status_code == 200
    assert len(wire["requests"]) == 1
    sent = wire["requests"][0]
    assert sent.url.host == "attacker.example"
    assert "authorization" not in sent.headers
    assert "sk-owner-secret" not in str(sent.headers) and b"sk-owner-secret" not in sent.content


@pytest.mark.asyncio
@pytest.mark.parametrize("new_url", [
    "https://other.example/v1",          # host
    "https://api.owner.example:8443/v1",  # port
    "http://api.owner.example/v1",       # scheme (downgrade would leak the key in clear text)
    "https://api.owner.example.evil.test/v1",
    "",                                  # endpoint removed
])
async def test_origin_change_with_blank_key_clears_key(session, new_url):
    await save_config(session, base_url="https://api.owner.example/v1", api_key="sk-owner-secret",
                      chat_model="m", embed_model="e", enabled=True)
    cleared = await save_config(session, base_url=new_url, api_key="", chat_model="m",
                                embed_model="e", enabled=True)
    assert cleared is True
    assert (await session.get(LLMSettings, 1)).api_key_enc is None
    assert (await get_config(session)).api_key == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("new_url", [
    "https://api.owner.example/v2",          # path-only change
    "https://api.owner.example/",
    "https://API.Owner.Example:443/v1",      # same origin once normalised
    "https://api.owner.example./v1",
])
async def test_same_origin_path_change_keeps_key(session, new_url):
    await save_config(session, base_url="https://api.owner.example/v1", api_key="sk-owner-secret",
                      chat_model="m", embed_model="e", enabled=True)
    cleared = await save_config(session, base_url=new_url, api_key="", chat_model="m",
                                embed_model="e", enabled=True)
    assert cleared is False
    cfg = await get_config(session)
    assert cfg.api_key == "sk-owner-secret" and cfg.base_url == new_url


@pytest.mark.asyncio
async def test_blank_key_with_unchanged_url_keeps_key(session):
    await _save(session, "http://localhost:11434/v1", "local-key")
    resp = await _save(session, "http://localhost:11434/v1", "")
    assert resp.headers["location"] == "/ai?saved=1"  # no "key cleared" notice
    assert (await get_config(session)).api_key == "local-key"


@pytest.mark.asyncio
async def test_new_key_replaces_old_key_even_across_hosts(session, resolver, wire):
    await _save(session, "https://api.owner.example/v1", "sk-old")
    resp = await _save(session, "https://api.new.example/v1", "sk-new")
    assert resp.headers["location"] == "/ai?saved=1"
    assert (await get_config(session)).api_key == "sk-new"
    await ai_routes.ai_test(_req(), session)
    assert wire["requests"][0].headers["authorization"] == "Bearer sk-new"


@pytest.mark.asyncio
async def test_settings_page_says_key_was_cleared_without_revealing_it(session):
    await _save(session, "https://api.owner.example/v1", "sk-owner-secret")
    await _save(session, "https://other.example/v1", "")
    page = _body(await ai_routes.ai_settings(_req(b"saved=1&key_cleared=1"), session))
    assert "stored API key was cleared" in page
    assert "(set)" not in page and "sk-owner-secret" not in page
    # Without the flag the notice is absent.
    assert "stored API key was cleared" not in _body(
        await ai_routes.ai_settings(_req(b"saved=1"), session))


@pytest.mark.asyncio
async def test_env_key_is_not_paired_with_a_db_configured_host(session, monkeypatch, resolver,
                                                              wire):
    """SCRYME_LLM_API_KEY must not ride along to an endpoint configured in-app."""
    s = get_settings()
    monkeypatch.setattr(s, "llm_base_url", "https://api.owner.example/v1")
    monkeypatch.setattr(s, "llm_api_key", "sk-env-secret")
    assert (await get_config(session)).api_key == "sk-env-secret"  # env-only config: unchanged

    await _save(session, "https://attacker.example/v1", "")
    cfg = await get_config(session)
    assert cfg.base_url == "https://attacker.example/v1" and cfg.api_key == ""

    wire["handler"] = lambda request: httpx.Response(
        200, json={"data": [{"embedding": [1.0], "index": 0}], **_CHAT_OK})
    client = EmbeddingClient(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.embed_model)
    assert client.api_key == ""
    await client.embed(["text"])
    await ChatClient(cfg).chat([{"role": "user", "content": "hi"}])
    assert len(wire["requests"]) == 2
    for sent in wire["requests"]:
        assert sent.url.host == "attacker.example" and "authorization" not in sent.headers


def test_embedding_client_env_key_only_for_the_env_origin(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_base_url", "https://api.owner.example/v1")
    monkeypatch.setattr(s, "llm_api_key", "sk-env-secret")
    assert EmbeddingClient().api_key == "sk-env-secret"
    assert EmbeddingClient(base_url="https://api.owner.example/other").api_key == "sk-env-secret"
    assert EmbeddingClient(base_url="https://elsewhere.example/v1").api_key == ""
    assert EmbeddingClient(base_url="https://elsewhere.example/v1", api_key="k").api_key == "k"


# --- B1. only http(s) URLs with a host, no embedded credentials ----------------------------------

_BAD_URLS = [
    ("file:///etc/passwd", "scheme"),
    ("gopher://127.0.0.1:6379/_INFO", "scheme"),
    ("ftp://example.com/v1", "scheme"),
    ("localhost:11434/v1", "scheme"),
    ("//example.com/v1", "scheme"),
    ("http:///v1", "host"),
    ("http://example.com:99999/v1", "host"),
    ("https://user:pass@example.com/v1", "credentials"),
    ("https://token@example.com/v1", "credentials"),
]


@pytest.mark.parametrize(("url", "code"), _BAD_URLS)
def test_parse_endpoint_rejects(url, code):
    with pytest.raises(EndpointError) as exc:
        parse_endpoint(url)
    assert exc.value.code == code
    assert url not in exc.value.message  # fixed text, never the input


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.20:11434/v1",
    "http://10.0.0.5/v1", "http://[::1]:11434/v1", "http://ollama:11434/v1",
    "https://api.openai.com/v1", "https://openrouter.ai/api/v1",
])
def test_parse_endpoint_accepts_local_and_public(url):
    assert parse_endpoint(url).host


def test_origin_normalisation():
    assert endpoint_origin("HTTP://Example.COM/v1") == ("http", "example.com", 80)
    assert endpoint_origin("https://example.com:8443") == ("https", "example.com", 8443)
    assert endpoint_origin("file:///x") is None
    assert same_origin("http://a.test/v1", "http://a.test:80/v2")
    assert not same_origin("http://a.test/v1", "https://a.test/v1")
    assert not same_origin("http://a.test/v1", "")
    assert same_origin("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "code"), _BAD_URLS)
async def test_save_rejects_bad_url_and_writes_nothing(session, url, code):
    await _save(session, "http://localhost:11434/v1", "local-key")
    resp = await _save(session, url, "attacker-key")
    assert resp.status_code == 303 and resp.headers["location"] == f"/ai?error={code}"
    cfg = await get_config(session)
    assert cfg.base_url == "http://localhost:11434/v1" and cfg.api_key == "local-key"


@pytest.mark.asyncio
async def test_settings_page_shows_fixed_error_and_never_echoes_the_query(session):
    page = _body(await ai_routes.ai_settings(_req(b"error=scheme"), session))
    assert "Settings not saved." in page and "must start with http:// or https://" in page
    odd = _body(await ai_routes.ai_settings(_req(b"error=%3Cscript%3Ex"), session))
    assert "Settings not saved." not in odd and "<script>x" not in odd


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://127.0.0.1:6379/_x",
                                 "http://user:pw@example.com/v1"])
async def test_clients_reject_bad_url_at_call_time(url, resolver, wire):
    """A bad URL already in the DB / env (saved before this check existed) is refused per call."""
    with pytest.raises(EndpointError):
        await ChatClient(LLMConfig(base_url=url, api_key="k", enabled=True)).chat(
            [{"role": "user", "content": "x"}])
    with pytest.raises(EndpointError):
        await EmbeddingClient(base_url=url, api_key="k", model="m").embed(["x"])
    assert wire["requests"] == [] and wire["kwargs"] == []  # no client was even built
    ok, msg = await check_connection(LLMConfig(base_url=url, api_key="k", enabled=True))
    assert not ok and url not in msg


# --- B2. link-local / metadata range and 0.0.0.0 -------------------------------------------------

_BLOCKED_LITERALS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.0.1:8080/v1",
    "http://0.0.0.0:11434/v1",
    "http://[fe80::1]/v1",
    "http://[fe80::1%25eth0]/v1",
    "http://[::ffff:169.254.169.254]/v1",
    "http://[::]/v1",
    "http://2852039166/v1",          # 169.254.169.254 as a decimal integer
    "http://0xa9.0xfe.0xa9.0xfe/v1",  # ... and in hex
    "http://0/v1",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", _BLOCKED_LITERALS)
async def test_blocked_literal_refused_at_save_and_call(session, url, wire):
    with pytest.raises(EndpointError) as exc:
        parse_endpoint(url)
    assert exc.value.code == "blocked"
    resp = await _save(session, url, "")
    assert resp.headers["location"] == "/ai?error=blocked"
    assert not await llm.has_config_row(session)
    with pytest.raises(EndpointError):
        await ChatClient(LLMConfig(base_url=url, enabled=True)).chat([])
    assert wire["requests"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [
    ["169.254.169.254"],
    [_PUBLIC_IP, "169.254.169.254"],       # every resolved address is checked, not just the first
    ["fe80::1%eth0"],
    ["::ffff:169.254.169.254"],
    ["0.0.0.0"],
])
async def test_hostname_resolving_to_blocked_range_is_refused(session, resolver, wire, addresses):
    resolver.table["metadata.attacker.example"] = addresses
    url = "http://metadata.attacker.example/v1"
    parse_endpoint(url)  # the name itself is fine — only resolution reveals the target
    with pytest.raises(EndpointError) as exc:
        await ensure_endpoint_allowed(url)
    assert exc.value.code == "blocked"

    with pytest.raises(EndpointError):
        await ChatClient(LLMConfig(base_url=url, api_key="k", enabled=True)).chat([])
    with pytest.raises(EndpointError):
        await EmbeddingClient(base_url=url, api_key="k", model="m").embed(["x"])
    assert wire["requests"] == []

    await _save(session, url, "k")
    test = _body(await ai_routes.ai_test(_req(), session))
    assert "not allowed" in test and wire["requests"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["::1", "127.0.0.1"], ["192.168.1.20"],
                                       ["10.1.2.3"], ["fd00::5"], [_PUBLIC_IP]])
async def test_local_and_private_addresses_stay_allowed(resolver, wire, addresses):
    resolver.table["ollama.lan"] = addresses
    cfg = LLMConfig(base_url="http://ollama.lan:11434/v1", chat_model="m", enabled=True)
    assert await ChatClient(cfg).chat([{"role": "user", "content": "x"}]) == "ok"
    assert resolver.seen == ["ollama.lan"]
    ok, msg = await check_connection(cfg)
    assert ok and "Connected" in msg


@pytest.mark.asyncio
async def test_real_resolver_handles_literals_and_localhost_without_dns():
    assert (await ensure_endpoint_allowed("http://127.0.0.1:11434/v1")).host == "127.0.0.1"
    assert (await ensure_endpoint_allowed("http://localhost:11434/v1")).host == "localhost"


# --- B3. redirects are not followed ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_and_embeddings_do_not_follow_redirects(resolver, wire):
    wire["handler"] = lambda request: httpx.Response(
        307, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    cfg = LLMConfig(base_url="https://api.owner.example/v1", api_key="k", chat_model="m",
                    enabled=True)
    with pytest.raises(httpx.HTTPStatusError):
        await ChatClient(cfg).chat([{"role": "user", "content": "x"}])
    with pytest.raises(httpx.HTTPStatusError):
        await EmbeddingClient(base_url=cfg.base_url, api_key="k", model="m").embed(["x"])
    assert [r.url.host for r in wire["requests"]] == ["api.owner.example"] * 2
    assert all(kw.get("follow_redirects") is False for kw in wire["kwargs"])  # explicit
    ok, msg = await check_connection(cfg)
    assert not ok and msg == "HTTP 307 from the endpoint."


# --- B4. uniform connection-test messages --------------------------------------------------------

class _Raises:
    def __init__(self, exc):
        self.exc = exc

    async def chat(self, messages, **kwargs):
        raise self.exc


@pytest.mark.asyncio
async def test_connection_failures_are_indistinguishable(caplog):
    cfg = LLMConfig(base_url="http://10.0.0.9:6379/v1", chat_model="m", enabled=True)
    failures = [
        httpx.ConnectError("All connection attempts failed"),                      # closed port
        httpx.RemoteProtocolError("Server disconnected without sending a response."),  # non-HTTP
        httpx.ReadError("[Errno 104] Connection reset by peer"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.LocalProtocolError("illegal header"),
    ]
    messages = set()
    with caplog.at_level("WARNING", logger="src.llm"):
        for exc in failures:
            ok, msg = await check_connection(cfg, client=_Raises(exc))
            assert not ok and str(exc) not in msg
            messages.add(msg)
    assert messages == {"Could not connect to the endpoint."}
    # The detail is still available to the operator, in the server log.
    assert "All connection attempts failed" in caplog.text
    assert "Server disconnected" in caplog.text


@pytest.mark.asyncio
async def test_connection_dns_failure_reads_like_any_other_failure(resolver):
    resolver.table["nope.invalid"] = socket.gaierror(-2, "Name or service not known")
    ok, msg = await check_connection(
        LLMConfig(base_url="http://nope.invalid/v1", chat_model="m", enabled=True))
    assert not ok and msg == "Could not connect to the endpoint."


@pytest.mark.asyncio
async def test_connection_never_echoes_response_bodies(resolver, wire):
    cfg = LLMConfig(base_url="http://10.0.0.9:8080/v1", chat_model="m", enabled=True)
    secret = "root:x:0:0:internal-admin-page"

    wire["handler"] = lambda request: httpx.Response(404, text=secret)
    ok, msg = await check_connection(cfg)
    assert not ok and msg == "HTTP 404 from the endpoint."

    for response in (httpx.Response(200, text=secret),                 # not JSON
                     httpx.Response(200, json={"error": secret}),      # wrong shape
                     httpx.Response(200, json=[secret]),
                     httpx.Response(200, json={"choices": [{"message": {"content": None}}]})):
        wire["handler"] = lambda request, r=response: r
        ok, msg = await check_connection(cfg)
        assert not ok and secret not in msg
        assert msg == "The endpoint answered, but not with an OpenAI-compatible chat response."


@pytest.mark.asyncio
async def test_ai_test_route_uses_the_fixed_messages(session, resolver, wire):
    await _save(session, "http://10.0.0.9:6379/v1", "")

    def boom(request):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    wire["handler"] = boom
    page = _body(await ai_routes.ai_test(_req(), session))
    assert "Could not connect to the endpoint." in page and "Server disconnected" not in page


def test_embeddings_module_shares_the_patched_httpx():
    """Guards the fixtures above: both clients build their AsyncClient from the same module."""
    assert emb.httpx is llm.httpx
