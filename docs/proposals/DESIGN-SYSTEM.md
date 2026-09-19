# The approved system

Extracted from **Today**, approved 2026-09-18. This is the source of truth for
pages 2–8. Do not invent a new language on a later page. If a page needs a
component that does not exist here, add it to `review.css` so every page gets it.

Files: `review.css` (tokens + components), `review.js` (review chrome).

## Tokens — unchanged from index.html

| | |
|---|---|
| Ground | `--bg #0E0F12` · `--bg2 #141518` · `--panel #191A1F` |
| Rules | `--line #26282F` · `--line2 #33363F` |
| Ink | `--ink #E8E4DB` · `--ink2 #A8A49B` · `--dim #6E6B64` |
| Accent | `--amber #E2A73B` — action and identity only |
| Semantic | `--up #5FBF84` · `--dn #E06B5A` — real state only, never decoration |
| Space | `--s1 6` `--s2 10` `--s3 16` `--s4 24` `--s5 36` `--s6 56` |
| Type | `--t-h1 30` `--t-h2 19` `--t-body 14` `--t-data 12.5` `--t-label 10.5` |

Light mode is defined three ways — bare `:root`, `@media` guarded by
`:not([data-theme=dark])`, and `[data-theme=light]`. A colour defined only inside
a media query is the bug that shipped in index.html.

## Type

Fraunces for identity, verdicts and major numbers. IBM Plex Mono for everything
technical: labels, data, symbols, dates. Labels are `--t-label`, uppercase,
`letter-spacing --tr`. Numbers in columns get `font-variant-numeric: tabular-nums`.

`--t-hero` (76px) is retired. The largest thing on a page is the verdict at
`clamp(30px, 5.2vw, 46px)`, and only one per page.

## Layout

Thin rules, not filled cards. A `.row` is a three-column grid — identity, plain
language, number — separated by `border-top`. `.panel` fills are for disclosures
and status blocks only. Radii stay at 5–6px. Wide content scrolls in its own
container; the page body never scrolls sideways.

Breakpoints inside a page use `@container`, not `@media`, so the review frame at
390px shows the real phone layout.

## Components

| Class | Use |
|---|---|
| `.tmast` | identity + timestamp. No controls. |
| `.whatis` | one line saying what this is, detail folded behind it |
| `.verdict` `.say` `.vstat` `.vquiet` | plain answer, count line, one sentence |
| `.ctx` | market context strip — VIX, path, QQQ |
| `.sec` `h2` `.tag` | a section, with `trade` / `watch` labelling |
| `.row` `.row.tight` | identity · plain language · number |
| `.sum` | two summaries side by side, capped, then `+N more` |
| `.deep` `.chk` | "View all checks" disclosure |
| `.week` | five real scans, not a sparkline |
| `.sk` `.err` `.stalebar` | loading, error, stale |
| `.foot` | data provenance |

## Content rules

1. **Plain-language answer first. Technical evidence second.** Never a paragraph
   where a count line and one sentence will do.
2. **Always distinguish** signal · watchlist · context · untested. The `.tag` on a
   section header carries this and is not optional.
3. **Never invent** a hit rate, probability, recommendation, signal or near-miss
   score. A near state is derived from real conditions and **names what is
   missing**.
4. **Never print a number you cannot source.** The Today count line dropped its
   "names" figure because `data.json` does not carry the universe size. Dropping
   the claim beat guessing it.
5. Green and red mean real state. They are never used to decorate.

## Motion

Subtle only. Everything is visible at rest — no section waits on scroll.
`prefers-reduced-motion` kills all of it.

## The process, unchanged

One page at a time. BEFORE → PROPOSED AFTER → KEEP/CHANGE → required states →
**STOP for approval**. Nothing spreads past an unapproved checkpoint. `index.html`
is not touched until all eight pages pass and the final consistency review runs.

## Board

| # | Page | Before | After | States | Approved |
|---|---|---|---|---|---|
| 1 | Today | ✓ | ✓ | ✓ | **✓ 2026-09-18** |
| 2 | Trades | ✓ | ✓ | ✓ | waiting |
| 3 | Watching | | | | |
| 4 | Setups | | | | |
| 5 | Rules | | | | |
| 6 | Sectors | | | | |
| 7 | Methodology | | | | |
| 8 | System | | | | |

## Open items, carried forward

- **Universe count.** Today wants "63 names" in its count line. `scan_daily.py`
  must emit a `universe` field. That is a data change and waits for approval.
- **Run scan now** has no home until System (page 8) exists. It is out of the
  masthead and not yet placed.
