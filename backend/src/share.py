"""Creating, resolving and revoking read-only share links (#80).

The token is generated and hashed the same way a device token is (:mod:`src.client_tokens`), and
for the same reason — but a share token is *more* exposed, not less: it travels in a URL that gets
pasted into a chat, a forum, or a screenshot. So it is high-entropy, stored only as a keyed hash,
and revocable at any moment.

Two decisions this module makes, both of which #80 left open:

**What a shared view exposes.** Prices and owned counts are **off by default** and enabled per link.
Sending someone a decklist should not also tell them what it is worth or how deep your collection
runs; that is a separate thing to volunteer, not a side effect of sharing a list.

**Expiry.** Revocation only — no timed expiry. A clock-based expiry needs a scheduler and a story
for what a half-expired link does, and it buys little over a Revoke button that acts instantly and
is visible on the page you shared from. Links show when they were last viewed, so a stale one is
easy to spot and retire.
"""

from __future__ import annotations

import datetime
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.client_tokens import hash_token
from src.models import Binder, Deck, ShareLink
from src.models.share_link import BINDER, DECK, KINDS

_TOKEN_BYTES = 24   # ~32 url-safe chars; unguessable, but still fits in a pasted link
# How stale ``last_viewed_at`` may get before a view refreshes it — see src.client_tokens.
_VIEW_RESOLUTION = datetime.timedelta(minutes=5)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _target_exists(session: AsyncSession, kind: str, target_id: int) -> bool:
    model = Deck if kind == DECK else Binder
    return await session.get(model, target_id) is not None


async def create_share_link(
    session: AsyncSession, kind: str, target_id: int, *, show_prices: bool = False
) -> tuple[ShareLink, str] | None:
    """Mint a link for one deck or binder. Returns (row, token), or None if the target is unknown.

    The token is returned once and never recoverable — but unlike a device token it is also written
    into a URL the owner can re-open, so losing it is a matter of revoking and re-sharing.
    """
    if kind not in KINDS or not await _target_exists(session, kind, target_id):
        return None
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    row = ShareLink(kind=kind, target_id=target_id, token_hash=hash_token(token),
                    show_prices=bool(show_prices))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, token


async def resolve_share_link(session: AsyncSession, token: str) -> ShareLink | None:
    """Resolve a presented token to a live link, touching ``last_viewed_at``. None if unusable."""
    if not token:
        return None
    row = (
        await session.execute(
            select(ShareLink).where(ShareLink.token_hash == hash_token(token))
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    now = _now()
    if row.last_viewed_at is None or now - row.last_viewed_at > _VIEW_RESOLUTION:
        row.last_viewed_at = now
        await session.commit()
    return row


async def links_for(session: AsyncSession, kind: str, target_id: int) -> list[ShareLink]:
    """Every link ever minted for one target, live first, newest first."""
    return list(
        (
            await session.execute(
                select(ShareLink)
                .where(ShareLink.kind == kind, ShareLink.target_id == target_id)
                .order_by(ShareLink.revoked_at.is_(None).desc(), ShareLink.created_at.desc())
            )
        ).scalars().all()
    )


async def revoke_share_link(session: AsyncSession, link_id: int) -> bool:
    """Withdraw a link. Idempotent; the row is kept as the record that it once existed."""
    row = await session.get(ShareLink, link_id)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = _now()
        await session.commit()
    return True


async def revoke_links_for(session: AsyncSession, kind: str, target_id: int) -> int:
    """Withdraw every live link to a target. Returns how many were revoked.

    Called when the target itself is deleted: a link outliving its deck would 404, which reads as a
    broken app rather than as "that isn't shared any more".
    """
    live = [link for link in await links_for(session, kind, target_id) if link.active]
    for link in live:
        link.revoked_at = _now()
    if live:
        await session.commit()
    return len(live)


__all__ = [
    "BINDER", "DECK", "create_share_link", "links_for", "resolve_share_link",
    "revoke_links_for", "revoke_share_link",
]
