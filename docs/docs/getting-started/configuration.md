# Configuration

scryme is configured through environment variables. Compose-level variables (`POSTGRES_PASSWORD`,
`SCRYME_PORT`) are read by `docker-compose.yml`; the rest are read by the backend and are prefixed
with `SCRYME_`.

## Common variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `scryme` | PostgreSQL password |
| `SCRYME_PORT` | `8080` | Host port for the web UI (nginx) |
| `SCRYME_READ_ONLY` | `false` | Demo mode — disables uploads/admin mutations and shows a banner |

## Database variables

Provide the connection as **discrete parts** (recommended) and scryme assembles the URL with the
password URL-encoded — so the password may safely contain `@`, `:`, `/`, and other special
characters. Alternatively, set `SCRYME_DATABASE_URL` directly to override the parts.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_DB_HOST` | `localhost` | Database host |
| `SCRYME_DB_PORT` | `5432` | Database port |
| `SCRYME_DB_USER` | `scryme` | Database user |
| `SCRYME_DB_PASSWORD` | `scryme` | Database password (any characters; encoded automatically) |
| `SCRYME_DB_NAME` | `scryme` | Database name |
| `SCRYME_DATABASE_URL` | *(assembled)* | Full async URL; overrides the parts above when set |

The bundled `docker-compose.yml` already wires these to the `postgres` service using
`POSTGRES_PASSWORD`.

## Other backend variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_DATA_DIR` | `/data` | Where bulk files and the image cache live |
| `SCRYME_IMAGE_CACHE_DIR` | `/data/images` | Cached card images directory (served at `/images`) |
| `SCRYME_BULK_REFRESH_MIN_HOURS` | `24` | Minimum hours between Scryfall bulk re-downloads |
| `SCRYME_ENVIRONMENT` | `development` | `development` / `production` / `test` |
| `SCRYME_DEBUG` | `false` | Verbose SQL logging |

## Scryfall API politeness

These rarely need changing — they keep scryme within
[Scryfall's API policy](https://scryfall.com/docs/api):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_SCRYFALL_USER_AGENT` | `scryme/<version> (+repo url)` | Identifies scryme to Scryfall (required header) |
| `SCRYME_SCRYFALL_MIN_REQUEST_INTERVAL` | `0.1` | Seconds between requests (≤ 10/s) |

!!! warning
    Always keep a descriptive `User-Agent` and stay under 10 requests per second. A `429` response
    locks API access for ~30 seconds; scryme backs off automatically.
