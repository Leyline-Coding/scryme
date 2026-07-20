# Price history

The **Prices** page (header link or `/prices`) tracks your collection's value over time, shows
which cards moved the most, and reports your acquisition profit/loss.

- **Acquisition P/L** — for cards with a recorded purchase price, compares what you paid to the
  current market value: total **cost basis**, current **market value**, and the **unrealized
  profit/loss** (dollars and percentage), plus your biggest winners and losers.
- **Collection value over time** — a bar per snapshot, with the current value highlighted.
- **Biggest movers** — the top gainers and losers between the two most recent snapshots, each with
  the old → new price, the dollar change, and the percentage, linking to the card page.

## Acquisition profit/loss

When you import a collection that includes a purchase price (ManaBox's *Purchase price*, Dragon
Shield's *Price Bought*, or Delver Lens's price column), scryme stores it per card. The Prices page
then compares that cost against the current Scryfall market price:

- **Foil-aware** — foil/etched stacks are compared against the foil market price.
- Only cards that have **both** a recorded purchase price **and** a current market price count
  toward the totals; everything else is reported as excluded so the numbers stay honest.
- **Winners/losers** are ranked by total dollar change across the whole stack (quantity × per-card
  change), so a small per-card gain on a big stack can outrank a large gain on a single card.

If none of your cards carry a purchase price, the P/L section is hidden.

## How snapshots are captured

scryme records a price snapshot automatically **after each scheduled Scryfall refresh** (when
prices are freshly updated). The total is foil-aware — foil stacks are valued at the foil price.

You can also capture one manually:

```bash
python -m src.cli snapshot-prices
```

Two snapshots are needed before movers appear, so the page fills in over time. On the read-only
public demo the scheduled refresh is disabled, so snapshots don't accumulate there.

## Per-card price history

Every [card page](cards.md) shows a **price-history chart** for that printing, with the same
time-range selector as the collection value chart. To keep the database bounded, each snapshot
records a per-card point only for cards you **own** or **track** — anything on your
[wishlist](wishlist.md) or price watchlist — rather than all ~90k printings. A card with no recorded
points yet shows a short "no history yet" note; add it to your collection or wishlist and it starts
building history from the next snapshot.

### Chart currency

The chart's header has a **currency dropdown** (USD, EUR, GBP, CAD, AUD, JPY). Prices are recorded
in USD, so to chart past values in another currency accurately scryme converts each snapshot at the
exchange rate that applied **on its own date** — not one flat current rate. The first time you pick
a non-USD currency it confirms a one-time **download of historical exchange rates** (from
[Frankfurter](https://frankfurter.dev), ECB reference rates); those are cached, so switching is
instant afterwards. Your choice is remembered per browser. If the rates can't be downloaded
(offline), the chart falls back to the current rate and labels itself *approximate*.

This is separate from the site-wide currency picker in Settings, which controls *current* prices
everywhere else; the collection value chart on the [stats](stats.md) page stays in USD.

To pre-download the rates (e.g. for an offline/self-host deploy):

```bash
python -m src.cli backfill-fx-history            # all convertible currencies
python -m src.cli backfill-fx-history --code gbp # just one
```
