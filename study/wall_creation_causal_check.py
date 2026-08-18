"""IS THE ROOM-FILTER EDGE REAL OR LOOK-AHEAD? The wall's radar band in absorption_level_detect.detect() uses the
FINAL ejection (favorable excursion over EJ_WIN=10 bars AFTER formation). Entering AT the formation bar, that width is
NOT knowable -> the room filter (which uses radar_hi/radar_lo) peeks at the future. Here we recompute the radar with the
CAUSAL band known at the entry bar's close (ej=0 -> band = P*vpct*BAND_MIN, exactly what detect would emit at i0), and
re-run. If the room-filtered edge collapses to ~coin-flip, it was look-ahead. python study/wall_creation_causal_check.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from study.wall_creation_entry import detect_walls

ATR_WIN = 50; BAND_MIN = 0.10; RM = 3.0; FEE = 0.0004; SLIP = 0.0003; H = 200


def run(A, tf, mode, val, min_room, causal):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    vpct = np.zeros(n); s = 0.0
    for i in range(n):
        s += (Hi[i] - Lo[i]) / C[i] if C[i] > 0 else 0.0
        if i >= ATR_WIN:
            s -= (Hi[i - ATR_WIN] - Lo[i - ATR_WIN]) / C[i - ATR_WIN] if C[i - ATR_WIN] > 0 else 0.0
        vpct[i] = s / min(i + 1, ATR_WIN)
    buf = 0.002 if tf == "1h" else 0.003
    tr = []; last = -1
    for (i0, side, P, band_final) in detect_walls(A):
        if i0 < 1 or i0 + 1 >= n:
            continue
        sgn = 1 if side == "S" else -1; entry = C[i0]
        band = (P * vpct[i0] * BAND_MIN) if causal else band_final       # CAUSAL: formation band (ej=0) vs FINAL (look-ahead)
        rlo = P - RM * band; rhi = P + RM * band
        sl = max(Lo[i0] * (1 - buf), rlo) if sgn > 0 else min(Hi[i0] * (1 + buf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        room = (rhi - entry) / entry if sgn > 0 else (entry - rlo) / entry
        if room < min_room or i0 <= last:
            continue
        tp_frac = (val * dist) if mode == "rr" else val
        j0 = i0 + 1; j1 = min(n, i0 + 1 + H)
        outc, gross, off = sim(sgn, entry, entry * (1 + sgn * tp_frac), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((net, net / dist)); last = i0 + int(off)
    return tr


def main():
    for mode, val, tplab in (("fix", 0.002, "0.2% TP"), ("rr", 1.0, "1:1 TP")):
        print("\n====  room>=1.0xTP,  %s  —  LOOK-AHEAD band vs CAUSAL band  ====" % tplab, flush=True)
        print("  %-4s %-6s | %-22s | %-22s" % ("tf", "data", "LOOK-AHEAD (n/win/expR)", "CAUSAL (n/win/expR/DD)"), flush=True)
        for tf in ("15m", "30m", "1h"):
            for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
                A = get_buckets(tf, root)
                la = run(A, tf, mode, val, 0.002, causal=False)
                ca = run(A, tf, mode, val, 0.002, causal=True)
                def fmt(tr):
                    if len(tr) < 8:
                        return "n=%-4d (too few)" % len(tr)
                    net = np.array([t[0] for t in tr]); rs = [t[1] for t in tr]
                    return "n=%-4d win=%2.0f%% expR=%+.3f" % (len(tr), 100 * (net > 0).mean(), np.mean(rs))
                caf = fmt(ca)
                if len(ca) >= 8:
                    caf += " DD=%.0f%%" % maxdd_pct([t[1] for t in ca])
                print("  %-4s %-6s | %-22s | %-22s" % (tf, ds, fmt(la), caf), flush=True)


if __name__ == "__main__":
    main()
