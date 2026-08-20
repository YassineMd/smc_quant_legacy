"""RETEST ENTRY for the Radar Runner (user 2026-08-20): on a signal, place a LIMIT at the broken radar EXTREME instead of
market-at-close — buy signal -> limit at radar_hi (upper extreme), sell -> limit at radar_lo. Fills only if price pulls
back to the extreme within a window; else NO trade (missed). Compare to BASELINE (market@close). TP 0.25% from the actual
entry, candle-capped SL, maker 0.04%RT. Reports fill%, win%, expectancy for both, OOS-split. Live sources 15c/30c/30bkt
(+1h). Walls/radar from the shipped RR detect. IN-SAMPLE. python study/radarrun_retest_entry.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL
FEE, TP, H, RM, MINVISIT, FILLWIN = 0.0004, 0.0025, 200, 3.0, 1, 20
SLBUF = {"15m": 0.003, "30m": 0.003, "1h": 0.002}
CELLS = [("clock", "study/clock_archive", "15m"), ("clock", "study/clock_archive", "30m"),
         ("bucket", "study/recon_archive", "30m"), ("clock", "study/clock_archive", "1h"), ("bucket", "study/recon_archive", "1h")]


def rr_signals(A, buf):
    """chunked RR detect capturing (k, s, entry_close, rlo, rhi, sl_candle, ts)."""
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step)
        for w in AL.detect(A[c0:c1], skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += step - 1000
    sigs = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1
        sl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        sigs.append((k, s, float(C[k]), rlo, rhi, sl, float(ST[k])))
    return sigs, Hi, Lo, C, n


def barrier(s, entry, sl, Hi, Lo, C, j0, n):
    tp = entry * (1 + s * TP); j1 = min(n, j0 + H)
    for j in range(j0, j1):
        hi = Hi[j]; lo = Lo[j]
        if (lo <= sl) if s > 0 else (hi >= sl):
            return s * (sl - entry) / entry - FEE
        if (hi >= tp) if s > 0 else (lo <= tp):
            return s * (tp - entry) / entry - FEE
    return (s * (C[j1 - 1] - entry) / entry - FEE) if j1 > j0 else -FEE


def main():
    print("RR RETEST ENTRY (limit at radar extreme) vs MARKET@close | TP0.25%% candle-SL maker | fill window %d bars | OOS | IN-SAMPLE\n" % FILLWIN, flush=True)
    for dsname, root, tf in CELLS:
        A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        sigs, Hi, Lo, C, n = rr_signals(A, SLBUF.get(tf, 0.003))
        base = []; ret = []; nsig = 0; nfill = 0; last_b = -1; last_r = -1; gain = []
        for (k, s, close, rlo, rhi, sl, ts) in sigs:
            yr = datetime.fromtimestamp(ts, tz=timezone.utc).year
            # BASELINE market@close, non-overlap
            if k > last_b:
                nb = barrier(s, close, sl, Hi, Lo, C, k + 1, n); base.append((nb, yr))
                # advance last_b by holding until resolution (approx: recompute off) -- use a light non-overlap by resolution
            nsig += 1
            # RETEST: limit at extreme (rhi long / rlo short), fill if price reaches it within window
            ext = rhi if s > 0 else rlo; fill_j = None
            for j in range(k + 1, min(n, k + 1 + FILLWIN)):
                if (Lo[j] <= ext) if s > 0 else (Hi[j] >= ext):
                    fill_j = j; break
            if fill_j is not None and k > last_r:
                nfill += 1
                nr = barrier(s, ext, sl, Hi, Lo, C, fill_j + 1, n); ret.append((nr, yr))
                gain.append(s * (close - ext) / close * 100.0)          # entry-price improvement (%)
                last_r = fill_j
            last_b = k
        def st(rows, yr):
            r = [x[0] for x in rows if x[1] == yr]
            if not r:
                return "n=0"
            a = np.array(r) * 100.0
            return "n=%-4d win%4.1f%% exp%+.4f%%" % (len(a), 100.0 * (a > 0).mean(), a.mean())
        print("================ %s %s  (%d signals, fill %.0f%%, avg better entry %.3f%%) ================"
              % (dsname, tf, nsig, 100.0 * nfill / max(1, nsig), np.mean(gain) if gain else 0), flush=True)
        print("  MARKET@close  IS %s | OOS %s" % (st(base, 2025), st(base, 2026)), flush=True)
        print("  RETEST@extreme IS %s | OOS %s" % (st(ret, 2025), st(ret, 2026)), flush=True)
        print("", flush=True)


if __name__ == "__main__":
    main()
