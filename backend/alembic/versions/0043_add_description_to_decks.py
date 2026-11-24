"""Add description field to decks

Revision ID: 0043_add_description_to_decks
Revises: 0042_drop_binder_and_cascade
Create Date: 2026-11-11 15:01:57.144063

"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic. scryme uses a linear migration chain, so the optional
# branch_labels / depends_on globals (for Alembic branching) are omitted — Alembic defaults them to
# None, and leaving them out avoids GitHub Code Quality's "unused global" false positive.
revision: str = '0043_add_description_to_decks'
down_revision: Union[str, None] = '0042_drop_binder_and_cascade'


def upgrade() -> None:
    op.add_column('deck', sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('deck', 'description')
