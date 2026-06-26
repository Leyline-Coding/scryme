# Search Syntax

scryme implements a faithful subset of [Scryfall's search syntax](https://scryfall.com/docs/syntax).
Queries combine **filters** with boolean logic, and by default search **your collection**.

!!! tip "Don't know the syntax yet?"
    The **[Advanced search](/advanced)** page (linked as *Advanced* next to the search bar) is a
    form-based builder: fill in names, colors, mana value, rarity, format, price, and more, and it
    assembles the query for you — then shows the generated query string so you can learn by example.

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
| `is:` | — | `is:foil`, `is:transform` | layout (split/flip/transform/mdfc/dfc/meld/leveler/saga/adventure) or a boolean flag on the card |
| `layout:` | — | `layout:saga` | |
| `border:` | — | `border:black` | |
| `frame:` | — | `frame:2015` | |
| `game:` | — | `game:mtgo` | available on paper/mtgo/arena |
| `st:` | `settype:` | `st:funny` | set type |
| `stamp:` | — | `stamp:oval` | security stamp |
| `m:` | `mana:` | `m:{R}{R}` | mana cost contains these symbols (approximate) |

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
