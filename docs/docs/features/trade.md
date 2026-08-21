# Trading

The **Trade** tab of *My Collection* covers both halves of a trade night: the **surplus binder**,
which is everything you *could* trade, and **trades**, which are the specific cards you're actually
swapping with one person.

## Trades

A **trade** is a staged list with two sides — what you're giving and what you're getting — that
survives until you settle it or discard it. Start one from the Trade tab with a name and, if you
like, who you're trading with.

### Staging cards

- **From a card page** — the `🤝 trade…` picker next to a stack stages *that exact copy*. This is
  the one to use when it matters: your foil, Lightly Played, Japanese copy is not worth the same as
  a plain English one, and staging the stack carries all of that across.
- **From search** — tick cards in the results grid and use **Give** or **Get** in the bulk bar. A
  card you're giving is staged from the largest stack you own of it; a card you're getting is
  staged as a plain English copy, which you can correct afterwards.

Staging **changes nothing in your collection**. A trade is a proposal until you settle it.

### Reading a trade

Each side totals up, and the **difference** tells you which way the trade leans. Both sides are
valued on the [currency](theming.md#display-currency) and
[price source](../getting-started/configuration.md) that were in effect when the trade was opened,
and the page says so — a trade is between two people, and the totals shouldn't move depending on
who's looking at them or when.

Use the ✎ on any row to fix the quantity, finish, condition or language; setting the quantity to
**0** unstages that card.

### Settling up

**Settle this trade** is the only step that changes your collection. It shows a review first —
everything still outstanding, ticked — and you confirm what actually happened.

- **Untick anything that fell through.** Those cards stay staged; the rest are settled. A trade
  where you agreed on six cards and only four changed hands is a normal outcome, not an error.
- **Incoming cards get the same intake as a manual addition** — finish, condition and language come
  from the trade itself, and you can file them all into a binder or storage box on the way in.
- **Outgoing cards are matched by copy, not by row.** Settling removes the printing in the finish,
  condition and language that was staged, so trading away your plain copy never quietly consumes
  your foil.

Afterwards you get a line-by-line report: what moved, what moved partly, and what couldn't move at
all. Anything that didn't happen is still staged, and **Settle the rest** picks up where you left
off once the missing cards turn up.

Cards that have already moved can't be unstaged — the trade keeps an honest record of what really
happened rather than letting the history be edited away.

### When your collection moves underneath a trade

If you stage three copies of a card and then sell two, the row is flagged **short** and a warning
appears at the top. Settling anyway moves the one you still have and leaves the other two staged.
Nothing is silently dropped or quietly adjusted — a trade is a promise to another person, so scryme
shows you the discrepancy and lets you decide.

This is also why a staged card survives you editing or deleting the stack it came from: the trade
records the *card*, not a row in your database.

## Surplus binder

The surplus binder gathers what you have spare, so you know what to bring in the first place.

- **Surplus** — any printing you own more than **Keep** copies of. The *Keep* selector (0–4) sets
  how many you hold back; the binder lists the **spares** beyond that. Set it to `4` to see only
  extras beyond a playset, or `1` to surface every duplicate.
- **Flagged** — any card with a `for-trade` (or `for trade` / `trade`) [tag](cards.md#tags), shown
  regardless of quantity and marked with a ★.

Each row shows how many you own, how many are tradeable, the per-card price, and the line value, in
your chosen [currency](theming.md#display-currency). The header totals the whole binder, sorted
most-valuable first.

### Export

**.txt** gives a plain `Qty Name (SET) Number` list (paste into a trade thread); **.csv** is a
spreadsheet with quantities and prices. Both respect the current *Keep* threshold.
