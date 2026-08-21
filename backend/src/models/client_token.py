"""Per-device API credentials: labelled, revocable tokens for the apps that talk to an
instance (#204).

This is the useful 80% of "accounts" — per-client credentials, revocation, and a record of which
app did what — **without** human identity, login or sessions.
:doc:`ADR 0001 </development/adr/0001-single-collection-accounts>` explicitly separates the two and
keeps scryme single-collection; ``owner_id`` is that ADR's standing forward-compat hedge and is
always ``NULL`` today.

Only the **hash** of a token is stored, so a database dump (or a backup, or a support screenshot)
never yields a working credential. The plaintext is shown exactly once, at issue time.

Revoked tokens are kept rather than deleted. That is deliberate on two counts: the row is the audit
trail of a credential that once existed, and the presence of *any* row is what tells the API that
this instance has been locked down — see :func:`src.client_tokens.auth_required`.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base

# What a token is allowed to do. ``write`` implies ``read`` — there is no write-only client.
READ = "read"
WRITE = "write"
SCOPES = (READ, WRITE)


class ClientToken(Base):
    __tablename__ = "client_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(128))          # "Pixel scanner", "iPad app"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Leading characters of the secret, kept in the clear so the UI can tell two tokens apart
    # without holding anything usable.
    prefix: Mapped[str] = mapped_column(String(16), default="")
    scope: Mapped[str] = mapped_column(String(16), default=WRITE)

    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)  # ADR 0001 hedge; always NULL

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def active(self) -> bool:
        return self.revoked_at is None
