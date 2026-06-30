"""price_target table (price watchlist)

Revision ID: 0010_price_targets
Revises: 0009_saved_search_alerts
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_price_targets"
down_revision: Union[str, None] = "0009_saved_search_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "price_target",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_price", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_price_target_scryfall_id", "price_target", ["scryfall_id"])


def downgrade() -> None:
    op.drop_index("ix_price_target_scryfall_id", table_name="price_target")
    op.drop_table("price_target")
