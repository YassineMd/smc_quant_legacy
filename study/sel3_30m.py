"""S2 — SEL3 vs the 30-MINUTE label (operator's geometry pick: barriers UNCHANGED 0.5/0.3, horizon 6h->30m).

Labels come from the S1 path-walker (scalp_geometry.first_touch_tables / cell_outcomes) — the machinery
that reproduced the frozen 6h labels EXACTLY at the parity gate. Weights re-derived for k=3 with the
weight_sweep pipeline (discovery-only bins, post-audit W-STAT flags). UNRESOLVED episodes (no barrier
within 30m) are DROPPED from fitting/eval — the score reads "TP-first GIVEN resolution"; the unresolved
rate is disclosed. Characterization on disc/spent-hold only; forward data is the judge.
Writes app/score_v1.json (variant W-STAT-SEL3-RD30) + study/out/score_weights_k3_30m.csv.
"""
import os, sys, csv, json, time
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import scalp_geometry as SG          # noqa: E402
import weight_sweep as WS            # noqa: E402
import frame_sweep as FS             # noqa: E402
import make_score_bundle as MSB      # noqa: E402
import features as FT                # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
TP, SL = 0.005, 0.003
H_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0     # horizon in minutes (CLI)
WIRE = "wire" in sys.argv[2:]                                  # write the panel bundle only when asked
H_S = H_MIN * 60.0
K = 3


def main():
    t0 = time.time()
    print("=== SEL3 vs %dmin label%s ===" % (H_MIN, "" if WIRE else " (DRY — panel bundle NOT written)"))
    # ── 30-minute labels from the parity-proven S1 walker ──
    hi, lo_, cl, st, et = SG.load()
    levels = sorted({TP, SL})
    li = {v: k for k, v in enumerate(levels)}
    ents, up_idx, up_t, dn_idx, dn_t, _, _ = SG.first_touch_tables(hi, lo_, cl, st, et, levels, H_S)
    t0s = et[ents]
    tpL, slL, amL, unL = SG.cell_outcomes(up_idx, up_t, dn_idx, dn_t, li[TP], li[SL], t0s, H_S)
    tpS, slS, amS, unS = SG.cell_outcomes(dn_idx, dn_t, up_idx, up_t, li[TP], li[SL], t0s, H_S)
    def out_arr(tp, sl):
        o = np.where(tp, "TP", np.where(sl, "SL", "UNRESOLVED"))
        return o
    outL30 = out_arr(tpL, slL); outS30 = out_arr(tpS, slS)
    print("[%.0fs] labels: long TP/SL/UN = %d/%d/%d  short = %d/%d/%d  (unresolved %.1f%%)"
          % (time.time() - t0, tpL.sum(), slL.sum(), unL.sum(), tpS.sum(), slS.sum(), unS.sum(),
             100 * (unL.sum() + unS.sum()) / (2 * len(ents))), flush=True)

    # ── weight_sweep data with the label override ──
    D = WS.load(t0)
    m = D["m"]
    # map walker entry index -> bucket order: ents are snapshot indices 16..n-1; m['_j'] matches
    lab_map_L = dict(zip(ents, outL30)); lab_map_S = dict(zip(ents, outS30))
    m["outL"] = m["_j"].map(lab_map_L); m["outS"] = m["_j"].map(lab_map_S)
    rL = m["outL"].isin(["TP", "SL"]); rS = m["outS"].isin(["TP", "SL"])
    masks = {"long": (m["_win"] == "disc") & rL, "short": (m["_win"] == "disc") & rS,
             "disc_any": (m["_win"] == "disc")}
    tps = {"long": (m["outL"] == "TP").values, "short": (m["outS"] == "TP").values}
    bases = {"long": 100.0 * tps["long"][masks["long"].values].mean(),
             "short": 100.0 * tps["short"][masks["short"].values].mean()}
    both = rL & rS
    null = 100.0 * SL / (TP + SL)
    print("[%.0fs] discovery baselines (of resolved): L %.2f / S %.2f  (geometric null %.1f)"
          % (time.time() - t0, bases["long"], bases["short"], null), flush=True)

    # ── k=3 features + re-derived weights vs the 30m label ──
    Fk = WS.build_Fk(D, K)
    dk, gk, aliask = WS.derive(Fk, D["roster"], masks, tps, bases, WS.NEW_EXCL)
    pl = FS.predict(Fk, dk["long"]["binners"], dk["long"]["wstat"], bases["long"])
    ps = FS.predict(Fk, dk["short"]["binners"], dk["short"]["wstat"], bases["short"])
    gap = (pl - bases["long"]) - (ps - bases["short"])
    print("\n== characterization (%dmin label;" % H_MIN + " spent data — forward is the judge) ==")
    for win in ("disc", "hold"):
        mm = (both & (m["_win"] == win)).values
        sel_tp = np.where(gap[mm] >= 0, tps["long"][mm], tps["short"][mm])
        aL, aS = 100.0 * tps["long"][mm].mean(), 100.0 * tps["short"][mm].mean()
        stp = 100.0 * sel_tp.mean()
        print("  %-4s n=%-5d selTP %.1f  always-L %.1f  always-S %.1f  (Δ vs better %+.1f)"
              % (win, mm.sum(), stp, aL, aS, stp - max(aL, aS)), flush=True)

    # ── weights CSV + bundle ──
    with open(os.path.join(OUT, "score_weights_k3_%dm.csv" % int(H_MIN)), "w", newline="", encoding="utf-8") as f:
        f.write("# SEL-k=3 re-derivation vs the %d-MINUTE label" % int(H_MIN) + " (barriers 0.5/0.3 unchanged; unresolved "
                "dropped). Spent-data characterization only; forward data is the judge.\n")
        w = csv.writer(f)
        w.writerow(["variant", "direction", "rank", "feature_code", "weight", "raw_strength"])
        for side in ("long", "short"):
            for rk, (fc, wt) in enumerate(sorted(dk[side]["wstat"].items(), key=lambda kv: -kv[1]), 1):
                w.writerow(["W-STAT-%dM" % int(H_MIN), side, rk, fc, "%.4f" % wt, "%.4f" % dk[side]["rs"][fc]])

    def e_kind(f):
        if f.startswith(("B-", "C.")) or f == "E52.01" or not f.startswith("E"):
            return "native"
        return FT.classify_transform(MSB.TEXT.get(f, "")) or "raw"
    bundle = {"variant": "W-STAT-SEL3-RD%dM" % int(H_MIN), "frozen_date": "2026-07-02", "frame": K,
              "label": "TP +0.5%%%% / SL -0.3%%%%, %d-MINUTE horizon (S2 operator pick; was 6h). UNRESOLVED " % int(H_MIN) +
                       "(~%.0f%% of episodes) dropped — pred reads TP-first GIVEN resolution."
                       % (100 * (unL.sum() + unS.sum()) / (2 * len(ents))),
              "note": "Forward-test display; NOT a validated probability. k=3 + re-derived weights vs the "
                      "30m label, all fits on spent data — forward snapshots are the judge.",
              "display": "edge-mode single L-S gap line; hover = per-side edge in pp; forward log RAW.",
              "retained_pct": {}, "sides": {}}
    for s in ("long", "short"):
        feats = {}
        for fcode, wt in dk[s]["wstat"].items():
            bn = dk[s]["binners"].get(fcode)
            if bn is None:
                continue
            bd = {"kind": bn.kind, "tpr": bn.tpr, "n": bn.n}
            if bn.kind == "num":
                bd["edges"] = [float(x) for x in bn.spec]
            else:
                bd["cats"] = [str(x) for x in bn.spec]
            feats[fcode] = {"weight": wt, "kind": e_kind(fcode), "bin": bd}
        bundle["sides"][s] = {"baseline": bases[s], "features": feats}
        bundle["retained_pct"][s] = 100.0
        print("%-5s SEL3-RD30 bundle: %d features, Σw=%.6f" % (s, len(feats),
              sum(x["weight"] for x in feats.values())))
    if WIRE:
        json.dump(bundle, open(os.path.join(REPO, "app", "score_v1.json"), "w"), indent=1)
        print("wrote app/score_v1.json (%s) + study/out/score_weights_k3_%dm.csv" % (bundle["variant"], int(H_MIN)))
    else:
        print("[dry] bundle prepared (%s) — run with 'wire' to install on the panel" % bundle["variant"])


if __name__ == "__main__":
    main()
