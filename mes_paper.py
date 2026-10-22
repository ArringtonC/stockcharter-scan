#!/usr/bin/env python3
"""MICRO FUTURES PAPER BOT (2026-10-21): runs the tested ICT model (thesis/ict: after a look-ahead fix −0.03 R, i.e. break-even before
costs; switched on anyway as a watch-it-live log at Arrington's request, 2026-10-21) live, minute by minute, as a SIMULATED Micro Nasdaq (MNQ, $2 per NQ point) trade. No broker, no orders.
The model reads QQQ (NQ's ETF twin) exactly as the backtest did:
  1h gap   bullish 1-hour fair value gap from the last 5 sessions, below today's open, within 3%, not traded through
  tap      9:30-11:30 ET QQQ trades into the gap's midpoint (CE) with no 5-minute close under the gap
  trigger  a 2-minute close above the top of a bearish 2-minute gap, after the tap -> long at the next minute's open
  stop     under the session low · target the morning high before the tap · out at 15:55 · one trade a day
P&L: QQQ move x (NQ / QQQ) points x $2 for 1 MNQ. Live data is Alpaca's free IEX feed (history: SIP), so fills can
differ a little from the backtest. Posts to #paper-portfolio. State ~/.ledger-mnq.json, log ~/.ledger-mnq.jsonl.
Self-test: MNQTEST=1 python3 mes_paper.py"""
import datetime as dt, json, os, urllib.request
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York"); STATE = os.path.expanduser("~/.ledger-mnq.json"); LOG = os.path.expanduser("~/.ledger-mnq.jsonl")
NQ_PER_QQQ = 41.2   # NQ futures / QQQ, about 29,860 / 725 in Oct 2026; refreshed from data when available
PT = 2.0            # MNQ dollars per NQ point


def agg(bars, n):
    """bars: (hh:mm, o, h, l, c) -> n-minute bars from the first bar."""
    return [(g[0][0], g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]) for g in (bars[i:i + n] for i in range(0, len(bars), n))]


def gaps_from(hist_hours, open_):
    """Bullish 1h gaps from the prior sessions' 60-minute bars, below the open within 3%, never traded through."""
    out = []
    for i in range(2, len(hist_hours)):
        lo, hi = hist_hours[i - 2][2], hist_hours[i][3]
        if hi > lo and all(b[3] > lo for b in hist_hours[i + 1:]) and open_ * 0.97 < hi < open_: out.append((lo, hi))
    return out


def plan(m, gaps):
    """Pure: today's 1-minute bars so far + gaps -> None or dict(k=entry bar index, stop, target, tap time).
    Identical rules to thesis/ict/ict_test.py."""
    if not gaps or not m: return None
    m5 = agg(m, 5); tap = None
    for k, (t, o, h, l, c) in enumerate(m):
        if t > "11:30": break
        for lo, hi in gaps:
            if l <= (lo + hi) / 2 and all(b[4] >= lo for b in m5 if b[0] <= t): tap = k; break
        if tap is not None: break
    if tap is None: return None
    m2 = agg(m, 2); bear = []; trig = None
    for j in range(2, len(m2)):
        a, c = m2[j - 2], m2[j]
        if c[0] > "11:30": break
        if j * 2 + 1 >= len(m): break                     # this 2-minute bar is not finished yet
        if c[2] < a[3]: bear.append((c[0], c[2], a[3]))
        if c[0] >= m[tap][0] and any(x[0] <= c[0] and c[4] > x[2] for x in bear): trig = c[0]; break
    if not trig: return None
    k = 2 * [y[0] for y in m2].index(trig) + 2              # the minute after the 2-minute trigger bar closes
    if k >= len(m) or m[k][0] > "11:31": return None
    stop = min(x[3] for x in m[:k]) - 0.01; tgt = max(x[2] for x in m[:tap + 1]); e = m[k][1]
    if e <= stop or tgt <= e: return None
    return dict(k=k, entry=e, stop=stop, target=tgt, tap=m[tap][0], trig=trig)


def manage(p, m):
    """Pure: an open trade + bars -> None (still open) or (exit price, why, time)."""
    for t, o, h, l, c in m[p["k"]:]:
        if l <= p["stop"]: return min(o, p["stop"]), "stop", t
        if h >= p["target"]: return (max(o, p["target"]) if t != m[p["k"]][0] else p["target"]), "target", t
        if t >= "15:55": return c, "15:55", t
    return None


def bars(start, end, feed):
    H = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}; out = {}; tok = None
    while True:
        u = f"https://data.alpaca.markets/v2/stocks/QQQ/bars?timeframe=1Min&start={start}&end={end}&feed={feed}&limit=10000&adjustment=split" + (f"&page_token={tok}" if tok else "")
        j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
        for b in j.get("bars") or []:
            t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
            if (9, 30) <= (t.hour, t.minute) < (16, 0): out.setdefault(t.strftime("%F"), []).append((t.strftime("%H:%M"), b["o"], b["h"], b["l"], b["c"]))
        tok = j.get("next_page_token")
        if not tok: return out


def dollars(p, x): return (x - p["entry"]) * NQ_PER_QQQ * PT


def tick(say):
    now = dt.datetime.now(ET); today = now.strftime("%F")
    if now.weekday() >= 5 or not ((9, 31) <= (now.hour, now.minute) <= (16, 5)): return
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if st.get("date") != today: st = dict(date=today, status="watching")
    if st["status"] in ("done", "no setup"): return
    utc = lambda d: d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "gaps" not in st:
        hist = bars(utc(now - dt.timedelta(days=10)), utc(now.replace(hour=9, minute=0)), "sip")
        days = sorted(d for d in hist if d < today)[-5:]
        hours = [b for d in days for b in agg(hist[d], 60)]
        st["hours"] = len(hours); st["_hours"] = hours
    m = bars(utc(now.replace(hour=9, minute=30, second=0)), utc(now), "iex").get(today, [])
    if not m: return
    if "gaps" not in st:
        st["gaps"] = gaps_from(st.pop("_hours"), m[0][1]); st["open"] = m[0][1]
        if not st["gaps"]: st["status"] = "no setup"; save(st, dict(date=today, kind="no gap")); return
    if st["status"] == "watching":
        p = plan(m, st["gaps"])
        if p:
            st.update(status="in", **p); risk = dollars(p, p["stop"]); rew = dollars(p, p["target"])
            save(st, dict(date=today, kind="entry", **p))
            say(f"🧪 **MNQ PAPER · LONG** (ICT 1h-gap model, not a signal)\nQQQ {p['entry']:.2f} at {m[p['k']][0]} ET · tapped the 1h gap at {p['tap']}, 2-min inversion {p['trig']}\n"
                f"STOP {p['stop']:.2f} (≈ ${-risk:,.0f}) · TARGET {p['target']:.2f} (≈ +${rew:,.0f}) · 1 MNQ ≈ ${NQ_PER_QQQ * PT:,.0f} per $1 of QQQ\n_paper only · this model tested at break-even · not advice_")
        elif now.strftime("%H:%M") > "11:32": st["status"] = "no setup"; save(st, dict(date=today, kind="no trigger"))
        else: save(st)
        return
    if st["status"] == "in":
        out = manage(st, m)
        if out:
            x, why, t = out; usd = dollars(st, x); r = (x - st["entry"]) / (st["entry"] - st["stop"])
            st.update(status="done", exit=x, why=why, exit_time=t, usd=round(usd, 2), R=round(r, 2)); save(st, dict(date=today, kind="exit", exit=x, why=why, time=t, usd=usd, R=r))
            book = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []; ex = [b for b in book if b["kind"] == "exit"]
            say(f"🧪 **MNQ PAPER · CLOSED** {why} at QQQ {x:.2f} ({t} ET) · **{r:+.2f} R · ${usd:+,.0f}** on 1 MNQ\nRecord: {len(ex)} trades · {sum(b['R'] for b in ex):+.1f} R · ${sum(b['usd'] for b in ex):+,.0f}\n_paper only · not advice_")
        else: save(st)


def save(st, event=None):
    json.dump(st, open(STATE, "w"), indent=1)
    if event:
        with open(LOG, "a") as f: f.write(json.dumps(event) + "\n")


if __name__ == "__main__":
    if os.environ.get("MNQTEST"):
        # a 1h gap at 100-101 under a 103 open; the day dips to 100.4 (CE 100.5), a 2-min downside gap, then an inversion
        hours = [("09:30", 99, 100, 98.5, 99.8), ("10:30", 99.8, 101.5, 99.7, 101.4), ("11:30", 101.4, 103, 101, 102.8)]
        g = gaps_from(hours, 103.0); assert g == [(100, 101)], g
        m = [("09:30", 103.0, 103.2, 102.9, 103.0), ("09:31", 103.0, 103.1, 102.6, 102.7)]
        px = [102.5, 102.2, 101.8, 101.4, 101.0, 100.7, 100.45, 100.6, 100.9, 101.3, 101.8, 102.0, 102.1, 102.3]
        for i, c in enumerate(px): m.append((f"09:{32 + i:02d}", c + 0.05, c + 0.1, c - 0.1, c))
        p = plan(m, g); assert p and p["stop"] < 100.4 and p["target"] >= 103.2 and p["entry"] > 100.9, p
        up = m + [("10:00", 103.0, 103.4, 102.9, 103.3)]; out = manage(p, up); assert out and out[1] == "target", out
        down = m + [("10:00", 101.0, 101.1, 100.2, 100.3)]; out = manage(p, down); assert out and out[1] == "stop", out
        assert plan(m, []) is None and gaps_from(hours, 99.0) == []
        print("mnq ok", {k: round(v, 2) if isinstance(v, float) else v for k, v in p.items()})
