#!/usr/bin/env python3
"""Forward paper log for the Phase-3 put hedge (StockCharter thesis/puts/hedge2-rules.md, locked 2026-11-06).
Paper only. Never places orders. Not a Ledger rule.

Each day after the close:
  V1  every QQQ 10/30-day EMA cross down (control)
  V2  the cross AND VIX up 2+ points over the prior 5 sessions (the idea under test)
  RND one random session a month (control)
Each row buys one QQQ at-the-money put, nearest monthly expiry 60-95 days out, at the next morning's first real print
(+1% half-spread, min $0.05). Exit at the first daily close print >= 2x the entry (-1% half-spread), else the next open
after the 10 crosses back above the 30, else expiry at intrinsic value. Marks are real Alpaca prints only.
Log: docs/hedge-log.json. Pass line (decided in the rules): >= 8 triggers before any verdict; V2 must beat both controls.
"""
import datetime as dt, json, os, random, sys, urllib.request
from zoneinfo import ZoneInfo
import scan_daily as S                                            # bars(): daily closes from Yahoo
ET, CT = ZoneInfo("America/New_York"), ZoneInfo("America/Chicago")
LOG = os.environ.get("LEDGER_HEDGE_LOG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "hedge-log.json")
START = "2026-11-06"; HS, FL, FEE, BUDGET = 0.01, 0.05, 0.65, 1.5   # half-spread, floor, per-contract fee, $ per $100 of account


def prints(o, start, end):
    """5-minute option prints [(t_et, o, h, l, c)]; end clipped to 20 minutes ago (the feed's delay rule)."""
    hd = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}
    end = min(end, dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)); out, tok = [], None
    while True:
        u = (f"https://data.alpaca.markets/v1beta1/options/bars?symbols={o}&timeframe=5Min&start={start:%Y-%m-%dT%H:%M:%SZ}&end={end:%Y-%m-%dT%H:%M:%SZ}&limit=10000"
             + (f"&page_token={tok}" if tok else ""))
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=30))
        for b in (j.get("bars") or {}).get(o, []):
            out.append((dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET), b["o"], b["h"], b["l"], b["c"]))
        tok = j.get("next_page_token")
        if not tok: return out


def ema(v, n):
    k = 2 / (n + 1); e = v[0]; o = []
    for x in v: e = x * k + e * (1 - k); o.append(e)
    return o


def load():
    try: return json.load(open(LOG))
    except FileNotFoundError: return {"start": START, "rows": []}


def contract(d, spot):
    """ATM QQQ put, nearest monthly expiry 60-95 days after d (Alpaca contracts list). None without keys."""
    k, sec = os.environ.get("ALPACA_KEY"), os.environ.get("ALPACA_SECRET")
    if not (k and sec): return None
    lo, hi = (dt.date.fromisoformat(d) + dt.timedelta(60)).isoformat(), (dt.date.fromisoformat(d) + dt.timedelta(95)).isoformat(); cs = []; tok = None
    while True:
        u = f"https://paper-api.alpaca.markets/v2/options/contracts?underlying_symbols=QQQ&status=active&type=put&expiration_date_gte={lo}&expiration_date_lte={hi}&limit=10000" + (f"&page_token={tok}" if tok else "")
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec}), timeout=30))
        cs += [x for x in j.get("option_contracts") or [] if x.get("root_symbol", "QQQ") == "QQQ"]; tok = j.get("next_page_token")
        if not tok: break
    if not cs: return None
    exps = sorted({x["expiration_date"] for x in cs}); monthly = [e for e in exps if 15 <= int(e[8:]) <= 21]; e = (monthly or exps)[0]
    best = min([x for x in cs if x["expiration_date"] == e], key=lambda x: abs(float(x["strike_price"]) - spot))
    return dict(occ=best["symbol"], expiry=e, strike=float(best["strike_price"]))


def score(r, dates, close, e10, e30, prints):
    """Fill, mark and exit one row from real prints. prints = [(t_et, o, h, l, c)] from the fill day on. Edits r in place."""
    i = dates.index(r["d"]); fill_day = dates[i + 1] if i + 1 < len(dates) else None
    if not fill_day: return
    day = {}
    for t, o, h, l, c in prints:
        k = t.date().isoformat()
        if (9, 30) <= (t.hour, t.minute) < (16, 0): day.setdefault(k, []).append((o, c))
    if fill_day not in day: r["status"] = "waiting for the first print"; return
    r["fill_day"] = fill_day; r["entry"] = round(day[fill_day][0][0] * (1 + HS) + 0, 2) if day[fill_day][0][0] * HS >= FL else round(day[fill_day][0][0] + FL, 2)
    r["marks"] = {k: round(v[-1][1] - max(HS * v[-1][1], FL), 2) for k, v in sorted(day.items()) if k > fill_day or len(v) > 1}
    cross_back = next((x for x in dates[i + 1:] if e10[dates.index(x)] > e30[dates.index(x)]), None) if r["kind"] != "RND" else None
    for k, bid in sorted(r["marks"].items()):
        if k > r["expiry"]: break
        if bid >= 2 * r["entry"]: r.update(status="closed", exit_day=k, exit=bid, why="2x"); break
        if cross_back and k > cross_back and k in dates: r.update(status="closed", exit_day=k, exit=round(day[k][0][0] * (1 - HS), 2) if k in day else bid, why="cross back (next open)"); break
    else:
        if dates[-1] > r["expiry"]: r.update(status="closed", exit_day=r["expiry"], exit=round(max(0.0, r["strike"] - close[dates.index(r["expiry"])]), 2) if r["expiry"] in dates else 0.0, why="expiry")
        else: r["status"] = "open"; r["last"] = r["marks"][max(r["marks"])] if r["marks"] else None
    if r["status"] == "closed":
        r["mult"] = round(r["exit"] / r["entry"], 2); n = BUDGET / (r["entry"] * 100)
        r["pnl_contract"] = round((r["exit"] - r["entry"]) * 100 - 2 * FEE, 2); r["pnl_per100"] = round(n * r["pnl_contract"], 2)


def nightly(today=None, fetch=None, cfetch=contract):
    L = load(); Q = S.bars("QQQ"); V = S.bars("^VIX")
    dates = [b[0] for b in Q]; close = [b[1] for b in Q]; e10, e30 = ema(close, 10), ema(close, 30); vix = {b[0]: b[1] for b in V}
    today = today or dates[-1]
    if today < START or today not in dates: return L
    i = dates.index(today); have = {(r["kind"], r["d"]) for r in L["rows"]}
    new = []
    if e10[i] < e30[i] and e10[i - 1] >= e30[i - 1]:
        new.append("V1")
        if vix.get(today) is not None and vix.get(dates[i - 5]) is not None and vix[today] - vix[dates[i - 5]] >= 2.0: new.append("V2")
    month = [x for x in dates if x[:7] == today[:7]]
    if month.index(today) == random.Random(today[:7]).randrange(min(20, 21)): new.append("RND")   # one random session a month, fixed by the month name
    for kind in new:
        if (kind, today) in have: continue
        c = cfetch(today, close[i])
        if not c: print("hedge watch: no contract (keys?)", kind, today); continue
        L["rows"].append(dict(kind=kind, d=today, spot=round(close[i], 2), vix=vix.get(today), status="pending", **c))
    fetch = fetch or prints
    for r in L["rows"]:
        if r["status"] == "closed": continue
        start = dt.datetime.fromisoformat(r["d"] + "T00:00:00+00:00"); end = dt.datetime.now(dt.timezone.utc)
        try: P = fetch(r["occ"], start, end)
        except Exception as ex: print("hedge watch: prints failed", r["occ"], ex); continue
        score(r, dates, close, e10, e30, P)
    L["asof"] = today; L["summary"] = summary(L["rows"]); L["state"] = dict(e10=round(e10[i], 2), e30=round(e30[i], 2), below="10 is below 30" if e10[i] < e30[i] else "10 is above 30", vix=vix.get(today))
    os.makedirs(os.path.dirname(LOG), exist_ok=True); json.dump(L, open(LOG, "w"), indent=1); return L


def summary(rows):
    out = {}
    for kind in ("V2", "V1", "RND"):
        R = [r for r in rows if r["kind"] == kind]; C = [r for r in R if r["status"] == "closed"]
        out[kind] = dict(triggers=len(R), closed=len(C), open=len(R) - len(C), won=sum(r["pnl_contract"] > 0 for r in C), total_per100=round(sum(r["pnl_per100"] for r in C), 2), total_contract=round(sum(r["pnl_contract"] for r in C), 2), doubled=sum(r.get("why") == "2x" for r in C))
    v2 = out["V2"]; out["verdict"] = "not enough (needs 8 V2 triggers)" if v2["triggers"] < 8 else ("V2 ahead of both controls" if v2["total_per100"] > max(out["V1"]["total_per100"], out["RND"]["total_per100"]) and v2["total_per100"] > 0 else "V2 not ahead")
    return out


def line():
    """One line for the weekend report."""
    L = load(); s = L.get("summary")
    if not s: return "HEDGE PAPER LOG: started, no triggers yet"
    return "HEDGE PAPER LOG (paper, not a rule): " + " · ".join(f"{k} {v['triggers']} trig, {v['won']}/{v['closed']} won, {v['total_per100']:+.2f}/$100" for k, v in s.items() if k != "verdict") + f" · {s['verdict']}"


def selftest():
    import tempfile; global LOG; LOG = os.path.join(tempfile.mkdtemp(), "h.json")
    base = dt.date(2026, 11, 2); dates = [(base + dt.timedelta(i)).isoformat() for i in range(0, 40) if (base + dt.timedelta(i)).weekday() < 5]
    close = [100 + i for i in range(10)] + [110 - 2 * i for i in range(len(dates) - 10)]            # up then straight down: cross down fires
    e10, e30 = ema(close, 10), ema(close, 30); i = next(j for j in range(1, len(dates)) if e10[j] < e30[j] and e10[j - 1] >= e30[j - 1])
    r = dict(kind="V2", d=dates[i], occ="X", expiry=dates[-1], strike=round(close[i]), status="pending")
    def P(px_by_day): return [(dt.datetime.fromisoformat(k + "T10:00:00").replace(tzinfo=ET), p, p, p, p) for k, p in px_by_day] + [(dt.datetime.fromisoformat(k + "T15:55:00").replace(tzinfo=ET), p, p, p, p) for k, p in px_by_day]
    score(r, dates, close, e10, e30, P([(dates[i + 1], 5.0), (dates[i + 2], 7.0), (dates[i + 3], 10.5)]))
    assert r["status"] == "closed" and r["why"] == "2x" and r["entry"] == 5.05 and r["exit_day"] == dates[i + 3], r
    close2 = close[:i + 1] + [close[i] + 3 * k for k in range(1, len(dates) - i)]                 # bounces: cross back, exit next open
    e10b, e30b = ema(close2, 10), ema(close2, 30); r2 = dict(kind="V1", d=dates[i], occ="X", expiry=dates[-1], strike=round(close[i]), status="pending")
    score(r2, dates, close2, e10b, e30b, P([(dates[i + k], 5.0 - 0.3 * k) for k in range(1, len(dates) - i)]))
    assert r2["status"] == "closed" and r2["why"].startswith("cross back") and r2["pnl_contract"] < 0, r2
    r3 = dict(kind="RND", d=dates[i], occ="X", expiry=dates[-1], strike=round(close[i]), status="pending"); score(r3, dates, close, e10, e30, [])
    assert r3["status"] == "waiting for the first print"
    s = summary([r, r2, r3]); assert s["verdict"].startswith("not enough") and s["V2"]["doubled"] == 1
    print("hedge_watch selftest OK (3 rows: 2x exit, cross-back exit, waiting)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "nightly"
    if cmd == "selftest": selftest()
    elif cmd == "line": print(line())
    elif cmd == "nightly":
        now = dt.datetime.now(CT)
        if now.weekday() >= 5 or now.hour * 60 + now.minute >= 15 * 60 + 20 or os.environ.get("LEDGER_HEDGE_FORCE"):
            L = nightly(); print(line())
        else: print("hedge watch: before the close, skipped")
