"""Alert behaviour, no network: python3 test_alerts.py"""
import datetime, os, tempfile, scan_daily as S
today = str(datetime.date.today())
S.ALERT_STATE = os.path.join(tempfile.mkdtemp(), "state.json")
S.PLAN = {**S.PLAN, "balance": 8355.13}   # pins the 7% cap at $600 for these checks
# pin the clock mid-session so the closing-run rule (always push after 3pm ET) cannot fire
class _DT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return datetime.datetime(2026, 9, 24, 16, 0, tzinfo=datetime.timezone.utc).astimezone(tz) if tz else datetime.datetime(2026, 9, 24, 11, 0)
S.datetime = type("M", (), {"datetime": _DT, "date": datetime.date, "timedelta": datetime.timedelta, "timezone": datetime.timezone})
S.affordable = lambda sym, px, budget: FAKE.get(sym)
S.panic_day = lambda sym: ("2026-09-24", -6.2, 3.4) if sym == "DEAR" else None
FAKE = {"CHEAP": dict(expiry="2026-11-20", strike=20, cost=240, n=2, over=False),
        "DEAR": dict(expiry="2026-11-20", strike=140, cost=1391, n=1, over=True)}
base = lambda **k: {**dict(date=today, F=[], S=[], vix=15, vix5=[15]*5, regime="CALM", vix_fires=False,
                           errors=[], taken=[], market={}, qqq=740), **k}
pos = lambda pl, dte: dict(symbol="DOCU", acct="real", kind="call", contracts=1, entry="26.23",
                           now=26.23 * (1 + pl / 100), pl_pct=pl, dte=dte, expiry="2027-01-15", strike="37.5")

# BUY: call under the cap -> contracts; over the cap -> shares plus the reason
t = S.summary(base(F=[dict(sym="CHEAP", px=19, tgt=25, up=30), dict(sym="DEAR", px=140, tgt=195, up=39)]), [])
assert "<b>BUY CHEAP $19.00</b>" in t and "NOV 20 · 2 × $20 CALLS · $1.20" in t and "$240 TOTAL" in t
assert "NOV 20 · 1 × $140 CALL · $13.91" in t and "OVER CAP → BUY 4 SHARES · $560" in t

# UPDATE: losses show, dollars show, the last week warns, the last 3 days name the day
t = S.summary(base(), [pos(-19, 58)]); assert "↓ TRADE UPDATE" in t and "−$498 · -19% · 58d left" in t
assert "⚠ 9d left" in S.summary(base(), [pos(5, 9)])
assert "EXPIRES" in S.summary(base(), [pos(5, 2)])

# dedupe: same state twice -> one push; crossing a 10% step -> push again
d = base(); S.should_push(d, [pos(23, 114)])
assert S.should_push(d, [pos(24, 114)]) == (False, "nothing changed")
assert S.should_push(d, [pos(31, 114)])[0]

# CLOSED: a real trades.csv row closed today becomes a card; it is not an open position
row = dict(symbol="QQQ", acct="real", kind="call", contracts=1, entry="2.24", exit="1.00",
           pl_pct="-55.4", closed=today, strike="748", expiry="2026-09-25")
t = S.summary(base(), [], [row])
assert "✓ TRADE CLOSED" in t and "$224 → <b>$100</b>" in t and "FINAL −$124 · -55%" in t
assert "TRADE UPDATE" not in t
print("alerts ok")

# CLOSED winner, and a bot trade's whole life: logged buy -> update -> near target -> closed
import autotrade as A
rs = []; A.log_buy(rs, dict(sym="BE", qty=2, tgt=351.28), 275.02, datetime.date.today())
live = lambda spot: {**rs[0], "now": spot, "pl_pct": (spot / 275.02 - 1) * 100, "to_target": (351.28 / spot - 1) * 100}
t = S.summary(base(), [live(300)])
assert "↑ TRADE UPDATE</b> · 🤖 PAPER" in t and "BE · 2 SHARES" in t and "$550 → <b>$600</b>" in t and "NEAR TARGET" not in t
t = S.summary(base(), [live(340)]); assert "NEAR TARGET $351.28</b> · 3% away" in t
assert S.should_push(base(), [live(340)])[0]       # entering the last 5% pushes by itself

# the two reports are separate messages with separate memory
m = base(market={"QQQ": 1.4, "QQQ_px": 750, "SPY": 0.6, "SPY_px": 770, "movers": [("NVDA", 3.1)]})
r = S.market_report(m); assert "$750.00" in r and "TRADE REPORT" not in r and "BUY" not in r
assert S.summary(m, []).startswith("<b>TRADE REPORT</b>") and "$750" not in S.summary(m, [])
mk = S.market_key(m); S.should_push(m, [], "mkt", mk)
assert S.should_push(m, [], "mkt", mk) == (False, "nothing changed")
m["market"]["QQQ"] = 2.3; assert S.should_push(m, [], "mkt", S.market_key(m))[0]   # crossing 2% pushes
A.close_filled(rs, {"BE": (351.28, today)})
t = S.summary(base(), [], [rs[0]])
assert "✓ TRADE CLOSED</b> · 🤖 PAPER" in t and "$550 → <b>$703</b>" in t and "FINAL +$153 · +28%" in t
assert "TRADE UPDATE" not in t
print("lifecycle ok")

assert S.to_discord('<b>BUY NOW</b> <i>x</i> <a href="https://a.b/">Ledger</a> &amp;') == '**BUY NOW** *x* [Ledger](<https://a.b/>) &'
print("discord ok")

# S&P 500 change inside the last week shows in the write-up, flagged when it is a scanned name
sp = base(sp500=[dict(effective=today, added="BE", added_name="Bloom Energy", removed="TAP",
                      removed_name="Molson Coors", reason="", announced="")])
w = "\n".join(S.writeup(sp, {}, True)); assert "<b>S&P 500:</b> <b>BE</b> (you scan it) joins" in w and "replacing <b>TAP</b>" in w
print("sp500 line ok")

# Discord routing: buys, movement and finished trades land in separate channels
p = S.summary(base(F=[dict(sym="DEAR", px=140, tgt=195, up=39)]), [pos(-19, 58)], [row], parts=True)
assert "BUY DEAR" in p["trades"] and "TRADE UPDATE" in p["updates"] and "TRADE CLOSED" in p["ledger"]
assert "BUY" not in p["updates"] + p["ledger"] and "UPDATE" not in p["trades"]
print("routing ok")

# real-money cards never reach Discord
live_real = {**pos(5, 90), "acct": "real"}
pub = lambda R: [r for r in R if r.get("acct") != "real"]
p = S.summary({**base(), "taken": []}, pub([live_real]), pub([row]), parts=True)
assert not p["updates"] and not p["ledger"], p
print("private ok")

# futures sessions: CME opens 5 PM Central Sunday-Thursday
ct = S.ZoneInfo("America/Chicago"); D = datetime.datetime
assert S.session_start(D(2026, 9, 28, 8, 0, tzinfo=ct)) == D(2026, 9, 27, 17, 0, tzinfo=ct)   # Mon morning -> Sun open
assert S.session_start(D(2026, 9, 27, 12, 0, tzinfo=ct)) == D(2026, 9, 24, 17, 0, tzinfo=ct)  # Sun noon -> Thu open
assert S.session_start(D(2026, 9, 27, 17, 30, tzinfo=ct)) == D(2026, 9, 27, 17, 0, tzinfo=ct) # Sun after the open
w = "\n".join(S.writeup(base(), {"fut": [("S&P", 7803.75, -0.3, True)]}, True)); assert "<b>FUTURES</b>\nS&P 7,803.75 -0.3%" in w
print("futures ok")

# volume "don't" rule: a recent panic day shows on the BUY card, and only there
t = S.summary(base(F=[dict(sym="CHEAP", px=19, tgt=25, up=30), dict(sym="DEAR", px=140, tgt=195, up=39)]), [])
assert "⚠ PANIC SELL 09-24: -6% ON 3.4× VOLUME" in t and t.count("PANIC SELL") == 1
print("panic ok")

# pattern WATCH cards: shown, never phrased as a buy, and routed with the new trades
w = dict(sym="META", date="2026-09-09", close=653.69, base_high=624.8, peak=790.8, e10=735.2, e30=701.4, e30_up=True, above30=True)
t = S.summary(base(watch=[w]), [])
assert "👀 SETUP G · WATCH META $653.69</b> · CORE" in t and "OVER $624.80" in t and "NOT AN ENTRY" in t and "BUY META" not in t
assert "10 EMA $735.20 > 30 EMA $701.40 · 30 rising · price above the 30" in t
assert "SETUP G · WATCH META" in S.summary(base(watch=[w]), [], parts=True)["watch"]
print("watch ok")

# phase change and light flips put a loud banner at the top of the market report
ph = [dict(n=i, name=n, when="", do=["x"]) for i, n in ((1, "Ride it"), (2, "One signal"), (3, "Both signals"), (4, "The drop"))]
mm = base(bubble=dict(phase=3, changed_from=2, phases=ph, ipo={}, since="2026-11-02"), playbook=dict(flips={"rule_a": False}),
          market={"heads": ["OpenAI prices IPO at $1 trillion valuation"]})
r = S.market_report(mm)
assert r.startswith("<b>🚨 AI BUBBLE PLAN: PHASE 2 → PHASE 3 (BOTH SIGNALS)</b>"), r[:80]
assert "🚨 BEAR RULE A FIRED" in r and "POSSIBLE BUBBLE SIGNAL 2" in r
assert "PHASE" not in S.market_report(base(bubble=dict(phase=2, changed_from=None, phases=ph, ipo={}, since="2026-06-15"), market={}))[:30]
print("phase alerts ok")

# a logged chart level that breaks gets one card
lv = dict(symbol="PLTR", pattern="ascending triangle after a trendline break", tf="30m", up=192.0, down=188.7,
          target_up=199.0, target_down=185.0, price=192.4, way="up")
t = S.summary(base(levels=[lv]), [])
assert "📍 LEVEL PLTR $192.40 · BROKE OUT" in t and "OVER $192.00 · TARGET $199.00" in t and "close back under $188.70" in t
print("levels ok")

# another mega-IPO after signal 2 already fired: a heads-up, not a phase change
mm2 = base(bubble=dict(phase=3, changed_from=None, phases=ph, ipo={"SpaceX": "2026-06-11"}, since="2026-06-15"),
           market={"heads": ["Anthropic prices IPO, raising $100 billion"]})
r = S.market_report(mm2); assert "⚠ ANOTHER MEGA-IPO (late-bubble sign)" in r and "PHASE 3 →" not in r, r[:200]
print("mega-ipo ok")

# weekdays: only fires not sent in the last 30 days go out; marking them stops the repeat
S.SENT = os.path.join(tempfile.mkdtemp(), "sent.json")
dd = base(F=[dict(sym="CHEAP", px=19, tgt=25, up=30), dict(sym="DEAR", px=140, tgt=195, up=39)], watch=[], levels=[])
assert [r["sym"] for r in S.fresh(dd)["F"]] == ["CHEAP", "DEAR"]
S.fresh(dd, mark=True)
assert S.fresh(dd)["F"] == []
dd2 = {**dd, "F": dd["F"] + [dict(sym="NEWB", px=50, tgt=70, up=40)]}
assert [r["sym"] for r in S.fresh(dd2)["F"]] == ["NEWB"]
assert "NEW TRADE" in S.summary(S.fresh(dd2), [], [], title="NEW TRADE") and "BUY NEWB" in S.summary(S.fresh(dd2), [], [], title="NEW TRADE")
print("fresh ok")

# the same scan twice on a weekday: run 1 sends the new fires + the market report, run 2 sends nothing
S.SENT = os.path.join(tempfile.mkdtemp(), "sent.json"); S.ALERT_STATE = os.path.join(tempfile.mkdtemp(), "a.json")
S.market_report = lambda d: "MARKET REPORT"; S.market_key = lambda d: "k"
held = dict(id="ZS-1", symbol="ZS", acct="bot", kind="stock", entry="200.79", now=212, pl_pct=5.6, to_target=3, target="219", expiry=None)
runs = []
for _ in (1, 2):
    got = []
    S.dispatch(dd2, [held], [], 0, send_tg=lambda t: got.append(("tg", t)), send_dc=lambda t, r: got.append((r, t)))
    runs.append(got)
print("  run 1 sent:", [(w, t.splitlines()[0][:50]) for w, t in runs[0]])
print("  run 2 sent:", runs[1])
assert any("BUY NEWB" in t for _, t in runs[0]) and any("ZS" in t for _, t in runs[0]) and runs[1] == []
# Saturday: every open position is in the weekend report, even ones already sent
got = []; S.dispatch(dd2, [held], [], 5, send_tg=got.append, send_dc=lambda t, r: None)
assert "WEEKEND REPORT" in got[-1] and "ZS" in got[-1]
print("dispatch twice ok")

# #trades holds only buys; G watches go to the watch part; weekend closes say CLOSED THIS WEEK + date
w = dict(sym="SG", close=9.1, date=today, base_high=8.8, peak=12.0)
p = S.summary(base(F=[dict(sym="CHEAP", px=19, tgt=25, up=30)], watch=[w]), [], parts=True)
assert "BUY CHEAP" in p["trades"] and "SETUP G" not in p["trades"] and "SETUP G · WATCH SG" in p["watch"]
assert "AFTER CLOSE" in S.summary(base(F=[dict(sym="CHEAP", px=19, tgt=25, up=30)]), [], parts=True, after_close=True)["trades"]
cl = dict(id="X", symbol="DOCU", acct="real", kind="stock", contracts=1, entry="100", exit="110", pl_pct=10, closed=today, expiry=None, strike=None)
assert "✓ CLOSED THIS WEEK · " + datetime.date.today().strftime("%b %-d").upper() in S.summary(base(), [], [cl], closed_days=7)
assert "✓ TRADE CLOSED" in S.summary(base(), [], [cl])
print("channels + wording ok")
