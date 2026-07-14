"""rules_chunk table (comprehensive-rules RAG)

Revision ID: 0016_rules_chunk
Revises: 0015_deck_chat
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_rules_chunk"
down_revision: Union[str, None] = "0015_deck_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rules_chunk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref", sa.String(length=64), nullable=False, index=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("vector", postgresql.ARRAY(postgresql.DOUBLE_PRECISION), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("rules_chunk")
