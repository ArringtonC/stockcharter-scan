"""Alert behaviour, no network: python3 test_alerts.py"""
import datetime, os, tempfile, scan_daily as S
today = str(datetime.date.today())
S.ALERT_STATE = os.path.join(tempfile.mkdtemp(), "state.json")
S.affordable = lambda sym, px, budget: FAKE[sym]
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
A.close_filled(rs, {"BE": (351.28, today)})
t = S.summary(base(), [], [rs[0]])
assert "✓ TRADE CLOSED</b> · 🤖 PAPER" in t and "$550 → <b>$703</b>" in t and "FINAL +$153 · +28%" in t
assert "TRADE UPDATE" not in t
print("lifecycle ok")
