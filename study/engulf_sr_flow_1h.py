"""Do the Overnight-Drift regime findings help the 1h Engulf S/R REVERSAL?

The 1h engulf is a fade-at-a-level (reversal) trade -> it lives in the REVERSION regime, so it should want:
  (1) TOXIC exhaustion on the reversal candle  -> VPIN gate  |delta|/vol > vthr  (climax, one side spent).
  (2) NO trending session against it            -> FLOW VETO: skip if the PRE-signal trailing flow (bars i-K..i-1,
      excludes the reversal candle) is strongly in the faded direction (momentum => the move continues).
      LONG fades a down-move  -> skip if trailing RSV <= -thr ; SHORT fades an up-move -> skip if trailing RSV >= +thr.
Filters are applied to the raw signal list (pre-entry), then the unchanged engulf_sr_1h simulators + non-overlap run.

CLI: python study/engulf_sr_flow_1h.py
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import study.engulf_sr_1h as ES

A = ES.A; n = ES.n
bv = np.array([float(b.get("buy_vol", 0) or 0) for b in A])
sv = np.array([float(b.get("sell_vol", 0) or 0) for b in A])
cbv = np.concatenate([[0.0], np.cumsum(bv)]); csv = np.concatenate([[0.0], np.cumsum(sv)])
VTHR = 0.14; FK = 3; FTHR = 0.0


def vpin(i):
    v = bv[i] + sv[i]
    return abs(bv[i] - sv[i]) / v if v > 0 else 0.0


def trsv_prev(i, K):
    a = max(0, i - K); b = cbv[i] - cbv[a]; s = csv[i] - csv[a]
    return (b - s) / (b + s) if b + s > 0 else 0.0


def keep(sg, use_vpin, use_flow, vthr=VTHR, fk=FK, fthr=FTHR):
    i = sg["i"]; s = sg["side"]
    if use_vpin and vpin(i) <= vthr:
        return False
    if use_flow:
        r = trsv_prev(i, fk)
        if s > 0 and r <= -fthr:      # long fades a down-move; skip if session trending down
            return False
        if s < 0 and r >= fthr:       # short fades an up-move; skip if session trending up
            return False
    return True


def block(name, sim, sigs):
    rows, _ = ES.run(sim, sigs)
    print("--- %s (n=%d) ---" % (name, len(rows)))
    ES.report("ALL", rows)
    ES.report("LONG", [r for r in rows if r["side"] > 0])
    ES.report("SHORT", [r for r in rows if r["side"] < 0])
    ES.report("2025", [r for r in rows if r["yr"] == 2025])
    ES.report("2026", [r for r in rows if r["yr"] == 2026])
    print()


def main():
    sigs = ES.gen_signals()
    variants = [
        ("BASE (unchanged)", lambda sg: True),
        ("VPIN>%.2f (toxic)" % VTHR, lambda sg: keep(sg, True, False)),
        ("FLOW-veto K=%dh (no trend against)" % FK, lambda sg: keep(sg, False, True)),
        ("BOTH (toxic + flow-veto)", lambda sg: keep(sg, True, True)),
    ]
    for simname, sim in (("A) FIXED 1:1.2", ES.sim_fixed), ("B) TARGET opp S/R", ES.sim_opp)):
        print("=" * 96)
        print("1h ENGULF S/R reversal + reversion-regime gates  |  simulator %s" % simname)
        print("=" * 96)
        for vname, f in variants:
            block(vname, sim, [sg for sg in sigs if f(sg)])


if __name__ == "__main__":
    main()
