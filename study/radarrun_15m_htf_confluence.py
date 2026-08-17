"""Does 15m Radar Runner @0.2% get RESCUED by HTF-wall confluence? Take only 15m signals whose breakout price sits
INSIDE an active 30m and/or 1h wall's radar zone. Cohorts: ALL | in-30m | in-1h | in-EITHER. Filter FIRST, then
non-overlap within the cohort (you only trade confluent signals, one at a time). Report retention, win%, expR, realized
DD, prop pass%/days (R0.5). PLACEBO control: repeat with wall levels replaced by a RANDOM real price from the same
window (same count/width/time) -- if in-wall ~= in-placebo, the 'confluence' is just small-subset selection, not an edge.
RECON + DAEMON. 3bps slip / 0.04% fee. python study/radarrun_15m_htf_confluence.py"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
import numpy as np
from study.candle_bias_1h import _f
from study.radarrun_tp_velocity import get_buckets
from study.radarrun_winrate_dd import sim, maxdd_pct
from study.radarrun_htf_confluence_dd import build_htf_walls, inside
from app import absorption_level_detect as AL

random.seed(7); np.random.seed(7)
RM = 3.0; MINVISIT = 1; H = 200; FEE = 0.0004; SLIP = 0.0003; SLBUF = 0.003; TP = 0.002
TARGET, MAXDD, DAILY = 10.0, 10.0, 5.0; NMC = 8000; MAXD = 400


def detect15(A):
    """All 15m signals @0.2% TP: list of (k, exit_bar, ts, entry, R) + ST/C arrays (for placebo price sampling)."""
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A]); C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A]); ST = np.array([_f(b.get("start_time")) for b in A])
    ev = {}; c0 = 0; step = 6000
    while c0 < n:
        c1 = min(n, c0 + step); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False, radar_mult=RM):
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
        rlo, rhi = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        j0 = k + 1; j1 = min(n, k + 1 + H)
        outc, gross, off = sim(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1], C[j0:j1])
        net = gross - FEE - SLIP - (SLIP if outc != "tp" else 0.0)
        sigs.append((k, k + int(off), float(ST[k]), float(entry), net / dist))
    return sigs, ST, C


def placebo(walls, ST, C):
    """Same time windows + widths, but each level moved to a RANDOM real 15m close from within that window."""
    out = []
    for (t0, t1, P, rad) in walls:
        idx = np.where((ST >= t0) & (ST <= t1))[0]
        if len(idx) == 0:
            continue
        out.append((t0, t1, float(C[idx[np.random.randint(len(idx))]]), rad))
    return out


def book(cohort):
    """Non-overlap within the cohort: walk by k, skip while in a trade. Returns [(ts, R)]."""
    last = -1; tr = []
    for (k, ex, ts, entry, R) in sorted(cohort):
        if k <= last:
            continue
        tr.append((ts, R)); last = ex
    return tr


def prop_mc(tr):
    by = {}
    for ts, r in tr:
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


def rep(label, cohort, n_all):
    tr = book(cohort)
    if len(tr) < 8:
        print("  %-20s n=%-4d (too few)" % (label, len(tr))); return
    rs = np.array([t[1] for t in tr])
    p, q = prop_mc(tr)
    print("  %-20s n=%-4d ret=%3.0f%% win=%2.0f%% expR=%+.3f DD=%4.1f%% pass=%3.0f%% days=%d/%d/%d" % (
        label, len(tr), 100.0 * len(tr) / max(1, n_all), 100 * (rs > 0).mean(), rs.mean(),
        maxdd_pct(list(rs)), p, q[0], q[1], q[2]), flush=True)


def main():
    for ds, root in (("RECON", {"root": "study/recon_archive"}), ("DAEMON", {})):
        sigs, ST, C = detect15(get_buckets("15m", root))
        w30 = build_htf_walls("30m", root); w1h = build_htf_walls("1h", root)
        p30 = placebo(w30, ST, C); p1h = placebo(w1h, ST, C)
        n_all = len(book(sigs))
        def tag(sig, wa, wb=None):
            _, _, ts, entry, _ = sig
            return inside(wa, ts, entry) or (wb is not None and inside(wb, ts, entry))
        print("\n################  15m @0.2%%  %s  (all n=%d) ################" % (ds, n_all), flush=True)
        rep("ALL (baseline)", sigs, n_all)
        rep("in 30m wall", [s for s in sigs if tag(s, w30)], n_all)
        rep("in 1h wall", [s for s in sigs if tag(s, w1h)], n_all)
        rep("in 30m|1h wall", [s for s in sigs if tag(s, w30, w1h)], n_all)
        print("  -- placebo control (random levels, same width/time) --", flush=True)
        rep("in PLACEBO 30m", [s for s in sigs if tag(s, p30)], n_all)
        rep("in PLACEBO 1h", [s for s in sigs if tag(s, p1h)], n_all)
        rep("in PLACEBO 30m|1h", [s for s in sigs if tag(s, p30, p1h)], n_all)


if __name__ == "__main__":
    main()
