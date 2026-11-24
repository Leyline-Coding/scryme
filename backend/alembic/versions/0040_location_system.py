"""Implement location system with m:m relationship

``collection_card.location`` changes from a free-text string to a reference to a ``location`` row,
which in turn points at a binder, box or deck.

A stack's old location string is matched by name to a binder, then a box, then a deck. A string
that matches none of them ("Shelf 3", "Red bulk", ...) becomes a new box of that name, so no
stack loses the place its owner filed it under.

The data steps are plain SQL on purpose: a migration must describe the schema as it was at this
revision, and importing the ORM models would tie it to whatever columns they have today.

Revision ID: 0040_location_system
Revises: 0039_add_sleeves_table
Create Date: 2026-11-03 15:00:00.000000

"""
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040_location_system"
down_revision: Union[str, None] = "0039_add_sleeves_table"

# Matching order when one name is used by more than one kind of place.
_SOURCES = ("binder", "box", "deck")


def upgrade() -> None:
    op.create_table(
        "location",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("foreign_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.alter_column("collection_card", "location", new_column_name="location_old")
    op.add_column("collection_card", sa.Column("location", sa.Integer()))

    # A location string that names no binder, box or deck becomes a box, so it survives.
    op.execute("""
        INSERT INTO box (name)
        SELECT DISTINCT ON (lower(left(btrim(cc.location_old), 128)))
               left(btrim(cc.location_old), 128)
        FROM collection_card cc
        WHERE btrim(coalesce(cc.location_old, '')) <> ''
          AND NOT EXISTS (SELECT 1 FROM binder b WHERE b.name = cc.location_old)
          AND NOT EXISTS (SELECT 1 FROM box b WHERE b.name = cc.location_old)
          AND NOT EXISTS (SELECT 1 FROM deck d WHERE d.name = cc.location_old)
          AND NOT EXISTS (
              SELECT 1 FROM box b
              WHERE lower(b.name) = lower(left(btrim(cc.location_old), 128))
          )
        ORDER BY lower(left(btrim(cc.location_old), 128)), cc.id
    """)

    for source in _SOURCES:
        op.execute(
            f"INSERT INTO location (type, foreign_id) SELECT '{source}', id FROM {source} ORDER BY id"
        )

    # Exact name first (in _SOURCES order), exactly as before.
    for source in _SOURCES:
        op.execute(f"""
            UPDATE collection_card cc
            SET location = (
                SELECT l.id FROM location l JOIN {source} s ON s.id = l.foreign_id
                WHERE l.type = '{source}' AND s.name = cc.location_old
                ORDER BY l.id LIMIT 1
            )
            WHERE cc.location_old IS NOT NULL AND cc.location IS NULL
        """)
    # Then the boxes created above, which hold the trimmed (and possibly shortened) name.
    op.execute("""
        UPDATE collection_card cc
        SET location = (
            SELECT l.id FROM location l JOIN box b ON b.id = l.foreign_id
            WHERE l.type = 'box' AND lower(b.name) = lower(left(btrim(cc.location_old), 128))
            ORDER BY l.id LIMIT 1
        )
        WHERE btrim(coalesce(cc.location_old, '')) <> '' AND cc.location IS NULL
    """)

    op.drop_column("collection_card", "location_old")


def downgrade() -> None:
    # Put each stack's place back as its name before the reference column goes away.
    op.add_column("collection_card", sa.Column("location_name", sa.String(length=256)))
    for source in _SOURCES:
        op.execute(f"""
            UPDATE collection_card cc
            SET location_name = s.name
            FROM location l JOIN {source} s ON s.id = l.foreign_id
            WHERE l.type = '{source}' AND l.id = cc.location
        """)
    op.drop_column("collection_card", "location")
    op.alter_column("collection_card", "location_name", new_column_name="location")
    op.drop_table("location")
