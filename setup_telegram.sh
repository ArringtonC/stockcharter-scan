#!/bin/zsh
# Turns on push alerts from the scan. Runs on GitHub's servers, not your Mac.
# The token goes straight from your terminal into ~/.stockcharter.env and GitHub.
# ponytail: finds the chat id for you instead of making you read JSON.
set -e
ENVF=~/.stockcharter.env
REPO=ArringtonC/stockcharter-scan

# This script asks you to type a secret, so it needs a real terminal. Run through
# Claude Code's "!" passthrough there is no TTY and the prompts read empty.
if [ ! -t 0 ]; then
  cat <<'MSG'
This needs a real terminal — it asks you to paste a token.

Open Terminal (Cmd+Space, type Terminal) and run:

    cd ~/Desktop/Projects/stockcharter-scan && ./setup_telegram.sh

MSG
  exit 1
fi

echo "Step 1 — in Telegram, message @BotFather, send /newbot, follow the prompts."
echo "It replies with a token that looks like 8123456789:AAF...xyz"
printf "Paste the token (hidden): "
read -rs TOK; echo

if [ -z "$TOK" ]; then echo "token required"; exit 1; fi

NAME=$(curl -s "https://api.telegram.org/bot$TOK/getMe" | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(d["result"]["username"] if d.get("ok") else "")')
if [ -z "$NAME" ]; then echo "that token did not work. Check it and rerun."; exit 1; fi
echo "Bot is @$NAME"

echo
echo "Step 2 — open Telegram, find @$NAME, and send it any message. Say hi."
printf "Press return once you have sent it: "
read -r _

CHAT=$(curl -s "https://api.telegram.org/bot$TOK/getUpdates" | python3 -c \
  'import sys,json
d=json.load(sys.stdin); r=d.get("result",[])
print(r[-1]["message"]["chat"]["id"] if r else "")')
if [ -z "$CHAT" ]; then
  echo "No message seen yet. Send the bot a message, then rerun this script."; exit 1
fi
echo "Found you: chat $CHAT"

umask 077
grep -v '^export TELEGRAM_' "$ENVF" 2>/dev/null > "$ENVF.tmp" || true
printf 'export TELEGRAM_TOKEN=%s\nexport TELEGRAM_CHAT=%s\n' "$TOK" "$CHAT" >> "$ENVF.tmp"
mv "$ENVF.tmp" "$ENVF"; chmod 600 "$ENVF"
echo "wrote $ENVF"

curl -s -o /dev/null -X POST "https://api.telegram.org/bot$TOK/sendMessage" \
  -d chat_id="$CHAT" -d parse_mode=HTML \
  -d text="<b>Ledger</b> is connected. You will get this twice each weekday."
echo "test message sent — check your phone"

printf "%s" "$TOK"  | gh secret set TELEGRAM_TOKEN --repo $REPO
printf "%s" "$CHAT" | gh secret set TELEGRAM_CHAT  --repo $REPO
echo "saved to GitHub — the scheduled scan will push from now on"
