"""price_snapshot + card_price_point tables

Revision ID: 0005_price_history
Revises: 0004_decks
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_price_history"
down_revision: Union[str, None] = "0004_decks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("total_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("card_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_price_snapshot_captured_at", "price_snapshot", ["captured_at"])
    op.create_table(
        "card_price_point",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id", sa.Integer(),
            sa.ForeignKey("price_snapshot.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usd", sa.Float(), nullable=False),
    )
    op.create_index("ix_card_price_point_snapshot_id", "card_price_point", ["snapshot_id"])
    op.create_index("ix_card_price_point_scryfall_id", "card_price_point", ["scryfall_id"])


def downgrade() -> None:
    op.drop_index("ix_card_price_point_scryfall_id", table_name="card_price_point")
    op.drop_index("ix_card_price_point_snapshot_id", table_name="card_price_point")
    op.drop_table("card_price_point")
    op.drop_index("ix_price_snapshot_captured_at", table_name="price_snapshot")
    op.drop_table("price_snapshot")
