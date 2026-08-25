"""SESSION RANGE FIX TIME — 15m bucket, DESCRIPTIVE (user 2026-08-25: "up to a certain point during a session a
range gets created and tends to be respected; find when it gets fixed per session; trending days don't respect it").

PRE-REGISTERED (frozen before results; study-phase = descriptive reliability, NO tradeability verdict):
  Sessions (canonical terminal windows, UTC): Tokyo 00-08 / London 08-13 / New York 13-21.
  Instance = (UTC day, session); 15m VOLUME buckets assigned by start_time; kept if >= 8 buckets AND first bucket
  starts within the session's first 20% AND last bucket ends within the last 20% (coverage guard).
  FIX FRACTION  = elapsed session fraction at which BOTH the session's final high and final low are set
                  (time of the LAST new extreme, either side).
  CONTAINMENT   = at checkpoints f in {0.2..0.8 step 0.1}: share of instances where NO later bucket exceeds the
                  range-so-far — STRICT, and with a WICK TOLERANCE of 10% of the range-so-far width.
  TREND SPLIT   = session efficiency |close-open|/range: TREND >= 0.6 / RANGE <= 0.4 / MIXED else.
                  (HINDSIGHT classification — characterizes, does not predict.)
  NULL BASELINE = per instance, 20 within-session ORDER SHUFFLES of the same 15m candles (preserves net drift +
                  candle vol, destroys timing) -> same fix-fraction stat. The real fix time is only "early" where
                  it beats this (arcsine-law guard, cf. london-midsession-reversal-null).
  Eras reported separately: recon 2025 / recon 2026 / daemon (2026-06-20..). python study/session_range_fix_15m.py"""
import os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

SESSIONS = (("Tokyo", 0, 8), ("London", 8, 13), ("NY", 13, 21))
CHECKS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
N_SHUF = 20


def _f(b, k, alt=None):
    v = b.get(k)
    if v is None and alt is not None:
        v = b.get(alt)
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load(era):
    from study.archive_loader import load_archive
    if era == "daemon":
        A = load_archive("15m", drop_degenerate=True)[1]
    else:
        A = load_archive("15m", root="study/recon_archive", drop_degenerate=False)[1]
    return sorted(A, key=lambda b: _f(b, "start_time"))


def instances(A):
    """{(date, sess_name): [(t_frac_start, t_frac_end, o, h, l, c), ...]} with coverage guard."""
    out = {}
    for b in A:
        st = _f(b, "start_time"); et = _f(b, "end_time") or st + 900
        d = datetime.fromtimestamp(st, timezone.utc)
        for name, h0, h1 in SESSIONS:
            s0 = d.replace(hour=h0, minute=0, second=0, microsecond=0).timestamp()
            s1 = s0 + (h1 - h0) * 3600
            if s0 <= st < s1:
                span = s1 - s0
                out.setdefault((d.date(), name), []).append(
                    ((st - s0) / span, min(1.0, (et - s0) / span),
                     _f(b, "open", "open_price"), _f(b, "high"), _f(b, "low"), _f(b, "close", "close_price")))
                break
    keep = {}
    for k, rows in out.items():
        rows.sort()
        if len(rows) >= 8 and rows[0][0] <= 0.20 and rows[-1][1] >= 0.80:
            keep[k] = rows
    return keep


def fix_fraction(rows):
    """Elapsed fraction of the LAST new session extreme (either side)."""
    hi = -1e18; lo = 1e18; t_hi = t_lo = 0.0
    for (t0, t1, o, h, l, c) in rows:
        if h > hi:
            hi = h; t_hi = t1
        if l < lo:
            lo = l; t_lo = t1
    return max(t_hi, t_lo)


def contained_at(rows, f, tol_frac):
    """True if no bucket after checkpoint f exceeds the range formed up to f (± tol_frac of that range)."""
    hi = -1e18; lo = 1e18; seen = False
    for (t0, t1, o, h, l, c) in rows:
        if t1 <= f:
            hi = max(hi, h); lo = min(lo, l); seen = True
    if not seen or hi <= lo:
        return None
    tol = (hi - lo) * tol_frac
    for (t0, t1, o, h, l, c) in rows:
        if t1 > f and (h > hi + tol or l < lo - tol):
            return False
    return True


def shuffle_fix(rows, rng):
    """Fix-fraction of the same candles in shuffled order (re-chained; extremes keep their candle-relative shape)."""
    rel = [(h - o, l - o, c - o) for (_, _, o, h, l, c) in rows]
    order = list(range(len(rel))); rng.shuffle(order)
    o0 = rows[0][2]; px = o0
    hi = -1e18; lo = 1e18; t_hi = t_lo = 0.0
    n = len(order)
    for j, idx in enumerate(order):
        dh, dl, dc = rel[idx]
        h = px + dh; l = px + dl; px = px + dc
        tf = (j + 1) / n
        if h > hi:
            hi = h; t_hi = tf
        if l < lo:
            lo = l; t_lo = tf
    return max(t_hi, t_lo)


def eff(rows):
    o = rows[0][2]; c = rows[-1][5]
    hi = max(r[3] for r in rows); lo = min(r[4] for r in rows)
    return abs(c - o) / (hi - lo) if hi > lo else 0.0


def report(era, insts):
    rng = random.Random(11)
    by = {}
    for (d, name), rows in insts.items():
        by.setdefault(name, []).append((d, rows))
    for name, _h0, _h1 in SESSIONS:
        lst = by.get(name, [])
        if len(lst) < 20:
            print("  %-7s n=%d — too few instances" % (name, len(lst)), flush=True)
            continue
        fixes = []; sh_fixes = []; effs = []
        for d, rows in lst:
            fixes.append(fix_fraction(rows)); effs.append(eff(rows))
            for _ in range(N_SHUF):
                sh_fixes.append(shuffle_fix(rows, rng))
        fixes = np.array(fixes); sh = np.array(sh_fixes); effs = np.array(effs)
        span_h = _h1 - _h0

        def clk(f):
            m = int(round(f * span_h * 60))
            return "%02d:%02d" % (_h0 + m // 60, m % 60)
        print("  %-7s n=%d | trend %.0f%% / mixed %.0f%% / range %.0f%%" % (
            name, len(lst), 100 * (effs >= 0.6).mean(), 100 * ((effs > 0.4) & (effs < 0.6)).mean(),
            100 * (effs <= 0.4).mean()), flush=True)
        print("    fix fraction  p25/med/p75: %.2f/%.2f/%.2f (=%s/%s/%s UTC) | SHUFFLED null med %.2f -> real-med - null-med = %+.2f"
              % (*np.percentile(fixes, (25, 50, 75)), clk(np.percentile(fixes, 25)), clk(np.percentile(fixes, 50)),
                 clk(np.percentile(fixes, 75)), np.median(sh), np.median(fixes) - np.median(sh)), flush=True)
        for label, mask in (("ALL  ", np.ones(len(lst), bool)), ("RANGE", effs <= 0.4), ("TREND", effs >= 0.6)):
            sub = [lst[i] for i in range(len(lst)) if mask[i]]
            if len(sub) < 10:
                print("    %s containment: n=%d too few" % (label, len(sub)), flush=True)
                continue
            row_s = []; row_t = []
            for f in CHECKS:
                cs = [contained_at(rows, f, 0.0) for _, rows in sub]
                ct = [contained_at(rows, f, 0.10) for _, rows in sub]
                cs = [x for x in cs if x is not None]; ct = [x for x in ct if x is not None]
                row_s.append(100 * np.mean(cs) if cs else float("nan"))
                row_t.append(100 * np.mean(ct) if ct else float("nan"))
            print("    %s contain%% strict  @ %s : %s" % (label, "/".join("%.0f" % (100 * f) for f in CHECKS),
                                                          " ".join("%3.0f" % v for v in row_s)), flush=True)
            print("    %s contain%% tol10%%  @ %s : %s" % (label, "/".join("%.0f" % (100 * f) for f in CHECKS),
                                                           " ".join("%3.0f" % v for v in row_t)), flush=True)


def main():
    print("SESSION RANGE FIX TIME — 15m bucket | Tokyo 00-08 / London 08-13 / NY 13-21 UTC | pre-registered (header)\n", flush=True)
    t0 = time.time()
    A = load("recon")
    for era, lo, hi in (("RECON 2025", "2025-01-01", "2026-01-01"), ("RECON 2026", "2026-01-01", "2026-06-20")):
        loT = datetime.fromisoformat(lo).replace(tzinfo=timezone.utc).timestamp()
        hiT = datetime.fromisoformat(hi).replace(tzinfo=timezone.utc).timestamp()
        sub = [b for b in A if loT <= _f(b, "start_time") < hiT]
        print("=" * 110, flush=True)
        print(era, flush=True)
        report(era, instances(sub))
    del A
    print("=" * 110, flush=True)
    print("DAEMON (2026-06-20 ..)", flush=True)
    report("daemon", instances(load("daemon")))
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
