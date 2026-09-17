# Design brief — Ledger, September 2026 pass

Confirmed by Arrington 2026-09-16. Register: **product**. Scope: **full redesign pass**.

## The problem, stated precisely

The page was built for one person's sixty-second morning check and it does that well. It
now also has to survive ninety seconds from someone who has never seen it. Those are
different jobs and the page currently only does the first.

Measured: a first-time reader meets, in order — a wordmark, a timestamp, a button, and
an 88px number with no explanation. **The page does not say what it is until the footer.**

## Decisions taken

| | |
|---|---|
| **First screen** | One sentence under the wordmark. Do NOT restructure the hero. The VIX reading stays the largest object. |
| **Setups tab** | Group by status: Active, Watchlist only, Retired. A reader should see in one glance that two of six are real. |
| **Scope** | Full pass: spacing scale, meter, stat strip, table typography, section rhythm. |

## The sentence

> Six tested setups, scanned twice a day. Most days nothing fires, and that is the point.

It does three jobs at once: says what the page is, sets the expectation that empties are
normal, and states the honest posture. Nothing else on the first screen needs to change.

## Craft principles for this pass

The user framed it as "what would Ive do." Practically that means reduction, not
restyling:

1. **Remove what is not working.** The decorative amber glow behind the body. The
   side-stripe accents on callouts, which are a banned pattern and which I introduced.
2. **One spacing scale, declared as tokens**, instead of thirty hand-picked margins.
3. **Fewer type steps, further apart.** The current scale has eight sizes between 10px
   and 88px with several within 1px of each other.
4. **The empty scan is the most common screen and should be the most composed one.** It
   currently reads as a failure. It is the normal state.
5. **Nothing added.** No new components, no motion, no color. This pass should end with
   less CSS than it started with.

## Out of scope

Colors, the two typefaces, the tab structure, the tables as primary object, every number
and its caveat. Those were decided and tested; this pass does not reopen them.

## How to tell it worked

A reader who knows markets but not this project can answer three questions in ninety
seconds without clicking: What is this? Is there a trade today? Which rules matter?
