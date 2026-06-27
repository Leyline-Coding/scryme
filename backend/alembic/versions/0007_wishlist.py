"""wishlist table

Revision ID: 0007_wishlist
Revises: 0006_collection_tags
Create Date: 2026-06-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_wishlist"
down_revision: Union[str, None] = "0006_collection_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wishlist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scryfall_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cards.scryfall_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scryfall_id", name="uq_wishlist_printing"),
    )
    op.create_index("ix_wishlist_scryfall_id", "wishlist", ["scryfall_id"])


def downgrade() -> None:
    op.drop_index("ix_wishlist_scryfall_id", table_name="wishlist")
    op.drop_table("wishlist")
