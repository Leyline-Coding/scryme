# Configuration

scryme is configured through environment variables. Compose-level variables (`POSTGRES_PASSWORD`,
`SCRYME_PORT`, `SCRYME_BIND`) are read by `docker-compose.yml`; the rest are read by the backend and
are prefixed with `SCRYME_`.

With the bundled Compose stack, put them in a `.env` file next to `docker-compose.yml` (or export
them in your shell) and run `docker compose up -d`. The compose file passes the variables on this
page marked *(compose)* through to the backend; for any other backend variable, add it to the
`backend` service's `environment:` block.

## Common variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `scryme` | PostgreSQL password. Set your own for a new install; it is only applied when the database volume is first created — see [changing it later](self-hosting.md#change-the-database-password) |
| `SCRYME_PORT` | `8080` | Host port for the web UI (nginx) |
| `SCRYME_BIND` | `0.0.0.0` | Host address the web UI port is published on. `127.0.0.1` accepts connections from the same machine only |
| `SCRYME_READ_ONLY` | `false` | Demo mode *(compose)* — refuses every write, disables the AI features, and shows a banner |
| `SCRYME_DEFAULT_CURRENCY` | `usd` | Default display currency for current values: `usd` / `eur` (native Scryfall prices) or `gbp` / `cad` / `aud` / `jpy` (converted from USD via a daily FX rate); visitors override in Settings |
| `SCRYME_BACKUP_DIR` | _(unset)_ | Folder for [on-disk backups](../features/backup.md#automatic-backups-to-a-folder-and-cross-device-sync); enables Back up now, scheduled backups, and restore from disk |
| `SCRYME_BACKUP_INTERVAL_HOURS` | `0` | Hours between scheduled backups to `SCRYME_BACKUP_DIR` (`0` disables scheduling) |
| `SCRYME_BACKUP_KEEP` | `14` | How many on-disk backups to retain (older ones are pruned) |
| `SCRYME_BACKUP_PASSPHRASE` | _(unset)_ | If set, scheduled/on-disk backups are [encrypted](../features/backup.md#encrypted-backups) with this passphrase (restore needs it) |
| `SCRYME_API_TOKEN` | _(unset)_ | Password for the whole instance *(compose)*. If set, the web UI asks for it once on a `/login` page and the [JSON API](../development/api.md) requires it (`Authorization: Bearer …` / `X-API-Key`). Unset = anyone who can reach the port has full access. [Device tokens](../development/api.md#device-tokens-recommended) issued under Settings → Devices also gate the API |

## Security and network variables

scryme has no user accounts. These settings decide who can reach an instance and how much load a
single client can put on it; see [Securing your install](self-hosting.md#securing-your-install)
for when to set them. All are optional and passed through by the bundled compose file *(compose)*.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_ALLOWED_HOSTS` | _(unset)_ | Comma-separated hostnames this instance answers to (e.g. `scryme.example.com`). Unset = any hostname. IP addresses and `localhost` are always accepted. Set it whenever the instance has a DNS name, to stop DNS-rebinding attacks; requests for any other name get `400` |
| `SCRYME_CLIENT_IP_HEADER` | _(unset)_ | Header carrying the real client address when a trusted proxy that is not the bundled nginx sits in front, e.g. `CF-Connecting-IP` behind Cloudflare. Used for rate limiting. Only set it when **every** request arrives through that proxy — otherwise clients can forge it |
| `SCRYME_RATE_LIMIT_DEFAULT` | `600` | Requests per minute allowed per client address |
| `SCRYME_RATE_LIMIT_HEAVY` | `30` | Requests per minute per client for expensive routes (AI calls, backups, exports, uploads, bulk imports) |
| `SCRYME_RATE_LIMIT_LOGIN` | `10` | Login attempts per minute per client |
| `SCRYME_STATEMENT_TIMEOUT_MS` | `30000` | Longest a single SQL statement may run inside a web request, in milliseconds. Lower it on a public instance so a pathological search can't tie up the database (the public demo uses `5000`). Background jobs such as ingest are not limited |
| `SCRYME_FORWARDED_ALLOW_IPS` | `127.0.0.1` (`*` in compose) | Which peers the backend believes when they send `X-Forwarded-For`: a comma-separated list of IPs/CIDRs, or `*`. Read by the container entrypoint. The compose default is `*` because there the backend port is not published and the only peer is the bundled nginx, which overwrites the header. If you run the image on its own behind a different proxy, set it to that proxy's address |

## AI assistant variables

Optional — power the [AI features](../features/ai.md) by pointing at any OpenAI-API-compatible
endpoint (local Ollama / LM Studio, or a hosted provider). All AI is off until a base URL is set,
and the in-app **Settings → AI** page overrides these.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCRYME_LLM_BASE_URL` | _(unset)_ | OpenAI-compatible API root (e.g. `http://localhost:11434/v1`); empty disables AI |
| `SCRYME_LLM_API_KEY` | _(unset)_ | API key (optional for local servers); stored encrypted when set in-app |
| `SCRYME_LLM_CHAT_MODEL` | `llama3.1` | Chat/completions model for analysis, chat, and suggestions |
| `SCRYME_LLM_EMBED_MODEL` | `nomic-embed-text` | Embeddings model for *Similar cards* + rules Q&A |

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
