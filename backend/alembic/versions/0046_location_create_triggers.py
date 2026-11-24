"""Add location entry after a deck/binder/box creation

This revision used to install ``AFTER INSERT`` triggers on ``binder``, ``box`` and ``deck`` whose
function inserted into a table that does not exist, so on a migrated database creating any of the
three failed. The application already creates the ``location`` row itself, on first use (see
``src/locations.py``), so the triggers are not needed and are no longer created here.
``0047_repair_fork_databases`` removes them from databases that ran the earlier version.

Revision ID: 0046_location_create_triggers
Revises: 0045_add_commander_id_to_deck
Create Date: 2026-11-19 14:00:00.000000

"""

# revision identifiers, used by Alembic.
revision = "0046_location_create_triggers"
down_revision = "0045_add_commander_id_to_deck"


def upgrade():
    pass


def downgrade():
    pass
