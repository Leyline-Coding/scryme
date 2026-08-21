"""trade commit: what a pool has actually applied to the collection (#332)

``applied_quantity`` records how much of each staged line has been reconciled into the collection,
so a pool can be committed a card at a time and what's left staged is exactly what hasn't happened
yet. That granularity is required rather than cosmetic: ADR 0002 (D3) needs "apply these moves
atomically at the card level, reporting exactly which ones applied", because a trade's two halves
will eventually be committed on two different machines and a half-applied trade must be
distinguishable from a completed one by both parties.

Revision ID: 0032_trade_commit
Revises: 0031_trade_pool
Create Date: 2026-08-21

"""
import sqlalchemy as sa
from alembic import op

revision: str = "0032_trade_commit"
down_revision: str | None = "0031_trade_pool"


def upgrade() -> None:
    op.add_column(
        "trade_pool_item",
        sa.Column("applied_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "trade_pool", sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("trade_pool", "committed_at")
    op.drop_column("trade_pool_item", "applied_quantity")
