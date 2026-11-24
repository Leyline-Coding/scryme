"""remove binder tables and migrate to location system

A binder used to list printings (``binder_card``); it is now a place a stack is filed in
(``collection_card.location``). Each listed printing's owned stacks that are not filed anywhere
yet are moved into the binder.

Two cases have no equivalent in the new model, where a stack lives in exactly one place: a
printing listed in a binder while its stack is already filed elsewhere, and a printing listed in
several binders. Rather than drop that information, the old table is kept as
``binder_card_legacy``. Nothing reads it; it is there so the lists can still be recovered.

Revision ID: 0041_remove_binder_card
Revises: 0040_location_system
Create Date: 2026-11-04 10:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0041_remove_binder_card"
down_revision = "0040_location_system"


def upgrade():
    # The earliest binder a printing was added to wins when it is listed in more than one.
    op.execute("""
        UPDATE collection_card cc
        SET location = picked.location_id
        FROM (
            SELECT DISTINCT ON (bc.scryfall_id) bc.scryfall_id, l.id AS location_id
            FROM binder_card bc
            JOIN location l ON l.type = 'binder' AND l.foreign_id = bc.binder_id
            ORDER BY bc.scryfall_id, bc.added_at, bc.id
        ) AS picked
        WHERE cc.scryfall_id = picked.scryfall_id AND cc.location IS NULL
    """)
    op.rename_table("binder_card", "binder_card_legacy")


def downgrade():
    op.rename_table("binder_card_legacy", "binder_card")
