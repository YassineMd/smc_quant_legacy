"""LIVE-TRUST TEST: does a shown 30m Radar Runner signal ever VANISH after more bars arrive (a REPAINT)? This is the
exact scenario 'signal appeared live, I walked away, it was gone on reload'. Method: replay the live edge advancing one
closed bar at a time over the DAEMON data. At each edge E we run the SAME detector the terminal runs -- detect(buckets
up to E, skip_last=True) -- and record the set of signal bars shown. A signal at absolute bar k is SETTLED once the edge
is >= k+2 (k is a closed, non-edge bar). If a SETTLED signal that was shown at edge E1 is ABSENT at some later edge
E2>E1, that is a repaint -> untrustworthy. We report: how many distinct signals ever showed, how many repainted, and a
few concrete cases with the reason (radar band widened / run changed). Left edge fixed at 0 (full history, like the
archive-extend load) so the ONLY thing changing is bars added on the right = future data. python study/radarrun_repaint_stability.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.radarrun_tp_velocity import get_buckets
from app import radar_breakout_detect as RB
from app import absorption_level_detect as AL

SLBUF = 0.003; TPF = 0.005


def sig_set(buckets):
    """Signals the terminal would SHOW with the edge at len(buckets): {k: (radar_lo, radar_hi, side, band)}."""
    out = {}
    for s in RB.detect(buckets, skip_last=True, sl_buf=SLBUF, tp_frac=TPF):
        out[int(s["i"])] = (round(s["radar_lo"], 4), round(s["radar_hi"], 4), int(s["side"]), round(s.get("band", 0.0), 5))
    return out


def main():
    B = get_buckets("30m", {})
    N = len(B)
    WINDOW = min(400, N - 50)                      # replay the last WINDOW edges (bar-by-bar)
    E0 = N - WINDOW
    print("DAEMON 30m: %d buckets. Replaying live edge E = %d..%d (bar-by-bar).\n" % (N, E0, N), flush=True)

    present = {}          # k -> list of edges E where k was SHOWN (as a settled bar, E >= k+2)
    firstseen = {}        # k -> first edge shown
    details = {}          # k -> (radar tuple) at first seen, for change diagnosis
    last_seen_at = {}     # k -> latest edge where present
    for E in range(E0, N + 1):
        s = sig_set(B[:E])
        for k, meta in s.items():
            if E >= k + 2:                          # k is a SETTLED closed bar at this edge (not the forming edge)
                present.setdefault(k, []).append(E)
                if k not in firstseen:
                    firstseen[k] = E; details[k] = meta
                last_seen_at[k] = E

    # A signal REPAINTED if, after first being shown as settled at E1, it is ABSENT at some later edge E2 (k<=E2-2).
    repainted = []; stable = []
    for k, edges in present.items():
        e1 = edges[0]; e_last = edges[-1]
        # every edge from e1..N should contain k if stable; find the first missing one
        eset = set(edges); missing = [E for E in range(e1, N + 1) if E >= k + 2 and E not in eset]
        if missing:
            repainted.append((k, e1, missing[0], details[k]))
        else:
            stable.append(k)

    tot = len(present)
    print("SETTLED signals that were shown at least once: %d" % tot, flush=True)
    print("  STABLE  (never vanished once settled): %d" % len(stable), flush=True)
    print("  REPAINTED (vanished after being shown): %d   <-- these are the danger" % len(repainted), flush=True)
    if tot:
        print("  repaint rate = %.0f%% of shown signals\n" % (100.0 * len(repainted) / tot), flush=True)

    for (k, e1, evanish, meta) in sorted(repainted)[:8]:
        # diagnose: compare the radar/band for the wall at bar k, edge e1 (shown) vs edge evanish (gone)
        s1 = sig_set(B[:e1]).get(k); s2 = sig_set(B[:evanish]).get(k)
        entry = None
        for ss in RB.detect(B[:e1], skip_last=True, sl_buf=SLBUF, tp_frac=TPF):
            if int(ss["i"]) == k:
                entry = ss["entry"]; break
        print("  signal @bar %d: SHOWN at edge %d (radar_lo/hi=%.4f/%.4f side=%d), GONE by edge %d" % (
            k, e1, meta[0], meta[1], meta[2], evanish), flush=True)
        print("      at vanish-edge the detector returns for bar %d: %s   (entry was %.4f)" % (
            k, ("radar %.4f/%.4f" % (s2[0], s2[1])) if s2 else "NO SIGNAL", entry or 0.0), flush=True)


if __name__ == "__main__":
    main()
