"""Sign-in for the web UI when ``SCRYME_API_TOKEN`` is set.

There are still no accounts: the one access token that already protected ``/api/v1`` now protects
the pages too. Presenting it here sets a session cookie; ``src/security.py`` checks that cookie on
every other request.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from src import security
from src.config import get_settings
from src.routes._safe import local_redirect
from src.templating import templates

router = APIRouter(tags=["auth"], include_in_schema=False)

_MONTH = 60 * 60 * 24 * 30


def _page(request: Request, next_path: str, error: str = "", status_code: int = 200) -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"next": next_path, "error": error}, status_code=status_code
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/") -> Response:  # noqa: A002 - query name
    token = get_settings().api_token
    if not token or security.is_authenticated(request, token):
        return local_redirect(next)
    return _page(request, next)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request, token: str = Form(""), next: str = Form("/"),  # noqa: A002 - form name
) -> Response:
    expected = get_settings().api_token
    if not expected:
        return local_redirect(next)
    if not hmac.compare_digest(token.strip().encode(), expected.encode()):
        return _page(request, next, "That token is not right.", status_code=401)
    response = local_redirect(next)
    response.set_cookie(
        security.SESSION_COOKIE, security.session_value(expected), max_age=_MONTH,
        httponly=True, samesite="lax", secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
async def logout() -> Response:
    response = local_redirect("/login")
    response.delete_cookie(security.SESSION_COOKIE)
    return response
