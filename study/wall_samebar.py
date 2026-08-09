# -*- coding: utf-8 -*-
"""IMMEDIATE SAME-BAR resolutions (ko==k0): price enters the radar and closes decisively beyond it on the SAME bar
(a momentum break, or a wick rejection). This is the population the vol x pen P(resist) model EXCLUDES (no post-entry
bar). Q: does the PRE-ENTRY approach impulse [j0,k0-1] (strictly causal) predict break-vs-reject here? Plus a
characterization of the resolving bar itself (contemporaneous, labelled). Both years."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
import numpy as np
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f, auc_p
from app import absorption_level_detect as AL

LF, DECAY, STR, MAXLEG, EPS = 24, 0.6, 0.12, 96, 1e-9
print("loading 15m + detect ...", flush=True)
_, r15, _ = load_archive("15m", root="study/recon_archive")
A = sorted(r15, key=lambda b: _f(b.get("start_time", 0)))
for b in A:
    b["open"] = _f(b.get("open_price")); b["close"] = _f(b.get("close_price"))
n = len(A)
O = np.array([b["open"] for b in A]); C = np.array([b["close"] for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
CVv = np.array([_f(b.get("curr_vol")) for b in A])
YR = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A]

walls = AL.detect(A)
Sanch = set(); Ranch = set()
for w in walls:
    (Sanch if w["side"] == "S" else Ranch).add(w["i0"])
    for r in w["radar_runs"]:
        (Sanch if w["side"] == "S" else Ranch).add(r[0])
Sanch = sorted(Sanch); Ranch = sorted(Ranch)


def last_anchor(anchors, k0):
    i = bisect.bisect_left(anchors, k0) - 1
    return anchors[i] if (i >= 0 and 6 <= k0 - anchors[i] <= MAXLEG) else None


V = []
n_imm = n_multi = 0
for w in walls:
    hits = w["hits"]; base = w["strength"] / (DECAY ** hits) if hits else w["strength"]
    P = w["price"]; band = w["band"]; side = w["side"]; r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
    for j, (k0, k1, *_) in enumerate(w["radar_runs"]):
        if base * (DECAY ** j) < STR:
            continue
        ko = oc = None
        for k in range(k0, min(n, k0 + LF)):
            r = (0 if C[k] > r_hi else (1 if C[k] < r_lo else None)) if side == "R" else \
                (0 if C[k] < r_lo else (1 if C[k] > r_hi else None))
            if r is not None:
                ko, oc = k, r; break
        if oc is None:
            continue
        if ko == k0:
            n_imm += 1
        else:
            n_multi += 1
            continue                                  # keep ONLY immediate same-bar resolutions
        j0 = last_anchor(Sanch if side == "R" else Ranch, k0)
        if j0 is None:
            continue
        d = 1.0 if side == "R" else -1.0
        if (C[k0] - C[j0]) * d <= 0:
            continue
        m = k0 - j0
        if m < 6:
            continue
        # PRE-ENTRY impulse [j0,k0-1] (causal)
        pc = C[j0:k0]; p0 = pc[0]; diffs = np.diff(pc)
        path = float(np.sum(np.abs(diffs))) + EPS
        er = ((pc[-1] - pc[0]) * d) / path
        dist = ((pc[-1] - pc[0]) * d) / p0 * 100.0
        vel = dist / m
        npull = float(np.mean((diffs * d) < 0))
        rl = 0
        for i in range(m - 1, 0, -1):
            if (pc[i] - pc[i - 1]) * d > 0:
                rl += 1
            else:
                break
        fresh = 1.0 if (pc[-1] >= pc.max() - EPS if d > 0 else pc[-1] <= pc.min() + EPS) else 0.0
        # RESOLVING-bar character (CONTEMPORANEOUS with outcome — characterization only)
        rng0 = max(H[k0] - L[k0], EPS)
        ent_body = ((C[k0] - O[k0]) * d) / rng0
        rmv = float(np.median(CVv[max(0, k0 - 200):k0])) if k0 > 5 else CVv[k0]
        ent_volr = CVv[k0] / rmv if rmv > 0 else np.nan
        pen = min(1.0, max(0.0, ((H[k0] - r_lo) if side == "R" else (r_hi - L[k0])) / (r_hi - r_lo)))
        V.append({"resist": oc, "yr": YR[k0], "er": er, "dist": dist, "vel": vel, "npull": npull,
                  "runlen": float(rl), "fresh": fresh, "ent_body": ent_body, "ent_volr": ent_volr, "pen": pen})

N = len(V)
br = 1 - sum(v["resist"] for v in V) / N
print("\n=== SAME-BAR resolutions: %d immediate / %d multi-bar (%.0f%% of all resolve same-bar) ===" % (
    n_imm, n_multi, 100 * n_imm / (n_imm + n_multi)), flush=True)
print("    usable (anchor+leg): %d | BREAK %.1f%% / REJECT %.1f%%  [multi-bar pop was ~71%% resist]" % (
    N, 100 * br, 100 * (1 - br)), flush=True)
if br < 0.005 or br > 0.995:
    print("\n    ** DEGENERATE: a registered radar VISIT that resolves same-bar can ONLY be a wick-REJECTION.", flush=True)
    print("       A body closing clean through the far edge = the wall BREAKING (a wall-death event, not a", flush=True)
    print("       visit) -> same-bar breaks are absent from radar_runs by construction. No break/resist", flush=True)
    print("       variation to predict here. CORRECTION: impulse -> deep same-bar POKE-then-REJECT (resist),", flush=True)
    print("       NOT impulse -> break. The whole visit/P(resist) analysis is conditioned on 'did not close", flush=True)
    print("       clean through on first contact'.", flush=True)


def rr(g): return 100 * sum(x["resist"] for x in g) / max(1, len(g))
def sub(yy): return [v for v in V if v["yr"] == yy]


print("\n(A) PRE-ENTRY impulse -> resolves as REJECT (resist)?  [AUC>0.5 -> higher -> REJECT]", flush=True)
rows = []
for feat in ("er", "dist", "vel", "npull", "runlen", "fresh"):
    a = [v[feat] for v in V if v["resist"] and v[feat] == v[feat]]; b = [v[feat] for v in V if not v["resist"] and v[feat] == v[feat]]
    if len(a) < 20 or len(b) < 20:
        continue
    g = auc_p(a, b)[0]
    a25 = auc_p([v[feat] for v in sub(2025) if v["resist"] and v[feat] == v[feat]], [v[feat] for v in sub(2025) if not v["resist"] and v[feat] == v[feat]])[0]
    a26 = auc_p([v[feat] for v in sub(2026) if v["resist"] and v[feat] == v[feat]], [v[feat] for v in sub(2026) if not v["resist"] and v[feat] == v[feat]])[0]
    rows.append((abs(g - 0.5), feat, g, a25, a26))
rows.sort(reverse=True)
for _, feat, g, a25, a26 in rows:
    flag = "  <-- both-yr" if (g - 0.5) * (a25 - 0.5) > 0 and (g - 0.5) * (a26 - 0.5) > 0 and abs(g - 0.5) >= 0.04 else ""
    print("    %-8s %.3f (25:%.2f 26:%.2f)%s" % (feat, g, a25, a26, flag), flush=True)

print("\n(B) er tercile -> REJECT rate (both yr):", flush=True)
xs = sorted(v["er"] for v in V); lo_t = xs[N // 3]; hi_t = xs[2 * N // 3]
for nm, g in (("bot3rd(grind)", [v for v in V if v["er"] <= lo_t]), ("top3rd(impulse)", [v for v in V if v["er"] >= hi_t])):
    g25 = [v for v in g if v["yr"] == 2025]; g26 = [v for v in g if v["yr"] == 2026]
    print("    %-16s n=%4d REJECT %.1f%% (25:%.1f 26:%.1f)" % (nm, len(g), rr(g), rr(g25), rr(g26)), flush=True)

print("\n(C) resolving-bar character (the same-bar REJECTION bar itself):", flush=True)
for feat, nm in (("ent_body", "entry body-frac (oriented)"), ("ent_volr", "entry vol / median"), ("pen", "penetration into radar")):
    rj = [v[feat] for v in V if v["resist"] and v[feat] == v[feat]]
    bk = [v[feat] for v in V if not v["resist"] and v[feat] == v[feat]]
    bkm = ("%+.2f" % np.mean(bk)) if bk else "  n/a"
    print("    %-26s REJECT-bar %+.2f   (BREAK-bar %s)" % (nm, np.mean(rj), bkm), flush=True)
