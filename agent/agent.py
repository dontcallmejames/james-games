#!/usr/bin/env python3
"""
Necesse status agent — runs on the Pi, PATCHes a GitHub Gist with server state.

Config via env (EnvironmentFile in the systemd unit):
  GIST_ID     gist to update (required)
  GH_TOKEN    GitHub PAT with 'gist' scope (required)
  LOG_PATH    defaults to /home/dontcallmejames/necesse-server/server.log
  SERVICE     defaults to necesse.service
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(os.environ.get("LOG_PATH", "/home/dontcallmejames/necesse-server/server.log"))
SERVICE  = os.environ.get("SERVICE", "necesse.service")
GIST_ID  = os.environ["GIST_ID"]
GH_TOKEN = os.environ["GH_TOKEN"]


def systemctl(*args):
    r = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


def service_state():
    active, _ = systemctl("is-active", SERVICE)
    ts, _ = systemctl("show", SERVICE, "--property=ActiveEnterTimestamp", "--value")
    return active, (ts or None)


# Confirmed Necesse 1.2.0 log formats:
#   Client "Punkass" connected on slot 1/5.
#   Player 76561197974989386 ("Punkass") disconnected with message: Quit
CONNECT_RE    = re.compile(r'Client\s+"([^"]+)"\s+connected on slot\s+(\d+)/(\d+)')
DISCONNECT_RE = re.compile(r'Player\s+\S+\s+\("([^"]+)"\)\s+disconnected')
EMPTY_HINT = re.compile(r"Suggesting garbage collection due to empty server")
START_LINE = re.compile(
    r"Started server using port (\d+) with (\d+) slots on world \"([^\"]+)\""
)


def parse_players(log_path: Path):
    out = {"online_players": [], "player_count": None, "max_players": None, "world": None, "port": None}
    try:
        text = log_path.read_text(errors="replace")
    except FileNotFoundError:
        return out

    lines = text.splitlines()

    # Find the most recent server start; only scan log entries after it.
    start_idx = 0
    for i, ln in enumerate(lines):
        m = START_LINE.search(ln)
        if m:
            start_idx = i
            out["port"] = int(m.group(1))
            out["max_players"] = int(m.group(2))
            out["world"] = m.group(3)

    active = set()
    for ln in lines[start_idx:]:
        if EMPTY_HINT.search(ln):
            active.clear()
            continue
        m = CONNECT_RE.search(ln)
        if m:
            active.add(m.group(1))
            # Connect line also reports live slot count; use it to keep max fresh.
            out["max_players"] = int(m.group(3))
            continue
        m = DISCONNECT_RE.search(ln)
        if m:
            active.discard(m.group(1))

    out["online_players"] = sorted(active)
    out["player_count"] = len(active)
    return out


def build_status():
    active, started = service_state()
    online = active == "active"
    status = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "necesse": {
            "status": "online" if online else "offline",
            "service_state": active,
            "started_at": started,
        },
    }
    status["necesse"].update(parse_players(LOG_PATH))
    return status


def patch_gist(status):
    body = json.dumps({
        "files": {"status.json": {"content": json.dumps(status, indent=2)}}
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "necesse-status-agent",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            print(f"gist update failed: HTTP {r.status}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    s = build_status()
    patch_gist(s)
    print(json.dumps(s, indent=2))
