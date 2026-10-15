#!/usr/bin/env python3
"""
Enshrouded status agent — runs on the Omarchy box, PATCHes enshrouded.json in
the shared gist. The server runs in Docker (mornedhels/enshrouded-server), so
state comes from `docker inspect`, and players come from a Steam A2S_INFO
query against the server's query port.

Config via env (EnvironmentFile in the systemd unit):
  GIST_ID     gist to update (required)
  GH_TOKEN    GitHub classic PAT with 'gist' scope (required)
  GIST_FILE   file within the gist (default: enshrouded.json)
  CONTAINER   docker container name (default: enshrouded)
  QUERY_HOST  A2S query host (default: 127.0.0.1)
  QUERY_PORT  A2S query port (default: 15637)
  PING_HOST   ping reference for upstream latency (default: 1.1.1.1)
"""

import json
import os
import re
import socket
import struct
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

CONTAINER  = os.environ.get("CONTAINER", "enshrouded")
QUERY_HOST = os.environ.get("QUERY_HOST", "127.0.0.1")
QUERY_PORT = int(os.environ.get("QUERY_PORT", "15637"))
PING_HOST  = os.environ.get("PING_HOST", "1.1.1.1")
GIST_ID    = os.environ["GIST_ID"]
GH_TOKEN   = os.environ["GH_TOKEN"]
GIST_FILE  = os.environ.get("GIST_FILE", "enshrouded.json")


def container_state():
    """Return (running, started_at) where started_at is 'YYYY-MM-DD HH:MM:SS UTC'
    — the systemd-style format the site's parseSystemdTs() understands."""
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}} {{.State.StartedAt}}", CONTAINER],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False, None
    running, started = r.stdout.strip().split(" ", 1)
    try:
        dt = datetime.fromisoformat(started[:19]).replace(tzinfo=timezone.utc)
        started = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        started = None
    return running == "true", started


def a2s_info(host=QUERY_HOST, port=QUERY_PORT, timeout=3.0):
    """Steam A2S_INFO query. Returns dict or None if the server doesn't answer."""
    req = b"\xFF\xFF\xFF\xFFTSource Engine Query\x00"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(req, (host, port))
        data, _ = s.recvfrom(4096)
        if data[4] == 0x41:  # challenge — resend with it appended
            s.sendto(req + data[5:9], (host, port))
            data, _ = s.recvfrom(4096)
    except OSError:
        return None
    finally:
        s.close()
    if data[4] != 0x49:
        return None

    pos = 6
    def cstr():
        nonlocal pos
        end = data.index(b"\x00", pos)
        val = data[pos:end].decode(errors="replace")
        pos = end + 1
        return val

    name, _map, _folder, _game = cstr(), cstr(), cstr(), cstr()
    _appid, players, max_players, _bots = struct.unpack_from("<HBBB", data, pos)
    return {"name": name, "player_count": players, "max_players": max_players}


def upstream_ping_ms(host: str = PING_HOST) -> float | None:
    """Ping a reference host and return average RTT in ms."""
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
    return round(float(m.group(1)), 1) if m else None


def build_status():
    running, started = container_state()
    info = a2s_info() if running else None
    if info:
        state = "online"
    elif running:
        state = "maintenance"  # container up, server not answering (updating/restarting)
    else:
        state = "offline"

    s = {
        "status": state,
        "container_running": running,
        "started_at": started,
        "player_count": info["player_count"] if info else None,
        "max_players": info["max_players"] if info else None,
        "server_name": info["name"] if info else None,
        "upstream_ping_ms": upstream_ping_ms(),
        "upstream_ping_target": PING_HOST,
    }
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "enshrouded": s}


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
            "User-Agent": "enshrouded-status-agent",
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
