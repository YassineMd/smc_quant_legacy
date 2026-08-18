"""WALL-CREATION ENTRY strategy: enter at the CLOSE of the candle that CREATES an order-flow wall, in the wall's defense
direction (support wall -> LONG, resistance wall -> SHORT). Same bracket as the Radar Runner: candle-capped SL (buf
0.2% on 1h / 0.3% else, capped at the wall's radar extreme P +/- 3*band) + fixed TP. Differs from Radar Runner, which
waits for the radar BREAKOUT; this enters immediately on wall birth. All tf except 1m; non-overlap book; RECON + DAEMON;
prop MC (target10/DD10/daily5, R0.5). Cold-boot junk auto-filtered by load_archive. DESCRIPTIVE. 3bps slip / 0.04% fee.
python study/wall_creation_entry.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; H = 200; FEE = 0.0004; SLIP = 0.0003
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 6000; MAXD = 500
TFS = ("5m", "15m", "30m", "1h", "4h")


def detect_walls(A):
    """Every wall CREATION over the full history: (i0, side, price, band). Chunked (6000/step 5000) + deduped by (i0,side)."""
    n = len(A); walls = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0 or side not in ("S", "R"):
                continue
            i0 = int(w.get("i0", 0)) + c0
            if 0 <= i0 < n:
                walls.setdefault((i0, side), (i0, side, P, band))
        if c1 >= n:
            break
        c0 += step - 1000
    return sorted(walls.values())


def signals(A, tf):
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    buf = 0.002 if tf == "1h" else 0.003
    out = []
    for (i0, side, P, band) in detect_walls(A):
        if i0 < 1 or i0 + 1 >= n:
            continue
        s = 1 if side == "S" else -1                     # support -> long, resistance -> short
        entry = C[i0]; rlo = P - RM * band; rhi = P + RM * band
        sl = max(Lo[i0] * (1 - buf), rlo) if s > 0 else min(Hi[i0] * (1 + buf), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        # ROOM = space from the entry CLOSE to the OPPOSITE radar extreme (the profit-side edge of the wall's radar):
        # long -> up to radar_hi, short -> down to radar_lo. A small room = the close is already jammed against the far
        # edge, so the TP can't be reached inside the wall's structure. Filter on this downstream.
        room = (rhi - entry) / entry if s > 0 else (entry - rlo) / entry
        out.append((i0, s, entry, sl, dist, float(ST[i0]), room))
    return out, Hi, Lo, C, n


def book(sigs_pack, mode, val, min_room=0.0):
    """Non-overlap one-at-a-time: (ts, net, R). mode 'fix' -> TP=val (fixed %); mode 'rr' -> TP=val*SL-dist. min_room
    filters out signals whose close-to-opposite-radar-extreme room is below the threshold (before non-overlap)."""
    sigs, Hi, Lo, C, n = sigs_pack; tr = []; last = -1
    for (k, s, entry, sl, dist, ts, room) in sigs:
        if room < min_room:                                  # not enough room to the opposite radar extreme -> skip
            continue
        if k <= last:
            continue
        tp_frac = (val * dist) if mode == "rr" else val      # 1:1 -> TP distance == SL distance
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * tp_frac), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        tr.append((ts, net, net / dist)); last = k + int(off)
    return tr


def prop_mc(tr):
    by = {}
    for ts, _n, r in tr:
        by.setdefault(datetime.fromtimestamp(ts, tz=timezone.utc).date(), []).append(r)
    if not by:
        return 0.0, [0, 0, 0]
    d0, d1 = min(by), max(by); days = []; d = d0
    while d <= d1:
        days.append(by.get(d, [])); d += timedelta(days=1)
    passes = 0; dtp = []
    for _ in range(NMC):
        eq = peak = 0.0; passed = failed = False
        for di in range(1, MAXD + 1):
            day = days[random.randrange(len(days))]; dstart = eq; dlow = eq
            for r in day:
                eq += 0.5 * r; dlow = min(dlow, eq); peak = max(peak, eq)
                if peak - eq >= MAXDD:
                    failed = True; break
                if eq >= TARGET:
                    passed = True; break
            if failed or (dstart - dlow) >= DAILY:
                failed = True
            if passed or failed:
                if passed:
                    passes += 1; dtp.append(di)
                break
    q = np.percentile(dtp, [25, 50, 75]) if dtp else [0, 0, 0]
    return 100.0 * passes / NMC, q


def main():
    packs = {}                                               # detect walls ONCE per (tf, dataset)
    for tf in TFS:
        for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
            try:
                packs[(tf, ds)] = signals(get_buckets(tf, root), tf)
            except Exception as e:
                packs[(tf, ds)] = e
    for mr, label in ((0.0, "0.2% TP  no room filter"), (0.002, "0.2% TP  room >= 1.0x TP"),
                      (0.003, "0.2% TP  room >= 1.5x TP"), (0.004, "0.2% TP  room >= 2.0x TP")):
        print("\n################  WALL-CREATION ENTRY  @ %s  ################" % label, flush=True)
        print("  %-4s %-6s %5s %6s %8s %8s %-18s" % ("tf", "data", "n", "win%", "expR", "realDD%", "prop pass / days"), flush=True)
        for tf in TFS:
            for ds in ("RECON", "DAEMON"):
                pk = packs[(tf, ds)]
                if isinstance(pk, Exception):
                    print("  %-4s %-6s  skipped: %s" % (tf, ds, pk)); continue
                tr = book(pk, "fix", 0.002, mr)
                if len(tr) < 8:
                    print("  %-4s %-6s %5d (too few)" % (tf, ds, len(tr))); continue
                net = np.array([t[1] for t in tr]); rs = [t[2] for t in tr]
                p, q = prop_mc(tr)
                print("  %-4s %-6s %5d %5.0f%% %+8.3f %8.1f  %3.0f%% / %d/%d/%d" % (
                    tf, ds, len(tr), 100 * (net > 0).mean(), np.mean(rs), maxdd_pct(rs),
                    p, q[0], q[1], q[2]), flush=True)


if __name__ == "__main__":
    main()
