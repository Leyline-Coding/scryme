"""binder / binder_group / binder_card tables + default groups & binders

Revision ID: 0018_binders
Revises: 0017_collection_location
Create Date: 2026-07-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_binders"
down_revision: Union[str, None] = "0017_collection_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default groups -> their binders (created empty; users add cards).
_DEFAULTS = {
    "Mono Colored": ["White", "Blue", "Black", "Red", "Green", "Colorless"],
    "Guilds": ["Azorius", "Dimir", "Rakdos", "Gruul", "Selesnya",
               "Orzhov", "Izzet", "Golgari", "Boros", "Simic"],
    "Shards": ["Bant", "Esper", "Grixis", "Jund", "Naya"],
    "Wedges": ["Abzan", "Jeskai", "Sultai", "Mardu", "Temur"],
    "Types": ["Artifacts", "Planeswalkers", "Creatures", "Instants", "Sorceries",
              "Battles", "Enchantments", "Lands"],
}


def upgrade() -> None:
    op.create_table(
        "binder_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "binder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("binder_group.id", ondelete="SET NULL"), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "binder_card",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("binder_id", sa.Integer(),
                  sa.ForeignKey("binder.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("scryfall_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("binder_id", "scryfall_id", name="uq_binder_card"),
    )

    conn = op.get_bind()
    for group_name, binders in _DEFAULTS.items():
        gid = conn.execute(
            sa.text("INSERT INTO binder_group (name) VALUES (:n) RETURNING id"), {"n": group_name}
        ).scalar()
        for binder_name in binders:
            conn.execute(
                sa.text("INSERT INTO binder (name, group_id) VALUES (:n, :g)"),
                {"n": binder_name, "g": gid},
            )


def downgrade() -> None:
    op.drop_table("binder_card")
    op.drop_table("binder")
    op.drop_table("binder_group")
