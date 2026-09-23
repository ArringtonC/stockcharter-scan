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
assert "BUY 2 CONTRACTS · $240" in t and "BUY 4 SHARES · $560" in t and "1 CALL COSTS $1,391 · OVER CAP" in t

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
assert "✓ TRADE CLOSED" in t and "START $224 → EXIT <b>$100</b>" in t and "FINAL −$124 · -55%" in t
assert "TRADE UPDATE" not in t
print("alerts ok")
