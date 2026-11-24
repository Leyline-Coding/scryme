# Self-Hosting

scryme runs as a small Docker Compose stack: a FastAPI backend, PostgreSQL, and an nginx reverse
proxy that also serves cached card images.

## Requirements

- Docker and Docker Compose
- ~2 GB of disk for the card database, plus more if you cache images (the Scryfall bulk file is
  ~550 MB compressed; full image caches can run to several GB)
- Any common CPU architecture — the published image is **multi-arch (linux/amd64 + linux/arm64)**,
  so it runs natively on x86 servers as well as a Raspberry Pi or Apple-Silicon machine. `docker
  pull ghcr.io/leyline-coding/scryme:latest` auto-selects your architecture; explicit
  **`:latest-amd64`** / **`:latest-arm64`** (and `:vX.Y.Z-amd64` / `-arm64`) tags are published too
  if you need a specific one.

## Start the stack

### Self-contained install

```bash
git clone https://github.com/Leyline-Coding/scryme.git
cd scryme
docker compose up -d
```

This builds the backend image, starts PostgreSQL, applies database migrations automatically, and
serves the app through nginx on port **8080** (override with `SCRYME_PORT`).

!!! warning "Open by default"
    Out of the box the app has no login and listens on every network interface, so anyone who
    can reach port 8080 can view and change your collection. That is fine on a machine only you
    can reach; otherwise read [Securing your install](#securing-your-install) before you start it.


### Deploy from image + BYODB

```bash
services:
  scryme:
    image: ghcr.io/leyline-coding/scryme:latest
    container_name: scryme
    restart: unless-stopped
    ports:
      - '8000:8000'
    environment:
      SCRYME_DB_HOST: ...
      SCRYME_DB_PORT: "5432"
      SCRYME_DB_USER: ...
      SCRYME_DB_PASSWORD: ...
      SCRYME_DB_NAME: "scryme"

      SCRYME_ENVIRONMENT: production
      SCRYME_DATA_DIR: /data
      SCRYME_IMAGE_CACHE_DIR: /data/images

    volumes:
      - '/path/to/persistent/storage:/data'
```

In this example, we skip the build step and simply pull and then use the latest pre-built image. This configuration also deomnstrates a customized scryme setup running with external database and external data store.

## Securing your install

scryme is a single-user app with no accounts. Settings go in a `.env` file next to
`docker-compose.yml`; apply changes with `docker compose up -d`. The variables are described in
[Configuration](configuration.md#security-and-network-variables).

```bash title=".env"
POSTGRES_PASSWORD=<a long random string>   # new installs only - see below
SCRYME_API_TOKEN=<another long random string>
SCRYME_ALLOWED_HOSTS=scryme.example.com
# SCRYME_BIND=127.0.0.1
```

- **New install: set `POSTGRES_PASSWORD` before the first `docker compose up`.** The default
  (`scryme`) is public knowledge. The database is not published outside the Compose network, so
  this is a second line of defence, but it costs nothing on day one. Generate one with
  `openssl rand -hex 24`.
- **If anyone else can reach the port, set `SCRYME_API_TOKEN`.** That covers a shared home
  network, a VPS, a tunnel or a port-forward. The web UI then asks for the token once on a login
  page and the API requires it. Without it, whoever can connect has full control.
- **If only this machine should reach it, set `SCRYME_BIND=127.0.0.1`.** The port is then
  published on localhost only — the right choice when a reverse proxy on the same host fronts
  scryme, or when you only use it from that computer.
- **If you use a domain name, set `SCRYME_ALLOWED_HOSTS`** to that name (comma-separate several).
  The app then refuses requests addressed to any other hostname, which stops DNS-rebinding
  attacks. Leave it unset if you only ever use an IP address or `localhost`.
- **Use HTTPS when traffic leaves your own network.** scryme serves plain HTTP; put a
  TLS-terminating reverse proxy (Caddy, Traefik, nginx, a Cloudflare Tunnel) in front.

!!! note "Behind another reverse proxy"
    The bundled nginx treats whoever connects to it as the client, and tells the backend so.
    With a second proxy in front, every visitor would appear to come from that proxy and share one
    rate limit. The comment block in `nginx/nginx.conf` shows the three lines to add
    (`set_real_ip_from` …) so the real address is passed along. If everything arrives through
    Cloudflare, setting `SCRYME_CLIENT_IP_HEADER=CF-Connecting-IP` does the same job.

### Change the database password

PostgreSQL only reads `POSTGRES_PASSWORD` when it creates a **new** data volume. On an existing
install, editing `.env` alone changes what the app sends but not what the database expects, and the
backend then fails to start with `password authentication failed`. Change both, in this order:

```bash
# 1. Change the password inside the running database (prompts twice; nothing lands in shell history)
docker compose exec postgres psql -U scryme -d scryme -c '\password scryme'

# 2. Put the same value in .env
#      POSTGRES_PASSWORD=<the new password>

# 3. Recreate the containers so the backend picks it up
docker compose up -d
```

Your data is untouched. If the backend is already failing after an edit to `.env`, either restore
the old value or run step 1 with the new one — the `psql` command works regardless, because it
connects from inside the database container.

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

Migrations run automatically on container start. The compose file pins PostgreSQL to a 16.x
release — do not change it to a newer major version on an existing install; the data volume would
need a dump and restore first.

!!! note "If you kept settings in `backend/.env`"
    The backend image no longer includes `backend/.env` (it used to be copied in at build time,
    which is how secrets end up inside images). Move any settings from that file to the `.env`
    next to `docker-compose.yml`, and for variables the compose file does not already pass through,
    add them to the `backend` service's `environment:` block.

## Development stack

For hot-reload local development:

```bash
docker compose -f docker-compose.dev.yml up   # app on http://localhost:8000
```

The development stack publishes the app and PostgreSQL on `127.0.0.1` only: it runs with debug
enabled, no login, and a fixed database password, so it must not be reachable from the network.

See [Configuration](configuration.md) for environment variables and the
[Architecture](../development/architecture.md) page for how the pieces fit together.
