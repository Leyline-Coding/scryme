"""deck.bracket_override for manually setting a Commander bracket (#159)

Revision ID: 0025_deck_bracket_override
Revises: 0024_fx_rate_history
Create Date: 2026-07-21

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_deck_bracket_override"
down_revision: Union[str, None] = "0024_fx_rate_history"


def upgrade() -> None:
    op.add_column("deck", sa.Column("bracket_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("deck", "bracket_override")
