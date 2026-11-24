"""Manual collection editing: add/increment a stack, nudge quantities, delete, and bulk actions.

A "stack" is one ``collection_card`` row, keyed by (scryfall_id, finish, condition, language,
binder). Adding reuses the matching stack (incrementing) so the unique constraint is never
violated; a quantity that drops to zero deletes the row. Bulk actions operate on a set of
printings selected in the results grid.

**Concurrent edits (#207).** One collection is deliberately shared by more than one person
(:doc:`ADR 0001 </development/adr/0001-single-collection-accounts>`), so two people can edit the
same stack at once. The guard distinguishes two kinds of edit, because they do not have the same
problem:

* **Relative** edits — "add one", "remove one", the ± steppers — are commutative. Two people each
  adding a copy should end up at +2, and that is what last-write-wins already gives. Guarding them
  would reject a correct outcome and make the buttons feel broken.
* **Absolute** edits — set the quantity to 4, set the condition, delete the stack — replace state
  the editor was *looking at*. If it moved in the meantime, applying them destroys the other
  person's change silently. These carry the version they were shown and are refused if it moved.

Every mutation bumps ``version``, so an absolute edit notices a relative one that happened under it.
"""

from __future__ import annotations

import ahocorasick
import structlog
from markupsafe import Markup, escape
from sqlalchemy import and_, select

from src.db import SessionLocal
from src.llm import get_config
from src.models import Card
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url

log = structlog.get_logger()

_cache = ImageCache()

def _image(card: Card, size: str = "normal") -> str:
    sid = str(card.scryfall_id)
    if size == "normal" and _cache.is_cached(sid):
        return _cache.url_path(sid)
    return cdn_image_url(card.raw, size) or cdn_image_url(card.raw) or ""


async def card_name_text_search_matcher() -> None:

    async with SessionLocal() as session:
      if(not (await get_config(session)).ready):
          return None
      items = (await session.execute(
          select(Card.name, Card.scryfall_id)
          .where(and_(Card.type_line != "Card", Card.type_line.not_ilike("%token%")))
          .distinct(Card.name)
        )).all()
      if(len(items)==0):
          return None
      automaton = ahocorasick.Automaton()
      for item in items:
          automaton.add_word(
              item.name.lower(),
              (item.scryfall_id, item.name)
          )

      automaton.make_automaton()
      log.info("card.finished_ahocorasick_card_name_preload", items=len(items))
      return automaton


def linkify_names(text: str, automaton, useMarkdown:bool=False) -> Markup:
    text_lower = text.lower()
    matches = []

    for end, (item_id, name) in automaton.iter(text_lower):
        start = end - len(name) + 1
        # Word boundaries
        if start > 0 and text[start - 1].isalnum():
            continue
        if end + 1 < len(text) and text[end + 1].isalnum():
            continue
        matches.append((start, end + 1, item_id))

    # Prefer longest matches when names overlap
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    result = []
    pos = 0
    for start, end, item_id in matches:
        if start < pos:
            continue
        result.append(escape(text[pos:start]))
        if(useMarkdown):
            result.append('['+escape(text[start:end])+f'](/card/{item_id})' )
        else:
            result.append(
                Markup(f'<a href="/card/{item_id}">')
                + escape(text[start:end])
                + Markup('</a>')
            )
        pos = end

    result.append(escape(text[pos:]))
    return Markup('').join(result)
