# ADR 0001 — Single collection, no accounts (with a forward-compat hedge)

- **Status:** Accepted
- **Deciders:** scryme maintainers
- **Related issues:** [#205](https://github.com/Leyline-Coding/scryme/issues/205) (decision spike),
  [#202](https://github.com/Leyline-Coding/scryme/issues/202)–[#204](https://github.com/Leyline-Coding/scryme/issues/204)
  (settings + device tokens)

## Context

scryme's founding constraint is **single-user, no auth — one implicit collection per
deployment** (see the README, `CLAUDE.md`, and `src/config.py`). Two forces put pressure on that
constraint:

1. **Mobile-app integration.** Two sibling apps are planned (a scanner, per
   [#164](https://github.com/Leyline-Coding/scryme/issues/164)–[#168](https://github.com/Leyline-Coding/scryme/issues/168),
   and a browser app). They need to authenticate to an instance and sync against a collection.
2. **Settings sprawl.** Configuration lives in three unrelated places — environment variables
   (`config.py`), a single DB row (`LLMSettings`, `id == 1`), and client-side `localStorage`/cookies
   — with no clear line between "operator configures the server" and "user sets their preferences."

The instinctive fix — "add accounts" — is the heaviest possible answer, and it is paid for by the
99% case that scryme is explicitly built for: **one person, one collection**.

### The reframe

"Accounts" bundles three separable concerns. Only the third is actually about accounts:

| Concern | What it really needs | Requires accounts? |
| --- | --- | --- |
| Client/device authorization | A revocable per-app credential ([#204](https://github.com/Leyline-Coding/scryme/issues/204)) | **No** |
| Preferences grouping | A per-collection preferences store ([#203](https://github.com/Leyline-Coding/scryme/issues/203), [#202](https://github.com/Leyline-Coding/scryme/issues/202)) | **No** |
| Multi-tenant identity | Real human accounts + per-user collection isolation | **Yes** |

Nothing shipped or currently planned requires the third row. The mobile apps need *device
authorization*, not identity; the settings work needs a *preferences store*, not identity.

### The shared-collection case

A real, common scenario informed this decision: a household (e.g. a partner who does
organization/input and another who builds decks and manages trade/sell binders) shares **one**
physical collection. This is still **one collection** — it argues *against* per-user identity, not
for it. What it genuinely requires is that two people editing the same collection at the same time
cannot silently clobber each other's changes. That is a concurrency-safety problem, not an
identity problem (see Consequences).

## Decision

**Option A — stay single-collection, with a forward-compat hedge.**

- Keep one implicit collection per deployment. Do **not** build human accounts, login, sessions, or
  per-user data isolation.
- Serve mobile integration with revocable per-device tokens
  ([#204](https://github.com/Leyline-Coding/scryme/issues/204)).
- Serve settings/preferences with a per-collection preferences singleton
  ([#203](https://github.com/Leyline-Coding/scryme/issues/203)) surfaced in a unified settings page
  ([#202](https://github.com/Leyline-Coding/scryme/issues/202)).
- **Hedge:** add a nullable `owner_id` / `collection_id` column to collection-scoped and
  new settings/token tables now. It is **always `NULL`** today and unused by any query. Its only
  job is to make a future reversal to multi-collection an **additive migration** rather than a
  schema-wide rewrite.

### Options considered

**Option A — single-collection + hedge (chosen).**

- Pro: no auth/session/password-reset/tenant-isolation tax; matches the stated 1 person : 1
  collection reality; self-hosters need nothing more; mobile + settings both unblocked.
- Con: a hosted multi-user SaaS would require future work (bounded by the hedge).

**Option B — build multi-user accounts now (rejected).**

- Pro: enables a hosted SaaS / cloud sync / multiple humans with isolated collections on one box.
- Con: large surface area (auth, sessions, isolation, migrating every collection-scoped query),
  paid for by the overwhelmingly common single-user case, to serve demand that does not exist yet.

## Triggers to revisit (A → B)

Reopen this decision only when one of these becomes real. Until then, "should we do accounts?" is a
lookup against this list, not a fresh debate:

- A **hosted, multi-user SaaS** offering (many users' data on infrastructure the maintainers run).
- **Cloud sync** of many users' collections through a single deployment.
- Genuine **multiple humans with separate collections** on one self-hosted box — distinct from a
  household sharing a single collection, which Option A already serves.

## Consequences

- **The `owner_id` hedge is a standing convention.** New collection-scoped tables and the
  settings/token tables carry a nullable `owner_id`/`collection_id`, defaulting to `NULL`. Reviewers
  should expect it; leaving it off a new mutable table is the thing to catch.
- **Shared editing must be made safe.** Because one collection is deliberately shared by more than
  one person, scryme takes on responsibility for **concurrent-edit safety** — two simultaneous
  editors must not silently overwrite each other. The chosen approach is **optimistic concurrency
  control** (a version/`updated_at` guard on mutable rows → HTTP 409 + a "this changed since you
  loaded it" merge prompt, rather than last-write-wins) plus **live sync** so both people see
  changes as they happen. The live-sync stream generalizes the SSE work already proposed for
  scanning ([#166](https://github.com/Leyline-Coding/scryme/issues/166)) into a collection-events
  stream. Full Google-Docs-style character-level co-editing (OT/CRDT) is explicitly **out of
  scope**: collection edits act on discrete stacks (quantity, tags, location, condition), so
  row-level optimistic locking is sufficient. Tracked in
  [#207](https://github.com/Leyline-Coding/scryme/issues/207).
- **No user attribution by default.** Since there are no accounts, changes are not attributed to a
  named person. If "who changed this" becomes desirable for shared households, it can be layered on
  via the device-token label ([#204](https://github.com/Leyline-Coding/scryme/issues/204)) without
  introducing identity — a deliberately lighter step than accounts.
