# Supported Formats

scryme recognizes three export formats by inspecting the CSV header — detection is automatic and
unambiguous. The tables below show how each app's columns map to scryme's internal fields.

## ManaBox

ManaBox includes a **Scryfall ID**, the most reliable match key.

**Header**

```
Binder Name,Binder Type,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,
ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,
Purchase price currency,Added
```

| ManaBox column | scryme field |
| --- | --- |
| `Name` | name |
| `Set code` | set code |
| `Collector number` | collector number |
| `Scryfall ID` | scryfall id *(primary match)* |
| `Foil` | finish (`normal` / `foil` / `etched`) |
| `Quantity` | quantity |
| `Condition` | condition |
| `Language` | language |
| `Purchase price` | purchase price |
| `Binder Name` | binder |

**Detected by:** presence of both `Scryfall ID` and `ManaBox ID`.

## Dragon Shield

Dragon Shield (MTG Scanner) has **no Scryfall ID**, so rows match on set code + collector number.
The export begins with a `sep=,` line, which scryme strips automatically.

**Header**

```
sep=,
Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,
Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET
```

| Dragon Shield column | scryme field |
| --- | --- |
| `Card Name` | name |
| `Set Code` | set code *(primary match, with Card Number)* |
| `Card Number` | collector number |
| `Printing` | finish (`Normal` / `Foil` / `Etched`) |
| `Quantity` | quantity |
| `Condition` | condition |
| `Language` | language (`English` → `en`, etc.) |
| `Price Bought` | purchase price |
| `Folder Name` | binder |

**Detected by:** presence of `Trade Quantity`, `Card Name`, and `Card Number`.

## Delver Lens

Delver Lens has a configurable, Deckbox-compatible export. scryme reads its columns
case-insensitively and accepts several aliases. When a **Scryfall ID** is present it is used as the
primary match key.

| Delver / Deckbox column | scryme field |
| --- | --- |
| `Name` / `Card Name` | name |
| `Set Code` / `Set` | set code |
| `Card Number` / `Collector Number` / `Number` | collector number |
| `Scryfall ID` | scryfall id *(primary match when present)* |
| `Foil` / `Printing` / `Finish` | finish |
| `Quantity` / `Count` | quantity |
| `Condition` | condition |
| `Language` / `Lang` | language |
| `My Price` / `Purchase Price` / `Price` | purchase price |

**Detected by:** a `Scryfall ID` column, or `Edition` + `Card Number` (and never a ManaBox file).

## Adding a new format

Importers self-register. To add one, create a module in `backend/src/importers/` with a class that
implements `detect(text)` and `parse(text)` (returning `ImportRow`s) and is decorated with
`@register`, then import it in `backend/src/importers/__init__.py`. Detection rules should be
specific enough not to overlap with existing formats.
