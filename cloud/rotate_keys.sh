#!/bin/bash
# Rotate the Ledger's secrets without pasting them anywhere visible. Run on the Mac:  bash ~/.ledger/cloud/rotate_keys.sh
# It asks for each new value with echo off, writes ~/.stockcharter.env here and /root/.stockcharter.env on the box,
# and restarts the box's timers. Press Enter to keep a value unchanged. Nothing is printed or logged.
set -e; ENV=~/.stockcharter.env; BOX=root@178.105.37.149
for K in ALPACA_KEY ALPACA_SECRET DISCORD_LEDGER_BOT_TOKEN DISCORD_WEBHOOK DISCORD_WEBHOOK_MARKET DISCORD_WEBHOOK_TRADES DISCORD_WEBHOOK_UPDATES DISCORD_WEBHOOK_LEDGER DISCORD_WEBHOOK_PAPER DISCORD_WEBHOOK_WATCH TELEGRAM_TOKEN; do
  read -r -s -p "$K (Enter = keep): " V; echo
  [ -z "$V" ] && continue
  python3 - "$ENV" "$K" "$V" <<'PY'
import sys, re; f, k, v = sys.argv[1:]; s = open(f).read()
s = re.sub(rf'^export {k}=.*$', f'export {k}="{v}"', s, flags=re.M) if re.search(rf'^export {k}=', s, re.M) else s.rstrip("\n") + f'\nexport {k}="{v}"\n'
open(f, "w").write(s)
PY
done
chmod 600 "$ENV"; scp -q "$ENV" "$BOX:/root/.stockcharter.env" && ssh "$BOX" 'chmod 600 /root/.stockcharter.env; systemctl restart ledger-tm.timer ledger-scan.timer; cd /root/ledger && set -a && . /root/.stockcharter.env && set +a && python3 -c "import autotrade; print(\"alpaca ok:\", autotrade.call(\"/clock\")[\"is_open\"] in (True, False))"'
echo "done — now revoke the OLD keys in the Alpaca and Discord dashboards"
