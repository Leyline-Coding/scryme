# Self-Hosting

scryme runs as a small Docker Compose stack: a FastAPI backend, PostgreSQL, and an nginx reverse
proxy that also serves cached card images.

## Requirements

- Docker and Docker Compose
- ~2 GB of disk for the card database, plus more if you cache images (the Scryfall bulk file is
  ~550 MB compressed; full image caches can run to several GB)

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
