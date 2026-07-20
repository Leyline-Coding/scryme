# Card details & symbols

## Card detail page

Click any card in the results grid (or a binder/stats list) to open its detail page:

- Full-size art and a link to the card on Scryfall.
- Mana cost, type line, oracle text (rendered per face for double-faced cards), and
  power/toughness or loyalty.
- Set, collector number, rarity, artist, language, and release date.
- Prices (USD / USD foil / EUR / TIX when available).
- A **price-history chart** for that printing, with the same time-range selector as the
  collection [value chart](prices.md) plus a [currency dropdown](prices.md#chart-currency). Cards you
  own or track (on your [wishlist](wishlist.md) or price watchlist) build history over time; other
  cards show a short "no history yet" note.
- A **format legality** grid.
- **In your collection** — every stack you own of that printing (quantity, finish, condition,
  language, binder). Own it in **foil** or **etched foil**? The card art gets an animated sheen (a
  rainbow holo for foil, a cooler engraved shimmer for etched) and the finish shows a shiny chip
  (✦ foil / ❖ etched). Find them with `is:foil` / `is:etched`.
- **Other printings** — thumbnails of every other printing of the card, each linking to its own
  page.
- **Rulings** — loaded on demand from Scryfall; the page renders instantly and degrades gracefully
  if rulings can't be fetched.

## Editing your collection

You don't have to re-import to make small changes. The **In your collection** box on each card page
is editable:

- **Adjust quantity** — the `−` / `+` buttons on a stack nudge its count; dropping to zero removes
  the stack.
- **Edit a stack** — the `✎` button opens an editor to change the **quantity**, **finish**
  (normal / foil / etched), and **printing** (move the stack to another printing of the same card).
  If an edit collides with a stack you already have, they're merged.
- **Tags**, a storage **location** (box / binder / deck), and **grading** are edited per stack too
  (see [tags](#tags) and [graded cards](grading.md)).
- **Remove from collection** — the **Remove…** link opens a dialog to choose **how many copies** to
  take out, and **which printings/finishes** when you own more than one.
- **Delete** a whole stack outright with the `✕` button.
- **Add to collection** — the small form adds a copy (quantity, finish, and an optional binder).
  Adding a printing you already own in the same finish/binder just increments that stack.

### Bulk edit from search

In the results grid, tick the checkbox on any cards to reveal a **bulk bar**:

- **Add tag** — apply a [tag](#tags) to every selected card at once.
- **+1 to collection** — add one copy of each selected printing (handy when browsing **All cards**
  to pull several into your collection quickly).

All editing is disabled on the read-only public demo.

## Tags

On a card you own, the detail page has a **Tags** editor: add free-form labels like `for-trade`,
`deck:goblins`, or `sentimental` and remove them with a click. Tags are stored on your collection
(not the card database), so they apply to the printing you own and survive re-ingests.

Find tagged cards with the [`tag:` search filter](../search/syntax.md) — e.g. `tag:for-trade` or
`-tag:for-trade` to exclude. Tags also show as small chips under each card in the results grid.
The **Tags** tab on the *My Collection* page lists every tag in use with its card count — click one
to jump straight to that `tag:` search.

Tags are the flexible, overlapping counterpart to a card's single physical
[location](locations.md): a card can carry as many tags as you like.

Tags are normalized (trimmed and lower-cased), so `For Trade` and `for trade` are the same tag.

## Mana & set symbols

Mana costs and oracle text render with real Magic symbols, and set codes render as their expansion
symbols (tinted by rarity), using the open-licensed
[Mana](https://mana.andrewgioia.com) and [Keyrune](https://keyrune.andrewgioia.com) icon fonts
(SIL OFL / MIT). The fonts are vendored, so symbols work offline with no external requests.

The symbols show up across the app: the **[advanced search](../search/advanced.md)** color pickers
are mana pips, the **[stats](stats.md)** and **[deck](decks.md)** color breakdowns are labeled with
their pips, and set symbols appear next to cards in results, binders, the wishlist, and set lists.

## Card typography

Card pages use MTG-flavored typefaces — **Cinzel** for card titles and **EB Garamond** for type
lines and oracle text — to approximate the look of a real card. These are free, open-licensed
(SIL OFL) fonts, vendored for offline use; they are not the proprietary fonts printed on physical
cards.
