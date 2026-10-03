#!/usr/bin/env python3
"""Paper auto-trader: pure Setup F, exactly as written, on the Alpaca PAPER account ($100k).

Since 2026-09-27 (Arrington: "lets do the pure setup F whatever it is"):
  buy    each F fire -> shares at market, 7% of account equity (his own position rule).
         One buy per name per 30 days.
  hold   no stop. Target = the prior high, set at entry.
  floor  when the stock REACHES the target, do not sell: place a stop at the target and
         let it run. Sell only if it falls back to that floor, or after 252 sessions.
Second book, same signals (acct bot-leaps, since 2026-09-27): the at-the-money call nearest
730 days out (thesis/leaps.md: 87% win, 9% wiped out -- the best F option structure tested),
7% of a $100k book per trade, sold when the stock reaches the target or 30 days before expiry. The 4 share positions bought
09-23/24 (acct paper-auto, $600 each) now follow the same floor rule."""
import csv, json, os, re, sys, time, datetime, urllib.request
from scan_daily import notify, discord, DOCS, TRADES, TCOLS, PLAN, bars, alpaca_quote, occ

API = "https://paper-api.alpaca.markets/v2"     # ponytail: hard-coded paper; real money is a separate decision
CAP = 600            # the old shares version, kept for sizing the Ideas share rows
RISK = 0.07          # 7% of equity per trade -- Arrington's own rule, applied to $100k
STATE = os.path.expanduser("~/.ledger-auto.json")  # outside the public repo


def call(path, body=None, method=None):
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"],
         "Content-Type": "application/json"}
    rq = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                headers=h, method=method or ("POST" if body else "GET"))
    return json.load(urllib.request.urlopen(rq, timeout=20))


def plan(fires, held, state, today, budget=CAP):
    """Which F fires to buy, and how many shares. Pure, so it can be tested."""
    out = []
    for r in fires:
        sym, px = r["sym"], r["px"]
        last = state.get(sym)
        if sym in held or (last and (today - datetime.date.fromisoformat(last)).days < 30): continue
        qty = int(budget // px)
        if qty >= 1: out.append(dict(sym=sym, qty=qty, px=px, tgt=round(r["tgt"], 2)))
    return out


def floor_action(row, highs_since, sessions, has_stop):
    """What pure F wants done with an open position now. Pure, so it can be tested.
    highs_since: daily highs after the entry date."""
    if sessions >= 252: return "sell"                      # the time limit
    if not has_stop and max(highs_since, default=0) >= float(row["target"]): return "floor"
    return None


def leap_pick(sym, spot, budget, today, contracts=None, ask=None):
    """The LEAPS a fire implies: expiry >= 540 days, nearest to 730; strike nearest spot among
    contracts with open interest >= 100; as many as the budget buys at the ask. None if none fits."""
    if contracts is None:
        q = f"/options/contracts?underlying_symbols={sym}&type=call&limit=1000" \
            f"&expiration_date_gte={today + datetime.timedelta(540)}&strike_price_gte={spot * .85:.0f}&strike_price_lte={spot * 1.15:.0f}"
        contracts = call(q).get("option_contracts", [])
    ok = [c for c in contracts if int(c.get("open_interest") or 0) >= 100]
    if not ok: return None
    goal = today + datetime.timedelta(730)
    exp = min({c["expiration_date"] for c in ok}, key=lambda e: abs((datetime.date.fromisoformat(e) - goal).days))
    c = min((c for c in ok if c["expiration_date"] == exp), key=lambda c: abs(float(c["strike_price"]) - spot))
    k = float(c["strike_price"])
    px = ask(sym, exp, k) if ask else leap_ask(sym, exp, k)
    if not px: return None
    n = int(budget // (px * 100))
    return dict(expiry=exp, strike=k, px=px, n=n, cost=px * 100 * n) if n >= 1 else None


def leap_ask(sym, exp, k):
    """The ask, not the mid: what a market buy actually pays."""
    o = occ(sym, exp, k, pad=False)
    try:
        rq = urllib.request.Request(f"https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols={o}",
                                    headers={"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]})
        q = json.load(urllib.request.urlopen(rq, timeout=20))["quotes"][o]
        return float(q["ap"]) if q.get("ap") else None
    except Exception:
        return alpaca_quote(sym, exp, k)


def rows():
    return list(csv.DictReader(open(TRADES)))


def save(rs):
    with open(TRADES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TCOLS); w.writeheader()
        for r in rs: w.writerow({k: r.get(k, "") for k in TCOLS})


def log_buy(rs, t, fill, today, acct="paper-auto", note=None):
    """The bot's buy as a trades.csv row, so the page and the cards see it."""
    rs.append(dict(id=f"{today}-{t['sym']}-{'auto' if acct == 'paper-auto' else 'f'}", kind="shares", acct=acct,
                   opened=str(today), symbol=t["sym"], setup="F", entry=f"{fill:.2f}", target=f"{t['tgt']:.2f}",
                   contracts=str(t["qty"]), status="open",
                   note=note or f"Paper bot. Setup F fired; {t['qty']} shares at market, GTC sell at the target."))


def close_filled(rs, sells):
    """sells: {symbol: (price, date)} for take-profit fills. Closes the matching open rows."""
    for r in rs:
        if r.get("acct") in ("paper-auto", "bot") and r["status"] == "open" and r["symbol"] in sells:
            px, day = sells[r["symbol"]]
            r.update(status="closed", closed=day, exit=f"{px:.2f}",
                     pl_pct=f"{(px / float(r['entry']) - 1) * 100:.1f}")
    return rs


def fill_price(oid, fallback, wait=30):
    """The broker's real fill. Polls up to `wait` seconds; None if it never filled."""
    for _ in range(wait):
        o = call(f"/orders/{oid}")
        if o.get("filled_avg_price"): return float(o["filled_avg_price"])
        if o.get("status") in ("canceled", "expired", "rejected"): return None
        time.sleep(1)
    return fallback


def sync():
    """Before the scan: real entries, floors at the target, the 252-session limit, and
    closes for anything Alpaca has sold."""
    rs = rows(); pos = {p["symbol"]: p for p in call("/positions")}
    mine = [r for r in rs if r.get("acct") in ("paper-auto", "bot") and r["status"] == "open"]
    for r in mine:   # a buy logged before its fill reported carries the scan price; the broker wins
        if r["symbol"] in pos: r["entry"] = f"{float(pos[r['symbol']]['avg_entry_price']):.2f}"
    if call("/clock")["is_open"]:
        orders = call("/orders?status=open&nested=true")
        for r in mine:
            if r["symbol"] not in pos: continue
            sym = r["symbol"]; mine_o = [o for o in orders if o["symbol"] == sym]
            # the old $600 bot put a take-profit limit at the target; pure F floors instead
            for o in mine_o:
                if o["side"] == "sell" and o["type"] == "limit":
                    call(f"/orders/{o['id']}", method="DELETE"); print(f"auto: {sym} take-profit removed, floor rule instead")
            has_stop = any(o["side"] == "sell" and o["type"] == "stop" for o in mine_o)
            after = [x for x in bars(sym, "2y") if x[0] > r["opened"]]
            act = floor_action(r, [x[2] for x in after], len(after), has_stop)
            qty = str(int(float(pos[sym]["qty"])))
            if act == "floor":
                call("/orders", dict(symbol=sym, qty=qty, side="sell", type="stop",
                                     stop_price=r["target"], time_in_force="gtc"))
                print(f"auto: {sym} reached ${r['target']} -- floor set there, letting it run")
                notify(f"<b>🤖 PAPER · {sym} HIT TARGET ${float(r['target']):,.2f}</b>\n"
                       f"FLOOR SET AT THE TARGET · LETTING IT RUN\n<i>pure Setup F · not real money</i>", "updates")
            elif act == "sell":
                for o in mine_o: call(f"/orders/{o['id']}", method="DELETE")
                call("/orders", dict(symbol=sym, qty=qty, side="sell", type="market", time_in_force="day"))
                print(f"auto: {sym} 252 sessions -- sold")
    # LEAPS: sell at the stock target or 30 days before expiry, on real fills
    if call("/clock")["is_open"]:
        today = datetime.date.today()
        for r in rs:
            if r.get("acct") != "bot-leaps" or r["status"] != "open": continue
            spot = bars(r["symbol"], "5d")[-1][1]
            dte = (datetime.date.fromisoformat(r["expiry"]) - today).days
            why = "target" if spot >= float(r["target"]) else "30 days left" if dte <= 30 else None
            if not why: continue
            o = call("/orders", dict(symbol=occ(r["symbol"], r["expiry"], r["strike"], pad=False),
                                     qty=r["contracts"], side="sell", type="market", time_in_force="day"))
            px = fill_price(o["id"], None)
            if px is None: continue
            r.update(status="closed", closed=str(today), exit=f"{px:.2f}",
                     pl_pct=f"{(px / float(r['entry']) - 1) * 100:.1f}", note=r["note"] + f" Sold: {why}.")
            print(f"auto: LEAPS {r['symbol']} sold ({why})")
    held = set(pos)
    want = {r["symbol"] for r in mine} - held
    sells = {}
    for sym in want:
        o = [x for x in call(f"/orders?status=closed&symbols={sym}&direction=desc&limit=20")
             if x["side"] == "sell" and x["status"] == "filled"]
        if o: sells[sym] = (float(o[0]["filled_avg_price"]), o[0]["filled_at"][:10])
    save(close_filled(rs, sells))
    if sells: print(f"auto: closed {sorted(sells)}")


# Arrington 2026-09-26: "we can do the real portfolio ... IDC". Flip to False if others join.
SHARE_REAL = True
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


def recap_text(acct, positions, trades, plan=PLAN, real=True):
    """Three books, each START -> NOW then its open trades. Pure, so it can be tested.
      REAL      his account, from the plan's start balance
      F CALLS   the Alpaca paper bot, $100k
      IDEAS     every other paper trade, scored as if in its own $100k account"""
    D = lambda x: f"${x:,.2f}"; S = lambda x: f"{'+' if x >= 0 else '−'}${abs(x):,.2f}"
    row = lambda lab, a, b: f"{lab} · {D(a)} → <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.0f}%"
    head = lambda a, b: f"START {D(a)} → NOW <b>{D(b)}</b> · {S(b - a)} · {(b / a - 1) * 100:+.2f}%"
    L = ["<b>📒 PORTFOLIOS</b>", ""]

    if not real: trades = [r for r in trades if r.get("acct") != "real"]
    show_real = real
    real = [r for r in trades if r.get("acct") == "real" and (r["status"] == "open" or r.get("closed", "") >= plan["started"])]
    rd = [dollars(r, CAP) for r in real]
    start = plan["start"] + sum(x["amt"] for x in plan.get("deposits", []))
    # the broker's own balance when we have one; trade math only adds what opened since then
    now_real = plan.get("balance", start) + sum(b - a for (a, b, _), r in zip(rd, real)
                                                 if r.get("opened", "") > plan.get("balance_as_of", ""))
    if show_real: L += ["<b>💵 REAL</b>", head(start, now_real)
          + (f" · Schwab {plan['balance_as_of'][5:].replace('-', '/')}" if plan.get("balance_as_of") else "")]
    if show_real: L += [row(lab, a, b) for (a, b, lab), r in zip(rd, real) if r["status"] == "open"] + [""]

    for title, accts in (("🤖 SETUP F", ("bot", "paper-auto")), ("🤖 SETUP F · LEAPS", ("bot-leaps",))):
        bk = [r for r in trades if r.get("acct") in accts]
        dd = [dollars(r, START * RISK) for r in bk]
        L += [f"<b>{title}</b>", head(START, START + sum(b - a for a, b, _ in dd))]
        L += [row(lab, a, b) for (a, b, lab), r in zip(dd, bk) if r["status"] == "open"] or ["No open trades."]
        done = [(a, b, lab) for (a, b, lab), r in zip(dd, bk) if r["status"] == "closed"]
        if done: L.append("closed: " + " · ".join(f"{lab} {S(b - a)}" for a, b, lab in done))
        L.append("")

    ideas = [r for r in trades if r.get("acct") in IDEAS]
    idd = [dollars(r, START * RISK) for r in ideas]
    done = [(a, b, lab) for (a, b, lab), r in zip(idd, ideas) if r["status"] == "closed"]
    L += ["<b>💡 IDEAS</b>", head(START, START + sum(b - a for a, b, _ in idd))]
    L += [row(lab, a, b) for (a, b, lab), r in sorted(zip(idd, ideas), key=lambda x: x[0][0] - x[0][1])
          if r["status"] == "open"]
    if done: L.append(f"closed: " + " · ".join(f"{lab} {S(b - a)}" for a, b, lab in done))
    L += ["", "<i>SETUP F and IDEAS are paper · not real money</i>"]
    return "\n".join(L)


def recap(force=False):
    """Once a day after the close (or when forced): the paper account, to #paper-portfolio."""
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = str(datetime.date.today())
    after_close = datetime.datetime.now(datetime.timezone.utc).hour >= 20
    dow = datetime.date.today().weekday()      # weekdays after the close, and in the Saturday weekend report
    due = (dow < 5 and after_close) or dow == 5
    if not force and (not due or state.get("_recap") == today): return
    d = json.load(open(os.path.join(DOCS, "data.json")))
    acct, pos, tr = call("/account"), call("/positions"), d.get("open", []) + d.get("closed", [])
    # Discord is a server other people may join: paper books only. The real account goes to
    # the private Telegram chat and nowhere else.
    notify(recap_text(acct, pos, tr), discord_too=False)
    if discord(recap_text(acct, pos, tr, real=SHARE_REAL), "paper"):
        state["_recap"] = today; json.dump(state, open(STATE, "w")); print("auto: recap posted")


def main():
    if not call("/clock")["is_open"]: return print("auto: market closed")
    d = json.load(open(os.path.join(DOCS, "data.json")))
    rs = rows()
    held = {r["symbol"] for r in rs if r.get("acct") in ("bot", "paper-auto") and r["status"] == "open"}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = datetime.date.today()
    budget = START * RISK   # two books share one Alpaca account; each sizes off its own $100k
    for t in plan(d["F"], held, state, today, budget):
        try:
            o = call("/orders", dict(symbol=t["sym"], qty=str(t["qty"]), side="buy", type="market", time_in_force="day"))
            fill = fill_price(o["id"], None)
            if fill is None: print(f"auto: {t['sym']} buy did not fill"); continue
            state[t["sym"]] = str(today)
            log_buy(rs, t, fill, today, acct="bot",
                    note=f"Pure Setup F. {t['qty']} shares, 7% of equity. Target ${t['tgt']:.2f}; no stop; "
                         f"floor at the target once reached; out at 252 sessions.")
            notify(f"<b>🤖 PAPER BOUGHT {t['sym']} ${fill:,.2f}</b>\n"
                   f"{t['qty']} SHARES · ${t['qty'] * fill:,.0f} · 7% OF THE ACCOUNT\n"
                   f"TARGET ${t['tgt']:,.2f} · +{(t['tgt'] / fill - 1) * 100:.0f}% · NO STOP\n"
                   f"<i>pure Setup F · Alpaca paper · not real money</i>", "trades")
            print(f"auto: bought {t['qty']} {t['sym']} at {fill}, target {t['tgt']}")
        except Exception as e:
            print(f"auto: {t['sym']} failed: {str(e)[:80]}")
    # second book: the same fires as 2-year calls, 7% of its own $100k
    lheld = {r["symbol"] for r in rs if r.get("acct") == "bot-leaps" and r["status"] == "open"}
    for f in d["F"]:
        sym = f["sym"]; last = state.get("leap:" + sym)
        if sym in lheld or (last and (today - datetime.date.fromisoformat(last)).days < 30): continue
        try:
            c = leap_pick(sym, f["px"], START * RISK, today)
            if not c: print(f"auto: LEAPS {sym} -- no liquid 2-year call fits ${START * RISK:,.0f}"); continue
            o = call("/orders", dict(symbol=occ(sym, c["expiry"], c["strike"], pad=False), qty=str(c["n"]),
                                     side="buy", type="market", time_in_force="day"))
            fill = fill_price(o["id"], None)
            if fill is None: print(f"auto: LEAPS {sym} buy did not fill"); continue
            state["leap:" + sym] = str(today)
            rs.append(dict(id=f"{today}-{sym}-leap", kind="call", acct="bot-leaps", opened=str(today), symbol=sym,
                           setup="F", entry=f"{fill:.2f}", target=f"{f['tgt']:.2f}", strike=f"{c['strike']:g}",
                           expiry=c["expiry"], contracts=str(c["n"]), status="open",
                           note=f"F LEAPS. {c['n']}x ${c['strike']:g}C {c['expiry']} (~2 years), 7% of a $100k book. "
                                f"Sells when the stock reaches ${f['tgt']:.2f} or 30 days before expiry."))
            when = datetime.date.fromisoformat(c["expiry"]).strftime("%b %-d %Y").upper()
            notify(f"<b>🤖 PAPER BOUGHT {sym} ${f['px']:,.2f}</b>\n"
                   f"{when} · {c['n']} × ${c['strike']:g} CALL{'S' if c['n'] != 1 else ''} · ${fill:,.2f}\n"
                   f"${fill * 100 * c['n']:,.0f} TOTAL · 7% OF $100K\n"
                   f"SELL WHEN STOCK HITS ${f['tgt']:,.2f}\n"
                   f"<i>Setup F LEAPS · Alpaca paper · not real money</i>", "trades")
            print(f"auto: LEAPS bought {c['n']} {sym} {c['strike']:g}C {c['expiry']} at {fill}")
        except Exception as e:
            print(f"auto: LEAPS {sym} failed: {str(e)[:80]}")
    json.dump(state, open(STATE, "w")); save(rs)


if __name__ == "__main__":
    if os.environ.get("AUTOTEST"):
        t = datetime.date(2026, 9, 28)
        f = [dict(sym="BE", px=274, tgt=351.28), dict(sym="NOW", px=140, tgt=194.73), dict(sym="BIG", px=9000, tgt=10000)]
        p = plan(f, {"NOW"}, {"BE": "2026-09-10"}, t, 7000)
        assert p == [], p                                   # BE bought 18 days ago, NOW held, BIG > $7k a share
        p = plan(f, set(), {}, t, 7000); assert [(x["sym"], x["qty"]) for x in p] == [("BE", 25), ("NOW", 50)], p
        row = dict(target="351.28")
        assert floor_action(row, [300, 340], 60, False) is None           # not there yet: hold, no stop
        assert floor_action(row, [300, 352], 60, False) == "floor"        # touched the target: set the floor
        assert floor_action(row, [300, 352], 61, True) is None            # floor already in place
        assert floor_action(row, [300], 252, False) == "sell"             # the 252-session limit
        rs = []; log_buy(rs, f[0] | {"qty": 25}, 274.0, t, acct="bot")
        assert rs[0]["acct"] == "bot" and rs[0]["id"].endswith("-BE-f") and rs[0]["contracts"] == "25"
        close_filled(rs, {"BE": (351.28, "2026-12-01")}); assert rs[0]["status"] == "closed"
        CS = [dict(expiration_date="2028-01-21", strike_price="140", open_interest=1897),
              dict(expiration_date="2028-12-15", strike_price="140", open_interest=167),
              dict(expiration_date="2028-12-15", strike_price="135", open_interest=146),
              dict(expiration_date="2029-01-19", strike_price="140", open_interest=49)]
        lp = leap_pick("NOW", 138, 7000, t, CS, ask=lambda s, e, k: 50.65)
        assert lp == dict(expiry="2028-12-15", strike=140.0, px=50.65, n=1, cost=5065.0), lp
        assert leap_pick("BE", 290, 7000, t, CS, ask=lambda s, e, k: 101.0) is None      # over budget: skip
        tr = [dict(acct="real", kind="shares", symbol="NOW", entry="138.26", now=140.26, contracts="1", status="open", opened="2026-09-24"),
              dict(acct="small", kind="call", symbol="QCOM", strike="240", expiry="2026-11-20", contracts="1",
                   entry="3.72", now=6.35, status="open"),
              dict(acct="paper", kind="call", symbol="BABA", strike="109", expiry="2026-10-02", contracts="1",
                   entry="4.25", exit="8.35", status="closed", closed="2026-09-21")]
        tr0 = [dict(acct="bot-leaps", kind="call", symbol="BE", strike="270", expiry="2028-01-21", contracts="1",
                    entry="40", now=45.0, status="open")]
        r = recap_text({"equity": "100500"}, [],
                       tr + tr0, dict(start=8355.13, started="2026-09-20", deposits=[], balance=8355.13, balance_as_of="2026-09-20"))
        assert "START $8,355.13 → NOW <b>$8,357.13</b> · +$2.00 · +0.02% · Schwab 09/20" in r, r
        assert "SETUP F · LEAPS</b>\nSTART $100,000.00 → NOW <b>$100,500.00</b>" in r and "BE 270C 01/21 · $4,000.00 → <b>$4,500.00</b>" in r, r
        assert "💡 IDEAS</b>\nSTART $100,000.00 → NOW <b>$100,673.00</b> · +$673.00" in r, r
        assert "closed: BABA 109C 10/02 +$410.00" in r
        pub = recap_text({"equity": "100500"}, [], tr, dict(start=8355.13, started="2026-09-20", deposits=[]), real=False)
        assert "REAL" not in pub and "8,355" not in pub and "NOW 1 SH" not in pub, pub
        print("autotrade ok")
    elif sys.argv[1:] == ["sync"]:
        sync(); recap()
    elif sys.argv[1:] == ["recap"]:
        recap(force=True)
    else:
        main()
