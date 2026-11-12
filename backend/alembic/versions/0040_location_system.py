"""Implement location system with m:m relationship

Revision ID: 0040_location_system
Revises: 0039_add_sleeves_table
Create Date: 2026-11-03 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from src.locations import LOCATION_TYPES
from src.models import Binder, Box, Deck, Location

# revision identifiers, used by Alembic.
revision: str = '0040_location_system'
down_revision: Union[str, None] = '0039_add_sleeves_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the new location table
    location_table = op.create_table('location',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=16), nullable=False),
        sa.Column('foreign_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Add locations column to collection_card table
    op.alter_column('collection_card', 'location', new_column_name='location_old')
    op.add_column('collection_card', sa.Column('location', sa.Integer() ))

    connection = op.get_bind()

    locations = []
    for type_name, model in LOCATION_TYPES.items():
        # Fetch source IDs and names
        rows = (connection.execute(sa.select(model.id, model.name))).all()
        if not rows: continue
        source_ids = [row.id for row in rows]
        connection.execute(location_table.insert(), [{'type': type_name, 'foreign_id': id} for id in source_ids])


    collection_card_table = sa.table(
        'collection_card',
        sa.column('id', sa.Integer),
        sa.column('location', sa.Integer)
    )

  
    ## Migrate the rest of old locations system
    binder_locations_map = dict(connection.execute(sa.select(Binder.name, Location.id).join(Binder, Binder.id == Location.foreign_id).where(Location.type == 'binder')).tuples().all())
    box_locations_map = dict(connection.execute(sa.select(Box.name, Location.id).join(Box, Box.id == Location.foreign_id).where(Location.type == 'box')).tuples().all())
    deck_locations_map = dict(connection.execute(sa.select(Deck.name, Location.id).join(Deck, Deck.id == Location.foreign_id).where(Location.type == 'deck')).tuples().all())

    for loc_map in [binder_locations_map, box_locations_map, deck_locations_map]:
        collection_cards_to_upgrade = connection.execute(sa.text("SELECT * FROM collection_card WHERE location_old IS NOT NULL AND location IS NULL")).all()
        for cctu in collection_cards_to_upgrade:
            #Match location by name
            if(cctu.location_old in loc_map.keys()):
                connection.execute(
                    collection_card_table.update()
                    .where(collection_card_table.c.id == cctu.id)
                    .values(location=loc_map[cctu.location_old])
                )

    # Drop the old location column from collection_card 
    op.drop_column('collection_card', 'location_old')



def downgrade() -> None:

    # Drop locations column  
    op.drop_column('collection_card', 'location')
    
    # Revert back to location string column
    op.add_column('collection_card', sa.Column('location', sa.String(length=256), nullable=True))

    # Drop location table
    op.drop_table('location')