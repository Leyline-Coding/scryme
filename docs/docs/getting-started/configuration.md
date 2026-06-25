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

## Backend variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_DATABASE_URL` | `postgresql+asyncpg://scryme:scryme@postgres:5432/scryme` | Async database URL |
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
