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



from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.models import Card

_cache = ImageCache()

def _image(card: Card, size: str = "normal") -> str:
    sid = str(card.scryfall_id)
    if size == "normal" and _cache.is_cached(sid):
        return _cache.url_path(sid)
    return cdn_image_url(card.raw, size) or cdn_image_url(card.raw) or ""
