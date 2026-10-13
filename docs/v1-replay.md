# Version 1 replay of the 35 trades · rules v1.0

Each trade rebuilt at its entry time from data that existed then (Build Step 8). Hard = the recorded-field verdict (Part C). V1 state = what the full engine would have said at that minute.

| # | Contract | Entry | Market | Trend | Trigger | Stop | Hard | V1 state | Codes | Missing |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | BE 10/09 277.5C | 10-09 08:45 CT | BEARISH -3 | UP | $286.95 | $283.53 | FAIL | NO TRADE | DTE_FAIL | option spread (no free quote history) |
| 2 | AAPL 10/16 335C | 10-09 08:30 CT | MIXED +1 | UP | $341.57 | $340.57 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 3 | NOW 10/16 140C | 10-08 08:45 CT | BEARISH -3 | UP | $140.80 | $140.32 | PASS | WAIT | MARKET_CONFLICT | option spread (no free quote history) |
| 4 | QQQ 10/09 751C | 10-09 08:45 CT | BEARISH -3 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 5 | AMZN 10/23 255C | 10-08 12:45 CT | CHOPPY -4 | DOWN | $253.17 | $253.71 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 6 | AMZN 10/21 260C | 10-07 11:55 CT | MIXED -1 | DOWN | $251.08 | $251.62 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 7 | AMZN 10/12 250P | 10-06 08:30 CT | MIXED +1 | DOWN | $250.18 | $251.02 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 8 | BE 10/02 292.5C | 10-02 08:30 CT | MIXED +0 | UP | $287.15 | $284.73 | FAIL | NO TRADE | DTE_FAIL | option spread (no free quote history) |
| 9 | META 10/02 710P | 10-01 08:30 CT | MIXED -1 | UP | $738.25 | $735.95 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 10 | PLTR 10/02 190C | 09-30 08:30 CT | MIXED +1 | UP | $188.63 | $188.05 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 11 | PLTR 10/02 192.5C | 09-30 08:30 CT | MIXED +1 | UP | $188.63 | $188.05 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 12 | QQQ 10/02 746C | 09-30 08:30 CT | MIXED +1 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 13 | QQQ 10/02 746C | 09-30 14:55 CT | BEARISH -4 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 14 | QQQ 10/02 745C | 09-28 09:10 CT | BEARISH -5 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 15 | QQQ 10/05 750C | 09-25 08:55 CT | BULLISH +5 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 16 | META 09/25 745P | 09-25 08:30 CT | MIXED +0 | UP | $779.82 | $776.58 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 17 | QQQ 09/25 748C | 09-23 08:35 CT | MIXED -2 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 18 | QQQ 09/24 752C | 09-22 08:50 CT | CHOPPY +2 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 19 | BE 09/18 275C | 09-18 08:50 CT | BEARISH -3 | UP | $288.00 | $286.13 | FAIL | NO TRADE | DTE_FAIL | option spread (no free quote history) |
| 20 | BABA 09/18 113C | 09-18 08:50 CT | BEARISH -3 | DOWN | $107.31 | $107.59 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 21 | BABA 10/02 109C | 09-14 09:50 CT | CHOPPY -2 | DOWN | $109.00 | $109.17 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 22 | AMZN 09/21 245P | 09-15 08:55 CT | CHOPPY +3 | DOWN | $250.74 | $251.50 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 23 | PYPL 09/25 53C x2 | 09-10 10:55 CT | MIXED -1 | DOWN | $52.13 | $52.25 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 24 | NVDA 09/11 222.5P | 09-04 08:30 CT | MIXED +2 | UP | $230.40 | $229.56 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 25 | AMZN 09/18 272.5C | 08-28 10:00 CT | BULLISH +4 | UP | $259.66 | $258.98 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 26 | AMZN 09/04 257.5C | 09-02 09:05 CT | BULLISH +5 | UP | $255.82 | $255.24 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 27 | AMZN 09/04 257.5C | 09-01 08:35 CT | MIXED -2 | UP | $264.36 | $263.30 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 28 | SPY 09/03 762P | 08-31 09:10 CT | BEARISH -4 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 29 | SPY 09/03 763P | 08-31 08:35 CT | MIXED -2 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 30 | NVDA 08/28 220P | 08-28 11:05 CT | BEARISH -3 | UP | $230.47 | $229.24 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 31 | QQQ 09/09 720C | 08-28 08:35 CT | MIXED +2 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |
| 32 | AAPL 08/31 315C | 08-27 10:00 CT | CHOPPY +4 | DOWN | $308.80 | $309.58 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 33 | ANF 09/04 150C | 08-27 08:40 CT | MIXED +1 | UP | $154.58 | $152.26 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 34 | AAPL 08/26 315C | 08-26 08:30 CT | MIXED +2 | DOWN | $308.21 | $308.84 | FAIL | NO TRADE | NO_SOURCE | option spread (no free quote history) |
| 35 | SPY 08/26 761P | 08-24 08:30 CT | MIXED -2 | — | — | — | FAIL | NO TRADE | INDEX_OPTION | option spread (no free quote history) |

**Hard rules: 1 PASS, 34 FAIL. Full engine at the entry minute: 0 NOW.** The option spread has no free history and stays MISSING for every trade.
