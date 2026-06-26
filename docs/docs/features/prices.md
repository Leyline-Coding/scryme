# Price history

The **Prices** page (header link or `/prices`) tracks your collection's value over time and shows
which cards moved the most.

- **Collection value over time** — a bar per snapshot, with the current value highlighted.
- **Biggest movers** — the top gainers and losers between the two most recent snapshots, each with
  the old → new price, the dollar change, and the percentage, linking to the card page.

## How snapshots are captured

scryme records a price snapshot automatically **after each scheduled Scryfall refresh** (when
prices are freshly updated). The total is foil-aware — foil stacks are valued at the foil price.

You can also capture one manually:

```bash
python -m src.cli snapshot-prices
```

Two snapshots are needed before movers appear, so the page fills in over time. On the read-only
public demo the scheduled refresh is disabled, so snapshots don't accumulate there.
