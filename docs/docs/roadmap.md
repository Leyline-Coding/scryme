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
- **Decks** — paste a list for [ownership coverage, format legality, per-deck stats, and
  export](features/decks.md) (text / Arena / Moxfield / MTGO). Legality is judged by the card (not
  the printing, so non-standard variants never mis-flag a legal card), and each line has an
  editable **printing + language** with **Proxy / Special** markers.
- **Binders** — browse your collection by its import binders.
- **[Set completion](features/sets.md)** — how much of each set you own, with the missing cards.
- **[Price history](features/prices.md)** — value-over-time snapshots, biggest movers, and
  acquisition profit/loss.
- **[Collection stats](features/stats.md)** — colors, rarity, types, mana curve, top sets, and a
  value-over-time chart.
- **[Tags](features/cards.md#tags)** — label owned cards and find them with `tag:`.
- **[Wishlist](features/wishlist.md)** — a want list, including "add a deck's missing cards".
- **[Theming](features/theming.md)** + **[currency](features/theming.md#display-currency)** (USD/EUR),
  result **sort**, result **export** (CSV / decklist / ManaBox), and **saved searches**.
- **[Backup & restore](features/backup.md)** — a portable JSON dump of your data, plus scheduled
  on-disk backups (optionally encrypted).
- In-app **collection editing** (add/edit/bulk), **faceted browse**, and **"did you mean?"** search.
- More import formats (Moxfield, Archidekt) + a **CSV column-mapping wizard**; **trade/surplus
  binder**; **custom checklists**.
- **[Saved-search alerts](search/advanced.md)** — after each card-data update, saved searches
  surface cards that newly match (with a "What's new" panel and desktop notifications).
- **Build a deck from your collection** — pick a commander you own and get a 99-card singleton deck
  drawn from cards you have, balanced across roles.
- **Price watchlist** — set a per-card USD target and get alerted (badge, home panel, desktop
  notification) when the price crosses it.
- **Import a deck from a URL** — paste a Moxfield / Archidekt / TappedOut link.
- **Undo last import** — one-click restore of the collection from just before a confirmed import.
- A versioned **JSON/REST API** (`/api/v1`) and **Prometheus metrics** (`/metrics`).
- **[Desktop app](getting-started/desktop.md)** — a native macOS/Windows/Linux build (see below).
- **Physical-only** — Arena/MTGO-only cards are excluded; scryme is for paper collections.

## Desktop app

The **[Electron desktop app](getting-started/desktop.md)** is shipped: an install-free build that
bundles PostgreSQL and the backend, with drag-and-drop import, a global quick-search hotkey, LAN
sharing, system notifications, and auto-update. Still on its roadmap:

- **Signed & notarized installers** — today's builds are unsigned (SmartScreen / Gatekeeper warn on
  first open). The signing config is in place; it needs Windows/Apple certificates.
- **Store distribution** — Homebrew cask, winget, Flatpak, and AUR (starter manifests exist under
  `desktop/packaging/`).

Builds are **multi-arch**: macOS (Apple Silicon), Windows x64, and Linux x64 + arm64 (arm64 ships as
an AppImage; the `.deb` is x64-only).

## Planned

Tracked as open issues — roughly in priority order:

| Issue | Feature |
| --- | --- |
| [#68](https://github.com/Leyline-Coding/scryme/issues/68) | Mobile-responsive pass |
| [#80](https://github.com/Leyline-Coding/scryme/issues/80) | Read-only share links for decks/binders |
| [#97](https://github.com/Leyline-Coding/scryme/issues/97) | Sell list + valuation report |
| [#100](https://github.com/Leyline-Coding/scryme/issues/100) | Deck diff / versions |
| [#101](https://github.com/Leyline-Coding/scryme/issues/101) | Duplicate / merge stacks |

See the [architecture notes](development/architecture.md) and
[contributing guide](development/contributing.md) if you'd like to help build any of it.
