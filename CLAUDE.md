# CLAUDE.md

Guidance for working in this repository.

## What scryme is

A self-hostable web app that ingests a user's Magic: The Gathering collection (exported from
ManaBox, Dragon Shield, or Delver Lens) into a local database and lets them search it with a
Scryfall-style UI that understands **Scryfall search syntax and regex**.

- **Single-user, no auth.** One implicit collection per deployment. A public demo runs with
  `SCRYME_READ_ONLY=true`.
- **Local card DB + cached images.** Scryfall *bulk data* is ingested into Postgres and card
  images are cached on disk, so the app works offline and stays within Scryfall's API policy.

## Architecture

- **Backend:** FastAPI + SQLAlchemy 2.0 async + asyncpg, Alembic migrations. `backend/src/`.
- **Frontend:** server-rendered Jinja2 templates + HTMX + Alpine.js + Tailwind (CDN). No SPA.
- **DB:** PostgreSQL 16. Searchable card fields are columns; the full Scryfall object lives in
  `cards.raw` (JSONB). `pg_trgm` GIN indexes back name/oracle-text regex search.
- **Layout:** `backend/src/{models,routes,scryfall,search,importers,templates,static}`.

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
cd backend && pytest tests/
ruff check src tests

# Migrations
cd backend && alembic revision --autogenerate -m "msg" && alembic upgrade head
```

## Conventions

- **Branch per feature** (`feat/*`) → PR into `main`; CI (GitHub Actions) must pass.
- Searchable card attributes get promoted to indexed columns; everything else reads from
  `cards.raw`. Add a migration when promoting a new field.
- Never commit personal collection data. `tests/fixtures/*_full.csv` is gitignored; commit only
  small redacted `*_sample.csv` fixtures.

## Operational commands

```bash
# Ingest the Scryfall Default Cards bulk file (honors the 24h cache guard; --force overrides)
python -m src.cli ingest [--force]
python -m src.cli backfill-images          # cache images for owned cards
# or via HTTP:  POST /admin/ingest   GET /admin/status
```

## Status

Phased build: Phase 0 (scaffold + CI) and Phase 1 (Scryfall ingestion + image cache) are done.
`src/scryfall/` holds the policy-compliant client, bulk ingest, and image cache; the daily
refresh runs in-process via APScheduler (`src/scheduler.py`). Next: Phase 2 search engine.
