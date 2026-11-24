"""Unescape deck descriptions that were HTML-escaped on write

``POST /decks/{id}/update-description`` used to store ``html.escape(description)``. The template
escapes on render as well, so a description like ``Tom & Jerry`` came back as ``Tom &amp; Jerry``
on the next page load. The route now stores the text as typed; this repairs rows written before
that.

The statement is the exact inverse of ``html.escape(..., quote=True)``: the five entities it
produces are replaced, ``&amp;`` last (it was applied first). Only rows containing one of those
entities are touched. A description that was never escaped but contains one of them verbatim is
indistinguishable from an escaped one and is unescaped too — acceptable, since until now the edit
form was the only way to change a description and it always escaped.

The downgrade is a no-op: the escaped form was a bug, not a schema state worth restoring.

Revision ID: 0048_unescape_deck_descriptions
Revises: 0047_repair_fork_databases
Create Date: 2026-11-24

"""
from alembic import op

revision: str = "0048_unescape_deck_descriptions"
down_revision: str | None = "0047_repair_fork_databases"

UNESCAPE_SQL = """
    UPDATE deck
       SET description = replace(replace(replace(replace(replace(
               description,
               '&lt;', '<'),
               '&gt;', '>'),
               '&quot;', '"'),
               '&#x27;', ''''),
               '&amp;', '&')
     WHERE description ~ '&(amp|lt|gt|quot|#x27);'
"""


def upgrade() -> None:
    op.execute(UNESCAPE_SQL)


def downgrade() -> None:
    pass
