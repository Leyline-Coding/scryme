# Storage Locations

scryme tracks **where each card physically lives**. A stack sits in exactly one place — a **box**, a
**binder**, or a **deck** — while [tags](cards.md#tags) handle the flexible, overlapping
labels ("Ramp", "Removal", "Cats") a card can carry many of.

Open the **Locations** tab on the *My Collection* page.

## The three location kinds

- **Boxes** — physical bulk-storage containers you create and name (deck boxes, fat-pack boxes, that
  shoebox of commons). Managed entirely on the Locations tab.
- **Binders** — the [custom binders](binders.md) you create in the Binders tab.
- **Decks** — the [decks](decks.md) you've built or imported.

The Locations tab lists all three, each browsable, so you can see at a glance how many cards live
where.

## Boxes

On the Locations tab:

- **Add box** — create a new named box (empty is fine).
- **Rename** a box — the change follows every card filed in it.
- **Delete** a box — its cards become *unfiled* (they aren't removed from your collection).

Click a box to search its contents. Boxes are searchable anywhere with the
[`loc:` filter](../search/syntax.md) — e.g. `loc:"Bulk Box"`.

## Filing a card

On any owned card's [detail page](cards.md), each stack has a **location picker** — one dropdown
grouped into **Boxes**, **Binders**, and **Decks**:

- pick a **box** to set that stack's physical location,
- pick a **binder** to add the printing to that binder,
- pick a **deck** to add it to that deck's list.

Choose *unfiled* to clear a stack's box.

!!! tip "Tags vs. locations"
    Use **locations** for the one physical place a card is, and **tags** for how you *think* about
    cards (themes, roles, projects) — a card can have many tags but one location. Tags live on their
    own tab and each is a click-through to a `tag:` search.
