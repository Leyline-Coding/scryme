# Regular Expressions

scryme supports regular-expression matching on text fields, inspired by
[Scryfall's regex support](https://scryfall.com/docs/regular-expressions).

## Using regex

Wrap a pattern in slashes. A bare `/…/` matches the **card name**; prefix it with a text filter to
match that field:

```text
/^Goblin/                 names starting with "Goblin"
o:/draws? a card/         oracle text matching the regex
t:/(instant|sorcery)/     type line matching either word
a:/^John/                 artist starting with "John"
wm:/.+/                   any card that has a watermark
```

Regex is available on the text fields: **name**, **`o:`/oracle**, **`t:`/type**,
**`a:`/artist**, and **`wm:`/watermark**. Using `/…/` on any other field is rejected with a
helpful message.

## Flavor and caveats

!!! warning "POSIX, not RE2"
    Scryfall uses Google's RE2 flavor. scryme evaluates regex with **PostgreSQL's POSIX regular
    expressions** (the `~*`, case-insensitive, operator). The common cases — anchors (`^` `$`),
    character classes, alternation `|`, quantifiers `* + ? {n,m}`, and groups — behave the same,
    but some advanced RE2 constructs differ or are unsupported. Matching is **case-insensitive**.

If a pattern behaves unexpectedly, simplify it to the POSIX-supported subset above.
