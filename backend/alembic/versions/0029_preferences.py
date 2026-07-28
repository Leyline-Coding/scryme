"""preferences table (collection-level preferences singleton, #203)

Single-row (id=1) collection preferences: theme/appearance, currency, price source, search
defaults — previously browser-only (localStorage/cookies). Falls back to SCRYME_DEFAULT_* env +
built-in client defaults when the row is absent (see src.preferences). server_defaults mirror the
current client-side defaults so introducing the row changes nothing until a value is set.

Revision ID: 0029_preferences
Revises: 0028_deck_card_finish
Create Date: 2026-07-27

"""
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_preferences"
down_revision: Union[str, None] = "0028_deck_card_finish"


def upgrade() -> None:
    op.create_table(
        "preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("collection_id", sa.Integer(), nullable=True),
        # server-read prefs
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="usd"),
        sa.Column("price_source", sa.String(length=32), nullable=False,
                  server_default="tcgplayer"),
        sa.Column("search_filter", sa.Text(), nullable=False, server_default=""),
        sa.Column("movers", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("view", sa.String(length=8), nullable=False, server_default="grid"),
        sa.Column("page_size", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("infinite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hist_currency", sa.String(length=8), nullable=True),
        # appearance prefs
        sa.Column("mode", sa.String(length=8), nullable=False, server_default="dark"),
        sa.Column("palette", sa.String(length=32), nullable=False, server_default="trop-orange"),
        sa.Column("accent", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("foil_speed", sa.Integer(), nullable=False, server_default=sa.text("6")),
        sa.Column("spin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("spin_speed", sa.Integer(), nullable=False, server_default=sa.text("6")),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_preferences_owner_id", "preferences", ["owner_id"])
    op.create_index("ix_preferences_collection_id", "preferences", ["collection_id"])


def downgrade() -> None:
    op.drop_index("ix_preferences_collection_id", table_name="preferences")
    op.drop_index("ix_preferences_owner_id", table_name="preferences")
    op.drop_table("preferences")
