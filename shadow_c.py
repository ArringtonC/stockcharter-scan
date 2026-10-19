#!/usr/bin/env python3
"""SHADOW C (2026-10-19): a second paper book beside the Setup F shares bot. Same entries (today's Setup F
fires from data.json, 7% of equity, one position per name, whole shares, $100,000 start, idle cash earns
nothing -- the same capital rules as the "bot" book so the two compare like for like). One difference,
the frozen Test C exit: after the stock has touched its 252-high target, sell at the next session after
a close under its 30-day EMA; otherwise sell after 252 sessions. Its own cash, holdings, orders, fills and
equity, in ~/.ledger-shadow-c.json (never the public repo). Fills at the next run after the signal while
the market is open, at the live price, 0.10% each way. Every decision is written before its outcome.
Nothing here touches Version 1, the bot book, Discord #trades or live trading. Saturday: one comparison
line to #paper-portfolio. Self-test: SHADOWTEST=1 python3 shadow_c.py"""
import datetime, json, os
import scan_daily as S
STATE = os.path.expanduser("~/.ledger-shadow-c.json"); START = 100_000.0; COST = 0.001; HOLD = 252


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else dict(cash=START, pos={}, pending=[], trades=[], log=[], curve=[])


def save(st): json.dump(st, open(STATE, "w"), indent=1)


def decide(st, d, bars_fn, today, market_open):
    """Pure given bars_fn(sym) -> daily bars. Entries from today's F fires; exits by the C rule; fills for
    pending orders at the live price when the market is open. Returns the equity."""
    fires = {r["sym"]: r for r in d.get("F", [])}
    # 1. fills for yesterday's decisions
    if market_open:
        for o in list(st["pending"]):
            b = bars_fn(o["sym"]); px = b[-1][1]
            if o["side"] == "buy":
                eq = equity(st, bars_fn); q = int(min(0.07 * eq, st["cash"]) // (px * (1 + COST)))
                if q < 1: st["log"].append(dict(date=today, sym=o["sym"], what="buy skipped: no cash", cash=round(st["cash"]))); st["pending"].remove(o); continue
                st["cash"] -= q * px * (1 + COST); st["pos"][o["sym"]] = dict(q=q, entry=round(px * (1 + COST), 4), opened=today, tgt=o["tgt"], armed=False, sessions=0)
                st["log"].append(dict(date=today, sym=o["sym"], what="filled buy", q=q, px=round(px, 2)))
            else:
                p = st["pos"].pop(o["sym"], None)
                if p:
                    st["cash"] += p["q"] * px * (1 - COST); pl = round(p["q"] * (px * (1 - COST) - p["entry"]), 2)
                    st["trades"].append(dict(sym=o["sym"], opened=p["opened"], closed=today, q=p["q"], entry=p["entry"], exit=round(px * (1 - COST), 4), pl=pl, why=o["why"]))
                    st["log"].append(dict(date=today, sym=o["sym"], what="filled sell", why=o["why"], pl=pl))
            st["pending"].remove(o)
    # 2. exit decisions on today's closes (the C rule), queued for the next session
    for sym, p in st["pos"].items():
        b = bars_fn(sym); c = [x[1] for x in b]
        if b[-1][0] != today: continue                                    # no close yet today
        p["sessions"] = p.get("sessions", 0) + 1
        if b[-1][2] >= p["tgt"]: p["armed"] = True
        e30 = S.ema(c, 30)[-1]
        if any(o["sym"] == sym for o in st["pending"]): continue
        if p["sessions"] >= HOLD: st["pending"].append(dict(side="sell", sym=sym, why="252 sessions"))
        elif p["armed"] and c[-1] < e30: st["pending"].append(dict(side="sell", sym=sym, why=f"C exit: touched ${p['tgt']:,.2f}, closed under the 30 EMA ${e30:,.2f}"))
        if st["pending"] and st["pending"][-1]["sym"] == sym: st["log"].append(dict(date=today, sym=sym, what="decision: " + st["pending"][-1]["why"], px=round(c[-1], 2)))
    # 3. entry decisions: today's fires not held, not pending
    for sym, r in fires.items():
        if sym in st["pos"] or any(o["sym"] == sym for o in st["pending"]): continue
        if any(t["sym"] == sym and t["closed"] >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=30)).isoformat() for t in st["trades"]): continue
        st["pending"].append(dict(side="buy", sym=sym, tgt=r["tgt"])); st["log"].append(dict(date=today, sym=sym, what="decision: buy at the next open (Setup F fire)", px=round(r["px"], 2), tgt=round(r["tgt"], 2)))
    eq = equity(st, bars_fn); st["curve"] = [x for x in st["curve"] if x[0] != today] + [[today, round(eq, 2)]]
    return eq


def equity(st, bars_fn):
    return st["cash"] + sum(p["q"] * bars_fn(s)[-1][1] for s, p in st["pos"].items())


def compare_text(st, d, bars_fn):
    """Saturday: Shadow C vs the bot book (same start, same entries, different exit)."""
    eq = equity(st, bars_fn); peak = max([v for _, v in st["curve"]] + [START]); dd = eq / peak - 1
    real = sum(t["pl"] for t in st["trades"]); unreal = sum(p["q"] * (bars_fn(s)[-1][1] - p["entry"]) for s, p in st["pos"].items())
    bot = [r for r in d.get("open", []) if r.get("acct") == "bot"]; botv = START + sum((float(r.get("now", 0)) - float(r["entry"])) * float(r.get("contracts") or 1) for r in bot)
    cx = len([t for t in st["trades"] if t["why"].startswith("C exit")])
    return (f"<b>🧪 SHADOW C · Setup F entries, the C exit · paper</b>\nSTART $100,000 → NOW <b>${eq:,.0f}</b> ({100 * (eq / START - 1):+.1f}%) · drawdown {100 * dd:.0f}% · "
            f"realized ${real:+,.0f} · unrealized ${unreal:+,.0f} · {len(st['pos'])} open · exposure {100 * (1 - st['cash'] / eq):.0f}%\n"
            f"BOT (hold 252): ${botv:,.0f} · {len(bot)} open\n{cx} C exits so far · "
            f"only in Shadow C: {', '.join(sorted(set(st['pos']) - {r['symbol'] for r in bot})) or 'none'} · only in the bot: {', '.join(sorted({r['symbol'] for r in bot} - set(st['pos']))) or 'none'}\n"
            f"<i>recorded, not advice · the exit rule under forward test · thesis/f-audit.md</i>")


def main():
    d = json.load(open(os.path.join(S.DOCS, "data.json"))); st = load(); today = d["date"]
    try: market_open = __import__("autotrade").call("/clock")["is_open"]
    except Exception: market_open = False
    bars_fn = lambda s: S.bars(s)
    eq = decide(st, d, bars_fn, today, market_open); save(st)
    print(f"shadow c: equity ${eq:,.0f} · {len(st['pos'])} open · {len(st['pending'])} pending · {len(st['trades'])} closed")
    if datetime.date.today().weekday() == 5 and not any(x.get("sat") == today for x in st["log"]):
        S.notify(compare_text(st, d, bars_fn), "paper"); st["log"].append(dict(date=today, sat=today, what="weekly comparison posted")); save(st)


if __name__ == "__main__":
    if os.environ.get("SHADOWTEST"):
        import tempfile; STATE = os.path.join(tempfile.mkdtemp(), "s.json")
        B = {"X": [(f"2026-09-{i:02d}", 100 + i, 101 + i, 99 + i) for i in range(1, 30)] + [("2026-10-01", 130, 131, 129), ("2026-10-02", 118, 119, 117)]}
        bf = lambda s: B[s]; st = load(); d = dict(F=[dict(sym="X", px=129, tgt=131)])
        decide(st, d, bf, "2026-09-29", False); assert st["pending"][0]["side"] == "buy"                  # decision logged before the fill
        decide(st, d, bf, "2026-09-30", True); assert "X" in st["pos"] and st["pos"]["X"]["q"] == int(7000 // (129 * 1.001))   # 7% at the live price
        decide(st, d, bf, "2026-10-01", True); assert st["pos"]["X"]["armed"] and not st["pending"]       # touched the target, still above the EMA
        decide(st, d, bf, "2026-10-02", True); assert st["pending"] and st["pending"][0]["why"].startswith("C exit")   # closed under the 30 EMA -> sell next session
        decide(st, d, bf, "2026-10-03", True); assert not st["pos"] and st["trades"][0]["why"].startswith("C exit") and st["trades"][0]["pl"] < 0
        print("shadow ok")
    else: main()
