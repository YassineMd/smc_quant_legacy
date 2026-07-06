# Cold-archive: un-capped bucket history → GCS (for long-horizon backtests)

The daemon's `history.db` is capped at ~10k buckets/tf (a ~4-day rolling 1m window), so old buckets are
**deleted** and long backtests are impossible off the live DB. This archives every bucket into an
append-only cold store **before the cap deletes it** — contiguous, cheap, and fully decoupled from the
daemon (no daemon code change, no restart, zero added latency).

```
 VM (cron, read-only on history.db)                 Local (on demand)
 ────────────────────────────────────               ─────────────────────────────
 ops/archive_buckets.py  ──gsutil──▶  GCS bucket  ──rsync──▶  study/archive_data/
   • id > hwm[tf], WAL read-snapshot                          study/pull_archive.ps1
   • computes true per-tf bid                                 study/archive_loader.py
   • gzip NDJSON chunks                                         → (bids, raws, gaps)  ← same as
   • per-tf high-water-mark                                       load_local_tape, unchanged
```

Storage cost ≈ **$0.03/month per year of full multi-tf history**. Egress only when you pull locally.

---

## One-time setup

### 1. Create the bucket (same region as the VM → free VM↔bucket traffic)
```bash
gsutil mb -l europe-west9 gs://smc-quant-archive
```
If you pick another name, update `GCS_DEFAULT` in `ops/archive_buckets.py` **and** `$GCS` in
`study/pull_archive.ps1`.

### 2. Let the VM write to the bucket
The VM's service account needs **both** an IAM role **and** a write access-scope.

```bash
# a) IAM: grant the VM's service account object write on the bucket
VM_SA=$(gcloud compute instances describe smc-quant-eu --zone europe-west9-b \
        --project yass-chart --format='value(serviceAccounts[0].email)')
gsutil iam ch "serviceAccount:${VM_SA}:roles/storage.objectAdmin" gs://smc-quant-archive

# b) Access scope: the default GCE scope is often storage-READ-only. Check it:
gcloud compute instances describe smc-quant-eu --zone europe-west9-b --project yass-chart \
       --format='value(serviceAccounts[0].scopes)'
```
If the scopes do **not** include `.../devstorage.read_write` or `.../cloud-platform`, widen them
(**requires a stop/start** — you run VM lifecycle yourself):
```bash
gcloud compute instances stop  smc-quant-eu --zone europe-west9-b --project yass-chart
gcloud compute instances set-service-account smc-quant-eu --zone europe-west9-b --project yass-chart \
       --scopes cloud-platform
gcloud compute instances start smc-quant-eu --zone europe-west9-b --project yass-chart
```

### 3. Deploy the archiver (surgical scp — daemon untouched)
```bash
gcloud compute scp ops/archive_buckets.py \
   smc-quant-eu:/home/yassine.mdouari/OrderFlowPlatform/ops/archive_buckets.py \
   --zone europe-west9-b --project yass-chart
```
(create the `ops/` dir on the VM first if needed: `... --command "mkdir -p .../OrderFlowPlatform/ops"`)

### 4. Test on the VM (dry-run stages + computes bids but uploads nothing)
```bash
gcloud compute ssh smc-quant-eu --zone europe-west9-b --project yass-chart --command \
  "cd /home/yassine.mdouari/OrderFlowPlatform && sudo -u yassine.mdouari python3 ops/archive_buckets.py --dry-run"
```
Then a real run (uploads + advances the hwm):
```bash
... --command "cd .../OrderFlowPlatform && sudo -u yassine.mdouari python3 ops/archive_buckets.py"
gsutil ls -r gs://smc-quant-archive/solusdt | head     # confirm chunks landed
```

### 5. Schedule (run as the daemon's service user so it can read the db + write state)
`crontab -e` (or `sudo crontab -u yassine.mdouari -e`) — every 6h is well under the ~4-day cap:
```
0 */6 * * * cd /home/yassine.mdouari/OrderFlowPlatform && /usr/bin/python3 ops/archive_buckets.py >> data/archive.log 2>&1
```

---

## Local backtesting
```powershell
powershell -ExecutionPolicy Bypass -File study\pull_archive.ps1     # mirror GCS -> study/archive_data/
```
```python
from study.archive_loader import load_archive
bids, raws, gaps = load_archive("1m")     # identical shape to pivot_backtest.load_local_tape
assert not gaps, gaps                      # a gap == a missed archiver window (never silent)
# optional columnar cache for repeated runs (pyarrow, local-only):
# from study.archive_loader import to_parquet; to_parquet("1m")
```

## Notes
- **Contiguity** relies on the cron cadence staying under the cap's age. 6h vs a ~4-day 1m cap = huge
  margin. If the VM/cron is down for days, the loader surfaces the gap in `gaps` — it never lies.
- The archiver reads `history.db` **read-only in WAL mode**, so it can never block or corrupt the
  daemon's writes (same safety model as `study/pull_snapshot.ps1`).
- `data` is stored verbatim (the daemon's bucket JSON), so `app.persistence._bucket_from_dict` and every
  existing study reconstruct buckets bit-identically to a live pull.
- Your existing `study/data/history_snapshot_*.db` snapshots stay valid; the archive supersedes them for
  new history but doesn't replace what you've already banked.
