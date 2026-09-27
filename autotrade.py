#!/usr/bin/env python3
"""Paper auto-trader: Setup F with calls, on the Alpaca PAPER account ($100k). Runs after the scan.

Since 2026-09-26 (Arrington: "Setup F with calls ... test as if we had a 100k account"):
  buy    each F fire -> the at-the-money call the scanner already picks (120 -> 45 days),
         sized at 7% of account equity, his own position rule. One buy per name per 30 days.
  sell   when the stock reaches the F target (the prior high), or 21 days before expiry,
         whichever comes first. F has no stop, so the calls have none either.
The 4 share positions bought 09-23/24 under the old $600-shares version keep their
GTC sells and close on their own. Nothing but Setup F is ever traded here."""
import csv, json, os, re, sys, time, datetime, urllib.request
from scan_daily import notify, discord, DOCS, TRADES, TCOLS, PLAN, affordable, occ, bars

API = "https://paper-api.alpaca.markets/v2"     # ponytail: hard-coded paper; real money is a separate decision
CAP = 600            # the old shares version, kept for sizing the Ideas share rows
RISK = 0.07          # 7% of equity per trade -- Arrington's own rule, applied to $100k
EXIT_DTE = 21        # sell calls this many days before expiry if the target has not hit
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


def plan_calls(fires, held, state, today, budget, pick=affordable):
    """F fires -> (fire, contract) to buy. pick(sym, px, budget) is the scanner's own picker."""
    out = []
    for r in fires:
        sym = r["sym"]; last = state.get("call:" + sym)
        if sym in held or (last and (today - datetime.date.fromisoformat(last)).days < 30): continue
        c = pick(sym, r["px"], budget)
        if c and not c["over"] and c["n"] >= 1: out.append((r, c))
    return out


def exit_due(row, spot, today):
    """Why a bot call should be sold now, or None. Target first, then time."""
    if spot >= float(row["target"]): return "target"
    if (datetime.date.fromisoformat(row["expiry"]) - today).days <= EXIT_DTE: return f"{EXIT_DTE} days left"
    return None


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


def log_call(rs, r, c, fill, n, today):
    rs.append(dict(id=f"{today}-{r['sym']}-fcall", kind="call", acct="bot", opened=str(today),
                   symbol=r["sym"], setup="F", entry=f"{fill:.2f}", target=f"{r['tgt']:.2f}",
                   strike=f"{c['strike']:g}", expiry=c["expiry"], contracts=str(n), status="open",
                   note=f"F-calls bot. {n}x ${c['strike']:g}C {c['expiry']}, 7% of equity. "
                        f"Sells at the stock target ${r['tgt']:.2f} or {EXIT_DTE} days before expiry."))


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
    # calls: sell on target or time. Needs an open market; otherwise the next run does it.
    if call("/clock")["is_open"]:
        today = datetime.date.today()
        for r in rs:
            if r.get("acct") != "bot" or r["status"] != "open": continue
            why = exit_due(r, bars(r["symbol"], "5d")[-1][1], today)
            if not why: continue
            o = call("/orders", dict(symbol=occ(r["symbol"], r["expiry"], r["strike"], pad=False),
                                     qty=r["contracts"], side="sell", type="market", time_in_force="day"))
            time.sleep(2); f = call(f"/orders/{o['id']}")
            if f.get("filled_avg_price"):
                px = float(f["filled_avg_price"])
                r.update(status="closed", closed=str(today), exit=f"{px:.2f}",
                         pl_pct=f"{(px / float(r['entry']) - 1) * 100:.1f}", note=r["note"] + f" Sold: {why}.")
                print(f"auto: sold {r['symbol']} call ({why})")
    save(close_filled(rs, sells))
    if sells: print(f"auto: closed {sorted(sells)}")


START = 100_000.0   # the paper account's opening balance


IDEAS = ("paper", "small", "big")


def dollars(r, share_size):
    """(cost, value, label) of one trades.csv row. Calls: 100 x contracts. Share rows with no
    count are sized at share_size dollars. Closed rows are valued at their exit."""
    e = float(r["entry"]); now = float(r["exit"]) if r["status"] == "closed" else float(r.get("now") or e)
    if r["kind"] == "call":
        n = int(r.get("contracts") or 1) * 100
        lab = f"{r['symbol']} {float(r['strike']):g}C {r['expiry'][5:].replace('-', '/')}"
    else:
        n = int(r["contracts"]) if r.get("contracts") else max(1, int(share_size // e))
        lab = f"{r['symbol']} {n} SH"
    return e * n, now * n, lab


def occ_label(sym):
    m = re.match(r"([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", sym)
    return f"{m[1]} {int(m[6]) / 1000:g}{m[5]} {m[3]}/{m[4]}" if m else sym


def recap_text(acct, positions, trades, plan=PLAN):
    """Three books, each START -> NOW then its open trades. Pure, so it can be tested.
      REAL      his account, from the plan's start balance
      F CALLS   the Alpaca paper bot, $100k
      IDEAS     every other paper trade, scored as if in its own $100k account"""
    D = lambda x: f"${x:,.2f}"; S = lambda x: f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"
    row = lambda lab, a, b: f"{lab} · {D(a)} → <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.0f}%"
    head = lambda a, b: f"START {D(a)} → NOW <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.2f}%"
    L = ["<b>📒 PORTFOLIOS</b>", ""]

    real = [r for r in trades if r.get("acct") == "real" and (r["status"] == "open" or r.get("closed", "") >= plan["started"])]
    rd = [dollars(r, CAP) for r in real]
    start = plan["start"] + sum(x["amt"] for x in plan.get("deposits", []))
    # the broker's own balance when we have one; trade math only adds what opened since then
    now_real = plan.get("balance", start) + sum(b - a for (a, b, _), r in zip(rd, real)
                                                 if r.get("opened", "") > plan.get("balance_as_of", ""))
    L += ["<b>💵 REAL</b>", head(start, now_real)
          + (f" · Schwab {plan['balance_as_of'][5:].replace('-', '/')}" if plan.get("balance_as_of") else "")]
    L += [row(lab, a, b) for (a, b, lab), r in zip(rd, real) if r["status"] == "open"] + [""]

    eq = float(acct["equity"])
    L += ["<b>🤖 SETUP F · CALLS</b>", head(START, eq)]
    L += [row(occ_label(p["symbol"]) if len(p["symbol"]) > 6 else f"{p['symbol']} {int(float(p['qty']))} SH",
              float(p["cost_basis"]), float(p["market_value"])) for p in sorted(positions, key=lambda p: p["symbol"])] \
         or ["No open trades."]
    L.append("")

    ideas = [r for r in trades if r.get("acct") in IDEAS]
    idd = [dollars(r, START * RISK) for r in ideas]
    done = [(a, b, lab) for (a, b, lab), r in zip(idd, ideas) if r["status"] == "closed"]
    L += ["<b>💡 IDEAS</b>", head(START, START + sum(b - a for a, b, _ in idd))]
    L += [row(lab, a, b) for (a, b, lab), r in sorted(zip(idd, ideas), key=lambda x: x[0][0] - x[0][1])
          if r["status"] == "open"]
    if done: L.append(f"closed: " + " · ".join(f"{lab} {S(b - a)}" for a, b, lab in done))
    L += ["", "<i>F CALLS and IDEAS are paper · not real money</i>"]
    return "\n".join(L)


def recap(force=False):
    """Once a day after the close (or when forced): the paper account, to #paper-portfolio."""
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = str(datetime.date.today())
    after_close = datetime.datetime.now(datetime.timezone.utc).hour >= 20
    if not force and (not after_close or state.get("_recap") == today): return
    d = json.load(open(os.path.join(DOCS, "data.json")))
    if discord(recap_text(call("/account"), call("/positions"), d.get("open", []) + d.get("closed", [])), "paper"):
        state["_recap"] = today; json.dump(state, open(STATE, "w")); print("auto: recap posted")


def main():
    if not call("/clock")["is_open"]: return print("auto: market closed")
    d = json.load(open(os.path.join(DOCS, "data.json")))
    rs = rows()
    # the legacy share positions do not block calls: this book tests F-with-calls on its own
    held = {r["symbol"] for r in rs if r.get("acct") == "bot" and r["status"] == "open"}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = datetime.date.today()
    budget = float(call("/account")["equity"]) * RISK
    for r, c in plan_calls(d["F"], held, state, today, budget):
        try:
            o = call("/orders", dict(symbol=occ(r["sym"], c["expiry"], c["strike"], pad=False),
                                     qty=str(c["n"]), side="buy", type="market", time_in_force="day"))
            time.sleep(2)
            fill = float(call(f"/orders/{o['id']}").get("filled_avg_price") or c["px"])
            state["call:" + r["sym"]] = str(today); log_call(rs, r, c, fill, c["n"], today)
            when = datetime.date.fromisoformat(c["expiry"]).strftime("%b %-d").upper()
            notify(f"<b>🤖 PAPER BOUGHT {r['sym']} ${r['px']:,.2f}</b>\n"
                   f"{when} · {c['n']} × ${c['strike']:g} CALL{'S' if c['n'] != 1 else ''} · ${fill:,.2f}\n"
                   f"${fill * 100 * c['n']:,.0f} TOTAL · 7% OF $100K\n"
                   f"SELL AT STOCK ${r['tgt']:,.2f} OR {EXIT_DTE} DAYS BEFORE EXPIRY\n"
                   f"<i>Setup F with calls · Alpaca paper · not real money</i>", "trades")
            print(f"auto: bought {c['n']} {r['sym']} {c['strike']:g}C {c['expiry']}")
        except Exception as e:
            print(f"auto: {r['sym']} failed: {str(e)[:80]}")
    json.dump(state, open(STATE, "w")); save(rs)


if __name__ == "__main__":
    if os.environ.get("AUTOTEST"):
        t = datetime.date(2026, 9, 28)
        C = dict(expiry="2027-01-15", strike=270, px=40.0, n=1, cost=4000, over=False)
        pick = lambda sym, px, budget: None if sym == "BIG" else C
        f = [dict(sym="BE", px=274, tgt=351.28), dict(sym="NOW", px=140, tgt=194.73), dict(sym="BIG", px=900, tgt=1000)]
        p = plan_calls(f, {"NOW"}, {"call:BE": "2026-09-10", "HOOD": "2026-09-27"}, t, 7000, pick)
        assert p == [], p                                   # BE bought 18 days ago, NOW held, BIG unaffordable
        p = plan_calls(f, set(), {}, t, 7000, pick); assert [x[0]["sym"] for x in p] == ["BE", "NOW"], p
        row = dict(target="351.28", expiry="2027-01-15")
        assert exit_due(row, 352, t) == "target" and exit_due(row, 300, t) is None
        assert exit_due(row, 300, datetime.date(2026, 12, 26)) == "21 days left"
        rs = []; log_call(rs, f[0], C, 40.0, 1, t)
        assert rs[0]["acct"] == "bot" and rs[0]["kind"] == "call" and rs[0]["strike"] == "270"
        tr = [dict(acct="real", kind="shares", symbol="NOW", entry="138.26", now=140.26, contracts="1", status="open", opened="2026-09-24"),
              dict(acct="small", kind="call", symbol="QCOM", strike="240", expiry="2026-11-20", contracts="1",
                   entry="3.72", now=6.35, status="open"),
              dict(acct="paper", kind="call", symbol="BABA", strike="109", expiry="2026-10-02", contracts="1",
                   entry="4.25", exit="8.35", status="closed", closed="2026-09-21")]
        r = recap_text({"equity": "100500"}, [{"symbol": "BE270115C00270000", "qty": "1", "cost_basis": "4000", "market_value": "4500"}],
                       tr, dict(start=8355.13, started="2026-09-20", deposits=[], balance=8355.13, balance_as_of="2026-09-20"))
        assert "START $8,355.13 → NOW <b>$8,357.13</b> · +$2.00 · +0.02% · Schwab 09/20" in r, r
        assert "BE 270C 01/15 · $4,000.00 → <b>$4,500.00</b>" in r
        assert "💡 IDEAS</b>\nSTART $100,000.00 → NOW <b>$100,673.00</b> · +$673.00" in r, r
        assert "closed: BABA 109C 10/02 +$410.00" in r
        print("autotrade ok")
    elif sys.argv[1:] == ["sync"]:
        sync(); recap()
    elif sys.argv[1:] == ["recap"]:
        recap(force=True)
    else:
        main()
