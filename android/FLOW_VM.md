# SMC Flow — the tablet engine in the cloud

The tablet app (`android/flowapp`, "SMC Flow") draws what `android/flow_engine.py` computes. At home the engine
runs on the PC and the tablet reaches it over the USB cable (`adb reverse tcp:8766 tcp:8766`). Away from the PC
the same engine runs on its own VM next to the daemon, and the app reaches it over Wi-Fi / 4G. The app tries USB
first, then the VM, forever (`EngineClient`), so nothing has to be switched by hand.

## The VM -- the engine runs ON THE DAEMON'S BOX since 2026-09-23

| | |
|---|---|
| name / zone | `smc-quant-eu` / `europe-west9-b` (project `yass-chart`) -- the daemon's box, shared |
| machine | `e2-medium` (2 shared vCPU, 1 sustained; 4 GB), disk 20 GB pd-balanced |
| external IP | `34.155.14.220`, **reserved static** (`smc-quant-ip`) -- also the DOM app's `feed.vmhost` |
| login | `gcloud compute ssh smc-quant-eu --project=yass-chart --zone=europe-west9-b` (user `yassine_mdouari`) |
| code | `/home/yassine_mdouari/smcflow/` = `app/`, `android/flow_engine.py`, `data/terminal_ui.json` |
| venv | `/home/yassine_mdouari/venv` -- **`PySide6_Essentials`** 6.11.1 (NOT full PySide6: the add-ons are ~400 MB and the only add-on imports, QtMultimedia / QtTextToSpeech in `app/alerts.py`, are optional), pyqtgraph 0.14.0, numpy, pandas, websockets, requests (437 MB) |
| system libs | `libgl1 libegl1 libxkbcommon0 fonts-dejavu-core` (Qt offscreen on a headless Debian) |
| firewall | rule `smc-flow-8766` (tcp:8766 from anywhere, tag `allow-flow`, now on `smc-quant-eu`) -- the token is the gate |

**History.** 2026-09-22 the engine got its own VM (`smc-flow-eu`, e2-small, `34.155.59.129`) because the daemon's
e2-small was memory-starved. 2026-09-23 the daemon's box froze during a sell-off burst and was resized to e2-medium;
a merge test the same day (a second engine beside the daemon, the daemon feeding TWO engines, a tablet-like client
panning every 60 s for 15 min) held the daemon at 3-7 s behind (16 s for ~20 s at the engine's boot), machine ~35%
busy, 0-0.9% steal, 1.7-2.0 GB free. So the engine moved onto the daemon's box, `smc-flow-eu` and its IP were
deleted, the disk grown 10 -> 20 GB: one VM instead of two, ~$17/month less.

## The service

No tunnel any more: the daemon binds `127.0.0.1:9999` on this same box, and the engine dials it directly.

`smcflow.service` -- `~/venv/bin/python -W ignore android/flow_engine.py --listen 0.0.0.0 --port 8766 --auth <token>
--compress --lean --no-tunnel`, `Restart=always`, `QT_QPA_PLATFORM=offscreen`, `After=orderflow.service`.
⚠ **The daemon comes first on this box:** `MemoryMax=1200M` (the engine settles ~330-470 MB, up to ~920 MB while
a 72 h backfill lands) and `CPUWeight=50` (half the daemon's weight -- under contention the daemon wins). Flags:

- `--auth` the client's first line must be `{"t":"auth","k":"<token>","z":1}` within 6 s (the DOM bridge's handshake);
- `--compress` with `z=1` everything the engine sends is one zlib stream (~12x on the wire);
- `--lean` caps the bucket scrollback and chart cache the tablet never sees;
- `--no-tunnel` never launches gcloud; dials 127.0.0.1:9999 (the daemon on the same box);
- `--debug` (not on the VM) enables the `shot` / `series` / `refetch` / `bfstate` probe commands.

The token is generated once (`secrets.token_hex(16)`) and lives in the unit file on the VM and in
`android/local.properties` on the PC as `flow.token=` (with `flow.vmhost=34.155.14.220`) -- both gitignored.
`flowapp/build.gradle` bakes them into `BuildConfig.VM_HOST` / `BuildConfig.FLOW_TOKEN`.

```
systemctl status orderflow smcflow smcbridge
sudo journalctl -u smcflow -f -o cat
```

## Updating the engine

From the repo on the PC (one file per `gcloud compute scp` -- a two-file scp fails silently):

```
gcloud compute scp android/flow_engine.py smc-quant-eu:/tmp/flow_engine.py --project=yass-chart --zone=europe-west9-b
gcloud compute ssh smc-quant-eu --project=yass-chart --zone=europe-west9-b --command "sudo cp /tmp/flow_engine.py /home/yassine_mdouari/smcflow/android/ && sudo chown yassine_mdouari: /home/yassine_mdouari/smcflow/android/flow_engine.py && sudo systemctl restart smcflow"
```

When `app/` changed, ship the changed files the same way into `/home/yassine_mdouari/smcflow/app/`. The engine
reads the terminal's settings (lookback, flow window, layer toggles) from `data/terminal_ui.json` at boot: after
changing them on the PC, ship that file too and restart. The boot takes ~15-20 s; the tablet reconnects on its own.
⚠ Every engine restart makes the DAEMON serve a 72 h history load -- batch deploys, do not restart casually
(the 2026-09-23 outage, memory note `daemon-outage-2026-09-23`).

## Changing the token or the IP

New token: edit `--auth` in `/etc/systemd/system/smcflow.service` (`sudo systemctl daemon-reload && sudo systemctl
restart smcflow`), put the same value in `android/local.properties`, rebuild and reinstall the app. A new IP (only
if the reserved address is ever released): `flow.vmhost` AND the DOM app's `feed.vmhost` in `local.properties`,
rebuild, reinstall both apps.


## ⚠ The box needs swap (learned the hard way on the old `smc-flow-eu`, 2026-09-22 -- the daemon's box has had swap since 2026-08-24)

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
  the 72 h backfill lands, then settles near 470 MB (330-385 MB measured on the merged box).
- One client at a time: a second connection (a probe from the PC, a second tablet) kicks the first.
- `BIGPRINT ARCHIVE REFRESH ERROR ... study/bigprint_archive.py` in the journal is expected: the VM has no
  big-print archive (it would download Binance monthly files); the Big Player marks come from the live tape.
- The cloud engine is a second instance (besides a PC engine, if one runs) with its own flow bins. Since the window-replace fix (`FlowStore.ingest_window`)
  both instances rebuild identically from the daemon's tape; before it they drifted apart (see the memory note
  `flow-bins-double-count`).
