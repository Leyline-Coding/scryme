"""import_snapshot table (undo last import)

Revision ID: 0011_import_snapshots
Revises: 0010_price_targets
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_import_snapshots"
down_revision: Union[str, None] = "0010_price_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("card_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("import_snapshot")
