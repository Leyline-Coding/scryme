# ADR 0002 — Cross-device trading: what has to be decided before it is built

- **Status:** Proposed — decisions open, recorded here so scryme and scanme design against one document
- **Deciders:** scryme maintainers, scanme maintainers
- **Related issues:** [#286](https://github.com/Leyline-Coding/scryme/issues/286) (QR matched trading),
  [#331](https://github.com/Leyline-Coding/scryme/issues/331) (trade pool selector),
  [#332](https://github.com/Leyline-Coding/scryme/issues/332) (in/out confirmation and reconciliation),
  [#207](https://github.com/Leyline-Coding/scryme/issues/207) (optimistic concurrency),
  [scanme#325](https://github.com/Leyline-Coding/scanme/issues/325) (mobile consumer),
  [#209](https://github.com/Leyline-Coding/scryme/issues/209) (cross-app dependency map)

## Context

The **Trade Function** milestone introduces something scryme has not done before: an operation
whose two halves live on **two different collections, on two different devices, owned by two
different people**. Everything scryme does today is single-collection and single-instance — even
the shared-household case in [ADR 0001](0001-single-collection-accounts.md) is *one* collection
edited by two people.

[#286](https://github.com/Leyline-Coding/scryme/issues/286) sketches the flow: user 1 shows a QR
code, user 2 scans it, a mutual trade list is established by searching each side's collection
against the other's wants, and on mutual confirmation cards are removed from and added to the
respective collections.

The sibling scanner app **[scanme](https://github.com/Leyline-Coding/scanme)** is the natural
second device — it already has a camera, QR handling (device pairing, and a nearby decklist
handoff), and a local collection. It has filed
[scanme#325](https://github.com/Leyline-Coding/scanme/issues/325) as the consumer.

### Why this ADR exists now, and what it deliberately does not do

Both sides are unbuilt. The temptation is to fix the QR payload and the trade endpoints first,
because that is the part two repos have to agree on. **That is the wrong order.** The wire format
is downstream of the trade-pool data model from
[#331](https://github.com/Leyline-Coding/scryme/issues/331)/[#332](https://github.com/Leyline-Coding/scryme/issues/332),
which does not exist yet. A protocol designed against a model that has not been written will
encode guesses, and both repos will then implement the guesses.

So this ADR **frames the decisions and records the constraints they must satisfy**. It does not
specify a payload. The concrete protocol belongs in a follow-up ADR once
[#331](https://github.com/Leyline-Coding/scryme/issues/331) has landed a trade pool worth
serializing.

## The decisions to make

### D1 — What the QR encodes

| Option | Shape | Trade-offs |
| --- | --- | --- |
| **A. Session pointer** | `{instance_url, session_id, token}` — the scanner fetches the offer from user 1's instance | Small, fixed-size code. **Requires user 2 to reach user 1's instance** — fine on shared Wi-Fi at an LGS, useless at a kitchen table on cellular, and it exposes an instance to a stranger's device |
| **B. Self-contained offer** | The pool itself, compressed, in the code | Works with **no connectivity between the two parties** and leaks no instance. Bounded by QR capacity (~2–3 KB realistically), so a large pool needs chunking or a compact card encoding |
| **C. Hybrid** | Self-contained offer, with an optional URL for pools too large to inline | Covers both, at the cost of two code paths and two failure modes |

The scenario that should drive this: **two people at a table, at least one on cellular, possibly
in a venue with hostile Wi-Fi.** That is the common case for trading, and it argues against A as
the only mechanism. Note that scanme's constitution requires it to keep working offline, which
makes a connectivity-dependent-only design a poor fit for the app most likely to implement it.

### D2 — Where the matching runs

"Search each side's collection against the other's wants" is the interesting half of
[#286](https://github.com/Leyline-Coding/scryme/issues/286).

- **Server-side** — one instance receives both pools and computes the match. Reuses scryme's
  search engine and pricing directly, so valuations are consistent. But it requires one party to
  send their collection/wants to *the other party's server*, which is a meaningful disclosure to
  make casually.
- **On-device** — each side computes the match locally against the offer it received. No
  collection data leaves either instance beyond what is deliberately offered. Requires the
  matching rules to be implemented in both apps identically — precisely the divergence risk that
  scanme's Principle V exists to control, and that this repo has already been bitten by (two card
  name resolvers that drifted apart until DFCs matched from a decklist but not from a CSV).

**Constraint either way:** whatever runs, the *valuation* must agree with what each side sees in
its own app — same `price_source`, same `currency` (see
[#231](https://github.com/Leyline-Coding/scryme/issues/231)/[#232](https://github.com/Leyline-Coding/scryme/issues/232)).
Two people looking at the same proposed trade and seeing different totals is the fastest way to
lose trust in the feature. Note this cuts against pure on-device matching when the two parties
have *different* price-source preferences: the trade needs one agreed basis, and it should be
stated in the UI rather than silently taken from whoever initiated.

### D3 — Committing against optimistic concurrency

[ADR 0001](0001-single-collection-accounts.md) commits scryme to optimistic concurrency control:
a version guard on mutable rows, HTTP `409` on mismatch rather than last-write-wins
([#207](https://github.com/Leyline-Coding/scryme/issues/207)).

A trade commit is the hardest case that model will face, because **both collections change at
once and the two commits are not in the same transaction** — they are not even on the same
machine. Open questions:

- If side A commits and side B's commit `409`s on a stale row, what state is the trade in? A
  half-applied trade is strictly worse than a failed one.
- [#332](https://github.com/Leyline-Coding/scryme/issues/332) already asks for **partial trades**
  ("a trade falls through on some cards mid-session") to be representable. So "atomic" cannot mean
  "all or nothing" at the *trade* level — but it must mean it at the *card* level, and the two
  sides must end up agreeing on which subset actually happened.
- Household sharing makes this concrete rather than theoretical: someone else may be editing the
  same stack on the same collection while a trade is being committed.

**Constraint:** the two sides must converge on the same outcome, and a failure must be
distinguishable from a success by both parties without asking each other. Neither side may be left
believing a card moved when it did not.

### D4 — Trust boundary

Every option above involves accepting structured data from **a stranger's device**. scryme has no
accounts and no identity ([ADR 0001](0001-single-collection-accounts.md)), so there is nothing to
authenticate a trading partner *as*. That is acceptable — the parties are standing in front of
each other, and the social protocol is the authentication — but it means:

- A scanned payload is **untrusted input** and must be validated at the boundary like any import.
- A trade offer must never be able to name a card that does not resolve, inject a price, or
  address a row in the receiving collection that the receiver did not select themselves.
- The receiving side decides what leaves its collection. Nothing in the payload should be capable
  of *instructing* a removal — it can only *propose* one.

## Decision

1. **Do not specify the QR payload or the trade endpoints yet.** Build
   [#331](https://github.com/Leyline-Coding/scryme/issues/331) (pool selector) and
   [#332](https://github.com/Leyline-Coding/scryme/issues/332) (in/out reconciliation) **first**,
   as a single-collection, single-device feature. A trade pool that works locally is useful on its
   own, and it produces the data model the protocol has to carry.
2. **Treat D1–D4 as binding constraints on that work**, not as later concerns. Specifically, the
   pool model should be serializable to something compact enough for D1 option B to stay open, and
   the commit path in [#332](https://github.com/Leyline-Coding/scryme/issues/332) should be built
   so that "apply this set of in/out moves atomically at the card level, reporting exactly which
   ones applied" is a real operation — because that is what D3 will need.
3. **Write the protocol as ADR 0003, jointly**, once (1) has landed. Both repos implement against
   that document rather than against each other's code.
4. **Until then, no cross-repo commitment exists.** scanme's
   [#325](https://github.com/Leyline-Coding/scanme/issues/325) is a consumer placeholder and should
   not be implemented against a guessed format.

### Options considered

- **Specify the protocol now, build to it.** Rejected: it fixes a wire format against a data model
  that does not exist, and the cost of getting it wrong is paid in two repos.
- **Let scanme define the format, scryme conforms.** Rejected: the valuation basis and the
  concurrency semantics (D2, D3) are scryme's, and a format that does not respect them would have
  to be renegotiated anyway.
- **Ship trading as a scryme-only feature, no second device.** Reasonable and possibly the right
  first release — but it does not resolve anything here, since
  [#286](https://github.com/Leyline-Coding/scryme/issues/286) is explicitly the two-device flow.
  This ADR's decision (1) is effectively this, staged.

## Triggers to revisit

- [#331](https://github.com/Leyline-Coding/scryme/issues/331) and
  [#332](https://github.com/Leyline-Coding/scryme/issues/332) land → **write ADR 0003** with the
  concrete payload and endpoints.
- The trade pool model turns out **not** to be compactly serializable → D1 option B is off the
  table, and the connectivity assumptions have to be revisited before the protocol is written.
- scryme grows accounts or federation (a [ADR 0001](0001-single-collection-accounts.md) trigger) →
  D4's trust boundary changes materially and this ADR should be reconsidered whole.

## Consequences

- **The trade feature ships in two stages, and the first is genuinely useful on its own.** A local
  trade pool with in/out reconciliation stands alone; the cross-device handoff is additive.
- **The cross-repo dependency stays explicit and unresolved rather than implicitly assumed.**
  [#209](https://github.com/Leyline-Coding/scryme/issues/209) records it as a decision-pending item,
  so neither repo builds against a format the other has not agreed to.
- **[#332](https://github.com/Leyline-Coding/scryme/issues/332) inherits a requirement it would not
  otherwise have:** card-level atomic application with a precise report of what applied. That is
  slightly more than a purely local trade needs, and it is a deliberate cost paid now to avoid
  reworking the commit path later.
- **Valuation consistency becomes a cross-app contract**, not just a display preference. The
  price-source and currency preferences already exposed on `/api/v1/preferences` are load-bearing
  for trading, which raises the stakes on any future change to them.
