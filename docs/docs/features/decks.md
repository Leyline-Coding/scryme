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
your copy), otherwise the most recent printing. Lines that don't match a known card are kept and
flagged as **unrecognized**.

## Ownership coverage

The deck page shows how complete the deck is against your collection:

- **% owned** and an owned / total card count.
- **Missing** cards (total and distinct) and an **estimated cost to complete** (USD).
- Per card: how many you own versus how many the deck needs. Ownership is matched by the card's
  oracle identity, so **any printing you own counts** — a deck calling for a new printing is
  satisfied by an old one you already have.

## Legality

Pick a format from the **Legality** dropdown (Standard, Pioneer, Modern, Legacy, Vintage,
Commander, Pauper, Brawl, Historic, Oathbreaker). scryme reads each card's legalities and reports:

- **✓ Legal** in the chosen format, or
- the number of cards that are **not legal** (banned or not in the format), with each offending
  card badged, or
- **can't confirm** when the deck still has unrecognized lines.

`restricted` cards (e.g. in Vintage) count as legal.

## Deck stats

Each deck page shows a quick profile:

- **Mana curve** — mainboard nonland spells bucketed by mana value (0–6 and 7+), so lands don't
  flatten the curve.
- **Colors** — the mainboard's color identity breakdown (also excluding lands).
- **Deck value** — the total USD value of every card in the deck at current Scryfall prices.

## Export

Use the **Export** buttons on the deck page to download the list:

- **Plain text** — `4 Lightning Bolt` lines with a `Sideboard` section. The most portable form.
- **Arena** — `4 Lightning Bolt (LEA) 161` annotated with set + collector number, for MTG Arena's
  import box.
- **Moxfield** — the same annotated lines with a `SIDEBOARD:` marker; pastes into Moxfield and
  Archidekt.
- **MTGO (.dek)** — an XML `.dek` file for Magic Online.

Cards scryme couldn't resolve are exported by name only (no set/number).

