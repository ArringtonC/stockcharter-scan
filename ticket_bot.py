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


def lock_stop(fill, peak):
    """Arrington 2026-10-12: at 2x don't sell -- set a stop that locks the gain. Once the option has
    been worth 2x, the stop is the higher of +50% (1.5x) and 75% of its best price since; else None."""
    return max(1.5 * fill, 0.75 * peak) if peak >= 2.0 * fill else None


def exit_reason(fill, bid, expiry, today, peak=0.0, tp=None):
    """Pure: why to sell now, or None. peak = the best bid seen since the buy. tp = a take-profit
    multiple (1.5 = the +50% bracket the ticket book uses since 2026-10-12)."""
    if tp and bid >= tp * fill: return f"take profit (+{100 * (tp - 1):.0f}%)"
    lock = lock_stop(fill, max(peak, bid))
    if lock is not None and bid <= lock: return f"locked-gain stop (${lock:.2f})"
    if lock is None and bid <= 0.5 * fill: return "stop (-50%)"
    if (datetime.date.fromisoformat(expiry) - today).days <= 2: return "2 days to expiry"
    return None


IDEAS = ("paper", "small", "big")


def manage(rs, state, today, quote_fn, sell_fn, label, tp=None):
    """Exits for every open call in a book: track the best bid, announce 2x once, sell on a reason.
    sell_fn(r, bid) -> fill or None. Pure apart from what the callables do."""
    out = []
    for r in rs:
        if r["opened"] == str(today): continue           # never a day trade
        b, _ = quote_fn(r)
        if not b: continue
        k = "pk:" + r["id"]; peak = max(float(state.get(k, 0)), b); state[k] = peak; fill = float(r["entry"])
        if peak >= 2 * fill and not state.get("2x:" + r["id"]):
            state["2x:" + r["id"]] = str(today)
            notify(f"<b>🤖 {label} · {r['symbol']} ${float(r['strike']):g}C HIT 2X · ${b:.2f}</b>\n"
                   f"NOT SELLING · STOP NOW LOCKS ${lock_stop(fill, peak):.2f} (+{100 * (lock_stop(fill, peak) / fill - 1):.0f}%) AND RISES WITH IT\n"
                   f"<i>paper · not real money</i>", "paper")
        long = (datetime.date.fromisoformat(r["expiry"]) - datetime.date.fromisoformat(r["opened"])).days > 60
        why = exit_reason(fill, b, r["expiry"], today, peak, None if long else tp)
        if not why: continue
        px = sell_fn(r, b)
        if px is None: continue
        r.update(status="closed", closed=str(today), exit=f"{px:.2f}", pl_pct=f"{(px / fill - 1) * 100:.1f}",
                 note=r.get("note", "") + f" Sold: {why}.")
        notify(f"<b>🤖 {label} · SOLD {r['symbol']} ${float(r['strike']):g}C · {why.upper()}</b>\n"
               f"${fill:.2f} → ${px:.2f} · {(px / fill - 1) * 100:+.0f}% · best was ${peak:.2f}\n"
               f"<i>paper · not real money</i>", "paper")
        out.append(r)
    return out


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
    def sell(r, b):                                   # a real paper order for the ticket book
        o = call("/orders", dict(symbol=occ(r["symbol"], r["expiry"], r["strike"], pad=False), qty=r["contracts"],
                                 side="sell", type="limit", limit_price=f"{b:.2f}", time_in_force="day"))
        return settle(o["id"])
    q = lambda r: quote(r["symbol"], r["expiry"], float(r["strike"]))
    mine = [r for r in rs if r.get("acct") == BOOK and r["status"] == "open"]
    manage(mine, state, today, q, sell, "PAPER TICKET", tp=1.5)   # the same +50/-50 bracket as the trade manager
    # the Ideas book: same exits, booked at the bid (ideas are a scorecard, not Alpaca orders)
    manage([r for r in rs if r.get("acct") in IDEAS and r.get("kind") == "call" and r["status"] == "open" and r.get("expiry")],
           state, today, q, lambda r, b: b, "PAPER IDEA")
    mine = [r for r in mine if r["status"] == "open"]
    if mine: json.dump(state, open(STATE, "w")); save(rs); return print("tickets: one open already")
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
        import trade_manager as TM                     # the same gate he uses in #ask-ledger
        sig, _ = TM.signal(sym); dte = (datetime.date.fromisoformat(c["expiry"]) - today).days
        v, gwhy = TM.gate(sym, dte, c["ask"] * 100, CAP, sig, False, (c["ask"] - c["bid"]) / c["ask"])
        if v != "APPROVED": print(f"tickets: {sym} -- gate says {v}: {gwhy}"); continue
        o = call("/orders", dict(symbol=occ(sym, c["expiry"], c["strike"], pad=False), qty="1", side="buy",
                                 type="limit", limit_price=f"{c['mid']:.2f}", time_in_force="day"))
        px = settle(o["id"])
        if px is None: print(f"tickets: {sym} buy did not fill at the mid"); continue
        state["tk:" + sym] = str(today)
        rs.append(dict(id=f"{today}-{sym}-tk", kind="call", acct=BOOK, opened=str(today), symbol=sym, setup=why[:20],
                       entry=f"{px:.2f}", target="", strike=f"{c['strike']:g}", expiry=c["expiry"], contracts="1", status="open",
                       note=f"Ticket book ({why}). Limit at the mid. Exits: +50% take profit, -50% stop, or 2 days to expiry (the trade-manager bracket)."))
        when = datetime.date.fromisoformat(c["expiry"]).strftime("%b %-d").upper()
        notify(f"<b>🤖 PAPER TICKET · BOUGHT {sym} {when} ${c['strike']:g}C · ${px:.2f}</b>\n"
               f"WHY: {why} · ${px * 100:,.0f} (cap $850)\n"
               f"STOP ${px * 0.5:.2f} · TAKE PROFIT ${px * 1.5:.2f} · OUT 2 DAYS BEFORE EXPIRY\n"
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
        assert exit_reason(5.0, 2.4, "2026-10-30", t) == "stop (-50%)"
        assert exit_reason(5.0, 7.6, "2026-10-30", t, 7.6, tp=1.5).startswith("take profit")
        assert exit_reason(5.0, 10.1, "2026-10-30", t, 10.1) is None                  # 2x: hold, stop locks +50%
        assert lock_stop(5.0, 10.0) == 7.5 and lock_stop(5.0, 12.0) == 9.0           # 1.5x floor, then 75% of the best
        assert exit_reason(5.0, 8.9, "2026-10-30", t, 12.0).startswith("locked-gain stop")
        assert exit_reason(5.0, 2.4, "2026-10-30", t, 12.0).startswith("locked-gain stop")   # after 2x, never back to -50%
        # QCOM 175C replay: entry 15.58, best 31.30 on 9-25 -> the lock is 23.48; the 9-28 close 16.12 sells it (+3%)
        assert exit_reason(15.58, 16.12, "2026-10-16", t, 31.30).startswith("locked-gain stop")
        assert exit_reason(5.0, 5.0, "2026-10-14", t) == "2 days to expiry" and exit_reason(5.0, 5.0, "2026-10-30", t) is None
        print("tickets ok")
    else:
        main()
