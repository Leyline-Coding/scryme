# Card details & symbols

## Card detail page

Click any card in the results grid (or a binder/stats list) to open its detail page:

- Full-size art and a link to the card on Scryfall.
- Mana cost, type line, oracle text (rendered per face for double-faced cards), and
  power/toughness or loyalty.
- Set, collector number, rarity, artist, language, and release date.
- Prices (USD / USD foil / EUR / TIX when available).
- A **format legality** grid.
- **In your collection** — every stack you own of that printing (quantity, finish, condition,
  language, binder).
- **Other printings** — thumbnails of every other printing of the card, each linking to its own
  page.
- **Rulings** — loaded on demand from Scryfall; the page renders instantly and degrades gracefully
  if rulings can't be fetched.

## Tags

On a card you own, the detail page has a **Tags** editor: add free-form labels like `for-trade`,
`deck:goblins`, or `sentimental` and remove them with a click. Tags are stored on your collection
(not the card database), so they apply to the printing you own and survive re-ingests.

Find tagged cards with the [`tag:` search filter](../search/syntax.md) — e.g. `tag:for-trade` or
`-tag:for-trade` to exclude. Tags also show as small chips under each card in the results grid.

Tags are normalized (trimmed and lower-cased), so `For Trade` and `for trade` are the same tag.

## Mana & set symbols

Mana costs and oracle text render with real Magic symbols, and set codes render as their expansion
symbols (tinted by rarity), using the open-licensed
[Mana](https://mana.andrewgioia.com) and [Keyrune](https://keyrune.andrewgioia.com) icon fonts
(SIL OFL / MIT). The fonts are vendored, so symbols work offline with no external requests.

## Card typography

Card pages use MTG-flavored typefaces — **Cinzel** for card titles and **EB Garamond** for type
lines and oracle text — to approximate the look of a real card. These are free, open-licensed
(SIL OFL) fonts, vendored for offline use; they are not the proprietary fonts printed on physical
cards.
