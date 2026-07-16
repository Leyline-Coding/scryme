"""Coverage for src/routes/_safe.py — same-origin redirect construction + fallbacks."""

from src.routes._safe import local_redirect


def test_local_redirect_keeps_local_path_and_query():
    resp = local_redirect("/search?q=bolt")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/search?q=bolt"


def test_local_redirect_strips_scheme_and_host():
    # An absolute URL is reduced to just its path — never leaves this origin.
    resp = local_redirect("http://evil.example/search?q=x")
    assert resp.headers["location"] == "/search?q=x"


def test_local_redirect_protocol_relative_falls_back_to_root():
    # A rebuilt "//host…" (protocol-relative) target is rejected -> app root (line 21).
    resp = local_redirect("http://evil.example//phish")
    assert resp.headers["location"] == "/"


def test_local_redirect_custom_status():
    resp = local_redirect("/x", status_code=307)
    assert resp.status_code == 307
