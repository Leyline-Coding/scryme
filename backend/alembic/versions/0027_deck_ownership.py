"""deck.ownership + deck_card.owned for owned-deck ↔ collection sync (#298)

Revision ID: 0027_deck_ownership
Revises: 0026_deck_version
Create Date: 2026-07-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_deck_ownership"
down_revision: Union[str, None] = "0026_deck_version"


def upgrade() -> None:
    op.add_column("deck", sa.Column("ownership", sa.String(length=8),
                                    server_default="none", nullable=False))
    op.add_column("deck_card", sa.Column("owned", sa.Boolean(),
                                         server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("deck_card", "owned")
    op.drop_column("deck", "ownership")
