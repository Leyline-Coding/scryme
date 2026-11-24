"""Bring databases from either line of history to the same schema

scryme's history briefly ran in two lines that were then merged: main (which added ``scan_batch``)
and a community fork (which added the location system). A database that followed the fork has
already recorded revisions ``0036_add_facet_row_cap_to_pref`` .. ``0046_location_create_triggers``
as applied, so two things have to be repaired here rather than in those revisions:

* it never ran ``0036_scan_batch``, so the table is created if it is missing;
* the first version of ``0046`` installed insert triggers that made creating a deck, binder or
  box fail. They are dropped.

On a database that came up through main, both steps find nothing to do.

Revision ID: 0047_repair_fork_databases
Revises: 0046_location_create_triggers
Create Date: 2026-11-24

"""
from alembic import op

revision: str = "0047_repair_fork_databases"
down_revision: str | None = "0046_location_create_triggers"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS scan_batch (
            id SERIAL PRIMARY KEY,
            idempotency_key VARCHAR(128) NOT NULL,
            response JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_scan_batch_idempotency_key "
        "ON scan_batch (idempotency_key)"
    )
    for table in ("binder", "box", "deck"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_create_location ON {table}")
    op.execute("DROP FUNCTION IF EXISTS create_location_after_source()")


def downgrade() -> None:
    # Nothing to undo: the table belongs to 0036_scan_batch, and the triggers never worked.
    pass
