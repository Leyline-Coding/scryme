"""graded/slabbed card fields on collection_card

Revision ID: 0021_graded_cards
Revises: 0020_set_release
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_graded_cards"
down_revision: Union[str, None] = "0020_set_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("collection_card", sa.Column("grade_company", sa.String(length=16), nullable=True))
    op.add_column("collection_card", sa.Column("grade", sa.String(length=16), nullable=True))
    op.add_column("collection_card", sa.Column("cert_number", sa.String(length=32), nullable=True))
    op.add_column("collection_card", sa.Column("grade_photo", sa.String(length=128), nullable=True))
    op.add_column("collection_card", sa.Column("value_override", sa.Float(), nullable=True))


def downgrade() -> None:
    for col in ("value_override", "grade_photo", "cert_number", "grade", "grade_company"):
        op.drop_column("collection_card", col)
