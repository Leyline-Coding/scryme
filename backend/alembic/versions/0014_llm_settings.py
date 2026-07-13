"""llm_settings table (in-app AI endpoint config)

Single-row (id=1) config for the OpenAI-compatible endpoint the AI features use. API key stored
encrypted (Fernet); the rest plain. Falls back to SCRYME_LLM_* env vars when absent.

Revision ID: 0014_llm_settings
Revises: 0013_card_embeddings
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_llm_settings"
down_revision: Union[str, None] = "0013_card_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("chat_model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("embed_model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("llm_settings")
