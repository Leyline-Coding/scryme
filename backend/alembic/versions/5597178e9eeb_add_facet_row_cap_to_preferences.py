"""Add facet_row_cap to preferences

Revision ID: 5597178e9eeb
Revises: 0035_share_link
Create Date: 2026-10-29 23:39:14.947608

"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic. scryme uses a linear migration chain, so the optional
# branch_labels / depends_on globals (for Alembic branching) are omitted — Alembic defaults them to
# None, and leaving them out avoids GitHub Code Quality's "unused global" false positive.
revision: str = '5597178e9eeb'
down_revision: Union[str, None] = '0035_share_link'


def upgrade() -> None:
    op.add_column('preferences', sa.Column('facet_row_cap', sa.Integer(), server_default=sa.text('4000'), nullable=False))
 


def downgrade() -> None:
    op.drop_column('preferences', 'facet_row_cap')

