"""Alert behaviour, no network: python3 test_alerts.py"""
import datetime, os, tempfile, scan_daily as S
today = str(datetime.date.today())
S.ALERT_STATE = os.path.join(tempfile.mkdtemp(), "state.json")
# pin the clock mid-session so the closing-run rule (always push after 3pm ET) cannot fire
class _DT(datetime.datetime):
    @classmethod
    def now(cls, tz=None): return datetime.datetime(2026, 9, 24, 16, 0, tzinfo=datetime.timezone.utc).astimezone(tz) if tz else datetime.datetime(2026, 9, 24, 11, 0)
S.datetime = type("M", (), {"datetime": _DT, "date": datetime.date, "timedelta": datetime.timedelta, "timezone": datetime.timezone})
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
