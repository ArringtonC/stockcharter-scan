# Turning on real option quotes

Without any of this the page still works — it falls back to Black-Scholes and labels
the mark `iv` or `est`. With it, the mark says `schwab` or `alpaca` and is a real
bid/ask midpoint.

Order tried: **Schwab → Alpaca → Yahoo chain IV → 60-day realized vol.**

## Alpaca (do this first — 5 minutes, free, no approval)

1. Sign up at **alpaca.markets**, paper account is fine.
2. Home → **Generate New Key**. Copy the Key ID and the Secret. The secret is shown once.
3. Add both as repo secrets:

```
gh secret set ALPACA_KEY    --repo ArringtonC/stockcharter-scan
gh secret set ALPACA_SECRET --repo ArringtonC/stockcharter-scan
```

## Schwab (slower — needs app approval, usually 1–2 days)

1. **developer.schwab.com** → create an account → **Create App**.
2. Product: **Accounts and Trading Production**. Callback URL: `https://127.0.0.1:8182`.
3. Wait for the app to move from *Approved - Pending* to **Ready For Use**.
4. Get a refresh token once, locally:

```
pip install schwab-py
python -c "from schwab.auth import client_from_login_flow as f; f('KEY','SECRET','https://127.0.0.1:8182','tok.json')"
```

A browser opens, you log in to Schwab, and `tok.json` is written. The refresh token
is the `refresh_token` field inside it.

5. Add three secrets:

```
gh secret set SCHWAB_KEY     --repo ArringtonC/stockcharter-scan
gh secret set SCHWAB_SECRET  --repo ArringtonC/stockcharter-scan
gh secret set SCHWAB_REFRESH --repo ArringtonC/stockcharter-scan
```

**The Schwab refresh token expires every 7 days.** Re-run step 4 and re-set
`SCHWAB_REFRESH` weekly, or leave Alpaca as the working source and treat Schwab as
the better-quote upgrade when it is fresh. Alpaca's keys do not expire.

## Checking it worked

After the next scan, open the Trades tab. The `now` column shows the source next to
the price. `schwab` or `alpaca` means a real quote. `iv` or `est` means the model.
