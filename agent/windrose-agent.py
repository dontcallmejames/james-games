#!/usr/bin/env python3
"""
Windrose status agent — runs on the Ubuntu host, PATCHes windrose.json in
the shared gist. Reads service state, started_at, upstream ping, and pulls
name/max_players/invite_code/password flag from ServerDescription.json.

Player count is not parsed from journalctl — Windrose's log format (Unreal
R5 engine) doesn't emit a consistent player-connected line, so player_count
is reported as null for now.

Config via env (EnvironmentFile in the systemd unit):
  GIST_ID     gist to update (required)
  GH_TOKEN    GitHub classic PAT with 'gist' scope (required)
  GIST_FILE   file within the gist (default: windrose.json)
  CONFIG_PATH path to ServerDescription.json
              (default: /home/dontcallmejames/windrose-server/R5/ServerDescription.json)
  SERVICE     systemd unit (default: windrose.service)
  PING_HOST   ping reference for upstream latency (default: 1.1.1.1)
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "CONFIG_PATH",
    "/home/dontcallmejames/windrose-server/R5/ServerDescription.json",
))
SERVICE   = os.environ.get("SERVICE", "windrose.service")
PING_HOST = os.environ.get("PING_HOST", "1.1.1.1")
GIST_ID   = os.environ["GIST_ID"]
GH_TOKEN  = os.environ["GH_TOKEN"]
GIST_FILE = os.environ.get("GIST_FILE", "windrose.json")


def systemctl(*args):
    r = subprocess.run(["systemctl", *args], capture_output=True, text=True)
    return r.stdout.strip(), r.returncode


def service_state():
    active, _ = systemctl("is-active", SERVICE)
    ts, _ = systemctl("show", SERVICE, "--property=ActiveEnterTimestamp", "--value")
    return active, (ts or None)


def upstream_ping_ms(host: str = PING_HOST):
    try:
        r = subprocess.run(
            ["ping", "-c", "3", "-W", "2", "-q", host],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"min/avg/max/[^=]+=\s*[\d.]+/([\d.]+)/", r.stdout)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 1)
    except ValueError:
        return None


def read_config(path: Path = CONFIG_PATH):
    out = {
        "server_name": None,
        "max_players": None,
        "invite_code": None,
        "password_protected": None,
    }
    try:
        data = json.loads(path.read_text())
    except Exception:
        return out
    persist = data.get("ServerDescription_Persistent") if isinstance(data, dict) else None
    if not isinstance(persist, dict):
        return out
    out["server_name"]        = persist.get("ServerName")
    out["max_players"]        = persist.get("MaxPlayerCount")
    out["invite_code"]        = persist.get("InviteCode")
    out["password_protected"] = persist.get("IsPasswordProtected")
    return out


def build_status():
    active, started = service_state()
    online = active == "active"
    windrose = {
        "status": "online" if online else "offline",
        "service_state": active,
        "started_at": started,
        # Log format doesn't currently support reliable player count extraction.
        "player_count": None,
        "online_players": [],
        "upstream_ping_ms": upstream_ping_ms(),
        "upstream_ping_target": PING_HOST,
    }
    windrose.update(read_config())
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "windrose": windrose,
    }


def patch_gist(status):
    body = json.dumps({
        "files": {GIST_FILE: {"content": json.dumps(status, indent=2)}}
    }).encode()
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        data=body,
        method="PATCH",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "windrose-status-agent",
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
