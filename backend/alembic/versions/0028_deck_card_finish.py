"""deck_card.finish + deck.source_url for faithful imports and re-sync

Revision ID: 0028_deck_card_finish
Revises: 0027_deck_ownership
Create Date: 2026-07-22

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_deck_card_finish"
down_revision: Union[str, None] = "0027_deck_ownership"


def upgrade() -> None:
    op.add_column("deck_card", sa.Column("finish", sa.String(length=16),
                                         server_default="normal", nullable=False))
    op.add_column("deck", sa.Column("source_url", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("deck", "source_url")
    op.drop_column("deck_card", "finish")
