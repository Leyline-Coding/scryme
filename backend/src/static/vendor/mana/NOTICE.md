# Mana font — attribution

Vendored from the **Mana** project by Andrew Gioia — https://mana.andrewgioia.com
(npm `mana-font`, version **1.18.0**).

Licenses (per the project's README):

- The **Mana font** (`fonts/mana.*`) is licensed under the **SIL Open Font License 1.1**
  (http://scripts.sil.org/OFL).
- The **CSS** (`mana.css`) is licensed under the **MIT License**
  (http://opensource.org/licenses/mit-license.html).

What was vendored here, and changes made:

- Only the `mana.*` web fonts are included (woff2/woff/ttf). The upstream package also bundles an
  `MPlantin` font, which is **not** redistributable; it has been deliberately excluded.
- `mana.css` was trimmed: the bundled `@font-face` blocks were removed and replaced with a single
  `@font-face` for the Mana font pointing at the vendored `fonts/` path, and the few rules that
  referenced `MPlantin` as a text family now fall back to free serifs only. The mana symbol
  classes (`.ms*`) are unchanged.

Mana is a fan project and is not affiliated with or endorsed by Wizards of the Coast.
