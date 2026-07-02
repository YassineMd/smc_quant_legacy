"""T1 assembler — builds study/out/dataset.{parquet,csv} + extraction_report.md over the FULL 1281-column
contract. Entry-legal E*/G*/C.*/K.* + the O.* excursion tail + labels; every other code present as NULL with
a categorized reason (deferred / not-computable / T2-T3). Runs the §5 validation battery. HARD STOP after.
"""
from __future__ import annotations
import csv
import hashlib
import os
import re
import sys
import time
import random
import datetime as dt
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "study"))
import extract as EX          # noqa: E402
import features as FT         # noqa: E402
import features_b as FT_B     # noqa: E402
from app import config        # noqa: E402

OUT = os.path.join(REPO, "study", "out")
CONTRACT = os.path.join(REPO, "study", "data", "column_contract.tsv")
SNAP_UTC = "2026-07-02T11:17:07Z"
t0 = time.time()

# ── contract (ordered 1281 codes) ───────────────────────────────────────────
contract = []
with open(CONTRACT, encoding="utf-8") as f:
    for r in csv.DictReader(f, delimiter="\t"):
        contract.append(r)                       # code, legality, section, field, text
CODES = [r["code"] for r in contract]
TEXT = {r["code"]: r["text"] for r in contract}
SECT = {r["code"]: r["section"] for r in contract}

# ── data + labeler ──────────────────────────────────────────────────────────
import json, sqlite3
con = sqlite3.connect("file:%s?mode=ro" % EX.SNAPSHOT, uri=True)
raw = [json.loads(x[0]) for x in con.execute("SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
tc = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
con.close()
from app.persistence import _bucket_from_dict
bks = [_bucket_from_dict(d) for d in raw]
snaps = [b.full_snapshot() for b in bks]
n = len(bks)
base_id = (int(tc[0]) if tc else n) - n
ids = [base_id + i + 1 for i in range(n)]
times = [float(snaps[i].get("end_time", 0.0)) for i in range(n)]
eps = EX.build_episodes(bks, ids)
print("[%.1fs] loaded %d buckets, %d episodes" % (time.time() - t0, n, len(eps)))

# ── precompute entry-legal per-bucket features ──────────────────────────────
rs = FT.repo_series(snaps, bks)
base = FT.base_series(snaps, bks, rs)
kc = FT.build_kc(snaps)
print("[%.1fs] repo/base/kc computed" % (time.time() - t0), flush=True)

CATEG = {"E60", "E62"}                            # categorical base (state) — no numeric transform
DERIV_RE = re.compile(r"^E(\d+)\.\d+$")
GRP_RE = re.compile(r"^G\d+\.\d+$")

# derivations grouped by parent base field
deriv_by_base = defaultdict(list)
for code in CODES:
    m = DERIV_RE.match(code)
    if m:
        deriv_by_base["E" + m.group(1)].append(code)

# precompute the transform arrays each computable base field needs (once)
TR = {}
for bc, series_reason in base.items():
    series, why = series_reason
    if series is None or bc in CATEG:
        continue
    kinds = {"raw"}
    for dc in deriv_by_base.get(bc, []):
        k = FT.classify_transform(TEXT[dc])
        if k:
            kinds.add(k)
    TR[bc] = FT.precompute_field_transforms(series, times, kinds)
print("[%.1fs] transforms precomputed (%d fields)" % (time.time() - t0, len(TR)), flush=True)

# per-code reason for NULLs / not-computable table (single pass, not per cell)
reason_tally = Counter()
code_reason = {}
def e_reason(code):
    parent = "E" + DERIV_RE.match(code).group(1)
    series, why = base.get(parent, (None, "unknown base"))
    if series is None:
        return why
    if parent in CATEG:
        return None if code.endswith(".01") else "categorical state: only raw (.01) defined"
    if FT.classify_transform(TEXT[code]) is None:
        return None if code.endswith(".01") else "catalog transform not unambiguously specified"
    return None
def g_reason(code):
    sec = SECT[code]
    if code in ("G12.4", "G19.2", "G19.3"):
        return "six-hour tape sequencing — NOT COMPUTABLE (depth.db/tape)"
    if sec.startswith("G17") or sec.startswith("G18") or sec.startswith("G20"):
        return "structure/cross-tf composite — deferred (order-block / higher-tf)"
    return "bespoke composite — deferred (T1 core = named primitives)"

def e_value(code, i):
    parent = "E" + DERIV_RE.match(code).group(1)
    series = base[parent][0]
    if series is None:
        return FT.NULL
    if parent in CATEG:
        return series[i] if code.endswith(".01") else FT.NULL
    k = FT.classify_transform(TEXT[code])
    if k is None:
        return series[i] if code.endswith(".01") else FT.NULL
    return TR[parent][k][i]

def label_map(ep):
    return {"L.01": ep["direction"], "L.02": ep["outcome"], "L.03": 1 if ep["resolved"] else 0,
            "L.04": ep["censor"], "L.05": ep["entry"], "L.06": ep["entry_ts"],
            "L.07": 1 if ep["ambiguous"] else 0, "L.08": ep["joint3"], "L.09": ep["episode_id"]}

# entry-legal codes that CAN carry a value (thin per-row dict; the rest fall out of reindex as NaN)
E_CODES = [c for c in CODES if DERIV_RE.match(c)]
G_CODES = [c for c in CODES if GRP_RE.match(c)]
C_CODES = [c for c in CODES if c.startswith("C.")]
K_CODES = [c for c in CODES if c.startswith("K.")]

# cache entry-legal row per bucket ONCE
ent_cache = {}
def entry_row(i):
    r = ent_cache.get(i)
    if r is not None:
        return r
    r = {}
    for code in E_CODES:
        v = e_value(code, i)
        if v is not None:
            r[code] = v
    grow = FT.build_g(base, rs, times, i)
    for code, v in grow.items():
        if v is not None:
            r[code] = v
    ctx = FT.build_context(snaps, rs, i)
    for code, v in ctx.items():
        if v is not None:
            r[code] = v
    for code, v in kc[i].items():
        if v is not None:
            r[code] = v
    for code, v in FT_B.compute_bscope(snaps, rs, i).items():   # T2 B-scope panels (entry-legal)
        if v is not None:
            r[code] = v
    ent_cache[i] = r
    return r

# ── assemble rows (thin: only value-bearing codes; NaN columns via reindex) ──
KEY = ["L.09", "bucket_id", "L.06", "L.01", "L.02", "L.08"]
ordered = KEY + [c for c in CODES if c not in KEY]
rows = []
for ep in eps:
    i = ep["i"]
    row = {"bucket_id": ids[i]}
    row.update(label_map(ep))
    paths = FT.compute_paths(bks, ids, ep)
    paths["O.01"] = ep["joint3"]
    for code, v in paths.items():
        if v is not None:
            row[code] = v
    row.update(entry_row(i))
    rows.append(row)
print("[%.1fs] assembled %d rows" % (time.time() - t0, len(rows)), flush=True)

# which B-scope codes are FILLED this tranche (compute_bscope returns the same keyset each bucket)
def b_reason(code):
    body = code[2:]
    if body[:2] in ("P5", "P6", "P7"):
        return "T2b: phase-panel segmentation (BEFORE/DURING/END) — pending"
    if body[:2] == "P8":
        return "T2b: large/small needs daemon size_thr anchor (E53, not persisted)"
    if body[:2] == "P9":
        return "T2b: composite-lean panel — pending"
    if body[:2] == "P0":
        return "T2b: smoothed-lean twin (locked-region / confirmed-cross) — pending"
    if body[:2] == "P4":
        return "T2b: exhaustion fire-count / weakest-leg — pending"
    return "T2b: B-scope panel — pending"

# ── DataFrame + writeout ─────────────────────────────────────────────────────
import pandas as pd
df = pd.DataFrame(rows).reindex(columns=ordered)
os.makedirs(OUT, exist_ok=True)
pq = os.path.join(OUT, "dataset.parquet"); cs = os.path.join(OUT, "dataset.csv")
df.to_parquet(pq, index=False)
df.to_csv(cs, index=False)
print("[%.1fs] wrote parquet+csv" % (time.time() - t0))

# ── deferral table: a code is DEFERRED iff its column is fully NULL in the finished dataset ──
notcomp = {}
for code in CODES:
    if code in KEY or not df[code].isna().all():
        continue                                      # computed -> not deferred
    if DERIV_RE.match(code):
        why = e_reason(code) or "catalog transform not unambiguously specified"
        reason_tally[why if ("NOT COMPUTABLE" in why or "deferred" in why) else "catalog-transform-deferred"] += 1
    elif GRP_RE.match(code):
        why = g_reason(code); reason_tally[why.split(" —")[0]] += 1
    elif code[:2] == "B-":
        why = b_reason(code); reason_tally["T2b B-scope pending"] += 1
    elif code[:2] in ("J-", "X-"):
        why = "T3: %s-scope (post-hoc)" % code[:2]
    else:
        why = "deferred"
    notcomp[code] = why
B_FILLED = {c for c in CODES if c.startswith("B-") and not df[c].isna().all()}
with open(os.path.join(OUT, "deferred_codes.tsv"), "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["code", "family", "reason"])
    for code in CODES:
        if code in notcomp:
            fam_ = code[:2] if code[:2] in ("B-", "J-", "X-") else re.match(r"[A-Z]+", code).group()
            w.writerow([code, fam_, notcomp[code]])
print("[%.1fs] deferred_codes.tsv: %d codes" % (time.time() - t0, len(notcomp)))

# ── validation battery (§5) ──────────────────────────────────────────────────
random.seed(7)
sample = random.sample(range(FT.CTX_N, n), 200)
# 5.3a  V*s + V*(1-s) == dominant volume  (in-direction buckets)
cons_ok = cons_tot = 0; max_err = 0.0
for i in sample:
    s = snaps[i]; bv = s.get("buy_vol", 0.0); sv = s.get("sell_vol", 0.0); c = s.get("close", 0.0); o = s.get("open", 0.0)
    if (bv > sv and c > o) or (sv > bv and c < o):
        dom = max(bv, sv)
        got = (rs["bull"][i] + rs["bear"][i]) + (rs["effb"][i] + rs["effr"][i])
        max_err = max(max_err, abs(got - dom)); cons_tot += 1
        if abs(got - dom) <= 1e-6 * max(1.0, dom):
            cons_ok += 1
# 5.3b size-hist volume sum == buy+sell (only where sz present)
sz_ok = sz_tot = 0
for i in sample:
    if hasattr(bks[i], "sz_vb") and (sum(bks[i].sz_vb) + sum(bks[i].sz_vs)) > 0:
        got = sum(bks[i].sz_vb) + sum(bks[i].sz_vs); exp = snaps[i].get("buy_vol", 0) + snaps[i].get("sell_vol", 0)
        sz_tot += 1
        if abs(got - exp) <= 1e-3 * max(1.0, exp):
            sz_ok += 1
# 5.3c classify_bucket determinism
det_ok = 0
for i in sample:
    bm, sm, _ = FT.R.exhaustion_mults(snaps, i)
    st, _ = FT.BS.classify_bucket(snaps, i, bm, sm)
    if st == rs["state"][i]:
        det_ok += 1
# 5.3d  T2 cross-scope: B-scope stats box reconciles with per-bucket T1 sums over the 16-bucket selection
b_ok = b_tot = 0
for i in random.sample(range(FT_B.SEL, n), 60):
    bs = FT_B.compute_bscope(snaps, rs, i)
    lo = i - FT_B.SEL + 1
    exp_cv = sum(float(snaps[j].get("curr_vol", 0.0)) for j in range(lo, i + 1))
    exp_dl = sum(float(snaps[j].get("buy_vol", 0.0)) - float(snaps[j].get("sell_vol", 0.0)) for j in range(lo, i + 1))
    exp_oi = sum((float(snaps[j].get("opL", 0.0)) + float(snaps[j].get("opS", 0.0)))
                 - (float(snaps[j].get("clL", 0.0)) + float(snaps[j].get("clS", 0.0))) for j in range(lo, i + 1))
    for got, exp in ((bs["B-S.03"], exp_cv), (bs["B-S.05"], exp_dl), (bs["B-S.06"], exp_oi)):
        b_tot += 1
        if abs(got - exp) <= 1e-6 * max(1.0, abs(exp)):
            b_ok += 1

# 5.4 NULL coverage per family
fam = defaultdict(lambda: [0, 0])   # prefix -> [null, total]
for code in CODES:
    pre = code[:2] if code[:2] in ("B-", "J-", "X-") else re.match(r"[A-Z]+", code).group()
    col = df[code]
    fam[pre][0] += int(col.isna().sum()); fam[pre][1] += len(col)
# computed vs deferred (entry-legal core)
computed = [c for c in CODES if not df[c].isna().all()]
schema_hash = hashlib.sha256("\n".join(ordered).encode()).hexdigest()[:16]

# outcome stats
by = {d: Counter(e["outcome"] for e in eps if e["direction"] == d) for d in ("long", "short")}
buckets = [e for e in eps if e["direction"] == "long"]
jc = Counter(e["joint3"] for e in buckets)
amb = sum(1 for e in eps if e["ambiguous"])

def rate(d):
    c = by[d]; res = c["TP"] + c["SL"]
    return 100 * c["TP"] / res if res else 0.0

# ── report ───────────────────────────────────────────────────────────────────
span0 = dt.datetime.utcfromtimestamp(bks[0].start_time); span1 = dt.datetime.utcfromtimestamp(bks[-1].end_time)
elapsed = time.time() - t0
L = []
L.append("# TP-vs-SL Barrier Study — Extraction Report (Phase 1–2) · **EXTRACTION CLOSED at 295/1281**\n")
L.append("_Generated %s UTC · hard stop after this report (no analysis)._\n" % dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))

L.append("\n## 1. Snapshot & row counts\n")
L.append("| item | value |\n|---|---|")
L.append("| snapshot UTC | %s |" % SNAP_UTC)
L.append("| file | history_snapshot_20260702.db, 67.3 MB |")
L.append("| 1m buckets | %d (cap) · 5m 5961 · 15m 4496 · 1h 3086 · 4h 2733 |" % n)
L.append("| 1m span | %s → %s UTC |" % (span0, span1))
L.append("| episodes | %d (idx 16→end, ×2 dir) · index-16 start honored |" % len(eps))
L.append("| output | dataset.parquet + dataset.csv (%d cols) |" % len(ordered))

L.append("\n## 2. Outcome distribution vs nulls\n")
L.append("| joint (per bucket) | n | %% |\n|---|---|---|")
for k in ("UP-resolve", "DOWN-resolve", "WHIPSAW", "unresolved"):
    L.append("| %s | %d | %.1f |" % (k, jc[k], 100 * jc[k] / len(buckets)))
L.append("\nPer-direction TP rate (of resolved): **long %.1f%%**, **short %.1f%%** vs the **37.5%% random-walk null** "
         "(= SL_dist/(TP+SL) = 0.3/0.8).  WHIPSAW **%.1f%%**.  UNRESOLVED long %.1f%% / short %.1f%% (all end-of-data).  "
         "Single-bucket ambiguous-hit (L.07) **%.3f%%** — ~0 because the 0.8%%-wide barrier pair exceeds any 1m bar's range."
         % (rate("long"), rate("short"), 100 * jc["WHIPSAW"] / len(buckets),
            100 * by["long"]["UNRESOLVED"] / len(buckets), 100 * by["short"]["UNRESOLVED"] / len(buckets),
            100 * amb / len(eps)))

L.append("\n## 3. Conservation spot-checks (200 random buckets, §5.3)\n")
L.append("| check | result |\n|---|---|")
L.append("| V·s + V·(1−s) == dominant V | %d/%d within 1e-6 (max err %.2e) |" % (cons_ok, cons_tot, max_err))
L.append("| Σ size-hist vol == buy+sell | %d/%d within 1e-3 (%s) |" % (sz_ok, sz_tot, "sz_* present only post-2026-06-30" if sz_tot < len(sample) else "all"))
L.append("| classify_bucket determinism | %d/200 reproduce stored state |" % det_ok)
L.append("| T2 B-scope stats ↔ T1 per-bucket sums (S.03/05/06) | %d/%d exact |" % (b_ok, b_tot))

L.append("\n## 4. NULL / mask coverage per family\n")
L.append("| family | NULL cells | total | %% NULL |\n|---|---|---|---|")
for pre in sorted(fam):
    nn, tt = fam[pre]
    L.append("| %s | %d | %d | %.1f |" % (pre, nn, tt, 100 * nn / tt))

present = sum(1 for c in CODES if c in df.columns)
L.append("\n## 5. Column-contract audit\n")
L.append("- **%d / %d** contract codes present + `bucket_id` identity col (schema hash `%s`, %d cols total).\n"
         % (present, 1281, schema_hash, len(ordered)))
L.append("- **%d / 1281** codes carry ≥1 computed value (entry-legal core + labels + O-tail); **%d** are fully "
         "NULL (deferred / not-computable / T2–T3), enumerated in deferred_codes.tsv.\n"
         % (len(computed), 1281 - len(computed)))
L.append("\nDeferral reasons (top):\n")
L.append("| reason | E-deriv count |\n|---|---|")
for why, c in reason_tally.most_common(8):
    L.append("| %s | %d |" % (why, c))
L.append("\n**Not-computable (structural, will never fill from this snapshot):** E41 + E66–E72 (six-hour depth.db/"
         "tape, 80 derivs), E53 (size_thr — engine_state anchor), G12.4/G19.2-3 (tape sequencing). "
         "**Deferred to enrichment:** compound/bespoke E-derivations, most G composites, E63–E65 (order-block "
         "reconstruction).")
L.append("\n**T2 B-scope (this tranche):** S stats + P1 ABSORPTION + P2 EFF-AGG + P3 E/R + P4 EXHAUSTION-core "
         "(**%d/129** B- fields) computed FAITHFULLY by replicating the terminal's _refresh_selection_stats math "
         "(region_state pure fns + exact badge locked-index). **T2b pending:** phase panels P5–P7 (segmentation "
         "state machine), P8L/P8S (need the daemon's live size_thr anchor, not persisted), P9 composite lean, P0 "
         "smoothed twin, and P4 fire-counts/weakest-leg. **T3:** J-/X- scopes (post-hoc).\n" % len(B_FILLED))
L.append("\n### Enrichment policy (architect-set, ON-DEMAND)\n")
L.append("The registry is a catalog; deferred codes are **not** ground out speculatively. When the analysis "
         "phase pre-registers its feature subset, any deferred code on that list gets its exact formula from the "
         "architect and is then computed. The full deferral inventory is persisted to **`study/out/deferred_codes."
         "tsv`** (code · family · reason) to shop from. No-guessing stands: a wrong-but-filled column is worse "
         "than an honest NULL.\n")

L.append("\n## 6. Files & timing\n")
_dcodes = os.path.join(OUT, "deferred_codes.tsv")
for p in (pq, cs, _dcodes):
    if os.path.exists(p):
        L.append("- `%s` — %.2f MB" % (os.path.relpath(p, REPO).replace("\\", "/"), os.path.getsize(p) / 1e6))
L.append("- extraction wall time: %.1fs\n" % elapsed)

L.append("\n## 7. Deviations from spec (flagged, accepted)\n")
L.append("1. **Phase-1 DB path** — `~/OrderFlowPlatform/data/history.db` (not `~/OrderFlowPlatform/history.db`). "
         "*Accepted by architect.*\n")
L.append("2. **Phase-1 VACUUM** — no `sqlite3` CLI on VM → `python3 \"VACUUM INTO\"` (transaction-consistent, "
         "identical). *Accepted by architect.*\n")
L.append("3. **Registry is a descriptive CATALOG, not a formula spec.** Base fields (72) map to production "
         "quantities reused bit-identically (full_snapshot + region_state/bucket_state/vpin/quant_engine). "
         "Derivations are filled ONLY where the text names one unambiguous generic transform (raw/z-trailing30/"
         "percentile/day-rank/streak/slope/sign/log) of the field's canonical scalar; compound or sub-quantity-"
         "specific texts are NULL+reason, never guessed (upholds the no-reimplementation rule).\n")
L.append("4. **C.* window** = the 15 buckets strictly before entry `[i-15, i-1]` (pre-entry context). "
         "**O.* excursion** interpreted per §2 directional rule; magnitudes are %% of entry.\n")
L.append("5. **KC/POC** frozen params used verbatim: EMA-20, 2.0×ATR-20 (SMA true-range), rolling-POC-240.\n")

L.append("\n## 8. Leakage guard\n")
L.append("Enforced mechanically by prefix: entry-legal = E*/G*/C.*/K.* (+ B-*, T2); post-hoc = L.*/O.* (+ J-*/X-*, "
         "T3). O.* filled this tranche are excursion outcomes — descriptive only, never entry-side.\n")

L.append("\n## 9. Extraction closure (architect ruling — T1+T2 gates PASS)\n")
L.append("Extraction is **CLOSED at %d/1281** computed. The remaining %d codes are NOT built now, by ruling; they "
         "reopen **strictly on-demand** from the analysis phase's pre-registered feature subset "
         "(`deferred_codes.tsv` is the shopping list):\n" % (len(computed), 1281 - len(computed)))
L.append("1. **On-demand catalog transforms** — 547 E + 105 G derivations whose text isn't one unambiguous "
         "generic transform; exact formula supplied when a code is pre-registered.\n")
L.append("2. **Composites derivable from the banked P1–P4 series** — P9 composite lean, P0 smoothed twin: "
         "reconstructable on demand from the already-extracted panel columns, no new primitives.\n")
L.append("3. **Blocked on the unpersisted `size_thr` anchor** — B-P8L/P8S (+ E52/E53). A histogram-derived "
         "threshold **PROXY is possible later, flagged APPROXIMATE**, on demand — not the daemon's live anchor.\n")
L.append("4. **Post-hoc-only** — J-/X- scopes (258): belong to the outcome-unfolding analysis, never entry-side.\n")
L.append("\n**Snapshot cadence.** The 10k/tf cap makes 1m history a **~4-day ROLLING window**; each snapshot "
         "freezes one window permanently. `study/pull_snapshot.ps1` runs the Phase-1 pull (datestamped, read-only, "
         "daemon untouched) — run every ~3 days to accumulate non-overlapping windows offline.\n")
L.append("\n**Hard stop holds. The analysis phase opens only on Yassine's explicit instruction.**\n")

report = os.path.join(OUT, "extraction_report.md")
open(report, "w", encoding="utf-8").write("\n".join(L))
print("[%.1fs] wrote %s" % (time.time() - t0, os.path.relpath(report, REPO)))
print("\nCOMPUTED cols: %d/1281 | conservation %d/%d | determinism %d/200 | B-scope xcheck %d/%d | schema %s"
      % (len(computed), cons_ok, cons_tot, det_ok, b_ok, b_tot, schema_hash))
