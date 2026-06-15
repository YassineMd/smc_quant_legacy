"""Step 5 authoritative synthetic gate (rule 0.7): adaptive exhaustion (Mode 3).

History-independent, deterministic. Asserts the new exhaustion multipliers are
SCALE-INVARIANT (react to relative effort extremes via a z-score, not absolute
E/R cutoffs) and degenerate-safe (cold start / dead-flat / extreme-z all stay
neutral or bounded, never explode). Imports the module-level helpers from
terminal.py (no Qt window needed).

Run:
    python scripts/test_step5_exhaustion.py
Exit 0 = all assertions passed.
"""

from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # terminal.py pulls in Qt on import

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.terminal import (                                # noqa: E402
    _exh_z_mult, _exhaustion_mults,
    EXH_WINDOW, EXH_MIN_WINDOW, EXH_K, EXH_OI_K)

_FAILS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""))
    if not cond:
        _FAILS.append(label)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def mk(buyer_er=0.0, seller_er=0.0, opL=0.0, opS=0.0, clL=0.0, clS=0.0, cv=1000.0):
    return {"buyer_er": buyer_er, "seller_er": seller_er, "opL": opL, "opS": opS,
            "clL": clL, "clS": clS, "curr_vol": cv}


def main() -> int:
    K_HI, OI_HI, OI_LO = math.exp(EXH_K), math.exp(EXH_OI_K), math.exp(-EXH_OI_K)

    # === 1. SCALE-INVARIANCE — the headline ================================
    # Same absolute E/R (200) fires in a LOW-baseline regime, NOT in a HIGH one.
    low = [mk(buyer_er=80.0 + (i % 7) * 7) for i in range(EXH_WINDOW)] + [mk(buyer_er=200.0)]
    high = [mk(buyer_er=900.0 + (i % 7) * 30) for i in range(EXH_WINDOW)] + [mk(buyer_er=200.0)]
    bl, _, _ = _exhaustion_mults(low, len(low) - 1)
    bh, _, _ = _exhaustion_mults(high, len(high) - 1)
    print(f"[1] scale-invariance: er=200  low-baseline mult={bl:.3f}  high-baseline mult={bh:.3f}")
    check("er=200 FIRES (mult>1) when anomalously high vs a low-baseline window", bl > 1.0)
    check("SAME er=200 does NOT fire (mult<1) in a high-baseline regime", bh < 1.0)
    check("exhaustion is RELATIVE not absolute (low-baseline mult >> high-baseline)",
          bl > 2.0 * bh, f"{bl:.3f} vs {bh:.3f}")

    # === 2. COLD START (rule 0.6/2) ========================================
    check("cold start: window < MIN_WINDOW -> neutral 1.0 (no z-score)",
          approx(_exh_z_mult([100.0] * (EXH_MIN_WINDOW - 1), 9999.0), 1.0))
    check("cold start holds even for an extreme value (no premature firing)",
          approx(_exh_z_mult([1.0] * (EXH_MIN_WINDOW - 1), 1e9), 1.0))

    # === 3. DEAD-FLAT / ZERO WINDOW (rule 0.6/1) ===========================
    flat = [500.0] * EXH_WINDOW
    check("dead-flat window, val==mean -> neutral 1.0 (no div-by-zero)",
          approx(_exh_z_mult(flat, 500.0), 1.0))
    m_flat = _exh_z_mult(flat, 600.0)
    check("dead-flat window, val above -> BOUNDED via CoV floor, not exploded",
          math.isfinite(m_flat) and 1.0 < m_flat <= K_HI + 1e-9, f"{m_flat:.4f}")
    check("all-zero window (mean=0,std=0) -> neutral 1.0 (denom guard)",
          approx(_exh_z_mult([0.0] * EXH_WINDOW, 0.0), 1.0))

    # === 4. BOUNDEDNESS — tanh saturates, never explodes ===================
    big = _exh_z_mult([100.0 + (i % 3) for i in range(EXH_WINDOW)], 1e12)
    check("extreme z -> multiplier saturates at exp(K) (BOUNDED, no 1e7 blowup)",
          approx(big, K_HI, 1e-6), f"{big:.4f} == exp(K) {K_HI:.4f}")
    tiny = _exh_z_mult([100.0 + (i % 3) for i in range(EXH_WINDOW)], -1e12)
    check("extreme negative z -> multiplier saturates at exp(-K) (bounded floor)",
          approx(tiny, 1.0 / K_HI, 1e-6), f"{tiny:.4f}")

    # === 5. OI-DIRECTION term from the Step-3 delta_oi =====================
    warm = [mk(buyer_er=100.0, seller_er=100.0) for _ in range(EXH_WINDOW)]
    closing = warm + [mk(100.0, 100.0, opL=50, opS=50, clL=300, clS=300, cv=1000)]   # doi=-500
    opening = warm + [mk(100.0, 100.0, opL=300, opS=300, clL=50, clS=50, cv=1000)]   # doi=+500
    neutral = warm + [mk(100.0, 100.0, opL=100, opS=100, clL=100, clS=100, cv=1000)]  # doi=0
    _, _, oi_close = _exhaustion_mults(closing, len(closing) - 1)
    _, _, oi_open = _exhaustion_mults(opening, len(opening) - 1)
    _, _, oi_neu = _exhaustion_mults(neutral, len(neutral) - 1)
    print(f"[5] OI term: closing={oi_close:.3f}  opening={oi_open:.3f}  neutral={oi_neu:.3f}")
    check("OI contracting (delta_oi<0) AMPLIFIES exhaustion (oi_mult>1)", oi_close > 1.0)
    check("OI expanding (delta_oi>0) DAMPENS exhaustion (oi_mult<1)", oi_open < 1.0)
    check("delta_oi==0 -> neutral OI multiplier 1.0", approx(oi_neu, 1.0))
    check("OI term smooth + BOUNDED to [exp(-OI_K), exp(OI_K)]",
          OI_LO - 1e-9 <= oi_open and oi_close <= OI_HI + 1e-9)

    # === 6. continuity (no rigid tiers) ====================================
    win = [mk(buyer_er=100.0) for _ in range(EXH_WINDOW)]
    seq = [_exh_z_mult([w["buyer_er"] for w in win], v) for v in (140, 150, 160, 300, 310)]
    mono = all(seq[i] <= seq[i + 1] + 1e-12 for i in range(len(seq) - 1))
    no_jump = all(abs(seq[i + 1] - seq[i]) < 0.5 for i in range(len(seq) - 1))  # smooth, no tier step
    print(f"[6] continuity across er 140->310: {[round(s,3) for s in seq]}")
    check("multiplier is monotonic and SMOOTH across the old 150/300 tier edges (no step)",
          mono and no_jump)

    print("\n" + "=" * 60)
    if _FAILS:
        print(f"RESULT: FAILED ({len(_FAILS)}): {_FAILS}")
        return 1
    print("RESULT: ALL ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
