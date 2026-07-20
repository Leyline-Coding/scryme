"""fx_rate_history table for per-date FX conversion of the card price-history chart (#233)

Revision ID: 0024_fx_rate_history
Revises: 0023_fx_rates
Create Date: 2026-07-20

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_fx_rate_history"
down_revision: Union[str, None] = "0023_fx_rates"


def upgrade() -> None:
    op.create_table(
        "fx_rate_history",
        sa.Column("code", sa.String(length=3), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("rate", sa.Float(), nullable=False),
    )
    op.create_index("ix_fx_rate_history_code", "fx_rate_history", ["code"])


def downgrade() -> None:
    op.drop_index("ix_fx_rate_history_code", table_name="fx_rate_history")
    op.drop_table("fx_rate_history")
