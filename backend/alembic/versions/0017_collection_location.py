"""collection_card.location (physical storage location)

Adds a `location` field (distinct from the import `binder_name`) so a card can be filed in a
physical place, and includes it in the stack unique key so the same card can be split across
locations.

Revision ID: 0017_collection_location
Revises: 0016_rules_chunk
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_collection_location"
down_revision: Union[str, None] = "0016_rules_chunk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = ["scryfall_id", "finish", "condition", "language", "binder_name", "location"]


def upgrade() -> None:
    op.add_column("collection_card", sa.Column("location", sa.String(length=256), nullable=True))
    op.drop_constraint("uq_collection_stack", "collection_card", type_="unique")
    op.create_unique_constraint("uq_collection_stack", "collection_card", _COLS)


def downgrade() -> None:
    op.drop_constraint("uq_collection_stack", "collection_card", type_="unique")
    op.drop_column("collection_card", "location")
    op.create_unique_constraint(
        "uq_collection_stack", "collection_card",
        ["scryfall_id", "finish", "condition", "language", "binder_name"],
    )
