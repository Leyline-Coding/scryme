"""collection_card.tags + GIN index

Revision ID: 0006_collection_tags
Revises: 0005_price_history
Create Date: 2026-06-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_collection_tags"
down_revision: Union[str, None] = "0005_price_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collection_card",
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=True),
    )
    op.create_index(
        "ix_collection_card_tags_gin", "collection_card", ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_collection_card_tags_gin", table_name="collection_card")
    op.drop_column("collection_card", "tags")
