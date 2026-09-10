#!/usr/bin/env python3
"""Daily scan + paper book. Self-contained. Writes docs/data.json and docs/history.json.

Runs in GitHub Actions twice a day. Nothing is cached; everything is fetched live:
  prices   Yahoo Finance
  revenue  SEC EDGAR XBRL, point-in-time (only filings dated on or before today)

Setups are LOCKED. The numbers quoted in SETUPS below come from the tests in the
private research repo; do not tune them here.

trades.csv is the paper book. This script marks it to market and auto-closes:
  shares  ARM a floor at the target when the high first touches it; close when a later low
          falls back to that floor, or at 252 sessions. (Tested: +6pp over closing at target.)
  calls   mark with a Black-Scholes estimate (60d realized vol); close at expiry at intrinsic
"""
import urllib.request, json, datetime, time, os, gzip, csv, math

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
TRADES = os.path.join(HERE, "trades.csv")
HIST = os.path.join(DOCS, "history.json")
SEC_UA = {"User-Agent": os.environ.get("SEC_CONTACT", "stockcharter-scan research")}
YF = {"User-Agent": "Mozilla/5.0"}

UNIVERSE = """AAPL MSFT NVDA AMZN GOOGL META NFLX TSLA AMD INTC MU QCOM AVGO TXN
CRM ADBE ORCL CSCO IBM HPQ DELL NOW WDAY PANW ZS OKTA SHOP XYZ PYPL TTD ROKU
SPOT DOCU JNJ PFE MRK ABBV LLY BMY AMGN GILD BIIB REGN VRTX MRNA XOM CVX OXY
SLB HAL COP DVN CAVA SG HOOD BE COIN PLTR U WBD F GM SNOW""".split()

TCOLS = ["id", "kind", "acct", "opened", "symbol", "setup", "entry", "target", "strike",
         "expiry", "contracts", "status", "closed", "exit", "pl_pct", "note"]

SETUPS = {
    "VIX": dict(name="Setup VIX", tag="buy the end of panic", size="$1,000 · ATM QQQ call · 30 DTE · hold 21 sessions",
        rules=["VIX closed at 25 or higher on any of the last 10 sessions",
               "VIX has now fallen 3 sessions in a row",
               "Buy an at-the-money QQQ call, about 30 days out",
               "Sell after 21 sessions. No stop."],
        numbers=["286 signals, 15 years", "+64% mean return · 58% win · 13% go to zero",
                 "A call on any random day: +46.7%. Edge +17.3pp, p = 0.002",
                 "Both halves work: 2011–19 +85.5%, 2020–26 +55.5%"],
        works="When panic peaks and starts to unwind. The VIX crush works for you: the stock rises while the option's volatility premium falls.",
        fails="When VIX keeps rising after three down days. 2022: −15%, 33% win. 2024: −34%. Buying the FIRST close over 25 is worse — it fails the null test (p=0.212) because you get run over on the way up."),
    "F": dict(name="Setup F", tag="recovery + earnings", size="shares · target = the prior high · no stop · 78–136 sessions",
        rules=["Price is 20–45% below its 252-day high",
               "10 EMA above 30 EMA, and 50 EMA above 200 EMA",
               "Trailing-12-month revenue growing 25%+ (SEC filings only, as of the signal date)",
               "Target is the prior high. One number, set at entry.",
               "When price REACHES the target, do not sell. Set a hard floor at the target and let it run. Sell only if it falls back to the floor, or at 252 sessions."],
        numbers=["198 signals · 83% reach the target · 115 sessions median",
                 "Exit test: close at target +20.7% mean · floor at target +26.8% mean, same 84% win, same median — strictly better",
                 "Excluding the top 5 names it still hits 76% — the only setup that survives that test",
                 "Revenue filter is a U-shape: 25%+ growth 85% hit, flat revenue 47–53%",
                 "Best VIX window is 16–20 (+10.76pp). Under 16 the edge is negative."],
        works="Bull markets, moderate fear. Five years of six.",
        fails="2022: 5% hit rate. A prior-high target cannot work when the index spends 91% of the year 10%+ below its own high. Adding 'QQQ above its 200 EMA' turns it off in a bear (93% hit vs 27% when rejected)."),
    "D": dict(name="Setup D", tag="shallow pullback", size="shares · hold ~63 sessions",
        rules=["Price is 5–20% below its 252-day high",
               "Price is above where it was 126 sessions ago",
               "RSI(14) above 55"],
        numbers=["8,148 signals · +3.75pp edge at 63 days · 62% win vs 59%",
                 "Fires on ~9% of all name-days — very frequent",
                 "Edge decayed: +9.8pp in 2019 down to −1.5pp in 2025 on the old universe"],
        works="Trending years. Best at VIX 25+ (+14.88%, +2.69pp).",
        fails="It never beat random picking on the hindsight-free universe. Treat it as a watchlist, not a signal."),
    "C": dict(name="Setup C", tag="breakout", size="shares · 21–63 sessions · tech names only",
        rules=["10 EMA crosses above the 50 SMA within the last 5 sessions",
               "50 EMA above 200 EMA",
               "Close clears the prior 20 sessions' highs",
               "Breadth filter: under 65% of the universe above its 200 EMA (unvalidated)"],
        numbers=["+4.56% at 21 days vs +1.66% baseline, p=0.000 — on the ORIGINAL universe",
                 "On a hindsight-free universe: +0.12pp broad (dead), +4.27pp tech-only (alive)",
                 "Portfolio-level it loses to random: fires only ~400 times, sits 57% in cash"],
        works="Liquid technology names in a trending tape. HOOD 2024: 7 signals, 7 winners.",
        fails="Anything that is not tech. Banks, energy, staples: no edge. It is a momentum idea and KO does not trend that way."),
    "S": dict(name="Setup S", tag="support-break short", size="$100 · puts · hold up to 10 sessions · not every trade",
        rules=["Today's close is below the lowest low of the prior 20 sessions",
               "50 EMA is below the 200 EMA",
               "Take the move. Do not wait for a target."],
        numbers=["2,221 signals · 49% drop 5%+ within 10 days vs 41% baseline",
                 "31% drop 8%+ vs 23% · null p = 0.000 · survives top-5 removal (46%)",
                 "At VIX 25+: 57% hit. At VIX 16–20: 38% — worse than nothing"],
        works="Volatile years only. 2020 +22pp, 2022 +19pp, 2026 +21pp.",
        fails="Calm years. 2021 −12.5pp, 2023 −8.5pp, 2024 −4.3pp. Second half of the window has +0.3pp edge. Every 63-day short hold in this project loses money — this only works as a 10-day trade."),
}


def get(url, hdr=YF, tries=3):
    for a in range(tries):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read()
            try: raw = gzip.decompress(raw)
            except Exception: pass
            return json.loads(raw)
        except Exception:
            if a == tries - 1: raise
            time.sleep(2 * (a + 1))


_bars = {}
def bars(sym, rng="2y"):
    if sym in _bars: return _bars[sym]
    d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d")
    res = d["chart"]["result"][0]; q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res["timestamp"]):
        if None in (q["close"][i], q["high"][i], q["low"][i]): continue
        out.append((str(datetime.date.fromtimestamp(t)), q["close"][i], q["high"][i], q["low"][i]))
    _bars[sym] = out
    return out


def ema(v, n):
    k = 2 / (n + 1); e = v[0]; o = [e]
    for x in v[1:]:
        e = x * k + e * (1 - k); o.append(e)
    return o


def sma(v, n):
    o = [None] * len(v); s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n: s -= v[i - n]
        if i >= n - 1: o[i] = s / n
    return o


def rsi(c, n=14):
    o = [None] * len(c); g = l = 0.0
    for i in range(1, len(c)):
        ch = c[i] - c[i - 1]; gg, ll = max(ch, 0), max(-ch, 0)
        if i <= n: g += gg / n; l += ll / n
        else: g = (g * (n - 1) + gg) / n; l = (l * (n - 1) + ll) / n
        if i >= n: o[i] = 100 - 100 / (1 + g / l) if l > 0 else 100
    return o


_cik = None; _rev = {}
def revenue_growth(sym):
    global _cik
    if sym in _rev: return _rev[sym]
    try:
        if _cik is None:
            m = get("https://www.sec.gov/files/company_tickers.json", SEC_UA)
            _cik = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in m.values()}
        c = _cik.get(sym)
        if not c: _rev[sym] = None; return None
        g = get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", SEC_UA).get("facts", {}).get("us-gaap", {})
        today = str(datetime.date.today()); best = {}
        for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"):
            for unit, vals in g.get(tag, {}).get("units", {}).items():
                for x in vals:
                    if x.get("form") not in ("10-Q", "10-K"): continue
                    st, en, fl = x.get("start"), x.get("end"), x.get("filed")
                    if not (st and en and fl) or fl > today: continue
                    days = (datetime.date.fromisoformat(en) - datetime.date.fromisoformat(st)).days
                    if not (60 <= days <= 120): continue
                    if en not in best or fl < best[en][0]: best[en] = (fl, x["val"])
        ser = sorted((en, v) for en, (fl, v) in best.items())
        if len(ser) < 8: _rev[sym] = None
        else:
            now = sum(v for _, v in ser[-4:]); prior = sum(v for _, v in ser[-8:-4])
            _rev[sym] = None if prior <= 0 else now / prior - 1
    except Exception:
        _rev[sym] = None
    time.sleep(0.12)
    return _rev[sym]


def scan():
    err = []
    v = bars("^VIX", "6mo"); vc = [x[1] for x in v]
    q = bars("QQQ", "1y"); qc = [x[1] for x in q]
    vnow = vc[-1]
    if vnow < 16: regime, advice = "CALM", "Setup F edge is negative here. Run nothing new."
    elif vnow < 20: regime, advice = "NORMAL", "Setup F best window (+10.76pp). No shorts."
    elif vnow < 25: regime, advice = "ELEVATED", "Setup F yes. Do NOT short — worst zone (−2.89pp)."
    else: regime, advice = "HIGH", "Everything live. Size up longs. Setup S works here."
    peaked = max(vc[-11:]) >= 25; falling = vc[-1] < vc[-2] < vc[-3]
    F, D, C, S, blocked = [], [], [], [], []
    for sym in UNIVERSE:
        try:
            b = bars(sym)
            if len(b) < 260: continue
            c = [x[1] for x in b]; h = [x[2] for x in b]; lo = [x[3] for x in b]
            i = len(c) - 1; px = c[i]
            e10, e30, e50, e200 = ema(c, 10), ema(c, 30), ema(c, 50), ema(c, 200)
            s50 = sma(c, 50); r14 = rsi(c)[i]
            hi252 = max(h[-252:]); dd = 1 - px / hi252
            hi20 = max(h[i - 20:i]); lo20 = min(lo[i - 20:i])
            trend = e10[i] > e30[i] and e50[i] > e200[i]
            cross = any(j > 0 and s50[j] and s50[j - 1] and e10[j] > s50[j] and e10[j - 1] <= s50[j - 1]
                        for j in range(i - 4, i + 1))
            up = (hi252 / px - 1) * 100
            if cross and e50[i] > e200[i] and px > hi20:
                C.append(dict(sym=sym, px=px, tgt=hi252, up=up))
            if 0.05 <= dd < 0.20 and i >= 126 and px > c[i - 126] and r14 and r14 > 55:
                D.append(dict(sym=sym, px=px, dd=dd * 100, rsi=r14, up=up))
            if 0.20 <= dd < 0.45:
                if trend:
                    rg = revenue_growth(sym)
                    if rg is not None and rg > 0.25:
                        F.append(dict(sym=sym, px=px, dd=dd * 100, tgt=hi252, up=up, rev=rg * 100))
                    else:
                        blocked.append(dict(sym=sym, px=px, dd=dd * 100, up=up,
                                            why=f"revenue {rg*100:+.0f}%" if rg is not None else "no SEC data"))
                else:
                    blocked.append(dict(sym=sym, px=px, dd=dd * 100, up=up, why="trend broken"))
            if px < lo20 and e50[i] < e200[i]:
                S.append(dict(sym=sym, px=px, lo20=lo20, below=(px / lo20 - 1) * 100))
            time.sleep(0.05)
        except Exception as e:
            err.append(f"{sym}: {str(e)[:40]}")
    for L in (F, D, C, blocked): L.sort(key=lambda r: -r["up"])
    return dict(date=str(datetime.date.today()),
                stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                vix=vnow, vix5=vc[-5:], vix_hi10=max(vc[-11:]), qqq=qc[-1],
                qdd=(qc[-1] / max(qc[-126:]) - 1) * 100, regime=regime, advice=advice,
                peaked=peaked, falling=falling, vix_fires=peaked and falling,
                F=F, D=D, C=C, S=S, blocked=blocked, errors=err)


def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs_call(s, k, t, sig, r=0.04):
    """Black-Scholes call. Used to MARK open calls; at expiry it equals intrinsic."""
    if t <= 0 or sig <= 0: return max(0.0, s - k)
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t)); d2 = d1 - sig * math.sqrt(t)
    return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)


def mark_trades():
    """Mark every open paper trade, auto-close on target / timeout / expiry, write trades.csv back."""
    rows = list(csv.DictReader(open(TRADES))) if os.path.exists(TRADES) else []
    today = datetime.date.today(); open_, closed = [], []
    for r in rows:
        try:
            b = bars(r["symbol"]); after = [x for x in b if x[0] > r["opened"]]
            spot = b[-1][1]; entry = float(r["entry"]); tgt = float(r["target"] or 0)
            sessions = len(after)
            if r["status"] == "open":
                if r["kind"] == "shares":
                    # arm a floor at the target the first time the high touches it;
                    # close only if a later low falls back to that floor (or at 252)
                    armed_i = next((n for n, x in enumerate(after) if x[2] >= tgt), None) if tgt else None
                    floor_hit = next((x for x in after[armed_i + 1:] if x[3] <= tgt), None) if armed_i is not None else None
                    if floor_hit:
                        r.update(status="closed", closed=floor_hit[0], exit=f"{tgt:.2f}",
                                 pl_pct=f"{(tgt/entry-1)*100:.1f}", note=(r["note"] + " · floored at target").strip(" ·"))
                    elif sessions >= 252:
                        r.update(status="closed", closed=b[-1][0], exit=f"{spot:.2f}",
                                 pl_pct=f"{(spot/entry-1)*100:.1f}", note=(r["note"] + " · 252-session timeout").strip(" ·"))
                else:  # call, marked at intrinsic
                    K = float(r["strike"]); exp = datetime.date.fromisoformat(r["expiry"])
                    intr = max(0.0, spot - K)
                    if today >= exp:
                        r.update(status="closed", closed=str(exp), exit=f"{intr:.2f}",
                                 pl_pct=f"{(intr/entry-1)*100:.1f}", note=(r["note"] + " · expired at intrinsic").strip(" ·"))
            if r["status"] == "open":
                if r["kind"] == "shares":
                    now = spot; pl = (spot / entry - 1) * 100; to_t = (tgt / spot - 1) * 100 if tgt else None
                    prog = max(0, min(1, (spot - entry) / (tgt - entry))) if tgt and tgt > entry else 0
                    armed = tgt and any(x[2] >= tgt for x in after)
                    extra = dict(now=now, pl_pct=pl, to_target=to_t, progress=prog, sessions=sessions, armed=bool(armed))
                else:
                    K = float(r["strike"]); exp = datetime.date.fromisoformat(r["expiry"])
                    c60 = [x[1] for x in b[-61:]]
                    sig = max(0.15, min(1.5, (sum((math.log(c60[j + 1] / c60[j])) ** 2 for j in range(len(c60) - 1)) / max(1, len(c60) - 1)) ** 0.5 * math.sqrt(252)))
                    dte = (exp - today).days
                    est = bs_call(spot, K, max(0.0, dte) / 365, sig)
                    extra = dict(now=est, pl_pct=(est / entry - 1) * 100, spot=spot, intrinsic=max(0.0, spot - K),
                                 dte=dte, sessions=sessions, mark="est")
                open_.append({**r, **extra})
            else:
                closed.append({**r, "result": "win" if float(r["pl_pct"] or 0) > 0 else "loss"})
        except Exception as e:
            open_.append({**r, "error": str(e)[:40]})
    with open(TRADES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TCOLS); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in TCOLS})
    closed.sort(key=lambda r: r["closed"], reverse=True)
    return open_, closed


def week(d, hist, closed, open_):
    days = hist[-5:]
    cut = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    wk_closed = [r for r in closed if r["closed"] >= cut]
    by = {}
    for r in wk_closed:
        s = by.setdefault(r["setup"], dict(wins=0, losses=0, pl=[]))
        s["wins" if r["result"] == "win" else "losses"] += 1; s["pl"].append(float(r["pl_pct"]))
    unreal = {}
    for r in open_:
        if "pl_pct" in r and isinstance(r["pl_pct"], (int, float)):
            unreal.setdefault(r["setup"], []).append(r["pl_pct"])
    verdict = {}
    for k in set(list(by) + list(unreal)):
        b = by.get(k, dict(wins=0, losses=0, pl=[])); u = unreal.get(k, [])
        verdict[k] = dict(wins=b["wins"], losses=b["losses"],
                          closed_mean=(sum(b["pl"]) / len(b["pl"])) if b["pl"] else None,
                          open_n=len(u), open_mean=(sum(u) / len(u)) if u else None)
    produced = sorted({(x["date"], s, k) for x in days for k in ("F", "D", "C", "S") for s in x.get(k, [])})
    return dict(days=days, closed=wk_closed, by_setup=verdict,
                produced=[dict(date=a, sym=b, setup=c) for a, b, c in produced])


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    d = scan()
    open_, closed = mark_trades()
    hist = json.load(open(HIST)) if os.path.exists(HIST) else []
    entry = dict(date=d["date"], vix=d["vix"], regime=d["regime"], vix_fires=d["vix_fires"],
                 F=[x["sym"] for x in d["F"]], D=[x["sym"] for x in d["D"]],
                 C=[x["sym"] for x in d["C"]], S=[x["sym"] for x in d["S"]])
    hist = [h for h in hist if h["date"] != d["date"]] + [entry]
    json.dump(hist, open(HIST, "w"), indent=1)
    d.update(open=open_, closed=closed, week=week(d, hist, closed, open_), setups=SETUPS)
    json.dump(d, open(os.path.join(DOCS, "data.json"), "w"), indent=1, default=str)
    print(f"{d['date']}  vix {d['vix']:.2f} {d['regime']}  F={len(d['F'])} D={len(d['D'])} C={len(d['C'])} "
          f"S={len(d['S'])}  open={len(open_)} closed={len(closed)}  errors={len(d['errors'])}")
