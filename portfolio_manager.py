#!/usr/bin/env python3
"""PORTFOLIO MANAGER (2026-10-19): two fully automated PAPER books, $100,000 each, every idle dollar in SPY.
  F V1      Setup F entries, 7% of equity funded by selling SPY, out after 252 sessions, proceeds back to SPY
  SHADOW C  the same entries; the registered Test C exit (after a touch of the 252-high target, out at the next
            open after a close under the 30-day EMA), otherwise 252 sessions. Paper only, never a signal.
Timing: the signal is the scan's Setup F list at a session's CLOSE. Orders are made after that close and filled
at the first run of the NEXT session while the market is open, at the live price, 0.10% each way. Exits are
judged on completed closes only, then filled the same way. SPY and stock dividends are reinvested (cash sweeps
into SPY); splits adjust the holding. Stale or missing data -> no order, reason logged. Idempotent: one
decision pass per session, orders keyed session+name+side, fills only for orders made on an earlier session.
State ~/.ledger-pm/<book>.json · append-only ledger ~/.ledger-pm/<book>.jsonl · every run replays the ledger
and must land on the state (reconcile) or it posts a risk event. Controls: `pause` / `resume` / `kill` / `status`
(files PAUSE and KILL in ~/.ledger-pm). No broker, no real money; nothing here changes the scan, Setup F,
Version 1 or #trades. Self-test: PMTEST=1 python3 portfolio_manager.py"""
import datetime, json, os, sys
import scan_daily as S
DIR = os.path.expanduser("~/.ledger-pm"); START = 100_000.0; COST = 0.001; HOLD = 252; SIZE = 0.07
BOOKS = {"f": dict(name="F V1", exit="252"), "c": dict(name="SHADOW C", exit="C"),
         "x": dict(name="F CAP 70", exit="252", cap=0.70),   # 2026-10-22: entry cap, paper only (thesis/allocation-audit.md); cloned from F V1 at start
         "s": dict(name="F FULL S&P", exit="252", src="full")}   # 2026-10-23: research rule on the full S&P 500 (f_full.py); own $100k, starts empty
NOTIFY = ("order", "fill", "rejected", "risk", "split")


def load(book):
    f = os.path.join(DIR, book + ".json")
    return json.load(open(f)) if os.path.exists(f) else dict(cash=START, spy=0.0, pos={}, pending=[], trades=[], curve=[], decided=[], acted=[], events=[])


def save(book, st): os.makedirs(DIR, exist_ok=True); json.dump(st, open(os.path.join(DIR, book + ".json"), "w"), indent=1)


def ledger(book, e):
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, book + ".jsonl"), "a") as f: f.write(json.dumps(e) + "\n")
    return e


def lines(book):
    f = os.path.join(DIR, book + ".jsonl")
    return [json.loads(x) for x in open(f)] if os.path.exists(f) else []


def equity(st, px):
    spx = px("SPY") or 0
    return st["cash"] + st["spy"] * spx + sum(p["q"] * (px(s) or p["entry"]) for s, p in st["pos"].items())


STALE_S = 900     # a quote older than 15 minutes does not fill an order


def quote(sym):
    """Live price WITH its timestamp (Yahoo's regular-market quote). None when missing."""
    try:
        m = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1m")["chart"]["result"][0]["meta"]
        t = datetime.datetime.fromtimestamp(m["regularMarketTime"], datetime.timezone.utc)
        return dict(px=float(m["regularMarketPrice"]), time=t.isoformat(timespec="seconds"), age=(datetime.datetime.now(datetime.timezone.utc) - t).total_seconds(), source="yahoo")
    except Exception: return None


def step(st, book, today, fires, px, bars_fn, market_open, actions=lambda s: {}, quote=None):
    """One run. px(sym) -> live price or None. bars_fn(sym) -> COMPLETED sessions only, (date, close, high, low,
    open, volume). fires -> today's Setup F list (sym, px, tgt), used only after today's close. Returns the events."""
    ev = []
    def L(kind, **kw):
        e = dict(date=today, at=datetime.datetime.now().strftime("%H:%M"), kind=kind, **kw); ledger(book, e); ev.append(e)
        st["events"] = (st["events"] + [e])[-60:]; return e
    sb = bars_fn("SPY"); last = sb[-1][0] if sb else None
    if not last or (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(last)).days > 5:
        L("risk", what=f"data stale: last SPY close {last}; no orders this run"); return ev
    spx = px("SPY")
    # 1 corporate actions on what is held (splits adjust the holding; dividends become cash)
    for sym, p in list(st["pos"].items()):
        a = actions(sym)
        for d, r in sorted(a.get("split", {}).items()):
            key = f"{sym}:{d}:split"
            if p["opened"] < d <= today and key not in st["acted"]:
                p["q"] = int(round(p["q"] * r)); p["entry"] = p["entry"] / r; p["tgt"] = p["tgt"] / r; st["acted"].append(key); L("split", sym=sym, ratio=r, q=p["q"])
        for d, amt in sorted(a.get("div", {}).items()):
            key = f"{sym}:{d}:div"
            if p["opened"] < d <= today and key not in st["acted"]: st["cash"] += p["q"] * amt; st["acted"].append(key); L("dividend", sym=sym, amt=p["q"] * amt)
    for d, amt in sorted(actions("SPY").get("div", {}).items()):
        key = f"SPY:{d}:div"
        if st["spy"] > 0 and st.get("since", "9999") < d <= today and key not in st["acted"]: st["cash"] += st["spy"] * amt; st["acted"].append(key); L("dividend", sym="SPY", amt=st["spy"] * amt)
    # 2 fills: orders from an earlier session, market open, live price
    if market_open and spx:
        for o in list(st["pending"]):
            if o["made"] >= today: continue
            qv = quote(o["sym"]) if quote else dict(px=px(o["sym"]), time=None, age=0, source="bars")
            if not qv or not qv["px"] or qv["px"] <= 0: L("hold", sym=o["sym"], what="no live price; order kept"); continue
            if qv["age"] > STALE_S: L("hold", sym=o["sym"], what=f"quote is {qv['age'] / 60:.0f} min old ({qv['time']}); order kept"); continue
            p = qv["px"]; q_at = dict(quote_time=qv["time"], age_s=round(qv["age"]), source=qv["source"])
            if o["side"] == "buy":
                eq = equity(st, px); avail = st["cash"] + st["spy"] * spx * (1 - COST); want = min(SIZE * eq, avail)
                cap = BOOKS[book].get("cap")
                if cap:                                     # entry cap: no new F money above cap x equity; never sells early
                    fval = sum(pp["q"] * (px(s) or pp["entry"]) for s, pp in st["pos"].items()); want = min(want, cap * eq - fval)
                    if want < 0.005 * eq: st["pending"].remove(o); L("rejected", id=o["id"], sym=o["sym"], what=f"cap: F positions are {100 * fval / eq:.0f}% of equity (cap {100 * cap:.0f}%)"); continue
                q = int(want // (p * (1 + COST)))
                if q < 1: st["pending"].remove(o); L("rejected", id=o["id"], sym=o["sym"], what=f"unfunded: ${avail:,.0f} available, 7% of equity is ${SIZE * eq:,.0f}, price ${p:,.2f}"); continue
                cost = q * p * (1 + COST); from_cash = min(st["cash"], cost); st["cash"] -= from_cash; need = cost - from_cash
                if need > 0: u = need / (spx * (1 - COST)); st["spy"] -= u; L("spy_sell", units=u, px=spx, usd=need, why=f"funds {o['sym']}")
                st["pos"][o["sym"]] = dict(q=q, entry=p * (1 + COST), opened=today, tgt=o["tgt"], armed=False)
                L("fill", id=o["id"], side="buy", sym=o["sym"], q=q, px=p, usd=cost, **q_at)
            else:
                pp = st["pos"].pop(o["sym"], None)
                if pp:
                    got = pp["q"] * p * (1 - COST); st["cash"] += got; pl = pp["q"] * (p * (1 - COST) - pp["entry"])
                    st["trades"].append(dict(id=o["id"], sym=o["sym"], opened=pp["opened"], closed=today, q=pp["q"], entry=round(pp["entry"], 4), exit=round(p * (1 - COST), 4), pl=round(pl, 2), why=o["why"]))
                    L("fill", id=o["id"], side="sell", sym=o["sym"], q=pp["q"], px=p, usd=got, pl=pl, why=o["why"], **q_at)
            st["pending"].remove(o)
    # 3 idle cash sweeps into SPY
    if st["cash"] > 0 and spx:
        u = st["cash"] * (1 - COST) / spx; st["spy"] += u; L("spy_buy", units=u, px=spx, usd=st["cash"]); st["cash"] = 0.0; st.setdefault("since", today)
    # 4 decisions, once per completed session
    if not market_open and last == today and today not in st["decided"]:
        prev = st["decided"][-1] if st["decided"] else None
        missed = [b[0] for b in sb if prev and prev < b[0] < today]
        if missed: L("risk", what=f"missed {len(missed)} session(s) {missed[0]}..{missed[-1]}: their entries are gone; exits judged at today's close")
        for sym, p in st["pos"].items():
            if any(o["sym"] == sym for o in st["pending"]): continue
            b = bars_fn(sym)
            if not b or b[-1][0] != today: L("hold", sym=sym, what="no close today; exit not judged"); continue
            held = sum(1 for x in b if x[0] > p["opened"])
            if b[-1][2] >= p["tgt"]: p["armed"] = True
            why = None
            if held >= HOLD: why = f"{HOLD} sessions"
            elif BOOKS[book]["exit"] == "C" and p["armed"]:
                e30 = S.ema([x[1] for x in b], 30)[-1]
                if b[-1][1] < e30: why = f"C exit: touched ${p['tgt']:,.2f}, closed ${b[-1][1]:,.2f} under the 30 EMA ${e30:,.2f}"
            if why: st["pending"].append(dict(id=f"{today}-{sym}-sell", side="sell", sym=sym, why=why, made=today)); L("order", id=f"{today}-{sym}-sell", side="sell", sym=sym, px=b[-1][1], why=why)
        seen = set(); avail = st["cash"] + st["spy"] * spx * (1 - COST)
        for r in fires:
            sym = r["sym"]
            if sym in seen or sym in st["pos"] or any(o["sym"] == sym for o in st["pending"]): continue   # held, queued or listed twice: one order at most
            seen.add(sym)
            if avail < r["px"] * (1 + COST): L("skipped", sym=sym, what=f"unfunded: ${avail:,.0f} in SPY and cash, price ${r['px']:,.2f}; opportunity recorded, no order"); continue
            st["pending"].append(dict(id=f"{today}-{sym}-buy", side="buy", sym=sym, tgt=r["tgt"], made=today))
            L("order", id=f"{today}-{sym}-buy", side="buy", sym=sym, px=r["px"], tgt=r["tgt"], why="Setup F fire; 7% of equity at the next open")
        st["decided"].append(today)
    eq = equity(st, px); st["curve"] = [x for x in st["curve"] if x[0] != today] + [[today, round(eq, 2)]]
    return ev


def reconcile(st, book):
    """Replay the append-only ledger from $100,000; it must land on the state."""
    cash = START; spy = 0.0; pos = {}
    for e in lines(book):
        k = e["kind"]
        if k == "spy_buy": cash -= e["usd"]; spy += e["units"]
        elif k == "spy_sell": cash += e["usd"]; spy -= e["units"]
        elif k == "fill" and e["side"] == "buy": cash -= e["usd"]; pos[e["sym"]] = pos.get(e["sym"], 0) + e["q"]
        elif k == "fill": cash += e["usd"]; pos.pop(e["sym"], None)
        elif k == "dividend": cash += e["amt"]
        elif k == "split": pos[e["sym"]] = e["q"]
    got = dict(cash=cash, spy=spy, pos=pos); have = dict(cash=st["cash"], spy=st["spy"], pos={s: p["q"] for s, p in st["pos"].items()})
    return abs(cash - st["cash"]) < 0.01 and abs(spy - st["spy"]) < 1e-6 and pos == have["pos"], got, have


_act = {}
def actions(sym):
    if sym not in _act:
        try:
            r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d&events=div,splits")["chart"]["result"][0].get("events", {})
            day = lambda v: str(datetime.date.fromtimestamp(v["date"]))
            _act[sym] = dict(div={day(v): v["amount"] for v in r.get("dividends", {}).values()}, split={day(v): v["numerator"] / v["denominator"] for v in r.get("splits", {}).values()})
        except Exception: _act[sym] = {}
    return _act[sym]


def text(book, st, ev, px):
    show = [e for e in ev if e["kind"] in NOTIFY]
    if not show: return None
    out = []
    for e in show:
        if e["kind"] == "order": out.append(f"ORDER {e['side'].upper()} {e['sym']} at the next open · {e['why']}")
        elif e["kind"] == "fill": out.append(f"{'BOUGHT' if e['side'] == 'buy' else 'SOLD'} {e['q']} {e['sym']} @ ${e['px']:,.2f} = ${e['usd']:,.0f}" + (f" · P&L ${e['pl']:+,.0f} · {e['why']}" if e["side"] == "sell" else " · funded from SPY"))
        elif e["kind"] == "rejected": out.append(f"REJECTED {e['sym']}: {e['what']}")
        elif e["kind"] == "split": out.append(f"SPLIT {e['sym']} {e['ratio']:g}:1 → {e['q']} shares")
        else: out.append(f"⚠️ {e['what']}")
    tag = "🤖 F V1 · paper book" if book == "f" else "🧪 SHADOW C · paper only, not a signal"
    return f"<b>{tag}</b>\n" + "\n".join(out) + f"\n<i>equity ${equity(st, px):,.0f} · {len(st['pos'])} held · recorded, not advice</i>"


def compare_text(books, px):
    out = ["<b>📒 PAPER BOOKS · weekly</b>"]
    for b, st in books.items():
        eq = equity(st, px); peak = max([v for _, v in st["curve"]] + [START])
        out.append(f"{BOOKS[b]['name']}: <b>${eq:,.0f}</b> ({100 * (eq / START - 1):+.1f}%) · drawdown {100 * (eq / peak - 1):.0f}% · {len(st['pos'])} held · exposure {100 * (1 - (st['cash'] + st['spy'] * (px('SPY') or 0)) / eq):.0f}% · {len(st['trades'])} closed")
    f, c = books["f"], books["c"]
    out.append(f"only in Shadow C: {', '.join(sorted(set(c['pos']) - set(f['pos']))) or 'none'} · only in F V1: {', '.join(sorted(set(f['pos']) - set(c['pos']))) or 'none'}")
    return "\n".join(out) + "\n<i>same entries, two exits; paper only; thesis/f-audit.md</i>"


def snapshot(book, st, px, bars_fn):
    eq = equity(st, px); peak = max([v for _, v in st["curve"]] + [START]); spx = px("SPY") or 0
    pos = []
    for s, p in st["pos"].items():
        now = px(s) or p["entry"]; b = bars_fn(s)
        pos.append(dict(sym=s, q=p["q"], entry=round(p["entry"], 2), now=round(now, 2), opened=p["opened"], tgt=round(p["tgt"], 2), armed=p["armed"], pl=round(p["q"] * (now - p["entry"]), 2), ret=round(now / p["entry"] - 1, 4), sessions=sum(1 for x in b if x[0] > p["opened"]), value=round(p["q"] * now, 2)))
    return dict(name=BOOKS[book]["name"], exit=BOOKS[book]["exit"], equity=round(eq, 2), ret=round(eq / START - 1, 4), dd=round(eq / peak - 1, 4), spy_value=round(st["spy"] * spx, 2), spy_units=round(st["spy"], 4), cash=round(st["cash"], 2),
                exposure=round(sum(x["value"] for x in pos) / eq, 4), realized=round(sum(t["pl"] for t in st["trades"]), 2), unrealized=round(sum(x["pl"] for x in pos), 2),
                positions=pos, pending=st["pending"], trades=st["trades"][-60:], events=st["events"][-40:], curve=st["curve"], last_decision=(st["decided"] or [None])[-1], since=st.get("since"))


MY = lambda: os.path.join(DIR, "my.json")
def my_load():
    return json.load(open(MY())) if os.path.exists(MY()) else dict(equity=S.PLAN["balance"], cash=8142.35, as_of=S.PLAN["balance_as_of"], holdings={}, actions=[])


def my_save(m): os.makedirs(DIR, exist_ok=True); json.dump(m, open(MY(), "w"), indent=1)


def my_actions(m, ev, today):
    """The F V1 book's orders, sized for the REAL account off its own equity and cash. Nothing is assumed done;
    the real holdings change only when Arrington says `done`. A model sell of something never bought is no action."""
    out = []
    for e in ev:
        if e["kind"] != "order" or any(a["id"] == e["id"] for a in m["actions"]): continue
        if e["side"] == "buy":
            q = int(min(SIZE * m["equity"], m["cash"]) // e["px"])
            a = dict(id=e["id"], date=today, side="buy", sym=e["sym"], q=q, px=round(e["px"], 2), usd=round(q * e["px"], 2), why=e["why"], status="proposed" if q else "unfunded")
        else:
            h = m["holdings"].get(e["sym"])
            if not h: continue
            a = dict(id=e["id"], date=today, side="sell", sym=e["sym"], q=h["q"], px=round(e["px"], 2), usd=round(h["q"] * e["px"], 2), why=e["why"], status="proposed")
        m["actions"].append(a)
        if a["status"] == "proposed": out.append(a)
    return out


def my_cmd(m, verb, sym, px=None, q=None, today=None):
    a = next((a for a in reversed(m["actions"]) if a["sym"] == sym and a["status"] == "proposed"), None)
    if not a: return f"no open action for {sym}"
    if verb == "skip": a["status"] = "skipped"; return f"{sym}: skipped, your holdings unchanged"
    q = q or a["q"]; px = px or a["px"]; a.update(status="done", done_px=px, done_q=q, done_at=today)
    if a["side"] == "buy":
        m["cash"] -= q * px; h = m["holdings"].get(sym, dict(q=0, px=0.0))
        m["holdings"][sym] = dict(q=h["q"] + q, px=round((h["q"] * h["px"] + q * px) / (h["q"] + q), 4), since=today)
    else: m["cash"] += q * px; m["holdings"].pop(sym, None)
    return f"{sym}: {a['side']} {q} @ ${px:,.2f} recorded · cash ${m['cash']:,.0f} · you hold {', '.join(f'{s} {h['q']}' for s, h in m['holdings'].items()) or 'nothing'}"


def my_text(acts, m):
    return ("<b>📋 MY ACTIONS · the model's orders, sized for the real account</b>\n" + "\n".join(f"{a['side'].upper()} {a['q']} {a['sym']} ≈ ${a['usd']:,.0f} · {a['why']}" for a in acts)
            + f"\n<i>your equity ${m['equity']:,.0f}, cash ${m['cash']:,.0f} (as of {m['as_of']}) · reply `done SYM [price] [shares]` or `skip SYM` in #ask-ledger · nothing is assumed done · the Ledger's rules, not advice</i>")


def my_snapshot(m, book="f"):
    status = {}
    for e in lines(book):
        if e["kind"] in ("fill", "rejected") and e.get("id"): status[e["id"]] = f"filled {e['q']} @ ${e['px']:,.2f} {e.get('quote_time', '')[-14:-6]}" if e["kind"] == "fill" else "rejected: " + e["what"]
    return dict(equity=m["equity"], cash=m["cash"], as_of=m["as_of"], holdings=m["holdings"], actions=[dict(a, model=status.get(a["id"], "pending")) for a in m["actions"][-40:]])


def main():
    os.makedirs(DIR, exist_ok=True)
    if os.path.exists(os.path.join(DIR, "KILL")): print("pm: KILL present, nothing done"); return
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("America/Chicago")).strftime("%F")
    d = json.load(open(os.path.join(S.DOCS, "data.json")))
    try: market_open = bool(__import__("autotrade").call("/clock")["is_open"])
    except Exception: market_open = False
    bars_fn = (lambda s: [b for b in S.bars(s) if b[0] < today]) if market_open else S.bars
    px = lambda s: (S.bars(s) or [(0, None)])[-1][1]
    fires = d.get("F", []) if d.get("date") == today else []
    try: ff = json.load(open(os.path.join(S.DOCS, "f_full.json")))
    except Exception: ff = {}
    fires_full = ff.get("F", []) if ff.get("date") == today else []        # the full-S&P book reads only its own scan
    paused = os.path.exists(os.path.join(DIR, "PAUSE")); books = {}; bad = []
    for book in BOOKS:
        st = load(book)
        if BOOKS[book].get("src") == "full" and not any(e.get("kind") == "start" for e in st["events"]):
            st["events"].append(ledger(book, dict(date=today, kind="start", what=f"F FULL S&P book opened with ${START:,.0f} cash: Setup F research rule on the full current S&P 500 (f_full.py); no history inherited")))
        bf = fires_full if BOOKS[book].get("src") == "full" else fires
        if paused:
            if not any(e.get("kind") == "paused" and e["date"] == today for e in st["events"]): st["events"].append(ledger(book, dict(date=today, kind="paused", what="PAUSE file present: no orders, no fills")))
            ev = []
        else: ev = step(st, book, today, bf, px, bars_fn, market_open, actions, quote if market_open else None)
        if book == "f":
            m = my_load(); acts = my_actions(m, ev, today); my_save(m)
            if acts: S.notify(my_text(acts, m), "trades")
        ok, got, have = reconcile(st, book)
        if not ok: ev.append(ledger(book, dict(date=today, kind="risk", what=f"RECONCILE FAILED: ledger {json.dumps(got)} vs state {json.dumps(have)}"))); st["events"] = (st["events"] + [ev[-1]])[-60:]; bad.append(book)
        save(book, st); books[book] = st
        t = text(book, st, ev, px)
        if t: S.notify(t, "paper")
        print(f"pm {BOOKS[book]['name']}: equity ${equity(st, px):,.0f} · {len(st['pos'])} held · {len(st['pending'])} pending · {len(st['trades'])} closed · reconcile {'ok' if ok else 'FAILED'}")
    wk = datetime.date.today().isocalendar()[1]; mark = os.path.join(DIR, "weekly")
    if datetime.date.today().weekday() == 5 and (not os.path.exists(mark) or open(mark).read() != str(wk)):
        S.notify(compare_text(books, px), "paper"); open(mark, "w").write(str(wk))
    json.dump(dict(generated=today, market_open=market_open, paused=paused, reconcile_failed=bad, books={b: snapshot(b, st, px, bars_fn) for b, st in books.items()}, my=my_snapshot(my_load())), open(os.path.join(S.DOCS, "pm.json"), "w"), indent=1)


def selftest():
    import tempfile; global DIR, HOLD; DIR = tempfile.mkdtemp()
    days = [f"2026-11-{i:02d}" for i in (2, 3, 4, 5, 6, 9, 10, 11)]                       # 11-07/08 weekend; 11-26 style holiday tested by skipping 11-05
    sess = [d for d in days if d != "2026-11-05"]                                             # 11-05 is a holiday: no bar
    SPY = {d: 600 + i for i, d in enumerate(sess)}
    X = {"2026-11-02": (100, 101, 99, 100), "2026-11-03": (108, 112, 107, 110), "2026-11-04": (115, 121, 114, 116), "2026-11-06": (118, 122, 117, 120), "2026-11-09": (90, 95, 89, 92), "2026-11-10": (88, 90, 85, 87), "2026-11-11": (86, 88, 84, 85)}
    B = {"SPY": {d: (v, v + 1, v - 1, v) for d, v in SPY.items()}, "X": X, "Y": {d: (50, 51, 49, 50) for d in sess}}
    def bars_to(d, inclusive):
        return lambda s: [(k, v[0], v[1], v[2], v[3], 0) for k, v in sorted(B[s].items()) if (k <= d if inclusive else k < d)]
    def day(st, book, d, fires=(), acts=lambda s: {}):
        """morning fill at the open, then the close decision"""
        ev = []
        if d in B["SPY"]:
            ev += step(st, book, d, [], lambda s: B[s][d][3] if d in B[s] else None, bars_to(d, False), True, acts)
            ev += step(st, book, d, list(fires), lambda s: B[s][d][0] if d in B[s] else None, bars_to(d, True), False, acts)
        else: ev += step(st, book, d, list(fires), lambda s: B[s][max(k for k in B[s] if k < d)][0], bars_to(d, False), False, acts)
        return ev
    fire = dict(sym="X", px=100, tgt=120)
    for book in ("f", "c"):
        st = load(book)
        day(st, book, "2026-11-02", [fire, fire])                                             # duplicate signal in one list
        assert len(st["pending"]) == 1 and st["spy"] > 0 and st["cash"] == 0, "one order; cash swept into SPY"
        ev = step(st, book, "2026-11-02", [fire], lambda s: B[s]["2026-11-02"][0], bars_to("2026-11-02", True), False)
        assert len(st["pending"]) == 1 and st["decided"] == ["2026-11-02"], "the same close twice: still one order"
        save(book, st); st = load(book)                                                      # restart between the close and the open
        day(st, book, "2026-11-03")
        q = int((SIZE * 100_000 * (1 - COST)) // (110 * (1 + COST)))                         # the gap: filled at the 11-03 open 110, not the 100 close
        assert st["pos"]["X"]["q"] in (q, q - 1) and not st["pending"], f"filled at the open: {st['pos']}"
        assert reconcile(st, book)[0], "ledger replays to the state"
        day(st, book, "2026-11-04"); assert st["pos"]["X"]["armed"], "touched the 120 target"
        ev = day(st, book, "2026-11-05"); assert st["decided"][-1] == "2026-11-04" and not st["pending"], "holiday: no decision, no order"
        acts = lambda s: dict(split={"2026-11-06": 2.0}) if s == "X" else dict(div={"2026-11-06": 1.5})
        q0 = st["pos"]["X"]["q"]; u0 = st["spy"]; day(st, book, "2026-11-06", acts=acts)
        assert st["pos"]["X"]["q"] == 2 * q0 and st["pos"]["X"]["tgt"] == 60, "split doubled the shares, halved the target"
        assert st["spy"] > u0 and st["cash"] == 0, "the SPY dividend was reinvested"
        assert reconcile(st, book)[0]
        day(st, book, "2026-11-09", acts=acts)                                                 # 92 close under the 30 EMA after the touch
        if book == "c": assert st["pending"] and st["pending"][0]["side"] == "sell" and "C exit" in st["pending"][0]["why"], "Shadow C sells next open"
        else: assert not st["pending"], "F V1 holds"
        day(st, book, "2026-11-10", acts=acts)
        if book == "c": assert not st["pos"] and abs(st["trades"][0]["pl"] - round(st["trades"][0]["q"] * (87 * (1 - COST) - st["trades"][0]["entry"]), 2)) < 0.01 and st["cash"] == 0, "sold at the 11-10 open, proceeds back in SPY"
        ev = step(st, book, "2026-11-30", [], lambda s: 1, bars_to("2026-11-11", True), False)
        assert ev[0]["kind"] == "risk" and "stale" in ev[0]["what"], "stale data: no orders"
        save(book, st); st = load(book); assert reconcile(st, book)[0], "after restart the ledger still replays to the state"
        # unfunded: a fire the book cannot pay for (a tampered copy, so its ledger no longer matches: that must be caught too)
        import copy; t = copy.deepcopy(st); t["spy"] = 0.01; t["decided"].remove("2026-11-10"); tb = book; DIR_T = DIR; DIR = tempfile.mkdtemp()
        ev = day(t, tb, "2026-11-10", [dict(sym="Y", px=50, tgt=60)], acts=acts)
        assert any(e["kind"] == "skipped" and "unfunded" in e["what"] for e in ev) and not t["pending"], "an unfunded fire is recorded, no order"
        t["pending"].append(dict(id="x-Y-buy", side="buy", sym="Y", tgt=60, made="2026-11-10")); ev = day(t, tb, "2026-11-11", acts=acts)
        assert any(e["kind"] == "rejected" and "unfunded" in e["what"] for e in ev) and "Y" not in t["pos"], "an order the book cannot pay for at the open is rejected with the reason"
        assert not reconcile(t, book)[0], "a state that drifted from its ledger is caught"; DIR = DIR_T
    # the 252-session exit, HOLD shrunk to 3 for the test
    HOLD = 3; st = dict(cash=START, spy=0.0, pos={}, pending=[], trades=[], curve=[], decided=[], acted=[], events=[])
    day(st, "f", "2026-11-02", [fire]); [day(st, "f", d) for d in ("2026-11-03", "2026-11-04", "2026-11-06", "2026-11-09")]
    assert st["pending"] and st["pending"][0]["why"] == "3 sessions", st["pending"]
    day(st, "f", "2026-11-10"); assert not st["pos"] and st["trades"][0]["why"] == "3 sessions"
    # quotes with a timestamp: a stale one holds the order, a fresh one fills and is recorded
    st = dict(cash=START, spy=0.0, pos={}, pending=[], trades=[], curve=[], decided=[], acted=[], events=[])
    day(st, "f", "2026-11-02", [fire]); px2 = lambda s: B[s]["2026-11-03"][3]
    now = datetime.datetime.now(datetime.timezone.utc)
    ev = step(st, "f", "2026-11-03", [], px2, bars_to("2026-11-03", False), True, quote=lambda s: dict(px=px2(s), time="old", age=3600, source="test"))
    assert ev[-1]["kind"] == "hold" and "min old" in ev[-1]["what"] and st["pending"], "stale quote: order kept"
    ev = step(st, "f", "2026-11-03", [], px2, bars_to("2026-11-03", False), True, quote=lambda s: dict(px=px2(s), time=now.isoformat(), age=20, source="test"))
    f = [e for e in ev if e["kind"] == "fill"][0]; assert f["quote_time"] == now.isoformat() and f["age_s"] == 20 and f["source"] == "test", "the fill records its quote and time"
    # my actions: sized off the real account, nothing assumed done, sells only what I hold
    m = dict(equity=12_674.74, cash=8_142.35, as_of="t", holdings={}, actions=[])
    acts = my_actions(m, [dict(kind="order", id="o1", side="buy", sym="X", px=100.0, why="fire")], "2026-11-02")
    assert acts[0]["q"] == int(0.07 * 12_674.74 // 100) == 8 and acts[0]["usd"] == 800 and m["holdings"] == {}, acts
    assert my_actions(m, [dict(kind="order", id="o1", side="buy", sym="X", px=100.0, why="fire")], "2026-11-02") == [], "the same order twice: one action"
    assert my_actions(m, [dict(kind="order", id="o2", side="sell", sym="Q", px=5.0, why="252")], "2026-11-03") == [], "a model sell of something I never bought: no action"
    my_cmd(m, "done", "X", 101.0, None, "2026-11-03"); assert m["holdings"]["X"]["q"] == 8 and abs(m["cash"] - (8_142.35 - 808)) < 0.01, m
    acts = my_actions(m, [dict(kind="order", id="o3", side="sell", sym="X", px=120.0, why="252 sessions")], "2026-11-04"); assert acts[0]["q"] == 8 and acts[0]["usd"] == 960
    assert "skipped" in my_cmd(m, "skip", "X", today="2026-11-05") and m["holdings"]["X"]["q"] == 8, "skip leaves the holdings alone"
    assert my_actions(m, [dict(kind="order", id="o4", side="buy", sym="Z", px=9_000.0, why="fire")], "2026-11-05") == [] and m["actions"][-1]["status"] == "unfunded"
    # entry cap: a book at its 70% ceiling records the fire instead of buying, and buys only the room left
    BOOKS["t"] = dict(name="T", exit="252", cap=0.70)
    st = dict(cash=0.0, spy=100.0, pos={"A": dict(q=100, entry=65.0, opened="2026-11-01", tgt=99, armed=False)}, pending=[], trades=[], curve=[], decided=[], acted=[], events=[])
    pxs = {"SPY": 300.0, "A": 650.0, "B": 10.0}                   # A = 65,000 of 95,000 equity = 68%
    st["pending"].append(dict(id="o-B", side="buy", sym="B", tgt=20, made="2026-11-02"))
    step(st, "t", "2026-11-03", [], lambda s: pxs.get(s), lambda s: [("2026-11-02", 1, 1, 1, 1, 0)], True)
    assert "B" in st["pos"] and st["pos"]["B"]["q"] * 10 <= 0.70 * 95_000 - 65_000 + 10, st["pos"]          # only the room left
    st["pending"].append(dict(id="o-C", side="buy", sym="C", tgt=20, made="2026-11-02")); pxs["C"] = 5.0
    ev = step(st, "t", "2026-11-03", [], lambda s: pxs.get(s), lambda s: [("2026-11-02", 1, 1, 1, 1, 0)], True)
    assert "C" not in st["pos"] and any(e["kind"] == "rejected" and "cap" in e["what"] for e in ev), ev
    del BOOKS["t"]
    print("pm ok")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else ""
    if os.environ.get("PMTEST"): selftest()
    elif a in ("pause", "kill"): os.makedirs(DIR, exist_ok=True); open(os.path.join(DIR, a.upper()), "w").write(datetime.datetime.now().isoformat()); print(a, "set")
    elif a == "resume": [os.remove(os.path.join(DIR, f)) for f in ("PAUSE", "KILL") if os.path.exists(os.path.join(DIR, f))]; print("resumed")
    elif a == "my":
        m = my_load(); v = sys.argv[2] if len(sys.argv) > 2 else "show"; today = datetime.date.today().isoformat()
        if v == "balance": m["equity"] = float(sys.argv[3]); m["cash"] = float(sys.argv[4]) if len(sys.argv) > 4 else m["cash"]; m["as_of"] = today; print("recorded")
        elif v in ("done", "skip"): print(my_cmd(m, v, sys.argv[3].upper(), float(sys.argv[4]) if len(sys.argv) > 4 else None, int(sys.argv[5]) if len(sys.argv) > 5 else None, today))
        else: print(json.dumps(my_snapshot(m), indent=1))
        my_save(m)
    elif a == "status":
        for b in BOOKS: st = load(b); print(BOOKS[b]["name"], json.dumps(dict(cash=round(st["cash"], 2), spy_units=round(st["spy"], 4), held=list(st["pos"]), pending=[o["id"] for o in st["pending"]], closed=len(st["trades"]), last_decision=(st["decided"] or [None])[-1])))
        print("paused" if os.path.exists(os.path.join(DIR, "PAUSE")) else "killed" if os.path.exists(os.path.join(DIR, "KILL")) else "running")
    else: main()
