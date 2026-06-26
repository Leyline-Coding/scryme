"""deck + deck_card tables

Revision ID: 0004_decks
Revises: 0003_saved_search
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_decks"
down_revision: Union[str, None] = "0003_saved_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deck",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "deck_card",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "deck_id", sa.Integer(),
            sa.ForeignKey("deck.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("board", sa.String(8), nullable=False, server_default="main"),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True)),
        sa.Column("oracle_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_deck_card_deck_id", "deck_card", ["deck_id"])
    op.create_index("ix_deck_card_oracle_id", "deck_card", ["oracle_id"])


def downgrade() -> None:
    op.drop_index("ix_deck_card_oracle_id", table_name="deck_card")
    op.drop_index("ix_deck_card_deck_id", table_name="deck_card")
    op.drop_table("deck_card")
    op.drop_table("deck")
