# Decks

Decks are named lists of cards you compare against your collection to answer **"what am I
missing?"** — and to check **format legality**.

Open **Decks** from the header (or `/decks`).

## Creating a deck

Click **+ New deck**, give it a name, and paste a plain decklist:

```
4 Lightning Bolt
4 Monastery Swiftspear
20 Mountain

Sideboard
2 Smash to Smithereens
```

- One card per line: `4 Lightning Bolt` (or `4x Lightning Bolt`).
- A `Sideboard` line starts the sideboard; an `SB:` prefix marks a single sideboard line.
- A trailing printing hint like `(MH2) 122` is ignored.
- `#` / `//` lines are treated as comments.

Each line is resolved to a card — preferring a printing you **own** (so the image and price match
your copy), then a **tournament-legal** printing, then the most recent. That ordering keeps a line
from silently landing on an art-series or oversized variant. Lines that don't match a known card
are kept and flagged as **unrecognized**. You can change the printing per card afterward — see
[Printings, language & proxies](#printings-language-proxies).

### Do you own these cards?

Both the paste and URL import forms ask whether you own the deck:

- **Not owned** (default) — just create the deck; your collection is untouched.
- **Fully owned** — every matched card is added to your collection at the deck's quantity, so
  coverage reads complete right away.
- **Partially owned** — you get a checklist of the deck's cards (all ticked by default); untick the
  ones you don't have, and only the ticked cards are added to your collection.

Unrecognized lines can't be added (there's no card to add), and adding is disabled on the read-only
demo.

### Import a whole profile

Got a lot of decks on Moxfield or Archidekt? Use **Import from a profile** on the New deck page
instead of pasting one link at a time:

1. Pick the site, then enter a **username** (or paste a profile URL like
   `moxfield.com/users/yourname` — the URL wins over the dropdown).
2. scryme lists that user's **public** decks — name, format, and card count.
3. Tick the ones you want (there's a select all / none), then **Import selected**.

Ownership applies to the whole batch — **Not owned** or **Fully owned**. (*Partially owned* is
per-deck, since it needs a per-card checklist, so it isn't offered for a bulk import.)

Private and unlisted decks are skipped, the list is capped at the first ~60 decks, and any deck that
can't be fetched is skipped rather than failing the whole batch.

## Ownership coverage

The deck page shows how complete the deck is against your collection:

- **% owned** and an owned / total card count.
- **Missing** cards (total and distinct) and an **estimated cost to complete** (USD).
- Per card: how many you own versus how many the deck needs. Ownership is matched by the card's
  oracle identity, so **any printing you own counts** — a deck calling for a new printing is
  satisfied by an old one you already have.

## Legality

Pick a format from the **Legality** dropdown (Standard, Pioneer, Modern, Legacy, Vintage,
Commander, Pauper, Brawl, Historic, Oathbreaker). scryme reports:

- **✓ Legal** in the chosen format, or
- the number of cards that are **not legal** (banned or not in the format), with each offending
  card badged, or
- **can't confirm** when the deck still has unrecognized lines.

`restricted` cards (e.g. in Vintage) count as legal.

## Commander bracket estimate

For **Commander** decks (any deck that runs a legendary creature that can be your commander), the
deck page shows an estimated **bracket** on WotC's 1–5 scale — **1 Exhibition · 2 Core · 3 Upgraded ·
4 Optimized · 5 cEDH** — so you can sanity-check "is my precon a 2 or a 3?".

It's a transparent **heuristic**, not an official rating: every signal that raised the estimate is
listed underneath, drawn from data scryme already has:

- **Game Changers** — cards on Scryfall's official Game Changers list. Any raises the deck to at
  least Upgraded (3); several push it to Optimized (4).
- **Mass land denial**, **extra-turn chaining** (two or more), and **known two-card infinite combos**
  each push the deck to at least Optimized (4).
- **Fast mana** (Mana Crypt, Mana Vault, Jeweled Lotus, rituals…) and **unconditional tutors** are
  density signals that nudge a baseline deck up to Upgraded (3). Sol Ring is legal at every bracket,
  so it doesn't count.

The estimate is capped at **4** — separating Optimized (4) from cEDH (5) reliably needs human
judgement, so a 4 is labelled "may be cEDH (5)". You can also search your collection for these cards
with **`is:gamechanger`**.

**Set it yourself.** If you disagree with the estimate, pick a bracket (1–5) from the **Set**
dropdown on the panel — including cEDH (5), which the estimate never assigns on its own. A manual
bracket is badged **manual** and still shows the computed estimate underneath for reference; choose
*— use estimate —* to clear it.

Legality is judged by the **card**, not the specific printing shown. Some printings — art-series
cards, tokens, gold-bordered World Championship / Collector's Edition cards, oversized and acorn
cards — are marked *not legal in every format* by Scryfall. scryme looks past those to a real
printing of the same card, so a legal staple is never flagged just because of the printing (or a
proxy) you're running.

## Printings, language & proxies

Each card line shows the printing it represents as **`SET·NUMBER·LANG`** (e.g. `CMM·942·EN`) —
the set code, collector number, and language. The printing follows what's in your collection when
you own the card, otherwise a tournament-legal one.

Click the **✎** on a line to change it (hidden on the read-only demo):

- **Printing** — pick any printing of that card. Non-standard variants are tagged *non-standard* in
  the list, but they're always available if that's what you run.
- **Language** — the language you play the card in, defaulting to **English**. Because scryme's card
  database is English-only per printing, the language is recorded on the deck line for reference
  (prices stay the English printing's).
- **Proxy** and **Special** — two independent markers. **Proxy** flags a printed proxy; **Special**
  flags a genuine but non-standard copy (art card, alter, misprint). Each shows its own badge on the
  card line and doesn't affect legality.

## Deck stats

Each deck page shows a quick profile:

- **Mana curve** — mainboard nonland spells bucketed by mana value (0–6 and 7+), so lands don't
  flatten the curve.
- **Colors** — the mainboard's color identity breakdown (also excluding lands).
- **Deck value** — the total USD value of every card in the deck at current Scryfall prices.

## Upgrade from your collection

Click **Suggest owned cards** on the deck page for deterministic, offline upgrade ideas drawn from
cards **you already own** — no AI endpoint required.

scryme buckets the deck's mainboard into roles (**ramp**, **card draw**, **removal**), spots the ones
that are below a typical Commander count, and offers owned cards that fill them — restricted to the
deck's **colour identity** and **Commander-legal**, and never a card already in the deck. Each
suggestion is ranked by mana-curve fit then price and shows a one-line reason; **+ Add** drops a copy
straight into the deck and refreshes the list. (Disabled on the read-only demo.)

For AI-powered suggestions and buy-to-upgrade plans, see the ✨ AI tools on the same page.

## Versions & diff

Tuning a deck over time? Save named **versions** and compare them to answer "what changed since last
week?".

- **Save version** snapshots the deck's current card list (with an optional label; otherwise `v1`,
  `v2`, …). Snapshots are immutable — later edits don't change them.
- Each saved version has a **Diff vs current** link, or use the **Compare A → B** picker to diff any
  two versions (or a version against the live deck).
- The diff is split by board and shows what's **added** (`+`), **removed** (`−`), and **changed in
  quantity** (`Δ from → to`), plus an unchanged count — a simple line diff keyed by card name.

Saving and deleting versions is disabled on the read-only demo; deleting a deck removes its versions.

## Export

Use the **Export** buttons on the deck page to download the list:

- **Plain text** — `4 Lightning Bolt` lines with a `Sideboard` section. The most portable form.
- **Arena** — `4 Lightning Bolt (LEA) 161` annotated with set + collector number, for MTG Arena's
  import box.
- **Moxfield** — the same annotated lines with a `SIDEBOARD:` marker; pastes into Moxfield and
  Archidekt.
- **MTGO (.dek)** — an XML `.dek` file for Magic Online.

Cards scryme couldn't resolve are exported by name only (no set/number).

