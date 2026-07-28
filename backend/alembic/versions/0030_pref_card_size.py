"""preferences.card_size (grid card-size slider, #203 follow-up)

Adds the grid card-size preference (1–10; drives the search/collection card-grid's minimum column
width via --card-min). server_default "5" == today's 10rem, so introducing the column changes
nothing until a value is set.

Revision ID: 0030_pref_card_size
Revises: 0029_preferences
Create Date: 2026-07-28

"""
import sqlalchemy as sa
from alembic import op

revision: str = "0030_pref_card_size"
down_revision: str | None = "0029_preferences"


def upgrade() -> None:
    op.add_column(
        "preferences",
        sa.Column("card_size", sa.Integer(), nullable=False, server_default=sa.text("5")),
    )


def downgrade() -> None:
    op.drop_column("preferences", "card_size")
