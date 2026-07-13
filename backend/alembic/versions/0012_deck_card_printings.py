"""deck_card printing metadata (proxy/special/language) + repoint non-playable printings

Adds ``deck_card.proxy`` / ``special`` (independent non-standard-copy markers) and ``language``
(the copy's Scryfall language code, English default), and repoints any existing deck card whose
representative printing is a *non-tournament-legal* variant (art-series, token, gold-border World
Championship / Collector's Edition, oversized, acorn, …) to a real playable printing of the same
card. Those variants are marked ``not_legal`` in every format by Scryfall, which made the deck
legality check flag perfectly legal cards. A "playable" printing is any that is not ``not_legal``
in *every* format.

Revision ID: 0012_deck_card_printings
Revises: 0011_import_snapshots
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_deck_card_printings"
down_revision: Union[str, None] = "0011_import_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# True when a printing is legal/restricted/banned in at least one format (i.e. a real card),
# rather than a non-playable variant that is not_legal everywhere.
_PLAYABLE = (
    "EXISTS (SELECT 1 FROM jsonb_each_text({col}) e WHERE e.value <> 'not_legal')"
)


def upgrade() -> None:
    op.add_column(
        "deck_card",
        sa.Column("proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "deck_card",
        sa.Column("special", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "deck_card",
        sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
    )
    # Repoint deck cards currently pointing at a non-playable printing to the newest playable
    # printing of the same oracle id. (No-op on a fresh DB where cards/decks are empty.)
    op.execute(
        f"""
        UPDATE deck_card dc
        SET scryfall_id = good.scryfall_id
        FROM (
            SELECT DISTINCT ON (oracle_id) oracle_id, scryfall_id
            FROM cards c
            WHERE {_PLAYABLE.format(col='c.legalities')}
            ORDER BY oracle_id, released_at DESC NULLS LAST
        ) good
        WHERE dc.oracle_id IS NOT NULL
          AND dc.oracle_id = good.oracle_id
          AND EXISTS (
              SELECT 1 FROM cards cur
              WHERE cur.scryfall_id = dc.scryfall_id
                AND NOT {_PLAYABLE.format(col='cur.legalities')}
          )
        """
    )


def downgrade() -> None:
    op.drop_column("deck_card", "language")
    op.drop_column("deck_card", "special")
    op.drop_column("deck_card", "proxy")
