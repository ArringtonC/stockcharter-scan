#!/usr/bin/env python3
"""DECISION JOURNAL (spec: StockCharter/thesis/decision-journal-spec.md, Revision 1; built 2026-10-22).
Records decisions; never recommends. Three books stay separate: REAL (Schwab / trades.csv acct=real), PAPER (the paper
ledgers), IDEAS (this journal: no balance).
  check <contract>   -> an immutable snapshot  ~/.ledger-journal/decisions/<id>.json  (written once, sha256 kept)
                        + the bot's reply gets ✅ take · ❌ pass · ⏳ wait. No reaction = UNCONFIRMED (never PASS).
  take:/pass:/wait:/reason: <text>   -> reason attached to your latest entry (events file, append-only)
  bought / sold      -> linked to the open entry for that contract; none -> EXECUTED — REASON MISSING (snapshot taken
                        at logging time and labelled so; never backdated)
  Saturday           -> one review message: counts and examples only, linked to the eight audit areas.
Each snapshot has three labelled blocks: live (quotes at that minute), model (Black-Scholes values), assumed.
Self-test: JOURNALTEST=1 python3 journal.py"""
import datetime as dt, hashlib, json, math, os, glob
DIR = os.path.expanduser("~/.ledger-journal"); DEC = os.path.join(DIR, "decisions")
EMO = {"✅": "TAKE", "❌": "PASS", "⏳": "WAIT"}
AREA = {"dte": "5 instrument", "event": "5 instrument", "iv": "5 instrument", "size": "6 sizing", "reason": "— record", "nodecision": "— record", "plan": "7 exit"}


# ---------- pure pieces (tested) ----------
def new_id(now, sym, kind): return f"{now:%Y-%m-%d-%H%M}-{sym}-{kind}"


def write_once(path, obj):
    """Exclusive create: a snapshot can never be overwritten. Returns its sha256."""
    raw = json.dumps(obj, indent=1, sort_keys=True).encode()
    with open(path, "xb") as f: f.write(raw)
    return hashlib.sha256(raw).hexdigest()


def event(eid, kind, **kw):
    os.makedirs(DEC, exist_ok=True)
    with open(os.path.join(DEC, eid + ".events.jsonl"), "a") as f:
        f.write(json.dumps(dict(at=dt.datetime.now().isoformat(timespec="seconds"), kind=kind, **kw)) + "\n")


def events(eid):
    f = os.path.join(DEC, eid + ".events.jsonl")
    return [json.loads(l) for l in open(f)] if os.path.exists(f) else []


def entries():
    out = []
    for f in sorted(glob.glob(os.path.join(DEC, "*.json"))):
        e = json.load(open(f)); e["_events"] = events(e["id"]); out.append(e)
    return out


def status(e):
    """The latest decision; UNCONFIRMED if none. Fills mark it EXECUTED (keeps the earlier decision visible)."""
    d = [x for x in e["_events"] if x["kind"] == "decision"]; dec = d[-1]["decision"] if d else e.get("decision", "UNCONFIRMED")
    filled = any(x["kind"] == "fill" for x in e["_events"])
    return dec + (" · EXECUTED" if filled and dec != "EXECUTED — REASON MISSING" else "")


def decide_from_reactions(users_by_emoji, owner):
    """{emoji: [user ids]} -> the decision the owner picked, or None. If several, the stronger order TAKE > PASS > WAIT."""
    for emo in ("✅", "❌", "⏳"):
        if owner in users_by_emoji.get(emo, []): return EMO[emo]
    return None


def match_entry(es, sym, exp, k, right, days=5, today=None):
    """The newest entry for this contract within `days` that was not PASS."""
    today = today or dt.date.today()
    for e in sorted(es, key=lambda x: x["id"], reverse=True):
        c = e.get("contract") or {}
        if e["sym"] == sym and (exp is None or c.get("exp") == exp) and c.get("k") is not None and abs(c["k"] - k) < 1e-6 and c.get("right") == right \
                and (today - dt.date.fromisoformat(e["id"][:10])).days <= days and not status(e).startswith("PASS"):
            return e
    return None


def flags(snap):
    """Instrument and size flags known at the snapshot; each with its audit area."""
    out = []; c = snap.get("contract") or {}; lv = snap["live"]; md = snap["model"]
    if c.get("dte") is not None and c["dte"] <= 1: out.append(("dte", f"{c['dte']} DTE"))
    if lv.get("earnings_in_window") == "yes": out.append(("event", f"earnings {lv.get('earnings')} inside the window"))
    if md.get("iv") and lv.get("rv60") and md["iv"] > 1.3 * lv["rv60"]: out.append(("iv", f"IV {md['iv']:.0%} vs {lv['rv60']:.0%} realized"))
    if snap.get("portfolio", {}).get("share_equiv_pct", 0) > 50: out.append(("size", f"{snap['portfolio']['share_equiv_pct']:.0f}% of the account in share terms"))
    return out


def review(es, start, end):
    """The Saturday message: counts and examples only."""
    W = [e for e in es if start <= e["id"][:10] <= end]
    if not W: return None
    st = [status(e) for e in W]; cnt = lambda p: sum(s.startswith(p) for s in st)
    fills = [(e, x) for e in W for x in e["_events"] if x["kind"] == "fill" and x.get("side") == "sell"]
    lines = [f"📒 **DECISION REVIEW · {start[5:]} – {end[5:]}**",
             f"Decisions {len(W)} · TAKE {cnt('TAKE')} · PASS {cnt('PASS')} · WAIT {cnt('WAIT')} · UNCONFIRMED {cnt('UNCONFIRMED')} · executed without a decision {cnt('EXECUTED — REASON MISSING')}"]
    if fills: lines.append("Closed: " + " · ".join(f"{e['sym']} {x.get('pl', 0):+,.0f}" for e, x in fills))
    by = {}
    for e in W:
        res = "loss" if any(x["kind"] == "fill" and x.get("side") == "sell" and x.get("pl", 0) < 0 for x in e["_events"]) else \
              "win" if any(x["kind"] == "fill" and x.get("side") == "sell" and x.get("pl", 0) >= 0 for x in e["_events"]) else \
              "pass" if status(e).startswith(("PASS", "WAIT", "UNCONFIRMED")) else "open"
        for k, _ in flags(e): by.setdefault(k, {}).setdefault(res, 0); by[k][res] += 1
    if by: lines.append("Flags at entry (win / loss / pass / open): " + " · ".join(f"{k} {v.get('win', 0)}/{v.get('loss', 0)}/{v.get('pass', 0)}/{v.get('open', 0)} [{AREA[k]}]" for k, v in by.items()))
    miss = [e for e in W if not any(x["kind"] == "reason" for x in e["_events"])]
    if miss: lines.append(f"Missing a reason: {len(miss)} ({', '.join(e['sym'] for e in miss[:6])})")
    ghosts = [e for e in W if status(e).startswith(("PASS", "WAIT", "UNCONFIRMED")) and e.get("ghost")]
    if ghosts: lines.append("Not taken, HYPOTHETICAL value change since the snapshot: " + " · ".join(f"{e['sym']} {e['ghost']:+,.0f}" for e in ghosts))
    rv = [e for e in W if status(e).startswith("EXECUTED — REASON MISSING")]
    if rv: lines.append("Needs your review: " + " · ".join(f"{e['sym']} ({e['id'][:10]})" for e in rv))
    lines.append("_counts and examples only · not a signal · thesis/decision-audit-framework.md_")
    return "\n".join(lines)


# ---------- live capture ----------
def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs(s, k, t, iv, right, r=0.04):
    if t <= 0 or iv <= 0: return max(0.0, (s - k) if right == "C" else (k - s))
    d1 = (math.log(s / k) + (r + iv * iv / 2) * t) / (iv * math.sqrt(t)); d2 = d1 - iv * math.sqrt(t)
    c = s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return c if right == "C" else c - s + k * math.exp(-r * t)


def implied(price, s, k, t, right):
    lo, hi = 0.01, 5.0
    if t <= 0 or price <= max(0.0, (s - k) if right == "C" else (k - s)): return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(s, k, t, mid, right) < price: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def capture(sym, contract, who_label, note="decision-time", now=None):
    """Everything automatic, at this minute. Missing pieces are recorded as missing, never guessed."""
    import scan_daily as S, urllib.request
    now = now or dt.datetime.now(); live, model, missing = {}, {}, []
    def meta(t):
        m = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1d&interval=1m")["chart"]["result"][0]["meta"]
        return m["regularMarketPrice"], m.get("chartPreviousClose") or m.get("previousClose")
    for t, key in ((sym, "px"), ("SPY", "spy"), ("QQQ", "qqq"), ("^VIX", "vix")):
        try: p, prev = meta(t); live[key] = round(p, 2); live[key + "_day"] = round(p / prev - 1, 4) if prev else None
        except Exception: missing.append(key)
    try:
        c = [b[1] for b in S.bars(sym, "1y")]; rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - 60, len(c))]
        live["rv60"] = round(math.sqrt(252) * (sum(x * x for x in rets) / len(rets) - (sum(rets) / len(rets)) ** 2) ** 0.5, 4)
        live["vs_qqq_20d"] = round(c[-1] / c[-21] - 1 - (lambda q: q[-1] / q[-21] - 1)([b[1] for b in S.bars("QQQ", "1y")]), 4)
    except Exception: missing.append("realized vol")
    snap_c = None
    if contract and contract.get("exp") and contract.get("k"):
        exp = contract["exp"]; k = float(contract["k"]); right = contract["right"]
        dte = (dt.date.fromisoformat(exp) - now.date()).days; snap_c = dict(exp=exp, k=k, right=right, dte=dte)
        try:
            o = S.occ(sym, exp, k, pad=False, right=right)
            H = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}
            q = json.load(urllib.request.urlopen(urllib.request.Request(f"https://data.alpaca.markets/v1beta1/options/quotes/latest?symbols={o}&feed=indicative", headers=H), timeout=20))["quotes"][o]
            bid, ask = q.get("bp") or 0, q.get("ap") or 0; mid = (bid + ask) / 2 if ask else None
            live.update(bid=bid, ask=ask, mid=round(mid, 3) if mid else None, spread_pct=round((ask - bid) / mid, 4) if mid else None, quote_time=q.get("t"))
            if mid and live.get("px"):
                T = max(dte, 0.5) / 365; iv = implied(mid, live["px"], k, T, right); model["iv"] = round(iv, 4) if iv else None
                ivx = iv or live.get("rv60") or 0.4; nxt = max(dte - 1, 0.25) / 365
                model["tomorrow"] = {f"{m:+.0%}": round(100 * bs(live["px"] * (1 + m), k, nxt, ivx, right), 0) for m in (-0.10, -0.05, 0, 0.05, 0.10)}
                model["tomorrow_iv_minus20pct"] = round(100 * bs(live["px"], k, nxt, ivx * 0.8, right), 0)
                model["breakeven_at_expiry"] = round(k + mid if right == "C" else k - mid, 2)
        except Exception as ex: missing.append("option quote")
        try:
            from ticket_bot import next_earnings
            ne = next_earnings(sym, now.date(), days=min(max(dte, 1) + 1, 30))
            live["earnings"] = ne.isoformat() if ne else None
            live["earnings_in_window"] = "yes" if ne and ne <= dt.date.fromisoformat(exp) else ("no" if dte <= 29 else "unknown beyond 30 days")
        except Exception: live["earnings_in_window"] = "unknown"; missing.append("earnings date")
    port = {}
    try:
        my = json.load(open(os.path.expanduser("~/.ledger-pm/my.json"))) if os.path.exists(os.path.expanduser("~/.ledger-pm/my.json")) else {}
        import csv
        rows = [r for r in csv.DictReader(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv"))) if r.get("acct") == "real" and r.get("status") == "open"]
        port = dict(equity=my.get("equity"), cash=my.get("cash"), as_of=my.get("as_of"), open_positions=[f"{r['symbol']} {r.get('strike') or ''}{(r.get('kind') or '')[:1].upper()}".strip() for r in rows])
        if snap_c and live.get("mid") and my.get("equity"):
            port["cost_pct"] = round(100 * live["mid"] * 100 / my["equity"], 1)
            port["share_equiv_pct"] = round(100 * 100 * live["px"] * 0.5 / my["equity"], 0)     # ~0.5 delta at the money; labelled MODEL in the reply
    except Exception: missing.append("portfolio")
    return dict(captured_at=now.isoformat(timespec="seconds"), capture=note, who=who_label, sym=sym, contract=snap_c, live=live, model=model,
                assumed=dict(costs="0.65/contract commission, fills at the mid unless logged", delta_for_exposure=0.5), portfolio=port, missing=missing)


def snapshot(sym, contract, who_label, kind="check", note="decision-time", decision="UNCONFIRMED"):
    os.makedirs(DEC, exist_ok=True); now = dt.datetime.now()
    snap = capture(sym, contract, who_label, note, now)
    eid = new_id(now, sym, kind); n = 1
    while os.path.exists(os.path.join(DEC, eid + ".json")): n += 1; eid = new_id(now, sym, kind) + f"-{n}"
    snap.update(id=eid, decision=decision)
    h = write_once(os.path.join(DEC, eid + ".json"), snap); event(eid, "snapshot", sha256=h)
    return snap


def card(snap):
    lv, md, c, p = snap["live"], snap["model"], snap.get("contract"), snap.get("portfolio", {})
    pct = lambda x: f"{x:+.1%}" if isinstance(x, (int, float)) else "—"
    L = [f"📋 **JOURNAL · snapshot {snap['id']}** ({snap['capture']})"]
    L.append(f"LIVE   {snap['sym']} ${lv.get('px', '—')} · SPY {pct(lv.get('spy_day'))} · QQQ {pct(lv.get('qqq_day'))} · VIX {lv.get('vix', '—')} · vs QQQ 20d {pct(lv.get('vs_qqq_20d'))}")
    if c:
        L.append(f"       option {lv.get('bid', '—')} × {lv.get('ask', '—')} (spread {lv.get('spread_pct', 0):.0%}) · {c['dte']} DTE · earnings {lv.get('earnings') or 'none found'} ({lv.get('earnings_in_window', '—')} in window)")
        if md.get("tomorrow"):
            L.append("MODEL  tomorrow at the same IV: " + " · ".join(f"{k} ${v:,.0f}" for k, v in md["tomorrow"].items()) + f" · flat with IV −20%: ${md['tomorrow_iv_minus20pct']:,.0f} · break-even at expiry ${md['breakeven_at_expiry']}")
            L.append(f"       IV {md['iv']:.0%} vs {lv.get('rv60', 0):.0%} realized (60d)" if md.get("iv") else "       IV: could not solve")
    if p.get("equity"): L.append(f"BOOK   REAL equity ${p['equity']:,.0f} · cash ${p.get('cash') or 0:,.0f} ({p.get('as_of')}) · open: {', '.join(p.get('open_positions') or []) or 'none'}" + (f" · this = {p['cost_pct']}% at cost, ~{p['share_equiv_pct']:.0f}% in share terms (MODEL)" if p.get("cost_pct") else ""))
    fl = flags(snap)
    if fl: L.append("FLAGS  " + " · ".join(f"⚠ {t} [{AREA[k]}]" for k, t in fl))
    miss = list(snap["missing"]) + ["your source", "your reason"]
    L.append(f"missing: {', '.join(miss)} · reply `take: …` / `pass: …` / `wait: …`")
    L.append("✅ take · ❌ pass · ⏳ wait  (no reaction = UNCONFIRMED) · _a record, not advice_")
    return "\n".join(L)


# ---------- bot hooks ----------
def on_check(c, who_label):
    """Called after the bot answers a `check`. Returns (snapshot, card text)."""
    con = dict(exp=c.get("exp"), k=c.get("k"), right=c.get("right")) if c.get("exp") and c.get("k") else None
    s = snapshot(c["sym"], con, who_label); return s, card(s)


def remember_message(eid, mid):
    os.makedirs(DIR, exist_ok=True); f = os.path.join(DIR, "pending.json")
    p = json.load(open(f)) if os.path.exists(f) else {}; p[eid] = dict(mid=mid, since=dt.date.today().isoformat()); json.dump(p, open(f, "w"))


def poll_reactions(api, ch, owner):
    """Reads ✅ ❌ ⏳ on pending snapshot replies (2 days), records the owner's choice once."""
    f = os.path.join(DIR, "pending.json")
    if not os.path.exists(f): return
    p = json.load(open(f)); keep = {}
    for eid, v in p.items():
        if (dt.date.today() - dt.date.fromisoformat(v["since"])).days > 2: continue
        users = {}
        for emo in EMO:
            try: users[emo] = [u["id"] for u in api(f"/channels/{ch}/messages/{v['mid']}/reactions/{urllib_quote(emo)}?limit=10")]
            except Exception: users[emo] = []
        d = decide_from_reactions(users, owner)
        if d: event(eid, "decision", decision=d, via="reaction")
        else: keep[eid] = v
    json.dump(keep, open(f, "w"))


def urllib_quote(s):
    import urllib.parse; return urllib.parse.quote(s)


def on_reason(word, text, who_label):
    es = [e for e in entries() if e.get("who") == who_label]
    if not es: return "No journal entry yet — `check` a contract first."
    e = es[-1]
    if word in ("take", "pass", "wait"): event(e["id"], "decision", decision=word.upper(), via="text")
    event(e["id"], "reason", text=text[:500])
    return f"📒 noted on {e['id']}: {word.upper() if word != 'reason' else 'reason'} — {text[:120]}"


def on_fill(side, c, price, who_label, pl=None):
    """`bought` / `sold`: link to the entry, or create EXECUTED — REASON MISSING (snapshot taken now, labelled)."""
    e = match_entry(entries(), c["sym"], c.get("exp"), c["k"], c["right"]) if c.get("k") else None
    if not e and side == "buy":
        con = dict(exp=c.get("exp"), k=c.get("k"), right=c.get("right")) if c.get("exp") else None
        e = snapshot(c["sym"], con, who_label, kind="fill", note="taken at fill time, not decision time", decision="EXECUTED — REASON MISSING")
    if e and side == "sell" and pl is None:
        buys = [x for x in events(e["id"]) if x["kind"] == "fill" and x.get("side") == "buy"]
        if buys: pl = round((price - buys[-1]["price"]) * 100 * c.get("n", 1), 2)
    if e: event(e["id"], "fill", side=side, price=price, pl=pl)
    return e


def weekly(say, today=None):
    today = today or dt.date.today()
    if today.weekday() != 5: return
    f = os.path.join(DIR, "weekly.json"); last = json.load(open(f)).get("last") if os.path.exists(f) else None
    if last == today.isoformat(): return
    start = (today - dt.timedelta(days=6)).isoformat()
    es = entries()
    for e in es:   # hypothetical value change for ideas not taken, from the snapshot's own price
        if e.get("contract") and status(e).startswith(("PASS", "WAIT", "UNCONFIRMED")) and e["live"].get("mid"):
            try:
                import scan_daily as S; now = S.bars(e["sym"])[-1][1]; c = e["contract"]; T = max((dt.date.fromisoformat(c["exp"]) - today).days, 0) / 365
                e["ghost"] = 100 * (bs(now, c["k"], T, e["model"].get("iv") or e["live"].get("rv60") or 0.4, c["right"]) - e["live"]["mid"])
            except Exception: pass
    t = review(es, start, today.isoformat())
    if t: say(t)
    os.makedirs(DIR, exist_ok=True); json.dump(dict(last=today.isoformat()), open(f, "w"))


if __name__ == "__main__":
    if os.environ.get("JOURNALTEST"):
        import tempfile; DIR = tempfile.mkdtemp(); DEC = os.path.join(DIR, "decisions"); os.makedirs(DEC)
        s = dict(id="2026-10-20-0941-NFLX-check", sym="NFLX", who="owner", decision="UNCONFIRMED", capture="decision-time",
                 contract=dict(exp="2026-12-18", k=69.0, right="C", dte=59), live=dict(px=69.2, mid=4.75, rv60=0.33, earnings="2026-10-20", earnings_in_window="yes"),
                 model=dict(iv=0.40), portfolio=dict(share_equiv_pct=24), missing=[])
        write_once(os.path.join(DEC, s["id"] + ".json"), s)
        try: write_once(os.path.join(DEC, s["id"] + ".json"), s); raise SystemExit("snapshot was overwritten")
        except FileExistsError: pass
        assert status(entries()[0]) == "UNCONFIRMED"
        assert decide_from_reactions({"❌": ["me"], "⏳": ["me"]}, "me") == "PASS" and decide_from_reactions({"✅": ["x"]}, "me") is None
        event(s["id"], "decision", decision="PASS"); assert status(entries()[0]) == "PASS"
        assert {k for k, _ in flags(s)} == {"event"}, flags(s)                         # IV 0.40 vs 0.33 is under 1.3x
        assert match_entry(entries(), "NFLX", "2026-12-18", 69, "C", today=dt.date(2026, 10, 21)) is None   # a PASS is not linked
        s2 = dict(s, id="2026-10-21-0935-SPY-fill", sym="SPY", decision="EXECUTED — REASON MISSING", contract=dict(exp="2026-10-21", k=770.0, right="P", dte=0),
                  live=dict(px=772, mid=0.52), model={}, portfolio={})
        write_once(os.path.join(DEC, s2["id"] + ".json"), s2); event(s2["id"], "fill", side="buy", price=0.52); event(s2["id"], "fill", side="sell", price=0.10, pl=-42)
        e = match_entry(entries(), "SPY", "2026-10-21", 770, "P", today=dt.date(2026, 10, 21)); assert e and e["id"] == s2["id"]
        t = review(entries(), "2026-10-17", "2026-10-23"); print(t)
        assert "PASS 1" in t and "executed without a decision 1" in t and "dte 0/1/0/0" in t and "event 0/0/1/0" in t and "SPY" in t
        print("journal ok")
