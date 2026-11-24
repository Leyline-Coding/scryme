"""The `scan_batch` table: the replay record for batch scan ingests (#164).

A scanner is the one client guaranteed to retry. It runs on a phone, on a home network, over a
connection that drops mid-box, and it queues scans offline to send later — so the same batch will
arrive twice, and "add these 40 cards" is not an operation that can safely happen twice.

Each batch is therefore keyed by the client's ``Idempotency-Key`` and the response is stored
verbatim. A replay returns the original answer rather than re-applying the merge, so a retry after
a timeout is indistinguishable (to the collection) from the request having succeeded once — which
it did. Storing the whole response, rather than just the key, is what lets the replay be *useful*:
the scanner gets back the same resolved printings and unmatched rows it would have got the first
time, so it can finish reconciling a batch it never saw the answer to.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ScanBatch(Base):
    __tablename__ = "scan_batch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The client's own key for this batch. Unique, and that constraint is load-bearing: it is what
    # makes two concurrent retries of the same batch resolve to one applied merge rather than a
    # check-then-act race.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    response: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
