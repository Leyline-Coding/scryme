# Public Demo

You can run a shared, read-only instance — useful for showing scryme off without letting visitors
modify the collection.

## Steps

1. Start the stack and ingest card data:

    ```bash
    docker compose up -d
    docker compose exec backend python -m src.cli ingest
    ```

2. Seed a sample collection. This marks a number of already-ingested cards as owned so the demo
   shows a populated, searchable collection:

    ```bash
    docker compose exec backend python -m src.cli seed-demo --limit 60
    ```

3. Restart in read-only mode:

    ```bash
    SCRYME_READ_ONLY=true docker compose up -d
    ```

The official public demo runs this way at **[demo.scryme.app](https://demo.scryme.app)**. To host
your own at a custom domain, point the hostname at your server and terminate TLS at a reverse proxy
in front of nginx (port `8080`).

## What read-only mode does

- Shows a banner indicating the instance is a shared, read-only sandbox.
- Returns `403` from `POST /upload`, `POST /upload/confirm`, and `POST /admin/ingest`.
- Disables the scheduled bulk refresh.

Searching, browsing, and the collection/all scope toggle all continue to work.
