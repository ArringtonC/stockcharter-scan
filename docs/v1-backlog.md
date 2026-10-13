# Version 1.1 backlog

Version 1 rules are locked (rule version 1.0, 12 Oct 2026). Nothing here changes Version 1.
Ideas wait here until Version 1 closes after the 30-session paper test.

## Gaps found while building Version 1

- **Expiry exit.** The locked plan has no exit for a 3–9 day option that reaches expiry day. The paper
  monitor sells at 14:45 CT on expiry day (code `EXPIRY_CLOSE`, not in the plan's reason list).
- **Break, retest, hold.** Part A allows it as an alternative to the 1.2x volume bar. Not coded; only
  the volume confirmation counts.
- **Breadth.** Part A says "breadth". Version 1 uses the equal-weight S&P ETF (RSP) up or down on the day.
  A real advance/decline line needs a paid feed.
- **Trigger level.** The prepared trigger is the prior day's high (calls) or low (puts). Stop area is
  1 ATR(15m) back through it; targets are 2 and 3 ATR on. An opening-range trigger is a 1.1 idea.
- **Full 35-trade reconstruction.** The replay gives each trade its hard-rule verdict and labels the missing
  fields (market score, VWAP, trigger, volume, chart stop, spread). Rebuilding those from history for the
  one PASS (NOW 10/16 140C) is open.
- **Ticket book.** `ticket_bot.py` (paper, 14–35 day calls) still follows the old gate. It is a separate
  paper book, not the Version 1 test.

## New strategy ideas

- 0DTE exception for Setup F (three BE wins, about +$353 with the bracket). Not added from three examples.
