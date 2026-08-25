"""LONG WICK @ WALL — HONEST test, 30m BUCKET (user 2026-08-25). SL 0.1% beyond the candle extreme; TP ladder
0.2/0.25/0.3/0.4/0.5% NET + RR 1:1. Canonical standards (study/HONEST_TEST_PROMPT.md):
  SIGNALS  = the SHIPPED app/longwick_detect geometry+wall rule, evaluated CAUSALLY per bar close: walls =
             absorption_level_detect.detect over the TRAILING W=2000 window ENDING at the bar (terminal-identical,
             frozen at the bar -> no wall repaint). Geometry pre-filter first (cheap), walls only for candidates.
  ENTRY    = signal candle CLOSE. SL = candle_high*(1+0.001) for SHORT (red ♦) / candle_low*(1-0.001) for LONG.
  EXITS    = fixed NET TPs (gross = net + 0.04% maker RT): 0.2->0.0024, 0.25->0.0029, 0.3->0.0034, 0.4->0.0044,
             0.5->0.0054; plus RR 1:1 on the SL distance. 1-MINUTE first-touch, SL-first ties, fees 0.04% RT +
             0.03% slip/leg, NON-OVERLAP taken(), prop first-attempt MC — all via the canonical eval_1m.
  ERAS     = recon 2025 + 2026 (per-year split in fmt) and DAEMON 30m = decisive OOS. ALL/LONG/SHORT splits.
  NO iteration beyond this pre-registration. python study/longwick_30mbkt_honest.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

W = 2000
SLB = 0.001
TPS = [("0.2% net", "fix", 0.0024), ("0.25% net", "fix", 0.0029), ("0.3% net", "fix", 0.0034),
       ("0.4% net", "fix", 0.0044), ("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)]
_A = None


def _init(daemon):
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    if daemon:
        _A = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    else:
        _A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1],
                    key=lambda b: _f(b.get("start_time", 0)))


def _geom_candidates(A):
    """Cheap pre-filter: bar indices passing the wick geometry (either direction), with side."""
    from study.candle_bias_1h import _f
    out = []
    for i, b in enumerate(A):
        o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
        h = _f(b.get("high")); l = _f(b.get("low"))
        if o <= 0 or c <= 0 or h <= l:
            continue
        body = abs(c - o); uw = h - max(o, c); lw = min(o, c) - l
        if body <= 0:
            continue
        if c < o and uw > body and uw >= 2.0 * lw:       # v2 geometry (968730b): 2x dominance, off-wick unconstrained
            out.append((i, -1))
        elif c > o and lw > body and lw >= 2.0 * uw:
            out.append((i, 1))
    return out


def _work(chunk):
    """Confirm wall qualification for geometry candidates: shipped longwick_detect on the trailing window."""
    from app import absorption_level_detect as AL
    from app import longwick_detect as LW
    from study.candle_bias_1h import _f
    out = []
    for (i, side) in chunk:
        lo = max(0, i - W)
        sub = _A[lo:i + 1]
        try:
            walls = AL.detect(sub, skip_last=False)
            marks = LW.detect(sub, walls, skip_last=False)
        except Exception:
            continue
        if any(int(e["i"]) == len(sub) - 1 and int(e["side"]) == side for e in marks):
            b = _A[i]
            e_ = _f(b.get("close", b.get("close_price")))
            sl = _f(b.get("high")) * (1 + SLB) if side < 0 else _f(b.get("low")) * (1 - SLB)
            out.append((i, _f(b.get("end_time")), side, e_, sl))
    return out


def signals(daemon):
    import multiprocessing as mp
    _init(daemon)
    cands = _geom_candidates(_A)
    print("  geometry candidates: %d / %d bars" % (len(cands), len(_A)), flush=True)
    chunks = [cands[k:k + 200] for k in range(0, len(cands), 200)]
    fires = []
    with mp.Pool(6, initializer=_init, initargs=(daemon,)) as pool:
        for j, res in enumerate(pool.imap(_work, chunks), 1):
            fires += res
            if j % 5 == 0 or j == len(chunks):
                print("  wall-confirm chunk %d/%d fires %d" % (j, len(chunks), len(fires)), flush=True)
    return sorted(fires)


def report(fires, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for label, keep in (("ALL", lambda s: True), ("LONG", lambda s: s > 0), ("SHORT", lambda s: s < 0)):
        fs = [f for f in fires if keep(f[2])]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-5s %-9s %s" % (label, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    print("LONG WICK @ WALL — 30m BUCKET HONEST TEST | shipped detector, trailing-%d walls | SL 0.1%% beyond extreme"
          " | 1m first-touch | non-overlap | fees+slip\n" % W, flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    fr = signals(daemon=False)
    report(fr, T1, H1, L1)
    del T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    frd = signals(daemon=True)
    report(frd, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
