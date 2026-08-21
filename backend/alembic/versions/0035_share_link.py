"""share_link: read-only public links to one deck or binder (#80)

A share link is the only way anything leaves an otherwise private instance, so the token is stored
hashed (a URL token is the most likely credential to leak, and a dump must not add working links to
that) and each row names exactly one target rather than a query that could be widened. Revoked rows
are kept so "was this ever shared?" stays answerable.

Revision ID: 0035_share_link
Revises: 0034_stack_version
Create Date: 2026-08-21

"""
import sqlalchemy as sa
from alembic import op

revision: str = "0035_share_link"
down_revision: str | None = "0034_stack_version"


def upgrade() -> None:
    op.create_table(
        "share_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("show_prices", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_share_link_token_hash", "share_link", ["token_hash"], unique=True)
    op.create_index("ix_share_link_target_id", "share_link", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_share_link_target_id", table_name="share_link")
    op.drop_index("ix_share_link_token_hash", table_name="share_link")
    op.drop_table("share_link")
