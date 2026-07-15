# Search Syntax

scryme implements a faithful subset of [Scryfall's search syntax](https://scryfall.com/docs/syntax).
Queries combine **filters** with boolean logic, and by default search **your collection**.

!!! tip "Don't know the syntax yet?"
    The **[Advanced search](advanced.md)** page (linked as *Advanced* next to the search bar) is a
    form-based builder: fill in names, colors, mana value, rarity, format, price, and more, and it
    assembles the query for you — then shows the generated query string so you can learn by example.

!!! tip "Did you mean?"
    If a plain-name search returns nothing, scryme suggests the closest card names (fuzzy matched)
    — click a suggestion to re-run the search with it. Suggestions respect your scope, so in
    *My collection* they only point at cards you own.

!!! tip "Grid or list"
    The toggle above the results switches between the **card grid** and a compact **table view**
    (name, set, collector number, rarity, quantity owned, and price). Your choice is remembered.

!!! tip "Filter by clicking"
    On wider screens a **facets** sidebar appears next to the results with the colors, rarities,
    types, sets, **years**, **formats** (Legal in…), and **foil** availability present in the
    current results (and how many of each). Click one to toggle that filter — scryme just adds or
    removes the matching token (`c:`, `r:`, `t:`, `s:`, `year:`, `f:`, `is:foil`) on your query, so
    the search bar stays the source of truth and you can keep editing it by hand.

## Scope

Use the dropdown next to the search bar (or the `scope` query parameter):

- **My collection** *(default)* — only cards you own.
- **All cards** — the entire local card database.

## Booleans and grouping

| Syntax | Meaning |
| --- | --- |
| `a b` | both `a` **and** `b` (implicit AND) |
| `a AND b` | both (explicit) |
| `a OR b` | either |
| `-a` | **not** `a` |
| `(a OR b) c` | grouping with parentheses |
| `"exact phrase"` | a quoted phrase (keeps spaces) |

## Filters

Most filters accept the `:` operator (a sensible default per field). Numeric and ordered fields
also accept `=`, `!=`, `<`, `<=`, `>`, `>=`. **For numeric fields, `:` behaves like `=`.**

### Text

| Filter | Aliases | Example | Matches |
| --- | --- | --- | --- |
| name | *(bare words)* | `goblin`, `name:"Black Lotus"` | card name contains |
| `o:` | `oracle:` | `o:"draw a card"` | oracle text contains |
| `t:` | `type:` | `t:legendary t:creature` | type line contains |
| `kw:` | `keyword:` | `kw:flying` | has the keyword |
| `a:` | `artist:` | `a:"rk post"` | artist contains |
| `wm:` | `watermark:` | `wm:izzet` | watermark |

### Colors

`c:` (color) and `id:` (color identity) accept letters (`w u b r g`), color names (`white`…),
guild/shard/wedge names (`azorius`, `bant`, `jeskai`…), `c`/`colorless`, and `m`/`multicolor`.

| Operator | Meaning | Example |
| --- | --- | --- |
| `:` or `>=` | contains at least these colors | `c:rg` (red **and** green, maybe more) |
| `=` | exactly these colors | `c=rg` |
| `<=` | only these colors (subset) | `c<=rg` |
| `>` / `<` | strict superset / subset | `c>r` |

```text
c:c            colorless
c:m            multicolor
id:wubrg       five-color identity
```

### Numbers

| Filter | Aliases | Example |
| --- | --- | --- |
| `mv` | `cmc`, `manavalue` | `mv>=6`, `cmc=0` |
| `pow` | `power` | `pow>tou` is **not** supported; use a number: `pow>=5` |
| `tou` | `toughness` | `tou<=1` |
| `loy` | `loyalty` | `loy=3` |
| `usd` / `eur` / `tix` | — | `usd>=10` |
| `year` | — | `year<=2011` |
| `date` | — | `date>=2020-01-01` |

### Printing and legality

| Filter | Aliases | Example | Notes |
| --- | --- | --- | --- |
| `r:` | `rarity:` | `r>=rare` | ordered: common < uncommon < rare < mythic < special < bonus |
| `s:` | `set:`, `e:`, `edition:` | `s:mh2` | set code |
| `cn:` | `number:` | `cn:122` | collector number |
| `f:` | `format:`, `legal:` | `f:modern` | legal or restricted in that format |
| `lang:` | `language:` | `lang:ja` | |
| `is:` | — | `is:foil`, `is:etched`, `is:transform`, `is:graded` | layout (split/flip/transform/mdfc/dfc/meld/leveler/saga/adventure), `foil` / `etched` finish availability, `graded` (an owned [graded copy](../features/grading.md)), or a boolean flag on the card |
| `layout:` | — | `layout:saga` | |
| `border:` | — | `border:black` | |
| `frame:` | — | `frame:2015` | |
| `game:` | — | `game:mtgo` | available on paper/mtgo/arena |
| `st:` | `settype:` | `st:funny` | set type |
| `stamp:` | — | `stamp:oval` | security stamp |
| `tag:` | `tags:` | `tag:for-trade` | your own [tag](../features/cards.md#tags) on an owned card |
| `m:` | `mana:` | `m:{R}{R}` | mana cost contains these symbols (approximate) |

`tag:` matches the labels you add to cards in your collection, so it only ever finds cards you own
(combine it with `-` to exclude, e.g. `-tag:for-trade`). Tags can contain colons, so
`tag:deck:goblins` works.

### Mana value vs. mana cost

- `mv` / `cmc` compares the numeric **mana value**.
- `m:` / `mana:` matches **symbols** in the printed mana cost (e.g. `m:{G}{G}`). It's an
  approximate "contains these symbols" match.

## Examples

```text
c:r t:instant mv<=2                  cheap red instants
t:creature pow>=5 f:commander        big creatures legal in Commander
-is:reprint year>=2022               recent first printings
o:/draw a card/ id:u                 blue cards whose text matches a regex
(t:goblin OR t:elf) r>=rare          rare-or-better goblins and elves
```

See [Regular Expressions](regex.md) for the `/…/` regex flavor and its caveats.

!!! tip "Unknown filters"
    If you use a keyword scryme doesn't recognize, it tells you instead of returning a confusing or
    empty result.

## Sorting results

Use the **Sort** and direction dropdowns next to the search bar (or the `sort` and `dir` query
parameters) to order results by **name**, **mana value**, **price (USD)**, **set**, **rarity**, or
**release date**, ascending or descending. Cards without a value (e.g. no price) always sort last.

## Universal filter (always applied)

Set a **universal search filter** in **Settings (⚙) → Universal search filter** — any Scryfall
syntax there is ANDed into *every* search automatically. Handy for always hiding cards you don't
care about or restricting to a format, e.g. `-is:ub` or `legal:commander`.

It's saved in your browser and shown as an **Always applied** chip above the results (with a ✕ to
clear it). A universal filter that doesn't parse is ignored for that search (and flagged) rather
than breaking every query.

## Card previews

Hovering a card **name link** anywhere in the app — search results, decks, tables, printings —
pops up a preview of that card's image. (Card names inside oracle text are plain text, so they're
excluded.)

## Saved searches

Save a query you run often from the **Saved** menu: it stores the query along with its scope, sort,
and direction, and one click re-runs it later. Saving with an existing name overwrites it. Saved
searches are disabled in read-only (demo) mode.

## Exporting results

The **Export** menu downloads the current results (it respects the live query, scope, and sort):

- **CSV** — one row per card with the owned quantity and USD price.
- **Decklist (.txt)** — `Nx Name (SET) CN` lines.
- **ManaBox CSV** — your owned stacks matching the search, in ManaBox's layout, so the file
  re-imports cleanly.
