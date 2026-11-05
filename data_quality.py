#!/usr/bin/env python3
"""List every record the old GitHub backup scan ("scan-bot") wrote, so verified history can be told apart from
unreliable history (ledger audit 2026-11-04). Deletes nothing. Writes docs/data-quality.json.

scan-bot ran 2-4 h late on UTC dates with stale prices: it overwrote the box's data, closed expiring options a day
early and wrote rows for days that were not sessions (or not yet over). Its schedule was removed on 2026-11-05."""
import datetime as dt, json, os, subprocess
from zoneinfo import ZoneInfo
H = os.path.dirname(os.path.abspath(__file__)); NY = ZoneInfo("America/New_York")
FILES = ["docs/signal-ledger.jsonl", "trades.csv", "docs/ihs-paper.json", "docs/history.json"]


def git(*a): return subprocess.run(["git", "-C", H, *a], capture_output=True, text=True).stdout


def added(c, f):
    """Lines scan-bot added to file f in commit c."""
    return [l[1:] for l in git("diff", f"{c}^", c, "--", f).splitlines() if l.startswith("+") and not l.startswith("+++")]


def history_days(c):
    """Dates scan-bot added or rewrote in docs/history.json in commit c."""
    def load(ref):
        try: return {x["date"]: x for x in json.loads(git("show", f"{ref}:docs/history.json") or "[]")}
        except Exception: return {}
    a, b = load(f"{c}^"), load(c)
    return sorted(d for d in b if b[d] != a.get(d))


def build():
    out = dict(generated=dt.datetime.now(NY).isoformat(timespec="minutes"), source="git author scan-bot", commits=[])
    for line in git("log", "--author=scan-bot", "--format=%H %at").splitlines():
        c, t = line.split(); when = dt.datetime.fromtimestamp(int(t), dt.timezone.utc)
        files = [f for f in git("show", "--name-only", "--format=", c).split() if f]
        rec = dict(commit=c[:7], at_utc=when.isoformat(timespec="minutes"), at_new_york=when.astimezone(NY).isoformat(timespec="minutes"), files=files)
        for f in FILES:
            if f not in files: continue
            if f == "docs/history.json": rec["history_days"] = history_days(c)
            else: rec[os.path.basename(f)] = added(c, f)
        out["commits"].append(rec)
    out["summary"] = dict(commits=len(out["commits"]),
                          signal_ledger_rows=sum(len(r.get("signal-ledger.jsonl", [])) for r in out["commits"]),
                          trades_rows=sum(len(r.get("trades.csv", [])) for r in out["commits"]),
                          ihs_rows=sum(len(r.get("ihs-paper.json", [])) for r in out["commits"]),
                          history_days=sorted({d for r in out["commits"] for d in r.get("history_days", [])}))
    return out


if __name__ == "__main__":
    q = build(); json.dump(q, open(os.path.join(H, "docs", "data-quality.json"), "w"), indent=1)
    s = q["summary"]; assert s["commits"] > 0, "no scan-bot commits found"
    print(json.dumps({k: (v if k != "history_days" else f"{len(v)} days: {v[:3]} ... {v[-3:]}") for k, v in s.items()}))
