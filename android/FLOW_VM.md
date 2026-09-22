# SMC Flow — the tablet engine in the cloud

The tablet app (`android/flowapp`, "SMC Flow") draws what `android/flow_engine.py` computes. At home the engine
runs on the PC and the tablet reaches it over the USB cable (`adb reverse tcp:8766 tcp:8766`). Away from the PC
the same engine runs on its own VM next to the daemon, and the app reaches it over Wi-Fi / 4G. The app tries USB
first, then the VM, forever (`EngineClient`), so nothing has to be switched by hand.

## The VM

| | |
|---|---|
| name / zone | `smc-flow-eu` / `europe-west9-b` (project `yass-chart`) |
| machine | `e2-small` (2 shared vCPU, 2 GB) — the engine sits at ~470 MB RSS, load ~0.5 |
| external IP | `34.155.59.129`, **reserved static** (`smc-flow-ip`), so a stop/start keeps it |
| internal IP | `10.200.0.3`; the daemon box `smc-quant-eu` is `10.200.0.2` |
| login | `gcloud compute ssh smc-flow-eu --project=yass-chart --zone=europe-west9-b` (user `yassine_mdouari`) |
| code | `~/smcflow/` = `app/`, `android/flow_engine.py`, `data/terminal_ui.json`, `requirements.txt` |
| venv | `~/venv` (PySide6 6.11.1, pyqtgraph 0.14.0, numpy, pandas, websockets, requests) |
| firewall | rule `smc-flow-8766` (tcp:8766 from anywhere, tag `allow-flow`) — the token is the gate |

Why its own VM: the daemon's box (`smc-quant-eu`, e2-small) runs at ~1.4 GB RSS with swap in use; the engine is
a whole offscreen terminal and would not fit beside it.

## The two services

`smcflow-tunnel.service` — `ssh -N -L 127.0.0.1:9999:127.0.0.1:9999 yassine.mdouari@10.200.0.2` with keepalives,
`Restart=always`. The engine VM's key (`~/.ssh/id_ed25519.pub`) is in `/home/yassine.mdouari/.ssh/authorized_keys`
on the daemon box. The daemon keeps binding `127.0.0.1:9999` only — the tunnel is the boundary, as for the PC.

`smcflow.service` — `~/venv/bin/python -W ignore android/flow_engine.py --listen 0.0.0.0 --port 8766 --auth <token>
--compress --lean --no-tunnel`, `Restart=always`, `MemoryMax=1700M`, `QT_QPA_PLATFORM=offscreen`. Flags:

- `--auth` the client's first line must be `{"t":"auth","k":"<token>","z":1}` within 6 s (the DOM bridge's handshake);
- `--compress` with `z=1` everything the engine sends is one zlib stream (~12x on the wire);
- `--lean` caps the bucket scrollback and chart cache the tablet never sees;
- `--no-tunnel` never launches gcloud; waits for the tunnel service's port instead;
- `--debug` (not on the VM) enables the `shot` / `series` / `refetch` / `bfstate` probe commands.

The token is generated once (`secrets.token_hex(16)`) and lives in the unit file on the VM and in
`android/local.properties` on the PC as `flow.token=` (with `flow.vmhost=34.155.59.129`) — both gitignored.
`flowapp/build.gradle` bakes them into `BuildConfig.VM_HOST` / `BuildConfig.FLOW_TOKEN`.

```
systemctl status smcflow smcflow-tunnel
sudo journalctl -u smcflow -f -o cat
```

## Updating the engine

From the repo on the PC:

```
gcloud compute scp android/flow_engine.py smc-flow-eu:smcflow/android/flow_engine.py --project=yass-chart --zone=europe-west9-b
gcloud compute ssh smc-flow-eu --project=yass-chart --zone=europe-west9-b --command "sudo systemctl restart smcflow"
```

When `app/` changed, ship it whole (a tar of `app/` without `__pycache__`, extract into `~/smcflow`). The engine
reads the terminal's settings (lookback, flow window, layer toggles) from `data/terminal_ui.json` at boot: after
changing them on the PC, ship that file too and restart. The boot takes ~15 s, the 72 h backfill a few minutes
more; the tablet reconnects on its own.

## Changing the token or the IP

New token: edit `--auth` in `/etc/systemd/system/smcflow.service` (`sudo systemctl daemon-reload && sudo systemctl
restart smcflow`), put the same value in `android/local.properties`, rebuild and reinstall the app. A new IP (only
if the reserved address is ever released): `flow.vmhost` in `local.properties`, rebuild, reinstall.


## ⚠ The box needs swap (learned the hard way, 2026-09-22)

Hours after it was created the VM WEDGED: the engine stopped accepting TCP (the tablet saw
`SocketTimeoutException ... after 6000ms`, not a refusal), SSH hung, and the guest agent logged
`DeadlineExceeded` / `CRASHED` plugin health checks. There was NO oom-kill in the serial console — the box
was thrashing, not out of memory. Cause: an e2-small (2 GB) running a whole offscreen terminal with no swap,
while `smcflow.service` carries `MemoryMax=1700M`; a cgroup that reaches its limit with nowhere to page
reclaims hard instead of failing fast. Recovery was `gcloud compute instances reset smc-flow-eu` (both units
are `enabled` + `Restart=always`, and the engine keeps no durable state — it works off a temp copy of
`data/terminal_ui.json`), then:

```
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo /sbin/mkswap /swapfile && sudo /sbin/swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
```

(`swapon`/`mkswap` live in `/sbin` and are NOT on the login PATH — use the absolute paths.) This is the same
safety net the daemon box has had since 2026-08-24. After it: 1,101 MB used, 2 GB swap free, disk 64%,
tablet redrawing at 8-9 ms a frame. If it wedges again the next step is fewer layers on the engine (HLH and
Big Player are the heavy ones) or e2-medium.
## Known limits

- The engine pings the client every 10 s from a thread of its own, because its GUI thread can stall for tens of
  seconds while a 6 h backfill chunk lands on the shared vCPU (the app's read timeout is 30 s), and its outbound
  queue holds ~3 min of ticks so a slow 4G downlink is not dropped. RSS moves between ~470 MB and ~920 MB while
  the 72 h backfill lands, then settles near 470 MB.
- One client at a time: a second connection (a probe from the PC, a second tablet) kicks the first.
- `BIGPRINT ARCHIVE REFRESH ERROR ... study/bigprint_archive.py` in the journal is expected: the VM has no
  big-print archive (it would download Binance monthly files); the Big Player marks come from the live tape.
- The VM engine is a second instance with its own flow bins. Since the window-replace fix (`FlowStore.ingest_window`)
  both instances rebuild identically from the daemon's tape; before it they drifted apart (see the memory note
  `flow-bins-double-count`).
