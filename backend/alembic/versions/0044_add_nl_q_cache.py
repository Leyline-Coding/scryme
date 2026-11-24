"""Add NL to Q Cache

Revision ID: 0044_add_nl_q_cache
Revises: 0043_add_description_to_decks
Create Date: 2026-11-15 15:01:57.144063

"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic. scryme uses a linear migration chain, so the optional
# branch_labels / depends_on globals (for Alembic branching) are omitted — Alembic defaults them to
# None, and leaving them out avoids GitHub Code Quality's "unused global" false positive.
revision: str = '0044_add_nl_q_cache'
down_revision: Union[str, None] = '0043_add_description_to_decks'


def upgrade() -> None:
    # Create the new location table
    location_table = op.create_table('nl_q_cache',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('nl_text', sa.String(length=512), nullable=False),
        sa.Column('q_text', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Drop location table
    op.drop_table('nl_q_cache')
