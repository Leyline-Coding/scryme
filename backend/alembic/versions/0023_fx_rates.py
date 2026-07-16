"""fx_rate table for display-currency conversion (USD -> GBP/CAD/AUD/JPY, #232)

Revision ID: 0023_fx_rates
Revises: 0022_market_prices
Create Date: 2026-07-16

"""
import datetime
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_fx_rates"
down_revision: Union[str, None] = "0022_market_prices"

# Seed fallback rates so converted currencies render a number immediately; updated_at=epoch marks
# them stale so the scheduler / `cli refresh-fx` replaces them with live ECB rates on first run.
_SEED = {"gbp": 0.79, "cad": 1.36, "aud": 1.52, "jpy": 157.0}


def upgrade() -> None:
    fx = op.create_table(
        "fx_rate",
        sa.Column("code", sa.String(length=3), primary_key=True),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC)
    op.bulk_insert(fx, [{"code": c, "rate": r, "updated_at": epoch} for c, r in _SEED.items()])


def downgrade() -> None:
    op.drop_table("fx_rate")
