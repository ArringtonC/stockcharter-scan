#!/usr/bin/env python3
"""One-time Discord layout for the OOZEMeter server, run by YOU in a terminal.

  COMMUNITY: #general  #oozebot
  STOCKBOT:  #market-report  #trades  #trade-updates  #ledger

Creates what is missing, moves what exists (no duplicates), makes a "Ledger" webhook in
each STOCKBOT channel, and writes the four webhook URLs into ~/.stockcharter.env and the
GitHub secrets. Uses OOZEBOT's token from ~/.hermes/.env (or asks for it, hidden).
OOZEBOT needs Manage Channels and Manage Webhooks on the server."""
import getpass, json, os, re, subprocess, urllib.request, urllib.error

GUILD = "1533631423498424490"          # OOZEMeter server (oozemeter/research/WEEKLY-CHANNELS.md)
LAYOUT = {"COMMUNITY": ["general", "oozebot"],
          "STOCKBOT": ["market-report", "trades", "trade-updates", "ledger"]}
ROUTE = {"market-report": "DISCORD_WEBHOOK_MARKET", "trades": "DISCORD_WEBHOOK_TRADES",
         "trade-updates": "DISCORD_WEBHOOK_UPDATES", "ledger": "DISCORD_WEBHOOK_LEDGER"}
ENVF = os.path.expanduser("~/.stockcharter.env")


def token():
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        for k in ("DISCORD_BOT_TOKEN", "DISCORD_TOKEN"):
            m = re.search(rf"^(?:export\s+)?{k}=['\"]?([^'\"\n]+)", open(p).read(), re.M)
            if m: return m.group(1)
    return getpass.getpass("OOZEBOT token (hidden): ").strip()


TOK = token()
def api(method, path, body=None):
    rq = urllib.request.Request("https://discord.com/api/v10" + path, method=method,
                                data=json.dumps(body).encode() if body is not None else None,
                                headers={"Authorization": f"Bot {TOK}", "Content-Type": "application/json",
                                         "User-Agent": "DiscordBot (stockcharter-scan, 1)"})
    try: r = urllib.request.urlopen(rq, timeout=20).read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Discord said {e.code} on {method} {path}: {e.read().decode()[:200]}\n"
                         "403 = OOZEBOT lacks a permission: Server Settings -> Roles -> its role -> "
                         "turn on Manage Channels and Manage Webhooks, then rerun.")
    return json.loads(r) if r else None


chans = api("GET", f"/guilds/{GUILD}/channels")
cat = {c["name"].upper(): c for c in chans if c["type"] == 4}
txt = {c["name"]: c for c in chans if c["type"] == 0}
hooks = {}
for cname, names in LAYOUT.items():
    if cname not in cat:
        cat[cname] = api("POST", f"/guilds/{GUILD}/channels", {"name": cname, "type": 4}); print(f"created category {cname}")
    for n in names:
        c = txt.get(n)
        if c is None:
            c = api("POST", f"/guilds/{GUILD}/channels", {"name": n, "type": 0, "parent_id": cat[cname]["id"]}); print(f"created #{n}")
        elif c.get("parent_id") != cat[cname]["id"]:
            api("PATCH", f"/channels/{c['id']}", {"parent_id": cat[cname]["id"]}); print(f"moved #{n} into {cname}")
        if n in ROUTE:
            h = next((w for w in api("GET", f"/channels/{c['id']}/webhooks") if w.get("name") == "Ledger" and w.get("token")), None) \
                or api("POST", f"/channels/{c['id']}/webhooks", {"name": "Ledger"})
            hooks[ROUTE[n]] = f"https://discord.com/api/webhooks/{h['id']}/{h['token']}"

lines = [l for l in open(ENVF).read().splitlines() if not any(l.startswith(f"export {k}=") for k in hooks)] if os.path.exists(ENVF) else []
lines += [f'export {k}="{v}"' for k, v in hooks.items()]
open(ENVF, "w").write("\n".join(lines) + "\n"); os.chmod(ENVF, 0o600)
print(f"wrote {len(hooks)} webhooks to {ENVF}")
for k, v in hooks.items():
    subprocess.run(["gh", "secret", "set", k, "--repo", "ArringtonC/stockcharter-scan"], input=v.encode(), check=False)
    urllib.request.urlopen(urllib.request.Request(v, data=json.dumps({"content": f"**Ledger** posts here: `{k.split('_')[-1].lower()}`"}).encode(),
                           headers={"Content-Type": "application/json", "User-Agent": "Ledger"}), timeout=20)
print("done: each STOCKBOT channel got a test post")
