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

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard
from src.tags import add_card_tag


_STACK_NOT_FOUND = "Stack not found."


def _as_uuid(value) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _clean(value: str | None) -> str | None:
    """Trim a free-text field; empty -> None (so it matches the NULLable stack key)."""
    v = (value or "").strip()
    return v or None


def _eq_or_null(col, value):
    return col.is_(None) if value is None else col == value


async def add_or_increment(
    session: AsyncSession,
    scryfall_id,
    quantity: int = 1,
    *,
    finish: str = "normal",
    condition: str | None = None,
    language: str = "en",
    location: int | None = None,
    purchase_price: float | None = None,
    commit: bool = True,
) -> CollectionCard | None:
    """Add a printing to the collection, incrementing the matching stack if it exists.

    Returns the stack, or None if the printing is unknown. ``commit=False`` leaves the row pending
    so a caller that has to apply several changes as one unit (a trade commit, #332) can own the
    transaction — the stack-key logic is subtle enough that a second copy of it would drift.
    """
    sid = _as_uuid(scryfall_id)
    if await session.get(Card, sid) is None:
        return None
    quantity = max(1, quantity)
    finish = finish if finish in ("normal", "foil", "etched") else "normal"
    condition, location = _clean(condition), _clean(location)
    language = (language or "en").strip().lower() or "en"

    stack = (
        await session.execute(
            select(CollectionCard).where(
                CollectionCard.scryfall_id == sid,
                CollectionCard.finish == finish,
                CollectionCard.language == language,
                _eq_or_null(CollectionCard.condition, condition),
                _eq_or_null(CollectionCard.location, location),
            )
        )
    ).scalar_one_or_none()

    if stack is None:
        stack = CollectionCard(
            scryfall_id=sid, quantity=quantity, finish=finish, condition=condition,
            language=language, location=location,
            purchase_price=purchase_price, source_format="manual",
        )
        session.add(stack)
    else:
        stack.quantity += quantity
        stack.version += 1
    if not commit:
        await session.flush()
        return stack
    await session.commit()
    await session.refresh(stack)
    return stack


async def adjust_quantity(session: AsyncSession, stack_id: int, delta: int):
    """Change a stack's quantity by ``delta``; delete it if it reaches zero. Returns scryfall_id."""
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    sid = stack.scryfall_id
    stack.quantity += delta
    stack.version += 1
    if stack.quantity <= 0:
        await session.delete(stack)
    await session.commit()
    return sid


_UNSET = object()


class StaleStackError(Exception):
    """An absolute edit was attempted against a version of a stack that has since changed.

    Carries the current row so the caller can show what it is *now* rather than just failing —
    losing the other person's edit and the editor's own work would be two problems, not one.
    """

    def __init__(self, stack: CollectionCard):
        super().__init__("This stack changed since it was loaded.")
        self.stack = stack


def _guard_version(stack: CollectionCard, expected: int | None) -> None:
    """Refuse an absolute edit whose base version moved. ``None`` opts out (internal callers)."""
    if expected is not None and stack.version != expected:
        raise StaleStackError(stack)


async def update_stack(
    session: AsyncSession,
    stack_id: int,
    *,
    quantity: int | None = None,
    finish: str | None = None,
    condition=_UNSET,
    language: str | None = None,
    location=_UNSET,
    tags=_UNSET,
    expected_version: int | None = None,
):
    """Update fields on a stack (any left unset stay put). Returns the stack, or None if missing.

    ``quantity`` is clamped to >= 1 (use :func:`delete_stack` to remove a stack). ``condition`` /
    ``binder`` / ``location`` / ``tags`` take an explicit ``None`` to clear them (sentinel default).

    This is an *absolute* edit, so ``expected_version`` guards it: pass the version the editor was
    shown and a :class:`StaleStackError` is raised if the row moved since (#207). Omitting it opts
    out, for internal callers that aren't replaying a human's stale view.
    """
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    _guard_version(stack, expected_version)
    if quantity is not None:
        stack.quantity = max(1, quantity)
    if finish is not None:
        stack.finish = finish
    if language is not None:
        stack.language = language or "en"
    if condition is not _UNSET:
        stack.condition = _clean(condition)
    if location is not _UNSET:
        stack.location = _clean(location)
    if tags is not _UNSET:
        stack.tags = tags or None
    stack.version += 1
    await session.commit()
    return stack


async def delete_stack(session: AsyncSession, stack_id: int, expected_version: int | None = None):
    """Delete a stack outright. Returns its scryfall_id (or None if it didn't exist).

    Guarded like :func:`update_stack`: deleting a stack someone else has just changed is the most
    destructive way to lose their edit.
    """
    stack = await session.get(CollectionCard, stack_id)
    if stack is None: return None
    _guard_version(stack, expected_version)
    sid = stack.scryfall_id
    await session.delete(stack)
    await session.commit()
    return sid


async def move_stacks_location(session: AsyncSession, stack_ids: list[int], location_id: int | None = None) -> list[int]:
    """Move stacks to a new location. Returns a list of scryfall_ids (or None if it didn't exist).
    
    If a target location already has an identical collection card, the source card's quantity
    will be added to the existing card and the source card will be deleted.
    """
    stacks_to_move = (await session.scalars(select(CollectionCard).where(CollectionCard.id.in_(stack_ids)))).all()
    
    # Preload any OTHER CollectionCard witch both a matching target location and with a matching scryfall_id. _cards_are_identical will perform the fine-grained match
    existing_stacks_preloaded = (await session.scalars(
        select(CollectionCard).where(
            CollectionCard.id.not_in([card.id for card in stacks_to_move]),
            CollectionCard.location == location_id,
            CollectionCard.scryfall_id.in_([card.scryfall_id for card in stacks_to_move])
        )
    )).all()

    sids = []
    # Process each stack one by one
    for stack_to_move in stacks_to_move:
          
          # Finely check the coarsely preload CollectionCard matches using _cards_are_identical()
          identical_existing = next(
              (existing_stack for existing_stack in existing_stacks_preloaded if stack_to_move.is_identical_to_stack(existing_stack)),
              None
          )

          if identical_existing:
              # Merge the stacks: increment the quantity of the existing card and delete source
              identical_existing.quantity += stack_to_move.quantity
              await session.delete(stack_to_move)
              sids += [identical_existing.id]
          else:
              # No identical card at target location, just update location of the existing stack
              stack_to_move.location = location_id
              sids += [stack_to_move.id]
    
    await session.commit()
    return sids



async def move_stack_location(session: AsyncSession, stack_id: int, location_id: int | None = None) -> int | None:
    """Move a single stack to a new location. Returns its scryfall_id (or None if it didn't exist).
    """
    result = await move_stacks_location(session, [stack_id], location_id)
    return result[0] if result else None


async def adjust_owned(session: AsyncSession, scryfall_id, delta: int) -> None:
    """Apply a +/- quantity delta to a printing's default (normal/en, unfiled) stack (#298).

    Used to mirror owned-deck edits into the collection. A positive delta increments (creating the
    stack if needed); a negative delta reduces the default stack, deleting it at zero.
    """
    if delta > 0:
        await add_or_increment(session, scryfall_id, delta)
        return
    if delta == 0:
        return
    stack = (await session.execute(
        select(CollectionCard).where(
            CollectionCard.scryfall_id == _as_uuid(scryfall_id),
            func.lower(CollectionCard.finish) == "normal",
            CollectionCard.language == "en",
            CollectionCard.location.is_(None),
        ).order_by(CollectionCard.id)
    )).scalars().first()
    if stack is None:
        return
    stack.quantity += delta
    if stack.quantity <= 0:
        await session.delete(stack)
    await session.commit()



async def edit_stack(
    session: AsyncSession, stack_id: int, *,
    quantity: int | None = None, finish: str | None = None, scryfall_id=None,
    expected_version: int | None = None,
):
    """Edit a stack's quantity, finish, and/or printing, merging into a sibling stack if the change
    collides with the (scryfall_id, finish, condition, language, binder, location) key.

    Returns the surviving stack (or None if the stack/target printing is missing). Absolute, so
    ``expected_version`` guards it (#207).
    """
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        return None
    _guard_version(stack, expected_version)
    target_sid = _as_uuid(scryfall_id) if scryfall_id else stack.scryfall_id
    if scryfall_id and await session.get(Card, target_sid) is None:
        return None
    target_finish = finish if finish in ("normal", "foil", "etched") else stack.finish
    new_qty = max(1, quantity) if quantity is not None else stack.quantity

    dup = (await session.execute(
        select(CollectionCard).where(
            CollectionCard.id != stack.id,
            CollectionCard.scryfall_id == target_sid,
            CollectionCard.finish == target_finish,
            CollectionCard.language == stack.language,
            _eq_or_null(CollectionCard.condition, stack.condition),
            _eq_or_null(CollectionCard.location, stack.location),
        )
    )).scalar_one_or_none()

    if dup is not None:
        dup.quantity += new_qty
        merged_tags = sorted(set((dup.tags or []) + (stack.tags or [])))
        dup.tags = merged_tags or None
        dup.version += 1
        await session.delete(stack)
        survivor = dup
    else:
        stack.scryfall_id = target_sid
        stack.finish = target_finish
        stack.quantity = new_qty
        stack.version += 1
        survivor = stack
    await session.commit()
    return survivor


async def owned_for_oracle(session: AsyncSession, oracle_id) -> list[tuple[CollectionCard, Card]]:
    """Every owned stack of any printing sharing this oracle id (for the remove-copies modal)."""
    rows = (await session.execute(
        select(CollectionCard, Card)
        .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        .where(Card.oracle_id == oracle_id)
        .order_by(Card.released_at.desc().nulls_last(), CollectionCard.finish)
    )).all()
    return [(cc, c) for cc, c in rows]


async def remove_copies(session: AsyncSession, removals: list[tuple[int, int]]) -> int:
    """Apply per-stack removals ``[(stack_id, count), ...]``; a stack hitting zero is deleted.

    Returns the number of copies actually removed.
    """
    removed = 0
    for stack_id, count in removals:
        if count <= 0:
            continue
        stack = await session.get(CollectionCard, stack_id)
        if stack is None:
            continue
        take = min(count, stack.quantity)
        stack.quantity -= take
        removed += take
        if stack.quantity <= 0:
            await session.delete(stack)
    if removed:
        await session.commit()
    return removed


async def bulk_add_to_collection(
    session: AsyncSession, scryfall_ids: list, quantity: int = 1
) -> int:
    """Add one default (normal/en) stack copy per printing. Returns how many were added."""
    added = 0
    for sid in scryfall_ids:
        if await add_or_increment(session, sid, quantity) is not None:
            added += 1
    return added


async def bulk_add_tag(session: AsyncSession, scryfall_ids: list, tag: str) -> int:
    """Add a tag to every owned stack of each printing. Returns how many printings were tagged."""
    tagged = 0
    for sid in scryfall_ids:
        result = await add_card_tag(session, _as_uuid(sid), tag)
        if result:
            tagged += 1
    return tagged


# --- organize by color identity (#160) ----------------------------------------------------------

_MONO = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
_GUILDS = {
    "WU": "Azorius", "WB": "Orzhov", "WR": "Boros", "WG": "Selesnya", "UB": "Dimir",
    "UR": "Izzet", "UG": "Simic", "BR": "Rakdos", "BG": "Golgari", "RG": "Gruul",
}
_SHARDS = {
    "WUB": "Esper", "WUR": "Jeskai", "WUG": "Bant", "WBR": "Mardu", "WBG": "Abzan",
    "WRG": "Naya", "UBR": "Grixis", "UBG": "Sultai", "URG": "Temur", "BRG": "Jund",
}


def color_identity_group(color_identity: list[str] | None) -> str:
    """Name of the color-identity binder a card belongs in (mono/guild/shard/…)."""
    key = "".join(c for c in "WUBRG" if c in set(color_identity or []))
    if not key:
        return "Colorless"
    if len(key) == 1:
        return _MONO[key]
    if len(key) == 2:
        return _GUILDS[key]
    if len(key) == 3:
        return _SHARDS[key]
    return "Four-color" if len(key) == 4 else "Five-color"


async def organize_by_color_identity(session: AsyncSession) -> int:
    """Set every owned stack's ``location`` to its card's color-identity group. Returns rows set."""
    from sqlalchemy import update

    rows = (await session.execute(
        select(CollectionCard.id, Card.color_identity)
        .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
    )).all()
    buckets: dict[str, list[int]] = {}
    for stack_id, ci in rows:
        buckets.setdefault(color_identity_group(ci), []).append(stack_id)
    for name, ids in buckets.items():
        await session.execute(
            update(CollectionCard).where(CollectionCard.id.in_(ids)).values(location=name)
        )
    await session.commit()
    return sum(len(ids) for ids in buckets.values())


@dataclass
class LocationSummary:
    location: str | None
    stacks: int
    quantity: int


async def location_summary(session: AsyncSession) -> list[LocationSummary]:
    """Card counts per physical location (None = unfiled), for the locations page."""
    rows = (await session.execute(
        select(CollectionCard.location, func.count(),
                func.sum(CollectionCard.quantity))
        .group_by(CollectionCard.location)
        .order_by(CollectionCard.location.nulls_last())
    )).all()
    return [LocationSummary(loc, int(n), int(q or 0)) for loc, n, q in rows]


async def bulk_move_to_location(session: AsyncSession, location_id: int, scryfall_ids) -> None:
    """Move owned printings to a specific location by updating their location."""
    # Convert to UUID if needed
    sids = {_as_uuid(s) for s in scryfall_ids}

    #TODO: If location is a deck, add cards to the deck card list before moving

    # Filter only those cards that exist in collection
    owned_rows = (await session.execute(
        select(CollectionCard.id).where(
            CollectionCard.scryfall_id.in_(sids)
        )
    )).scalars().all()
    
    if not owned_rows:
        return

    await session.execute(
        update(CollectionCard)
        .where(CollectionCard.id.in_(owned_rows))
        .values(location=location_id)
    )
    await session.commit()


async def pull_stack_from_existing_stack(session: AsyncSession, stack_id: int, num_cards: int = 1, new_location_id: int | None = None, expected_version: int | None = None) -> uuid.UUID | None:
    """Pull num_cards card(s) from an existing stack into a new stack with all the same attributes.
    
    Returns the scryfall_id of the card that was pulled, or None if the operation failed.
    """
    original_stack = await session.get(CollectionCard, stack_id)
    if original_stack is None:
        return None
    
    _guard_version(original_stack, expected_version)
    
    # If we can't reduce quantity, or the old stack will be 0, nothing to do
    if original_stack.quantity <= num_cards: return None
        
    # Create a new stack with all the same attributes but quantity of num_cards
    new_stack = CollectionCard(
        scryfall_id=original_stack.scryfall_id,
        quantity=num_cards,
        finish=original_stack.finish,
        condition=original_stack.condition,
        language=original_stack.language,
        location=new_location_id,
        purchase_price=original_stack.purchase_price,
        source_format="manual",
        added_at=original_stack.added_at,
        tags=original_stack.tags
    )
    
    session.add(new_stack)
    await session.flush()  # Get the ID for version update
    
    # Update original stack quantity
    original_stack.quantity -= num_cards
    original_stack.version += 1
    
    await session.commit()
    await session.refresh(new_stack)
    
    return new_stack.scryfall_id




async def move_qty_from_existing_stack(session: AsyncSession, collection_card: CollectionCard, quantity: int, new_location_id: int | None) -> int:
    """ Move arbitrary qty of cards in CollectionCard to the specified location. 
    
    Will attempt to move up to but no more than collection_card.quantity. Returns the number of cards in the new location, thus the number of cards actualyl moved.
    """
    # If the stack has more cards than we need, split it into the new location
    if(collection_card.quantity > quantity):
        await pull_stack_from_existing_stack(session, stack_id=collection_card.id, num_cards=quantity, new_location_id=new_location_id)
        return quantity
    # If the stack has as many or fewer cards than we need, move the whole stack
    else:
        remainder = collection_card.quantity - quantity
        await move_stack_location(session, collection_card.id, location_id=new_location_id)
        return collection_card.quantity