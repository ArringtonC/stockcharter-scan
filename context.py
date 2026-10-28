#!/usr/bin/env python3
"""Market Context (2026-10-27): monthly / weekly / daily structure and 6-month relative strength for the Focus names.
INFORMATION ONLY. It reads prices and writes docs/context.json; it changes, filters or delays no signal, book or alert.
Rules: StockCharter thesis/structure/display-rules.md, validated in validation-report.md (no look-ahead, reproducible).
  Swings: high above the N bars on each side (strict), confirmed only when the Nth later bar is complete. N = 3 daily, 2 weekly/monthly.
  Prices: Alpaca SIP, split-adjusted only (what is traded, TradingView's default). The current week/month and an unfinished
  trading day are never used for swings. Relative strength = 6-month (126-session) return, percentile among today's S&P 500.
  python3 context.py           normal run (Focus names from docs/focus.json)
  python3 context.py MSFT,KO   these names -> stdout
  CTXTEST=1 python3 context.py self-test"""
import datetime as dt, json, os, sys, urllib.request
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); DOCS = os.path.join(H, "docs"); OUT = os.path.join(DOCS, "context.json")
ET = ZoneInfo("America/New_York"); NS = {"Monthly": 2, "Weekly": 2, "Daily": 3}
KEY = {"Monthly": lambda d: d[:7], "Weekly": lambda d: dt.date.fromisoformat(d).isocalendar()[:2]}


def alpaca(syms, start, end):
    hd = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}; out = {}
    for k in range(0, len(syms), 100):
        tok = None
        while True:
            u = (f"https://data.alpaca.markets/v2/stocks/bars?symbols={','.join(syms[k:k + 100])}&timeframe=1Day&start={start}&end={end}"
                 f"&feed=sip&adjustment=split&limit=10000" + (f"&page_token={tok}" if tok else ""))
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=60))
            for s, v in (r.get("bars") or {}).items(): out.setdefault(s, []).extend((x["t"][:10], x["o"], x["h"], x["l"], x["c"]) for x in v)
            tok = r.get("next_page_token")
            if not tok: break
    return out


def group(daily, key):
    out = []
    for d, o, h, l, c in daily:
        k = key(d)
        if out and out[-1]["k"] == k: b = out[-1]; b.update(h=max(b["h"], h), l=min(b["l"], l), c=c, end=d)
        else: out.append(dict(k=k, start=d, end=d, o=o, h=h, l=l, c=c))
    return out[:-1]                                   # the last period is still in progress


def swings(B, N):
    hs, ls = [], []
    for k in range(N, len(B) - N):
        nb = [j for j in range(k - N, k + N + 1) if j != k]
        if all(B[k]["h"] > B[j]["h"] for j in nb): hs.append(dict(p=B[k]["h"], pivot=B[k]["start"], conf=B[k + N]["end"]))
        if all(B[k]["l"] < B[j]["l"] for j in nb): ls.append(dict(p=B[k]["l"], pivot=B[k]["start"], conf=B[k + N]["end"]))
    return hs, ls


def potential(B, N, hs, ls):
    """Unconfirmed pivots: an extreme bar with 1..N-1 complete bars after it, all on its right side."""
    out = []
    for kind, conf, f in (("high", hs, "h"), ("low", ls, "l")):
        start = next((j for j in range(len(B)) if conf and B[j]["start"] == conf[-1]["pivot"]), -1) + 1
        for k in range(max(start, len(B) - N), len(B)):
            after = B[k + 1:]
            better = (lambda a, b: a > b) if kind == "high" else (lambda a, b: a < b)
            if 1 <= len(after) < N and all(better(B[k][f], b[f]) for b in after) and all(better(B[k][f], B[j][f]) for j in range(max(0, k - N), k)):
                out.append(dict(kind=kind, p=B[k][f], pivot=B[k]["start"], need=N - len(after)))
    return out


def classify(hs, ls, close):
    """(label, detail). Labels: Bullish, Bearish, Range, Transitioning up/down, Insufficient data."""
    if len(hs) < 2 or len(ls) < 2: return "Insufficient data", "fewer than two confirmed highs and lows"
    hh, hl = hs[-1]["p"] > hs[-2]["p"], ls[-1]["p"] > ls[-2]["p"]
    if hh and hl: return "Bullish", "HH / HL" + (" — BROKEN: closed below the last higher low" if close < ls[-1]["p"] else "")
    if not hh and not hl: return "Bearish", "LH / LL" + (" — BROKEN: closed above the last lower high" if close > hs[-1]["p"] else "")
    flag = " — price below the last confirmed low" if close < ls[-1]["p"] else " — price above the last confirmed high" if close > hs[-1]["p"] else ""
    if len(ls) >= 3 and not hh and ls[-2]["p"] < ls[-3]["p"] and hl: return "Transitioning up", "was LH/LL, now a higher low" + flag
    if len(hs) >= 3 and hl and hs[-2]["p"] > hs[-3]["p"] and not hh: return "Transitioning down", "was HH/HL, now a lower high" + flag
    return "Range", ("HH" if hh else "LH") + " + " + ("HL" if hl else "LL") + flag


def block(done, N, close):
    hs, ls = swings(done, N); label, detail = classify(hs, ls, close)
    out = dict(label=label, detail=detail)
    if label == "Insufficient data": return out
    U, Lo = hs[-1], ls[-1]; pots = potential(done, N, hs, ls); recent = [x["c"] for x in done[-4:]]
    side = "high" if close > U["p"] else "low" if close < Lo["p"] else None
    pend = [p for p in pots if p["kind"] == side and (p["p"] > U["p"] if side == "high" else p["p"] < Lo["p"])] if side else []
    out.update(hi=U, lo=Lo, unconfirmed=pots,
               res=min([h["p"] for h in hs[-12:] if h["p"] > close], default=None), sup=max([l["p"] for l in ls[-12:] if l["p"] < close], default=None),
               range_break="Above upper boundary" if side == "high" else "Below lower boundary" if side == "low" else "Inside range",
               swing_conf=(f"New swing {side} ${pend[0]['p']:,.2f} not confirmed (needs {pend[0]['need']} more bar{'s' if pend[0]['need'] > 1 else ''})" if pend
                           else f"No new swing {side} yet" if side else "—"),
               outcome="Holding" if side else "Failed (closed back inside)" if any(c > U["p"] or c < Lo["p"] for c in recent) else "—")
    return out


def interpret(B, close):
    m, w, d = (B[k]["label"].lower() for k in ("Monthly", "Weekly", "Daily"))
    s = f"All three timeframes read {m}." if m == w == d else (f"Daily reads {d}, but monthly reads {m}; weekly reads {w}." if d != m else f"Monthly {m}, weekly {w}, daily {d}.")
    wk = B["Weekly"]
    if wk.get("range_break") == "Above upper boundary": s += f" Price has crossed above the confirmed weekly high (${wk['hi']['p']:,.2f}); {wk['swing_conf'][0].lower()}{wk['swing_conf'][1:]}."
    elif wk.get("range_break") == "Below lower boundary": s += f" Price has crossed below the confirmed weekly low (${wk['lo']['p']:,.2f}); {wk['swing_conf'][0].lower()}{wk['swing_conf'][1:]}."
    elif wk.get("hi"): s += f" Price is inside the confirmed weekly range (${wk['lo']['p']:,.2f}–${wk['hi']['p']:,.2f})."
    r = wk.get("res")
    return s + (f" Nearest confirmed weekly resistance ${r:,.2f} is {100 * (r / close - 1):.1f}% above." if r else " No confirmed weekly resistance above price.")


def build(syms, now=None):
    now = now or dt.datetime.now(ET)
    end = (now - dt.timedelta(minutes=20)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today, market_open = now.date().isoformat(), now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)
    done_day = lambda b: [x for x in b if not (x[0] == today and market_open)]      # an unfinished trading day is not a bar
    px = {s: done_day(v) for s, v in alpaca(sorted(set(syms) | {"SPY"}), "2016-01-01", end).items()}
    last = px["SPY"][-1][0] if px.get("SPY") else None
    try: members = json.load(open(os.path.join(DOCS, "sp500.json")))["symbols"]
    except Exception: members = []
    rs_px = {s: done_day(v) for s, v in alpaca(sorted(set(members)), (now.date() - dt.timedelta(days=200)).isoformat(), end).items()} if members else {}
    ret = {s: v[-1][4] / v[-127][4] - 1 for s, v in rs_px.items() if len(v) >= 127 and v[-1][0] == last}
    order = sorted(ret, key=ret.get); pct = {s: round(100 * (i + 0.5) / len(order)) for i, s in enumerate(order)}
    out = []
    for s in syms:
        b = px.get(s) or []
        if len(b) < 60: out.append(dict(sym=s, error="not enough price history")); continue
        close = b[-1][4]
        B = {tf: block([dict(k=x[0], start=x[0], end=x[0], h=x[2], l=x[3], c=x[4]) for x in b] if tf == "Daily" else group(b, KEY[tf]), N, close) for tf, N in NS.items()}
        r6 = b[-1][4] / b[-127][4] - 1 if len(b) >= 127 else None
        out.append(dict(sym=s, asof=b[-1][0], close=close, stale=b[-1][0] != last, blocks=B, interpretation=interpret(B, close),
                        rs=dict(pct=pct.get(s), ret6m=r6, universe=len(order), member=s in members)))
    return dict(generated=now.isoformat(timespec="minutes"), last_session=last, items=out,
                note="Information only. Trend alignment and RS-1 (2017-2020) were tested: no trading edge shown. Never filters a signal.")


def selftest():
    z = [(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", 0, 10 + i + (3 if i % 4 == 0 else 0), 9 + i - (3 if i % 4 == 2 else 0), 10 + i) for i in range(56)]
    B = [dict(k=d, start=d, end=d, h=h, l=l, c=c) for d, o, h, l, c in z]
    assert classify(*swings(B, 1), 70)[0] == "Bullish"
    assert classify(*swings([dict(b, h=-b["l"], l=-b["h"]) for b in B], 1), -70)[0] == "Bearish"
    assert len(group(z, KEY["Monthly"])) == 1                                   # February is in progress, January complete
    assert classify([], [], 1)[0] == "Insufficient data"
    print("context self-test ok")


if __name__ == "__main__":
    if os.environ.get("CTXTEST"): selftest(); sys.exit()
    if len(sys.argv) > 1: print(json.dumps(build(sys.argv[1].split(",")), indent=1)); sys.exit()
    try: syms = json.load(open(os.path.join(DOCS, "focus.json")))["syms"]
    except Exception: syms = []
    if not syms: print("context: no focus names"); sys.exit()
    r = build(syms); json.dump(r, open(OUT, "w"), indent=1)
    print(f"context: {len(r['items'])} names · last session {r['last_session']} · stale {sum(1 for x in r['items'] if x.get('stale'))}")
