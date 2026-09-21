#!/bin/zsh
# The reliable path. GitHub's cron runs this 2-4 hours late every time -- it is
# best-effort on public repos -- so the Mac drives the schedule and GitHub stays
# as the backstop for when the Mac is off. Duplicate runs are harmless: the alert
# only sends when something changed.
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
cd "$(dirname "$0")" || exit 1
[ -f ~/.stockcharter.env ] && source ~/.stockcharter.env

log=~/Library/Logs/ledger-scan.log
exec >> "$log" 2>&1
echo "=== $(date '+%F %T %Z') ==="

git pull --rebase --autostash -q || { echo "pull failed"; exit 1; }
python3 scan_daily.py || { echo "scan failed"; exit 1; }

git add docs/ trades.csv
git diff --cached --quiet && { echo "no change"; exit 0; }
git commit -q -m "scan $(date -u '+%F %H:%M')"
git push -q || { git pull --rebase --autostash -q && git push -q; }
echo "pushed"
