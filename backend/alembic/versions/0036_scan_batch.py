"""scan_batch: idempotent replay record for batch scan ingest (#164)

The scanner retries — offline queues, dropped home wifi, a timeout that fired after the server had
already committed. Keying each batch on the client's ``Idempotency-Key`` and storing the response
means a retry returns the original answer instead of adding the cards a second time. The unique
index on the key is the mechanism, not just a constraint: it is what makes two concurrent retries
collapse into one applied merge.

Revision ID: 0036_scan_batch
Revises: 0035_share_link
Create Date: 2026-09-02

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_scan_batch"
down_revision: str | None = "0035_share_link"


def upgrade() -> None:
    op.create_table(
        "scan_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_scan_batch_idempotency_key", "scan_batch", ["idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_scan_batch_idempotency_key", table_name="scan_batch")
    op.drop_table("scan_batch")
