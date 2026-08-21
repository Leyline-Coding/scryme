"""Issuing, verifying and revoking per-device API tokens (#204).

Two questions #204 left open, and the answers this module implements:

**Scopes.** ``read`` and ``write`` from the start, rather than a single ``full`` scope. Retrofitting
scopes later means either invalidating every issued token or silently defaulting them to full
access, and a silent privilege grant is a bad thing to owe your future self. Enforcement is by HTTP
method rather than per-endpoint annotation — a safe method needs ``read``, anything else needs
``write`` — so there is no way to add an endpoint and forget to protect it.

**What happens when the last token is revoked.** The API stays **closed**, and revoking everything
hard-denies rather than falling open. Inferring "no tokens means no auth wanted" would turn a
revocation — the one action a person takes *because* something went wrong — into the thing that
re-opens the instance. Since revoked rows are kept, "has this instance ever issued a token?" is
simply "does any row exist?", and it can't be undone by accident. Nobody is locked out of scryme
itself by this: the HTML UI is not token-gated, so a new token is always one click away.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models import ClientToken
from src.models.client_token import READ, SCOPES, WRITE

# A recognizable marker so a leaked token is identifiable in a log or by a secret scanner, and so
# an operator pasting the wrong string into the field gets an obvious mismatch.
TOKEN_MARKER = "scryme_"
_SECRET_BYTES = 32          # 256 bits of entropy
_PREFIX_CHARS = 8
# How stale ``last_used_at`` may get before a successful call refreshes it. Without this every
# read of the API would also write a row.
_LAST_USED_RESOLUTION = datetime.timedelta(seconds=60)

# HTTP methods that only read. Everything else needs the write scope.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def hash_token(token: str) -> str:
    """SHA-256 of the token.

    A plain hash is the right primitive here, not a password KDF: these secrets are 256 bits of
    ``secrets``-grade randomness, so there is no dictionary to slow an attacker down against, and a
    deliberately slow hash would only tax every legitimate API request.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_scope(value: str | None) -> str:
    return value if value in SCOPES else WRITE


def scope_for_method(method: str) -> str:
    return READ if (method or "").upper() in _SAFE_METHODS else WRITE


def scope_allows(granted: str, needed: str) -> bool:
    """``write`` implies ``read``; a read token may only read."""
    return granted == WRITE or needed == READ


async def issue_token(
    session: AsyncSession, label: str, scope: str = WRITE
) -> tuple[ClientToken, str]:
    """Mint a token. Returns the row and the plaintext, which is never recoverable again."""
    secret = f"{TOKEN_MARKER}{secrets.token_urlsafe(_SECRET_BYTES)}"
    row = ClientToken(
        label=(label or "").strip()[:128] or "Unnamed device",
        token_hash=hash_token(secret),
        prefix=secret[len(TOKEN_MARKER):][:_PREFIX_CHARS],
        scope=normalize_scope(scope),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, secret


async def list_tokens(session: AsyncSession) -> list[ClientToken]:
    """Every token ever issued, active first, newest first — revoked ones are the audit trail."""
    return list(
        (
            await session.execute(
                select(ClientToken).order_by(
                    ClientToken.revoked_at.is_(None).desc(), ClientToken.created_at.desc()
                )
            )
        ).scalars().all()
    )


async def revoke_token(session: AsyncSession, token_id: int) -> bool:
    """Revoke a token. Idempotent; the row is kept. False if there was no such token."""
    row = await session.get(ClientToken, token_id)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = _now()
        await session.commit()
    return True


async def auth_required(session: AsyncSession) -> bool:
    """Whether ``/api/v1`` demands a credential — see the module docstring.

    True once an ``SCRYME_API_TOKEN`` is configured *or* this instance has ever issued a device
    token. Revoking every token does not turn authentication back off.
    """
    if get_settings().api_token:
        return True
    return bool(await session.scalar(select(func.count()).select_from(ClientToken)))


async def verify_token(session: AsyncSession, provided: str, needed: str) -> ClientToken | None:
    """Resolve a presented secret to a live token with sufficient scope, or None.

    Touches ``last_used_at`` so the settings page can show which devices are actually in use — and,
    more usefully, which ones are not and could be revoked.
    """
    if not provided:
        return None
    row = (
        await session.execute(
            select(ClientToken).where(ClientToken.token_hash == hash_token(provided))
        )
    ).scalar_one_or_none()
    # Looking the row up *by* the hash is what makes this safe: there is no secret-dependent
    # comparison to time, because the database index does the matching on a value an attacker
    # would have to invert SHA-256 to influence.
    if row is None or row.revoked_at is not None:
        return None
    if not scope_allows(row.scope, needed):
        return None

    now = _now()
    if row.last_used_at is None or now - row.last_used_at > _LAST_USED_RESOLUTION:
        row.last_used_at = now
        await session.commit()
    return row
