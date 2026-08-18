"""Is the '15m winner/loser' story just R:R geometry (candle-SL distance), and does absorpR add anything beyond it?

15m clock RR, prop exit (0.2% TP + candle-capped SL). Per trade: SL-distance (known at entry), absorpR, net return.
At a FIXED TP a tighter SL is smaller loss but also a lower win% (easier to get wicked out) -- so 'tight SL = better'
is NOT tautological; we test whether net EXPECTANCY actually rises, out-of-sample.

Gates:
  A. SL-distance quintiles -> win% AND net-return, per year (shows the tradeoff, not just the return).
  B. OOS: 'tight' = SL-dist <= 2025 median; test 2026 net vs baseline + bootstrap 95% CI. + permutation on the
     Q1(tight)-Q5(wide) net spread.
  C. Does absorpR add BEYOND SL-distance? absorpR top-bottom-tercile net spread WITHIN each SL-distance tercile.
Usage: python study/radarrun_15m_sldist_test.py [tf]
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.radarrun_filter_5m_clock import detect_events
from app import absorption as ABS

TP = 0.002; SLBUF = 0.003; FEE = 0.0004; SLIP = 0.0003; H = 200


def build(tf):
    A = sorted(load_archive(tf, root="study/clock_archive", drop_degenerate=False)[1],
               key=lambda b: float(b.get("start_time", 0) or 0))
    n = len(A)
    O = np.array([float(b.get("open", 0)) for b in A]); C = np.array([float(b.get("close", 0)) for b in A])
    Hi = np.array([float(b.get("high", 0)) for b in A]); Lo = np.array([float(b.get("low", 0)) for b in A])
    yr = np.array([datetime.fromtimestamp(float(b.get("start_time", 0)), tz=timezone.utc).year for b in A])
    ev = detect_events(A, O, C, Hi, Lo)
    sld, aR, net, win, years = [], [], [], [], []
    for (k, side) in sorted(ev):
        if k + 1 >= n:
            continue
        rlo, rhi, band = ev[(k, side)]; s = 1 if side == "S" else -1; entry = C[k]
        sl = max(Lo[k] * (1 - SLBUF), rlo) if s > 0 else min(Hi[k] * (1 + SLBUF), rhi)
        dist = abs(entry - sl) / entry
        if dist <= 0:
            continue
        tp = entry * (1 + s * TP); j0 = k + 1; j1 = min(n, k + 1 + H)
        r = w = None
        for off in range(j1 - j0):
            hi = Hi[j0 + off]; lo = Lo[j0 + off]
            if (lo <= sl) if s > 0 else (hi >= sl):
                r = s * (sl - entry) / entry - FEE - SLIP; w = 0; break
            if (hi >= tp) if s > 0 else (lo <= tp):
                r = s * (tp - entry) / entry - FEE; w = 1; break
        if r is None:
            continue
        try:
            a = ABS.absorption(A, k)[0]
        except Exception:
            a = None
        sld.append(dist); aR.append(a if a is not None else np.nan); net.append(r); win.append(w); years.append(int(yr[k]))
    return (np.array(sld), np.array(aR, dtype=np.float64), np.array(net), np.array(win), np.array(years))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sld, aR, net, win, yr = build(tf)
    print("%s clock RR prop(0.2%%TP): %d trades (2025=%d, 2026=%d). baseline net 2025 %+.4f%% / 2026 %+.4f%%\n"
          % (tf, len(net), (yr == 2025).sum(), (yr == 2026).sum(), 100 * net[yr == 2025].mean(), 100 * net[yr == 2026].mean()),
          flush=True)

    print("A. SL-DISTANCE quintiles (Q1=tightest .. Q5=widest) -> win%% / avg net-return%%, per year:")
    for Y in (2025, 2026):
        m = yr == Y; x = sld[m]; wv = win[m]; rv = net[m]
        q = np.quantile(x, [0.2, 0.4, 0.6, 0.8]); bins = np.digitize(x, q)
        ws = "  ".join("%2.0f%%" % (100 * wv[bins == b].mean()) if (bins == b).any() else " -- " for b in range(5))
        rs = "  ".join("%+.3f" % (100 * rv[bins == b].mean()) if (bins == b).any() else " -- " for b in range(5))
        print("   %d  win: %s   net: %s" % (Y, ws, rs), flush=True)

    def q1q5(x, r):
        q = np.quantile(x, [0.2, 0.8]); return (r[x <= q[0]].mean() - r[x >= q[1]].mean()) * 100.0   # tight - wide
    obs = q1q5(sld, net)
    rng = np.random.default_rng(7); ge = sum(q1q5(rng.permutation(sld), net) >= obs for _ in range(3000))
    print("\nB. tight-vs-wide (Q1-Q5) net spread = %+.4f%%   permutation p = %.3f" % (obs, ge / 3000), flush=True)
    thr = np.median(sld[yr == 2025]); te = yr == 2026
    tight = net[te & (sld <= thr)]; base = net[te]
    d = []
    for _ in range(3000):
        d.append(rng.choice(tight, len(tight)).mean() - rng.choice(base, len(base)).mean())
    lo, hi = np.percentile(d, [2.5, 97.5])
    print("   OOS(2026): tight-set (SL<=2025 median) %+.4f%% (n=%d) vs baseline %+.4f%%  |  lift %+.4f%% 95%%CI[%+.4f,%+.4f] cross0? %s"
          % (100 * tight.mean(), len(tight), 100 * base.mean(), 100 * (tight.mean() - base.mean()),
             100 * lo, 100 * hi, "YES" if lo < 0 < hi else "no"), flush=True)

    print("\nC. does absorpR add BEYOND SL-distance? absorpR top-bottom-tercile net spread WITHIN each SL-dist tercile:")
    fin = np.isfinite(aR)
    print("   corr(absorpR, SL-distance) = %+.3f" % np.corrcoef(aR[fin], sld[fin])[0, 1], flush=True)
    sq = np.quantile(sld[fin], [1 / 3, 2 / 3]); sb = np.digitize(sld, sq)
    for band in range(3):
        for Y in (2025, 2026):
            m = (sb == band) & (yr == Y) & fin
            if m.sum() < 60:
                print("   SLband %d %d: n<60" % (band, Y)); continue
            x = aR[m]; rv = net[m]; q = np.quantile(x, [1 / 3, 2 / 3])
            spread = (rv[x >= q[1]].mean() - rv[x <= q[0]].mean()) * 100.0
            print("   SLband %d %d (n=%d): absorpR topT-botT net spread %+.4f%%" % (band, Y, m.sum(), spread), flush=True)


if __name__ == "__main__":
    main()
