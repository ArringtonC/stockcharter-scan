#!/usr/bin/env python3
"""Setup F on the FULL current S&P 500 (2026-10-23). Research-identical rule (thesis/pit_test.py SIG + f_audit2.eligible):
  20-45% under the highest high of the 252 sessions BEFORE today · 10 EMA > 30 EMA · 50 EMA > 200 EMA
  (daily closes, Alpaca SIP, adjustment=all, as in the research) · revenue growth > 25% (last 4 quarters vs the
  prior 4, SEC 10-Q/10-K filed by today, 8 consecutive quarters, Q4 derived from the 10-K: rev_growth below, matching the
  research revenue file; scan_daily.revenue_growth skips Q4s and overstates growth, found 2026-10-23) · today's S&P 500 list (Wikipedia, the source of the research membership file).
Runs once per weekday after the close (15:20 CT or later); writes docs/f_full.json. Read-only: it places nothing and
changes no other book. The paper manager's separate "F FULL S&P" book reads this file.
  python3 f_full.py                 normal run (skips unless after the close and not yet done today)
  python3 f_full.py dry 2026-10-05  scan as of a past close (bars cut at that date; revenue as of today) -> stdout
  FFTEST=1 python3 f_full.py        self-test of the pure rule"""
import datetime as dt, json, os, re, sys, urllib.request
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(H, "docs", "f_full.json"); MEM = os.path.join(H, "docs", "sp500.json")
CT = ZoneInfo("America/Chicago")


def ema(v, n):
    k = 2 / (n + 1); e = v[0]; o = [e]
    for x in v[1:]: e = x * k + e * (1 - k); o.append(e)
    return o


def rule(b):
    """Pure. b = [(date, o, h, l, c)] up to and including the decision day. Returns dict or None (chart part only)."""
    if len(b) < 260: return None
    c = [x[4] for x in b]; i = len(b) - 1
    hi = max(x[2] for x in b[i - 252:i]); dd = 1 - c[i] / hi
    e10, e30, e50, e200 = (ema(c, n)[-1] for n in (10, 30, 50, 200))
    if 0.20 <= dd <= 0.45 and e10 > e30 and e50 > e200:
        return dict(px=c[i], dd=round(dd * 100, 1), tgt=hi, up=round((hi / c[i] - 1) * 100, 1))
    return None


def members():
    try:
        u = "https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw"
        txt = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Ledger/1.0 (stockcharter-scan)"}), timeout=30).read().decode()
        first = txt[:txt.index("|}")]
        now = sorted(set(re.findall(r"\{\{\s*(?:NyseSymbol|NasdaqSymbol|nyse|nasdaq|NYSE|NASDAQ|BZX|Cboe)\s*\|\s*([A-Z.\-]+)", first, re.I)))
        if len(now) >= 490: json.dump(dict(date=dt.date.today().isoformat(), symbols=now), open(MEM, "w")); return now, "wikipedia"
    except Exception: pass
    m = json.load(open(MEM)); return m["symbols"], f"cached {m['date']}"


def bars(syms, end):
    H_ = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}
    start = (dt.date.fromisoformat(end[:10]) - dt.timedelta(days=760)).isoformat(); out = {}
    for k in range(0, len(syms), 100):
        part = syms[k:k + 100]; tok = None
        while True:
            u = (f"https://data.alpaca.markets/v2/stocks/bars?symbols={','.join(part)}&timeframe=1Day&start={start}&end={end}"
                 f"&feed=sip&adjustment=all&limit=10000" + (f"&page_token={tok}" if tok else ""))
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H_), timeout=60))
            for s, v in (r.get("bars") or {}).items(): out.setdefault(s, []).extend((x["t"][:10], x["o"], x["h"], x["l"], x["c"]) for x in v)
            tok = r.get("next_page_token")
            if not tok: break
    return out


_cik = None
def rev_growth(sym, asof=None):
    """Last 4 quarters vs the prior 4, from SEC companyfacts, facts filed by `asof` (default today). Unlike
    scan_daily.revenue_growth, a missing Q4 is derived as the 10-K annual total minus that year's three quarters (SEC
    rarely reports Q4 alone), and the 8 quarters must be consecutive. This matches the research revenue file."""
    global _cik
    import scan_daily as S
    asof = asof or dt.date.today().isoformat()
    if _cik is None:
        m = S.get("https://www.sec.gov/files/company_tickers.json", S.SEC_UA); _cik = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in m.values()}
    c = _cik.get(sym.replace(".", "-"))
    if not c: return None
    g = S.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", S.SEC_UA).get("facts", {}).get("us-gaap", {})
    q, yr = {}, {}
    for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"):
        for unit, vals in g.get(tag, {}).get("units", {}).items():
            if unit != "USD": continue
            for x in vals:
                st, en, fl = x.get("start"), x.get("end"), x.get("filed")
                if x.get("form") not in ("10-Q", "10-K") or not (st and en and fl) or fl > asof: continue
                n = (dt.date.fromisoformat(en) - dt.date.fromisoformat(st)).days
                book = q if 60 <= n <= 120 else yr if 330 <= n <= 400 else None
                if book is not None and (en not in book or fl < book[en][0]): book[en] = (fl, x["val"], st)
    for en, (fl, val, st) in yr.items():                      # derive a missing Q4 from the annual figure
        if en in q: continue
        inside = [v for e, (f, v, s) in q.items() if st < e < en]
        if len(inside) == 3: q[en] = (fl, val - sum(inside), st)
    ser = sorted((en, v) for en, (fl, v, st) in q.items())[-8:]
    if len(ser) < 8: return None
    gaps = [(dt.date.fromisoformat(b[0]) - dt.date.fromisoformat(a[0])).days for a, b in zip(ser, ser[1:])]
    if any(not 75 <= x <= 105 for x in gaps): return None      # not 8 consecutive quarters: unknown, never guessed
    now = sum(v for _, v in ser[-4:]); prior = sum(v for _, v in ser[:4])
    return None if prior <= 0 else now / prior - 1


def scan(as_of=None):
    import scan_daily as S
    syms, src = members()
    now = dt.datetime.now(CT)
    end = (as_of + "T23:59:00Z") if as_of else (now - dt.timedelta(minutes=20)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    B = bars(syms, end); day = as_of or now.date().isoformat()
    F, chart_only, cnt = [], [], dict(members=len(syms), bars=0, stale=0, short=0, chart=0, rev_pass=0, rev_fail=0, rev_unknown=0)
    for s in syms:
        b = [x for x in B.get(s, []) if x[0] <= day]
        if not b: continue
        cnt["bars"] += 1
        if b[-1][0] != day: cnt["stale"] += 1; continue           # no bar for the decision day: no decision
        if len(b) < 260: cnt["short"] += 1; continue
        x = rule(b)
        if not x: continue
        cnt["chart"] += 1
        try: g = rev_growth(s)
        except Exception: g = None
        if g is None: cnt["rev_unknown"] += 1; chart_only.append(dict(sym=s, **x, rev=None)); continue
        if g > 0.25: cnt["rev_pass"] += 1; F.append(dict(sym=s, **x, rev=round(g * 100, 1)))
        else: cnt["rev_fail"] += 1; chart_only.append(dict(sym=s, **x, rev=round(g * 100, 1)))
    L66 = set(S.UNIVERSE + S.CORE)
    return dict(date=day, generated=now.isoformat(timespec="minutes"), rule="setup-f research rule, full S&P 500", members_source=src,
                counts=cnt, F=F, in_66=[r["sym"] for r in F if r["sym"] in L66], chart_only=chart_only)


def selftest():
    d0 = dt.date(2024, 1, 1); p = []
    p += [50 + 150 * k / 300 for k in range(300)]          # a long rise to 200
    p += [200 - 60 * k / 5 for k in range(5)]              # a sharp drop to 140
    p += [140 + 16 * k / 40 for k in range(40)]            # a slow recovery to 156: 22.6% under the high, 10>30, 50>200
    b = [((d0 + dt.timedelta(days=i)).isoformat(), x, x * 1.005, x * 0.995, x) for i, x in enumerate(p)]
    x = rule(b); assert x and abs(x["dd"] - 22.6) < 0.1, x
    assert rule(b[:259]) is None                                           # under 260 sessions
    assert rule(b[:310]) is None                                           # mid-drop: 10 EMA under the 30
    assert rule([(r[0], 100, 101, 99, 100) for r in b]) is None            # flat: never 20% under
    print("self-test ok", x)


if __name__ == "__main__":
    if os.environ.get("FFTEST"): selftest(); sys.exit()
    if sys.argv[1:2] == ["dry"]:
        r = scan(sys.argv[2]); print(json.dumps({k: v for k, v in r.items() if k != "chart_only"}, indent=1)); sys.exit()
    now = dt.datetime.now(CT)
    done = json.load(open(OUT)).get("date") if os.path.exists(OUT) else None
    if now.weekday() >= 5 or (now.hour, now.minute) < (15, 20) or done == now.date().isoformat():
        print("f_full: not due"); sys.exit()
    r = scan(); json.dump(r, open(OUT, "w"), indent=1)
    print(f"f_full {r['date']}: {len(r['F'])} Setup F on the full S&P 500 · counts {r['counts']}")
