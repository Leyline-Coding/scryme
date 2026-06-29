"""checklist + checklist_item tables

Revision ID: 0008_checklists
Revises: 0007_wishlist
Create Date: 2026-06-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_checklists"
down_revision: Union[str, None] = "0007_wishlist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "checklist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "checklist_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "checklist_id", sa.Integer(),
            sa.ForeignKey("checklist.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("oracle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_checklist_item_checklist_id", "checklist_item", ["checklist_id"])
    op.create_index("ix_checklist_item_oracle_id", "checklist_item", ["oracle_id"])


def downgrade() -> None:
    op.drop_index("ix_checklist_item_oracle_id", table_name="checklist_item")
    op.drop_index("ix_checklist_item_checklist_id", table_name="checklist_item")
    op.drop_table("checklist_item")
    op.drop_table("checklist")
