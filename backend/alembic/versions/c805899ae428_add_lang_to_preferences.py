"""Add lang to preferences

Revision ID: c805899ae428
Revises: 5597178e9eeb
Create Date: 2026-10-30 13:21:13.524638

"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic. scryme uses a linear migration chain, so the optional
# branch_labels / depends_on globals (for Alembic branching) are omitted — Alembic defaults them to
# None, and leaving them out avoids GitHub Code Quality's "unused global" false positive.
revision: str = 'c805899ae428'
down_revision: Union[str, None] = '5597178e9eeb'


def upgrade() -> None:
    op.add_column('preferences', sa.Column('lang', sa.String(length=8), server_default='en', nullable=False))



def downgrade() -> None:
    op.drop_column('preferences', 'lang')