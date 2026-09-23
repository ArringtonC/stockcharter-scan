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
  calls   mark with a real quote (Schwab, then Alpaca), falling back to Black-Scholes; close at expiry at intrinsic
"""
import urllib.request, urllib.parse, json, datetime, time, os, gzip, csv, math
from zoneinfo import ZoneInfo

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

# Part 2: the basket he trades. Part 1 (UNIVERSE, the setups) is untouched; these
# only tag alerts CORE, and the ones outside UNIVERSE run the same check() on the side.
CORE = "AAPL NVDA TSLA BAC BE GOOGL AMZN META QQQ SPY".split()
PART2 = [s for s in CORE if s not in UNIVERSE]

# Known-in-advance market days, Central time. From bls.gov/schedule and
# federalreserve.gov on 2026-09-23. ponytail: typed by hand; add 2027 in December.
EVENTS = {"2026-10-02": "jobs report 7:30 CT", "2026-10-14": "CPI 7:30 CT",
          "2026-10-28": "Fed decision 1:00 CT", "2026-11-06": "jobs report 7:30 CT",
          "2026-11-10": "CPI 7:30 CT", "2026-12-04": "jobs report 7:30 CT",
          "2026-12-09": "Fed decision 1:00 CT", "2026-12-10": "CPI 7:30 CT"}

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
                 "VIX 16–20 is where it adds the most over baseline (+10.76pp); 25+ is where it pays the most (+34% mean). Under 16 the edge is negative."],
        works="Bull markets, moderate fear. Five years of six.",
        fails="2022: 5% hit rate. A prior-high target cannot work when the index spends 91% of the year 10%+ below its own high. Adding 'QQQ above its 200 EMA' turns it off in a bear (93% hit vs 27% when rejected)."),
    "E": dict(name="Setup E", tag="retired — replaced by Setup F on 2026-09-09",
        size="shares · target = the prior high · no stop",
        rules=["Price is 20–45% below its 252-day high",
               "10 EMA above 30 EMA, and 50 EMA above 200 EMA",
               "Target is the prior high. No tight stop.",
               "NO earnings condition — that is the whole difference from Setup F."],
        numbers=["356 signals · 71% hit rate · +46.7% a year with the trend filter",
                 "Without the trend filter: 51% hit, +33.0% a year — the filter was worth +13.7pp",
                 "Setup F is this plus SEC revenue +25%, and F survives the top-5 concentration test"],
        works="The best per-trade idea in the project, and Arrington's own. The trend filter inside it was his catch too, from a PYPL chart.",
        fails="Superseded, not broken. Seven open paper trades still carry the E tag — they were opened before the revenue condition existed, and are left marked rather than quietly relabelled."),
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
        # Keep the first four fields stable: the scanner has historically read
        # date/close/high/low by position. Open and volume are appended for the
        # richer website charts without changing any scan or trade logic.
        open_ = q.get("open", [])[i] if i < len(q.get("open", [])) else None
        volume = q.get("volume", [])[i] if i < len(q.get("volume", [])) else None
        out.append((str(datetime.date.fromtimestamp(t)), q["close"][i], q["high"][i], q["low"][i],
                    open_ if open_ is not None else q["close"][i], volume or 0))
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


def check(sym, F, D, C, S, blocked):
    """Every setup rule for one name. Part 1 and part 2 both run exactly this."""
    b = bars(sym)
    if len(b) < 260: return
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


def scan():
    err = []
    v = bars("^VIX", "6mo"); vc = [x[1] for x in v]
    q = bars("QQQ", "1y"); qc = [x[1] for x in q]
    vnow = vc[-1]
    if vnow < 16: regime, advice = "CALM", "Setup F edge is negative here. Run nothing new."
    elif vnow < 20: regime, advice = "NORMAL", "Setup F adds the most here (+10.76pp vs baseline). It pays more at 25+. No shorts."
    elif vnow < 25: regime, advice = "ELEVATED", "Setup F yes. Do NOT short — worst zone (−2.89pp)."
    else: regime, advice = "HIGH", "Everything live. Size up longs. Setup S works here."
    peaked = max(vc[-11:]) >= 25; falling = vc[-1] < vc[-2] < vc[-3]
    F, D, C, S, blocked = [], [], [], [], []
    for sym in UNIVERSE:
        try:
            check(sym, F, D, C, S, blocked)
            time.sleep(0.05)
        except Exception as e:
            err.append(f"{sym}: {str(e)[:40]}")
    for L in (F, D, C, blocked): L.sort(key=lambda r: -r["up"])
    return dict(date=str(datetime.date.today()),
                stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                # how many names the scan actually evaluated. The page used to infer this
                # from the union of setup hits, which read "1 names" on a quiet day.
                universe=len(UNIVERSE), scanned=len(UNIVERSE) - len(err),
                vix=vnow, vix5=vc[-5:], vix_hi10=max(vc[-11:]), qqq=qc[-1],
                qdd=(qc[-1] / max(qc[-126:]) - 1) * 100, regime=regime, advice=advice,
                peaked=peaked, falling=falling, vix_fires=peaked and falling,
                F=F, D=D, C=C, S=S, blocked=blocked, errors=err, sectors=sectors(), today=today_trades(), taken=TAKEN, weekly=weekly_cross())


def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs_call(s, k, t, sig, r=0.04):
    """Black-Scholes call. Used to MARK open calls; at expiry it equals intrinsic."""
    if t <= 0 or sig <= 0: return max(0.0, s - k)
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t)); d2 = d1 - sig * math.sqrt(t)
    return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)


_CHAIN = {}
def chain_iv(sym, expiry, strike):
    """Implied vol of one call from the live Yahoo chain. None if unavailable. Cached per (sym, expiry)."""
    key = (sym, expiry)
    if key not in _CHAIN:
        _CHAIN[key] = {}
        try:
            import http.cookiejar
            if "_op" not in _CHAIN:
                op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                op.addheaders = [("User-Agent", "Mozilla/5.0")]
                try: op.open("https://fc.yahoo.com", timeout=10)
                except Exception: pass
                _CHAIN["_op"] = (op, op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode())
            op, crumb = _CHAIN["_op"]
            ts = int(datetime.datetime.fromisoformat(expiry).replace(tzinfo=datetime.timezone.utc).timestamp())
            rr = json.load(op.open(f"https://query2.finance.yahoo.com/v7/finance/options/{sym}?date={ts}&crumb={crumb}", timeout=25))
            for c in rr["optionChain"]["result"][0]["options"][0].get("calls", []):
                if c.get("impliedVolatility"): _CHAIN[key][float(c["strike"])] = float(c["impliedVolatility"])
        except Exception:
            pass
    iv = _CHAIN[key].get(float(strike))
    return max(0.05, min(3.0, iv)) if iv else None


# ---------------------------------------------------------------- option quotes
# Real quotes beat the Black-Scholes estimate. Tried in order; each one degrades to
# the next, so the page still works with no credentials at all:
#   1 Schwab   (SCHWAB_KEY / SCHWAB_SECRET / SCHWAB_REFRESH)  real bid/ask, the broker we trade at
#   2 Alpaca   (ALPACA_KEY / ALPACA_SECRET)                   real bid/ask, free tier
#   3 Yahoo chain IV  -> Black-Scholes                        no credentials needed
#   4 60-day realized vol -> Black-Scholes                    always available
def occ(sym, expiry, strike, pad=True, right="C"):
    """OCC option symbol. Schwab pads the root to 6 chars; Alpaca does not."""
    y, m, d = expiry.split("-")
    root = f"{sym:<6s}" if pad else sym
    return f"{root}{y[2:]}{m}{d}{right}{int(round(float(strike) * 1000)):08d}"

_TOK = {}
def schwab_quote(sym, expiry, strike):
    """Mid of the real bid/ask from Schwab. None unless all three env vars are set."""
    k, sec, ref = (os.environ.get(x) for x in ("SCHWAB_KEY", "SCHWAB_SECRET", "SCHWAB_REFRESH"))
    if not (k and sec and ref): return None
    try:
        import base64, urllib.parse
        if "t" not in _TOK or time.time() > _TOK.get("exp", 0):
            body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": ref}).encode()
            auth = base64.b64encode(f"{k}:{sec}".encode()).decode()
            rq = urllib.request.Request("https://api.schwabapi.com/v1/oauth/token", data=body,
                                        headers={"Authorization": f"Basic {auth}",
                                                 "Content-Type": "application/x-www-form-urlencoded"})
            j = json.load(urllib.request.urlopen(rq, timeout=20))
            _TOK["t"] = j["access_token"]; _TOK["exp"] = time.time() + j.get("expires_in", 1800) - 60
        u = "https://api.schwabapi.com/marketdata/v1/quotes?symbols=" + urllib.request.quote(occ(sym, expiry, strike))
        rq = urllib.request.Request(u, headers={"Authorization": f"Bearer {_TOK['t']}"})
        j = json.load(urllib.request.urlopen(rq, timeout=20))
        q = list(j.values())[0].get("quote", {})
        b, a = q.get("bidPrice") or 0, q.get("askPrice") or 0
        return (b + a) / 2 if b > 0 and a > 0 else (q.get("lastPrice") or None)
    except Exception:
        return None

def alpaca_quote(sym, expiry, strike, right="C"):
    """Mid of the real bid/ask from Alpaca's free options feed. None without keys."""
    k, sec = os.environ.get("ALPACA_KEY"), os.environ.get("ALPACA_SECRET")
    if not (k and sec): return None
    try:
        o = occ(sym, expiry, strike, pad=False, right=right)
        rq = urllib.request.Request(
            f"https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols={o}",
            headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": sec})
        q = json.load(urllib.request.urlopen(rq, timeout=20)).get("quotes", {}).get(o, {})
        b, a = q.get("bp") or 0, q.get("ap") or 0
        return (b + a) / 2 if b > 0 and a > 0 else None
    except Exception:
        return None

def option_mark(sym, expiry, strike, spot, dte, realized):
    """(price, source-tag). Real quote if any source answers, else Black-Scholes."""
    for fn, tag in ((schwab_quote, "schwab"), (alpaca_quote, "alpaca")):
        v = fn(sym, expiry, strike)
        if v and v > 0: return v, tag
    iv = chain_iv(sym, expiry, strike)
    return bs_call(spot, float(strike), max(0.0, dte) / 365, iv or realized), ("iv" if iv else "est")


SECTORS = {"XLK": "Technology", "XLF": "Financials", "XLV": "Health Care", "XLY": "Consumer Disc",
           "XLP": "Staples", "XLE": "Energy", "XLI": "Industrials", "XLB": "Materials",
           "XLU": "Utilities", "XLRE": "Real Estate", "XLC": "Communications"}

_HOLD = {}
def holdings(sym):
    """Top-10 holdings of a sector ETF, from Yahoo. Cached per run, [] on failure."""
    if sym in _HOLD: return _HOLD[sym]
    out = []
    try:
        import http.cookiejar
        if "_op" not in _HOLD:
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            op.addheaders = [("User-Agent", "Mozilla/5.0")]
            try: op.open("https://fc.yahoo.com", timeout=10)
            except Exception: pass
            _HOLD["_op"] = (op, op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10).read().decode())
        op, crumb = _HOLD["_op"]
        j = json.load(op.open(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}?modules=topHoldings&crumb={crumb}", timeout=25))
        for h in j["quoteSummary"]["result"][0]["topHoldings"].get("holdings", []):
            out.append(dict(sym=h["symbol"], name=h.get("holdingName", ""),
                            pct=round(h["holdingPercent"]["raw"] * 100, 2)))
    except Exception:
        pass
    _HOLD[sym] = out
    return out

def sectors():
    """The leadership board. CONTEXT ONLY — rotation was tested twice and carries no
    signal (thesis/rotation.md): buying last month's leader loses to SPY by 0.25pp, and
    86% of leadership spells last exactly one month. The history grid exists to make
    that churn visible, not to be traded."""
    WEEKS, STEP = 10, 5                       # ten weekly snapshots, one per 5 sessions
    try:
        spy = bars("SPY"); sc = [x[1] for x in spy]
        if len(sc) < 300: return []
    except Exception:
        return []
    px = {}
    for sym in SECTORS:
        try:
            b = bars(sym); c = [x[1] for x in b]
            if len(c) >= 300: px[sym] = c
        except Exception:
            continue
    if len(px) < 8: return []
    n = min(len(v) for v in px.values())
    # rank on 21-session return at each weekly snapshot, most recent first
    hist = []
    for w in range(WEEKS):
        k = n - 1 - w * STEP
        if k - 21 < 0: break
        r = {sym: c[k] / c[k - 21] - 1 for sym, c in px.items()}
        hist.append([sym for sym in sorted(r, key=lambda x: -r[x])])
    if not hist: return []
    now, wk_ago = hist[0], hist[min(1, len(hist) - 1)], 
    mo_ago = hist[min(4, len(hist) - 1)]
    spy_1m = sc[-1] / sc[-22] - 1
    out = []
    for sym, name in SECTORS.items():
        if sym not in px: continue
        c = px[sym]; e50, e200 = ema(c, 50), ema(c, 200)
        out.append(dict(sym=sym, name=name, px=c[-1],
                        gap=(e50[-1] / e200[-1] - 1) * 100,
                        up=e50[-1] > e200[-1],
                        rel=((c[-1] / c[-22] - 1) - spy_1m) * 100,
                        rank=now.index(sym) + 1,
                        d_week=wk_ago.index(sym) - now.index(sym),
                        d_month=mo_ago.index(sym) - now.index(sym),
                        track=[h.index(sym) + 1 for h in hist],
                        hold=holdings(sym),
                        ours=sorted(x["sym"] for x in holdings(sym) if x["sym"] in set(UNIVERSE))))
    out.sort(key=lambda r: r["rank"])
    # how much churn is in the window?
    churn = sum(1 for a, b in zip(hist, hist[1:]) if a[0] != b[0])
    for r in out: r["churn"] = f"{churn} of {len(hist)-1}"
    return out

# Trades of the day — the four Arrington asked to track on 2026-09-17, in his format.
# cost/breakeven/target are fixed at entry; `now` and `profit` are marked each scan.
# Trades actually taken with real money, closed. Each links to its post-mortem.
# The lesson column is the one sentence worth carrying forward.
TAKEN = [
    dict(sym="PYPL", contract="53 Call 9/25", n=2, paid=1.66, cost=332,
         opened="2026-09-10", closed="2026-09-11", exit=1.91,
         pl=50, pct=15.1, setup=None,
         lesson="Cut early on a trade with no setup behind it. The exit was the reason it won.",
         link=None),
    dict(sym="BABA", contract="109 Call 10/2", n=1, paid=4.25, cost=425,
         opened="2026-09-14", closed="2026-09-16", exit=3.43,
         pl=-82, pct=-19.3, setup=None,
         lesson="Stop was 1.38% from entry against a 1.40% median day. A 5% stop was never touched and returns +$220.",
         link="trades/2026-09-14-BABA-109C.html"),
    dict(sym="QQQ", contract="748 Call 9/25", n=1, paid=2.24, cost=224,
         opened="2026-09-23", closed="2026-09-23", exit=1.00,
         pl=-124, pct=-55.4, setup=None,
         lesson="Bought the day after a $20 jump, near the open high, 2 days to expiry. No setup fired on QQQ.",
         link=None),
]

TODAY = [
    dict(sym="SG",   kind="call",   strike=3,   expiry="2028-01-21", prem=4.60, n=1,
         target=17.60, odds=None,
         why="Conviction hold, not a signal. Revenue +4.4% sits in the flat zone no setup wants.",
         exit="No mechanical exit. 491 days. Sell if the thesis on the business changes.",
         kills="A close under $5.67, the 2026 low. Below that the recovery case is gone."),
    dict(sym="NOW",  kind="shares", strike=None, expiry=None,        prem=139.29, n=4,
         target=194.73, odds=83,
         why="The only real Setup F. 50/200 cross held through the Fed. Revenue +29.4%.",
         exit="Reach $194.73, then FLOOR it there — do not sell. Close only if it falls back to the floor, or at 252 sessions.",
         kills="Nothing. No stop, by design — six tests say stops make this worse."),
    dict(sym="QCOM", kind="call",   strike=240, expiry="2026-11-20", prem=3.92, n=1,
         target=253.72, odds=None,
         why="Setup C fired 09-15 but breadth fails and revenue +4% fails F. Up 12.6% in ten sessions — this is the chase.",
         exit="Sell at +100% or by Nov 13, a week before expiry. Do not hold into the last week.",
         kills="A close back under $180, which would undo the breakout that produced the signal."),
    dict(sym="SNAP", kind="call",   strike=5,   expiry="2028-01-21", prem=2.34, n=2,
         target=12.34, odds=None,
         why="Halfway state: 50 EMA needs 44c to cross the 200. Revenue +15.7%, under F's +25% bar.",
         exit="No mechanical exit. 491 days is the whole point — it buys time for the cross.",
         kills="A close under $4.71, the 2026 low. The halfway state would become all-trends-down, which hits 39%."),
]
def today_trades():
    """Mark each of the four. Returns [] if prices fail rather than half a table."""
    out = []
    for t in TODAY:
        try:
            spot = bars(t["sym"])[-1][1]
        except Exception:
            continue
        cost = t["prem"] * (100 if t["kind"] == "call" else 1) * t["n"]
        be   = (t["strike"] + t["prem"]) if t["kind"] == "call" else t["prem"]
        val  = (max(0.0, t["target"] - t["strike"]) * 100 * t["n"]) if t["kind"] == "call" else t["target"] * t["n"]
        if t["kind"] == "call":
            plain = (f'Buy {t["n"]} {t["sym"]} call{"s" if t["n"] > 1 else ""}, ${t["strike"]:g} strike, '
                     f'expires {datetime.date.fromisoformat(t["expiry"]).strftime("%b %d, %Y")}.')
        else:
            plain = f'Buy {t["n"]} shares of {t["sym"]}.'
        days = (datetime.date.fromisoformat(t["expiry"]) - datetime.date.today()).days if t["expiry"] else None
        out.append(dict(sym=t["sym"], kind=t["kind"], plain=plain, why=t["why"],
                        exit=t["exit"], kills=t["kills"], odds=t["odds"], days=days,
                        now=spot, target=t["target"], cost=cost, be=be,
                        loss=(-cost if t["kind"] == "call" else None),
                        profit=val - cost, ret=(val / cost - 1) * 100 if cost else 0,
                        move=(t["target"] / spot - 1) * 100,
                        strike=t["strike"], expiry=t["expiry"], n=t["n"], prem=t["prem"]))
    return out


def weekly_cross():
    """Weekly 10/30 EMA cross. WATCHLIST ONLY — tested 2026-09-18 and it does not pass:
    +2.27pp raw at 13 weeks but +0.49pp ex-top-5, p=0.363. It is kept because it is by far
    the best-behaved member of the EMA-cross family: the DAILY version of the same signal
    is -0.97pp and p=0.991. Weekly beats daily by 3.2pp. That is worth watching, not trading."""
    out = []
    for sym in UNIVERSE:
        try:
            d = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5y&interval=1wk")
            q = d["chart"]["result"][0]["indicators"]["quote"][0]
            rows = [(c, h) for c, h in zip(q["close"], q["high"]) if c and h]
            if len(rows) < 60: continue
            c = [x[0] for x in rows]; h = [x[1] for x in rows]
            e10, e30 = ema(c, 10), ema(c, 30); i = len(c) - 1
            hi = max(h[-52:]); dd = (1 - c[i] / hi) * 100
            gap = (e10[i] - e30[i]) / c[i] * 100
            state = ("crossed" if (e10[i] > e30[i] and e10[i-4] <= e30[i-4])
                     else "near" if -6 < gap < 0 else None)
            if not state: continue
            out.append(dict(sym=sym, px=c[i], gap=gap, dd=dd, hi=hi,
                            up=(e30[i] > e30[i-4]), state=state,
                            band=(20 <= dd <= 45)))
        except Exception:
            continue
    out.sort(key=lambda r: (r["state"] != "crossed", -r["gap"]))
    return out


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
                    est, src = option_mark(r["symbol"], r["expiry"], K, spot, dte, sig)
                    extra = dict(now=est, pl_pct=(est / entry - 1) * 100, spot=spot, intrinsic=max(0.0, spot - K),
                                 dte=dte, sessions=sessions, mark=src)
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


# ── the report ─────────────────────────────────────────────────────────────────
# Everything else on this site says what is true right now. This says what
# changed and what it means, which is the only thing a daily scan cannot tell you.
#
# Four obligations, borrowed and kept honest:
#   notice a relationship you could miss · distinguish a changed number from a
#   changed situation · say why a number matters in YOUR plan · make an unresolved
#   decision obvious without making it for you.
#
# The plan's own constants. Update BALANCE when you deposit or the account moves;
# nothing else on this site knows what your account is actually worth.
# start is the real balance on the day the plan began, not a round number. The
# $8,500 the plan was drafted with was an estimate; this is the account.
PLAN = dict(start=8355.13, deposit=1000.0, target=30000.0,
            target_date="2027-12-01", started="2026-09-20",
            balance=8355.13, balance_as_of="2026-09-20",
            # add {"date": "2026-10-01", "amt": 1000} each time one lands
            deposits=[])


REPORT_STATE = os.path.join(DOCS, ".report-state.json")


def _clean(t):
    """Did this trade follow the rules that existed when it was opened?
    Outcome is deliberately not consulted."""
    bad = []
    if (t.get("setup") or "none") in ("none", "", None): bad.append("no setup")
    if t.get("kind") == "call" and t.get("opened") and t.get("expiry"):
        try:
            held = (datetime.date.fromisoformat(t["expiry"])
                    - datetime.date.fromisoformat(t["opened"])).days
            if held < 45: bad.append(f"{held} days at entry")
        except Exception: pass
    return (not bad), bad


def week_key(iso):
    y, w, _ = datetime.date.fromisoformat(iso).isocalendar()
    return f"{y}-W{w:02d}"


def report(d, hist, open_, closed):
    """A week, with last week beside it. Before and after is the whole device —
    a number on its own says nothing, the same number twice says everything."""
    today = datetime.date.fromisoformat(d["date"])
    wk = week_key(d["date"])
    period = [x for x in hist if week_key(x["date"]) == wk]
    mon = today - datetime.timedelta(days=today.weekday())
    fri = mon + datetime.timedelta(days=4)
    final = today.weekday() >= 4 and datetime.datetime.now(datetime.timezone.utc).hour >= 20

    prev = {}
    if os.path.exists(REPORT_STATE):
        try: prev = json.load(open(REPORT_STATE))
        except Exception: prev = {}
    weeks = prev.get("weeks") or []
    before = next((w for w in reversed(weeks) if w.get("week") != wk), None)

    # ── where you are ─────────────────────────────────────────────────────────
    real_open = [r for r in open_ if r["acct"] == "real"]
    paper_open = [r for r in open_ if r["acct"] != "real"]
    def avg(rows):
        v = [r["pl_pct"] for r in rows if r.get("pl_pct") is not None]
        return round(sum(v) / len(v), 1) if v else None
    taken = d.get("taken", [])
    now = dict(
        week=wk, date=d["date"],
        balance=PLAN["balance"],
        real_pl=avg(real_open), paper_pl=avg(paper_open),
        real_net=sum(t.get("pl", 0) for t in taken),
        open_n=len(open_), real_n=len(real_open),
        fired=len([x for x in period if x["F"] or x["vix_fires"]]),
        sessions=len(period),
        pos={r["id"]: dict(sym=r["symbol"], kind=r.get("kind"), strike=r.get("strike"),
                           dte=r.get("dte"), now=r.get("now"), pl=r.get("pl_pct"),
                           acct=r["acct"], setup=r.get("setup")) for r in open_},
    )

    def pair(label, key, fmt="pct", sub=""):
        b = (before or {}).get(key)
        a = now.get(key)
        return dict(label=label, before=b, after=a, fmt=fmt, sub=sub,
                    delta=(None if (b is None or a is None) else round(a - b, 2)))

    changed = [
        pair("Account", "balance", "money", "hand-entered"),
        pair("Open · real", "real_pl", "pct", f"{now['real_n']} position" + ("" if now["real_n"] == 1 else "s")),
        pair("Open · paper", "paper_pl", "pct", f"{len(paper_open)} positions"),
        pair("Closed real money", "real_net", "money", f"{len(taken)} trades"),
    ]

    # ── trades this week ──────────────────────────────────────────────────────
    week_trades = []
    for t in taken:
        if t.get("closed") and mon.isoformat() <= t["closed"] <= fri.isoformat():
            week_trades.append(dict(sym=t["sym"], what=t.get("contract"), act="closed",
                                    on=t["closed"], pl=t.get("pl"), pct=t.get("pct")))
    for r in open_:
        if r.get("opened") and mon.isoformat() <= r["opened"] <= fri.isoformat():
            week_trades.append(dict(sym=r["symbol"],
                                    what=(f"{r.get('strike')}C {r.get('expiry')}" if r.get("kind") == "call"
                                          else "shares"),
                                    act="opened", on=r["opened"], pl=None, pct=r.get("pl_pct")))
    week_trades.sort(key=lambda x: x["on"])

    # ── positions, before and after ───────────────────────────────────────────
    bpos = (before or {}).get("pos") or {}
    rows = []
    for pid, p in now["pos"].items():
        b = bpos.get(pid)
        rows.append(dict(sym=p["sym"], kind=p["kind"], strike=p["strike"], dte=p["dte"],
                         acct=p["acct"], setup=p["setup"],
                         before=(b or {}).get("now"), after=p["now"],
                         before_pl=(b or {}).get("pl"), after_pl=p["pl"],
                         isnew=b is None))
    rows.sort(key=lambda r: (r["acct"] != "real", -(r["after_pl"] or -999)))
    gone = [dict(sym=b["sym"], kind=b.get("kind"), pl=b.get("pl"))
            for pid, b in bpos.items() if pid not in now["pos"]]

    # ── notes ─────────────────────────────────────────────────────────────────
    real_all = [dict(sym=t["sym"], setup=t.get("setup"), kind="call", opened=t.get("opened"),
                     expiry=None) for t in taken]
    for r in real_open:
        real_all.append(dict(sym=r["symbol"], setup=r.get("setup"), kind=r.get("kind"),
                             opened=r.get("opened"), expiry=r.get("expiry")))
    for t in real_all: t["ok"], t["broke"] = _clean(t)
    broke = [t for t in real_all if not t["ok"]]

    notes = []
    if broke and len(broke) == len(real_all) and real_all:
        notes.append(f"All {len(real_all)} real trades so far were taken outside the rules "
                     f"({', '.join(sorted({b for t in broke for b in t['broke']}))}). "
                     f"The scanner fired on {now['fired']} of {now['sessions']} sessions this week. "
                     f"The gap is between what it found and what was bought.")
    win_broke = [r for r in real_open if (r.get("pl_pct") or 0) > 0 and not _clean(r)[0]]
    if win_broke:
        w = win_broke[0]
        notes.append(f"{w['symbol']} is up {w['pl_pct']:.1f}% on a trade that broke a rule going in. "
                     f"A good outcome is not a good decision.")
    decay = sorted([r for r in open_ if r.get("kind") == "call" and (r.get("dte") or 99) <= 21],
                   key=lambda r: r["dte"])
    if decay:
        notes.append(f"{len(decay)} call{'s' if len(decay) > 1 else ''} under 21 days: "
                     + ", ".join(f"{r['symbol']} {r['dte']}d" for r in decay)
                     + ". The rule cannot undo a position you already own — it only says do not add another.")
    if len(taken) < 20:
        notes.append(f"{len(taken)} closed real trades is a tally, not a track record. "
                     f"Until there are more, the deposits are what move the account.")

    # ── the goal ──────────────────────────────────────────────────────────────
    deposited = sum(x["amt"] for x in PLAN["deposits"])
    goal = dict(balance=PLAN["balance"], target=PLAN["target"],
                pct=round(PLAN["balance"] / PLAN["target"] * 100, 1),
                to_go=round(PLAN["target"] - PLAN["balance"]),
                target_date=PLAN["target_date"],
                floor=round(PLAN["start"] + deposited), deposits=PLAN["deposits"],
                need=round((PLAN["target"] - PLAN["start"] - PLAN["deposit"] * 15) / 15),
                as_of=PLAN["balance_as_of"])

    weeks = [w for w in weeks if w.get("week") != wk][-11:] + [now]
    json.dump(dict(weeks=weeks), open(REPORT_STATE, "w"), indent=1)

    return dict(week=wk, final=final, from_=mon.isoformat(), to_=fri.isoformat(),
                had_before=before is not None,
                before_week=(before or {}).get("week"), before_date=(before or {}).get("date"),
                here=dict(balance=PLAN["balance"], as_of=PLAN["balance_as_of"],
                          open_n=len(open_), real_n=len(real_open),
                          real_pl=now["real_pl"], paper_pl=now["paper_pl"],
                          fired=now["fired"], sessions=now["sessions"]),
                changed=changed, trades=week_trades, positions=rows, gone=gone,
                notes=notes, goal=goal)


# ── charts ─────────────────────────────────────────────────────────────────────
# bars() already cached every name's 2-year history while the scan ran, so this
# costs one extra fetch (the S&P) and writes only the names you actually hold or
# that fired. 51 charts of names you do not own would go stale and never be opened.
def chart_data(d, open_, n=180):
    want = {r["symbol"] for r in open_}
    want |= {x["sym"] for k in ("F", "C", "S") for x in d.get(k, [])}
    out = {}
    for sym in sorted(want) + ["^GSPC", "QQQ"]:
        try:
            b = bars(sym)
        except Exception:
            continue
        if len(b) < 60: continue
        b = b[-n:]
        c = [x[1] for x in b]
        e10, e30 = ema(c, 10), ema(c, 30)
        out[sym] = dict(
            d=[x[0] for x in b],
            o=[round(x[4], 2) for x in b],
            h=[round(x[2], 2) for x in b],
            l=[round(x[3], 2) for x in b],
            c=[round(x, 2) for x in c],
            v=[int(x[5]) for x in b],
            e10=[round(x, 2) for x in e10],
            e30=[round(x, 2) for x in e30],
        )
    return out


def third_friday(d):
    """The monthly expiry for d's month. Standard chains are deepest here."""
    f = datetime.date(d.year, d.month, 1)
    f += datetime.timedelta(days=(4 - f.weekday()) % 7)      # first Friday
    return f + datetime.timedelta(days=14)


def affordable(sym, spot, budget=600):
    """Walk the rule's own window - 120 down to 45 days - for the first ATM call
    that fits the cap. Below 45 days the rule says don't, so it stops there and
    reports the cheapest thing it saw instead of breaking a rule to find a fit."""
    best = None
    for days in (120, 90, 60, 45):
        c = pick_contract(sym, spot, days=days, budget=budget)
        if not c: continue
        if not c["over"]: return c
        if best is None or c["cost"] < best["cost"]: best = c
    return best


def pick_contract(sym, spot, days=120, budget=600):
    """The actual trade a fired setup implies, priced.

    Not a recommendation and nothing here is invented — the rules already say
    at-the-money, 90-120 days, $600 cap. This just resolves them to a contract
    that exists and asks what it costs. Returns None if nothing fits.
    """
    target = datetime.date.today() + datetime.timedelta(days=days)
    for m in (0, 1, -1, 2):                                   # nearest monthly that lists
        exp = third_friday(datetime.date(target.year + (target.month - 1 + m) // 12,
                                         (target.month - 1 + m) % 12 + 1, 1))
        if exp <= datetime.date.today(): continue
        e = exp.isoformat()
        chain_iv(sym, e, spot)                                # warms _CHAIN with real strikes
        strikes = [k for k in _CHAIN.get((sym, e), {}) if isinstance(k, float)]
        if not strikes: continue
        strike = min(strikes, key=lambda k: abs(k - spot))     # at the money
        dte = (exp - datetime.date.today()).days
        px, src = option_mark(sym, e, strike, spot, dte, 0.45)
        if not px or px <= 0: continue
        n = int(budget // (px * 100))
        if n < 1: return dict(expiry=e, strike=strike, px=px, src=src, dte=dte,
                              n=0, cost=px * 100, over=True)
        return dict(expiry=e, strike=strike, px=px, src=src, dte=dte,
                    n=n, cost=px * 100 * n, over=False)
    return None


# ── when to actually send ──────────────────────────────────────────────────────
# Running hourly does not mean messaging hourly. Ten identical "nothing fires"
# notes a day is how an alert becomes wallpaper. This pushes on the first run of
# the day, on the closing run, and otherwise only when something changed.
def market(d, open_):
    """What can be known about today's index move, before and during. None of it
    says which way -- thesis/scalp.md: nothing predicted QQQ/SPY direction."""
    m = dict(events=[EVENTS[d["date"]]] if d["date"] in EVENTS else [])
    for s in ("QQQ", "SPY", "NQ=F"):
        try:
            q = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=5m")["chart"]["result"][0]["meta"]
            m[s] = (q["regularMarketPrice"] / q["chartPreviousClose"] - 1) * 100
            m[s + "_px"] = q["regularMarketPrice"]
        except Exception:
            pass
    # earnings today for the basket or anything held
    try:
        mine = set(CORE) | {r["symbol"] for r in open_}
        rows = get(f"https://api.nasdaq.com/api/calendar/earnings?date={d['date']}",
                   {"User-Agent": "Mozilla/5.0", "Accept": "application/json"})["data"]["rows"] or []
        m["events"] += [f"{r['symbol']} earnings" + (" after close" if "after" in r["time"] else
                        " before open" if "pre" in r["time"] else "") for r in rows if r["symbol"] in mine]
    except Exception:
        pass
    # expected move = at-the-money straddle to the nearest Friday
    t = datetime.date.fromisoformat(d["date"]); fri = t + datetime.timedelta((4 - t.weekday()) % 7)
    m["exp_by"] = fri.strftime("%a")
    for s in ("QQQ", "SPY"):
        try:
            k = round(m.get(s + "_px") or d["qqq"]); c = alpaca_quote(s, str(fri), k); p = alpaca_quote(s, str(fri), k, "P")
            if c and p: m[s + "_exp"] = c + p
        except Exception:
            pass
    # biggest basket movers, only once today's bar exists
    mv = []
    for s in CORE:
        if s in ("QQQ", "SPY"): continue
        b = _bars.get(s) or []
        if len(b) > 1 and b[-1][0] == d["date"]: mv.append((s, (b[-1][1] / b[-2][1] - 1) * 100))
    m["movers"] = sorted(mv, key=lambda x: -abs(x[1]))[:3]
    return m


def move_bucket(m):
    """QQQ+1 / SPY-2 ... the push fires when this changes, i.e. on crossing 1% or 2%."""
    b = []
    for s in ("QQQ", "SPY"):
        v = m.get(s)
        if v is not None and abs(v) >= 1: b.append(f"{s}{'+' if v > 0 else '-'}{min(int(abs(v)), 2)}")
    return b


ALERT_STATE = os.path.join(DOCS, ".alert-state.json")

def alert_key(d, open_):
    """What would make this message worth reading. Price alone is not it."""
    fired = sorted([x["sym"] for x in d["F"]]
                   + (["VIX"] if d["vix_fires"] else [])
                   + ([x["sym"] for x in d["S"]] if d["vix"] >= 25 else []))
    soon = sorted(f"{r['symbol']}{r.get('dte')}" for r in open_
                  if r.get("kind") == "call" and (r.get("dte") or 99) <= 21)
    steps = sorted(f"{r['symbol']}{int((r.get('pl_pct') or 0) // 10)}{'!' if (r.get('dte') or 99) <= 7 else ''}"
                   for r in open_ if r.get("acct") == "real")
    return json.dumps(dict(date=d["date"], fired=fired, soon=soon, steps=steps, move=move_bucket(d.get("market", {})),
                           errs=len(d["errors"]), regime=d["regime"]), sort_keys=True)


def should_push(d, open_):
    """(send?, why). Closing run and first-of-day always go; the rest must earn it."""
    now = datetime.datetime.now(datetime.timezone.utc)
    closing = now.hour >= 20                       # after 3pm ET, the session is done
    prev = {}
    if os.path.exists(ALERT_STATE):
        try: prev = json.load(open(ALERT_STATE))
        except Exception: prev = {}
    key = alert_key(d, open_)
    first = prev.get("date") != d["date"]
    changed = prev.get("key") != key
    json.dump(dict(date=d["date"], key=key, at=now.isoformat()), open(ALERT_STATE, "w"))
    if closing: return True, "close"
    if first:   return True, "first run today"
    if changed: return True, "something changed"
    return False, "nothing changed"


# ── push ───────────────────────────────────────────────────────────────────────
# Nine days in ten this message saves you opening the page at all. On the tenth
# it reaches you before the open. No token set means it silently does nothing.
def notify(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT")
    if not (tok and chat): return False
    try:
        body = urllib.parse.urlencode(dict(chat_id=chat, text=text,
                                           parse_mode="HTML",
                                           disable_web_page_preview="true")).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body),
            timeout=20).read()
        return True
    except Exception as e:
        print(f"notify failed: {str(e)[:60]}")
        return False


def left(dte, today=None):
    """114d left is information; the last week is a warning; the last 3 days name the day."""
    if dte is None: return ""
    if dte <= 3:
        day = (today or datetime.date.today()) + datetime.timedelta(days=dte)
        return "<b>EXPIRES TODAY</b>" if dte <= 0 else f"<b>EXPIRES {day.strftime('%a').upper()}</b>"
    return f"<b>⚠ {dte}d left</b>" if dte <= 10 else f"{dte}d left"


def summary(d, open_, closed=()):
    """BUY -> UPDATE -> CLOSED. One job per line, so the whole thing reads without
    doing any arithmetic: what it is, what to do, what it is worth."""
    cap = round(PLAN["balance"] * 0.07 / 50) * 50
    D = lambda x: f"${x:,.0f}"

    def when(iso):
        return datetime.date.fromisoformat(iso).strftime("%b %-d").upper()

    B = []
    for r in d["F"]:
        sym, px = r["sym"], r["px"]
        c = affordable(sym, px, budget=cap)
        B.append(f"<b>BUY {sym} ${px:,.2f}</b>" + (" · CORE" if sym in CORE else ""))
        sh = max(1, int(cap // px))
        shares = f"BUY {sh} SHARE{'S' if sh != 1 else ''} · {D(sh*px)}"
        if c:
            # price per share of one call, the number a broker shows
            B.append(f"{when(c['expiry'])} · ${c['strike']:g} CALL · ${c['cost'] / max(c['n'], 1) / 100:,.2f}")
            # over the cap the setup still wants the call; the cap only reaches the stock
            B.append(f"OVER CAP → {shares}" if c["over"] else
                     f"BUY {c['n']} CALL{'S' if c['n'] != 1 else ''} · {D(c['cost'])}")
        else:
            B.append(shares)
        B.append(f"TARGET ${r['tgt']:,.0f} · +{r['up']:.0f}%")
        B.append("")

    if d["vix_fires"]:
        c = pick_contract("QQQ", d["qqq"], days=30, budget=1000)
        if c:
            n = max(1, c["n"])
            B += [f"<b>BUY QQQ</b>", f"{when(c['expiry'])} · ${c['strike']:g} CALL",
                  f"BUY {n} CONTRACT{'S' if n != 1 else ''} · {D(c['cost'])}",
                  "HOLD 21 SESSIONS", ""]
    if d["vix"] >= 25:
        for r in d["S"]:
            B += [f"<b>SHORT {r['sym']}</b>", f"${r['px']:,.2f} · {r['below']:.1f}% UNDER 20-DAY LOW",
                  "UP TO 10 SESSIONS", ""]

    # every real position, where it started and where it is
    U = []
    for r in open_:
        if r["acct"] != "real": continue
        n = int(r.get("contracts") or 1)
        mult = 100 if r.get("kind") == "call" else 1
        a, b = float(r["entry"]) * mult * n, (r.get("now") or 0) * mult * n
        head = f"{r['symbol']} · {when(r['expiry'])} · ${float(r['strike']):g} CALL" if r.get("kind") == "call" \
               else f"{r['symbol']} · {n} SHARES"
        U += [f"<b>{'↑' if b >= a else '↓'} TRADE UPDATE</b>", head,
              f"{D(a)} → <b>{D(b)}</b>",
              f"{'+' if b >= a else '−'}{D(abs(b - a))} · {r['pl_pct']:+.0f}%"
              + (f" · {left(r['dte'])}" if r.get("dte") is not None else ""), ""]

    C = []
    # the trade log first (it knows the expiry), TAKEN only for anything the log lacks
    done = []
    for r in closed:
        if r.get("acct") != "real" or r.get("closed") != d["date"]: continue
        n = int(r.get("contracts") or 1); mult = 100 if r.get("kind") == "call" else 1
        a = float(r["entry"]) * mult * n
        done.append(dict(sym=r["symbol"], contract=f"{when(r['expiry'])} · ${float(r['strike']):g} CALL" if r.get("kind") == "call" else f"{n} SHARES",
                         cost=a, pl=float(r["exit"]) * mult * n - a, pct=float(r["pl_pct"] or 0)))
    seen = {t["sym"] for t in done}
    done += [t for t in d.get("taken", []) if t.get("closed") == d["date"] and t["sym"] not in seen]
    for t in done:
        C += ["<b>✓ TRADE CLOSED</b>", f"{t['sym']} · {t['contract'].upper()}".replace("  ", " "),
              f"{D(t['cost'])} → <b>{D(t['cost'] + t['pl'])}</b>",
              f"FINAL {'+' if t['pl'] >= 0 else '−'}{D(abs(t['pl']))} · {t['pct']:+.0f}%", ""]

    M = []
    m = d.get("market", {})
    ny = datetime.datetime.now(ZoneInfo("America/New_York"))
    if (ny.hour, ny.minute) < (9, 30):
        M.append("<b>BEFORE THE OPEN</b>")
        if m.get("NQ=F") is not None: M.append(f"NASDAQ FUTURES {m['NQ=F']:+.1f}%")
    elif move_bucket(m):
        M.append(f"<b>{'▲' if (m.get('QQQ') or 0) > 0 else '▼'} BIG MOVE TODAY</b>")
    # price, day move, expected move by Friday -- every message
    for s in ("QQQ", "SPY"):
        if m.get(s + "_px"):
            M.append(f"{s} ${m[s + '_px']:,.2f} {m[s]:+.1f}%"
                     + (f" · ±${m[s + '_exp']:.0f} by {m['exp_by']}" if m.get(s + "_exp") else ""))
    if move_bucket(m) and m.get("movers"):
        M.append(" · ".join(f"{s} {v:+.1f}%" for s, v in m["movers"]))
    M += [e.upper() for e in m.get("events", [])]
    if M: M += ["<i>not a signal</i>", ""]

    L = M + C + B + U
    if not (C + B + U): L = M + ["<b>NOTHING TO BUY TODAY</b>", ""]
    while L and L[-1] == "": L.pop()

    intra = datetime.datetime.now(datetime.timezone.utc).hour < 20
    # direction earns one word: it flips the sign at 20-25 and is the whole of
    # Setup VIX at 25+ (thesis/vix-trend.md, 2026-09-22). Under 16 it changes nothing.
    v5 = d.get("vix5") or []
    dirn = ("" if len(v5) < 5 or abs(d["vix"] / v5[0] - 1) < 0.03
            else (" rising" if d["vix"] > v5[0] else " falling"))
    L += ["", '<a href="https://arringtonc.github.io/stockcharter-scan/">Ledger</a>'
          + f" · VIX {d['vix']:.1f} {d['regime'].lower()}{dirn}"
          + (" · intraday" if intra else "")
          + (f" · <b>{len(d['errors'])} failed</b>" if d["errors"] else "")]
    return "\n".join(L)


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    d = scan()
    open_, closed = mark_trades()
    p2 = dict(F=[], D=[], C=[], S=[], blocked=[])
    for sym in PART2:
        try: check(sym, p2["F"], p2["D"], p2["C"], p2["S"], p2["blocked"])
        except Exception as e: d["errors"].append(f"{sym}: {str(e)[:40]}")
    # part 2 F fires join the BUY list; D/C/S stay watchlist-only exactly as in part 1
    d["F"] += p2["F"]; d["part2"] = p2
    d["market"] = market(d, open_)
    hist = json.load(open(HIST)) if os.path.exists(HIST) else []
    entry = dict(date=d["date"], vix=d["vix"], regime=d["regime"], vix_fires=d["vix_fires"],
                 F=[x["sym"] for x in d["F"]], D=[x["sym"] for x in d["D"]],
                 C=[x["sym"] for x in d["C"]], S=[x["sym"] for x in d["S"]])
    hist = [h for h in hist if h["date"] != d["date"]] + [entry]
    json.dump(hist, open(HIST, "w"), indent=1)
    d.update(open=open_, closed=closed, week=week(d, hist, closed, open_), setups=SETUPS,
             charts=chart_data(d, open_), report=report(d, hist, open_, closed))
    json.dump(d, open(os.path.join(DOCS, "data.json"), "w"), indent=1, default=str)
    print(f"{d['date']}  vix {d['vix']:.2f} {d['regime']}  F={len(d['F'])} D={len(d['D'])} C={len(d['C'])} "
          f"S={len(d['S'])}  open={len(open_)} closed={len(closed)}  errors={len(d['errors'])}"
          f"  charts={len(d['charts'])}")
    send, why = should_push(d, open_)
    if send and notify(summary(d, open_, closed)): print(f"  pushed to telegram ({why})")
    elif not send: print(f"  no push — {why}")
