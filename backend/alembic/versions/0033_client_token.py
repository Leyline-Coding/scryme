"""client_token: labelled, revocable per-device API credentials (#204)

Only a hash of each token is stored, so the table is useless to anyone who obtains a dump. Rows are
never deleted — a revoked token stays as an audit record, and the presence of any row is what marks
this instance as locked down (see src.client_tokens.auth_required). ``owner_id`` is ADR 0001's
forward-compat hedge and is always NULL today.

Revision ID: 0033_client_token
Revises: 0032_trade_commit
Create Date: 2026-08-21

"""
import sqlalchemy as sa
from alembic import op

revision: str = "0033_client_token"
down_revision: str | None = "0032_trade_commit"


def upgrade() -> None:
    op.create_table(
        "client_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="write"),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_client_token_token_hash", "client_token", ["token_hash"], unique=True)
    op.create_index("ix_client_token_owner_id", "client_token", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_client_token_owner_id", table_name="client_token")
    op.drop_index("ix_client_token_token_hash", table_name="client_token")
    op.drop_table("client_token")
