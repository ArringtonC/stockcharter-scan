# Ledger — daily scan

A one-page trading dashboard, rebuilt twice a day by GitHub Actions.

- **Scan** — today's signals across five locked setups, plus the VIX regime
- **Trades** — the paper book, marked to market; wins and losses
- **Week** — what fired this week and what worked
- **Setups** — the rules and the numbers behind each one

`scan_daily.py` fetches prices from Yahoo Finance and point-in-time revenue from
SEC EDGAR, runs the setups, marks `trades.csv`, and writes `docs/`.

Add a paper trade by appending a row to `trades.csv`. The scan closes it automatically
at the target, at 252 sessions, or at option expiry.

The research that produced these setups lives in a private repo. Every backtest
behind this page carries a survivorship caveat: the price source does not serve
delisted tickers.
