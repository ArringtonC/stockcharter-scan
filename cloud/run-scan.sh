#!/bin/bash
# The scan on the Central-time slots (the Mac's ~/.ledger-run.sh, for Linux). Runs every 15 minutes
# from the timer; scans once per due slot. The Mac is the backup when this box is down.
export PATH=/usr/local/bin:/usr/bin:/bin
cd /root/ledger || exit 1
slot=$(python3 - <<'PY'
import datetime as dt, os
from zoneinfo import ZoneInfo
now = dt.datetime.now(ZoneInfo("America/Chicago")); d = now.weekday()
times = {5: ["09:00"], 6: ["18:30"]}.get(d, ["07:00", "08:30", "10:00", "11:30", "13:00", "14:30", "16:30"])
due = [t for t in times if t <= now.strftime("%H:%M") and now - now.replace(hour=int(t[:2]), minute=int(t[3:])) < dt.timedelta(minutes=80)]
f = os.path.expanduser("~/.ledger-last-slot"); last = open(f).read().strip() if os.path.exists(f) else ""
key = f"{now:%F} {due[-1]}" if due else ""
if key and key != last: open(f, "w").write(key); print(key)
PY
)
set -a; . /root/.stockcharter.env; set +a
python3 delivery.py missed >/dev/null || echo "missed-slot check failed"   # every 15 min: a slot that never went out texts you once
[ -z "$slot" ] && exit 0
echo "=== $(date '+%F %T %Z') · slot $slot CT ==="
git pull --rebase --autostash -q -X theirs || echo "pull failed -- scanning anyway"
python3 autotrade.py sync || echo "autotrade sync failed"
python3 scan_daily.py 2>&1 | tee /tmp/ledger-scan.out
if [ "${PIPESTATUS[0]}" -ne 0 ]; then   # delivery check: log it; the 2nd failure of a slot texts you (delivery.py)
  python3 delivery.py fail "$slot" "$(grep -v '^[[:space:]]*$' /tmp/ledger-scan.out | grep -v '^LEDGER_SENT' | tail -1 | cut -c1-200)" || true
  echo "scan failed -- will retry in 15 min"; rm -f ~/.ledger-last-slot; exit 1
fi
python3 delivery.py ok "$slot" /tmp/ledger-scan.out || echo "delivery check failed"
python3 context.py || echo "context failed"                    # information only: structure + 6-month strength for the Focus names -> docs/context.json
python3 cost_log.py || echo "cost log failed"                 # read-only: live option bid/ask for the Focus names -> ~/.ledger-costs/quotes.jsonl
python3 autotrade.py || echo "autotrade failed"
python3 ticket_bot.py || echo "ticket bot failed"
python3 f_full.py || echo "full S&P scan failed"
python3 opportunities.py nightly || echo "opportunities failed"       # after the close only: copy V1 results, replay open plans -> docs/opportunities.jsonl                  # after the close only: Setup F on the full S&P 500 for the F FULL S&P book
python3 hedge_watch.py nightly || echo "hedge watch failed"             # paper only: forward log of the Phase-3 put hedge idea (V2 vs V1 vs random) -> docs/hedge-log.json
python3 calls_paper.py daily || echo "calls paper daily failed"   # paper only: new F episodes and G1 events -> queued call entries
python3 portfolio_manager.py || echo "portfolio manager failed"   # paper only: F V1, Shadow C, F CAP 70, F FULL S&P books
git add docs/ trades.csv
git diff --cached --quiet && { echo "no change"; exit 0; }
git commit -q -m "scan $(date -u '+%F %H:%M')"
git push -q || { git pull --rebase --autostash -q -X theirs && git push -q; } || echo "push failed -- kept locally"
echo "pushed"
