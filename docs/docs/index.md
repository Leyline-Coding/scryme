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
- 🧰 **Collection tools** — a **[stats dashboard](features/stats.md)** (value + growth over time,
  with click-through breakdowns), **[price history](features/prices.md)** (movers + acquisition
  profit/loss), **[decks](features/decks.md)** with coverage, legality, stats, and export,
  **[set completion](features/sets.md)**, **[custom checklists](features/checklists.md)**, a
  **[wishlist](features/wishlist.md)**, and a **[trade binder](features/trade.md)**.
- 🗂️ **Organize your way** — **[custom binders](features/binders.md)**, physical
  **[storage locations](features/locations.md)** (boxes / binders / decks), and overlapping
  **[tags](features/cards.md#tags)** you can search with `tag:`.
- 🤖 **Optional [AI assistant](features/ai.md)** — connect any OpenAI-compatible endpoint (local
  **Ollama** / **LM Studio**, or hosted **OpenAI** / **Claude** / **Gemini** / **Perplexity**) for
  grounded deck analysis, upgrade planning, coaching chat, plain-English search, and card rules Q&A.
- 📥 **Collection import** from ManaBox, Dragon Shield, Delver Lens, Moxfield, and Archidekt — or
  **any CSV** via the column-mapping wizard (preview → **replace / increment / per-card** merge),
  and **[backup & restore](features/backup.md)** of all your data — including scheduled on-disk
  backups to a folder you choose.
- 🗃️ **Local card database + image cache** built from Scryfall bulk data — works offline and stays
  within [Scryfall's API policy](https://scryfall.com/docs/api).
- 🎨 **Themeable UI** (preset themes + custom accent), **USD/EUR** currency, and 🐳 **self-hostable
  via Docker**, with an optional read-only public demo.
- 🖥️ **Desktop app** (macOS / Windows / Linux) — an install-free build that bundles PostgreSQL and
  the backend, with drag-and-drop import, a quick-search hotkey, LAN sharing, and auto-update.

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
- :material-monitor: **[Desktop App](getting-started/desktop.md)** — the install-free native build.
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
