# Version 1 replay of the 35 trades · rules v1.0

| # | Contract | Cost | DTE | Source | V1 | Codes |
|---|---|---|---|---|---|---|
| 1 | BE 10/09 277.5C | $98 | 0 | Setup F | FAIL | DTE_FAIL |
| 2 | AAPL 10/16 335C | $401 | 7 | none | FAIL | NO_SOURCE, COST_FAIL |
| 3 | NOW 10/16 140C | $386 | 8 | Setup F | PASS | needs full trigger replay |
| 4 | QQQ 10/09 751C | $102 | 0 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |
| 5 | AMZN 10/23 255C | $591 | 15 | none | FAIL | NO_SOURCE, DTE_FAIL, COST_FAIL |
| 6 | AMZN 10/21 260C | $515 | 14 | none | FAIL | NO_SOURCE, DTE_FAIL, COST_FAIL |
| 7 | AMZN 10/12 250P | $242 | 6 | none | FAIL | NO_SOURCE |
| 8 | BE 10/02 292.5C | $230 | 0 | Setup F | FAIL | DTE_FAIL |
| 9 | META 10/02 710P | $227 | 1 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 10 | PLTR 10/02 190C | $254 | 2 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 11 | PLTR 10/02 192.5C | $170 | 2 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 12 | QQQ 10/02 746C | $304 | 2 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |
| 13 | QQQ 10/02 746C | $248 | 2 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |
| 14 | QQQ 10/02 745C | $281 | 4 | none | FAIL | NO_SOURCE, INDEX_OPTION |
| 15 | QQQ 10/05 750C | $565 | 10 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL, COST_FAIL |
| 16 | META 09/25 745P | $191 | 0 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 17 | QQQ 09/25 748C | $227 | 2 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |
| 18 | QQQ 09/24 752C | $138 | 2 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |
| 19 | BE 09/18 275C | $225 | 0 | Setup F | FAIL | DTE_FAIL |
| 20 | BABA 09/18 113C | $66 | 0 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 21 | BABA 10/02 109C | $435 | 18 | none | FAIL | NO_SOURCE, DTE_FAIL, COST_FAIL |
| 22 | AMZN 09/21 245P | $119 | 6 | none | FAIL | NO_SOURCE |
| 23 | PYPL 09/25 53C x2 | $332 | 15 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 24 | NVDA 09/11 222.5P | $114 | 7 | none | FAIL | NO_SOURCE |
| 25 | AMZN 09/18 272.5C | $485 | 21 | none | FAIL | NO_SOURCE, DTE_FAIL, COST_FAIL |
| 26 | AMZN 09/04 257.5C | $167 | 2 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 27 | AMZN 09/04 257.5C | $217 | 3 | none | FAIL | NO_SOURCE |
| 28 | SPY 09/03 762P | $199 | 3 | none | FAIL | NO_SOURCE, INDEX_OPTION |
| 29 | SPY 09/03 763P | $205 | 3 | none | FAIL | NO_SOURCE, INDEX_OPTION |
| 30 | NVDA 08/28 220P | $85 | 0 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 31 | QQQ 09/09 720C | $845 | 12 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL, COST_FAIL |
| 32 | AAPL 08/31 315C | $247 | 4 | none | FAIL | NO_SOURCE |
| 33 | ANF 09/04 150C | $470 | 8 | none | FAIL | NO_SOURCE, COST_FAIL |
| 34 | AAPL 08/26 315C | $29 | 0 | none | FAIL | NO_SOURCE, DTE_FAIL |
| 35 | SPY 08/26 761P | $203 | 2 | none | FAIL | NO_SOURCE, INDEX_OPTION, DTE_FAIL |

**1 PASS, 34 FAIL.** Missing for every trade (not filled with hindsight): market score, VWAP side, prepared trigger, trigger-bar volume, chart stop, option spread.
