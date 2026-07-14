"""set_release calendar table

Revision ID: 0020_set_release
Revises: 0019_boxes
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_set_release"
down_revision: Union[str, None] = "0019_boxes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "set_release",
        sa.Column("code", sa.String(length=16), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("released_at", sa.Date(), nullable=True),
        sa.Column("set_type", sa.String(length=32), nullable=True),
        sa.Column("card_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digital", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("icon_uri", sa.String(length=512), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("set_release")
