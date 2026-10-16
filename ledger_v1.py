#!/usr/bin/env python3
"""The Ledger Trade Improver, Version 1 (locked plan, 2026-10-12). RULE VERSION 1.0 -- the rules below
do not change during the build or the 30-session paper test; new ideas go to docs/v1-backlog.md.

  market    ES/NQ futures vs the overnight midpoint, SPY/QQQ vs VWAP and the opening midpoint (after 8:45 CT),
            breadth + VIX -> BULLISH / BEARISH / MIXED / CHOPPY. Provisional before 8:45, confirmed after.
  workspace at most 7 single-stock charts, each with a saved daily + 15-minute plan (docs/v1.json, v1.html)
  decide    the hard rules (Part A): source, prep, fresh data, single-stock, 3-9 DTE, earnings after expiry,
            spread <= 10%, one contract <= $400, planned loss <= $200 and 2% of equity, 5-minute trigger,
            chase limits -> NO TRADE / PREP NEEDED / DATA STALE / WAIT / NOW / MISSED (+ HOLD / OUT once open)
  monitor   every minute (from trade_manager): states change -> one Discord message; same state -> nothing
  paper     every NOW is paper-filled at the ask and followed: +50% / -50% bracket or the chart stop
  audit     every decision is appended to ~/.ledger-v1/decisions.jsonl (raw inputs + outputs + rule version)
The rules decide; the message only formats the rule result. PAPER ONLY -- nothing here places an order.
Self-test: V1TEST=1 python3 ledger_v1.py   ·   report: python3 ledger_v1.py report   ·   replay: ... replay"""
import datetime as dt, json, os, sys, uuid, zoneinfo
RULE_VERSION = "1.0"
CT = zoneinfo.ZoneInfo("America/Chicago")
H = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.expanduser("~/.ledger-v1"); DB = os.path.join(STORE, "decisions.jsonl"); ST = os.path.join(STORE, "state.json")
WS = os.path.join(H, "docs", "v1.json")
MAX_CHARTS, MAX_COST, MAX_LOSS, MAX_LOSS_PCT, MAX_SPREAD = 7, 400.0, 200.0, 0.02, 0.10
DTE_MIN, DTE_MAX, CHASE_ATR, CHASE_ASK, VOL_X = 3, 9, 0.25, 0.15, 1.2
FEE = 0.66
INDEXES = {"SPY", "QQQ", "IWM", "DIA", "SPX", "NDX", "XSP", "VIX", "RSP"}


# ---------- data ----------
def yf(sym, rng="5d", interval="5m", prepost=False):
    """[(datetime CT, o, h, l, c, v)] from Yahoo."""
    from scan_daily import get
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={interval}"
            + ("&includePrePost=true" if prepost else ""))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]; out = []
    for i, t in enumerate(r.get("timestamp") or []):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        out.append((dt.datetime.fromtimestamp(t, CT), q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0))
    return out


def session(bars, day):
    """Regular-session bars (8:30-15:00 CT) of one day."""
    return [b for b in bars if b[0].date() == day and (8, 30) <= (b[0].hour, b[0].minute) < (15, 0)]


def vwap_series(bs):
    pv = v = 0.0; out = []
    for b in bs:
        tp = (b[2] + b[3] + b[4]) / 3; pv += tp * (b[5] or 1); v += b[5] or 1; out.append(pv / v)
    return out


def atr(bs, n=14):
    tr = [max(b[2] - b[3], abs(b[2] - a[4]), abs(b[3] - a[4])) for a, b in zip(bs, bs[1:])]
    return sum(tr[-n:]) / len(tr[-n:]) if tr else None


def option_quote(sym, exp, k, right):
    """(bid, ask, quote time UTC) from Alpaca."""
    import urllib.request
    from scan_daily import occ
    o = occ(sym, exp, k, pad=False, right=right)
    rq = urllib.request.Request(f"https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols={o}",
                                headers={"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]})
    q = json.load(urllib.request.urlopen(rq, timeout=20)).get("quotes", {}).get(o, {})
    t = q.get("t"); t = dt.datetime.fromisoformat(t[:26].rstrip("Z") + "+00:00") if t else None
    return (q.get("bp") or 0.0), (q.get("ap") or 0.0), t


# ---------- market score (Part A) ----------
def futures_point(bars, now):
    """+1 above the overnight midpoint and rising, -1 below and falling, else 0. Overnight = 17:00 CT
    yesterday to 8:30 CT today."""
    start = (now - dt.timedelta(days=1 if now.hour >= 17 else 1)).replace(hour=17, minute=0, second=0, microsecond=0)
    if now.weekday() == 0: start -= dt.timedelta(days=2)     # Monday: the session opened Sunday 17:00
    on = [b for b in bars if start <= b[0] < now.replace(hour=8, minute=30, second=0, microsecond=0)]
    rec = [b for b in bars if b[0] <= now]
    if not on or len(rec) < 7: return 0, None
    mid = (max(b[2] for b in on) + min(b[3] for b in on)) / 2; last = rec[-1][4]; ago = rec[-7][4]   # 30 minutes on 5m bars
    return (1 if last > mid and last > ago else -1 if last < mid and last < ago else 0), round(mid, 2)


def index_point(bs):
    """SPY/QQQ after 8:45: +1 above VWAP and the opening midpoint, -1 below both, else 0. Plus VWAP crosses
    in the first 30 minutes (for CHOPPY)."""
    if len(bs) < 3: return 0, 0
    vw = vwap_series(bs); om = (max(b[2] for b in bs[:3]) + min(b[3] for b in bs[:3])) / 2; last = bs[-1][4]
    side = [1 if b[4] > w else -1 for b, w in zip(bs[:6], vw[:6])]
    crosses = sum(1 for a, b in zip(side, side[1:]) if a != b)
    return (1 if last > vw[-1] and last > om else -1 if last < vw[-1] and last < om else 0), crosses


def score_state(points, spy, qqq, crosses, confirmed):
    """Pure. points = the five input points (index points 0 before 8:45)."""
    s = sum(points)
    if confirmed and ((spy * qqq == -1) or max(crosses) >= 3): return "CHOPPY", s
    return ("BULLISH" if s >= 3 else "BEARISH" if s <= -3 else "MIXED"), s


def market(now=None):
    now = now or dt.datetime.now(CT); day = now.date(); confirmed = (now.hour, now.minute) >= (8, 45)
    es, es_mid = futures_point(yf("ES=F", "5d", "5m", True), now)
    nq, nq_mid = futures_point(yf("NQ=F", "5d", "5m", True), now)
    spy_b, qqq_b = session(yf("SPY"), day), session(yf("QQQ"), day)
    (spy, cs), (qqq, cq) = (index_point(spy_b), index_point(qqq_b)) if confirmed else ((0, 0), (0, 0))
    rsp, vix = yf("RSP", "5d", "1d"), yf("^VIX", "5d", "1d")
    br = rsp[-1][4] - rsp[-2][4] if len(rsp) > 1 else 0          # ponytail: breadth = equal-weight S&P (RSP) up or down today
    vr = vix[-1][4] > vix[-2][4] if len(vix) > 1 else False
    bv = 1 if br > 0 and not vr else -1 if br < 0 and vr else 0
    state, s = score_state([es, nq, spy, qqq, bv], spy, qqq, (cs, cq), confirmed)
    times = [b[-1][0] for b in (spy_b, qqq_b) if b]
    return dict(state=state, score=s, confirmed=confirmed, at=now.isoformat(timespec="minutes"),
                data_time=(min(times) if times else now).isoformat(timespec="minutes"),
                parts=dict(es=es, es_mid=es_mid, nq=nq, nq_mid=nq_mid, spy=spy, qqq=qqq, breadth_vix=bv,
                           spy_vwap_crosses=cs, qqq_vwap_crosses=cq, vix_rising=vr))


# ---------- the chart plan (daily + 15 minute) ----------
def make_plan(sym, daily, m15, earn, day=None):
    """Pure. daily = scan_daily.bars rows (date, close, high, low, ...); m15 = yf rows. Only bars before
    `day` (default today) count -- the plan is made before the open. Trigger = the prior day's high (calls)
    or low (puts); stop area 1 ATR(15m) back through it; targets 2 and 3 ATR on."""
    from scan_daily import ema
    day = day or dt.date.today().isoformat()
    daily = [x for x in daily if x[0] < day]; m15 = [x for x in m15 if x[0].date().isoformat() < day]
    if len(daily) < 40 or len(m15) < 20: return dict(sym=sym, complete=False, missing=["bars"])
    c = [x[1] for x in daily]; e10, e30 = ema(c, 10)[-1], ema(c, 30)[-1]
    bull = e10 > e30; prior = daily[-1]
    a15 = atr(m15); trig = prior[2] if bull else prior[3]; sg = 1 if bull else -1
    p = dict(sym=sym, made=dt.datetime.now(CT).isoformat(timespec="minutes"), rule_version=RULE_VERSION,
             daily=dict(e10=round(e10, 2), e30=round(e30, 2), trend="UP" if bull else "DOWN",
                        support=round(min(x[3] for x in daily[-20:]), 2), resistance=round(max(x[2] for x in daily[-20:]), 2),
                        thesis=f"10 EMA {'above' if bull else 'below'} the 30 EMA", failure=round(e30, 2),
                        earnings=earn),
             m15=dict(direction="CALLS" if bull else "PUTS", trigger=round(trig, 2), atr=round(a15, 2) if a15 else None,
                      stop=round(trig - sg * a15, 2) if a15 else None,
                      targets=[round(trig + sg * 2 * a15, 2), round(trig + sg * 3 * a15, 2)] if a15 else None,
                      chase=round(trig + sg * CHASE_ATR * a15, 2) if a15 else None))
    p["missing"] = [k for g in ("daily", "m15") for k, v in p[g].items() if v is None and k != "earnings"]
    p["complete"] = not p["missing"]
    return p


def source_of(sym, d, levels):
    """A Ledger source recorded before entry: Setup F, Pattern 7, or a logged level. None if none."""
    stamp = d.get("stamp") or d.get("date")
    if any(r["sym"] == sym for r in d.get("F", [])): return dict(type="Setup F", time=stamp, version="scan_daily", evidence="docs/data.json F")
    x = next((x for x in d.get("ihs", []) if x["sym"] == sym), None)
    if x: return dict(type="Pattern 7", time=x.get("at"), version="scan_daily ihs", evidence="docs/data.json ihs")
    x = next((x for x in levels if x["symbol"] == sym and not x.get("fired")), None)
    if x: return dict(type="level", time=x.get("logged"), version="levels.json", evidence=x.get("pattern", "")[:80])
    return None


# ---------- trigger (Part A) ----------
def trigger_state(bs, plan, now):
    """Pure. bs = today's closed 5-minute bars. Returns (code, info): TRIGGER_WAITING, TRIGGER_CONFIRMED or
    CHASE_EXCEEDED. Confirmed = a 5m close beyond the level, on the right side of VWAP, volume >= 1.2x the
    prior 20 bars (ponytail: the break-retest-hold alternative is not coded; backlog)."""
    m = plan["m15"]; sg = 1 if m["direction"] == "CALLS" else -1; vw = vwap_series(bs)
    closed = [i for i, b in enumerate(bs) if b[0] + dt.timedelta(minutes=5) <= now]
    hit = None
    for i in closed:
        b = bs[i]; prev = [x[5] for x in bs[max(0, i - 20):i]]
        avg = sum(prev) / len(prev) if prev else 0
        if sg * (b[4] - m["trigger"]) > 0 and sg * (b[4] - vw[i]) > 0 and (avg == 0 or b[5] >= VOL_X * avg):
            hit = i; break
    if hit is None: return "TRIGGER_WAITING", {}
    last = bs[closed[-1]][4] if closed else bs[-1][4]
    if sg * (last - m["chase"]) > 0: return "CHASE_EXCEEDED", dict(at=bs[hit][0].isoformat(timespec="minutes"), last=last)
    if sg * (last - m["trigger"]) <= 0: return "TRIGGER_WAITING", dict(note="broke, then fell back through the level")
    return "TRIGGER_CONFIRMED", dict(at=bs[hit][0].isoformat(timespec="minutes"), vwap=round(vw[closed[-1]], 2), last=last)


# ---------- contract (Part A) ----------
def contract_codes(o, earn, today, equity):
    """Pure. o = dict(exp, k, right, bid, ask). The hard contract rules that fail."""
    codes = []; dte = (dt.date.fromisoformat(o["exp"]) - today).days
    if not DTE_MIN <= dte <= DTE_MAX: codes.append("DTE_FAIL")
    if earn and earn <= o["exp"]: codes.append("EARNINGS_FAIL")
    mid = (o["bid"] + o["ask"]) / 2
    if not o["ask"] or not o["bid"] or (o["ask"] - o["bid"]) / mid > MAX_SPREAD: codes.append("SPREAD_FAIL")
    if o["ask"] * 100 > MAX_COST: codes.append("COST_FAIL")
    if o["ask"] * 100 * 0.5 > min(MAX_LOSS, MAX_LOSS_PCT * equity): codes.append("RISK_FAIL")
    return codes


def choose(sym, right, spot, today, earn, equity, quote_fn=None, chain=None):
    """The eligible contract: 3-9 DTE, at the money or one strike in the money, every contract rule passing.
    Returns (contract or None, codes seen)."""
    quote_fn = quote_fn or (lambda e, k: option_quote(sym, e, k, right))
    if chain is None:
        from autotrade import call
        chain = [(x["expiration_date"], float(x["strike_price"])) for x in call(
            f"/options/contracts?underlying_symbols={sym}&type={'call' if right == 'C' else 'put'}&limit=1000"
            f"&expiration_date_gte={today + dt.timedelta(DTE_MIN)}&expiration_date_lte={today + dt.timedelta(DTE_MAX)}"
            f"&strike_price_gte={spot * 0.9:.2f}&strike_price_lte={spot * 1.1:.2f}").get("option_contracts", [])]
    seen = set()
    for e in sorted({e for e, _ in chain}):
        ks = sorted({k for x, k in chain if x == e})
        if not ks: continue
        atm = min(ks, key=lambda k: abs(k - spot)); i = ks.index(atm)
        itm = ks[i - 1] if right == "C" and i > 0 else ks[i + 1] if right == "P" and i + 1 < len(ks) else None
        for k in [atm] + ([itm] if itm else []):
            q = quote_fn(e, k); b, a = q[0], q[1]
            o = dict(exp=e, k=k, right=right, bid=b, ask=a, t=q[2].isoformat() if len(q) > 2 and q[2] else None)
            codes = contract_codes(o, earn, today, equity); seen |= set(codes)
            if not codes: return o, sorted(seen)
    return None, sorted(seen or {"DTE_FAIL"})


# ---------- the decision (Part 2 decision path) ----------
def decide(req, mk, plan, src, opt, opt_codes, trig, equity, now, stale=False, alert_ask=None):
    """Pure. The rules decide. req = dict(sym, right or None). Returns (state, codes, risk)."""
    if req["sym"] in INDEXES: return "NO TRADE", ["INDEX_OPTION"], None
    if not src: return "NO TRADE", ["NO_SOURCE"], None
    if not plan or not plan.get("complete"): return "PREP NEEDED", ["PREP_MISSING"], None
    if stale: return "DATA STALE", ["DATA_STALE"], None
    want = "C" if plan["m15"]["direction"] == "CALLS" else "P"; codes = []
    if req.get("right") and req["right"] != want: codes.append("TREND_CONFLICT")     # the idea is turned to the chart's side
    if not opt and opt_codes: return "NO TRADE", codes + [c for c in opt_codes if c.endswith("_FAIL")], None   # a hard contract rule fails
    against = (mk["state"] == "BEARISH" and want == "C") or (mk["state"] == "BULLISH" and want == "P")
    if mk["state"] == "CHOPPY" or against: return "WAIT", codes + ["MARKET_CONFLICT"], None
    if trig == "CHASE_EXCEEDED": return "MISSED", codes + ["CHASE_EXCEEDED"], None
    if trig != "TRIGGER_CONFIRMED": return "WAIT", codes + ["TRIGGER_WAITING"], None
    if not opt: return "NO TRADE", codes + ([c for c in opt_codes if c.endswith("_FAIL")] or ["DTE_FAIL"]), None
    mid = round((opt["bid"] + opt["ask"]) / 2, 2); ref = alert_ask or opt["ask"]
    risk = dict(limit=mid, max=round(min(ref * (1 + CHASE_ASK), MAX_COST / 100), 2), qty=1, cost=round(mid * 100),
                chart_stop=plan["m15"]["stop"], premium_stop=round(mid * 0.5, 2), take_profit=round(mid * 1.5, 2),
                planned_loss=round(mid * 50))
    if alert_ask and opt["ask"] > alert_ask * (1 + CHASE_ASK): return "MISSED", codes + ["CHASE_EXCEEDED"], risk
    return "NOW", codes + ["TRIGGER_CONFIRMED"], risk


def hold_or_out(pos, spot, bid, now):
    """Pure. An open position: OUT on the chart stop, +50% or -50%; else HOLD. Expiry day 14:45 CT = out
    (not in the locked plan; a 3-9 day option can't be left to expire -- noted in the backlog)."""
    sg = 1 if pos["right"] == "C" else -1
    if sg * (spot - pos["chart_stop"]) <= 0: return "OUT", "THESIS_INVALID"
    if bid and bid >= pos["take_profit"]: return "OUT", "TARGET_HIT"
    if bid and bid <= pos["premium_stop"]: return "OUT", "STOP_HIT"
    if now.date().isoformat() >= pos["exp"] and (now.hour, now.minute) >= (14, 45): return "OUT", "EXPIRY_CLOSE"
    return "HOLD", "THESIS_VALID"


# ---------- audit store ----------
def record(rec):
    os.makedirs(STORE, exist_ok=True)
    rec.setdefault("rule_version", RULE_VERSION); rec.setdefault("logged", dt.datetime.now(CT).isoformat(timespec="seconds"))
    with open(DB, "a") as f: f.write(json.dumps(rec, default=str) + "\n")
    return rec


def records():
    return [json.loads(l) for l in open(DB)] if os.path.exists(DB) else []


def load_state():
    return json.load(open(ST)) if os.path.exists(ST) else {}


def save_state(s):
    os.makedirs(STORE, exist_ok=True); json.dump(s, open(ST, "w"), indent=1)


# ---------- the seven-chart workspace ----------
def pin(sym, st, on=True):
    """Pure-ish: pins persist until removed; an eighth chart can't become active."""
    pins = st.setdefault("pins", [])
    if not on: st["pins"] = [p for p in pins if p != sym]; return f"Unpinned {sym}."
    if sym in pins: return f"{sym} is already pinned."
    if sym in INDEXES: return f"{sym} is an index -- Version 1 trades single stocks only."
    if len(pins) >= MAX_CHARTS: return f"Seven charts are pinned. Unpin one first (`unpin {pins[0]}`)."
    pins.append(sym); return f"Pinned {sym} ({len(pins)}/7)."


def pick_slots(pins, cands, dist):
    """Pure. Pins first, then the candidates closest to their trigger (in ATRs); never more than 7."""
    rest = sorted([c for c in dict.fromkeys(cands) if c not in pins and c not in INDEXES], key=lambda s: dist.get(s, 99))
    return (list(pins) + rest)[:MAX_CHARTS]


def build_workspace(st, now=None):
    """Morning prep: up to 7 charts with daily + 15-minute plans. Plans are saved for the day."""
    import scan_daily as S
    from ticket_bot import next_earnings
    now = now or dt.datetime.now(CT)
    d = json.load(open(os.path.join(H, "docs", "data.json"))); L = json.load(open(os.path.join(H, "docs", "levels.json")))
    cands = [r["sym"] for r in d.get("F", [])] + [x["sym"] for x in d.get("ihs", [])] + [x["symbol"] for x in L if not x.get("fired")]
    plans, dist = {}, {}
    for s in dict.fromkeys(st.get("pins", []) + cands):
        if s in INDEXES: continue
        try:
            e = next_earnings(s, now.date(), 12)
            p = make_plan(s, S.bars(s), yf(s, "10d", "15m"), e.isoformat() if e else None)
        except Exception as ex: p = dict(sym=s, complete=False, missing=[f"data: {str(ex)[:60]}"])
        p["source"] = source_of(s, d, L)
        try:
            p["bars_d"] = [[x[0], round(x[4], 2), round(x[2], 2), round(x[3], 2), round(x[1], 2)] for x in S.bars(s)[-130:]]   # date o h l c
            p["bars_15"] = [[int(x[0].timestamp() + x[0].utcoffset().total_seconds()), *[round(v, 2) for v in x[1:5]]] for x in yf(s, "5d", "15m")]
        except Exception: pass
        if p.get("complete"):
            spot = S.bars(s)[-1][1]; dist[s] = abs(spot - p["m15"]["trigger"]) / (p["m15"]["atr"] or 1)
        plans[s] = p
    slots = pick_slots(st.get("pins", []), [s for s in plans if plans[s].get("source")], dist)
    st["day"] = now.date().isoformat(); st["plans"] = {s: plans[s] for s in slots}
    st.setdefault("sources", {})
    for s in slots:
        if plans[s].get("source"): st["sources"].setdefault(f"{now.date()}:{s}", plans[s]["source"])
    return slots


# ---------- the live loop (called every minute by trade_manager) ----------
def card(state, sym, p, mk, codes, risk=None, opt=None, label="PAPER"):
    """The Discord card: the rule result first, then the market time, data age, PAPER/LIVE and source."""
    m = p["m15"]; side = m["direction"]; t = dt.datetime.now(CT).strftime("%-I:%M %p CT")
    src = (p.get("source") or {}).get("type", "none")
    head = f"**{state}  {sym} {side}**"
    L = [head]
    if state == "NOW" and opt:
        L += [f"{'Break above' if side == 'CALLS' else 'Break below'} ${m['trigger']:,.2f} confirmed · market {mk['state']}",
              f"**BUY 1 × {opt['exp'][5:].replace('-', '/')} ${opt['k']:g}{opt['right']}  LIMIT ${risk['limit']:.2f}  MAX ${risk['max']:.2f}**",
              f"COST ${risk['cost']:,}  PLANNED LOSS ${risk['planned_loss']:,}",
              f"OUT if {sym} {'falls to' if side == 'CALLS' else 'reclaims'} ${m['stop']:,.2f} · OCO +50% ${risk['take_profit']:.2f} / −50% ${risk['premium_stop']:.2f}",
              "Quote valid 2 minutes. Limit order only."]
    elif state == "HOLD":
        L += [f"Still {'above' if side == 'CALLS' else 'below'} ${m['trigger']:,.2f}", f"Target ${m['targets'][0]:,.2f} then ${m['targets'][1]:,.2f} · thesis fails at ${m['stop']:,.2f}",
              "Do not add · do not move the stop"]
    elif state == "OUT":
        L += [f"{', '.join(codes)} -- exit or confirm the fill", "Cancel the rest of the bracket after the fill. No re-entry without a new trigger."]
    elif state == "MISSED":
        L += ["Moved past the chase limit. Do not enter. No cheaper, farther strike."]
    else:
        L += [f"Plan {side} {'above' if side == 'CALLS' else 'below'} ${m['trigger']:,.2f} · stop area ${m['stop']:,.2f}",
              f"Targets ${m['targets'][0]:,.2f} then ${m['targets'][1]:,.2f} · {', '.join(codes)}"]
    L.append(f"_{t} · market {mk['state']} (data {mk['data_time'][11:16]}) · {label} · source {src} · rules v{RULE_VERSION}_")
    return "\n".join(L)


def recap(st, day):
    """Pure. The closing recap: the market, each chart's final state, today's paper results. Plans that
    finished or broke leave the workspace tomorrow morning; pinned charts stay."""
    mk = (st.get("mk") or {}).get("last") or {}
    L = [f"**CLOSING RECAP · {day}** · market {mk.get('state', '—')} · rules v{RULE_VERSION}"]
    for s, p in st.get("plans", {}).items():
        x = st.get("states", {}).get(s) or {}; pos = (st.get("pos") or {}).get(s)
        L.append(f"{s}: {x.get('state', 'WAIT')}" + (f" · paper {pos['k']:g}{pos['right']} {pos['status']}"
                 + (f" {'+' if pos.get('pl', 0) >= 0 else '−'}${abs(pos.get('pl', 0)):,.0f}" if pos.get("pl") is not None else "") if pos else "")
                 + (" · pinned" if s in st.get("pins", []) else ""))
    return "\n".join(L) + "\n_PAPER · tomorrow's seven charts are prepared at 7:00 CT_"


def settled(hist, state, n=3):
    """Pure. A new market state counts only after 3 reads in a row (15 minutes)."""
    return len(hist) >= n and all(h["state"] == state for h in hist[-n:])


def tick(say, now=None):
    """One pass: market state, workspace, trigger states, paper positions. Sends only on change."""
    import scan_daily as S
    now = now or dt.datetime.now(CT)
    if now.weekday() >= 5 or not (7, 0) <= (now.hour, now.minute) < (15, 10): return
    st = load_state(); day = now.date().isoformat()
    if (now.hour, now.minute) >= (15, 0):
        if st.get("recap") != day and st.get("day") == day:
            st["recap"] = day; save_state(st); say(recap(st, day))
        return
    if st.get("day") != day:
        build_workspace(st, now); st["sent"] = {}; st["mk"] = {}; save_state(st)
    # market: 8:15 provisional, 8:45 confirmed, then one message per change
    if (now.hour, now.minute) >= (8, 15) and (not st["mk"].get("last") or now.minute % 5 == 0):
        mk = market(now); prev = st["mk"].get("last")
        st.setdefault("mk_hist", []).append({k: mk[k] for k in ("at", "state", "score", "confirmed")}); st["mk_hist"] = st["mk_hist"][-200:]
        if prev and prev["confirmed"] == mk["confirmed"] and not settled(st["mk_hist"], mk["state"]):
            mk = dict(prev, data_time=mk["data_time"])      # a flicker: keep the settled state (2026-10-13: 14 flips in one day)
        if not prev or prev["state"] != mk["state"] or prev["confirmed"] != mk["confirmed"]:
            say(f"**MARKET {mk['state']}** · score {mk['score']:+d} · {'confirmed' if mk['confirmed'] else 'provisional (before 8:45)'}\n"
                f"ES {mk['parts']['es']:+d} · NQ {mk['parts']['nq']:+d} · SPY {mk['parts']['spy']:+d} · QQQ {mk['parts']['qqq']:+d} · breadth/VIX {mk['parts']['breadth_vix']:+d}\n"
                f"_{now.strftime('%-I:%M %p CT')} · context only, not a trade · rules v{RULE_VERSION}_")
            record(dict(kind="market", day=day, market=mk))
        st["mk"]["last"] = mk
    mk = st["mk"].get("last")
    if not mk or (now.hour, now.minute) < (8, 35): return save_state(st)
    equity = S.PLAN["balance"]
    for sym, p in st.get("plans", {}).items():
        try: step(sym, p, mk, st, say, now, equity)
        except Exception as ex: print("v1", sym, ex)
    follow_originals(st, now)
    save_state(st); write_site(st)


def step(sym, p, mk, st, say, now, equity, bs=None, quote_fn=None, chain=None):
    """One chart: open paper position -> HOLD/OUT; else WAIT/NOW/MISSED. A Discord card only on change."""
    day = now.date().isoformat(); pos = st.setdefault("pos", {}).get(sym)
    bs = bs if bs is not None else session(yf(sym), now.date())
    if not bs: return
    spot = bs[-1][4]; age = (now - bs[-1][0] - dt.timedelta(minutes=5)).total_seconds() / 60
    if pos and pos["status"] == "open":
        q = (quote_fn or (lambda e, k: option_quote(sym, e, k, pos["right"])))(pos["exp"], pos["k"])
        state, code = hold_or_out(pos, spot, q[0], now)
        if state == "OUT":
            px = q[0] or 0.0; pl = round((px - pos["fill"]) * 100 - 2 * FEE, 2)
            pos.update(status="closed", exit=px, exit_code=code, pl=pl, closed=now.isoformat(timespec="minutes"))
            record(dict(kind="result", id=pos["id"], sym=sym, result=dict(fill=pos["fill"], exit=px, net=pl, reason=code,
                        best=pos.get("best"), worst=pos.get("worst"), adherence="rules")))
        else:
            pos["best"] = max(pos.get("best") or 0, q[0] or 0); pos["worst"] = min(pos.get("worst") or 9e9, q[0] or 9e9)
        key = f"{sym}:{state}"
        if not st["sent"].get(key): st["sent"][key] = now.isoformat(timespec="minutes"); say(card(state, sym, p, mk, [code]))
        return
    if pos or (st.get("states", {}).get(sym) or {}).get("state") == "MISSED": return   # OUT or MISSED: no re-entry without a new prepared trigger
    if not p.get("complete"):
        st.setdefault("states", {})[sym] = dict(state="PREP NEEDED", codes=["PREP_MISSING"]); return
    trig, info = trigger_state(bs, p, now)
    want = "C" if p["m15"]["direction"] == "CALLS" else "P"
    opt, oc = choose(sym, want, spot, now.date(), p["daily"]["earnings"], equity, quote_fn, chain) if trig == "TRIGGER_CONFIRMED" else (None, [])
    alert = st.setdefault("alert_ask", {}).get(f"{day}:{sym}")
    stale = age > 10 or bool(opt and opt.get("t") and (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(opt["t"])).total_seconds() > 300)
    state, codes, risk = decide(dict(sym=sym, right=None), mk, p, p.get("source"), opt, oc, trig, equity, now, stale, alert)
    st.setdefault("states", {})[sym] = dict(state=state, codes=codes, at=now.isoformat(timespec="minutes"))
    key = f"{sym}:{state}"
    if st["sent"].get(key): return
    first = not any(k.startswith(sym + ":") for k in st["sent"])
    st["sent"][key] = now.isoformat(timespec="minutes")
    if first and state in ("WAIT", "NO TRADE", "PREP NEEDED"): return      # the morning state lives on the page; Discord stays quiet
    rid = str(uuid.uuid4())[:8]
    record(dict(kind="decision", id=rid, sym=sym, env="PAPER", request="Ledger workspace plan", original=None,
                market=mk, daily=p["daily"], m15=p["m15"], source=p.get("source"), trigger=dict(code=trig, **info),
                option=opt, risk=risk, state=state, codes=codes))
    if state == "NOW":
        st["alert_ask"][f"{day}:{sym}"] = opt["ask"]
        st["pos"][sym] = dict(id=rid, status="open", right=want, exp=opt["exp"], k=opt["k"], fill=opt["ask"],      # paper fill at the ask (conservative)
                              chart_stop=p["m15"]["stop"], take_profit=round(opt["ask"] * 1.5, 2), premium_stop=round(opt["ask"] * 0.5, 2),
                              opened=now.isoformat(timespec="minutes"), source=(p.get("source") or {}).get("type"), market=mk["state"])
    say(card(state, sym, p, mk, codes, risk, opt))


def follow_originals(st, now, quote_fn=None):
    """The original idea, followed quietly on paper with the same +50% / -50% bracket (no chart stop: the
    idea had none). Its result sits beside the improved plan in the report."""
    for oid, o in st.get("orig", {}).items():
        if o["status"] != "open": continue
        try: b = (quote_fn or (lambda: option_quote(o["sym"], o["exp"], o["k"], o["right"])))()[0]
        except Exception: continue
        code = ("TARGET_HIT" if b and b >= o["fill"] * 1.5 else "STOP_HIT" if b and b <= o["fill"] * 0.5 else
                "EXPIRY_CLOSE" if now.date().isoformat() >= o["exp"] and (now.hour, now.minute) >= (14, 45) else None)
        if code:
            pl = round((b - o["fill"]) * 100 - 2 * FEE, 2); o.update(status="closed", exit=b, pl=pl)
            record(dict(kind="result", id=oid, sym=o["sym"], result=dict(fill=o["fill"], exit=b, net=pl, reason=code, track="original idea")))


def improve(c, who="owner", now=None):
    """`check META calls` / `check BE 10/16 272.50 call`: the rough idea -> one rule state. The original
    idea is stored beside the improved plan. Returns (text, chart png or None)."""
    import scan_daily as S
    from ticket_bot import next_earnings
    now = now or dt.datetime.now(CT); st = load_state(); sym = c["sym"]
    d = json.load(open(os.path.join(H, "docs", "data.json"))); L = json.load(open(os.path.join(H, "docs", "levels.json")))
    p = (st.get("plans") or {}).get(sym)
    if not p:
        if sym in INDEXES: p = dict(sym=sym, complete=False, missing=[])
        else:
            e = next_earnings(sym, now.date(), 12)
            done = now.weekday() >= 5 or (now.hour, now.minute) >= (15, 0)      # after the close: today's bar counts (it is tomorrow's prep)
            p = make_plan(sym, S.bars(sym), yf(sym, "10d", "15m"), e.isoformat() if e else None,
                          (now.date() + dt.timedelta(days=1)).isoformat() if done else None)
        p["source"] = source_of(sym, d, L)
    mk = (st.get("mk") or {}).get("last") if st.get("day") == now.date().isoformat() else None
    mk = mk or market(now)
    bs = session(yf(sym), now.date()) if p.get("complete") else []
    open_ = now.weekday() < 5 and (8, 30) <= (now.hour, now.minute) < (15, 0)
    stale = not open_ or not bs or (now - bs[-1][0] - dt.timedelta(minutes=5)).total_seconds() > 600
    trig, info = trigger_state(bs, p, now) if bs else ("TRIGGER_WAITING", {})
    want = ("C" if p["m15"]["direction"] == "CALLS" else "P") if p.get("complete") else c.get("right")
    opt, oc = (None, [])
    if p.get("complete") and not stale and trig == "TRIGGER_CONFIRMED":
        opt, oc = choose(sym, want, bs[-1][4], now.date(), p["daily"]["earnings"], S.PLAN["balance"])
    state, codes, risk = decide(dict(sym=sym, right=c.get("right")), mk, p, p.get("source"), opt, oc, trig, S.PLAN["balance"], now, stale)
    orig = None
    if c.get("exp") and c.get("k"):
        try: b, a, t = option_quote(sym, c["exp"], c["k"], c["right"])
        except Exception: b = a = 0; t = None
        o = dict(exp=c["exp"], k=c["k"], right=c["right"], bid=b, ask=a)
        orig = dict(id=str(uuid.uuid4())[:8], contract=f"{sym} {c['exp']} {c['k']:g}{c['right']}", bid=b, ask=a,
                    quote_time=str(t), codes=contract_codes(o, p.get("daily", {}).get("earnings"), now.date(), S.PLAN["balance"]))
        if open_ and a: st.setdefault("orig", {})[orig["id"]] = dict(status="open", sym=sym, exp=c["exp"], k=c["k"], right=c["right"], fill=a)
    rid = str(uuid.uuid4())[:8]
    record(dict(kind="decision", id=rid, sym=sym, env="PAPER", who=who, request=c.get("text") or f"check {sym}", original=orig,
                market=mk, daily=p.get("daily"), m15=p.get("m15"), source=p.get("source"), trigger=dict(code=trig, **info),
                option=opt, risk=risk, state=state, codes=codes))
    if state == "NOW" and sym not in st.setdefault("pos", {}):
        st["pos"][sym] = dict(id=rid, status="open", right=want, exp=opt["exp"], k=opt["k"], fill=opt["ask"], chart_stop=p["m15"]["stop"],
                              take_profit=round(opt["ask"] * 1.5, 2), premium_stop=round(opt["ask"] * 0.5, 2),
                              opened=now.isoformat(timespec="minutes"), source=(p.get("source") or {}).get("type"), market=mk["state"])
    save_state(st)
    why = {"NO_SOURCE": "no Ledger source recorded before entry (Setup F, Pattern 7 or a logged level)",
           "INDEX_OPTION": "index options are out in Version 1 -- single stocks only", "PREP_MISSING": "the chart plan is not complete",
           "DATA_STALE": "market closed or quotes not current -- check again in market hours"}
    if not p.get("complete"):
        text = f"**{state}  {sym}**\n{why.get(codes[0], ', '.join(codes))}"
    else:
        text = card(state, sym, p, mk, codes, risk, opt)
        if "TREND_CONFLICT" in codes: text += f"\n↪ You asked for {'calls' if c.get('right') == 'C' else 'puts'}; the chart favors {p['m15']['direction'].lower()}."
        if state in ("NO TRADE", "DATA STALE") and codes[-1] in why: text += "\n" + why[codes[-1]]
    if orig and orig["codes"]: text += f"\nYour contract: {', '.join(orig['codes'])}"
    if who != "owner": text += "\n_Ledger's rules, not investment advice. You place every order._"
    png = None
    if p.get("complete"):
        try:
            m = p["m15"]
            png = S._chart(sym, f"{sym} · daily · plan {m['direction']}", [
                (m["trigger"], f"TRIGGER ${m['trigger']:,.2f}", "#000", ":", True), (m["stop"], f"THESIS FAILS ${m['stop']:,.2f}", "#c62828", "--"),
                (m["targets"][0], f"TARGET 1 ${m['targets'][0]:,.2f}", "#2e7d32", "--"), (m["targets"][1], f"TARGET 2 ${m['targets'][1]:,.2f}", "#2e7d32", ":")], start=-130)
        except Exception as ex: print("chart failed", ex)
    return text, png


def write_site(st):
    out = dict(rule_version=RULE_VERSION, updated=dt.datetime.now(CT).isoformat(timespec="minutes"), day=st.get("day"),
               market=st.get("mk", {}).get("last"), market_history=st.get("mk_hist", [])[-20:], pins=st.get("pins", []),
               charts=[dict(plan=p, state=st.get("states", {}).get(s, {"state": "WAIT" if p.get("complete") else "PREP NEEDED"}))
                       for s, p in st.get("plans", {}).items()])
    json.dump(out, open(WS, "w"), indent=1, default=str)


# ---------- the 30-session report ----------
def report():
    R = records(); res = {r["id"]: r for r in R if r["kind"] == "result"}
    dec = [r for r in R if r["kind"] == "decision"]
    sessions = sorted({r["logged"][:10] for r in R if r["kind"] == "market"})
    L = [f"# Version 1 paper test · {len(sessions)} of 30 sessions · rules v{RULE_VERSION}", ""]
    tracks = {"improved plan": [], "original idea": [], "no trade": []}
    for d in dec:
        if d["state"] == "NOW" and d["id"] in res: tracks["improved plan"].append(res[d["id"]]["result"]["net"])
        if d.get("original") and d["original"].get("id") in res: tracks["original idea"].append(res[d["original"]["id"]]["result"]["net"])
    for k, v in tracks.items():
        eq = pk = dd = 0.0
        for x in v: eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
        L.append(f"- **{k}**: {len(v)} trades · net ${sum(v):+,.0f} · worst drawdown ${dd:,.0f}" if k != "no trade" else "- **no trade**: $0")
    ch = {}
    for d in dec:
        for c in ("WAIT", "MISSED", "NO TRADE", "DATA STALE", "PREP NEEDED"):
            if d["state"] == c: ch[c] = ch.get(c, 0) + 1
        if "TREND_CONFLICT" in d.get("codes", []): ch["opposite direction"] = ch.get("opposite direction", 0) + 1
    L += ["", "Changed the idea: " + (" · ".join(f"{k} {v}" for k, v in ch.items()) or "none yet")]
    by = {}
    for d in dec:
        if d["state"] == "NOW" and d["id"] in res:
            k = f"{(d.get('source') or {}).get('type')} / {d['market']['state']}"; by.setdefault(k, []).append(res[d["id"]]["result"]["net"])
    L += ["", "By source / market: " + (" · ".join(f"{k}: {len(v)} ${sum(v):+,.0f}" for k, v in by.items()) or "none yet")]
    n = len(tracks["improved plan"]); net = sum(tracks["improved plan"])
    verdict = ("Insufficient evidence -- keep the rules, keep watching" if n < 10 else
               "Promising evidence -- consider a separately approved small live pilot" if net > 0 else "Failed evidence -- do not go live")
    L += ["", f"Decision so far: **{verdict}** ({n} eligible trades; needs 10+ and 30 sessions)."]
    return "\n".join(L)


# ---------- the 35-trade replay (Build Step 8) ----------
# (contract, cost $, DTE, source recorded before entry) -- from the exit gauntlet (thesis/exit-gauntlet.md)
HIST = [("BE 10/09 277.5C", 98, 0, "Setup F"), ("AAPL 10/16 335C", 401, 7, None), ("NOW 10/16 140C", 386, 8, "Setup F"),
        ("QQQ 10/09 751C", 102, 0, None), ("AMZN 10/23 255C", 591, 15, None), ("AMZN 10/21 260C", 515, 14, None),
        ("AMZN 10/12 250P", 242, 6, None), ("BE 10/02 292.5C", 230, 0, "Setup F"), ("META 10/02 710P", 227, 1, None),
        ("PLTR 10/02 190C", 254, 2, None), ("PLTR 10/02 192.5C", 170, 2, None), ("QQQ 10/02 746C", 304, 2, None),
        ("QQQ 10/02 746C", 248, 2, None), ("QQQ 10/02 745C", 281, 4, None), ("QQQ 10/05 750C", 565, 10, None),
        ("META 09/25 745P", 191, 0, None), ("QQQ 09/25 748C", 227, 2, None), ("QQQ 09/24 752C", 138, 2, None),
        ("BE 09/18 275C", 225, 0, "Setup F"), ("BABA 09/18 113C", 66, 0, None), ("BABA 10/02 109C", 435, 18, None),
        ("AMZN 09/21 245P", 119, 6, None), ("PYPL 09/25 53C x2", 332, 15, None), ("NVDA 09/11 222.5P", 114, 7, None),
        ("AMZN 09/18 272.5C", 485, 21, None), ("AMZN 09/04 257.5C", 167, 2, None), ("AMZN 09/04 257.5C", 217, 3, None),
        ("SPY 09/03 762P", 199, 3, None), ("SPY 09/03 763P", 205, 3, None), ("NVDA 08/28 220P", 85, 0, None),
        ("QQQ 09/09 720C", 845, 12, None), ("AAPL 08/31 315C", 247, 4, None), ("ANF 09/04 150C", 470, 8, None),
        ("AAPL 08/26 315C", 29, 0, None), ("SPY 08/26 761P", 203, 2, None)]
# entry times (ET, the 5-minute bar the fill landed in) from the exit gauntlet, same order as HIST
T_IN = ["10-09 09:45", "10-09 09:30", "10-08 09:45", "10-09 09:45", "10-08 13:45", "10-07 12:55", "10-06 09:30", "10-02 09:30",
        "10-01 09:30", "09-30 09:30", "09-30 09:30", "09-30 09:30", "09-30 15:55", "09-28 10:10", "09-25 09:55", "09-25 09:30",
        "09-23 09:35", "09-22 09:50", "09-18 09:50", "09-18 09:50", "09-14 10:50", "09-15 09:55", "09-10 11:55", "09-04 09:30",
        "08-28 11:00", "09-02 10:05", "09-01 09:35", "08-31 10:10", "08-31 09:35", "08-28 12:05", "08-28 09:35", "08-27 11:00",
        "08-27 09:40", "08-26 09:30", "08-24 09:30"]
MISSING = "market score, VWAP side, prepared trigger, trigger-bar volume, chart stop, option spread"


def replay_one(contract, cost, dte, src):
    """Pure: the hard-rule verdict from the fields the report recorded. Earnings: none landed inside
    these windows (checked in the gauntlet). Never guesses a missing value -- it is listed instead."""
    sym = contract.split()[0]; codes = []
    if not src: codes.append("NO_SOURCE")
    if sym in INDEXES: codes.append("INDEX_OPTION")
    if not DTE_MIN <= dte <= DTE_MAX: codes.append("DTE_FAIL")
    if cost > MAX_COST: codes.append("COST_FAIL")
    return ("PASS" if not codes else "FAIL"), codes


def alpaca_bars(sym, tf, start, end):
    """[(datetime CT, o, h, l, c, v)] from Alpaca's SIP history (free; no lookahead: ends where we say)."""
    import urllib.request
    out, tok = [], None
    while True:
        u = (f"https://data.alpaca.markets/v2/stocks/bars?symbols={sym}&timeframe={tf}&start={start}&end={end}"
             f"&limit=10000&feed=sip&adjustment=split" + (f"&page_token={tok}" if tok else ""))
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"APCA-API-KEY-ID": os.environ["ALPACA_KEY"],
                                                                                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}), timeout=30))
        out += [(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(CT), b["o"], b["h"], b["l"], b["c"], b["v"])
                for b in r.get("bars", {}).get(sym, [])]
        tok = r.get("next_page_token")
        if not tok: return out


def replay_full():
    """Build Step 8: every one of the 35 trades rebuilt at its entry time from data that existed then.
    Stock bars: Alpaca SIP. Futures and VIX: Yahoo 5-minute (60 days back). Option spread: no free
    history -- labelled MISSING, never guessed. Raw bars are cached in ~/.ledger-v1/replay_cache.json."""
    import scan_daily as S
    from ticket_bot import next_earnings
    cf = os.path.join(STORE, "replay_cache.json"); cache = json.load(open(cf)) if os.path.exists(cf) else {}
    def cached(key, fn):
        if key not in cache: cache[key] = [[b[0].isoformat(), *b[1:]] for b in fn()]
        return [(dt.datetime.fromisoformat(b[0]), *b[1:]) for b in cache[key]]
    fut = {k: cached(f"yf:{k}", lambda k=k: yf(k, "60d", "5m", True)) for k in ("ES=F", "NQ=F", "^VIX")}
    rows = []
    for i, (h, t_in) in enumerate(zip(HIST, T_IN)):
        contract, cost, dte, src = h; sym = contract.split()[0]; right = contract.split()[2].split()[0][-1]
        t = dt.datetime.fromisoformat(f"2026-{t_in[:5]}T{t_in[6:]}").replace(tzinfo=dt.timezone(dt.timedelta(hours=-4))).astimezone(CT)
        day = t.date(); exp = (dt.date(2026, int(contract.split()[1][:2]), int(contract.split()[1][3:5])))
        miss = ["option spread (no free quote history)"]
        d5 = lambda s: cached(f"5m:{s}:{day}", lambda: alpaca_bars(s, "5Min", f"{day}T13:00:00Z", f"{day}T21:00:00Z"))
        bs = [b for b in session(d5(sym), day) if b[0] + dt.timedelta(minutes=5) <= t + dt.timedelta(minutes=5)]
        m15 = cached(f"15m:{sym}:{day}", lambda: alpaca_bars(sym, "15Min", f"{day - dt.timedelta(days=12)}T13:00:00Z", f"{day}T13:00:00Z"))
        daily = [x for x in S.bars(sym) if x[0] < str(day)]
        ek = f"earn:{sym}:{day}"
        if ek not in cache: e = next_earnings(sym, day, (exp - day).days + 1); cache[ek] = e.isoformat() if e else None
        m15 = [b for b in m15 if (8, 30) <= (b[0].hour, b[0].minute) < (15, 0)]          # regular session, as live
        p = make_plan(sym, daily, m15, cache[ek], str(day)) if sym not in INDEXES else dict(complete=False)
        # market at the entry minute
        es, _ = futures_point([b for b in fut["ES=F"] if b[0] <= t], t); nq, _ = futures_point([b for b in fut["NQ=F"] if b[0] <= t], t)
        conf = (t.hour, t.minute) >= (8, 45)
        spy_b = [b for b in session(d5("SPY"), day) if b[0] < t]; qqq_b = [b for b in session(d5("QQQ"), day) if b[0] < t]
        (spy, cs), (qqq, cq) = (index_point(spy_b), index_point(qqq_b)) if conf else ((0, 0), (0, 0))
        rsp = [b for b in session(d5("RSP"), day) if b[0] < t]; rsp_prev = [x for x in S.bars("RSP") if x[0] < str(day)]
        vx = [b for b in fut["^VIX"] if b[0] <= t]; vx_prev = [b for b in fut["^VIX"] if b[0].date() < day]
        if not fut["ES=F"] or not [b for b in fut["ES=F"] if b[0].date() <= day - dt.timedelta(days=1)]: miss.append("futures")
        br = (rsp[-1][4] - rsp_prev[-1][1]) if rsp and rsp_prev else 0
        vr = bool(vx and vx_prev and vx[-1][4] > vx_prev[-1][4])
        bv = 1 if br > 0 and not vr else -1 if br < 0 and vr else 0
        mstate, score = score_state([es, nq, spy, qqq, bv], spy, qqq, (cs, cq), conf)
        mk = dict(state=mstate, score=score)
        trig, info = trigger_state(bs, p, t) if p.get("complete") and bs else ("TRIGGER_WAITING", {})
        if not bs: miss.append("stock bars")
        buy = cost / 100 / (2 if "x2" in contract else 1)
        o = dict(exp=exp.isoformat(), k=0, right=right, bid=buy, ask=buy)      # spread unknown: not judged here
        oc = [c for c in contract_codes(o, cache[ek], day, S.PLAN["balance"]) if c != "SPREAD_FAIL"]
        state, codes, risk = decide(dict(sym=sym, right=right), mk, p, dict(type=src) if src else None,
                                    None if oc else o, oc, trig, S.PLAN["balance"], t)
        hard, _ = replay_one(*h)
        rows.append(dict(n=i + 1, contract=contract, entry=t.strftime("%m-%d %H:%M CT"), market=f"{mstate} {score:+d}",
                         trend=(p.get("daily") or {}).get("trend", "—"), trigger=(p.get("m15") or {}).get("trigger"),
                         stop=(p.get("m15") or {}).get("stop"), trig=trig, state=state, codes=codes, hard=hard, missing=miss))
    json.dump(cache, open(cf, "w"))
    return rows


def replay():
    R = replay_full()
    L = [f"# Version 1 replay of the 35 trades · rules v{RULE_VERSION}", "",
         "Each trade rebuilt at its entry time from data that existed then (Build Step 8). Hard = the recorded-field verdict "
         "(Part C). V1 state = what the full engine would have said at that minute.", "",
         "| # | Contract | Entry | Market | Trend | Trigger | Stop | Hard | V1 state | Codes | Missing |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda x: f"${x:,.2f}" if x else "—"
    for r in R:
        L.append(f"| {r['n']} | {r['contract']} | {r['entry']} | {r['market']} | {r['trend']} | {f(r['trigger'])} | {f(r['stop'])} | "
                 f"{r['hard']} | {r['state']} | {', '.join(r['codes'])} | {'; '.join(r['missing'])} |")
    n = sum(r["hard"] == "PASS" for r in R); now_ = [r["contract"] for r in R if r["state"] == "NOW"]
    L += ["", f"**Hard rules: {n} PASS, {len(R) - n} FAIL. Full engine at the entry minute: {len(now_)} NOW"
          + (f" ({', '.join(now_)})" if now_ else "") + ".** The option spread has no free history and stays MISSING for every trade."]
    return "\n".join(L)


if __name__ == "__main__":
    if os.environ.get("V1TEST"):
        import tempfile; DB = os.path.join(tempfile.mkdtemp(), "d.jsonl")   # never the real paper log
        T = dt.date(2026, 10, 12); now = dt.datetime(2026, 10, 12, 10, 20, tzinfo=CT)
        # market score
        assert score_state([1, 1, 1, 0, 0], 1, 0, (0, 0), True) == ("BULLISH", 3)
        assert score_state([-1, -1, -1, -1, -1], -1, -1, (0, 0), True) == ("BEARISH", -5)
        assert score_state([1, 1, 1, -1, 1], 1, -1, (0, 0), True)[0] == "CHOPPY"             # SPY and QQQ disagree
        assert score_state([1, 1, 0, 0, 1], 0, 0, (3, 0), True)[0] == "CHOPPY"               # 3 VWAP crosses
        assert score_state([1, 1, 0, 0, 0], 0, 0, (3, 0), False)[0] == "MIXED"               # before 8:45 no CHOPPY
        # contract rules
        o = dict(exp="2026-10-19", k=100, right="C", bid=1.50, ask=1.60)
        assert contract_codes(o, None, T, 12000) == []
        assert contract_codes(dict(o, exp="2026-10-14"), None, T, 12000) == ["DTE_FAIL"]
        assert contract_codes(o, "2026-10-16", T, 12000) == ["EARNINGS_FAIL"]
        assert contract_codes(dict(o, bid=1.0), None, T, 12000) == ["SPREAD_FAIL"]
        assert contract_codes(dict(o, bid=4.0, ask=4.1), None, T, 12000) == ["COST_FAIL", "RISK_FAIL"]
        assert contract_codes(o, None, T, 3000) == ["RISK_FAIL"]                             # $80 loss > 2% of $3k
        # trigger
        base = dt.datetime(2026, 10, 12, 8, 30, tzinfo=CT)
        bs = [(base + dt.timedelta(minutes=5 * i), 99, 99.5, 98.5, 99, 1000) for i in range(20)]
        p = dict(m15=dict(direction="CALLS", trigger=100.0, atr=1.0, stop=99.0, targets=[102, 103], chase=100.25), complete=True,
                 daily=dict(earnings=None))
        assert trigger_state(bs, p, now)[0] == "TRIGGER_WAITING"
        bs2 = bs + [(base + dt.timedelta(minutes=100), 99.5, 100.3, 99.4, 100.2, 2000)]
        assert trigger_state(bs2, p, now)[0] == "TRIGGER_CONFIRMED"
        assert trigger_state(bs + [(base + dt.timedelta(minutes=100), 99.5, 100.3, 99.4, 100.2, 1100)], p, now)[0] == "TRIGGER_WAITING"   # thin volume
        assert trigger_state(bs + [(base + dt.timedelta(minutes=100), 99.5, 100.9, 99.4, 100.8, 2000)], p, now)[0] == "CHASE_EXCEEDED"
        # decide: every failure returns its reason
        mk = dict(state="BULLISH"); src = dict(type="Setup F")
        assert decide(dict(sym="QQQ"), mk, p, src, o, [], "TRIGGER_CONFIRMED", 12000, now)[:2] == ("NO TRADE", ["INDEX_OPTION"])
        assert decide(dict(sym="X"), mk, p, None, o, [], "TRIGGER_CONFIRMED", 12000, now)[:2] == ("NO TRADE", ["NO_SOURCE"])
        assert decide(dict(sym="X"), mk, dict(p, complete=False), src, o, [], "TRIGGER_CONFIRMED", 12000, now)[0] == "PREP NEEDED"
        assert decide(dict(sym="X"), mk, p, src, o, [], "TRIGGER_CONFIRMED", 12000, now, stale=True)[0] == "DATA STALE"   # stale never NOW
        assert decide(dict(sym="X"), mk, p, src, None, ["COST_FAIL"], "TRIGGER_CONFIRMED", 12000, now)[:2] == ("NO TRADE", ["COST_FAIL"])
        assert decide(dict(sym="X"), dict(state="BEARISH"), p, src, None, ["DTE_FAIL"], "TRIGGER_WAITING", 12000, now)[:2] == ("NO TRADE", ["DTE_FAIL"])
        assert decide(dict(sym="X"), dict(state="CHOPPY"), p, src, o, [], "TRIGGER_CONFIRMED", 12000, now)[1] == ["MARKET_CONFLICT"]
        assert decide(dict(sym="X"), dict(state="BEARISH"), p, src, o, [], "TRIGGER_CONFIRMED", 12000, now)[0] == "WAIT"
        assert decide(dict(sym="X"), mk, p, src, o, [], "TRIGGER_WAITING", 12000, now)[:2] == ("WAIT", ["TRIGGER_WAITING"])
        assert decide(dict(sym="X"), mk, p, src, o, [], "CHASE_EXCEEDED", 12000, now)[0] == "MISSED"
        assert decide(dict(sym="X"), mk, p, src, o, [], "TRIGGER_CONFIRMED", 12000, now, alert_ask=1.30)[0] == "MISSED"   # ask +23%
        s, c, r = decide(dict(sym="X", right="P"), mk, p, src, o, [], "TRIGGER_CONFIRMED", 12000, now)
        assert s == "NOW" and c == ["TREND_CONFLICT", "TRIGGER_CONFIRMED"] and r["limit"] == 1.55 and r["planned_loss"] == 78
        assert r["chart_stop"] == 99.0 and r["premium_stop"] == 0.78                          # both stop layers stored
        # choose: ATM or one ITM, 3-9 DTE, all rules
        ch = [("2026-10-14", 100.0), ("2026-10-16", 99.0), ("2026-10-16", 100.0), ("2026-10-16", 101.0)]
        qf = lambda e, k: {("2026-10-16", 100.0): (1.0, 1.4), ("2026-10-16", 99.0): (1.9, 2.0)}.get((e, k), (0, 0))
        oo, _ = choose("X", "C", 100.2, T, None, 12000, qf, ch)
        assert oo["k"] == 99.0 and oo["exp"] == "2026-10-16"                                # ATM spread too wide -> one ITM
        assert choose("X", "C", 100.2, T, "2026-10-15", 12000, qf, ch)[0] is None           # earnings before expiry
        # hold / out
        pos = dict(right="C", chart_stop=99.0, take_profit=2.4, premium_stop=0.8, exp="2026-10-16")
        assert hold_or_out(pos, 100.5, 1.6, now) == ("HOLD", "THESIS_VALID")
        assert hold_or_out(pos, 98.9, 1.6, now) == ("OUT", "THESIS_INVALID")
        assert hold_or_out(pos, 100.5, 2.5, now) == ("OUT", "TARGET_HIT") and hold_or_out(pos, 100.5, 0.7, now) == ("OUT", "STOP_HIT")
        # seven charts
        stt = {"pins": [f"S{i}" for i in range(7)]}
        assert pin("META", stt).startswith("Seven charts") and len(stt["pins"]) == 7
        assert pin("QQQ", {"pins": []}).startswith("QQQ is an index")
        assert len(pick_slots(["A"], [f"B{i}" for i in range(10)] + ["SPY"], {})) == 7 and pick_slots(["A"], ["C", "B"], {"B": 0.1, "C": 2})[:2] == ["A", "B"]
        # once-only dispatch: the same state twice sends one message
        sent = []; stt = dict(sent={}, states={}, pos={}, alert_ask={})
        p2 = dict(p, source=src, daily=dict(earnings=None))
        step("X", p2, dict(state="BULLISH", data_time="2026-10-12T10:00"), stt, sent.append, now, 12000, bs2, lambda e, k: (*qf(e, k), None), ch)
        step("X", p2, dict(state="BULLISH", data_time="2026-10-12T10:00"), stt, sent.append, now, 12000, bs2, lambda e, k: (*qf(e, k), None), ch)
        assert len(sent) == 2 and sent[0].startswith("**NOW") and sent[1].startswith("**HOLD"), sent   # NOW, then one HOLD
        step("X", p2, dict(state="BULLISH", data_time="2026-10-12T10:00"), stt, sent.append, now, 12000, bs2, lambda e, k: (*qf(e, k), None), ch)
        assert len(sent) == 2                                                                  # third pass: nothing
        # MISSED is final for the day (2026-10-13: SG went MISSED at 8:40, then NOW at 11:01)
        sent = []; stt = dict(sent={}, states={"X": dict(state="MISSED")}, pos={}, alert_ask={})
        step("X", p2, dict(state="BULLISH", data_time="2026-10-12T10:00"), stt, sent.append, now, 12000, bs2, lambda e, k: (*qf(e, k), None), ch)
        assert sent == [] and not stt["pos"]
        h = [dict(state="MIXED")] * 3 + [dict(state="BEARISH")] * 2
        assert recap(dict(plans={"X": {}}, states={"X": dict(state="MISSED")}, mk={}), "2026-10-13").splitlines()[1] == "X: MISSED"
        assert not settled(h, "BEARISH") and settled(h + [dict(state="BEARISH")], "BEARISH")
        # replay: one PASS (NOW 10/16 140C), matching the locked plan's Part C
        assert [h[0] for h in HIST if replay_one(*h)[0] == "PASS"] == ["NOW 10/16 140C"]
        # audit: a sample decision saves and reloads with every group (Part B)
        record(dict(kind="decision", id="t1", sym="X", env="PAPER", request="check X calls", original=None, market=mk, daily=p["daily"],
                    m15=p["m15"], source=src, option=o, risk=r, state=s, codes=c))
        back = records()[-1]
        assert all(back.get(k) is not None for k in ("id", "sym", "logged", "env", "request", "rule_version", "market", "daily", "m15",
                                                       "source", "option", "risk", "state", "codes")) and back["rule_version"] == "1.0"
        print("v1 ok")
    elif sys.argv[1:] == ["report"]: print(report())
    elif sys.argv[1:] == ["replay"]: print(replay())
    elif sys.argv[1:] == ["prep"]:
        s = load_state(); print(build_workspace(s)); save_state(s); write_site(s)
