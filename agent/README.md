# Necesse Status Agent

Runs on the Pi every minute. Reads `necesse.service` state + `server.log`, writes a JSON status blob to a GitHub Gist. The site fetches that gist to show live data.

## One-time setup

### 1. Create the Gist

Go to https://gist.github.com, create a **secret** gist with a single file called `status.json` and any placeholder content (e.g. `{}`). Save and grab the gist ID from the URL — it's the hex string after your username.

### 2. Create a PAT

Must be a **classic** PAT — fine-grained tokens cannot write gists.

https://github.com/settings/tokens → **"Tokens (classic)"** → Generate new token (classic). Only check the **`gist`** scope. Copy the `ghp_...` token.

### 3. Install on the Pi

```bash
mkdir -p ~/necesse-server/status-agent
# From your laptop:
#   scp agent/agent.py dontcallmejames@RaspberryPi5:~/necesse-server/status-agent/
# Or paste it with: nano ~/necesse-server/status-agent/agent.py
chmod +x ~/necesse-server/status-agent/agent.py

# Env file (NOT committed anywhere)
cat > ~/necesse-server/status-agent/.env <<'EOF'
GIST_ID=PASTE_GIST_ID_HERE
GH_TOKEN=PASTE_PAT_HERE
EOF
chmod 600 ~/necesse-server/status-agent/.env
```

### 4. Smoke test

```bash
set -a; source ~/necesse-server/status-agent/.env; set +a
python3 ~/necesse-server/status-agent/agent.py
```

Should print the JSON status and update your gist. Check the gist page — `status.json` should now have real data.

### 5. Install the systemd timer

```bash
sudo cp ~/necesse-server/status-agent/necesse-status.service /etc/systemd/system/
sudo cp ~/necesse-server/status-agent/necesse-status.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now necesse-status.timer
```

### 6. Verify

```bash
systemctl list-timers necesse-status.timer
journalctl -u necesse-status.service -n 20
```

## Wiring the site

Get the gist **raw** URL — on the gist page click "Raw" on `status.json`. Strip the revision hash so the URL always points to latest:

```
https://gist.githubusercontent.com/<user>/<gist_id>/raw/status.json
```

Paste that into `STATUS_URL` in `index.html`.

## Caveats

- **Connect/disconnect regex is best-effort.** Log had no player-join examples when the agent was written. First time a real player joins, check `~/necesse-server/server.log` — if the line doesn't match one of the patterns in `CONNECT_RES` in `agent.py`, paste the line and we'll tighten the regex.
- **Gist CDN cache.** Raw URLs are cached by GitHub's CDN for ~1 min. The site fetches with `?t=<timestamp>` to bust it.
