# Importing Collections

scryme imports collections exported as CSV from popular collection apps. Importing is a two-step
flow so you always see what will change before it happens.

## The flow

1. **Upload** — go to `/upload` (or the **+ Upload** button in the search header) and choose your
   CSV. scryme detects the format automatically.
2. **Preview** — scryme parses the file, matches each row to a card, and shows:
    - how many rows **matched** vs. were **unmatched** (unmatched cards are skipped),
    - a sample of any unmatched cards,
    - which owned cards **conflict** with the import.
3. **Confirm** — pick a [merge strategy](merge.md) and apply. You're redirected to search.

## How cards are matched

Each row is resolved to a Scryfall printing using the first method that succeeds:

1. **Scryfall ID** — exact, when the export includes one (ManaBox, often Delver Lens).
2. **Set code + collector number** — used by Dragon Shield and as a fallback.
3. **Card name** — falls back to the most recent printing of that name.

Rows that match none of these are reported as **unmatched** and skipped. Malformed Scryfall IDs are
ignored gracefully and fall through to set/number/name matching.

See [Supported Formats](formats.md) for the exact columns each app exports, and
[Merge Strategies](merge.md) for how quantities are combined.
