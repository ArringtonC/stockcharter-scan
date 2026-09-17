# Ledger — design context

## Theme

**Dark, and it is a decision.** The scene: one person at a desk at 7:40am in a room
where the only other light is a second monitor with a chart on it, checking whether
today requires anything of him. Dark is the ambient match, and it keeps the two colors
that carry meaning — amber for "attention here", red for "real money" — legible without
shouting.

Not dark because trading tools are dark. If the scene were a lit office at noon this
would be a light page.

## Color

Strategy: **restrained.** Tinted neutrals and one accent.

- Ground `#15191E`, panel `#1C2128` — blue-tinted charcoal, never `#000`.
- Ink `#E4E8EE`, secondary `#B4BCC7`, dim `#8A94A3`.
- Accent **amber** `#E2A73B`, second step `#F5C866`. Used for: the wordmark, the current
  tab, setup keys, "attention here" in a table. Under 10% of surface.
- Up `#5FBF84`, down `#E06B5A`. Reserved for direction. Never decorative.
- Light theme exists and is complete, keyed off `prefers-color-scheme`.

## Typography

Two faces, both purposeful:

- **Fraunces** — the wordmark, section headings, and every large number. A serif with
  optical sizing, which is why the VIX reading at 64–96px has presence without being
  loud. It signals "document", not "app".
- **IBM Plex Mono** — all data, labels, rules, and body copy. Tabular by nature, so
  columns align without `font-variant-numeric` fights.

Scale: 64px VIX / 30px h1 / 20px h2 / 15px body / 12.5px table / 10.5px uppercase label.
Uppercase labels carry 0.08–0.14em tracking.

## Layout

- Single column, `max-width: 1040px`, generous left/right padding.
- Tabs as the only navigation. Five of them. State persisted in localStorage.
- Tables are the primary object, not cards. Cards appear only for the stat strip and the
  Rules entries, where each item genuinely is a separate object.
- `<details>` for every explanation, so the page is short at rest and deep on demand.

## Components

- **VIX meter** — a four-band gradient rail with a position marker. The only chart.
- **Stat strip** — a hairline grid, one border, no shadows.
- **Pill** — setup key, real/paper tag, win/loss result.
- **Rank track** — a 150px inline SVG polyline, sectors only.

## Known gaps

- The masthead runs four lines before the first real information.
- "Scan 4" as a tab badge counts things that mostly cannot be traded.
- Six setups now, and a first-time reader has no way to know which two matter.
