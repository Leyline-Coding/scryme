# Self-Hosting

scryme runs as a small Docker Compose stack: a FastAPI backend, PostgreSQL, and an nginx reverse
proxy that also serves cached card images.

## Requirements

- Docker and Docker Compose
- ~2 GB of disk for the card database, plus more if you cache images (the Scryfall bulk file is
  ~550 MB compressed; full image caches can run to several GB)
- Any common CPU architecture — the published image is **multi-arch (linux/amd64 + linux/arm64)**,
  so it runs natively on x86 servers as well as a Raspberry Pi or Apple-Silicon machine.

## Start the stack

```bash
git clone https://github.com/Leyline-Coding/scryme.git
cd scryme
docker compose up -d
```

This builds the backend image, starts PostgreSQL, applies database migrations automatically, and
serves the app through nginx on port **8080** (override with `SCRYME_PORT`).

## Load card data

scryme searches a **local** copy of the Scryfall card database. Ingest it once after starting:

```bash
docker compose exec backend python -m src.cli ingest
```

This downloads Scryfall's **Default Cards** bulk file and streams it into PostgreSQL. A daily
in-process job keeps it fresh, honoring Scryfall's "cache for at least 24 hours" guidance, so you
never need to re-run this manually.

!!! tip "Cache images for offline use"
    Card images load from Scryfall's CDN by default. To cache the images for cards you own (so the
    app works fully offline), run:

    ```bash
    docker compose exec backend python -m src.cli backfill-images
    ```

## Status & monitoring

The **`/admin`** page is a status dashboard — card count, image cache size, last ingest, database
size, and how much you've got in your collection / decks / wishlist / checklists, plus whether
backups are configured.

For monitoring, **`/metrics`** exposes the same figures in **Prometheus** text format
(`scryme_cards_total`, `scryme_collection_value_usd`, `scryme_last_ingest_timestamp_seconds`, …),
so you can scrape it into Prometheus/Grafana. Both endpoints are read-only; if your instance is
public, put them behind your reverse proxy's auth.

## Upgrading

```bash
git pull
docker compose up -d --build
```

Migrations run automatically on container start.

## Development stack

For hot-reload local development:

```bash
docker compose -f docker-compose.dev.yml up   # app on http://localhost:8000
```

See [Configuration](configuration.md) for environment variables and the
[Architecture](../development/architecture.md) page for how the pieces fit together.
