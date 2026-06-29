# scryme

**scryme** is a self-hostable, [Scryfall](https://scryfall.com)-style search engine for **your
own** Magic: The Gathering collection. Upload an export from ManaBox, Dragon Shield, or Delver
Lens, then search it with Scryfall syntax and regular expressions.

## Highlights

- 🔎 **Scryfall-compatible search** with regex — scoped to your collection or all cards, with
  sorting, a **grid / table** toggle, **clickable [facets](search/syntax.md)**, **"did you mean?"**
  suggestions, **saved searches**, an **[advanced form builder](search/advanced.md)**, and CSV /
  decklist / ManaBox **export**.
- 🃏 **Rich card pages** — full oracle text, prices, legalities, printings, rulings, real
  **mana & set symbols**, **[tags](features/cards.md#tags)** you can search with `tag:`, and inline
  **[add/edit](features/cards.md#editing-your-collection)** (plus bulk edit from results).
- 🧰 **Collection tools** — a **[stats dashboard](features/stats.md)** (value + growth over time),
  **[price history](features/prices.md)** (movers + acquisition profit/loss),
  **[decks](features/decks.md)** with coverage, legality, stats, and export,
  **[set completion](features/sets.md)**, **[custom checklists](features/checklists.md)**, a
  **[wishlist](features/wishlist.md)**, a **[trade binder](features/trade.md)**, and
  **[binder](features/binders.md)** browsing.
- 📥 **Collection import** from ManaBox, Dragon Shield, Delver Lens, Moxfield, and Archidekt — or
  **any CSV** via the column-mapping wizard (preview → **replace / increment / per-card** merge),
  and **[backup & restore](features/backup.md)** of all your data — including scheduled on-disk
  backups to a folder you choose.
- 🗃️ **Local card database + image cache** built from Scryfall bulk data — works offline and stays
  within [Scryfall's API policy](https://scryfall.com/docs/api).
- 🎨 **Themeable UI** (preset themes + custom accent), **USD/EUR** currency, and 🐳 **self-hostable
  via Docker**, with an optional read-only public demo.

See the **[roadmap](roadmap.md)** for what's shipped and what's planned.

## Quick start

```bash
docker compose up -d
docker compose exec backend python -m src.cli ingest   # download the Scryfall bulk file
# open http://localhost:8080
```

The home page starts as an upload prompt. After you import a collection it becomes a
Scryfall-style search bar.

## Where to next

<div class="grid cards" markdown>

- :material-rocket-launch: **[Self-Hosting](getting-started/self-hosting.md)** — run scryme with Docker.
- :material-upload: **[Importing Collections](import/overview.md)** — supported formats and merge behavior.
- :material-magnify: **[Search Syntax](search/syntax.md)** — every supported filter, sorting, and export.
- :material-cards: **[Decks](features/decks.md)** — ownership coverage and legality checks.
- :material-chart-box: **[Collection stats](features/stats.md)** — value and breakdowns at a glance.
- :material-code-braces: **[Architecture](development/architecture.md)** — how it's built.

</div>

!!! note "Fan content"
    Card data and images come from [Scryfall](https://scryfall.com). scryme is unofficial Fan
    Content permitted under the Wizards of the Coast Fan Content Policy and is not affiliated with
    or endorsed by Wizards of the Coast or Scryfall.
