# Graded Cards

Track graded/slabbed copies with more than a condition — the grading company, grade, certificate
number, a photo, and a manual value (graded prices aren't in Scryfall).

## Grading a card

On a card you own, open the **🏅 grading** control on any stack (its
[detail page](cards.md#editing-your-collection)) and set:

- **Company** — PSA, BGS, CGC, or SGC.
- **Grade** — e.g. `10`, `9.5`.
- **Cert #** — the certification number.
- **Value $** — a manual value that overrides the market price for this copy.
- **Photo** — an optional condition/provenance image (JPEG/PNG/WebP, up to 8 MB), stored under your
  data directory and shown on the card page.

A graded stack shows a **🏅 badge** with the company and grade; **Clear** removes the grading and
photo.

## Where grades show up

- **Card page** — the badge, cert/value in its tooltip, and the photo.
- **Search** — `is:graded` finds every graded copy you own (combine with `-is:graded` to exclude).
- **[Valuation report](sell.md#valuation-report)** — the manual value override is used instead of
  the market price.

!!! note
    Grade metadata lives on your collection and is included in [backups](backup.md); the photo files
    on disk are not part of the JSON backup.
