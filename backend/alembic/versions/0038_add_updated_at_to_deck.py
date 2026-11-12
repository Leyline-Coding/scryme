"""Add updated_at column to deck table

Revision ID: 0038_add_updated_at_to_deck
Revises: 0037_add_lang_to_preferences
Create Date: 2026-10-30 23:40:39.425553

"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic. scryme uses a linear migration chain, so the optional
# branch_labels / depends_on globals (for Alembic branching) are omitted — Alembic defaults them to
# None, and leaving them out avoids GitHub Code Quality's "unused global" false positive.
revision: str = '0038_add_updated_at_to_deck'
down_revision: Union[str, None] = '0037_add_lang_to_preferences'


def upgrade() -> None:
    op.add_column('binder', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('deck', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))


def downgrade() -> None:
    op.drop_column('deck', 'updated_at')
    op.drop_column('binder', 'updated_at')
