# JSON API

scryme exposes a small, versioned **JSON API** under `/api/v1` — the same services the web UI uses,
so you can script it, build a mobile client, or drive it from another app. It's
[OpenAPI](https://swagger.io/specification/)-documented: browse and try it at **`/docs`** (Swagger
UI), or fetch the schema from **`/openapi.json`**.

## Authentication

By default the API is **open** — fine for a single-user instance on your own machine or LAN. There
are two ways to close it, and they work together.

Either way, the credential goes in one of two headers:

```bash
curl -H "Authorization: Bearer $TOKEN" https://your-host/api/v1/stats
# or:  -H "X-API-Key: $TOKEN"
```

### Device tokens (recommended)

**Settings → Devices** issues a labelled token per app — one for the scanner, one for a script, one
for whatever else talks to your instance. Each can be revoked on its own, and the page shows when
each was last used, so you can spot the ones nothing is using any more.

Choose **read only** or **read and write** when you create it. Read-only tokens can call any `GET`
but are refused (`401`) on anything that changes data, which is what you want for something that
only displays your collection.

Two things worth knowing:

- **The token is shown once.** Only a hash is stored, so it can't be recovered — if you lose it,
  revoke it and issue another.
- **Issuing your first token closes the API**, and revoking every token does *not* re-open it.
  Revocation is what you reach for when something has gone wrong; it would be a poor design if that
  were also the thing that removed the lock. Issue a new token from the same page to get back in.
  (The web UI is never token-gated, so you can't lock yourself out of scryme itself.)

### A single shared token

**`SCRYME_API_TOKEN`** still works and is unchanged: set it and every `/api/*` request must present
it. It predates scopes and always grants full access. It's the simplest option if you just want one
secret in an environment file; device tokens are better as soon as more than one thing is calling
your instance.

Mutating endpoints additionally respect `SCRYME_READ_ONLY` (they return `403` on the demo).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/search` | Search (`q`, `scope`, `page`, `sort`, `dir`) — full [Scryfall syntax](../search/syntax.md). Returns cards with your owned quantity + tags. |
| `GET` | `/api/v1/cards/{id}` | One printing: details, your owned stacks, and tags. |
| `GET` | `/api/v1/stats` | Collection stats (`currency=usd|eur|gbp|cad|aud|jpy`). |
| `GET` | `/api/v1/decks` · `/api/v1/decks/{id}` | Decks list / one deck's coverage. |
| `GET` | `/api/v1/wishlist` | Wishlist with estimated cost. |
| `POST` | `/api/v1/collection` | Add/increment an owned stack (`scryfall_id`, `quantity`, `finish`, …). |
| `POST` / `DELETE` | `/api/v1/cards/{id}/tags` | Add / remove a tag. |
| `POST` | `/api/v1/wishlist` · `DELETE` `/api/v1/wishlist/{id}` | Add / remove a wishlist entry. |

## Example

```bash
# Search your collection for red instants under 3 mana
curl "http://localhost:8080/api/v1/search?q=c:r+t:instant+mv<=2"

# Add four foil copies of a printing
curl -X POST http://localhost:8080/api/v1/collection \
  -H 'content-type: application/json' \
  -d '{"scryfall_id":"...","quantity":4,"finish":"foil"}'
```
