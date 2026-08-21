"""collection_card.version: optimistic-concurrency guard for shared collections (#207)

ADR 0001 keeps scryme single-collection on purpose, which means one collection is deliberately
edited by more than one person. This column makes an absolute edit ("set the quantity to 4",
"delete this stack") refusable when the row moved since the editor loaded it, instead of silently
clobbering the other person's change.

Existing rows start at 1; no edit in flight can be stale against a version nobody has seen yet.

Revision ID: 0034_stack_version
Revises: 0033_client_token
Create Date: 2026-08-21

"""
import sqlalchemy as sa
from alembic import op

revision: str = "0034_stack_version"
down_revision: str | None = "0033_client_token"


def upgrade() -> None:
    op.add_column(
        "collection_card",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("collection_card", "version")
