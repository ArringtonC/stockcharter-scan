#!/usr/bin/env python3
"""Paper auto-trader. Runs after scan_daily.py. Alpaca PAPER account only.

Setup F fires -> buy $600 of shares at market, with a GTC sell at the target
(the prior high; F has no stop). One buy per name per 30 days. Everything else
the scanner shows (D, C, S, weekly) is watchlist-only and never traded here.
A trial started 2026-09-23 to compare the rule against Arrington's own trades
for one week before any real-money broker (Schwab) is considered."""
import json, os, datetime, urllib.request
from scan_daily import notify, DOCS

API = "https://paper-api.alpaca.markets/v2"     # ponytail: hard-coded paper; real money is a separate decision
CAP = 600
STATE = os.path.expanduser("~/.ledger-auto.json")  # outside the public repo


def call(path, body=None, method=None):
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"],
         "Content-Type": "application/json"}
    rq = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                headers=h, method=method or ("POST" if body else "GET"))
    return json.load(urllib.request.urlopen(rq, timeout=20))


def plan(fires, held, state, today):
    """Which F fires to buy, and how many shares. Pure, so it can be tested."""
    out = []
    for r in fires:
        sym, px = r["sym"], r["px"]
        last = state.get(sym)
        if sym in held or (last and (today - datetime.date.fromisoformat(last)).days < 30): continue
        qty = int(CAP // px)
        if qty >= 1: out.append(dict(sym=sym, qty=qty, px=px, tgt=round(r["tgt"], 2)))
    return out


def main():
    if not call("/clock")["is_open"]: return print("auto: market closed")
    d = json.load(open(os.path.join(DOCS, "data.json")))
    held = {p["symbol"] for p in call("/positions")} | {o["symbol"] for o in call("/orders?status=open")}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = datetime.date.today()
    for t in plan(d["F"], held, state, today):
        try:
            call("/orders", dict(symbol=t["sym"], qty=str(t["qty"]), side="buy", type="market",
                                 time_in_force="gtc", order_class="oto",
                                 take_profit=dict(limit_price=str(t["tgt"]))))
            state[t["sym"]] = str(today)
            notify(f"<b>🤖 PAPER BOUGHT {t['sym']} ${t['px']:,.2f}</b>\n"
                   f"{t['qty']} SHARES · ${t['qty'] * t['px']:,.0f}\n"
                   f"SELL ORDER AT ${t['tgt']:,.2f} · +{(t['tgt'] / t['px'] - 1) * 100:.0f}%\n"
                   f"<i>Alpaca paper account · not real money</i>")
            print(f"auto: bought {t['qty']} {t['sym']}, sell at {t['tgt']}")
        except Exception as e:
            print(f"auto: {t['sym']} failed: {str(e)[:80]}")
    json.dump(state, open(STATE, "w"))


if __name__ == "__main__":
    if os.environ.get("AUTOTEST"):
        t = datetime.date(2026, 9, 23)
        f = [dict(sym="BE", px=274, tgt=351.28), dict(sym="NOW", px=140, tgt=194.73), dict(sym="BIG", px=900, tgt=1000)]
        p = plan(f, {"NOW"}, {"BE": "2026-09-01"}, t)
        assert p == [], p                                   # BE bought 22 days ago, NOW held, BIG over cap
        p = plan(f, set(), {"BE": "2026-08-01"}, t)
        assert [(x["sym"], x["qty"]) for x in p] == [("BE", 2), ("NOW", 4)], p
        print("autotrade ok")
    else:
        main()
