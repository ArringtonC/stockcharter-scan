#!/usr/bin/env python3
"""Forward paper track: F-calls and G1-calls (thesis/g1-paper-track-design.md, approved 2026-10-23). PAPER ONLY:
no orders, no alerts, never touches trades.csv, the paper books or the journal.
  daily()  after the close (run-scan, after f_full.py): new Setup F episodes from docs/f_full.json -> queue an F-call;
           open episodes whose close crossed the G1 gap level -> queue a G1-call (rules of f-continuation-rules.md §11)
  tick()   every minute from trade_manager: entries at 8:45 / 9:15 / 10:00 CT (none after 10:30), at the ASK of the
           30-45 DTE at-the-money call; exits every 5 minutes at the BID: +50%, -50%, or 14:45 CT on the earlier of
           session 20 or 7 days before expiry
Quotes: Alpaca's free option feed is "indicative" (not the full exchange quote); every record says so.
Log: /root/.ledger-calls/events.jsonl (append-only) · state.json · docs/calls_paper.json (summary for the site)
Self-test: CPTEST=1 python3 calls_paper.py"""
import datetime as dt, json, os, sys, urllib.request
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); CT = ZoneInfo("America/Chicago")
DIR = os.path.expanduser("~/.ledger-calls"); LOG = os.path.join(DIR, "events.jsonl"); STATE = os.path.join(DIR, "state.json")
SITE = os.path.join(H, "docs", "calls_paper.json"); FEED = "indicative"; FEE = 0.65; WIN = 120; FLOOR = 0.97; MAXSPREAD = 0.25
CHECKS = [(8, 45), (9, 15), (10, 0)]


# ---------- pure ----------
def gap_level(r, i):
    """Lowest unfilled down-gap top above the close of day i (r = [(date, o, h, l, c)])."""
    best = None
    for k in range(max(1, i - 252), i + 1):
        if r[k][2] < r[k - 1][3]:
            top = r[k - 1][3]
            if top <= r[i][4] or any(r[j][2] >= top for j in range(k + 1, i + 1)): continue
            best = top if best is None else min(best, top)
    return best


def exit_check(ask_paid, bid, now, xday):
    if bid and bid >= 1.5 * ask_paid: return "TARGET"
    if bid and bid <= 0.5 * ask_paid: return "STOP"
    if now.date().isoformat() >= xday and (now.hour, now.minute) >= (14, 45): return "TIME"
    return None


def selftest():
    d = lambda k: (dt.date(2026, 1, 1) + dt.timedelta(days=k)).isoformat()
    r = [(d(k), 100, 101, 99, 100) for k in range(10)] + [(d(10), 95, 96, 94, 95)] + [(d(k), 95, 96, 94, 95) for k in range(11, 15)]
    assert gap_level(r, 14) == 99                                        # day 10 high 96 < day 9 low 99: gap top 99
    r2 = r + [(d(15), 98, 100, 97, 100)]; assert gap_level(r2, 15) is None  # filled (high 100 >= 99)
    now = dt.datetime(2026, 1, 5, 10, 0, tzinfo=CT)
    assert exit_check(2.0, 3.0, now, "2026-02-01") == "TARGET" and exit_check(2.0, 1.0, now, "2026-02-01") == "STOP"
    assert exit_check(2.0, 2.1, now, "2026-02-01") is None and exit_check(2.0, 2.1, now.replace(hour=14, minute=50), "2026-01-05") == "TIME"
    print("calls_paper self-test ok")


# ---------- io ----------
def load():
    try: return json.load(open(STATE))
    except Exception: return dict(episodes={}, pending=[], open=[], closed=[], started=dt.date.today().isoformat())


def save(st): os.makedirs(DIR, exist_ok=True); json.dump(st, open(STATE, "w"), indent=1)


def log(**e):
    os.makedirs(DIR, exist_ok=True); e["at"] = dt.datetime.now(CT).isoformat(timespec="seconds"); e["feed"] = FEED
    with open(LOG, "a") as f: f.write(json.dumps(e) + "\n")
    return e


def _get(u, base="https://data.alpaca.markets"):
    h = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}
    return json.load(urllib.request.urlopen(urllib.request.Request(base + u, headers=h), timeout=30))


def daily_bars(sym, days=420):
    s = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = _get(f"/v2/stocks/bars?symbols={sym}&timeframe=1Day&start={s}&end={end}&feed=sip&adjustment=raw&limit=10000").get("bars", {}).get(sym, [])
    return [(x["t"][:10], x["o"], x["h"], x["l"], x["c"]) for x in b]


def spot(sym):
    q = _get(f"/v2/stocks/trades/latest?symbols={sym}&feed=iex").get("trades", {}).get(sym, {})
    return q.get("p")


def pick(sym, px, today):
    lo, hi = today + dt.timedelta(25), today + dt.timedelta(50); ch = []
    tok = None
    while True:
        r = _get(f"/v2/options/contracts?underlying_symbols={sym}&type=call&status=active&expiration_date_gte={lo}&expiration_date_lte={hi}"
                 f"&strike_price_gte={px * 0.9:.2f}&strike_price_lte={px * 1.1:.2f}&limit=10000" + (f"&page_token={tok}" if tok else ""), base="https://paper-api.alpaca.markets")
        ch += [(x["expiration_date"], float(x["strike_price"]), x["symbol"]) for x in r.get("option_contracts") or [] if x.get("root_symbol", sym) == sym]
        tok = r.get("next_page_token")
        if not tok: break
    for a, b in ((30, 45), (25, 50)):
        ex = sorted({x[0] for x in ch if a <= (dt.date.fromisoformat(x[0]) - today).days <= b}, key=lambda e: (abs((dt.date.fromisoformat(e) - today).days - 37), e))
        if ex: return min([x for x in ch if x[0] == ex[0]], key=lambda x: (abs(x[1] - px), x[1]))
    return None


def quote(occ):
    q = _get(f"/v1beta1/options/quotes/latest?symbols={occ}&feed={FEED}").get("quotes", {}).get(occ, {})
    return q.get("bp") or 0.0, q.get("ap") or 0.0, q.get("bs"), q.get("as"), q.get("t")


def sessions_after(today, n):
    out, d = [], dt.date.fromisoformat(today)
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5: out.append(d.isoformat())        # shortcut: weekdays only; holidays shift the 20th session by a day, upgrade with a market calendar if it matters
    return out


# ---------- the two entry points ----------
def daily(today=None):
    today = today or dt.datetime.now(CT).date().isoformat(); st = load()
    if st.get("last_daily") == today: return                      # once per day, whatever slot calls it
    try: ff = json.load(open(os.path.join(H, "docs", "f_full.json")))
    except Exception: ff = {}
    if ff.get("date") != today: return                             # wait for today's full scan; never count a session twice
    if True:
        for r in ff.get("F", []):
            s = r["sym"]; ep = st["episodes"].get(s)
            if ep and ep["open"]: continue
            b = daily_bars(s)
            if not b or b[-1][0] != today: continue
            L = gap_level(b, len(b) - 1)
            st["episodes"][s] = dict(sym=s, fire=today, px=b[-1][4], L=L, open=True, g1=None, sessions=0)
            log(kind="episode", sym=s, fire=today, px=b[-1][4], gap_level=L, note="new Setup F episode (full S&P scan)")
            st["pending"].append(dict(arm="F-call", sym=s, signal=today, tries=0)); log(kind="queue", arm="F-call", sym=s, signal=today)
    for s, ep in st["episodes"].items():
        if not ep["open"] or ep["fire"] == today: continue
        ep["sessions"] += 1
        if ep["sessions"] > WIN: ep["open"] = False; log(kind="episode_end", sym=s, g1=ep["g1"], why="120 sessions"); continue
        if ep["L"] is None or ep["g1"]: continue
        b = daily_bars(s, 30)
        if b and b[-1][0] == today and b[-1][4] > ep["L"]:
            ep["g1"] = today; log(kind="g1", sym=s, date=today, close=b[-1][4], level=ep["L"], sessions_after_fire=ep["sessions"])
            st["pending"].append(dict(arm="G1-call", sym=s, signal=today, tries=0)); log(kind="queue", arm="G1-call", sym=s, signal=today)
    st["last_daily"] = today
    save(st); summary(st)


def tick(now=None):
    now = now or dt.datetime.now(CT)
    if now.weekday() >= 5 or not ((8, 45) <= (now.hour, now.minute) < (15, 0)): return
    st = load(); today = now.date().isoformat(); changed = False
    for o in [o for o in st["pending"] if o["signal"] < today]:
        due = [c for c in CHECKS if (now.hour, now.minute) >= c]
        if (now.hour, now.minute) >= (10, 30):
            st["pending"].remove(o); log(kind="no_entry", arm=o["arm"], sym=o["sym"], why="no usable quote by 10:30"); changed = True; continue
        if len(due) <= o["tries"]: continue
        o["tries"] = len(due); changed = True
        px = spot(o["sym"]); c = pick(o["sym"], px, now.date()) if px else None
        if not c: log(kind="skip", arm=o["arm"], sym=o["sym"], why="no 25-50 DTE contract" if px else "no stock price"); continue
        exp, k, occ = c; bid, ask, bs, asz, qt = quote(occ); mid = (bid + ask) / 2 if bid and ask else 0
        if not ask or not bid or (ask - bid) / mid > MAXSPREAD:
            log(kind="skip", arm=o["arm"], sym=o["sym"], occ=occ, bid=bid, ask=ask, quote_time=qt, why="no quote" if not ask else "spread over 25% of mid"); continue
        xd = [d for d in [today] + sessions_after(today, 19) if (dt.date.fromisoformat(exp) - dt.date.fromisoformat(d)).days >= 7]
        pos = dict(arm=o["arm"], sym=o["sym"], signal=o["signal"], occ=occ, k=k, exp=exp, entry=ask, entry_time=now.isoformat(timespec="seconds"),
                   stock=px, xday=xd[-1] if xd else today)
        st["open"].append(pos); st["pending"].remove(o)
        log(kind="entry", **pos, bid=bid, bid_size=bs, ask_size=asz, quote_time=qt)
    if now.minute % 5 == 0:
        for p in list(st["open"]):
            try: bid, ask, bs, asz, qt = quote(p["occ"])
            except Exception: continue
            why = exit_check(p["entry"], bid, now, p["xday"])
            if why:
                pl = (bid - p["entry"]) * 100 - 2 * FEE
                st["open"].remove(p); st["closed"].append(dict(p, exit=bid, exit_time=now.isoformat(timespec="seconds"), why=why, pl=round(pl, 2), ret=round(bid / p["entry"] - 1, 4)))
                log(kind="exit", arm=p["arm"], sym=p["sym"], occ=p["occ"], bid=bid, ask=ask, quote_time=qt, why=why, pl=round(pl, 2)); changed = True
    if changed: save(st); summary(st)


def summary(st):
    out = dict(updated=dt.datetime.now(CT).isoformat(timespec="minutes"), feed=FEED, started=st.get("started"), arms={},
               episodes=len(st["episodes"]), reached_g1=sum(1 for e in st["episodes"].values() if e.get("g1")))
    for a in ("F-call", "G1-call"):
        C = [c for c in st["closed"] if c["arm"] == a]; r = [1000 * c["ret"] - 1000 * 2 * FEE / (100 * c["entry"]) for c in C]
        w = [x for x in r if x > 0]; l = [x for x in r if x <= 0]; eq = pk = dd = 0.0
        for x in r: eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
        out["arms"][a] = dict(closed=len(C), open=sum(1 for p in st["open"] if p["arm"] == a), pending=sum(1 for p in st["pending"] if p["arm"] == a),
                              win=round(100 * len(w) / len(r), 1) if r else None, expectancy=round(sum(r) / len(r), 1) if r else None,
                              pf=round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None, total=round(sum(r), 1), maxdd=round(dd, 1))
    json.dump(out, open(SITE, "w"), indent=1)


if __name__ == "__main__":
    if os.environ.get("CPTEST"): selftest(); sys.exit()
    if sys.argv[1:2] == ["daily"]: daily()
