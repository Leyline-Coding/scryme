"""market_prices + mtgjson_id on cards (preferred-marketplace pricing, #231)

Revision ID: 0022_market_prices
Revises: 0021_graded_cards
Create Date: 2026-07-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_market_prices"
down_revision: Union[str, None] = "0021_graded_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("market_prices", postgresql.JSONB(), nullable=True))
    op.add_column("cards", sa.Column("mtgjson_id", sa.String(length=64), nullable=True))
    op.create_index("ix_cards_mtgjson_id", "cards", ["mtgjson_id"])


def downgrade() -> None:
    op.drop_index("ix_cards_mtgjson_id", table_name="cards")
    op.drop_column("cards", "mtgjson_id")
    op.drop_column("cards", "market_prices")
