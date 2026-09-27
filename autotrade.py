#!/usr/bin/env python3
"""Paper auto-trader. Runs after scan_daily.py. Alpaca PAPER account only.

Setup F fires -> buy $600 of shares at market, with a GTC sell at the target
(the prior high; F has no stop). One buy per name per 30 days. Everything else
the scanner shows (D, C, S, weekly) is watchlist-only and never traded here.
A trial started 2026-09-23 to compare the rule against Arrington's own trades
for one week before any real-money broker (Schwab) is considered."""
import csv, json, os, sys, time, datetime, urllib.request
from scan_daily import notify, discord, DOCS, TRADES, TCOLS

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


def rows():
    return list(csv.DictReader(open(TRADES)))


def save(rs):
    with open(TRADES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TCOLS); w.writeheader()
        for r in rs: w.writerow({k: r.get(k, "") for k in TCOLS})


def log_buy(rs, t, fill, today):
    """The bot's buy as a trades.csv row, so the page and the cards see it."""
    rs.append(dict(id=f"{today}-{t['sym']}-auto", kind="shares", acct="paper-auto", opened=str(today),
                   symbol=t["sym"], setup="F", entry=f"{fill:.2f}", target=f"{t['tgt']:.2f}",
                   contracts=str(t["qty"]), status="open",
                   note=f"Paper bot. Setup F fired; {t['qty']} shares at market, GTC sell at the target."))


def close_filled(rs, sells):
    """sells: {symbol: (price, date)} for take-profit fills. Closes the matching open rows."""
    for r in rs:
        if r.get("acct") == "paper-auto" and r["status"] == "open" and r["symbol"] in sells:
            px, day = sells[r["symbol"]]
            r.update(status="closed", closed=day, exit=f"{px:.2f}",
                     pl_pct=f"{(px / float(r['entry']) - 1) * 100:.1f}")
    return rs


def sync():
    """Before the scan: any bot position Alpaca has sold is closed in trades.csv."""
    rs = rows(); pos = {p["symbol"]: p for p in call("/positions")}; held = set(pos)
    # a buy logged before its fill reported carries the scan price; the broker's average wins
    for r in rs:
        if r.get("acct") == "paper-auto" and r["status"] == "open" and r["symbol"] in pos:
            r["entry"] = f"{float(pos[r['symbol']]['avg_entry_price']):.2f}"
    want = {r["symbol"] for r in rs if r.get("acct") == "paper-auto" and r["status"] == "open"} - held
    sells = {}
    for sym in want:
        o = [x for x in call(f"/orders?status=closed&symbols={sym}&direction=desc&limit=20")
             if x["side"] == "sell" and x["status"] == "filled"]
        if o: sells[sym] = (float(o[0]["filled_avg_price"]), o[0]["filled_at"][:10])
    save(close_filled(rs, sells))
    if sells: print(f"auto: closed {sorted(sells)}")


START = 100_000.0   # the paper account's opening balance


def book_rows(open_):
    """The Ledger paper book (paper/small/big rows in trades.csv) as (label, cost, value).
    Calls: 100 x contracts. Share rows carry no count, so they are sized like the rule: $600."""
    out = []
    for r in open_:
        if r.get("acct") not in ("paper", "small", "big") or r.get("now") is None: continue
        e, now = float(r["entry"]), float(r["now"])
        if r["kind"] == "call":
            n = int(r.get("contracts") or 1) * 100
            lab = f"{r['symbol']} {float(r['strike']):g}C {r['expiry'][5:].replace('-', '/')}"
        else:
            n = max(1, int(CAP // e)); lab = f"{r['symbol']} {n} SH"
        out.append((lab, e * n, now * n))
    return out


def recap_text(acct, positions, book=()):
    """Start -> now for every paper trade: the Ledger book, then the auto bot. Pure, so it can be tested."""
    D = lambda x: f"${x:,.2f}"; S = lambda x: f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"
    row = lambda lab, a, b: f"{lab} · {D(a)} → <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.0f}%"
    L = ["<b>📒 PAPER PORTFOLIO</b>", ""]
    if book:
        a, b = sum(x[1] for x in book), sum(x[2] for x in book)
        L += ["<b>LEDGER PAPER TRADES</b>", f"START {D(a)} → NOW <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.1f}%", ""]
        L += [row(*x) for x in sorted(book, key=lambda x: x[2] - x[1], reverse=True)] + [""]
    eq = float(acct["equity"])
    L += ["<b>🤖 AUTO BOT (Alpaca)</b>", f"START {D(START)} → NOW <b>{D(eq)}</b> · {S(eq - START)} · {(eq / START - 1) * 100:+.2f}%", ""]
    L += [row(f"{p['symbol']} {int(float(p['qty']))} SH", float(p["cost_basis"]), float(p["market_value"]))
          for p in sorted(positions, key=lambda p: p["symbol"])] or ["No open trades."]
    L += ["", "<i>paper · not real money</i>"]
    return "\n".join(L)


def recap(force=False):
    """Once a day after the close (or when forced): the paper account, to #paper-portfolio."""
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = str(datetime.date.today())
    after_close = datetime.datetime.now(datetime.timezone.utc).hour >= 20
    if not force and (not after_close or state.get("_recap") == today): return
    book = book_rows(json.load(open(os.path.join(DOCS, "data.json"))).get("open", []))
    if discord(recap_text(call("/account"), call("/positions"), book), "paper"):
        state["_recap"] = today; json.dump(state, open(STATE, "w")); print("auto: recap posted")


def main():
    if not call("/clock")["is_open"]: return print("auto: market closed")
    d = json.load(open(os.path.join(DOCS, "data.json")))
    held = {p["symbol"] for p in call("/positions")} | {o["symbol"] for o in call("/orders?status=open")}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = datetime.date.today(); rs = rows()
    for t in plan(d["F"], held, state, today):
        try:
            o = call("/orders", dict(symbol=t["sym"], qty=str(t["qty"]), side="buy", type="market",
                                 time_in_force="gtc", order_class="oto",
                                 take_profit=dict(limit_price=str(t["tgt"]))))
            state[t["sym"]] = str(today)
            time.sleep(2)   # a market order fills in well under a second in paper
            fill = float(call(f"/orders/{o['id']}").get("filled_avg_price") or t["px"])
            log_buy(rs, t, fill, today)
            notify(f"<b>🤖 PAPER BOUGHT {t['sym']} ${t['px']:,.2f}</b>\n"
                   f"{t['qty']} SHARES · ${t['qty'] * t['px']:,.0f}\n"
                   f"SELL ORDER AT ${t['tgt']:,.2f} · +{(t['tgt'] / t['px'] - 1) * 100:.0f}%\n"
                   f"<i>Alpaca paper account · not real money</i>", "trades")
            print(f"auto: bought {t['qty']} {t['sym']}, sell at {t['tgt']}")
        except Exception as e:
            print(f"auto: {t['sym']} failed: {str(e)[:80]}")
    json.dump(state, open(STATE, "w")); save(rs)


if __name__ == "__main__":
    if os.environ.get("AUTOTEST"):
        t = datetime.date(2026, 9, 23)
        f = [dict(sym="BE", px=274, tgt=351.28), dict(sym="NOW", px=140, tgt=194.73), dict(sym="BIG", px=900, tgt=1000)]
        p = plan(f, {"NOW"}, {"BE": "2026-09-01"}, t)
        assert p == [], p                                   # BE bought 22 days ago, NOW held, BIG over cap
        p = plan(f, set(), {"BE": "2026-08-01"}, t)
        assert [(x["sym"], x["qty"]) for x in p] == [("BE", 2), ("NOW", 4)], p
        rs = []; log_buy(rs, dict(sym="BE", qty=2, tgt=351.28), 275.02, t)
        assert rs[0]["acct"] == "paper-auto" and rs[0]["entry"] == "275.02" and rs[0]["status"] == "open"
        close_filled(rs, {"BE": (351.28, "2026-10-20")})
        assert rs[0]["status"] == "closed" and rs[0]["pl_pct"] == "27.7" and rs[0]["exit"] == "351.28"
        r = recap_text({"equity": "100123.45", "cash": "97000"},
                       [{"symbol": "BE", "qty": "2", "cost_basis": "550.04", "market_value": "577.40"}])
        assert "START $100,000.00 → NOW <b>$100,123.45</b> · +$123.45 · +0.12%" in r
        assert "BE 2 SH · $550.04 → <b>$577.40</b> · +$27.36 · +5%" in r
        b = book_rows([dict(acct="small", kind="call", symbol="QCOM", strike="240", expiry="2026-11-20",
                            contracts="1", entry="3.72", now=6.35),
                       dict(acct="paper", kind="shares", symbol="MU", entry="1000.26", now=1082.28),
                       dict(acct="paper-auto", kind="shares", symbol="BE", entry="275", now=288)])
        assert b == [("QCOM 240C 11/20", 372.0, 635.0), ("MU 1 SH", 1000.26, 1082.28)], b
        print("autotrade ok")
    elif sys.argv[1:] == ["sync"]:
        sync(); recap()
    elif sys.argv[1:] == ["recap"]:
        recap(force=True)
    else:
        main()
