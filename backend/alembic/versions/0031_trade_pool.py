"""trade pools: staged in/out card lists for a single trade (#331)

A pool is a chosen list ("these cards, for this trade"), unlike the surplus binder in
``src.trade`` which is derived from what you own. Items are self-describing — printing plus the
finish/condition/language that carry the value — and reference the originating ``collection_card``
only advisorily (ON DELETE SET NULL), so editing or deleting a stack cannot silently rewrite a
staged trade. See ADR 0002 (cross-device trading), D1/D2/D4.

Revision ID: 0031_trade_pool
Revises: 0030_pref_card_size
Create Date: 2026-08-21

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_trade_pool"
down_revision: str | None = "0030_pref_card_size"


def upgrade() -> None:
    op.create_table(
        "trade_pool",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("partner", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("note", sa.Text(), nullable=True),
        # The agreed valuation basis, frozen at creation so both sides of a trade see one total.
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="usd"),
        sa.Column("price_source", sa.String(length=32), nullable=False,
                  server_default="tcgplayer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "trade_pool_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pool_id", sa.Integer(),
                  sa.ForeignKey("trade_pool.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cards.scryfall_id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("finish", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("condition", sa.String(length=32), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("collection_card_id", sa.Integer(),
                  sa.ForeignKey("collection_card.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("pool_id", "direction", "scryfall_id", "finish", "condition",
                            "language", name="uq_trade_pool_item"),
    )
    op.create_index("ix_trade_pool_item_pool_id", "trade_pool_item", ["pool_id"])
    op.create_index("ix_trade_pool_item_scryfall_id", "trade_pool_item", ["scryfall_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_pool_item_scryfall_id", table_name="trade_pool_item")
    op.drop_index("ix_trade_pool_item_pool_id", table_name="trade_pool_item")
    op.drop_table("trade_pool_item")
    op.drop_table("trade_pool")
