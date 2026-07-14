# Set completion

The **Sets** page (header link or `/sets`) tracks how much of each set you've collected.

- **Progress per set** — for every set you own at least one card from, it shows the distinct
  printings you own versus the total scryme knows for that set, as a fraction, a percentage, and a
  progress bar (e.g. *March of the Machine — 198/387 (51%)*).
- **Most complete first** — sets you're closest to finishing rise to the top, so the giant
  catch-all sets you'll only ever own a sliver of (Secret Lair Drop, The List) sink to the bottom.
- **Drill in** — click a set to see exactly which printings you're **missing**, ordered by
  collector number, each linking to its [card page](cards.md). A **View owned →** link jumps to a
  search of what you already have from that set.

## What counts

- The **total** for a set is every paper printing scryme has ingested for it (Arena/MTGO-only
  cards are [excluded](../import/overview.md)). Alternate-art and showcase printings have their own
  collector numbers, so they count as separate cards — exactly as Scryfall lists them.
- A set's **owned** count is the number of distinct printings you have at least one copy of.
  Owning multiple copies, or a foil, still counts the printing once.
- The page is built from the cards you own, so it's empty until you
  [import a collection](../import/overview.md).

## Set release calendar

The **[release calendar](../features/sets.md)** at **`/calendar`** (linked from the Sets view) shows
**upcoming** sets — with a countdown to each release — and **recently released** ones. The list
syncs from Scryfall's set catalog (cached for 24 hours, so it works offline after the first view),
and covers all paper sets regardless of whether you own any of their cards yet.

Refresh it manually with `python -m src.cli refresh-sets`.
