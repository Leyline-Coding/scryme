"""deck_version table for manual deck snapshots + diffs (#100)

Revision ID: 0026_deck_version
Revises: 0025_deck_bracket_override
Create Date: 2026-07-21

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_deck_version"
down_revision: Union[str, None] = "0025_deck_bracket_override"


def upgrade() -> None:
    op.create_table(
        "deck_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("cards", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["deck_id"], ["deck.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_deck_version_deck_id", "deck_version", ["deck_id"])


def downgrade() -> None:
    op.drop_index("ix_deck_version_deck_id", table_name="deck_version")
    op.drop_table("deck_version")
