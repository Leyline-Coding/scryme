# Merge Strategies

When you confirm an import, scryme combines it with your existing collection using one of three
strategies. A **stack** is a distinct combination of *(card, finish, condition, language, binder)*
— the same identity used in storage — so the same card in foil vs. non-foil, or in two different
binders, are tracked separately.

## Add (increment)

Adds the imported quantities on top of what you already own.

- New stacks are inserted.
- Stacks you already own have their quantity **increased** by the imported amount.

> Example: you own `1× Black Lotus`; importing `1× Black Lotus` leaves you with `2×`.

## Replace

Wipes the current collection entirely, then inserts the import.

- The collection becomes exactly what's in the uploaded file.
- Use this to re-sync from an authoritative export.

## Decide per card

For each card that **already exists** in your collection, you choose individually:

- **Add** — increment that stack's quantity, or
- **Replace** — set that stack's quantity to the imported amount.

Cards that are new to your collection are always inserted. This is the most flexible option when an
export overlaps your collection only partially.

!!! info "Within a single file"
    If the same stack appears on multiple rows of one upload, those quantities are summed first,
    then the chosen strategy is applied.
