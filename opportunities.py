#!/usr/bin/env python3
"""Opportunity history (reviews/trade-plan-design.md, step 1). One append-only file, docs/opportunities.jsonl:
a PLAN row the moment an alert fires (the rule's own fields; a missing rule is written as "none in rules", never
guessed) and later OUTCOME rows with the same id, replayed only from the exit rule saved in the plan.
Real / paper / idea are never added together. Nothing here trades or decides.

  opportunities.py nightly   after the close: copy V1 results, replay every open plan, write outcomes
  opportunities.py selftest  synthetic bars, sends nothing"""
import datetime as dt, hashlib, json, os, sys
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); OPP = os.environ.get("LEDGER_OPP") or os.path.join(H, "docs", "opportunities.jsonl")
V1DB = os.path.expanduser("~/.ledger-v1/decisions.jsonl")
CT, NY = ZoneInfo("America/Chicago"), ZoneInfo("America/New_York"); NONE = "none in rules"; SLIP = 0.001   # 0.10% each way on shares, as in the research
EXIT = {"f-shares-v1": "target = the 1-year high, fixed at entry; once touched, a floor at the target: sell on the first low back at it; else sell at 252 sessions; no stop",
        "ihs-v1": "limit at the neckline break, no fill above the chase line, only inside the valid window; stop = right-shoulder low; at the target a trail under the highest close",
        "v1": "the V1 ticket's own exits: +50% of premium, -50% of premium, chart stop, 14:45 CT on expiry day"}


def rows():
    try: return [json.loads(l) for l in open(OPP) if l.strip()]
    except FileNotFoundError: return []


def write(r):
    os.makedirs(os.path.dirname(OPP), exist_ok=True)
    with open(OPP, "a") as f: f.write(json.dumps(r, default=str) + "\n")
    return r


def open_ids(R=None):
    """Plan ids with no closing outcome yet (never-triggered counts as closed)."""
    R = rows() if R is None else R; done = {r["id"] for r in R if r["kind"] == "outcome" and r["result"] != "open"}
    return [r["id"] for r in R if r["kind"] == "plan" and r["id"] not in done]


def plan(setup, sym, book, made_at, exit_rule, **f):
    """Write one plan row unless this (setup, sym) already has an open plan. made_at: aware datetime."""
    R = rows(); live = set(open_ids(R))
    if any(r["kind"] == "plan" and r["id"] in live and r["setup"] == setup and r["sym"] == sym for r in R): return None
    try:
        import scan_daily as S; bal, asof = S.PLAN["balance"], S.PLAN.get("balance_as_of", "")
    except Exception: bal, asof = None, ""
    ct = made_at.astimezone(CT); nxt = ct.date() + dt.timedelta(days=1 if ct.hour >= 16 else 0)
    while nxt.weekday() >= 5: nxt += dt.timedelta(days=1)
    r = dict(kind="plan", id=hashlib.sha256(f"{setup}|{sym}|{ct.isoformat()}".encode()).hexdigest()[:10], setup=setup, sym=sym, book=book,
             made_at=ct.isoformat(timespec="minutes"), made_at_utc=made_at.astimezone(dt.timezone.utc).isoformat(timespec="minutes"),
             status="alerted", exit_rule=exit_rule, exit_text=EXIT.get(exit_rule, NONE), review_on=f"{nxt} closing run",
             balance_as_of=asof, balance_stale=bool(asof) and (ct.date() - dt.date.fromisoformat(asof)).days > 7)
    for k in ("why", "entry", "vehicle", "size", "target", "stop", "hold_limit", "invalidation", "valid_until", "evidence"): r[k] = f.pop(k, NONE)
    r.update(f); return write(r)


def from_scan(d):
    """Plan rows for today's Setup F fires and pattern-#7 alerts, as the cards describe them."""
    made = dt.datetime.strptime(d["stamp"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=dt.timezone.utc); n = 0
    for x in d.get("F", []):
        n += bool(plan("Setup F", x["sym"], "IDEA", made, "f-shares-v1", why=f"{x['dd']:.0f}% under its 1-year high, 10>30 and 50>200 EMA, revenue +{x.get('rev', 0):.0f}%",
                       entry=f"next open (signal close ${x['px']:,.2f}; the daily bar was {'final' if made.astimezone(NY).hour >= 16 else 'NOT final'} at alert time)",
                       vehicle="shares", size="7% of the account", target=round(x["tgt"], 2), stop="none (by design)", hold_limit="252 sessions",
                       signal_px=round(x["px"], 2), evidence=d["stamp"]))
    for x in d.get("ihs", []):
        if x.get("qty", 0) < 1: continue
        at = dt.datetime.strptime(x["at"], "%Y-%m-%d %H:%M").replace(tzinfo=NY)
        n += bool(plan("Pattern 7", x["sym"], "PAPER:ihs", made, "ihs-v1", why=f"inverse head-and-shoulders broke the neckline ${x['neck']:,.2f} on the {x['tf']} chart",
                       entry=x["entry"], chase=x["chase"], vehicle="shares", size=x["qty"], target=x["target"], stop=x["stop"], trail=x["trail"], tf=x["tf"],
                       hold_limit=NONE, invalidation="the stop", valid_until=x.get("valid", NONE), alert_at=at.isoformat(timespec="minutes"), evidence=d["stamp"]))
    return n


def from_v1():
    """Copy V1 NOW tickets (plans) and their results (outcomes) by V1 id; V1 already saves its plan at alert time."""
    try: V = [json.loads(l) for l in open(V1DB) if l.strip()]
    except FileNotFoundError: return 0
    R = rows(); have = {(r["kind"], r.get("v1_id")) for r in R}; n = 0
    for v in V:
        if v.get("kind") == "decision" and v.get("state") == "NOW" and ("plan", v["id"]) not in have:
            m, o, rk = v.get("m15") or {}, v.get("option") or {}, v.get("risk") or {}
            made = dt.datetime.fromisoformat(v["logged"])
            p = plan("V1", v["sym"], "PAPER:v1", made, "v1", why=(v.get("source") or {}).get("evidence", NONE), entry=rk.get("limit", o.get("ask", NONE)), chase=m.get("chase", NONE),
                     vehicle=f"{o.get('right', '?')} {o.get('k', '?')} exp {o.get('exp', '?')}", size="1 contract", target=rk.get("take_profit", NONE),
                     stop=f"chart {rk.get('chart_stop', NONE)} / premium {rk.get('premium_stop', NONE)}", hold_limit=f"expiry {o.get('exp', '?')} 14:45 CT",
                     invalidation=(v.get("daily") or {}).get("thesis", NONE), valid_until="chase line", evidence="v1 decisions.jsonl", v1_id=v["id"])
            n += bool(p)
        if v.get("kind") == "result" and ("outcome", v["id"]) not in have:
            pid = next((r["id"] for r in rows() if r["kind"] == "plan" and r.get("v1_id") == v["id"]), None)
            if not pid: continue
            res = v["result"]; fill = res.get("fill") or 0
            write(dict(kind="outcome", id=pid, v1_id=v["id"], result="closed", entry_available=True, fill=fill, exit_price=res.get("exit"), exit_date=v["logged"][:10],
                       pnl_per_1000=round(1000 * res["net"] / (fill * 100), 1) if fill else None, reason=res.get("reason"), priced="copied from V1 (real quotes at the time)",
                       at=dt.datetime.now(CT).isoformat(timespec="minutes"))); n += 1
    return n


def replay_f(p, b):
    """Setup F shares on daily bars (date, close, high, low, open, vol): fill at the next open after the signal."""
    sig = p["made_at"][:10]; after = [x for x in b if x[0] > sig]
    if not after: return dict(result="open", entry_available=None, note="no session yet")
    fill = after[0][4] * (1 + SLIP); tgt = float(p["target"]); held = after
    armed = next((i for i, x in enumerate(held) if x[2] >= tgt), None)
    floor = next((x for x in held[armed + 1:] if x[3] <= tgt), None) if armed is not None else None
    if floor: ex, day, why = tgt, floor[0], "floor at target"
    elif len(held) >= 252: ex, day, why = held[251][1], held[251][0], "252 sessions"
    else: return dict(result="open", entry_available=True, fill=round(fill, 2), fill_date=after[0][0], mark=held[-1][1], mark_date=held[-1][0],
                      pnl_per_1000=round(1000 * (held[-1][1] * (1 - SLIP) / fill - 1), 1), armed=armed is not None, sessions=len(held))
    ex *= 1 - SLIP
    return dict(result="closed", entry_available=True, fill=round(fill, 2), fill_date=after[0][0], exit_price=round(ex, 2), exit_date=day,
                pnl_per_1000=round(1000 * (ex / fill - 1), 1), reason=why)


def replay_ihs(p, bars15):
    """Pattern #7 on 15-minute NY bars (t, high, low, close) from the alert: fill at the limit inside the valid window and under the chase line."""
    at = dt.datetime.fromisoformat(p["alert_at"]); day = at.date()
    vt = dt.datetime.strptime(p["valid_until"], "%I:%M %p CT").time() if p.get("valid_until", NONE) != NONE else dt.time(16, 0)
    end = dt.datetime.combine(day, vt, CT).astimezone(NY)
    e, chase, stop, tgt, trail = (float(p[k]) for k in ("entry", "chase", "stop", "target", "trail"))
    B = [x for x in bars15 if x[0] >= at]; fi = None
    for i, (t, h, l, c) in enumerate(B):
        if t > end: break
        if l <= e: fi = i; break
        if c > chase: return dict(result="never-triggered", entry_available=False, reason=f"ran past the chase line ${chase:,.2f} before filling")
    if fi is None: return dict(result="never-triggered", entry_available=False, reason="the limit never filled inside the valid window") if B and B[-1][0] > end else dict(result="open", entry_available=None, note="window still open")
    fill = e; hi = -1e9; tstop = None
    for t, h, l, c in B[fi + 1:]:
        if l <= stop: return dict(result="closed", entry_available=True, fill=fill, fill_date=str(B[fi][0])[:16], exit_price=stop, exit_date=str(t)[:16], pnl_per_1000=round(1000 * (stop / fill - 1), 1), reason="stop")
        if h >= tgt: hi = max(hi, c); tstop = hi - trail
        if tstop is not None and l <= tstop: return dict(result="closed", entry_available=True, fill=fill, fill_date=str(B[fi][0])[:16], exit_price=round(tstop, 2), exit_date=str(t)[:16], pnl_per_1000=round(1000 * (tstop / fill - 1), 1), reason="trail after target")
    last = B[-1]
    return dict(result="open", entry_available=True, fill=fill, fill_date=str(B[fi][0])[:16], mark=last[3], mark_date=str(last[0])[:16], pnl_per_1000=round(1000 * (last[3] / fill - 1), 1), trail_active=tstop is not None)


def bars15(sym):
    import scan_daily as S
    q = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=60d&interval=15m")["chart"]["result"][0]; o = q["indicators"]["quote"][0]; out = []
    for i, t in enumerate(q["timestamp"]):
        z = dt.datetime.fromtimestamp(t, NY)
        if o["close"][i] is not None and (9, 30) <= (z.hour, z.minute) < (16, 0): out.append((z, o["high"][i], o["low"][i], o["close"][i]))
    return out


def outcomes(daily=None, intraday=None):
    """One outcome row per open plan (F and #7 replayed on bars; V1 copied by from_v1). Returns the rows written."""
    import scan_daily as S
    daily = daily or (lambda s: S.bars(s)); intraday = intraday or bars15; R = rows(); out = []
    for p in [r for r in R if r["kind"] == "plan" and r["id"] in set(open_ids(R))]:
        try:
            if p["exit_rule"] == "f-shares-v1": o = replay_f(p, daily(p["sym"])); o["priced"] = "modeled: daily bars, 0.10% slippage each way"
            elif p["exit_rule"] == "ihs-v1": o = replay_ihs(p, intraday(p["sym"])); o["priced"] = "modeled: 15-minute bars, fills at the limit, no slippage"
            else: continue                                                       # V1: outcomes come from its own result rows
        except Exception as e: o = dict(result="open", note=f"replay failed: {str(e)[:60]}")
        out.append(write(dict(kind="outcome", id=p["id"], at=dt.datetime.now(CT).isoformat(timespec="minutes"), **o)))
    return out


def nightly():
    now = dt.datetime.now(CT); stamp = os.path.join(H, "docs", ".opportunities-day")
    done = open(stamp).read().strip() if os.path.exists(stamp) else ""
    if now.weekday() >= 5 or (now.hour, now.minute) < (15, 20) or done == str(now.date()): return "skipped"
    n = from_v1(); o = outcomes(); open(stamp, "w").write(str(now.date()))
    return f"v1 copied {n} · outcomes {len(o)} ({sum(x['result'] == 'closed' for x in o)} closed, {sum(x['result'] == 'never-triggered' for x in o)} never triggered)"


def selftest():
    global OPP, V1DB
    import tempfile; OPP = os.path.join(tempfile.mkdtemp(), "o.jsonl"); V1DB = os.path.join(tempfile.mkdtemp(), "v.jsonl")
    made = dt.datetime(2026, 11, 3, 8, 30, tzinfo=CT)
    d = dict(stamp="2026-11-03 14:30 UTC", F=[dict(sym="SHOP", px=142.57, dd=21.2, tgt=180.88, rev=31)], ihs=[dict(sym="SPY", tf="30-MIN", at="2026-11-04 09:30", entry=764.07, neck=763.86,
             stop=753.51, target=776.12, trail=4.59, qty=1, chase=766.71, valid="9:30 AM CT"), dict(sym="QQQ", tf="4-HOUR", at="2026-11-04 09:30", entry=1, neck=1, stop=1, target=2, trail=1, qty=0, chase=1)])
    assert from_scan(d) == 2 and from_scan(d) == 0                               # once per open (setup, sym); qty 0 = PASS, no plan
    P = {r["sym"]: r for r in rows()}; assert P["SHOP"]["hold_limit"] == "252 sessions" and P["SHOP"]["invalidation"] == NONE and P["SHOP"]["review_on"] == "2026-11-03 closing run"
    assert P["SPY"]["valid_until"] == "9:30 AM CT" and "NOT final" in P["SHOP"]["entry"]
    # F replay: fill next open 144, target 180.88 touched then the floor hit -> closed at the target
    D = [("2026-11-03", 142.57, 143, 141, 142, 1), ("2026-11-04", 150, 151, 143, 144, 1), ("2026-11-05", 179, 181, 175, 176, 1), ("2026-11-06", 178, 182, 180.5, 181, 1)]
    o = replay_f(P["SHOP"], D); assert o["result"] == "closed" and o["reason"] == "floor at target" and o["exit_date"] == "2026-11-06" and 240 < o["pnl_per_1000"] < 260, o
    o = replay_f(P["SHOP"], D[:3]); assert o["result"] == "open" and o["armed"] and o["sessions"] == 2
    # #7 replay: fill at 764.07 inside the window, stop hit later
    t0 = dt.datetime(2026, 11, 4, 9, 30, tzinfo=NY); q = lambda m, h, l, c: (t0 + dt.timedelta(minutes=m), h, l, c)
    o = replay_ihs(P["SPY"], [q(0, 765, 763.9, 764.5), q(15, 765, 764, 764.2), q(60, 764, 753, 753.4)]); assert o["result"] == "closed" and o["reason"] == "stop", o
    o = replay_ihs(P["SPY"], [q(0, 767, 766, 766.9), q(15, 768, 765, 767)]); assert o["result"] == "never-triggered" and "chase" in o["reason"], o
    o = replay_ihs(P["SPY"], [q(0, 765, 764.5, 764.8), q(60, 766, 765, 765.5), q(90, 766, 765, 765.5)]); assert o["result"] == "never-triggered" and "window" in o["reason"], o
    o = replay_ihs(P["SPY"], [q(0, 765, 764, 764.5), q(15, 777, 770, 776.5), q(30, 778, 771.5, 772)]); assert o["result"] == "closed" and o["reason"] == "trail after target" and o["exit_price"] == round(776.5 - 4.59, 2), o
    # V1 copy: plan then result
    open(V1DB, "w").write(json.dumps(dict(kind="decision", id="abc", sym="NOW", state="NOW", logged="2026-11-04T10:20:00-06:00", m15=dict(chase=150.6), option=dict(right="C", k=150, exp="2026-11-07", ask=1.5),
                                          risk=dict(limit=1.55, take_profit=2.33, chart_stop=149, premium_stop=0.78), source=dict(evidence="Setup F fire"), daily=dict(thesis="10 EMA above the 30 EMA"))) + "\n")
    assert from_v1() == 1 and from_v1() == 0
    open(V1DB, "a").write(json.dumps(dict(kind="result", id="abc", sym="NOW", logged="2026-11-05T14:45:00-06:00", result=dict(fill=1.55, exit=0.78, net=-78.3, reason="STOP_HIT"))) + "\n")
    assert from_v1() == 1; last = rows()[-1]; assert last["kind"] == "outcome" and last["pnl_per_1000"] == -505.2 and last["result"] == "closed"
    # outcomes(): SHOP + SPY replayed, V1 untouched; closed ones leave the open list
    out = outcomes(daily=lambda s: D, intraday=lambda s: [q(0, 765, 763.9, 764.5), q(60, 764, 753, 753.4)]); assert len(out) == 2 and open_ids() == [], open_ids()
    print("selftest ok:", len(rows()), "rows")


if __name__ == "__main__":
    a = sys.argv[1:2]
    if a == ["selftest"]: selftest()
    elif a == ["nightly"]: print(nightly())
    else: print(__doc__)
