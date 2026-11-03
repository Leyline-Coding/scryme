"""Implement location system with m:m relationship

Revision ID: 0040_location_system
Revises: 0039_add_sleeves_table
Create Date: 2026-11-03 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0040_location_system'
down_revision: Union[str, None] = '0039_add_sleeves_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the new location table
    op.create_table('location',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=16), nullable=False),
        sa.Column('foreign_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Drop the old location column from collection_card 
    op.drop_column('collection_card', 'location')
    
    # Add locations column to collection_card table
    op.add_column('collection_card', sa.Column('location', sa.Integer() ))



def downgrade() -> None:
    
    # Drop locations column  
    op.drop_column('collection_card', 'location')

    # Revert back to location string column
    op.add_column('collection_card', sa.Column('location', sa.String(length=256), nullable=True))

    # Drop location table
    op.drop_table('location')