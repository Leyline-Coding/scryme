"""saved_search alert state: seen + new match ids

Revision ID: 0009_saved_search_alerts
Revises: 0008_checklists
Create Date: 2026-06-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_saved_search_alerts"
down_revision: Union[str, None] = "0008_checklists"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline match set as of the last evaluation (NULL = never evaluated, so first run only
    # establishes the baseline and doesn't alert on the whole collection).
    op.add_column("saved_search", sa.Column("seen_ids", postgresql.JSONB(), nullable=True))
    # Newly-matching ids the user hasn't looked at yet (drives the badge / "What's new" panel).
    op.add_column(
        "saved_search",
        sa.Column("new_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("saved_search", "new_ids")
    op.drop_column("saved_search", "seen_ids")
