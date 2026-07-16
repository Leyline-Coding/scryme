# Architecture

scryme is a single Python service (FastAPI) backed by PostgreSQL, rendering a server-side UI with
Jinja2 + HTMX + Tailwind. There is no separate single-page app.

## Stack

| Layer | Technology |
| --- | --- |
| Backend | FastAPI, async SQLAlchemy 2.0 + asyncpg, Alembic |
| Frontend | Jinja2 templates + HTMX + Alpine.js + Tailwind (CDN) |
| Database | PostgreSQL 16 (`pg_trgm` for regex/`ILIKE` acceleration) |
| Scheduling | APScheduler (in-process daily bulk refresh) |
| Packaging | Docker (multi-stage), Docker Compose, nginx |
| CI | GitHub Actions (ruff, pytest + coverage, pip-audit, CodeQL) + a self-hosted SonarQube gate — see [Quality & Security](quality-and-security.md) |

## Data model

- **`cards`** — one row per Scryfall printing. Frequently-searched attributes are promoted to
  indexed columns; the complete Scryfall object is kept in a `raw` JSONB column (GIN-indexed) so any
  field is reachable without a migration.
- **`collection_card`** — the single user's owned stacks, keyed by
  *(card, finish, condition, language, binder)*.
- **`ingest_state`** — tracks the last bulk download to honor the ≥24h cache rule.
- **`import_staging`** — holds a parsed, matched upload between preview and confirm.
- **`saved_search`** — named queries (query + scope + sort + direction).
- **`deck` / `deck_card`** — decks and their lines, matched to the collection by oracle id.
- **`price_snapshot` / `card_price_point`** — periodic collection-value snapshots + per-card prices
  for the price-history page.

## Backend modules (`backend/src/`)

| Module | Responsibility |
| --- | --- |
| `scryfall/` | Policy-compliant API client, bulk ingestion (`ijson` streaming), image cache |
| `search/` | `lexer` → `parser` (AST) → `compiler` (SQLAlchemy) → `engine.run_search` |
| `importers/` | Format registry, per-app parsers, card matching, merge strategies |
| `decks.py` | Decklist parsing, card resolution, ownership coverage + legality |
| `stats.py` | Owned-collection aggregates for the stats dashboard |
| `prices.py` | Price snapshots, value-over-time series, biggest movers |
| `symbols.py` | Render `{…}` mana tokens and set symbols via the Mana/Keyrune fonts |
| `routes/` | `health`, `home`, `search`, `card`, `upload`, `export`, `saved`, `stats`, `prices`, `decks`, `binders`, `admin` |
| `scheduler.py` | Daily Scryfall refresh |
| `cli.py` | `ingest`, `backfill-images`, `seed-demo` |

## Scryfall integration

scryme follows [Scryfall's API policy](https://scryfall.com/docs/api): every request sends a
descriptive `User-Agent` and an `Accept` header, traffic stays under 10 requests/second, a `429`
triggers a 30-second backoff, and bulk data is cached for at least 24 hours. Mass data and images
come from **bulk downloads**, not per-card API calls.

## Request flow (search)

```
Browser ──HTMX GET /search──▶ FastAPI route
                               └─▶ search.engine.run_search
                                     ├─ parser.parse → AST
                                     ├─ compiler.compile_node → SQLAlchemy WHERE
                                     └─ execute against cards (scoped to collection)
                               ◀── rendered results partial (card grid)
```
