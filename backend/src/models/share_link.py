"""Read-only share links: a public URL for one deck or binder, and nothing else (#80).

scryme is otherwise entirely private, so a share link is the only way anything leaves it. That
shapes the model:

* **The token is stored hashed**, exactly like a device token (:mod:`src.client_tokens`). The token
  lives in a URL that gets pasted into chats and forums, so it is the most likely credential in the
  system to leak — and a database dump or backup must not hand over working links on top of that.
* **A link names one target.** It carries a kind and an id, not a query someone could widen, so a
  shared deck can never be walked outward into the rest of the collection.
* **Revoked rows are kept.** A link that was shared publicly and then withdrawn is worth a record;
  deleting the row would make "was this ever shared?" unanswerable.
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base

DECK = "deck"
BINDER = "binder"
KINDS = (DECK, BINDER)


class ShareLink(Base):
    __tablename__ = "share_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))       # deck | binder
    target_id: Mapped[int] = mapped_column(Integer, index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Whether the public view shows prices and how many copies you own. Off by default: sharing a
    # decklist should not disclose what it is worth, or how deep your collection is, unless that is
    # a thing you chose.
    show_prices: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_viewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def active(self) -> bool:
        return self.revoked_at is None
