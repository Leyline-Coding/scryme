# scryme

A localized, self-hostable implementation of [Scryfall](https://scryfall.com) for indexing and
searching **your own** Magic: The Gathering collection.

Import an export from **ManaBox**, **Dragon Shield**, **Delver Lens**, **Moxfield**, or
**Archidekt** (or any CSV, via a column-mapping wizard) and search it with a Scryfall-style
interface that understands [Scryfall search syntax](https://scryfall.com/docs/syntax) and
[regular expressions](https://scryfall.com/docs/regular-expressions).

**[Live demo](https://demo.scryme.app)** (read-only) · **[Documentation](https://docs.scryme.app)**

## Features

- 🔎 **Scryfall-compatible search** with regex, scoped to your collection or all cards — plus a
  grid/table toggle, clickable facets, "did you mean?" suggestions, saved searches, and an
  advanced form builder.
- 📥 **Import** from ManaBox, Dragon Shield, Delver Lens, Moxfield, and Archidekt — or any CSV via
  the column-mapping wizard — with **replace / increment / per-card** merge on re-import.
- 🃏 **Rich card pages** — oracle text, prices, legalities, printings, rulings, real mana & set
  symbols, tags (`tag:` search), and inline add/edit (plus bulk edit from results).
- 🧰 **Collection tools** — stats dashboard (value + growth over time), price history with
  acquisition profit/loss, decks (coverage, legality, stats, export), set completion, custom
  checklists, a wishlist, and a trade/surplus binder.
- 💱 **USD / EUR** price display and a themeable UI (preset themes + custom accent).
- 💾 **Backup & restore** — a portable JSON dump of your data, with scheduled on-disk backups to a
  folder you choose (point it at a synced folder for cross-device backup).
- 🖼️ **Local card database + image cache** — works offline and respects Scryfall's API policy.
- 🐳 **Self-hostable via Docker**, with an optional read-only public demo.

## Quick start (self-host)

```bash
docker compose up -d
# open http://localhost:8080
```

On first run the collection is empty, so the home page shows an upload prompt. After importing a
collection it becomes a Scryfall-style search bar.

After starting, ingest card data and (optionally) cache images:

```bash
docker compose exec backend python -m src.cli ingest          # ~550 MB Scryfall bulk file
docker compose exec backend python -m src.cli backfill-images # cache images for owned cards
```

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `scryme` | Database password |
| `SCRYME_PORT` | `8080` | Host port for the web UI |
| `SCRYME_READ_ONLY` | `false` | Demo mode — disables uploads/mutations and shows a banner |
| `SCRYME_DEFAULT_CURRENCY` | `usd` | Default display currency (`usd` or `eur`) |
| `SCRYME_BACKUP_DIR` | _(unset)_ | Folder for on-disk/scheduled backups (e.g. a synced folder) |

See the [configuration docs](https://docs.scryme.app/getting-started/configuration/) for the full list.

### Public demo

Ingest cards, seed a sample collection, then run read-only:

```bash
docker compose exec backend python -m src.cli ingest
docker compose exec backend python -m src.cli seed-demo --limit 60
SCRYME_READ_ONLY=true docker compose up -d
```

## Development

```bash
docker compose -f docker-compose.dev.yml up   # hot reload, app on http://localhost:8000
cd backend && pytest tests/                   # tests (needs Postgres via SCRYME_DATABASE_URL)
ruff check src tests                          # lint
```

Feature branches → PRs into `main`; GitHub Actions runs the tests + lint on every PR. See
[CLAUDE.md](CLAUDE.md) for architecture and conventions, or the
[documentation](https://docs.scryme.app) for the full guide.

## Tech stack

FastAPI · SQLAlchemy 2.0 (async) + asyncpg · PostgreSQL 16 · Alembic · Jinja2 + HTMX + Alpine.js +
Tailwind · APScheduler · Docker + nginx.

## Acknowledgements

Card data and images come from [Scryfall](https://scryfall.com). scryme is unofficial Fan
Content permitted under the Wizards of the Coast Fan Content Policy and is not affiliated with
or endorsed by Wizards of the Coast or Scryfall.

## License

See [LICENSE](LICENSE).
