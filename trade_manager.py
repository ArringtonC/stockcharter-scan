#!/usr/bin/env python3
"""Trade manager (2026-10-12): Arrington texts the Ledger bot in Discord #ask-ledger; it checks, logs,
watches and journals his REAL trades. It never places an order -- every order is his click.

  check AMZN 10/23 265C        -> the pre-trade check: Ledger signal? cost vs the 7% cap, breakeven,
                                  earnings before expiry, and the bracket to enter (sell at 2x / stop at -50%)
  bought AMZN 10/23 265C 3.18  -> logs it in trades.csv (acct real) and starts watching it
  sold AMZN 265C 4.90          -> closes it, journals the result and whether the plan was followed
  trades                       -> open real positions with live prices
  anything else                -> the help text
Watching: every run, each open real option trade is priced; one alert at 2x (take profit; 2026-10-26: 56k-trade
tests found selling at 2x beat +50%, 3x, 4x, runners and trailing stops) and one at -50% (stop). Only messages from the server owner are acted on.
Runs every minute (launchd com.arrington.ledger-tm). Self-test: TMTEST=1 python3 trade_manager.py"""
import csv, datetime, json, os, re, time, urllib.request
import journal as J
CH, OWNER = "1559302594189328544", "468902102995959808"
# who may use it: the owner's trades go to trades.csv (public repo); a friend's journal stays on this Mac
FRIENDS = {"1288838148436136057": "jcbreeze"}   # 2026-10-12: sized as if it were Arrington's account, his request
STATE = os.path.expanduser("~/.ledger-tm.json")
H = os.path.dirname(os.path.abspath(__file__))
YEAR = datetime.date.today().year
HELP = ("**I'm your trade manager (Ledger rules v1.0).** Text me in #ask-ledger:\n`check META calls` · `check BE 10/16 272.50 call` · `done ZS 214.10 2` / `skip ZS` / `my balance 12674 cash 8142` (My Actions) · `take: …` / `pass: …` / `wait: …` (journal) · "
        "`bought AMZN 10/23 265C 3.18` · `sold AMZN 265C 4.90` · `trades` · `pin META` · `unpin META` · `charts` · `report`\n"
        "I answer WAIT, NOW, HOLD, OUT, MISSED, NO TRADE or DATA STALE. I log, watch and journal. You place every order.")


def api(path, body=None, method=None):
    rq = urllib.request.Request("https://discord.com/api/v10" + path, data=json.dumps(body).encode() if body else None,
                                method=method or ("POST" if body else "GET"),
                                headers={"Authorization": f"Bot {os.environ['DISCORD_LEDGER_BOT_TOKEN']}",
                                         "Content-Type": "application/json", "User-Agent": "DiscordBot (ledger, 1)"})
    b = urllib.request.urlopen(rq, timeout=20).read()
    return json.loads(b) if b else {}


def say(text): return api(f"/channels/{CH}/messages", {"content": text[:1990]})


CONTRACT = r"(?P<sym>[A-Za-z]{1,5})\s+(?:(?P<mm>\d{1,2})/(?P<dd>\d{1,2})(?:/(?P<yy>\d{2,4}))?\s+)?\$?(?P<k>\d+(?:\.\d+)?)\s*(?P<r>calls?|puts?|c|p)\b"


def parse(text):
    """Pure: (verb, dict) from a message, or ("help", {})."""
    t = text.strip()
    if re.match(r"^(trades|positions|how are my trades)\b", t, re.I): return "trades", {}
    m = re.match(r"^(take|pass|wait|reason)\s*:\s*(.+)$", t, re.I | re.S)                     # decision journal
    if m: return "journal", dict(word=m.group(1).lower(), text=m.group(2).strip())
    m = re.match(r"^(done|skip)\s+([A-Za-z]{1,5})(?:\s+\$?(\d+(?:\.\d+)?))?(?:\s+x?(\d+))?\s*$", t, re.I)       # my actions: "done ZS 214.10 2"
    if m: return m.group(1).lower(), dict(sym=m.group(2).upper(), px=float(m.group(3)) if m.group(3) else None, n=int(m.group(4)) if m.group(4) else None)
    m = re.match(r"^my\s+balance\s+\$?(\d[\d,]*(?:\.\d+)?)(?:\s+cash\s+\$?(\d[\d,]*(?:\.\d+)?))?", t, re.I)
    if m: return "mybal", dict(bal=float(m.group(1).replace(",", "")), cash=float(m.group(2).replace(",", "")) if m.group(2) else None)
    m = re.match(r"^(pin|unpin)\s+([A-Za-z]{1,5})\b", t, re.I)
    if m: return m.group(1).lower(), dict(sym=m.group(2).upper())
    if re.match(r"^(charts|report)\b", t, re.I): return t.split()[0].lower(), {}
    m = re.match(r"^(check|details)\s+([A-Za-z]{1,5})(?:\s+(calls?|puts?))?\s*$", t, re.I)    # a rough idea: "check META calls"
    if m: return "check", dict(sym=m.group(2).upper(), exp=None, k=None, right=(m.group(3) or "x")[0].upper().replace("X", "") or None, px=None, n=1)
    m = re.match(r"^(check|details|bought|bto|buy|sold|stc|sell)\s+" + CONTRACT + r"(?:\s+\$?(?P<px>\d+(?:\.\d+)?))?(?:\s+x?(?P<n>\d+))?", t, re.I)
    if not m: return "help", {}
    v = m.group(1).lower(); v = {"bto": "bought", "buy": "bought", "stc": "sold", "sell": "sold"}.get(v, v)
    exp = None
    if m["mm"]:
        y = int(m["yy"]) if m["yy"] else YEAR; y = y + 2000 if y < 100 else y
        exp = datetime.date(y, int(m["mm"]), int(m["dd"])).isoformat()
    return v, dict(sym=m["sym"].upper(), exp=exp, k=float(m["k"]), right=m["r"][0].upper(),
                   px=float(m["px"]) if m["px"] else None, n=int(m["n"]) if m["n"] else 1)


def bracket(px):
    """Pure: the exits to enter with the buy. Take profit at 2x, stop at -50% (2026-10-26: thesis/exits/runner-results.md;
    +50% made about half as much on winners). lock = the 2x level, kept for long-dated thesis calls."""
    return dict(tp=round(px * 2, 2), sl=round(px * 0.5, 2), lock=round(px * 2, 2))


def path(who=OWNER):
    return os.path.join(H, "trades.csv") if who == OWNER else os.path.expanduser(f"~/.ledger-friend-{FRIENDS[who]}.csv")


def rows(who=OWNER):
    p = path(who)
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []


def save(rs, who=OWNER):
    cols = list(csv.DictReader(open(os.path.join(H, "trades.csv"))).fieldnames)
    with open(path(who), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rs)


def signal(sym):
    """What Ledger currently says about a symbol: Setup F, a pattern, a fired level."""
    d = json.load(open(os.path.join(H, "docs", "data.json"))); out = []
    if any(r["sym"] == sym for r in d.get("F", [])): out.append("Setup F")
    if any(x["sym"] == sym for x in d.get("ihs", [])): out.append("pattern #7")
    if any(b["sym"] == sym and b.get("forming") for b in d.get("blocked", [])): out.append("F forming")
    for x in json.load(open(os.path.join(H, "docs", "levels.json"))):
        f = x.get("fired") or {}
        if x["symbol"] == sym and f.get("way") in ("up", "down") and str(f.get("at", ""))[:10] >= (datetime.date.today() - datetime.timedelta(days=3)).isoformat():
            out.append(f"level broke {f['way']} {str(f['at'])[5:10]}")
    pats = [p["kind"] for p in d.get("charts", {}).get(sym, {}).get("pat", []) if p["kind"] != "level"]
    return out, pats[-3:]


def gate(sym, dte, cost, cap, sig, earn_before, spread):
    """Pure: APPROVED / WAIT / PASS and why (2026-10-12, replayed on his 35 trades: PASS held 31 of them,
    -$859; the 4 let through made +$603 with a +50/-50 bracket at strict fills). Three playbooks:
      long-dated (60+ days)  thesis trades: allowed at half the cap without a signal, 2x-lock exits
      QQQ/SPY                only on a pattern #7 alert
      everything else        needs Setup F or pattern #7; 3-60 days; under the cap; no earnings inside;
                             Setup F with 0-2 days = WAIT (3 BE trades, untested as a rule)
    A level break (his own chart note) is untested: WAIT."""
    tested = [x for x in sig if x in ("Setup F", "pattern #7")]
    if cost > cap: return "PASS", f"${cost:,.0f} is over the 7% cap (${cap:,.0f})"
    if dte > 60: return ("APPROVED", "long-dated") if tested or cost <= cap / 2 else ("WAIT", "long-dated with no signal: half size or less")
    if sym in ("QQQ", "SPY"): return ("APPROVED", "pattern #7 on the index") if "pattern #7" in tested else ("PASS", "QQQ/SPY without a pattern #7 alert (your record: -$463 on 11)")
    if not tested: return ("WAIT", f"only {', '.join(sig)} -- not a tested entry yet") if sig else ("PASS", "no Ledger signal (your record: -$859 on 31)")
    if earn_before: return "WAIT", "earnings land before expiry"
    if dte <= 2: return "WAIT", "Setup F with 0-2 days left -- 3 wins, all BE, not yet a rule; paper it"
    if spread > 0.15: return "WAIT", "spread over 15% -- wait for a tighter quote"
    return "APPROVED", f"{', '.join(tested)} · {dte} days · inside the cap"


def post(text, png=None):
    """A message, with the chart attached under it when there is one (multipart, bot token)."""
    if not png: return say(text)
    k = "ledgerTM9f3b"
    body = (f"--{k}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\nContent-Type: application/json\r\n\r\n"
            + json.dumps({"content": text[:1990]}) + f"\r\n--{k}\r\nContent-Disposition: form-data; name=\"files[0]\"; "
            "filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{k}--\r\n".encode()
    rq = urllib.request.Request(f"https://discord.com/api/v10/channels/{CH}/messages", data=body, method="POST",
                                headers={"Authorization": f"Bot {os.environ['DISCORD_LEDGER_BOT_TOKEN']}",
                                         "Content-Type": f"multipart/form-data; boundary={k}", "User-Agent": "DiscordBot (ledger, 1)"})
    return json.loads(urllib.request.urlopen(rq, timeout=30).read() or b"{}")


def bought(c, rs, who=OWNER):
    if not (c["exp"] and c["px"]): return "Format: `bought AMZN 10/23 265C 3.18`"
    today = datetime.date.today().isoformat(); kind = "call" if c["right"] == "C" else "put"
    rid = f"{today}-{c['sym']}-{c['k']:g}{c['right'].lower()}-real"
    while any(r["id"] == rid for r in rs): rid += "b"
    br = bracket(c["px"]); sig, _ = signal(c["sym"])
    rs.append(dict(id=rid, kind=kind, acct="real" if who == OWNER else "friend-" + FRIENDS[who], opened=today, symbol=c["sym"], setup=", ".join(sig) or "none", entry=f"{c['px']:.2f}",
                   target="", strike=f"{c['k']:g}", expiry=c["exp"], contracts=str(c["n"]), status="open", closed="", exit="", pl_pct="",
                   note=f"REAL. Logged from Discord #ask-ledger. Bracket: take profit {br['tp']}, stop {br['sl']}; 2x {br['lock']} locks the gain."))
    save(rs, who)
    return (f"✅ **LOGGED · {c['sym']} {c['exp'][5:]} ${c['k']:g}{c['right']} × {c['n']} @ ${c['px']:.2f}** (${c['px'] * 100 * c['n']:,.0f})\n"
            f"Enter now in Schwab: take-profit limit **${br['tp']}** + stop **${br['sl']}** (OCO)\n"
            f"Sell at 2x: put the take-profit in now. I'll text you at 2x and at -50%. Signal: {', '.join(sig) if sig else 'none ⚠️'}")


def sold(c, rs, who=OWNER):
    if not c["px"]: return "Format: `sold AMZN 265C 4.90`"
    m = [r for r in rs if r["acct"] in ("real", "friend-" + FRIENDS.get(who, "")) and r["status"] == "open" and r["symbol"] == c["sym"]
         and abs(float(r.get("strike") or 0) - c["k"]) < 1e-6 and (r["kind"] == "call") == (c["right"] == "C")]
    if not m: return f"I can't find an open {c['sym']} ${c['k']:g}{c['right']} -- `trades` lists what I have."
    r = m[0]; e = float(r["entry"]); n = int(r["contracts"] or 1); pl = (c["px"] - e) * 100 * n; br = bracket(e)
    plan = ("took profit at the bracket ✅" if c["px"] >= br["tp"] * 0.97 else "stopped at the bracket ✅" if c["px"] <= br["sl"] * 1.03
            else "sold between the bracket levels -- by plan, or a feeling?")
    r.update(status="closed", closed=datetime.date.today().isoformat(), exit=f"{c['px']:.2f}", pl_pct=f"{100 * (c['px'] / e - 1):.1f}",
             note=r["note"] + f" Closed from Discord at {c['px']:.2f}: {plan}")
    save(rs, who)
    return f"📒 **CLOSED · {r['symbol']} ${c['k']:g}{c['right']}** ${e:.2f} → ${c['px']:.2f} · **{'+' if pl >= 0 else '−'}${abs(pl):,.0f}** ({100 * (c['px'] / e - 1):+.0f}%)\nPlan: {plan}"


def trades(rs, who=OWNER):
    from ticket_bot import quote
    L = ["**OPEN REAL TRADES**" if who == OWNER else "**YOUR OPEN TRADES**"]
    for r in [r for r in rs if r["acct"] in ("real", "friend-" + FRIENDS.get(who, "")) and r["status"] == "open"]:
        if r["kind"] in ("call", "put") and r.get("expiry"):
            try: b, a = quote(r["symbol"], r["expiry"], float(r["strike"]), "P" if r["kind"] == "put" else "C")
            except Exception: b = a = 0
            m = (a + b) / 2 if a else None; e = float(r["entry"]); n = int(r["contracts"] or 1)
            L.append(f"{r['symbol']} {r['expiry'][5:]} ${float(r['strike']):g}{r['kind'][0].upper()} ×{n} · ${e:.2f} → " +
                     (f"${m:.2f} · {'+' if m >= e else '−'}${abs(m - e) * 100 * n:,.0f} ({100 * (m / e - 1):+.0f}%)" if m else "no quote"))
        else: L.append(f"{r['symbol']} {r.get('contracts') or 1} share(s) @ ${float(r['entry']):.2f}")
    return "\n".join(L)


OPEN_RISK_PCT = 0.05     # alert when the planned loss (half the premium) on open real options passes 5% of the account


def open_risk(rs, balance):
    """Pure. Planned loss on open real options (the -50% stop on each), and its share of the account."""
    pl = sum(0.5 * float(r["entry"]) * 100 * int(float(r["contracts"] or 1)) for r in rs
             if r["acct"] == "real" and r["status"] == "open" and r["kind"] in ("call", "put"))
    return pl, (pl / balance if balance else 0.0)


def watch(rs, st, tag=""):
    """One text per event per trade: 2x (take profit) and -50% (stop). Calls and puts (puts were unwatched until 2026-10-22), and the
    hand-logged paper books (small, big), labelled PAPER. For the owner, one text a day when open option risk passes the cap."""
    from ticket_bot import quote
    for r in [r for r in rs if (r["acct"] in ("real", "small", "big") or r["acct"].startswith("friend-")) and r["status"] == "open"
              and r["kind"] in ("call", "put") and r.get("expiry")]:
        rt = "P" if r["kind"] == "put" else "C"
        try: b, a = quote(r["symbol"], r["expiry"], float(r["strike"]), rt)
        except Exception: continue
        if not b: continue
        e = float(r["entry"]); br = bracket(e); nm = f"{r['symbol']} ${float(r['strike']):g}{rt}" + (" · PAPER" if r["acct"] in ("small", "big") else "")
        far = (datetime.date.fromisoformat(r["expiry"]) - datetime.date.today()).days > 60   # long-dated (SG, SNAP 2028): 2x lock only
        for key, hit, msg in (("tp", b >= br["tp"], f"🎯 **{nm} at 2x** · bid ${b:.2f} (you paid ${e:.2f}) -- your take-profit level: the tested exit is to sell here"),
                              ("sl", b <= br["sl"], f"🛑 **{nm} at -50%** · bid ${b:.2f} -- your stop level"),
                              ("2x", b >= br["lock"], f"🚀 **{nm} at 2x** · bid ${b:.2f} -- set a stop at ${max(1.5 * e, 0.75 * b):.2f} to lock the gain")):
            k = f"{r['id']}:{key}"
            if far and key == "tp": continue
            if not far and key == "2x": continue                                     # 2x is the take-profit now: one text, not two
            if hit and not st.get(k): st[k] = datetime.date.today().isoformat(); say(tag + msg)
    if not tag:
        import scan_daily as S
        pl, pct = open_risk(rs, S.PLAN.get("balance") or 0)
        k = f"openrisk:{datetime.date.today().isoformat()}"
        if pct > OPEN_RISK_PCT and not st.get(k):
            st[k] = 1; say(f"⚠️ **Open option risk ${pl:,.0f}** = {100 * pct:.1f}% of the account (cap {100 * OPEN_RISK_PCT:.0f}%) "
                           f"if every option hits its -50% stop. Not advice: the Ledger's risk line.")


def pins(sym, on):
    import ledger_v1 as V
    s1 = V.load_state(); r = V.pin(sym, s1, on); V.save_state(s1); return r


def charts():
    import ledger_v1 as V
    s1 = V.load_state()
    return "\n".join([f"**WORKSPACE** ({len(s1.get('plans', {}))}/7 · pinned {', '.join(s1.get('pins', [])) or 'none'})"] +
                     [f"{s}: {(s1.get('states', {}).get(s) or {}).get('state', 'WAIT' if p.get('complete') else 'PREP NEEDED')}"
                      + (f" · {p['m15']['direction']} past ${p['m15']['trigger']:,.2f}" if p.get("complete") else "") for s, p in s1.get("plans", {}).items()])


def chart_events(r, b):
    """Pure. The chart-side exits for one open trade from daily bars (date, close, high, low, ...):
    target touched (the stop now sits at the target), floor hit (fell back to it), and a close under
    the 30-day EMA (the thesis line Version 1 uses). Returns [(key, message)]."""
    import scan_daily as S
    after = [x for x in b if x[0] > r["opened"]]; out = []
    tgt = float(r["target"] or 0); name = f"{r['symbol']} " + (f"${float(r['strike']):g}{r['kind'][0].upper()}" if r["kind"] != "shares" else "shares")
    if tgt and after:
        i = next((n for n, x in enumerate(after) if x[2] >= tgt), None)
        if i is not None:
            out.append(("tgt", f"🎯 **{name} touched the target ${tgt:,.2f}** ({after[i][0][5:]}) -- the stop now sits at the target"))
            if any(x[3] <= tgt for x in after[i + 1:]): out.append(("floor", f"🛑 **{name} fell back to the target floor ${tgt:,.2f}** -- the rule says sell"))
    c = [x[1] for x in b]
    if len(c) > 31:
        e = S.ema(c, 30)
        if c[-1] < e[-1] and c[-2] >= e[-2] * (1 - 1e-9):      # the cross day only (tolerance: float EMA)
            out.append((f"e30:{b[-1][0]}", f"📉 **{name}: {r['symbol']} closed under the 30-day EMA** ${e[-1]:,.2f} (close ${c[-1]:,.2f}) -- the thesis line; decide, don't drift"))
    return out


def watch_charts(rs, st, tag=""):
    """Once a day after the close: the chart exits for every open real trade, shares and options."""
    import scan_daily as S
    for r in [r for r in rs if (r["acct"] == "real" or r["acct"].startswith("friend-")) and r["status"] == "open"]:
        try: b = S.bars(r["symbol"])
        except Exception: continue
        for key, msg in chart_events(r, b):
            k = f"{r['id']}:{key}"
            if not st.get(k): st[k] = datetime.date.today().isoformat(); say(tag + msg)


def board_text(rs):
    """The live board: every open real position with its live price and P&L, one message, edited in place."""
    import scan_daily as S
    from ticket_bot import quote
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5)))
    L = [f"**📊 LIVE BOARD** · {now.strftime('%a %-I:%M %p')} CT · account ${S.PLAN['balance']:,.0f} (Schwab {S.PLAN.get('balance_as_of', '')[5:]})"]
    tot = 0.0
    for r in [r for r in rs if r["acct"] == "real" and r["status"] == "open"]:
        e = float(r["entry"]); n = int(r["contracts"] or 1)
        try:
            if r["kind"] in ("call", "put") and r.get("expiry"):     # puts were priced as shares before 2026-10-22
                rt = "P" if r["kind"] == "put" else "C"
                b, a = quote(r["symbol"], r["expiry"], float(r["strike"]), rt); m = (a + b) / 2 if a else None
                name = f"{r['symbol']} {r['expiry'][5:]} ${float(r['strike']):g}{rt} ×{n}"; pl = (m - e) * 100 * n if m else None
            else:
                m = S.bars(r["symbol"], "5d")[-1][1]; name = f"{r['symbol']} {n} sh"; pl = (m - e) * n
        except Exception: m = pl = None; name = r["symbol"]
        if pl is not None: tot += pl
        L.append(f"{name} · ${e:.2f} → {'$%.2f' % m if m else '—'} · {('+' if pl >= 0 else '−') + '$%s' % f'{abs(pl):,.0f}' if pl is not None else 'no quote'}"
                 + (f" ({100 * (m / e - 1):+.0f}%)" if m else ""))
    L.append(f"**Open P&L {'+' if tot >= 0 else '−'}${abs(tot):,.0f}** · edited every minute in market hours · not advice")
    return "\n".join(L)[:1990]


def board(rs, st):
    """Post the board once, then edit the same message every minute (no new messages, no pings)."""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))); day = now.date().isoformat()
    live = now.weekday() < 5 and (8, 25) <= (now.hour, now.minute) <= (15, 5)
    if not live and st.get("board_day") == day: return
    if not st.get("board_ch"):
        chans = api(f"/guilds/1533631423498424490/channels")
        c = next((c for c in chans if c.get("name") == "trade-updates"), None)
        if not c: return
        st["board_ch"] = c["id"]
    text = board_text(rs)
    try:
        if st.get("board_id"): api(f"/channels/{st['board_ch']}/messages/{st['board_id']}", {"content": text}, method="PATCH")
        else: raise KeyError("no board yet")
    except Exception:
        m = api(f"/channels/{st['board_ch']}/messages", {"content": text}); st["board_id"] = m.get("id")
    st["board_day"] = day


def myact(v, c):
    """My Actions (portfolio_manager): confirm, skip, or update the real balance. Owner only."""
    import portfolio_manager as PM; m = PM.my_load(); today = datetime.date.today().isoformat()
    if v == "mybal":
        m["equity"] = c["bal"]; m["cash"] = c["cash"] if c["cash"] is not None else m["cash"]; m["as_of"] = today; r = f"balance ${m['equity']:,.2f}, cash ${m['cash']:,.2f} recorded"
    else: r = PM.my_cmd(m, v, c["sym"], c["px"], c["n"], today)
    PM.my_save(m); return r


def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    msgs = api(f"/channels/{CH}/messages?limit=20" + (f"&after={st['last']}" if st.get("last") else ""))
    for m in sorted(msgs, key=lambda x: int(x["id"])):
        st["last"] = m["id"]; who = m["author"]["id"]
        if (who != OWNER and who not in FRIENDS) or m["author"].get("bot"): continue
        v, c = parse(m.get("content", "")); rs = rows(who); label = "owner" if who == OWNER else FRIENDS[who]
        if v in ("done", "skip", "mybal") and who != OWNER: v = "help"
        try:
            import ledger_v1 as V
            reply = {"check": lambda: V.improve(dict(c, text=m.get("content", "")), "owner" if who == OWNER else FRIENDS[who]),
                     "details": lambda: V.improve(dict(c, text=m.get("content", "")), "owner" if who == OWNER else FRIENDS[who]),
                     "bought": lambda: bought(c, rs, who), "sold": lambda: sold(c, rs, who), "trades": lambda: trades(rs, who),
                     "pin": lambda: pins(c["sym"], True), "unpin": lambda: pins(c["sym"], False), "charts": charts, "report": V.report,
                     "done": lambda: myact("done", c), "skip": lambda: myact("skip", c), "mybal": lambda: myact("mybal", c),
                     "journal": lambda: J.on_reason(c["word"], c["text"], label)}.get(v, lambda: HELP)()
        except Exception as e:
            reply = f"Something broke on that one: {str(e)[:120]}"
        text, png = reply if isinstance(reply, tuple) else (reply, None)
        post((f"<@{who}> " if who != OWNER else "") + text, png)
        try:                                            # decision journal (records only; never a signal)
            if v in ("check", "details") and c.get("sym"):
                snap, cardtext = J.on_check(c, label); msg = say(cardtext)
                for emo in ("%E2%9C%85", "%E2%9D%8C", "%E2%8F%B3"): api(f"/channels/{CH}/messages/{msg['id']}/reactions/{emo}/@me", method="PUT")
                J.remember_message(snap["id"], msg["id"])
            elif v == "bought" and isinstance(text, str) and text.startswith("✅"): J.on_fill("buy", c, c["px"], label)
            elif v == "sold" and isinstance(text, str) and text.startswith("📒"): J.on_fill("sell", c, c["px"], label)
        except Exception as e: print("journal failed", e)
        try: api(f"/channels/{CH}/messages/{m['id']}/reactions/%E2%9C%85/@me", method="PUT")
        except Exception: pass
    if st.get("last") is None and msgs == []: st["last"] = "0"
    try:
        import ledger_v1 as V; V.tick(say)              # Version 1: market, seven charts, states -- sends only on change
    except Exception as e: print("v1 tick failed", e)
    try: J.poll_reactions(api, CH, OWNER); J.weekly(say)  # journal: ✅ ❌ ⏳ choices, Saturday review
    except Exception as e: print("journal tick failed", e)
    try:                                                # MNQ paper bot: Frank's ICT model, tested at break-even, on by Arrington's choice 2026-10-21
        import mes_paper, scan_daily as S; mes_paper.tick(lambda t: S.discord(t, "paper"))
    except Exception as e: print("mnq tick failed", e)
    try:                                                # F-call / G1-call forward paper track (2026-10-23): quotes only, no orders, no messages
        import calls_paper; calls_paper.tick()
    except Exception as e: print("calls paper tick failed", e)
    try: board(rows(), st)
    except Exception as e: print("board failed", e)
    try:
        watch(rows(), st)
        for fid in FRIENDS: watch(rows(fid), st, f"<@{fid}> ")
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))); day = now.date().isoformat()
        if now.weekday() < 5 and (now.hour, now.minute) >= (15, 10) and st.get("charts_day") != day:   # once, after the close
            st["charts_day"] = day; watch_charts(rows(), st)
            for fid in FRIENDS: watch_charts(rows(fid), st, f"<@{fid}> ")
    except Exception as e: print("watch failed", e)
    json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    if os.environ.get("TMTEST"):
        assert parse("bought AMZN 10/23 265C 3.18") == ("bought", dict(sym="AMZN", exp=f"{YEAR}-10-23", k=265.0, right="C", px=3.18, n=1))
        assert parse("check dash 10/30 200c")[1]["sym"] == "DASH" and parse("check dash 10/30 200c")[0] == "check"
        assert parse("sold AMZN 265C 4.90") == ("sold", dict(sym="AMZN", exp=None, k=265.0, right="C", px=4.9, n=1))
        assert parse("bto PYPL 9/25 53C 1.66 x2")[1]["n"] == 2 and parse("how are my trades")[0] == "trades"
        assert parse("Check be 10/16 272.50 call")[1] == dict(sym="BE", exp=f"{YEAR}-10-16", k=272.5, right="C", px=None, n=1)
        assert parse("check AMZN 10/23 250 puts")[1]["right"] == "P"
        assert parse("check META calls")[1]["right"] == "C" and parse("check META")[1]["right"] is None and parse("pin meta") == ("pin", dict(sym="META"))
        assert parse("hello")[0] == "help" and bracket(3.18) == dict(tp=6.36, sl=1.59, lock=6.36)
        assert gate("NFLX", 70, 622, 850, [], False, 0.05)[0] == "WAIT" and gate("SG", 400, 400, 850, [], False, 0.05)[0] == "APPROVED"
        assert gate("QQQ", 3, 300, 850, [], False, 0.02)[0] == "PASS" and gate("QQQ", 3, 300, 850, ["pattern #7"], False, 0.02)[0] == "APPROVED"
        assert gate("AMZN", 11, 302, 850, ["level broke up 10-09"], False, 0.03)[0] == "WAIT" and gate("AMZN", 11, 302, 850, [], False, 0.03)[0] == "PASS"
        assert gate("NOW", 8, 386, 850, ["Setup F"], False, 0.04)[0] == "APPROVED" and gate("BE", 0, 98, 850, ["Setup F"], False, 0.04)[0] == "WAIT"
        assert gate("NOW", 8, 386, 850, ["Setup F"], True, 0.04)[0] == "WAIT" and gate("NOW", 8, 900, 850, ["Setup F"], False, 0.04)[0] == "PASS"
        # chart exits: target touched, floor hit, close under the 30 EMA (once, on the cross day)
        mk = lambda i, c, h, l: (f"2026-09-{i:02d}", c, h, l, c, 0)
        B = [mk(i, 100 + i * 0.1, 100.3 + i * 0.1, 99.8 + i * 0.1) for i in range(1, 29)]
        row = dict(id="t", symbol="X", kind="shares", opened="2026-09-10", target="110", strike="")
        assert chart_events(row, B) == []
        assert [k for k, _ in chart_events(row, B + [mk(29, 109, 110.5, 108)])] == ["tgt"]
        assert [k for k, _ in chart_events(row, B + [mk(29, 109, 110.5, 108), mk(30, 109.5, 111, 109.9)])] == ["tgt", "floor"]
        D = [mk(i, 100, 101, 99) for i in range(1, 32)] + [mk(32, 90, 91, 89)]      # 32 bars: the cross day is the last
        ev = chart_events(dict(row, target=""), D); assert len(ev) == 1 and ev[0][0].startswith("e30:"), ev
        assert chart_events(dict(row, target=""), D + [mk(33, 90, 91, 89)]) == []           # still under: no repeat
        bt = board_text([dict(acct="real", status="open", kind="shares", symbol="NOW", entry="138.11", contracts="1", expiry="", strike="")])
        assert bt.startswith("**📊 LIVE BOARD**") and "NOW 1 sh" in bt and "Open P&L" in bt, bt
        print("tm ok")
    else:
        main()
