#!/usr/bin/env python3
"""Option cost log (2026-10-28). Records live bid/ask for near-the-money options on the Focus names + SPY/QQQ, so we can
measure what trading really costs (the half-spread decides whether random calls win or lose: thesis/rs, 2026-10-28).
READ-ONLY: it places nothing and changes no signal. Quotes are Alpaca's free "indicative" options feed (not the full
OPRA NBBO) — good for spread size, labeled as such. Appends to ~/.ledger-costs/quotes.jsonl during market hours only.
  python3 cost_log.py            normal run (skips outside 9:35-15:55 ET on weekdays)
  python3 cost_log.py now NVDA   one name, any time -> stdout
  COSTTEST=1 python3 cost_log.py self-test"""
import datetime as dt, json, os, sys, urllib.request
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); ET = ZoneInfo("America/New_York")
OUT = os.path.expanduser("~/.ledger-costs/quotes.jsonl")
BUCKETS = {"short": (3, 9), "long": (30, 60)}          # calendar days to expiry: the 3-9 day trades vs a 30-60 day alternative


def chain(sym, lo, hi):
    hd = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET"]}; out, tok = {}, None
    while True:
        u = (f"https://data.alpaca.markets/v1beta1/options/snapshots/{sym}?feed=indicative&limit=1000"
             f"&expiration_date_gte={lo}&expiration_date_lte={hi}" + (f"&page_token={tok}" if tok else ""))
        r = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=hd), timeout=30))
        out.update(r.get("snapshots") or {}); tok = r.get("next_page_token")
        if not tok: return out


def parse(occ):
    """NVDA261030C00210000 -> (root, '2026-10-30', 'C', 210.0)"""
    i = len(occ) - 15
    return occ[:i], f"20{occ[i:i+2]}-{occ[i+2:i+4]}-{occ[i+4:i+6]}", occ[i+6], int(occ[i+7:]) / 1000


def pick(snaps):
    """Nearest expiry; the at-the-money strike = where call mid - put mid is smallest (no stock quote needed); +-1 strike."""
    q = {}
    for k, v in snaps.items():
        b, a = (v.get("latestQuote") or {}).get("bp") or 0, (v.get("latestQuote") or {}).get("ap") or 0
        _, exp, right, strike = parse(k)
        q.setdefault(exp, {}).setdefault(strike, {})[right] = (k, b, a, (v.get("latestQuote") or {}).get("t"))
    if not q: return []
    exp = min(q); S = q[exp]
    both = [k for k, v in S.items() if "C" in v and "P" in v and v["C"][1] > 0 and v["P"][1] > 0]
    if not both: return []
    atm = min(both, key=lambda k: abs((S[k]["C"][1] + S[k]["C"][2]) / 2 - (S[k]["P"][1] + S[k]["P"][2]) / 2))
    ks = sorted(S); i = ks.index(atm)
    return [(exp, k, atm, S[k][r]) for k in ks[max(0, i - 1):i + 2] for r in "CP" if r in S[k]]


def rows(sym, now):
    out = []
    for bucket, (lo, hi) in BUCKETS.items():
        try: sel = pick(chain(sym, (now.date() + dt.timedelta(days=lo)).isoformat(), (now.date() + dt.timedelta(days=hi)).isoformat()))
        except Exception as e: print(f"  cost log {sym} {bucket}: {str(e)[:60]}"); continue
        for exp, k, atm, (occ, b, a, t) in sel:
            mid = (b + a) / 2 if b > 0 and a > 0 else None
            out.append(dict(ts=now.isoformat(timespec="minutes"), sym=sym, bucket=bucket, exp=exp, dte=(dt.date.fromisoformat(exp) - now.date()).days,
                            strike=k, atm=atm, right=occ[len(occ) - 9], bid=b, ask=a, mid=mid,
                            half_spread=round((a - b) / 2 / mid, 4) if mid else None, quote_t=t, feed="indicative"))
    return out


def selftest():
    fake = {"X261030C00100000": {"latestQuote": {"bp": 5, "ap": 5.2}}, "X261030P00100000": {"latestQuote": {"bp": 4.9, "ap": 5.1}},
            "X261030C00105000": {"latestQuote": {"bp": 2, "ap": 2.2}}, "X261030P00105000": {"latestQuote": {"bp": 7, "ap": 7.4}},
            "X261030C00095000": {"latestQuote": {"bp": 9, "ap": 9.4}}, "X261030P00095000": {"latestQuote": {"bp": 2.1, "ap": 2.3}},
            "X261106C00100000": {"latestQuote": {"bp": 6, "ap": 6.3}}, "X261106P00100000": {"latestQuote": {"bp": 6, "ap": 6.3}}}
    p = pick(fake)
    assert parse("NVDA261030C00210000") == ("NVDA", "2026-10-30", "C", 210.0)
    assert {x[0] for x in p} == {"2026-10-30"} and p[0][2] == 100.0 and len(p) == 6, p
    print("cost log self-test ok")


if __name__ == "__main__":
    if os.environ.get("COSTTEST"): selftest(); sys.exit()
    now = dt.datetime.now(ET)
    if len(sys.argv) > 2 and sys.argv[1] == "now":
        for r in rows(sys.argv[2], now): print(json.dumps(r))
        sys.exit()
    if now.weekday() >= 5 or not (9, 35) <= (now.hour, now.minute) <= (15, 55): print("cost log: market closed"); sys.exit()
    try: syms = json.load(open(os.path.join(H, "docs", "focus.json")))["syms"]
    except Exception: syms = []
    syms = list(dict.fromkeys(["SPY", "QQQ"] + syms))
    os.makedirs(os.path.dirname(OUT), exist_ok=True); n = 0
    with open(OUT, "a") as f:
        for s in syms:
            for r in rows(s, now): f.write(json.dumps(r) + "\n"); n += 1
    print(f"cost log: {n} quotes for {len(syms)} names -> {OUT}")
