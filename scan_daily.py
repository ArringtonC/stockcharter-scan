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
EVENTS = {"2026-09-29": "job openings (JOLTS) 9:00 CT",
          "2026-09-30": "inflation + GDP 7:30 CT",   # added 2026-09-28 from a market video's calendar
          "2026-10-02": "jobs report 7:30 CT", "2026-10-14": "CPI 7:30 CT",
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
    dict(sym="BABA", contract="109 Call 10/2", n=1, paid=4.35, cost=435,
         opened="2026-09-14", closed="2026-09-16", exit=3.39,
         pl=-96, pct=-22.1, setup=None,
         lesson="Stop was 1.38% from entry against a 1.40% median day. A 5% stop was never touched and returns +$220.",
         link="trades/2026-09-14-BABA-109C.html"),
    dict(sym="QQQ", contract="748 Call 9/25", n=1, paid=2.27, cost=227,
         opened="2026-09-23", closed="2026-09-23", exit=1.14,
         pl=-113, pct=-49.8, setup=None,
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
            # the paper bot's broker orders own its exits; autotrade.py syncs them in
            if r["status"] == "open" and r.get("acct") not in ("paper-auto", "bot", "bot-leaps"):
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
            balance=9941.87, balance_as_of="2026-10-01",   # Schwab total value 10-01 (Oct 1 deposit not in yet)
            # add {"date": "2026-10-01", "amt": 1000} each time one lands
            deposits=[{"date": "2026-09-22", "amt": 1200.0}])   # ACH into Futures ...116


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
def flags(h, l, c):
    """Bull and bear flags (Setup G note, 2026-10-03; rule fixed before testing):
      pole     close moves 15%+ in 15 sessions or fewer (bear: falls 15%+)
      flag     the next 5-20 sessions drift against the pole in a channel: the line through the highs
               and the line through the lows both slope against the pole (or flat), and the flag gives
               back no more than half the pole
      breakout first close through the flag's far line (bull: above the top line) -> signal
      target   breakout + pole height (the "measured move"); stop = the flag's other edge
    Returns [dict(kind, p0, p1, f1, top=(y at p1, y at f1), bot=(...), brk index or None, target, stop)]."""
    def fit(ys):
        n = len(ys); mx = (n - 1) / 2; my = sum(ys) / n
        sl = sum((i - mx) * (y - my) for i, y in enumerate(ys)) / sum((i - mx) ** 2 for i in range(n))
        return sl, my - sl * mx
    out, i = [], 15
    while i < len(c) - 5:
        found = None
        for bull in (True, False):
            lo = min(range(i - 15, i), key=lambda k: c[k]) if bull else max(range(i - 15, i), key=lambda k: c[k])
            move = c[i] / c[lo] - 1
            if (move < 0.15) if bull else (move > -0.15): continue
            if c[i] != (max if bull else min)(c[lo:i + 1]): continue
            for L in range(20, 4, -1):          # the longest flag that holds
                j = i + L
                if j >= len(c): continue
                hs, ls = h[i + 1:j + 1], l[i + 1:j + 1]
                (sh, ih), (sl_, il) = fit(hs), fit(ls)
                pole = abs(c[i] - c[lo])
                giveback = (c[i] - min(ls)) if bull else (max(hs) - c[i])
                if (sh > 0 or sl_ > 0) if bull else (sh < 0 or sl_ < 0): continue
                if giveback > pole / 2: continue
                if not all((x <= ih + sh * k * 1.0 + 0.02 * c[i]) for k, x in enumerate(hs)) or \
                   not all((x >= il + sl_ * k - 0.02 * c[i]) for k, x in enumerate(ls)): continue
                brk = None
                for k in range(j + 1, min(len(c), j + 6)):
                    edge = (ih + sh * (k - i - 1)) if bull else (il + sl_ * (k - i - 1))
                    if (c[k] > edge) if bull else (c[k] < edge): brk = k; break
                    if (c[k] < il + sl_ * (k - i - 1)) if bull else (c[k] > ih + sh * (k - i - 1)): break
                top = (ih, ih + sh * (L - 1)); bot = (il, il + sl_ * (L - 1))
                found = dict(kind="bull flag" if bull else "bear flag", p0=lo, p1=i, f1=j, top=top, bot=bot, brk=brk,
                             target=(c[brk] + pole if bull else c[brk] - pole) if brk else None,
                             stop=(bot[1] if bull else top[1]))
                break
            if found: break
        if found: out.append(found); i = found["f1"] + 1
        else: i += 1
    return out


def ihs(h, l, c, since):
    """Inverse head & shoulders breakouts at index >= since. Same rule as thesis/patterns/ihs.py
    (a 25%+ fall from a 1-year high to the head, shoulders 3%+ above it and within 12% of each other,
    first close over the flat neckline). Target = neckline + (neckline - head); stop = right shoulder."""
    out, used = [], set()
    for b in range(max(320, since), len(c)):
        hd = min(range(b - 150, b - 30), key=lambda k: l[k])
        if hd in used or min(l[hd + 1:b]) < l[hd]: continue
        pk = max(range(max(0, hd - 252), hd), key=lambda k: h[k])
        if l[hd] > h[pk] * 0.75: continue
        lo_ls = max(pk, hd - 60)
        if hd - 8 <= lo_ls or b - 2 <= hd + 8: continue
        ls = min(range(lo_ls, hd - 8), key=lambda k: l[k]); rs = min(range(hd + 8, b - 2), key=lambda k: l[k])
        if l[ls] < l[hd] * 1.03 or l[rs] < l[hd] * 1.03 or abs(l[ls] - l[rs]) / max(l[ls], l[rs]) > 0.12: continue
        neck = max(h[ls:rs + 1])
        if not (c[b] > neck and all(c[k] <= neck for k in range(rs, b))): continue
        out.append(dict(ls=ls, hd=hd, rs=rs, b=b, neck=neck, target=neck + (neck - l[hd]))); used.add(hd)
    return out


def patterns_for(b, n):
    """Every pattern the scanner knows, drawn on the site chart as notes (none is a tested edge).
    b = full daily bars; only patterns inside the last n bars. Each: kind, lines [[t0,y0,t1,y1,dashed]],
    label {time, text, up}, end (date the pattern last updated)."""
    D = [x[0] for x in b]; c = [x[1] for x in b]; h = [x[2] for x in b]; l = [x[3] for x in b]
    lo = len(b) - n; out = []
    for f in [f for f in flags(h[lo:], l[lo:], c[lo:]) if f["brk"] or f["f1"] >= n - 5][-2:]:   # latest 2: broke out or still forming
        g = lambda k: D[lo + k]; bull = f["kind"] == "bull flag"; fs = f["p1"] + 1
        out.append(dict(kind=f["kind"], end=g(f["brk"] or f["f1"]),
            lines=[[g(f["p0"]), c[lo + f["p0"]], g(f["p1"]), c[lo + f["p1"]], 0],
                   [g(fs), f["top"][0], g(f["f1"]), f["top"][1], 1], [g(fs), f["bot"][0], g(f["f1"]), f["bot"][1], 1]],
            label=dict(time=g(f["brk"] or f["f1"]), up=bull, text=f["kind"].upper() + ("" if f["brk"] else " · forming"))))
    for p in ihs(h, l, c, lo)[-1:]:
        out.append(dict(kind="inverse head & shoulders", end=D[p["b"]],
            lines=[[D[p["ls"]], p["neck"], D[p["b"]], p["neck"], 1],
                   [D[p["ls"]], l[p["ls"]], D[p["hd"]], l[p["hd"]], 0], [D[p["hd"]], l[p["hd"]], D[p["rs"]], l[p["rs"]], 0],
                   [D[p["rs"]], l[p["rs"]], D[p["b"]], c[p["b"]], 0]],
            label=dict(time=D[p["b"]], up=True, text="INVERSE H&S")))
    return out


def chart_data(d, open_, n=504):
    want = {r["symbol"] for r in open_}
    want |= {x["sym"] for k in ("F", "C", "S", "watch") for x in d.get(k, [])}
    lv = {}
    try:
        for x in json.load(open(LEVELS)): lv.setdefault(x["symbol"], []).append(x)
    except Exception: pass
    want |= set(lv)
    PAT = {}
    for sym in set(UNIVERSE) | set(CORE) | want:       # a pattern anywhere in the scan gets a chart
        try:
            b = bars(sym)
            if len(b) < 340: continue
            ps = patterns_for(b, 180)
            w = next((x for x in d.get("watch", []) if x["sym"] == sym), None) or channel_breakout(sym, recent=10)
            if w:
                ps.append(dict(kind="channel breakout", end=w["date"],
                    lines=[[w["peak_date"], w["peak"], b[-1][0], w["peak"], 1],          # the old high = the target
                           [b[[x[0] for x in b].index(w["date"]) - 25][0], w["base_high"], w["date"], w["base_high"], 1]],   # the base it cleared
                    label=dict(time=w["date"], up=True, text="CHANNEL BREAKOUT")))
            for x in lv.get(sym, []):
                t0 = max(x["logged"], b[-60][0])
                for y, name in ((x["up"], "BREAKOUT"), (x["down"], "BREAKDOWN")):
                    ps.append(dict(kind="level", end=x["logged"], lines=[[t0, y, b[-1][0], y, 1]],
                                   label=dict(time=b[-1][0], up=name == "BREAKOUT", text=f"{name} ${y:,.2f} · {x['pattern']}")))
            if ps: PAT[sym] = ps
            recent = b[-10][0]
            if any(p["end"] >= recent for p in ps if p["kind"] != "level"): want.add(sym)
        except Exception:
            continue
    out = {}
    for sym in sorted(want) + ["^GSPC", "QQQ"]:
        try:
            b = bars(sym)
        except Exception:
            continue
        if len(b) < 60: continue
        full = [x[1] for x in b]   # EMAs on the whole history, then cut, so the first bars are right
        e10, e30 = ema(full, 10)[-n:], ema(full, 30)[-n:]
        b = b[-n:]
        c = [x[1] for x in b]
        out[sym] = dict(
            d=[x[0] for x in b],
            o=[round(x[4], 2) for x in b],
            h=[round(x[2], 2) for x in b],
            l=[round(x[3], 2) for x in b],
            c=[round(x, 2) for x in c],
            v=[int(x[5]) for x in b],
            e10=[round(x, 2) for x in e10],
            e30=[round(x, 2) for x in e30],
            pat=PAT.get(sym, []),   # chart notes, not tested setups: drawn so the eye can check them
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
def sp500_changes(n=12):
    """Recent adds/drops to the S&P 500, newest first, from Wikipedia's historical-components
    table (editors list announced changes there before they take effect). ponytail: wikitext
    parsed by hand; switch to S&P's own announcements feed if this table ever changes shape."""
    import re
    url = "https://en.wikipedia.org/w/index.php?title=Historical_components_of_the_S%26P_500&action=raw"
    txt = urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "Ledger/1.0 (stockcharter-scan)"}), timeout=20).read().decode()
    body = txt[txt.index('id="changes"'):]
    out = []
    for row in body.split("\n|-\n")[2:]:
        cells = [c.strip() for c in re.findall(r"^\|\|(.*)$", row, re.M)]
        if len(cells) < 6: continue
        try: eff = datetime.datetime.strptime(cells[0], "%B %d, %Y").date()
        except ValueError: continue
        name = lambda c: re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", c).strip()
        ann = re.search(r"\|\s*date\s*=\s*([^|}]+)", row)
        out.append(dict(effective=str(eff), added=cells[1], added_name=name(cells[2]),
                        removed=cells[3], removed_name=name(cells[4]),
                        reason=re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", cells[5])[:120],
                        announced=ann.group(1).strip() if ann else ""))
        if len(out) >= n: break
    return out


# ── the AI-bubble plan (2026-09-28; BofA/Hartnett's two signals; thesis/bubble-plan.md) ──
# Signal 2 is typed by hand: set the date the day SpaceX or OpenAI prices its IPO.
IPO_SIGNALS = {"SpaceX": "2026-06-11", "OpenAI": None}   # SpaceX priced 2026-06-11 at $135 (SPCX, $75B raised)
BUBBLE_STATE = os.path.join(DOCS, ".bubble-state.json")
PHASES = [
    dict(n=1, name="Ride it", when="No signal has fired",
         do=["Deposits: 60% SPY / 40% QQQ", "Take Setup F BUY cards (~7% each)", "Hold what you own"]),
    dict(n=2, name="One signal", when="CPI 4%+ OR a SpaceX/OpenAI IPO prices",
         do=["Stop adding to QQQ -- new deposits go to SPY only", "Sell half of any position up 50%+", "Keep taking F cards"]),
    dict(n=3, name="Both signals", when="CPI 4%+ AND a mega-IPO has priced",
         do=["Sell the QQQ part, keep SPY", "Deposits pile up as cash", "No puts, no shorting"]),
    dict(n=4, name="The drop", when="Phase 3, then QQQ 20%+ off its high AND VIX 25+ falling 3 days",
         do=["Put the cash into QQQ (Setup VIX)", "F fires more after drops -- take the cards", "Deposits back to 60/40"]),
]


def cpi_yoy(months=36):
    """[(month, CPI % vs a year earlier)] from FRED, newest last."""
    x = urllib.request.urlopen(urllib.request.Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS",
                               headers=YF), timeout=45).read().decode().strip().split("\n")[1:]
    v = {r.split(",")[0][:7]: float(r.split(",")[1]) for r in x if r.split(",")[1]}
    ks = sorted(v)
    return [(k, round((v[k] / v[f"{int(k[:4]) - 1}{k[4:]}"] - 1) * 100, 2)) for k in ks[-months:] if f"{int(k[:4]) - 1}{k[4:]}" in v]


def bubble(d):
    """Current phase of the AI-bubble plan + the two chart series for the site."""
    st_ = json.load(open(BUBBLE_STATE)) if os.path.exists(BUBBLE_STATE) else {}
    cpi = cpi_yoy(); now_cpi = cpi[-1][1]
    # a signal stays fired once it fires (the replay's rule). Look back a year, dated the
    # 15th of the next month -- when that CPI number was actually published.
    hot = [m for m, v in cpi[-12:] if v >= 4]
    if hot and not st_.get("cpi"):
        y_, m_ = int(hot[0][:4]), int(hot[0][5:])
        st_["cpi"] = f"{y_ + (m_ == 12)}-{m_ % 12 + 1:02d}-15"
    ipo = {k: v for k, v in IPO_SIGNALS.items() if v}
    if ipo and not st_.get("ipo"): st_["ipo"] = min(ipo.values())
    q = bars("QQQ", "2y"); qc = [x[1] for x in q]; hi = max(qc)
    off = (qc[-1] / hi - 1) * 100
    v = bars("^VIX", "6mo"); vc = [x[1] for x in v]
    vix_buy = max(vc[-11:]) >= 25 and vc[-1] < vc[-2] < vc[-3]
    phase = 1 + bool(st_.get("cpi")) + bool(st_.get("ipo"))
    if st_.get("phase") == 4 or (phase == 3 and off <= -20 and vix_buy): phase = 4
    changed_from = st_.get("phase") if st_.get("phase") and phase != st_.get("phase") else None
    if phase != st_.get("phase"):
        st_["phase"] = phase
        st_["since"] = max([x for x in (st_.get("cpi"), st_.get("ipo")) if x], default=d["date"]) if phase in (2, 3) else d["date"]
    json.dump(st_, open(BUBBLE_STATE, "w"))
    runhi, dd = 0, []
    for x in q[::5] + [q[-1]]:
        runhi = max(runhi, x[1]); dd.append((x[0], round((x[1] / runhi - 1) * 100, 1)))
    return dict(phase=phase, since=st_["since"], phases=PHASES, changed_from=changed_from, cpi=now_cpi, cpi_month=cpi[-1][0],
                cpi_peak=max(cpi[-12:], key=lambda x: x[1]),
                cpi_fired=st_.get("cpi"), ipo=IPO_SIGNALS, ipo_fired=st_.get("ipo"),
                qqq=qc[-1], qqq_hi=hi, qqq_off=round(off, 1), vix=vc[-1], vix_buy=vix_buy,
                cpi_series=cpi, qqq_dd=dd)


def playbook():
    """Live status of every bear-watch light (thesis/plan-backtest.md, portfolio-gaps.md)."""
    out = {}
    try:   # Rule A: S&P 500 vs its 200-day line, with the 20-close confirmation
        q = get(f"https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=2y&interval=1d")["chart"]["result"][0]
        c = [x for x in q["indicators"]["quote"][0]["close"] if x]
        sma = [sum(c[i - 199:i + 1]) / 200 for i in range(199, len(c))]; cc = c[199:]
        state, run = True, 0
        for x, m in zip(cc, sma):
            side = x > m; run = run + 1 if side != state else 0
            if run >= 20: state, run = side, 0
        streak = 0
        for x, m in zip(reversed(cc), reversed(sma)):
            if (x > m) != (cc[-1] > sma[-1]): break
            streak += 1
        out["rule_a"] = dict(spx=cc[-1], sma200=sma[-1], gap=(cc[-1] / sma[-1] - 1) * 100,
                             above=cc[-1] > sma[-1], streak=streak, invested=state, pending=run)
    except Exception as e:
        out["rule_a_err"] = str(e)[:60]
    try:   # yield curve, 10-year minus 3-month (FRED, daily)
        x = urllib.request.urlopen(urllib.request.Request("https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y3M",
                                   headers=YF), timeout=45).read().decode().strip().split("\n")
        last = [r.split(",") for r in x[-400:] if r.split(",")[1] not in ("", ".")]
        v = float(last[-1][1]); inv_days = sum(1 for r in last[-250:] if float(r[1]) < 0)
        out["curve"] = dict(value=v, date=last[-1][0], inverted=v < 0, inverted_days_1y=inv_days)
    except Exception as e:
        out["curve_err"] = str(e)[:60]
    try:   # OOZEMeter household score (public repo)
        j = get("https://raw.githubusercontent.com/ArringtonC/oozemeter/main/data/latest.json")
        out["household"] = dict(score=j["ooze"], prev=j.get("prevOoze"), month=j.get("monthLabel"))
    except Exception as e:
        out["household_err"] = str(e)[:60]
    # remember the last reading of each light so a change can be announced once
    path = os.path.join(DOCS, ".playbook-state.json")
    prev = json.load(open(path)) if os.path.exists(path) else {}
    now = dict(rule_a=out.get("rule_a", {}).get("invested"), curve=out.get("curve", {}).get("inverted"),
               household=(out.get("household", {}).get("score") or 0) >= 50)
    out["flips"] = {k: v for k, v in now.items() if v is not None and k in prev and prev[k] is not None and prev[k] != v}
    json.dump({k: (v if v is not None else prev.get(k)) for k, v in now.items()}, open(path, "w"))
    return out


def market(d, open_):
    """What can be known about today's index move, before and during. None of it
    says which way -- thesis/scalp.md: nothing predicted QQQ/SPY direction."""
    m = dict(events=[EVENTS[d["date"]]] if d["date"] in EVENTS else [])
    for s in ("QQQ", "SPY", "USO", "NQ=F", "CL=F", "^TNX", "^VIX"):
        try:
            q = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=5m")["chart"]["result"][0]["meta"]
            m[s] = (q["regularMarketPrice"] / q["chartPreviousClose"] - 1) * 100
            m[s + "_px"] = q["regularMarketPrice"]
            m[s + "_chg"] = q["regularMarketPrice"] - q["chartPreviousClose"]
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
    m["fut"] = futures()
    # premarket last trade vs yesterday's close, for the price lines
    for s in ("QQQ", "SPY", "USO", "^VIX"):
        try:
            r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=1d&interval=1m&includePrePost=true")["chart"]["result"][0]
            last = [c for c in r["indicators"]["quote"][0]["close"] if c]
            # regularMarketPrice is yesterday's close before the open; chartPreviousClose is the day before that
            base = r["meta"]["regularMarketPrice"] if s != "^VIX" else m.get("^VIX_px", 0) - m.get("^VIX_chg", 0)
            if last and base: m[s + "_pre"] = (base, last[-1])
        except Exception:
            pass
    # two market headlines for the write-up; keyword filter, no AI
    try:
        import re, html
        heads = []
        for u in ("https://www.cnbc.com/id/100003114/device/rss/rss.html",
                  "https://www.cnbc.com/id/20910258/device/rss/rss.html"):
            x = urllib.request.urlopen(urllib.request.Request(u, headers=YF), timeout=20).read().decode()
            heads += [html.unescape(t) for t in re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", x, re.S)]
        # war and diplomacy move oil and rates before the numbers do; they rank first
        geo = re.compile(r"\b(iran|israel|russia|ukraine|china|xi|opec|war|strike|sanction|peace|ceasefire)", re.I)
        mkt = re.compile(r"\b(yields?|rates?|fed|warsh|oil|crude|stocks?|futures|nasdaq|s&p|dow|inflation|cpi|jobs|treasur\w*|tariffs?|chips?)\b", re.I)
        u = list(dict.fromkeys(heads))
        ipo = re.compile(rf"\b({MEGA_IPOS})\b.*\b(IPO|prices|priced|pricing|debut|listing|goes public)\b", re.I)
        m["heads"] = ([h for h in u if ipo.search(h)][:1] + [h for h in u if geo.search(h) and not ipo.search(h)][:2]
                      + [h for h in u if mkt.search(h) and not geo.search(h) and not ipo.search(h)])[:3]
    except Exception:
        m["heads"] = []
    # is the 10-year at a multi-year high? (monthly history, highest-since year)
    try:
        t = bars("^TNX", "max"); now = m.get("^TNX_px")
        older = [x for x in t if x[0] < d["date"][:4] and max(x[1], x[2]) >= now]
        m["tnx_since"] = older[-1][0][:4] if older else None
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


FUTS = [("ES=F", "S&P"), ("NQ=F", "Nasdaq"), ("YM=F", "Dow"), ("RTY=F", "Russell"), ("CL=F", "Oil"), ("GC=F", "Gold")]


def session_start(now):
    """Most recent CME open: 5 PM Central, Sunday through Thursday."""
    t = now.replace(hour=17, minute=0, second=0, microsecond=0)
    if t > now: t -= datetime.timedelta(days=1)
    while t.weekday() in (4, 5): t -= datetime.timedelta(days=1)   # no session opens Fri or Sat
    return t


def futures(now=None):
    """[(name, price, % vs the prior session's close, trading now?)] for the main futures."""
    ct = ZoneInfo("America/Chicago"); now = now or datetime.datetime.now(ct); s0 = session_start(now)
    out = []
    for sym, name in FUTS:
        try:
            q = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=15m")["chart"]["result"][0]
            pts = [(datetime.datetime.fromtimestamp(t, ct), c) for t, c in zip(q["timestamp"], q["indicators"]["quote"][0]["close"]) if c]
            last = pts[-1]; live = last[0] >= s0
            base = s0 if live else session_start(s0 - datetime.timedelta(minutes=1))
            prev = [c for t, c in pts if t < base]
            if prev: out.append((name, last[1], (last[1] / prev[-1] - 1) * 100, live))
        except Exception:
            pass
    return out


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
                   f"{'^' if (r.get('to_target') if r.get('to_target') is not None else 99) <= 5 else ''}"
                   for r in open_ if r.get("acct") in ("real", "paper-auto", "bot", "bot-leaps"))
    return json.dumps(dict(date=d["date"], fired=fired, soon=soon, steps=steps,
                           watch=sorted(w["sym"] + w["date"] for w in d.get("watch", [])),
                           levels=sorted(x["symbol"] + x["way"] for x in d.get("levels", [])),
                           errs=len(d["errors"]), regime=d["regime"]), sort_keys=True)


def should_push(d, open_, name="key", key=None):
    """(send?, why). Closing run and first-of-day always go; the rest must earn it.
    name="key" is the trade report; name="mkt" the market report, keyed on the 1%/2% bucket."""
    now = datetime.datetime.now(datetime.timezone.utc)
    closing = now.hour >= 20                       # after 3pm ET, the session is done
    prev = {}
    if os.path.exists(ALERT_STATE):
        try: prev = json.load(open(ALERT_STATE))
        except Exception: prev = {}
    key = key or alert_key(d, open_)
    first = prev.get("date") != d["date"] or name not in prev
    changed = prev.get(name) != key
    json.dump({**(prev if prev.get("date") == d["date"] else {}),
               "date": d["date"], name: key, "at": now.isoformat()}, open(ALERT_STATE, "w"))
    if closing: return True, "close"
    if first:   return True, "first run today"
    if changed: return True, "something changed"
    return False, "nothing changed"


SENT = os.path.join(DOCS, ".sent-trades.json")


def fresh(d, mark=False):
    """Weekdays only send what is NEW: a Setup F fire not sent in the last 30 days, a Setup G
    watch not sent before, and level breaks (those fire once anyway). Arrington 2026-10-03:
    "one or two new trades vs sending all the trades"."""
    st_ = json.load(open(SENT)) if os.path.exists(SENT) else {"F": {}, "G": {}}
    today = datetime.date.fromisoformat(d["date"])
    newF = [r for r in d.get("F", []) if r["sym"] not in st_["F"]
            or (today - datetime.date.fromisoformat(st_["F"][r["sym"]])).days > 30]
    newG = [w for w in d.get("watch", []) if w["sym"] + w["date"] not in st_["G"]]
    newV = bool(d.get("vix_fires")) and ("VIX" not in st_["F"] or (today - datetime.date.fromisoformat(st_["F"]["VIX"])).days > 30)
    if mark:
        for r in newF: st_["F"][r["sym"]] = d["date"]
        for w in newG: st_["G"][w["sym"] + w["date"]] = d["date"]
        if newV: st_["F"]["VIX"] = d["date"]
        json.dump(st_, open(SENT, "w"), indent=1)
    return {**d, "F": newF, "watch": newG, "taken": [], "vix_fires": newV}


def position_events(open_, closed, d):
    """Meaningful position events (real and bot books), each one sent once: a position closed today,
    entering the last 5% before its target, or entering its last 7 days before expiry."""
    ev = []
    for r in closed:
        if r.get("acct") in ("real", "paper-auto", "bot", "bot-leaps") and r.get("closed") == d["date"]:
            ev.append(("closed", r["id"]))
    for r in open_:
        if r.get("acct") not in ("real", "paper-auto", "bot", "bot-leaps"): continue
        if r.get("to_target") is not None and r["to_target"] <= 5: ev.append(("near", r["id"]))
        if r.get("expiry") and (datetime.date.fromisoformat(r["expiry"]) - datetime.date.fromisoformat(d["date"])).days <= 7:
            ev.append(("expiry", r["id"]))
    return ev


def dispatch(d, open_, closed, dow, send_tg=None, send_dc=None, send_file=None):
    """Who gets what, when (Arrington 2026-10-03):
      every run   market report when it has news (first run of the day = the premarket briefing)
      weekdays    a trade message ONLY for new things: a new Setup F opportunity, a new Setup G watch,
                  a level break, a new Setup VIX, or a meaningful position event -- each sent once
      Saturday    the weekend report: every position, the week's closes, all current F fires
      Sunday      the futures briefing only (market report)
    Paper fills (the bots' confirmed orders) are sent by autotrade.py to #paper-portfolio, apart from these."""
    send_tg = send_tg or (lambda t: notify(t, discord_too=False)); send_dc = send_dc or discord
    send_file = send_file or (lambda t, png, r: discord_file(t, png, r) if send_dc is discord else send_dc(t, r))
    from autotrade import SHARE_REAL
    pub = lambda R: [r for r in R if SHARE_REAL or r.get("acct") != "real"]
    out = []
    send, why = should_push(d, open_, "mkt", market_key(d))
    if send:
        t = market_report(d); send_tg(t); send_dc(t, "market"); out.append(("market", why))
    if dow == 5:
        full = dict(title="WEEKEND REPORT · ALL TRADES", closed_days=7)
        send_tg(summary(d, open_, closed, **full))
        for route, part in summary(d if SHARE_REAL else {**d, "taken": []}, pub(open_), pub(closed), parts=True, **full).items():
            if part: send_dc(part, route)
        out.append(("weekend", "all positions"))
    elif dow < 5:
        nd = fresh(d)
        st_ = json.load(open(SENT)) if os.path.exists(SENT) else {"F": {}, "G": {}}
        sent_e = set(st_.get("E", []))
        ev = [e for e in position_events(open_, closed, d) if "|".join(e) not in sent_e]
        ids = {i for _, i in ev}
        ev_open = [r for r in open_ if r["id"] in ids]; ev_closed = [r for r in closed if r["id"] in ids]
        if nd["F"] or nd["watch"] or nd.get("levels") or nd.get("vix_fires") or ev:
            late = datetime.datetime.now(ZoneInfo("America/Chicago")).hour >= 15   # the 16:30 run: act tomorrow
            msg = summary(nd, ev_open, ev_closed, title=("NEW TRADE" if (nd["F"] or nd.get("vix_fires")) else "UPDATE")
                          + (" · AFTER CLOSE · for tomorrow" if late else ""))
            send_tg(msg)
            for route, part in summary({**nd, "F": []}, [], pub(ev_closed), parts=True, after_close=late).items():
                if part: send_dc(part, route)
            for r in pub(ev_open):   # one message per position event: its chart, the update card under it
                try: png = chart_pos(r)
                except Exception as e: png = None; print(f"  chart {r['symbol']} failed: {str(e)[:40]}")
                send_file(summary({**nd, "F": [], "watch": [], "levels": [], "vix_fires": False}, [r], parts=True)["updates"], png, "updates")
            for r in nd["F"]:   # one message per new Setup F trade: its chart, the card under it
                card = summary({**nd, "F": [r], "watch": [], "levels": [], "vix_fires": False}, [], parts=True, after_close=late)["trades"]
                try: png = chart_f(r["sym"], r["px"], r["tgt"], option="CALL" in card)
                except Exception as e: png = None; print(f"  chart {r['sym']} failed: {str(e)[:40]}")
                send_file(card, png, "trades")
            fresh(d, mark=True)
            st_ = json.load(open(SENT)); st_["E"] = sorted(sent_e | {"|".join(e) for e in ev}); json.dump(st_, open(SENT, "w"), indent=1)
            out.append(("trades", f"F {[r['sym'] for r in nd['F']]} · G {[w['sym'] for w in nd['watch']]} · events {ev}"))
    for o in out: print(f"  {o[0]} pushed ({o[1]})")
    if not out: print("  nothing new to send")
    return out


def market_key(d):
    ny = datetime.datetime.now(ZoneInfo("America/New_York"))
    return json.dumps([(ny.hour, ny.minute) < (9, 30), move_bucket(d.get("market", {})), (d.get("bubble") or {}).get("phase"),
                       sorted(((d.get("playbook") or {}).get("flips") or {}).items()),
                       mega_ipo_headlines(d.get("market", {}))[:1],
                       sorted(c["effective"] + c["added"] + c["removed"] for c in d.get("sp500", [])[:6])])


# ── push ───────────────────────────────────────────────────────────────────────
# Nine days in ten this message saves you opening the page at all. On the tenth
# it reaches you before the open. No token set means it silently does nothing.
def to_discord(text):
    """Telegram HTML -> Discord markdown. The same message, both places."""
    import re, html
    t = re.sub(r'<a href="([^"]+)">(.*?)</a>', r"[\2](<\1>)", text)
    t = t.replace("<b>", "**").replace("</b>", "**").replace("<i>", "*").replace("</i>", "*")
    return html.unescape(t)


# channel -> env var holding that channel's webhook. Any unset one falls back to DISCORD_WEBHOOK.
ROUTES = {"watch": "DISCORD_WEBHOOK_WATCH", "market": "DISCORD_WEBHOOK_MARKET", "trades": "DISCORD_WEBHOOK_TRADES",
          "updates": "DISCORD_WEBHOOK_UPDATES", "ledger": "DISCORD_WEBHOOK_LEDGER",
          "paper": "DISCORD_WEBHOOK_PAPER"}


def discord(text, route=None):
    """Post to a Discord channel webhook. Unset means it does nothing."""
    if route == "watch" and not os.environ.get("DISCORD_WEBHOOK_WATCH"): route = "updates"
    url = os.environ.get(ROUTES.get(route, "")) or os.environ.get("DISCORD_WEBHOOK")
    if not url: return False
    try:
        urllib.request.urlopen(urllib.request.Request(
            url, data=json.dumps(dict(content=to_discord(text)[:2000])).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Ledger (stockcharter-scan)"}), timeout=20).read()
        return True
    except Exception as e:
        print(f"discord failed: {str(e)[:60]}")
        return False


def _chart(sym, title, lines, start=-190, mark=None):
    """Phone-sized PNG: daily candles, 30/200-day EMAs, labeled flat lines [(price, label, color, style)],
    a circle at mark=(index, price). None if matplotlib is missing (the post goes out as text)."""
    try:
        import io, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    except ImportError: return None
    b = bars(sym); c = [x[1] for x in b]; e10, e30 = ema(c, 10)[start:], ema(c, 30)[start:]; b = b[start:]; n = len(b)
    fig, ax = plt.subplots(figsize=(8, 5), dpi=110)
    for k, (_, cl, hi, lo, op, _v) in enumerate(b):
        col = "#26a69a" if cl >= op else "#ef5350"   # TradingView colors
        ax.vlines(k, lo, hi, color=col, lw=0.8); ax.bar(k, abs(cl - op) or cl * 0.001, bottom=min(cl, op), color=col, width=0.7)
    ax.plot(e10, color="#43a047", lw=1.6, label="10 EMA"); ax.plot(e30, color="#1e88e5", lw=1.6, label="30 EMA")
    for k in range(1, n):   # the EMA cross: green + when the 10 crosses over the 30, red X when it crosses under
        if (e10[k] > e30[k]) != (e10[k - 1] > e30[k - 1]):
            up = e10[k] > e30[k]   # big green + up, big red X down
            ax.plot(k, e30[k], "P" if up else "X", ms=18, color="#43a047" if up else "#e53935", mec="white", mew=1, zorder=5)
    box = dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5)
    for y, lbl, col, ls in lines:
        ax.axhline(y, color=col, ls=ls, lw=1.2); ax.text(2, y, lbl, va="bottom", fontsize=10, color=col, bbox=box)
    if mark: ax.plot(mark[0] if mark[0] >= 0 else n + mark[0], mark[1], "o", ms=8, mfc="none", mec="#000", mew=1.5)
    ax.set_xlim(-2, n + 2); step = max(1, n // 6)
    ax.set_xticks(range(0, n, step), [datetime.date.fromisoformat(b[k][0]).strftime("%b %-d" if n < 90 else "%b") for k in range(0, n, step)], fontsize=9)
    ax.set_title(title, fontsize=12, loc="left"); ax.legend(loc="lower left", fontsize=9, frameon=False); ax.grid(alpha=0.2); fig.tight_layout()
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
    return buf.getvalue()


def chart_f(sym, px, tgt, option=False):
    """New Setup F trade: ~9 months, entry and target."""
    return _chart(sym, f"{sym} · Setup F signal {bars(sym)[-1][0][5:]} · daily" + (" · UNDERLYING STOCK, not the call" if option else ""),
                  [(tgt, f"TARGET ${tgt:,.2f} (old high) · +{(tgt / px - 1) * 100:.0f}%", "#2e7d32", "--"),
                   (px, f"ENTRY ${px:,.2f}", "#000", ":")], mark=(-1, px))


def chart_pos(r):
    """Position update: price since the trade opened (+20 sessions before), the target, and the
    active exit level (once the target is touched, the stop sits at the target)."""
    b = bars(r["symbol"]); k = next((i for i, x in enumerate(b) if x[0] >= r["opened"]), len(b) - 1)
    start = max(0, k - 20) - len(b); ent = b[k][1]; tgt = float(r["target"]) if r.get("target") else None
    lines = [(ent, f"OPENED {b[k][0][5:]} · stock ${ent:,.2f}", "#000", ":")]
    if tgt: lines.append((tgt, (f"STOP AT TARGET ${tgt:,.2f} (target touched)" if r.get("armed") else f"TARGET ${tgt:,.2f}"),
                          "#c62828" if r.get("armed") else "#2e7d32", "--"))
    return _chart(r["symbol"], f"{r['symbol']} · since entry" + (" · UNDERLYING STOCK, not the call" if r.get("kind") == "call" else ""),
                  lines, start=start, mark=(k - (len(b) + start), ent))


def discord_file(text, png, route=None, name="chart.png"):
    """Webhook post with one image attached (multipart). Falls back to text only."""
    if route == "watch" and not os.environ.get("DISCORD_WEBHOOK_WATCH"): route = "updates"
    url = os.environ.get(ROUTES.get(route, "")) or os.environ.get("DISCORD_WEBHOOK")
    if not url: return False
    if not png: return discord(text, route)
    k = "ledgerboundary7MA4YWxk"
    body = (f"--{k}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n"
            + json.dumps(dict(content=to_discord(text)[:2000])) + f"\r\n--{k}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{k}--\r\n".encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={k}",
                               "User-Agent": "Ledger (stockcharter-scan)"}), timeout=30).read()
        return True
    except Exception as e:
        print(f"discord chart failed: {str(e)[:60]}"); return discord(text, route)


def notify(text, route=None, discord_too=True):
    if discord_too: discord(text, route)
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


def writeup(d, m, pre=True):
    """Plain sentences from the numbers, the way a person would brief it. Rules, not AI.
    Before the open it leads with futures; during the day with QQQ's move so far."""
    W, P = [], []
    # the 15-minute futures series knows about the Sunday open; the daily quote does not
    nq = next((c for n, p, c, live in m.get("fut") or [] if n == "Nasdaq"), m.get("NQ=F")) if pre else None
    q = m.get("QQQ")
    if not pre and q is not None:
        size = "a big day" if abs(q) >= 1 else "a normal day" if abs(q) >= 0.3 else "a quiet day"
        P.append(f"<b>QQQ is {'up' if q > 0 else 'down'} {abs(q):.1f}% today</b>, {size}.")
    nq_live = next((live for n, p, c, live in m.get("fut") or [] if n == "Nasdaq"), True)
    ct = datetime.datetime.now(ZoneInfo("America/Chicago"))
    if ct.weekday() == 5 or (ct.weekday() == 6 and ct.hour < 17) or (ct.weekday() == 4 and ct.hour >= 16):
        nq_live = False     # futures are shut from Friday 4 PM to Sunday 5 PM CT
    if nq is not None and not nq_live:   # weekend: say what the last session did, not "an open"
        P.append(f"<b>Futures are closed until Sunday 5 PM CT.</b> The last session, Nasdaq futures "
                 f"{'rose' if nq > 0 else 'fell'} {abs(nq):.1f}%.")
    elif nq is not None:
        size = "a big open" if abs(nq) >= 1 else "a normal open" if abs(nq) >= 0.3 else "a flat open"
        P.append(f"<b>Nasdaq futures are {'up' if nq > 0 else 'down'} {abs(nq):.1f}%</b>, {size}.")
    r, rc = m.get("^TNX_px"), m.get("^TNX_chg")
    if r is not None:
        since = f", the highest since {m['tnx_since']}" if m.get("tnx_since") and int(m["tnx_since"]) < int(d["date"][:4]) - 1 else ""
        if rc >= 0.05: P.append(f"Rates jumped: the 10-year is {r:.2f}%{since}. Rising rates hit tech and QQQ hardest.")
        elif rc <= -0.05: P.append(f"Rates fell to {r:.2f}%. Falling rates help tech.")
        else: P.append(f"Rates are steady at {r:.2f}%{since}.")
    o = m.get("CL=F")
    if o is not None and abs(o) >= 1:
        P.append(f"Oil is {'up' if o > 0 else 'down'} {abs(o):.1f}% to ${m['CL=F_px']:.0f}"
                 + (", which feeds inflation fear." if o > 0 else ", which eases inflation fear."))
    v, vc = m.get("^VIX_px"), m.get("^VIX_chg")
    if v is not None:
        zone = "calm" if v < 16 else "normal" if v < 20 else "elevated, big swings both ways" if v < 25 else "high"
        P.append(f"VIX is {v:.1f} ({'+' if vc >= 0 else ''}{vc:.1f}), {zone}.")
    W.append(" ".join(P))
    for e in m.get("events", []): W += ["", f"<b>Today:</b> {e}."]
    if m.get("heads"): W += [""] + [f"· <i>{h}</i>" for h in m["heads"]]
    if not pre and move_bucket(m) and m.get("movers"):
        W += ["", "Biggest basket moves: " + " · ".join(f"{s} {v:+.1f}%" for s, v in m["movers"])]
    fut = m.get("fut") or []
    if pre and fut:
        live = any(f[3] for f in fut)
        W += ["", "<b>FUTURES</b>" + ("" if live else " · closed, last session")]
        W += [f"{n} {p:,.2f} {c:+.1f}%" for n, p, c, _ in fut]
    # yesterday's close -> now, the same arrow as the trade cards
    W.append("")
    for s in ("QQQ", "SPY", "USO", "^VIX"):
        name = s.lstrip("^")
        if not pre and m.get(s + "_px") and m.get(s) is not None:   # in session: prior close from the day change
            m = {**m, s + "_pre": (m[s + "_px"] / (1 + m[s] / 100), m[s + "_px"])}
        if not m.get(s + "_pre"):   # no premarket print yet: yesterday's close alone
            if m.get(s + "_px"): W.append(f"{name} ${m[s + '_px']:,.2f}")
            continue
        a, b = m[s + "_pre"]
        chg = f"{b - a:+.1f}" if s == "^VIX" else f"{(b / a - 1) * 100:+.1f}%"
        u = "" if s == "^VIX" else "$"   # VIX is points, not dollars
        W.append(f"{name} {u}{a:,.2f} → <b>{u}{b:,.2f}</b> {chg}"
                 + (f" · ±${m[s + '_exp']:.0f} {m['exp_by']}" if m.get(s + "_exp") else ""))
    # S&P 500 changes effective in the last 7 days or still ahead -- index funds must trade them
    today = datetime.date.fromisoformat(d["date"]); mine = set(UNIVERSE) | set(CORE)
    for c in d.get("sp500", []):
        days = (datetime.date.fromisoformat(c["effective"]) - today).days
        if days < -7: continue
        when = f"on {datetime.date.fromisoformat(c['effective']).strftime('%b %-d')}"
        who = lambda t: f"<b>{t}</b>" + (" (you scan it)" if t in mine else "")
        if c["added"]: W.append(f"<b>S&P 500:</b> {who(c['added'])} joins {'' if days < 0 else 'effective '}{when}"
                                + (f", replacing {who(c['removed'])}" if c["removed"] else "") + ".")
        elif c["removed"]: W.append(f"<b>S&P 500:</b> {who(c['removed'])} leaves {when}.")
    bb = d.get("bubble")
    if bb and bb["phase"] > 1:
        ph = bb["phases"][bb["phase"] - 1]
        W += ["", f"<b>AI BUBBLE PLAN · PHASE {bb['phase']}: {ph['name'].upper()}</b> (since {bb['since'][5:]})"] \
             + [f"<i>A 4-step plan for a possible AI bubble. Phase {bb['phase']} = {ph['when']}.</i>"] + [f"· {x}" for x in ph["do"]]
    f = [x["sym"] for x in d.get("F", [])]
    W += ["", f"<b>Setup F:</b> {', '.join(f)}. Details in the trade report." if f else "<b>Setup F:</b> nothing fires."]
    if pre and nq is not None and abs(nq) >= 1:
        W.append("<b>Big open expected. Do not trade the first 30 minutes.</b>")
    return W


ALERT_TEXT = {
    ("rule_a", False): ("🚨 BEAR RULE A FIRED", ["S&P closed 20 days in a row under its 200-day line",
                         "Move the SPY core to cash · deposits wait in cash", "Back in after 20 closes above the line"]),
    ("rule_a", True): ("✅ BEAR RULE A CLEARED", ["S&P closed 20 days in a row back above its 200-day line", "Buy back the SPY core"]),
    ("household", True): ("🟡 HOUSEHOLD SCORE 50+", ["No new Setup F trades", "Deposits wait in cash"]),
    ("household", False): ("✅ HOUSEHOLD SCORE BACK UNDER 50", ["Setup F trades back on"]),
    ("curve", True): ("🟡 YIELD CURVE INVERTED", ["Recession odds up for the next 1-3 years", "Watch the banks first: XLF, KRE", "Nothing to sell on this alone"]),
    ("curve", False): ("✅ YIELD CURVE BACK TO NORMAL", ["Recessions often start after this -- keep watching"]),
}


def alerts(d, m):
    """Loud lines at the very top of the market report, on the run where something changed."""
    A = []
    bb = d.get("bubble") or {}
    if bb.get("changed_from"):
        ph = bb["phases"][bb["phase"] - 1]
        A += [f"<b>🚨 AI BUBBLE PLAN: PHASE {bb['changed_from']} → PHASE {bb['phase']} ({ph['name'].upper()})</b>", f"<i>A 4-step plan for a possible AI bubble. Phase {bb['phase']} = {ph['when']}.</i>"] + [f"· {x}" for x in ph["do"]] + [""]
    for k, v in ((d.get("playbook") or {}).get("flips") or {}).items():
        t, lines = ALERT_TEXT[(k, v)]
        A += [f"<b>{t}</b>"] + [f"· {x}" for x in lines] + [""]
    ipo = mega_ipo_headlines(m)
    if ipo and not any((d.get("bubble") or {}).get("ipo", {}).values()):
        A += ["<b>⚠ POSSIBLE BUBBLE SIGNAL 2 (mega-IPO)</b>", f"· <i>{ipo[0]}</i>", "· Tell Claude to confirm it -- that would move the plan to Phase 3", ""]
    elif ipo:   # signal 2 already fired: another mega-IPO is a late-bubble heads-up, not a phase change
        A += ["<b>⚠ ANOTHER MEGA-IPO (late-bubble sign)</b>", f"· <i>{ipo[0]}</i>",
              "· Signal 2 already fired (SpaceX) -- no phase change", "· Bubbles tend to top as the biggest companies sell stock", ""]
    return A


MEGA_IPOS = r"SpaceX|OpenAI|Anthropic|xAI"   # 2026-10-02: Anthropic reported to target a ~$2T listing by Nov 2026


def mega_ipo_headlines(m):
    import re
    return [h for h in (m.get("heads") or []) if re.search(rf"\b({MEGA_IPOS})\b.*\b(IPO|prices|priced|pricing|debut|listing|goes public)\b", h, re.I)]


def market_report(d):
    """Message 1 of 2: where the market is. Separate from the trades on purpose."""
    M = []
    m = d.get("market", {})
    ny = datetime.datetime.now(ZoneInfo("America/New_York"))
    weekend = ny.weekday() >= 5
    pre = weekend or (ny.hour, ny.minute) < (9, 30)   # on weekends the futures are the only live market
    head = (("SUNDAY FUTURES" if ny.weekday() == 6 and ny.hour >= 18 else "WEEKEND REPORT") if weekend else
            "PREMARKET REPORT" if pre else
            f"{'▲' if (m.get('QQQ') or 0) > 0 else '▼'} BIG MOVE TODAY" if move_bucket(m) else "MARKET REPORT")
    M += [f"<b>{head}</b>", ""] + writeup(d, m, pre) + ["", "<i>not a signal</i>"]
    M = alerts(d, m) + M
    return "\n".join(M)


def panic_day(sym, look=21):
    """Latest day in the last `look` sessions the stock fell 5%+ on 3x its 50-day volume, or None.
    thesis/volume/volume.md: those keep lagging for about a month (-1.46pp without top 5)."""
    try:
        b = bars(sym)
        for i in range(len(b) - 1, max(len(b) - 1 - look, 51), -1):
            v = [x[5] for x in b[i - 50:i] if x[5]]
            if not b[i][5] or len(v) < 40: continue
            chg, rv = b[i][1] / b[i - 1][1] - 1, b[i][5] / (sum(v) / len(v))
            if chg <= -0.05 and rv >= 3: return (b[i][0], chg * 100, rv)
    except Exception:
        pass
    return None


# ── pattern #1 forward test (2026-09-29): falling channel -> base -> breakout ──────────
# thesis/patterns: no edge 2016-2026 (60% fail within 10 days, 67% reach the old high within
# a year). Arrington spots it on his own charts (SG, META), so CORE names get a WATCH card --
# never a BUY card -- and every one is logged to docs/pattern-watch.json for a live record.
WATCH_LOG = os.path.join(DOCS, "pattern-watch.json")


def channel_breakout(sym, recent=3):
    """Pattern #1 breakout in the last `recent` sessions, or None. Same rule as thesis/patterns/patterns.py."""
    b_ = bars(sym)
    d = [x[0] for x in b_]; c = [x[1] for x in b_]; h = [x[2] for x in b_]; l = [x[3] for x in b_]
    if len(b_) < 300: return None
    e10, e30 = ema(c, 10), ema(c, 30)
    for b in range(len(b_) - 1, len(b_) - 1 - recent, -1):
        p = max(range(b - 252, b - 60), key=lambda k: h[k])
        if h[p] < max(h[b - 252:b]) or b - 15 <= p + 20: continue
        t = min(range(p + 20, b - 15), key=lambda k: l[k])
        if l[t] > h[p] * 0.75 or min(l[t + 1:b]) < l[t]: continue
        piv = [k for k in range(p + 10, b - 10) if h[k] == max(h[k - 5:k + 6]) and h[k] < h[p]]
        if not piv: continue
        q = max(piv, key=lambda k: h[k]); slope = (h[q] - h[p]) / (q - p)
        if slope >= 0: continue
        base_hi = max(h[b - 25:b])
        if c[b] > base_hi and c[b] > h[p] + slope * (b - p) and e10[b] > e30[b] and c[b - 1] <= max(h[b - 26:b - 1]):
            return dict(sym=sym, date=d[b], close=c[b], base_high=base_hi, peak=h[p], peak_date=d[p], low=l[t],
                        e10=e10[-1], e30=e30[-1], e30_up=e30[-1] > e30[-11], above30=c[-1] > e30[-1])
    return None


def watch_patterns(d):
    """Scan CORE for pattern #1; append new ones to the live log."""
    log = json.load(open(WATCH_LOG)) if os.path.exists(WATCH_LOG) else []
    seen = {(x["sym"], x["date"]) for x in log}; hits = []
    for s in CORE:
        try:
            w = channel_breakout(s)
        except Exception:
            w = None
        if w:
            hits.append(w)
            if (w["sym"], w["date"]) not in seen:
                log.append({**w, "pattern": "channel-base-breakout", "logged": d["date"]})
    json.dump(log, open(WATCH_LOG, "w"), indent=1)
    return hits


# ── chart levels Arrington logs from his own charts (thesis/patterns/patterns.db, table levels) ──
LEVELS = os.path.join(DOCS, "levels.json")


def check_levels():
    """Price through a logged chart trigger -> one card, once. Price = the latest quote at run time."""
    if not os.path.exists(LEVELS): return []
    L = json.load(open(LEVELS)); hits = []
    for x in L:
        if x.get("fired"): continue
        try:
            q = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{x['symbol']}?range=1d&interval=5m")["chart"]["result"][0]["meta"]
            p = q["regularMarketPrice"]
        except Exception:
            continue
        way = "up" if p >= x["up"] else "down" if p <= x["down"] else None
        if way:
            x["fired"] = dict(way=way, price=p, at=datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M"))
            hits.append({**x, "price": p, "way": way})
    json.dump(L, open(LEVELS, "w"), indent=1)
    return hits


def left(dte, today=None):
    """114d left is information; the last week is a warning; the last 3 days name the day."""
    if dte is None: return ""
    if dte <= 3:
        day = (today or datetime.date.today()) + datetime.timedelta(days=dte)
        return "<b>EXPIRES TODAY</b>" if dte <= 0 else f"<b>EXPIRES {day.strftime('%a').upper()}</b>"
    return f"<b>⚠ {dte}d left</b>" if dte <= 10 else f"{dte}d left"


def summary(d, open_, closed=(), parts=False, title="TRADE REPORT", closed_days=0, after_close=False):
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
            n = max(c["n"], 1)
            B.append(f"{when(c['expiry'])} · {n} × ${c['strike']:g} CALL{'S' if n != 1 else ''}"
                     f" · ${c['cost'] / n / 100:,.2f}")
            # over the cap the setup still wants the call; the cap only reaches the stock
            B.append(f"OVER CAP → {shares}" if c["over"] else f"{D(c['cost'])} TOTAL")
        else:
            B.append(shares)
        B.append(f"TARGET ${r['tgt']:,.0f} · +{r['up']:.0f}%")
        B.append("EXIT: at the target the stop moves up to it · else sell after 1 year · calls: sell 30 days before expiry")
        p = panic_day(sym)
        if p: B.append(f"⚠ PANIC SELL {p[0][5:]}: {p[1]:.0f}% ON {p[2]:.1f}× VOLUME · <i>these usually keep lagging a month</i>")
        B.append("")

    W_ = []
    for x in d.get("levels", []):
        up = x["way"] == "up"
        W_ += [f"<b>📍 LEVEL {x['symbol']} ${x['price']:,.2f} · {'BROKE OUT' if up else 'BROKE DOWN'}</b>",
               f"{x['tf']} CHART NOTE: {x['pattern'].upper()}",
               f"{'OVER' if up else 'UNDER'} ${x['up' if up else 'down']:,.2f} · TARGET ${x['target_up' if up else 'target_down']:,.2f}",
               f"<i>{'fails on a close back under $' + format(x['down'], ',.2f') if up else 'a chart level, not a tested setup'}</i>", ""]
    for w in d.get("watch", []):
        W_ += [f"<b>👀 SETUP G · WATCH {w['sym']} ${w['close']:,.2f}</b>" + (" · CORE" if w["sym"] in CORE else ""),
               f"CHANNEL BREAKOUT {w['date'][5:]} · OVER ${w['base_high']:,.2f}",
               f"OLD HIGH ${w['peak']:,.2f} · +{(w['peak'] / w['close'] - 1) * 100:.0f}%",
               (f"10 EMA ${w['e10']:,.2f} {'>' if w['e10'] > w['e30'] else '<'} 30 EMA ${w['e30']:,.2f}"
                f" · 30 {'rising' if w.get('e30_up') else 'flat/falling'}"
                f" · price {'above' if w.get('above30') else 'BELOW'} the 30") if w.get("e10") else "",
               "<i>WATCH, NOT AN ENTRY: a chart pattern with no tested edge yet · 40% hold, 67% reach the old high in a year</i>", ""]
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
        if r["acct"] not in ("real", "paper-auto", "bot", "bot-leaps"): continue
        n = int(r.get("contracts") or 1)
        mult = 100 if r.get("kind") == "call" else 1
        a, b = float(r["entry"]) * mult * n, (r.get("now") or 0) * mult * n
        head = f"{r['symbol']} · {when(r['expiry'])} · ${float(r['strike']):g} CALL" if r.get("kind") == "call" \
               else f"{r['symbol']} · {n} SHARES"
        bot = " · 🤖 PAPER" if r["acct"] in ("paper-auto", "bot", "bot-leaps") else " · 👤 ARRINGTON'S REAL ACCOUNT"
        U += [f"<b>{'↑' if b >= a else '↓'} TRADE UPDATE</b>{bot}", head,
              f"{D(a)} → <b>{D(b)}</b>",
              f"{'+' if b >= a else '−'}{D(abs(b - a))} · {r['pl_pct']:+.0f}%"
              + (f" · {left(r['dte'])}" if r.get("dte") is not None else "")]
        if r.get("to_target") is not None and r["to_target"] <= 5:
            U.append(f"<b>NEAR TARGET ${float(r['target']):,.2f}</b> · {max(r['to_target'], 0):.0f}% away")
        U.append("")

    C = []
    # the trade log first (it knows the expiry), TAKEN only for anything the log lacks
    done = []
    since = (datetime.date.fromisoformat(d["date"]) - datetime.timedelta(days=closed_days)).isoformat()
    for r in closed:
        if r.get("acct") not in ("real", "paper-auto", "bot", "bot-leaps") or not (since <= (r.get("closed") or "") <= d["date"]): continue
        n = int(r.get("contracts") or 1); mult = 100 if r.get("kind") == "call" else 1
        a = float(r["entry"]) * mult * n
        done.append(dict(bot=r["acct"] in ("paper-auto", "bot", "bot-leaps"), sym=r["symbol"], contract=f"{when(r['expiry'])} · ${float(r['strike']):g} CALL" if r.get("kind") == "call" else f"{n} SHARES",
                         cost=a, pl=float(r["exit"]) * mult * n - a, pct=float(r["pl_pct"] or 0), closed=r["closed"]))
    seen = {t["sym"] for t in done}
    done += [t for t in d.get("taken", []) if since <= (t.get("closed") or "") <= d["date"] and t["sym"] not in seen]
    for t in done:
        C += [(f"<b>✓ CLOSED THIS WEEK · {when(t['closed'])}</b>" if closed_days else "<b>✓ TRADE CLOSED</b>") + (" · 🤖 PAPER" if t.get("bot") else " · 👤 ARRINGTON'S REAL ACCOUNT"), f"{t['sym']} · {t['contract'].upper()}".replace("  ", " "),
              f"{D(t['cost'])} → <b>{D(t['cost'] + t['pl'])}</b>",
              f"FINAL {'+' if t['pl'] >= 0 else '−'}{D(abs(t['pl']))} · {t['pct']:+.0f}%", ""]

    if parts:   # Discord splits the report by channel: new buys, movement, finished trades
        j = lambda X: "\n".join(X).strip()
        head = "<b>NEW TRADES · AFTER CLOSE · for tomorrow</b>" if after_close else "<b>NEW TRADES</b>"
        return dict(trades=j([head, ""] + B) if B else "", watch=j(["<b>WATCHLIST</b>", ""] + W_) if W_ else "",
                    updates=j(U), ledger=j(C))
    L = [f"<b>{title}</b>", ""] + C + B + W_ + U
    if not (C + B + U): L += ["NOTHING TO BUY TODAY", ""]
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
    try: d["playbook"] = playbook()
    except Exception as e: d["errors"].append(f"playbook: {str(e)[:40]}")
    try: d["levels"] = check_levels()
    except Exception as e: d["levels"] = []; d["errors"].append(f"levels: {str(e)[:40]}")
    try: d["watch"] = watch_patterns(d)
    except Exception as e: d["watch"] = []; d["errors"].append(f"watch: {str(e)[:40]}")
    try: d["bubble"] = bubble(d)
    except Exception as e:
        # FRED times out from GitHub's runners now and then: keep the last good reading
        # rather than blanking the section (seen 2026-09-29 01:05 UTC)
        try: d["bubble"] = json.load(open(os.path.join(DOCS, "data.json")))["bubble"]; print(f"  bubble: kept last reading ({str(e)[:40]})")
        except Exception: d["errors"].append(f"bubble: {str(e)[:40]}")
    try: d["sp500"] = sp500_changes()
    except Exception as e: d["sp500"] = []; d["errors"].append(f"sp500: {str(e)[:40]}")
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
    dispatch(d, open_, closed, datetime.datetime.now(ZoneInfo("America/Chicago")).weekday())
