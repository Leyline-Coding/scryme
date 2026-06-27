# Roadmap

Where scryme is and where it's headed. Planned work lives in
[GitHub Issues](https://github.com/Leyline-Coding/scryme/issues); this page is a curated overview,
not a commitment to dates or order. Have an idea? [Open an issue](https://github.com/Leyline-Coding/scryme/issues/new).

## Shipped

The MVP plus a steady stream of post-MVP features:

- **Search engine** — full Scryfall-style syntax, regex, boolean logic, and a
  [form-based advanced search](search/advanced.md). Collection-scoped by default.
- **Collection import** — ManaBox, Dragon Shield, and Delver Lens, with a
  [preview → confirm merge](import/merge.md) flow.
- **Card detail pages** with art, oracle text, prices, legalities, rulings, and
  [mana/set symbols](features/cards.md).
- **Decks** — paste a list for [ownership coverage + format legality](features/decks.md).
- **Binders** — browse your collection by its import binders.
- **[Set completion](features/sets.md)** — how much of each set you own, with the missing cards.
- **[Price history](features/prices.md)** — value-over-time snapshots, biggest movers, and
  acquisition profit/loss.
- **[Collection stats](features/stats.md)** — colors, rarity, types, mana curve, top sets, and a
  value-over-time chart.
- **[Tags](features/cards.md#tags)** — label owned cards and find them with `tag:`.
- **[Wishlist](features/wishlist.md)** — a want list, including "add a deck's missing cards".
- **[Theming](features/theming.md)**, result **sort**, result **export** (CSV / decklist / ManaBox),
  and **saved searches**.
- **Physical-only** — Arena/MTGO-only cards are excluded; scryme is for paper collections.

## Planned

Tracked as open issues — roughly in priority order:

| Issue | Feature |
| --- | --- |
| [#64](https://github.com/Leyline-Coding/scryme/issues/64) | Deck export (text / Arena / MTGO / Moxfield) + per-deck stats |
| [#65](https://github.com/Leyline-Coding/scryme/issues/65) | Backup & restore of your data (logical dump) |
| [#58](https://github.com/Leyline-Coding/scryme/issues/58) | Saved-search alerts — surface newly-matching cards after an ingest |
| [#59](https://github.com/Leyline-Coding/scryme/issues/59) | Undo last import (pre-merge snapshot + one-click restore) |
| [#66](https://github.com/Leyline-Coding/scryme/issues/66) | More import formats (Archidekt / Moxfield / TCGplayer / Deckbox) + CSV column mapping |
| [#67](https://github.com/Leyline-Coding/scryme/issues/67) | Trade / surplus binder view |
| [#68](https://github.com/Leyline-Coding/scryme/issues/68) | Mobile-responsive pass |

## On the horizon

- **[#49 — Electron desktop app](https://github.com/Leyline-Coding/scryme/issues/49)** — package
  scryme as a self-contained desktop app with portable Postgres and a user-chosen data directory
  you can back up to Google Drive, Dropbox, and the like.

See the [architecture notes](development/architecture.md) and
[contributing guide](development/contributing.md) if you'd like to help build any of it.
