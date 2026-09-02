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

- **The token is shown once.** Only a hash of it is stored, keyed to a secret kept in your data
  directory (`tokens.key`) rather than in the database — so a database dump or backup carries
  hashes computed under a key it doesn't contain. The token can't be recovered; if you lose it,
  revoke it and issue another. (Losing `tokens.key` invalidates every issued token, which is the
  safe direction to fail in — they stop working rather than becoming guessable.)
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

### Concurrent edits

Collection rows carry a `version`. Send it back on `PATCH /api/v1/collection/{id}` (in the body) or
`DELETE /api/v1/collection/{id}?version=` and the write is refused with **`409`** if the row changed
since you read it, instead of overwriting whatever changed:

```json
{"detail": {"error": "stale", "message": "This stack changed since it was loaded.",
            "current_version": 7}}
```

The current version comes back with the error so you can retry deliberately rather than blindly.
Omitting `version` applies the write regardless, so existing clients are unaffected.

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
| `POST` | `/api/v1/scan` | [Batch scan ingest](#batch-scan-ingest) — increment-merge a batch of scanned cards. |

## Batch scan ingest

`POST /api/v1/scan` adds a batch of scanned cards straight to your collection, skipping the
scan → export CSV → upload loop. It's what the companion scanner app talks to, but it's an ordinary
endpoint — anything that can identify a printing can use it.

Identify each row by `scryfall_id`, or by `set` + `collector_number`, or (least precisely) by
`name`. Rows are resolved by the same matcher a CSV import uses, and anything that doesn't resolve
comes back in the response instead of failing the batch.

```bash
curl -X POST http://localhost:8080/api/v1/scan \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: box-a-2026-09-02-001' \
  -d '{
        "location": "Box A",
        "rows": [
          {"scryfall_id": "e3285e6b-3e79-4d7c-bf96-d920f973b0d5", "quantity": 2},
          {"set": "mh2", "collector_number": "122", "finish": "foil"},
          {"name": "Llanowar Elves"}
        ]
      }'
```

The response reports each row in the order you sent it — what it resolved to, and how — plus the
totals:

```json
{
  "ok": true, "replayed": false, "idempotency_key": "box-a-2026-09-02-001",
  "total_rows": 3, "matched": 3, "unmatched": 0,
  "inserted": 3, "updated": 0, "total_quantity": 4, "location": "Box A",
  "rows": [
    {"index": 0, "matched": true, "method": "scryfall_id", "quantity": 2, "name": "…"},
    {"index": 1, "matched": true, "method": "set_number", "quantity": 1, "name": "Lightning Bolt"},
    {"index": 2, "matched": true, "method": "name", "quantity": 1, "name": "Llanowar Elves"}
  ]
}
```

Cards are always **added** to what you already own — there's no replace mode, because a scan is
unambiguously an addition. A `location` applies to the whole batch and keeps those copies as their
own stack, so filing a box doesn't silently move cards that were never in it.

### Send an `Idempotency-Key`

A scanner runs on a phone, on a home network, with an offline queue — so the same batch will
eventually be sent twice. Give each batch a key and the second arrival returns the first one's
response with `"replayed": true`, without adding the cards again. A retry is then safe to do
blindly, which is the only way to make "did that land?" answerable after a timeout.

Keep the key stable across retries of *the same* batch and different between batches. Without a
key, every request is applied as sent.

### Pairing a device

Rather than typing an address and a 43-character token into a phone, open **`/pair`** (or
**Settings → Devices → Show pairing code**). It mints a fresh per-device token and shows a QR
carrying exactly this:

```json
{"base_url": "http://192.168.1.5:8080", "token": "scryme_…"}
```

The token is an ordinary device token, revocable on its own from Settings → Devices. If you're
looking at the page over `localhost` — which you usually are, pairing from the machine running
scryme — the code uses your machine's network address instead, since a phone can't reach
`localhost`. On the desktop app the other device also needs
[LAN sharing](../getting-started/desktop.md) turned on; the pairing page says so when it's off.

## Example

```bash
# Search your collection for red instants under 3 mana
curl "http://localhost:8080/api/v1/search?q=c:r+t:instant+mv<=2"

# Add four foil copies of a printing
curl -X POST http://localhost:8080/api/v1/collection \
  -H 'content-type: application/json' \
  -d '{"scryfall_id":"...","quantity":4,"finish":"foil"}'
```
