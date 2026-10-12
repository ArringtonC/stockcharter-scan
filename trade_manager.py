#!/usr/bin/env python3
"""Trade manager (2026-10-12): Arrington texts the Ledger bot in Discord #trade-log; it checks, logs,
watches and journals his REAL trades. It never places an order -- every order is his click.

  check AMZN 10/23 265C        -> the pre-trade check: Ledger signal? cost vs the 7% cap, breakeven,
                                  earnings before expiry, and the bracket to enter (+50% / -50%)
  bought AMZN 10/23 265C 3.18  -> logs it in trades.csv (acct real) and starts watching it
  sold AMZN 265C 4.90          -> closes it, journals the result and whether the plan was followed
  trades                       -> open real positions with live prices
  anything else                -> the help text
Watching: every run, each open real option trade is priced; one alert at +50% (take profit) and one
at -50% (stop), and one at 2x (lock the gain). Only messages from the server owner are acted on.
Runs every minute (launchd com.arrington.ledger-tm). Self-test: TMTEST=1 python3 trade_manager.py"""
import csv, datetime, json, os, re, time, urllib.request
CH, OWNER = "1559302594189328544", "468902102995959808"
# who may use it: the owner's trades go to trades.csv (public repo); a friend's journal stays on this Mac
FRIENDS = {"1288838148436136057": "jcbreeze"}   # 2026-10-12: sized as if it were Arrington's account, his request
STATE = os.path.expanduser("~/.ledger-tm.json")
H = os.path.dirname(os.path.abspath(__file__))
YEAR = datetime.date.today().year
HELP = ("**I'm your trade manager.** Text me:\n`check AMZN 10/23 265C` · `bought AMZN 10/23 265C 3.18` · "
        "`sold AMZN 265C 4.90` · `trades`\nI log, watch and journal. You place every order.")


def api(path, body=None, method=None):
    rq = urllib.request.Request("https://discord.com/api/v10" + path, data=json.dumps(body).encode() if body else None,
                                method=method or ("POST" if body else "GET"),
                                headers={"Authorization": f"Bot {os.environ['DISCORD_LEDGER_BOT_TOKEN']}",
                                         "Content-Type": "application/json", "User-Agent": "DiscordBot (ledger, 1)"})
    b = urllib.request.urlopen(rq, timeout=20).read()
    return json.loads(b) if b else {}


def say(text): api(f"/channels/{CH}/messages", {"content": text[:1990]})


CONTRACT = r"(?P<sym>[A-Za-z]{1,5})\s+(?:(?P<mm>\d{1,2})/(?P<dd>\d{1,2})(?:/(?P<yy>\d{2,4}))?\s+)?\$?(?P<k>\d+(?:\.\d+)?)\s*(?P<r>[cpCP])\b"


def parse(text):
    """Pure: (verb, dict) from a message, or ("help", {})."""
    t = text.strip()
    if re.match(r"^(trades|positions|how are my trades)\b", t, re.I): return "trades", {}
    m = re.match(r"^(check|bought|bto|buy|sold|stc|sell)\s+" + CONTRACT + r"(?:\s+\$?(?P<px>\d+(?:\.\d+)?))?(?:\s+x?(?P<n>\d+))?", t, re.I)
    if not m: return "help", {}
    v = m.group(1).lower(); v = {"bto": "bought", "buy": "bought", "stc": "sold", "sell": "sold"}.get(v, v)
    exp = None
    if m["mm"]:
        y = int(m["yy"]) if m["yy"] else YEAR; y = y + 2000 if y < 100 else y
        exp = datetime.date(y, int(m["mm"]), int(m["dd"])).isoformat()
    return v, dict(sym=m["sym"].upper(), exp=exp, k=float(m["k"]), right=m["r"].upper(),
                   px=float(m["px"]) if m["px"] else None, n=int(m["n"]) if m["n"] else 1)


def bracket(px):
    """Pure: the exits to enter with the buy (thesis/exit-gauntlet.md: +50%/-50% was the best rule)."""
    return dict(tp=round(px * 1.5, 2), sl=round(px * 0.5, 2), lock=round(px * 2, 2))


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
    if dte > 60: return ("APPROVED", "long-dated: 2x lock + -50% stop") if tested or cost <= cap / 2 else ("WAIT", "long-dated with no signal: half size or less")
    if sym in ("QQQ", "SPY"): return ("APPROVED", "pattern #7 on the index") if "pattern #7" in tested else ("PASS", "QQQ/SPY without a pattern #7 alert (your record: -$463 on 11)")
    if not tested: return ("WAIT", f"only {', '.join(sig)} -- not a tested entry yet") if sig else ("PASS", "no Ledger signal (your record: -$859 on 31)")
    if earn_before: return "WAIT", "earnings land before expiry"
    if dte <= 2: return "WAIT", "Setup F with 0-2 days left -- 3 wins, all BE, not yet a rule; paper it"
    if spread > 0.15: return "WAIT", "spread over 15% -- wait for a tighter quote"
    return "APPROVED", f"{', '.join(tested)} · {dte} days · inside the cap"


def check(c, who=OWNER):
    import scan_daily as S
    from ticket_bot import quote, next_earnings
    if not c["exp"]: return "Add the expiry: `check AMZN 10/23 265C`."
    spot = S.bars(c["sym"], "5d")[-1][1]; b, a = quote(c["sym"], c["exp"], c["k"]) if c["right"] == "C" else (0, 0)
    cap = round(S.PLAN["balance"] * 0.07 / 50) * 50; sig, pats = signal(c["sym"]); earn = next_earnings(c["sym"], datetime.date.today())
    dte = (datetime.date.fromisoformat(c["exp"]) - datetime.date.today()).days
    v, why = gate(c["sym"], dte, (a or 0) * 100, cap, sig, bool(earn and earn.isoformat() <= c["exp"]), (a - b) / a if a else 1)
    icon = {"APPROVED": "✅", "WAIT": "🟡", "PASS": "⛔"}[v]
    bb = S.bars(c["sym"]); e30 = S.ema([x[1] for x in bb], 30)[-1]
    inval, inval_why = (e30, "30-day EMA") if spot > e30 else (min(x[3] for x in bb[-10:]), "10-day low")   # already under the EMA: the recent low
    L = [f"{icon} **{v}** -- {why}", f"**{c['sym']} {c['exp'][5:]} ${c['k']:g}{c['right']}** · stock ${spot:,.2f}"]
    if a:
        be = c["k"] + a; L += [f"Ask ${a:.2f} (bid ${b:.2f}) · **${a * 100:,.0f}** vs cap ${cap:,} → {'fits ✅' if a * 100 <= cap else 'OVER CAP ❌'}",
                               f"Breakeven at expiry ${be:,.2f} ({100 * (be / spot - 1):+.1f}%) · spread {100 * (a - b) / a:.0f}%"]
        if (a - b) / a > 0.15: L.append(f"⚠️ Wide spread right now -- buy with a limit near the middle (${(a + b) / 2:.2f}), never a market order")
        mid = (a + b) / 2; br = bracket(mid)
        L.append(f"Limit **${mid:.2f}** · don't pay above **${mid + (a - mid) / 2:.2f}**")
        L.append(f"Exits to enter with it: take profit **${br['tp']}** (+50%) · stop **${br['sl']}** (-50%)" if dte <= 60 else
                 f"Exits (long-dated): stop **${br['sl']}** (-50%); at 2x (${br['lock']}) move the stop up to lock the gain")
        L.append(f"Stock invalidation: a close under ${inval:,.2f} ({inval_why})")
    else: L.append("No live quote (puts aren't priced here yet).")
    L.append(f"Ledger signal: **{', '.join(sig) if sig else 'none ⚠️ -- a no-signal trade'}**" + (f" · chart: {', '.join(pats)}" if pats else ""))
    # your own record (thesis/trade-manager-replay.md: 35 real trades 08-24..10-09, +50/-50 bracket)
    if v == "PASS" or who != OWNER: pass
    elif c["sym"] in ("QQQ", "SPY") and "pattern #7" not in sig:
        L.append("📉 Your record: QQQ/SPY options -$467 on 11 trades (worse with a bracket). Only with a pattern #7 alert.")
    if v == "PASS" or who != OWNER: pass
    elif dte <= 2: L.append(f"📉 Your record: 0-2 day options -$35 on 17 trades (-$239 with a bracket). {dte} days left.")
    elif dte <= 9: L.append(f"✅ Your best zone: 3-9 days to expiry (+$643 on 11 trades with a bracket). {dte} days left.")
    elif dte <= 60: L.append(f"⚠️ Your record at 10+ days: -$753 on 7 trades (big size, held losers). {dte} days left -- keep it small.")
    if not sig and who == OWNER: L.append("📉 Your record without a Ledger signal: -$868 on 31 trades. With Setup F: 4 for 4, +$635 with a bracket.")
    if earn and earn.isoformat() <= c["exp"]: L.append(f"⚠️ Earnings {earn.isoformat()[5:]} land BEFORE expiry -- option prices drop after the report")
    elif earn: L.append(f"Earnings {earn.isoformat()[5:]}, after expiry ✅")
    if who != OWNER: L.append("_Ledger's rules on a $12k account, not investment advice. You place every order._")
    return "\n".join(L)


def bought(c, rs, who=OWNER):
    if not (c["exp"] and c["px"]): return "Format: `bought AMZN 10/23 265C 3.18`"
    today = datetime.date.today().isoformat(); kind = "call" if c["right"] == "C" else "put"
    rid = f"{today}-{c['sym']}-{c['k']:g}{c['right'].lower()}-real"
    while any(r["id"] == rid for r in rs): rid += "b"
    br = bracket(c["px"]); sig, _ = signal(c["sym"])
    rs.append(dict(id=rid, kind=kind, acct="real" if who == OWNER else "friend-" + FRIENDS[who], opened=today, symbol=c["sym"], setup=", ".join(sig) or "none", entry=f"{c['px']:.2f}",
                   target="", strike=f"{c['k']:g}", expiry=c["exp"], contracts=str(c["n"]), status="open", closed="", exit="", pl_pct="",
                   note=f"REAL. Logged from Discord #trade-log. Bracket: take profit {br['tp']}, stop {br['sl']}; 2x {br['lock']} locks the gain."))
    save(rs, who)
    return (f"✅ **LOGGED · {c['sym']} {c['exp'][5:]} ${c['k']:g}{c['right']} × {c['n']} @ ${c['px']:.2f}** (${c['px'] * 100 * c['n']:,.0f})\n"
            f"Enter now in Schwab: take-profit limit **${br['tp']}** + stop **${br['sl']}** (OCO)\n"
            f"I'll text you at +50%, at -50%, and at 2x. Signal: {', '.join(sig) if sig else 'none ⚠️'}")


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
            try: b, a = quote(r["symbol"], r["expiry"], float(r["strike"])) if r["kind"] == "call" else (0, 0)
            except Exception: b = a = 0
            m = (a + b) / 2 if a else None; e = float(r["entry"]); n = int(r["contracts"] or 1)
            L.append(f"{r['symbol']} {r['expiry'][5:]} ${float(r['strike']):g}{r['kind'][0].upper()} ×{n} · ${e:.2f} → " +
                     (f"${m:.2f} · {'+' if m >= e else '−'}${abs(m - e) * 100 * n:,.0f} ({100 * (m / e - 1):+.0f}%)" if m else "no quote"))
        else: L.append(f"{r['symbol']} {r.get('contracts') or 1} share(s) @ ${float(r['entry']):.2f}")
    return "\n".join(L)


def watch(rs, st, tag=""):
    """One text per event per trade: +50%, -50%, 2x."""
    from ticket_bot import quote
    for r in [r for r in rs if (r["acct"] == "real" or r["acct"].startswith("friend-")) and r["status"] == "open" and r["kind"] == "call" and r.get("expiry")]:
        try: b, a = quote(r["symbol"], r["expiry"], float(r["strike"]))
        except Exception: continue
        if not b: continue
        e = float(r["entry"]); br = bracket(e)
        far = (datetime.date.fromisoformat(r["expiry"]) - datetime.date.today()).days > 60   # long-dated (SG, SNAP 2028): 2x lock only
        for key, hit, msg in (("tp", b >= br["tp"], f"🎯 **{r['symbol']} ${float(r['strike']):g}C at +50%** · bid ${b:.2f} (you paid ${e:.2f}) -- your take-profit level"),
                              ("sl", b <= br["sl"], f"🛑 **{r['symbol']} ${float(r['strike']):g}C at -50%** · bid ${b:.2f} -- your stop level"),
                              ("2x", b >= br["lock"], f"🚀 **{r['symbol']} ${float(r['strike']):g}C at 2x** · bid ${b:.2f} -- set a stop at ${max(1.5 * e, 0.75 * b):.2f} to lock the gain")):
            k = f"{r['id']}:{key}"
            if far and key == "tp": continue
            if hit and not st.get(k): st[k] = datetime.date.today().isoformat(); say(tag + msg)


def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    msgs = api(f"/channels/{CH}/messages?limit=20" + (f"&after={st['last']}" if st.get("last") else ""))
    for m in sorted(msgs, key=lambda x: int(x["id"])):
        st["last"] = m["id"]; who = m["author"]["id"]
        if (who != OWNER and who not in FRIENDS) or m["author"].get("bot"): continue
        v, c = parse(m.get("content", "")); rs = rows(who)
        try:
            reply = {"check": lambda: check(c, who), "bought": lambda: bought(c, rs, who), "sold": lambda: sold(c, rs, who),
                     "trades": lambda: trades(rs, who)}.get(v, lambda: HELP)()
        except Exception as e:
            reply = f"Something broke on that one: {str(e)[:120]}"
        say((f"<@{who}> " if who != OWNER else "") + reply)
        try: api(f"/channels/{CH}/messages/{m['id']}/reactions/%E2%9C%85/@me", method="PUT")
        except Exception: pass
    if st.get("last") is None and msgs == []: st["last"] = "0"
    try:
        watch(rows(), st)
        for fid in FRIENDS: watch(rows(fid), st, f"<@{fid}> ")
    except Exception as e: print("watch failed", e)
    json.dump(st, open(STATE, "w"))


if __name__ == "__main__":
    if os.environ.get("TMTEST"):
        assert parse("bought AMZN 10/23 265C 3.18") == ("bought", dict(sym="AMZN", exp=f"{YEAR}-10-23", k=265.0, right="C", px=3.18, n=1))
        assert parse("check dash 10/30 200c")[1]["sym"] == "DASH" and parse("check dash 10/30 200c")[0] == "check"
        assert parse("sold AMZN 265C 4.90") == ("sold", dict(sym="AMZN", exp=None, k=265.0, right="C", px=4.9, n=1))
        assert parse("bto PYPL 9/25 53C 1.66 x2")[1]["n"] == 2 and parse("how are my trades")[0] == "trades"
        assert parse("hello")[0] == "help" and bracket(3.18) == dict(tp=4.77, sl=1.59, lock=6.36)
        assert gate("NFLX", 70, 622, 850, [], False, 0.05)[0] == "WAIT" and gate("SG", 400, 400, 850, [], False, 0.05)[0] == "APPROVED"
        assert gate("QQQ", 3, 300, 850, [], False, 0.02)[0] == "PASS" and gate("QQQ", 3, 300, 850, ["pattern #7"], False, 0.02)[0] == "APPROVED"
        assert gate("AMZN", 11, 302, 850, ["level broke up 10-09"], False, 0.03)[0] == "WAIT" and gate("AMZN", 11, 302, 850, [], False, 0.03)[0] == "PASS"
        assert gate("NOW", 8, 386, 850, ["Setup F"], False, 0.04)[0] == "APPROVED" and gate("BE", 0, 98, 850, ["Setup F"], False, 0.04)[0] == "WAIT"
        assert gate("NOW", 8, 386, 850, ["Setup F"], True, 0.04)[0] == "WAIT" and gate("NOW", 8, 900, 850, ["Setup F"], False, 0.04)[0] == "PASS"
        print("tm ok")
    else:
        main()
