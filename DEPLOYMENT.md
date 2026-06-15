# Cloud Deployment Runbook — OrderFlowDaemon on GCP (Debian 12, from source)

Run the headless `OrderFlowDaemon` 24/7 on a GCP VM; keep `OrderFlowTerminal` local
and reach the daemon over an SSH tunnel. The daemon binds **`127.0.0.1:9999` on the
VM only** (never the public internet) — the SSH local-forward is the entire security
boundary, so **no TLS, no auth, and no firewall rule for 9999** are needed.

| Setting | Value |
|---------|-------|
| Project | `yass-chart` |
| Zone | `europe-west9-b` |
| VM | `smc-quant-eu` (`e2-standard-2`, Debian 12 bookworm) |
| VM user | `john_doe` ← **replace with your real username everywhere** |
| Code dir on VM | `/home/john_doe/OrderFlowPlatform` |

> Debian 12 gotcha: system pip is "externally managed" (PEP 668) — `pip install` to
> system Python is blocked. We use a **venv**, which sidesteps it cleanly.
> The daemon needs outbound HTTPS/WSS to `fapi`/`fstream.binance.com`; GCP default
> egress allows this as long as the VM has an external IP (or Cloud NAT).

Prerequisite: `gcloud` installed + authenticated locally (`gcloud auth login`), and
SSH to the VM already works (`gcloud compute ssh ...` provisions keys automatically).

---

## Step 1 — Prep the VM (run locally)

Installs Python + venv and creates the code dir (must exist before the upload):

```bash
gcloud compute ssh john_doe@smc-quant-eu --project=yass-chart --zone=europe-west9-b --command="\
  sudo apt-get update && \
  sudo apt-get install -y python3 python3-venv python3-pip && \
  mkdir -p /home/john_doe/OrderFlowPlatform && \
  echo 'VM PREPPED'"
```

## Step 2 — Upload the code (run locally, from the project root)

```bash
gcloud compute scp --recurse --project=yass-chart --zone=europe-west9-b \
  app data requirements-daemon.txt run_daemon.py \
  john_doe@smc-quant-eu:/home/john_doe/OrderFlowPlatform/
```

> `data/` seeds the cloud with your local history (the daemon instant-rehydrates
> from `data/history.db`). Stop the local daemon first so the WAL is consistent.
> Prefer a clean start? Drop `data` from the command — the daemon auto-creates a
> fresh `data/history.db` and builds history live.

## Step 3 — venv, dependencies, and the systemd service (on the VM)

SSH in interactively:

```bash
gcloud compute ssh john_doe@smc-quant-eu --project=yass-chart --zone=europe-west9-b
```

Then paste this block on the VM:

```bash
cd /home/john_doe/OrderFlowPlatform
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-daemon.txt

sudo tee /etc/systemd/system/orderflow.service > /dev/null <<'EOF'
[Unit]
Description=OrderFlow Quant Daemon (SOLUSDT order-flow core)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=john_doe
Group=john_doe
WorkingDirectory=/home/john_doe/OrderFlowPlatform
ExecStart=/home/john_doe/OrderFlowPlatform/venv/bin/python run_daemon.py
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now orderflow.service
sudo systemctl status orderflow.service --no-pager
```

Why these matter:
- `Restart=always` + `enable` → survives crashes **and** VM reboots.
- `KillSignal=SIGINT` → `systemctl stop` raises `KeyboardInterrupt`, so the daemon
  runs its **final SQLite flush** on shutdown (plain SIGTERM would skip it).
- `PYTHONUNBUFFERED=1` → boot logs reach `journalctl` immediately.
- Runs as `john_doe` (not root); `data/history.db` lives under the code dir.

## Step 4 — Verify

```bash
journalctl -u orderflow.service -f --no-pager
```

Expect `SQLITE REHYDRATE COMPLETE — N closed buckets armed (no tick replay)`
(or `COLD START …` on a fresh box) then `DAEMON LISTENING ON ('127.0.0.1', 9999)`.
`Ctrl+C` stops following (the service keeps running).

## Step 5 — Run the local terminal (the tunnel is automatic)

`OrderFlowTerminal` opens the SSH tunnel itself on boot — `SSHTunnelManager` in
`terminal.py`'s `main()` checks whether `127.0.0.1:9999` is already live and, if not,
launches `gcloud compute ssh ... -N -L 9999:127.0.0.1:9999` invisibly in the
background, then kills that whole process tree when you close the window. Daily use is
just:

```bash
python -m app.terminal
```

The chart reads "disconnected" for the few seconds the tunnel takes to establish, then
auto-connects (the worker retries every 2s). The tunnel identity lives at the top of
the tunnel section in `terminal.py` (currently `yassine.mdouari@smc-quant-eu`).

**Manual fallback** — if gcloud isn't on PATH, or to watch tunnel errors directly:

```bash
gcloud compute ssh <user>@smc-quant-eu --project=yass-chart --zone=europe-west9-b -- -N -L 9999:127.0.0.1:9999
```

---

## Ops cheatsheet

| Task | Command (on the VM unless noted) |
|------|----------------------------------|
| Push code update | (local) re-run Step 2's `scp`, then `sudo systemctl restart orderflow` |
| Restart / stop | `sudo systemctl restart orderflow` · `sudo systemctl stop orderflow` (flushes) |
| Live logs | `journalctl -u orderflow -f` |
| Status | `systemctl status orderflow` |
| Update deps | `./venv/bin/pip install -r requirements-daemon.txt` then restart |

Security: keep port 9999 off every GCP firewall rule — loopback bind + SSH tunnel
means it's never publicly reachable. Only SSH (22, via gcloud/IAP) needs to work.
