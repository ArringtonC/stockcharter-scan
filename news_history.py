#!/usr/bin/env python3
"""News history for the Focus page (2026-10-17): Google News headlines per company, one month at a time,
back to the start of the 2-year chart. The live feeds only reach two weeks back, which is why Meta's
Muse launch (2026-04-08) was missing. Stored in docs/focus-history.json; the scan refreshes the current
and previous month once a day, this script backfills the rest.
  python3 news_history.py            backfill every focus name (slow: one request per name per month)
  python3 news_history.py META       one name"""
import datetime as dt, json, os, sys, time, urllib.parse
from scan_daily import DOCS, COMPANY, _rss, focus_syms

PATH = os.path.join(DOCS, "focus-history.json")


def month_windows(months=24):
    today = dt.date.today(); y, m = today.year, today.month; out = []
    for _ in range(months):
        start = dt.date(y, m, 1); end = (start + dt.timedelta(days=32)).replace(day=1)
        out.append((start.isoformat(), end.isoformat())); m -= 1
        if m == 0: y, m = y - 1, 12
    return out


def fetch(sym, start, end, n=25):
    q = urllib.parse.quote(f"{COMPANY.get(sym, sym)} after:{start} before:{end}")
    ev = _rss(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", n)
    for e in ev: e["text"] = e["text"].rsplit(" - ", 1)[0] if " - " in e["text"] else e["text"]; e["src"] = "Google News"
    return ev


def load():
    return json.load(open(PATH)) if os.path.exists(PATH) else {}


def save(h):
    json.dump(h, open(PATH, "w"), separators=(",", ":"))


def refresh(h, syms, months):
    """Fetch the given month windows for each name, replacing those months. Dedupe by title."""
    for s in syms:
        rows = [e for e in h.get(s, {}).get("events", []) if not any(a <= e["date"] < b for a, b in months)]
        for a, b in months:
            try: rows += fetch(s, a, b)
            except Exception as ex: print(s, a, "failed", str(ex)[:50])
            time.sleep(1.0)
        seen = set(); uniq = []
        for e in sorted(rows, key=lambda e: e["date"], reverse=True):
            k = e["text"].lower()[:60]
            if k in seen: continue
            seen.add(k); uniq.append(e)
        h[s] = dict(refreshed=str(dt.date.today()), events=uniq); print(s, len(uniq))
    return h


if __name__ == "__main__":
    d = json.load(open(os.path.join(DOCS, "data.json")))
    syms = sys.argv[1:] or focus_syms(d, d.get("open", []))
    save(refresh(load(), syms, month_windows(24)))
