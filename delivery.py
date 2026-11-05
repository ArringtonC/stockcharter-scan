#!/usr/bin/env python3
"""Delivery check for the scan slots (reviews/friday-failure-and-delivery-fix.md). Standalone on purpose:
it must still work when scan_daily.py itself is what crashes.

  delivery.py fail  SLOT "last error line"   log a failed run; text you on the 2nd failure of the same slot
  delivery.py ok    SLOT SCAN_OUTPUT_FILE     log a good run; text you if a must-send slot sent nothing
  delivery.py missed                          every 15 min: text you once for any slot today that never went out
  delivery.py week                            missed / failed / late counts for the last 7 days
  delivery.py selftest                        assert-based check, sends nothing

LEDGER_ALERT_DRY=1 prints alerts instead of sending them."""
import datetime as dt, json, os, re, sys, urllib.parse, urllib.request
LOG = os.environ.get("LEDGER_DELIVERY_LOG", os.path.expanduser("~/.ledger-delivery.jsonl"))
MUST = {"16:30"}        # the closing run always sends (scan_daily should_send: closing -> True); other slots may rightly stay quiet


def rows():
    try: return [json.loads(l) for l in open(LOG) if l.strip()]
    except FileNotFoundError: return []


def log(**r):
    r["ts"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a") as f: f.write(json.dumps(r) + "\n")


def alert(text):
    """Telegram + the Discord updates channel, straight from the env (no scan_daily import)."""
    if os.environ.get("LEDGER_ALERT_DRY"): print("ALERT (dry):", text); return True
    ok = False
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT")
    if tok and chat:
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage",
                                   urllib.parse.urlencode(dict(chat_id=chat, text=text)).encode(), timeout=20).read(); ok = True
        except Exception as e: print("telegram alert failed:", str(e)[:80])
    url = os.environ.get("DISCORD_WEBHOOK_UPDATES") or os.environ.get("DISCORD_WEBHOOK")
    if url:
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(dict(content=text[:1900])).encode(),
                                   headers={"Content-Type": "application/json", "User-Agent": "Ledger delivery"}), timeout=20).read(); ok = True
        except Exception as e: print("discord alert failed:", str(e)[:80])
    return ok


def fails_in_a_row(slot):
    n = 0
    for r in reversed([r for r in rows() if r.get("slot") == slot]):
        if r["status"] != "failed": break
        n += 1
    return n


def on_fail(slot, err):
    log(slot=slot, status="failed", err=err[:300]); n = fails_in_a_row(slot)
    if n == 2:                                       # once per slot, not on every retry
        alert(f"Ledger: the {slot[11:]} CT run failed twice — {err[:200]}. Reports from this run may be missing. "
              "It keeps retrying every 15 minutes.")
    return n


def sent_counts(text):
    m = re.findall(r"LEDGER_SENT ok=(\d+) failed=(\d+)", text)
    return (int(m[-1][0]), int(m[-1][1])) if m else (None, None)


def on_ok(slot, out_file):
    try: text = open(out_file, errors="ignore").read()
    except FileNotFoundError: text = ""
    ok, bad = sent_counts(text); log(slot=slot, status="ok", sent=ok, send_failed=bad)
    if slot[11:] in MUST and ok == 0:
        alert(f"Ledger: the {slot[11:]} CT run finished but no message went out ({bad} sends failed). Check the bot.")
        return "silent"
    return "ok"


def schedule(day):
    """The Central-time slots (same as cloud/run-scan.sh)."""
    return {5: ["09:00"], 6: ["18:30"]}.get(day.weekday(), ["07:00", "08:30", "10:00", "11:30", "13:00", "14:30", "16:30"])


def missed(now=None):
    """Any slot today whose 80-minute retry window has passed with no good run: one text per slot.
    Covers a slot that failed once and was never retried, or never started. Slots before the first log line are ignored."""
    from zoneinfo import ZoneInfo
    now = now or dt.datetime.now(ZoneInfo("America/Chicago")); R = rows()
    if not R: return []
    first = min(r["ts"] for r in R); out = []
    for t in schedule(now.date()):
        key = f"{now:%F} {t}"; start = now.replace(hour=int(t[:2]), minute=int(t[3:]), second=0, microsecond=0)
        if now - start < dt.timedelta(minutes=80) or start.isoformat()[:19] < first[:19]: continue
        mine = [r for r in R if r.get("slot") == key]
        if any(r["status"] in ("ok", "missed_alerted") for r in mine): continue
        why = "failed and was not retried" if any(r["status"] == "failed" for r in mine) else "never started (box or timer down?)"
        alert(f"Ledger: the {t} CT report did not go out — it {why}."); log(slot=key, status="missed_alerted"); out.append(t)
    return out


def week(now=None):
    now = now or dt.datetime.now(); cut = (now - dt.timedelta(days=7)).isoformat()
    R = [r for r in rows() if r.get("ts", "") >= cut]; slots = {r["slot"] for r in R}
    good = {r["slot"] for r in R if r["status"] == "ok"}
    silent = sum(1 for r in R if r["status"] == "ok" and r.get("sent") == 0 and r["slot"][11:] in MUST)
    retried = sum(1 for s in good if any(r["slot"] == s and r["status"] == "failed" for r in R))
    return dict(slots=len(slots), ok=len(good), never_ran_ok=len(slots - good), recovered_after_retry=retried, ran_but_sent_nothing=silent,
                missed_alerts=sum(1 for r in R if r["status"] == "missed_alerted"))


def selftest():
    import tempfile
    global LOG
    LOG = tempfile.mktemp(); os.environ["LEDGER_ALERT_DRY"] = "1"
    assert on_fail("2026-11-02 16:30", "TypeError: 25 - None") == 1
    assert on_fail("2026-11-02 16:30", "TypeError: 25 - None") == 2      # alert fires here
    assert on_fail("2026-11-02 16:30", "TypeError: 25 - None") == 3      # no second alert
    f = tempfile.mktemp(); open(f, "w").write("...\nLEDGER_SENT ok=3 failed=0\n")
    assert on_ok("2026-11-02 16:30", f) == "ok"
    open(f, "w").write("discord failed: HTTP 404\nLEDGER_SENT ok=0 failed=2\n")
    assert on_ok("2026-11-03 16:30", f) == "silent"
    assert on_ok("2026-11-03 10:00", f) == "ok"                           # not a must-send slot
    w = week(); assert w["slots"] == 3 and w["recovered_after_retry"] == 1 and w["ran_but_sent_nothing"] == 1, w
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago"); open(LOG, "w").write(json.dumps(dict(slot="2026-11-04 07:00", status="ok", ts="2026-11-04T07:00:05")) + "\n")
    log(slot="2026-11-04 08:30", status="failed", err="x")               # failed once, never retried
    assert missed(dt.datetime(2026, 11, 4, 10, 30, tzinfo=CT)) == ["08:30"]   # 10:00 still inside its window
    assert missed(dt.datetime(2026, 11, 4, 11, 25, tzinfo=CT)) == ["10:00"]   # 10:00 never started; 08:30 not re-sent
    assert missed(dt.datetime(2026, 11, 4, 11, 30, tzinfo=CT)) == []
    print("selftest ok", w)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["fail"]: print(on_fail(a[1], a[2] if len(a) > 2 else "unknown error"))
    elif a[:1] == ["ok"]: print(on_ok(a[1], a[2]))
    elif a[:1] == ["missed"]: print(missed())
    elif a[:1] == ["week"]: print(json.dumps(week()))
    elif a[:1] == ["selftest"]: selftest()
    else: print(__doc__)
