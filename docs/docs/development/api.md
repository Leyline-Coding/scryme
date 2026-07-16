# JSON API

scryme exposes a small, versioned **JSON API** under `/api/v1` — the same services the web UI uses,
so you can script it, build a mobile client, or drive it from another app. It's
[OpenAPI](https://swagger.io/specification/)-documented: browse and try it at **`/docs`** (Swagger
UI), or fetch the schema from **`/openapi.json`**.

## Authentication

By default the API is **open** — fine for a single-user instance on your own machine or LAN. If your
instance is reachable by others, set **`SCRYME_API_TOKEN`** and every `/api/*` request must then
include it:

```bash
curl -H "Authorization: Bearer $TOKEN" https://your-host/api/v1/stats
# or:  -H "X-API-Key: $TOKEN"
```

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
