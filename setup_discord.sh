#!/bin/zsh
# Adds a Discord channel to the Ledger alerts. The webhook URL goes from your
# terminal straight into ~/.stockcharter.env; Claude never sees it.
#   Discord: channel -> Edit Channel -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL
set -e
ENVF=~/.stockcharter.env
[ -t 0 ] || { echo "run this in a real terminal: ./setup_discord.sh"; exit 1; }
printf "Paste the webhook URL (hidden): "; read -rs U; echo
case "$U" in https://discord.com/api/webhooks/*|https://discordapp.com/api/webhooks/*) ;; *) echo "that is not a Discord webhook URL"; exit 1;; esac
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"content":"**Ledger** is connected. Premarket and trade reports will post here."}' "$U")
[ "$code" = "204" ] || { echo "test post FAILED (HTTP $code). Nothing saved."; exit 1; }
grep -v '^export DISCORD_WEBHOOK=' "$ENVF" > "$ENVF.tmp" 2>/dev/null || true
printf 'export DISCORD_WEBHOOK="%s"\n' "$U" >> "$ENVF.tmp"; mv "$ENVF.tmp" "$ENVF"; chmod 600 "$ENVF"
echo "saved to $ENVF and a test message was posted"
printf "%s" "$U" | gh secret set DISCORD_WEBHOOK --repo ArringtonC/stockcharter-scan && echo "saved to GitHub Actions secrets"
