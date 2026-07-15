# Theming

scryme ships with several themes and a custom accent color. Open the **settings** gear
(top-right of any page) to change them — the same panel holds the currency toggle and links to
Backup and Status.

- **Preset themes** — Midnight (default dark), Slate, Daylight (light), and Parchment (a warm,
  MTG-flavored light theme).
- **Custom accent** — pick any accent color, or choose one of the quick swatches; reset returns to
  the theme's default.

Your choice is stored in the browser (localStorage) and applied before the page paints, so there's
no flash of the default theme on load. Because it's client-side, theming also works on the
read-only public demo — each visitor gets their own preference.

## Display currency

The same settings panel has a **Currency** toggle — **USD** or **EUR**. scryme shows the matching
Scryfall price (`usd` / `eur`) for *current values*: the [stats](stats.md) collection value and
growth, [deck](decks.md) value and cost-to-complete, the [wishlist](wishlist.md) estimate, and the
card page's price list (the chosen currency leads). It's a price-key choice, **not** a converted
rate.

The default can be set per-deployment with `SCRYME_DEFAULT_CURRENCY=eur`; each visitor's override is
remembered in a cookie. **[Price history](prices.md)** (snapshots, profit/loss, and movers) stays in
USD — it's built on stored USD snapshots and recorded purchase prices, which can't be converted
without an exchange rate.

## Card effects

The settings panel also has a **Card effects** section:

- **Foil speed** — how fast the [holographic foil sheen](cards.md) animates on cards you own in
  foil (slower is more subtle).
- **Spin cards on hover** — a small 3-D partial turn on card thumbnails/art when you hover them,
  with its own **Spin speed** slider.

Both are saved in your browser and respect the OS *reduced motion* preference.
