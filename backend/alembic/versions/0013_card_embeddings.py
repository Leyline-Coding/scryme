"""card_embedding table (semantic card similarity)

Per-oracle text-embedding vectors (float8[]) for "cards like this" / role-fill. No pgvector
dependency — similarity is computed in Python at personal-collection scale.

Revision ID: 0013_card_embeddings
Revises: 0012_deck_card_printings
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_card_embeddings"
down_revision: Union[str, None] = "0012_deck_card_printings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "card_embedding",
        sa.Column("oracle_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", postgresql.ARRAY(postgresql.DOUBLE_PRECISION), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("card_embedding")
