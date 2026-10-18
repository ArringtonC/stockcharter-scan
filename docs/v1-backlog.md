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
- **Option spread history.** The 35-trade replay (docs/v1-replay.md) rebuilds the market score, trend,
  trigger, stop and trigger state at each entry minute. The option spread has no free history, so it stays
  MISSING for every trade.
- **MIXED market.** The plan says only "the cleanest aligned setup" may advance on a MIXED day. Version 1
  treats MIXED as no block; trend, VWAP and volume must still line up for NOW.
- **Ticket book.** `ticket_bot.py` (paper, 14–35 day calls) still follows the old gate. It is a separate
  paper book, not the Version 1 test.

## New strategy ideas

- 0DTE exception for Setup F (three BE wins, about +$353 with the bracket). Not added from three examples.

## Software fixes during the paper test (rules unchanged)

- 2026-10-13: SG went MISSED at 8:40 and NOW at 11:01. The plan forbids re-entry after MISSED. MISSED is
  now final for the day. The SG 10/16 10C paper fill is void and not counted.
- 2026-10-13: the market state flipped 14 times in one day. A new state now counts only after 3 reads
  in a row (15 minutes). The score inputs are unchanged.
- **Range fade on CHOPPY days ("ping pong", YouTube, logged 2026-10-14). TESTED, FAILED.** Sell the
  opening-range high and buy its low on choppy days, tight stop, target the other side. SPY and QQQ 5-minute
  bars 2016–2026, 2,711 sessions: the fade equals a random entry with the same stop and target, about zero
  before costs and negative after (SPY choppy days −0.036% a trade, QQQ −0.037%). Both stop widths, both
  sides, every era. Not added. (thesis/range-fade.md)
- **Chase line (2026-10-18, from the play-of-the-day replay).** 0.25 × ATR(15m) past the trigger blocks
  every entry: 0 NOW in 199 sessions; 19 of 48 triggers MISSED. Live, Oct 13–17: five MISSED, no NOW.
  Candidates for 1.1: 1.0 × ATR (1 trade, +$587) or no chase line with the limit price as the cap
  (6 trades, 3 wins, +$704). Also binding: the 3% proximity line, the $400 cap on $200+ stocks.
  Full result: thesis/play-of-day-test.md (private).
