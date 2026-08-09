"""WALL REGIME READ — trend/range + directional bias from wall CREATION + MITIGATION over a trailing window.

DESCRIPTIVE, COINCIDENT (study/wall_regime.py + wall_regime_lead.py, both years): reads the regime the market is IN
right now; it does NOT lead the forward move (creation-side->forward corr ~0.00). Signals used:
  * REGIME  = break one-sidedness `brk_asym` (study AUC 0.647): trends break walls ONE-sided (~0.61), ranges break
              BOTH sides (~0.42).  brk_asym = |R_breaks - S_breaks| / breaks.
  * BIAS    = CREATION side (study corr -0.645): a wave of RESISTANCE births (sellers rejecting highs) coincides with
              price FALLING; SUPPORT births with rising.  r_create = R_creations / creations.
Inputs are the marks from absorption_level_detect.detect() (reused, not recomputed). A break = a wall whose i1 fell
inside the window and is < n-1 (i1 == n-1 => still active). Fail-safe: returns a not-ready dict.
"""
from __future__ import annotations

W_DEFAULT = 96              # trailing window in bars (24h on 15m; ratios are dimensionless so tf-robust)
MIN_EVENTS = 3             # need at least this many creations AND breaks before classifying
ASYM_TREND = 0.55          # brk_asym >= -> TREND ; <= ASYM_RANGE -> RANGE (study: trend 0.61 / range 0.42)
ASYM_RANGE = 0.45
RC_DOWN = 0.55             # r_create >= -> resistance-heavy -> DOWN bias ; <= RC_UP -> support-heavy -> UP
RC_UP = 0.45


def regime_read(marks, n, W=W_DEFAULT):
    lo = max(0, n - int(W))
    Rc = Sc = Rb = Sb = 0
    ages = []
    for m in marks or ():
        try:
            i0 = int(m["i0"]); i1 = int(m["i1"]); side = m["side"]
        except (KeyError, TypeError, ValueError):
            continue
        if i0 >= lo:                                   # wall CREATED inside the window
            if side == "R":
                Rc += 1
            else:
                Sc += 1
        if lo <= i1 < n - 1:                           # wall BROKEN (mitigated-by-break) inside the window
            if side == "R":
                Rb += 1
            else:
                Sb += 1
            ages.append(i1 - i0)
    ncre = Rc + Sc
    nbrk = Rb + Sb
    ready = ncre >= MIN_EVENTS and nbrk >= MIN_EVENTS
    r_create = (Rc / ncre) if ncre else 0.5
    brk_asym = (abs(Rb - Sb) / nbrk) if nbrk else 0.0
    net_brk = ((Rb - Sb) / nbrk) if nbrk else 0.0
    avg_age = (sum(ages) / len(ages)) if ages else 0.0

    if not ready:
        regime, bias, bias_dir = "—", "—", 0
    else:
        regime = "TREND" if brk_asym >= ASYM_TREND else ("RANGE" if brk_asym <= ASYM_RANGE else "MIXED")
        if r_create <= RC_UP:
            bias, bias_dir = "UP", 1                    # support-heavy creation
        elif r_create >= RC_DOWN:
            bias, bias_dir = "DOWN", -1                 # resistance-heavy creation
        else:
            bias, bias_dir = "FLAT", 0
    return {"ready": ready, "regime": regime, "bias": bias, "bias_dir": bias_dir,
            "brk_asym": brk_asym, "r_create": r_create, "net_brk": net_brk, "avg_age": avg_age,
            "Rc": Rc, "Sc": Sc, "Rb": Rb, "Sb": Sb, "ncre": ncre, "nbrk": nbrk, "window": int(W)}
