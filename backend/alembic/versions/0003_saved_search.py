"""saved_search table for named, reusable searches

Revision ID: 0003_saved_search
Revises: 0002_import_staging
Create Date: 2026-06-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_saved_search"
down_revision: Union[str, None] = "0002_import_staging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_search",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(16), nullable=False, server_default="collection"),
        sa.Column("sort", sa.String(16), nullable=False, server_default="name"),
        sa.Column("direction", sa.String(8), nullable=False, server_default="asc"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("saved_search")
