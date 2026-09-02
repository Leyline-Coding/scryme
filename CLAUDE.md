# CLAUDE.md

Guidance for working in this repository.

## What scryme is

A self-hostable web app that ingests a user's Magic: The Gathering collection (exported from
ManaBox, Dragon Shield, Delver Lens, Moxfield, or Archidekt) into a local database and lets them
search it with a Scryfall-style UI that understands **Scryfall search syntax and regex**. It has
since grown into a full collection-management app: decks, pricing, physical organization,
selling/trading, and optional LLM-backed deck tooling.

- **Single-user, no auth.** One implicit collection per deployment. A public demo runs with
  `SCRYME_READ_ONLY=true`.
- **Local card DB + cached images.** Scryfall *bulk data* is ingested into Postgres and card
  images are cached on disk, so the app works offline and stays within Scryfall's API policy.

## Architecture

- **Backend:** FastAPI + SQLAlchemy 2.0 async + asyncpg, Alembic migrations. `backend/src/`.
- **Frontend:** server-rendered Jinja2 templates + HTMX + Alpine.js + Tailwind (CDN). No SPA.
- **DB:** PostgreSQL 16. Searchable card fields are columns; the full Scryfall object lives in
  `cards.raw` (JSONB). `pg_trgm` GIN indexes back name/oracle-text regex search.
- **Layout:** `backend/src/{models,routes,scryfall,search,importers,templates,static}` plus
  ~50 feature modules directly under `src/` (see the feature map below).
- **Desktop:** `desktop/` — Electron shell wrapping a PyInstaller-frozen backend + embedded
  Postgres. Ships AppImage (x64/arm64), Windows exe, and macOS dmg/zip. Entry: `src/desktop_entry.py`.
- **Docs:** `docs/` — MkDocs site published to docs.scryme.app.
- **CI:** GitHub Actions (Tests, CodeQL, Trivy, pip-audit, Dependabot) *and* Jenkins → SonarQube
  (`Jenkinsfile`, `sonar-project.properties`). Deployment manifests in `deploy/k8s`.

## Scryfall API rules (do not violate)

See https://scryfall.com/docs/api. Enforced in `src/scryfall/`:
- Send `User-Agent` and `Accept` headers on every request (`src/config.py`).
- Keep requests under 10/s; back off on HTTP 429 (30s lockout).
- Prefer the **bulk data** files for mass lookups; cache downloaded data for >= 24h
  (`ingest_state` table guards re-downloads).

## Common commands

```bash
# Local dev (hot reload + Postgres)
docker compose -f docker-compose.dev.yml up

# Production / self-host
docker compose up -d            # serves on http://localhost:8080

# Backend tests (needs a Postgres reachable via SCRYME_DATABASE_URL)
cd backend && pytest tests/     # ~1100 tests
ruff check src tests

# Migrations
cd backend && alembic revision --autogenerate -m "msg" && alembic upgrade head
```

> **Never point `SCRYME_DATABASE_URL` at a database you care about when running the tests.**
> `tests/conftest.py` runs `Base.metadata.drop_all` against whatever it resolves to, so running
> the suite against the dev DB **wipes it**. Use a dedicated database (e.g. `scryme_test`).

## Conventions

- **Branch per feature** (`feat/*`) → PR into `main`; CI (GitHub Actions) must pass.
- Searchable card attributes get promoted to indexed columns; everything else reads from
  `cards.raw`. Add a migration when promoting a new field.
- Never commit personal collection data. `tests/fixtures/*_full.csv` is gitignored; commit only
  small redacted `*_sample.csv` fixtures.
- Feature modules carry the originating issue number in their docstring (`"""... (#179)."""`) —
  useful for tracing why something exists.

## Operational commands

```bash
# Card data
python -m src.cli ingest [--force]          # Scryfall bulk file (24h cache guard; --force overrides)
python -m src.cli backfill-images           # cache images for owned cards
python -m src.cli prune-digital             # drop digital-only (Arena/MTGO) cards
python -m src.cli refresh-sets              # sync the set-release calendar
python -m src.cli backfill-mtgjson-ids      # map cards to MTGJSON ids (for Card Kingdom prices)

# Pricing
python -m src.cli snapshot-prices           # price snapshot of the owned collection
python -m src.cli seed-price-history        # synthesize monthly history (dev/demo only)
python -m src.cli sync-market-prices        # preferred-marketplace prices (#231)
python -m src.cli refresh-fx                # FX rates for converted currencies (#232)
python -m src.cli backfill-fx-history       # historical daily FX rates (#233)

# AI features (optional)
python -m src.cli backfill-embeddings       # oracle-text embeddings for "cards like this"
python -m src.cli backfill-rules            # chunk the comprehensive rules for rules Q&A

# Data
python -m src.cli backup [--dir DIR]        # JSON backup of user data
python -m src.cli restore FILE [--apply]    # restore (dry-run without --apply)
python -m src.cli seed-demo                 # sample collection for the demo
python -m src.cli organize-locations        # set each card's location to its color-identity group

# or via HTTP:  POST /admin/ingest   GET /admin/status
```

On-disk/scheduled backups live in `src/backup.py` (`write_backup`/`list_backups`/`prune_backups`/
`restore_from_path`), driven by `SCRYME_BACKUP_DIR` / `_INTERVAL_HOURS` / `_KEEP`; the scheduler
(`src/scheduler.py`) adds a backup job when configured. Optional passphrase encryption in
`src/cryptobackup.py`. UI: `/backup` (download/upload restore + on-disk list).

## Search engine (`src/search/`)

`lexer` → `parser` (AST) → `compiler` (SQLAlchemy over `cards`) → `engine.run_search`.
Supported filters: name, `o:`/oracle, `t:`/type, `c:`/color, `id:`/identity, `m:`/mana,
`mv`/`cmc`, `pow`/`tou`/`loy`, `r:`/rarity, `s:`/set, `cn:`, `is:`, `f:`/format, `usd`/`eur`/`tix`,
`lang`, `kw:`, `year`/`date`, `layout`, `a:`/artist, `wm:`/watermark, `border:`, `frame:`,
`game:`, `st:`/set_type, `stamp:`, `tag:` (user tags on owned cards, via `collection_card.tags`),
`location:` (physical storage, via `collection_card.location`);
boolean `OR`/`AND`/`-`/parentheses; `/regex/` (Postgres `~*`,
text fields only). `:` means `=` for numeric fields. Unknown keywords raise `SearchError`. Default
scope is the owned collection; `scope=all` searches every card.

## Collection import (`src/importers/`)

Two-phase upload: `service.stage_upload` detects the format (`base` registry), parses to
`ImportRow`s, matches each to a card (`matching`: Scryfall ID → set+number → name → unmatched),
and stages the result in `import_staging`; `service.confirm_upload` applies a `MergeStrategy`
(replace / increment / per_card) via `merge.apply_merge` and clears the staging row. Parsers:
ManaBox, Dragon Shield, Delver Lens, Moxfield, Archidekt. Add one by writing a module in
`importers/` with `detect`/`parse` and `@register`, then import it in `importers/__init__.py`. Any
unrecognized CSV falls back to the **column-mapping wizard** (`importers/mapping.py`;
`service.stage_mapped_upload`). Routes: `/upload` (form + preview), `/upload/mapped` (wizard),
`/upload/confirm`. `src/import_undo.py` snapshots the collection before a merge so an import can
be rolled back.

## Feature map

Where to look when working on an area. Each module's docstring names the issue it came from.

| Area | Routes | Modules |
| --- | --- | --- |
| Search & browse | `/search`, `/advanced`, `/saved`, `/card/{id}` | `search/`, `facets.py`, `saved_alerts.py`, `symbols.py` |
| Collection | `/collection` (`routes/mycollection.py`), `/collection/*` edits (`routes/collection.py`), `/upload`, `/export`, `/stats` | `collection_edit.py`, `importers/`, `import_undo.py`, `tags.py`, `stats.py` |
| Decks | `/decks` | `decks.py`, `deck_builder.py`, `deck_import.py`, `deck_export.py`, `deck_sync.py`, `deck_versions.py`, `deck_suggest.py`, `brackets.py` |
| Pricing | `/prices`, `/watch`, `/alerts`, `/sell`, `/valuation` | `prices.py`, `pricing.py`, `market_prices.py`, `price_watch.py`, `currency.py`, `fx.py`, `valuation.py`, `sell.py` |
| Physical organization | `/binders`, `/sets`, `/calendar`, `/checklists`, `/trade` | `binder_service.py`, `box_service.py`, `sets.py`, `set_calendar.py`, `checklists.py`, `grading.py`, `trade.py` |
| AI (optional) | `/ai`, `/decks/{id}/{analyze,suggest,chat,upgrade}` | `llm.py`, `rules_rag.py`, `embeddings.py` |
| Platform | `/api/v1`, `/admin`, `/settings`, `/prefs`, `/backup`, `/health` | `api.py`, `preferences.py`, `backup.py`, `cryptobackup.py`, `scheduler.py`, `perfcache.py`, `admin_stats.py`, `lan.py` |

**AI features are opt-in and grounded.** `src/llm.py` talks to any OpenAI-compatible
`/chat/completions` endpoint (OpenAI, OpenRouter, local Ollama / LM Studio); config lives in the
`LLMSettings` singleton with the API key encrypted at rest, falling back to `SCRYME_LLM_*` env
vars. Every card the model names is **validated against the database** before it reaches the UI,
so hallucinated cards never surface. The HTTP client is injectable — tests use a deterministic
fake and never hit the network.

**JSON API:** versioned at `/api/v1` (`src/routes/api.py`), OpenAPI at `/docs`, optional
`SCRYME_API_TOKEN`.

## Status

**Six of the eight milestones are closed**: #7 (Decks & EDH tooling), #8 (Collection & physical
organization), #9 (Platform: API & AI), #10 (UX, mobile & polish), #13 (Collection Pricing, Selling
& Grading) and #14 (Trade Function). Current release line is **0.26.x**; migrations through
`0035_share_link`.

In flight:
- **Milestone #11 — Cross-app integration.** The last unstarted epic, and now unblocked: a companion
  scanner app ("scanme") consuming a batch scan-ingest API (#164), QR device pairing (#165), SSE
  live updates (#166), perceptual-hash card recognition (#167), scan sessions (#168) and decklist
  OCR (#169), plus QR-matched trading (#286). #209 tracks the producer/consumer contract map
  against the scanme repo. Per-device tokens (#204) shipped, so nothing here is blocked any more.
- **Milestone #12 — Settings & multi-device foundation.** The preferences singleton, unified
  `/settings` page and per-device API tokens have shipped, as has the optimistic-concurrency half
  of #207 (a `version` guard on `collection_card`, HTTP 409 on mismatch). #207 stays open for its
  live-sync half, which should share the SSE stream with #166.
