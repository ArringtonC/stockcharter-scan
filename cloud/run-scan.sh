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
[ -z "$slot" ] && exit 0
set -a; . /root/.stockcharter.env; set +a
echo "=== $(date '+%F %T %Z') · slot $slot CT ==="
git pull --rebase --autostash -q -X theirs || echo "pull failed -- scanning anyway"
python3 autotrade.py sync || echo "autotrade sync failed"
python3 scan_daily.py || { echo "scan failed -- will retry in 15 min"; rm -f ~/.ledger-last-slot; exit 1; }
python3 autotrade.py || echo "autotrade failed"
python3 ticket_bot.py || echo "ticket bot failed"
python3 shadow_c.py || echo "shadow c failed"   # paper only: the Test C exit beside the bot book
git add docs/ trades.csv
git diff --cached --quiet && { echo "no change"; exit 0; }
git commit -q -m "scan $(date -u '+%F %H:%M')"
git push -q || { git pull --rebase --autostash -q -X theirs && git push -q; } || echo "push failed -- kept locally"
echo "pushed"
