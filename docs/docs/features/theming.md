# Theming

scryme ships with a set of color **palettes**, each in a **light and dark** variation, plus a custom
accent color. Open the **settings** gear (top-right of any page) to change them — the same panel
holds the currency toggle and links to Backup and Status.

- **Mode** — a **🌙 Dark / ☀️ Light** switch that flips the current palette's brightness.
- **Palette** — nine schemes: **Tropic** (the default), Prussian, Pine, Walnut, Malachite, Lemon,
  Bubblegum, Midnight, and Slate. Each has a light and a dark variant, so mode and palette are
  chosen independently.
- **Custom accent** — pick any accent color, or choose one of the quick swatches; reset returns to
  the current palette/mode's default accent.

Your choice is stored in the browser (localStorage) and applied before the page paints, so there's
no flash of the default theme on load. Because it's client-side, theming also works on the
read-only public demo — each visitor gets their own preference.

## Display currency

The same settings panel has a **Currency** picker with six options: **USD** and **EUR** (Scryfall's
native prices) plus **GBP**, **CAD**, **AUD**, and **JPY**. It applies to *current values* wherever
they're shown: the [stats](stats.md) collection value and growth, [deck](decks.md) value and
cost-to-complete, the [wishlist](wishlist.md) estimate, and the card page's price list (the chosen
currency leads).

USD and EUR are a direct price-key choice — Scryfall carries both. The others have no Scryfall
price, so scryme converts the USD value using a foreign-exchange rate refreshed daily from the
European Central Bank (via [Frankfurter](https://frankfurter.dev)). A set of fallback rates ships
with the app so amounts render immediately, and `python -m src.cli refresh-fx` fetches the latest on
demand.

The default can be set per-deployment with `SCRYME_DEFAULT_CURRENCY` (e.g. `gbp`); each visitor's
override is remembered in a cookie. Collection **[price history](prices.md)** — the value chart,
profit/loss, and movers — stays in **USD**: it's built on stored USD snapshots and recorded purchase
prices, kept honest in their source currency rather than converted after the fact. The one exception
is a **card's own [price-history chart](prices.md#chart-currency)**, which has its own currency
dropdown and converts each past point at that date's historical exchange rate.

## Card effects

The settings panel also has a **Card effects** section:

- **Foil speed** — how fast the [holographic foil sheen](cards.md) animates on cards you own in
  foil (slower is more subtle).
- **Spin cards on hover** — a cursor-reactive 3-D tilt: card thumbnails/art lean toward the pointer
  based on where you hover over them, with its own **Spin speed** slider.

Both are saved in your browser and respect the OS *reduced motion* preference.
