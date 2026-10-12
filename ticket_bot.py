#!/usr/bin/env python3
"""Paper options book that trades Ledger's tickets the way Arrington trades (2026-10-12).
PAPER ONLY (Alpaca paper). It exists to find out, before any real money, whether the tickets work.

  book     acct "bot-tickets", sized like his real account: $12,000, one call at a time, cost <= $850
  signals  newest first: a level alert that fired UP today (AMZN/SNAP/DASH-type breakouts), a pattern #7
           break (QQQ/SPY), then a Setup F fire. One trade per symbol per 30 days.
  contract expiry 14-35 days out and BEFORE the stock's next earnings; the at-the-money strike or the
           first one above it whose ask fits $850. Buy with a limit at the mid (never a market order).
  exits    checked only on later days -- so it never makes a day trade:
           option bid <= 50% of the fill (stop) · bid >= 2x the fill (take profit) · 2 days to expiry
Run after the scan: python3 ticket_bot.py   ·   self-test: TICKTEST=1 python3 ticket_bot.py"""
import datetime, json, os, time, urllib.request
from scan_daily import DOCS, notify, occ, bars, get
from autotrade import call, fill_price, rows, save, STATE

BOOK, CAP, START = "bot-tickets", 850.0, 12_000.0


def quote(sym, exp, k):
    o = occ(sym, exp, k, pad=False)
    rq = urllib.request.Request(f"https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols={o}",
                                headers={"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]})
    q = json.load(urllib.request.urlopen(rq, timeout=20)).get("quotes", {}).get(o, {})
    return (q.get("bp") or 0.0), (q.get("ap") or 0.0)


def next_earnings(sym, today, days=40):
    """The next earnings date within `days`, from Nasdaq's calendar; None if none found."""
    for i in range(days):
        x = today + datetime.timedelta(days=i)
        if x.weekday() >= 5: continue
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://api.nasdaq.com/api/calendar/earnings?date={x}", headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}), timeout=15))
            if any(z.get("symbol") == sym for z in ((r.get("data") or {}).get("rows") or [])): return x
        except Exception: pass
    return None


def pick(spot, contracts, quote_fn, earn, today, cap=CAP):
    """Pure: the contract to buy. contracts = [(expiry, strike)]. Latest expiry first (more time),
    14-35 days out and before earnings; strikes from at-the-money upward; first ask that fits."""
    ok = sorted({e for e, _ in contracts if 14 <= (datetime.date.fromisoformat(e) - today).days <= 35
                 and (earn is None or datetime.date.fromisoformat(e) < earn)}, reverse=True)
    for e in ok:
        ks = sorted(k for x, k in contracts if x == e and k >= spot * 0.995)[:4]
        for k in ks:
            b, a = quote_fn(e, k)
            if a and b and a * 100 <= cap and (a - b) / a <= 0.15:     # a sane spread, not a trap
                return dict(expiry=e, strike=k, bid=b, ask=a, mid=round((a + b) / 2, 2))
    return None


def exit_reason(fill, bid, expiry, today):
    """Pure: why to sell now, or None."""
    if bid <= 0.5 * fill: return "stop (-50%)"
    if bid >= 2.0 * fill: return "take profit (2x)"
    if (datetime.date.fromisoformat(expiry) - today).days <= 2: return "2 days to expiry"
    return None


def signals(d, today):
    """Newest confirmations first: level breakouts fired today, pattern #7, then Setup F."""
    out = []
    L = json.load(open(os.path.join(DOCS, "levels.json"))) if os.path.exists(os.path.join(DOCS, "levels.json")) else []
    for x in L:
        f = x.get("fired") or {}
        if f.get("way") == "up" and str(f.get("at", ""))[:10] == str(today): out.append((x["symbol"], f"level: {x['pattern'][:60]}"))
    out += [(x["sym"], f"pattern #7 {x['tf']}") for x in d.get("ihs", [])]
    out += [(r["sym"], "Setup F") for r in d.get("F", [])]
    return out


def settle(oid, wait=60):
    """Fill price, or None. Cancels if not filled in time, then checks again: a fill can land
    between the wait and the cancel (it did on 2026-10-12, ZS 235C)."""
    px = fill_price(oid, None, wait=wait)
    if px is not None: return px
    try: call(f"/orders/{oid}", method="DELETE")
    except Exception: pass
    time.sleep(2); o = call(f"/orders/{oid}")
    return float(o["filled_avg_price"]) if o.get("filled_avg_price") else None


def main():
    if not call("/clock")["is_open"]: return print("tickets: market closed")
    today = datetime.date.today(); rs = rows(); state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    mine = [r for r in rs if r.get("acct") == BOOK and r["status"] == "open"]
    for r in mine:                                   # exits first, never on the buy day
        if r["opened"] == str(today): continue
        b, _ = quote(r["symbol"], r["expiry"], float(r["strike"]))
        why = exit_reason(float(r["entry"]), b, r["expiry"], today)
        if not why: continue
        o = call("/orders", dict(symbol=occ(r["symbol"], r["expiry"], r["strike"], pad=False), qty=r["contracts"],
                                 side="sell", type="limit", limit_price=f"{b:.2f}", time_in_force="day"))
        px = settle(o["id"])
        if px is None: print(f"tickets: {r['symbol']} sell did not fill"); continue
        r.update(status="closed", closed=str(today), exit=f"{px:.2f}", pl_pct=f"{(px / float(r['entry']) - 1) * 100:.1f}",
                 note=r["note"] + f" Sold: {why}.")
        notify(f"<b>🤖 PAPER TICKET · SOLD {r['symbol']} ${float(r['strike']):g}C · {why.upper()}</b>\n"
               f"${float(r['entry']):.2f} → ${px:.2f} · {(px / float(r['entry']) - 1) * 100:+.0f}%\n"
               f"<i>CONFIRMED PAPER FILL · ticket book · not real money</i>", "paper")
        mine = []
    if mine: save(rs); return print("tickets: one open already")
    d = json.load(open(os.path.join(DOCS, "data.json")))
    for sym, why in signals(d, today):
        last = state.get("tk:" + sym)
        if last and (today - datetime.date.fromisoformat(last)).days < 30: continue
        spot = bars(sym, "5d")[-1][1]
        cs = call(f"/options/contracts?underlying_symbols={sym}&type=call&limit=1000&expiration_date_gte={today + datetime.timedelta(14)}"
                  f"&expiration_date_lte={today + datetime.timedelta(35)}&strike_price_gte={spot * 0.99:.2f}&strike_price_lte={spot * 1.1:.2f}"
                  ).get("option_contracts", [])
        c = pick(spot, [(x["expiration_date"], float(x["strike_price"])) for x in cs],
                 lambda e, k: quote(sym, e, k), next_earnings(sym, today), today)
        if not c: print(f"tickets: {sym} -- no call fits (cap, earnings, spread)"); continue
        o = call("/orders", dict(symbol=occ(sym, c["expiry"], c["strike"], pad=False), qty="1", side="buy",
                                 type="limit", limit_price=f"{c['mid']:.2f}", time_in_force="day"))
        px = settle(o["id"])
        if px is None: print(f"tickets: {sym} buy did not fill at the mid"); continue
        state["tk:" + sym] = str(today)
        rs.append(dict(id=f"{today}-{sym}-tk", kind="call", acct=BOOK, opened=str(today), symbol=sym, setup=why[:20],
                       entry=f"{px:.2f}", target="", strike=f"{c['strike']:g}", expiry=c["expiry"], contracts="1", status="open",
                       note=f"Ticket book ({why}). Limit at the mid. Exits: -50% stop, 2x take profit, or 2 days to expiry."))
        when = datetime.date.fromisoformat(c["expiry"]).strftime("%b %-d").upper()
        notify(f"<b>🤖 PAPER TICKET · BOUGHT {sym} {when} ${c['strike']:g}C · ${px:.2f}</b>\n"
               f"WHY: {why} · ${px * 100:,.0f} (cap $850)\n"
               f"STOP ${px * 0.5:.2f} · TAKE PROFIT ${px * 2:.2f} · OUT 2 DAYS BEFORE EXPIRY\n"
               f"<i>CONFIRMED PAPER FILL · ticket book · not real money</i>", "paper")
        print(f"tickets: bought {sym} {c['strike']:g}C {c['expiry']} at {px} ({why})")
        break
    json.dump(state, open(STATE, "w")); save(rs)


if __name__ == "__main__":
    if os.environ.get("TICKTEST"):
        t = datetime.date(2026, 10, 12)
        cs = [("2026-10-16", 260.0), ("2026-10-30", 260.0), ("2026-10-30", 265.0), ("2026-11-13", 260.0), ("2026-11-20", 260.0)]
        q = {("2026-10-30", 260.0): (5.0, 5.4), ("2026-10-30", 265.0): (3.0, 3.2), ("2026-11-13", 260.0): (9.0, 9.4)}.get
        qf = lambda e, k: q((e, k)) or (0, 0)
        assert pick(259.0, cs, qf, None, t)["expiry"] == "2026-10-30"          # Nov 13 costs $940 > cap; Nov 20 too far
        assert pick(259.0, cs, qf, datetime.date(2026, 10, 29), t) is None     # Oct 30 is after earnings -> nothing
        assert pick(259.0, cs, lambda e, k: (1.0, 5.4) if k == 260 else qf(e, k), None, t)["strike"] == 265.0   # wide spread skipped
        assert exit_reason(5.0, 2.4, "2026-10-30", t) == "stop (-50%)" and exit_reason(5.0, 10.1, "2026-10-30", t) == "take profit (2x)"
        assert exit_reason(5.0, 5.0, "2026-10-14", t) == "2 days to expiry" and exit_reason(5.0, 5.0, "2026-10-30", t) is None
        print("tickets ok")
    else:
        main()
