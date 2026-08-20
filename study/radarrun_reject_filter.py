"""Does the FRESH-WALL REJECTION filter improve the RADAR RUNNER (the wall BREAK, our one OOS-surviving wall edge)?

Hypothesis (user): a wall that first PROVED itself by rejecting close candles, then gets overrun, is a higher-conviction
breakout -> bigger run -> wider TP viable / fewer failures. Distinct from wall-reject-alone (a bounce play, OOS-dead).

Method: re-implement the SHIPPED Radar Runner detection EXACTLY (chunked AL.detect 6000/1000 overlap, MINVISIT=1,
candle-capped SL capped at the radar extreme, fee 0.04%RT + 0.03% slip, taken() non-overlap). For each breakout event
at bar k off wall (side,P,band,i0) after visit [a,b], compute whether the wall REJECTED close candles BEFORE the break
(causal, window [a, k-1]): a bar whose defended extreme comes within ZW*band of the wall price P and closes in the
defense half (loose) / with a clear rejection WICK (strong). Then compare, per (dataset x tf x TP), three trade streams
each with its OWN taken() non-overlap: ALL / REJ / NO-REJ, split by YEAR (2025 in-sample vs 2026-H1 OOS).

datasets: bucket = study/recon_archive, clock = study/clock_archive. tfs: 5m/15m/30m/1h/4h (no 1m, per user).
python study/radarrun_reject_filter.py [tf ...]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from study.radarrun_winrate_dd import sim
from app import absorption_level_detect as AL

RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003
TPS = [0.002, 0.003, 0.004, 0.005]
SLBUF = {"5m": 0.003, "15m": 0.003, "30m": 0.003, "1h": 0.002, "4h": 0.002}
WICK_MIN = 0.40; CLOSE_FAR = 0.40          # "strong" rejection: clear wick + close in the far/defense end
DATASETS = [("bucket", "study/recon_archive"), ("clock", "study/clock_archive")]


def yr(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).year


def rejected(side, P, band, lo_idx, k, O, Hi, Lo, C, zw, strong):
    """any bar j in [lo_idx, k-1] whose defended extreme is within zw*band of P and rejects in the defense dir."""
    for j in range(max(0, lo_idx), k):
        rng = Hi[j] - Lo[j]
        if rng <= 0:
            continue
        bh = max(O[j], C[j]); bl = min(O[j], C[j]); cp = (C[j] - Lo[j]) / rng
        if side == "R":                                    # resistance: high tests near P, close pushed DOWN
            tested = abs(Hi[j] - P) <= zw * band
            wick = (Hi[j] - bh) / rng
            ok = tested and (wick >= WICK_MIN and cp <= CLOSE_FAR if strong else cp <= 0.5)
        else:                                              # support: low tests near P, close pushed UP
            tested = abs(Lo[j] - P) <= zw * band
            wick = (bl - Lo[j]) / rng
            ok = tested and (wick >= WICK_MIN and cp >= (1.0 - CLOSE_FAR) if strong else cp >= 0.5)
        if ok:
            return True
    return False


def build(A, buf):
    """returns list of events: (k, s, entry, sl, dist, ts, year, flags dict). Detection == shipped, chunked."""
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0 or side not in ("S", "R"):
                continue
            i0 = int(w.get("i0", 0)) + c0
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
                    ev[(k, side)] = (P, band, i0, a); break        # keep first wall that fires this breakout
        if c1 >= n:
            break
        c0 += step - 1000
    out = []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        P, band, i0, a = ev[(k, side)]
        s = 1 if side == "S" else -1; entry = C[k]
        rlo = P - RM * band; rhi = P + RM * band
        sl = max(Lo[k] * (1 - buf), rlo) if s > 0 else min(Hi[k] * (1 + buf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        flags = {
            "strongN": rejected(side, P, band, a, k, O, Hi, Lo, C, 1.0, True),   # strong wick, within 1*band
            "strongW": rejected(side, P, band, a, k, O, Hi, Lo, C, 2.0, True),   # strong wick, within 2*band
            "looseW":  rejected(side, P, band, a, k, O, Hi, Lo, C, 2.0, False),  # any close in defense half, 2*band
            "life":    rejected(side, P, band, i0, k, O, Hi, Lo, C, 1.0, True),  # strong, whole wall life pre-break
        }
        out.append((k, s, entry, sl, dist, float(ST[k]), flags))
    return out, Hi, Lo, C, n


def stream(events, Hi, Lo, C, n, tp, keep):
    """taken() non-overlap over the SUBSET where keep(flags) is True. returns per-year {year:(n,win,avgnet)}."""
    by = {}; last = -1
    for (k, s, entry, sl, dist, ts, flags) in events:
        if keep is not None and not keep(flags):
            continue
        if k <= last:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        by.setdefault(yr(ts), []).append(net); last = k + int(off)
    res = {}
    for y, arr in by.items():
        a = np.array(arr); res[y] = (len(a), 100.0 * (a > 0).mean(), a.mean() * 100.0)
    return res


def fmt(res, y):
    if y not in res:
        return "  n=0            "
    n, w, net = res[y]
    return "n=%-4d win%4.1f%% %+.3f%%" % (n, w, net)


def main():
    tfs = sys.argv[1:] or ["5m", "15m", "30m", "1h", "4h"]
    print("RADAR RUNNER + fresh-wall REJECTION filter | candle-SL | fee 0.04%%RT+0.03%%slip | taken() | OOS 2025/2026\n",
          flush=True)
    REJDEF = os.environ.get("REJDEF", "strongW")   # primary: strong wick within 2*band, visit->break; override to test robustness
    for dsname, root in DATASETS:
        for tf in tfs:
            try:
                A = sorted(load_archive(tf, root=root, drop_degenerate=False)[1],
                           key=lambda b: _f(b.get("start_time", 0)))
            except Exception as e:
                print("== %s %s : load ERR %s" % (dsname, tf, e), flush=True); continue
            if not A:
                print("== %s %s : empty" % (dsname, tf), flush=True); continue
            ev, Hi, Lo, C, n = build(A, SLBUF.get(tf, 0.003))
            nrej = {kk: sum(1 for e in ev if e[6][kk]) for kk in ("strongN", "strongW", "looseW", "life")}
            print("================ %s  %s  (%d breakouts | rej counts: strongN=%d strongW=%d looseW=%d life=%d) ==========="
                  % (dsname.upper(), tf, len(ev), nrej["strongN"], nrej["strongW"], nrej["looseW"], nrej["life"]), flush=True)
            print("   using REJ = %s (strong wick, extreme within 2*band of wall, in the visit->break window)" % REJDEF, flush=True)
            for tp in TPS:
                allr = stream(ev, Hi, Lo, C, n, tp, None)
                rejr = stream(ev, Hi, Lo, C, n, tp, lambda f: f[REJDEF])
                nor  = stream(ev, Hi, Lo, C, n, tp, lambda f: not f[REJDEF])
                print("  TP %.2f%%" % (tp * 100), flush=True)
                for y in (2025, 2026):
                    tag = "in-sample" if y == 2025 else "OOS      "
                    print("    %d %s | ALL  %s | REJ  %s | NOREJ %s"
                          % (y, tag, fmt(allr, y), fmt(rejr, y), fmt(nor, y)), flush=True)
            print("", flush=True)


if __name__ == "__main__":
    main()
