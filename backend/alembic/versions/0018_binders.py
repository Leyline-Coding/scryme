"""custom binders: binder / binder_card tables

Revision ID: 0018_binders
Revises: 0017_collection_location
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_binders"
down_revision: Union[str, None] = "0017_collection_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "binder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "binder_card",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binder_id", sa.Integer(),
                  sa.ForeignKey("binder.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("binder_id", "scryfall_id", name="uq_binder_card"),
    )


def downgrade() -> None:
    op.drop_table("binder_card")
    op.drop_table("binder")
