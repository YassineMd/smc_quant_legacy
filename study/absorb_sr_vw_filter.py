"""5m Absorption S/R strategy: does filtering entries by the ENTRY candle's vw% (>= T) improve the win rate?

vw% = (max(up_ticks, dn_ticks) / min - 1) * 100 on the entry bar (the terminal's Ease 'vw'). Win = TP hit before SL
(SL/TP straight from app.engulf5m_detect). Compares the baseline win rate to the vw>=T subset, to vw>=T ALIGNED (vw
direction matches the trade side), and shows the win rate binned by vw%. Also net expectancy in R (win=+RR, loss=-1).
Exact two-sided binomial p (subset win rate vs the vw<T complement). Runs on recon (18mo) or live daemon archive.

CLI: python study/absorb_sr_vw_filter.py [recon|live] [vw_thresh]
"""
import os, sys, math, datetime as dt
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import engulf5m_detect as E5
from app.engulf_sr_detect import _ohlc


def _lp(i, n, p):
    if p <= 0.0:
        return 0.0 if i == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if i == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
            + i * math.log(p) + (n - i) * math.log1p(-p))


def binom_p(k, n, p):
    if n == 0 or p <= 0.0 or p >= 1.0:
        return 1.0
    lpk = _lp(k, n, p); tot = 0.0
    for i in range(n + 1):
        if _lp(i, n, p) <= lpk + 1e-9:
            tot += math.exp(_lp(i, n, p))
    return min(1.0, tot)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "recon"
    T = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    root = os.path.join("study", "archive_data" if src == "live" else "recon_archive")
    _, rows, _ = load_archive("5m", root=root)
    A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
    O = [0.0] * n; C = [0.0] * n; H = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(A):
        O[i], C[i], H[i], Lo[i] = _ohlc(b)

    def vwbar(i):
        b = A[i]; ut = float(b.get("up_ticks", 0) or 0); dtk = float(b.get("dn_ticks", 0) or 0)
        if min(ut, dtk) <= 0:
            return None
        return (max(ut, dtk) / min(ut, dtk) - 1.0) * 100.0, (ut > dtk)

    def outcome(s):
        i = s["i"]; side = s["side"]; tp = s["tp"]; sl = s["sl"]
        for j in range(i + 1, n):
            if side > 0:
                hs = Lo[j] <= sl; ht = H[j] >= tp
            else:
                hs = H[j] >= sl; ht = Lo[j] <= tp
            if hs and ht:
                return 0
            if ht:
                return 1
            if hs:
                return 0
        return None

    sigs = E5.detect(A)
    sys.stdout.write("5m src=%s  buckets=%d  raw signals=%d\n" % (src, n, len(sigs))); sys.stdout.flush()
    recs = []
    for s in sigs:
        oc = outcome(s)
        if oc is None:
            continue
        vb = vwbar(s["i"])
        if vb is None:
            continue
        vw, up = vb
        aligned = (up and s["side"] > 0) or ((not up) and s["side"] < 0)
        rr = abs(s["tp"] - s["entry"]) / max(1e-9, abs(s["entry"] - s["sl"]))
        recs.append(dict(win=oc, vw=vw, aligned=aligned, rr=rr))
    if len(recs) < 30:
        print("  too few resolved signals with ticks (%d)" % len(recs)); return

    def stat(g, base=None):
        k = sum(r["win"] for r in g); m = len(g)
        wr = 100 * k / m if m else 0.0
        exp = sum((r["rr"] if r["win"] else -1.0) for r in g) / m if m else 0.0   # net R (gross of fees)
        p = ""
        if base is not None and m:
            p = "  p=%.3f" % binom_p(k, m, base)
        return "n=%-5d win=%.1f%%  netR=%+.3f%s" % (m, wr, exp, p)

    base_wr = sum(r["win"] for r in recs) / len(recs)
    print("\nBASELINE (all resolved entries): " + stat(recs))
    lo = [r for r in recs if r["vw"] < T]; base_lo = (sum(r["win"] for r in lo) / len(lo)) if lo else base_wr
    print("\n--- the hypothesis: vw%% >= %g ---" % T)
    print("  vw <  %-4g : %s" % (T, stat(lo)))
    print("  vw >= %-4g : %s" % (T, stat([r for r in recs if r["vw"] >= T], base_lo)))
    print("  vw >= %-4g & ALIGNED (vw dir == trade side): %s" % (T, stat([r for r in recs if r["vw"] >= T and r["aligned"]], base_lo)))
    print("  vw >= %-4g & NOT aligned              : %s" % (T, stat([r for r in recs if r["vw"] >= T and not r["aligned"]], base_lo)))
    print("\n--- win rate by vw%% band ---")
    for a, b in [(0, 3), (3, 6), (6, 10), (10, 15), (15, 25), (25, 50), (50, 1e9)]:
        g = [r for r in recs if a <= r["vw"] < b]
        print("  vw [%g,%s) : %s" % (a, "%g" % b if b < 1e8 else "inf", stat(g, base_wr)))
    print("\n--- aligned vs not (all vw) ---")
    print("  ALIGNED     : %s" % stat([r for r in recs if r["aligned"]], base_wr))
    print("  NOT aligned : %s" % stat([r for r in recs if not r["aligned"]], base_wr))


if __name__ == "__main__":
    main()
