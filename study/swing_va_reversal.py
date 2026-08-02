"""HYPOTHESIS: price REVERSES at a Price&CVD-Swings VA-zone area (the band spanning the swing's buy-POC/sell-POC/LVN).

For every confirmed ZigZag swing leg we build the VA-zone [zlo, zhi] = min..max of {buy_poc, sell_poc, lvn}
(swing_lvn_detect.va_lines), live CAUSALLY from the pivot's confirm bar (no look-ahead). When price later first
REACHES the band, a symmetric first-passage test asks: does it REVERSE (exit back to the approach side by D) or BREAK
(exit the far side by D) first, within K candles? Reversal rate at real zones is compared to PLACEBO zones (same
timing/width, shifted to a random nearby level) — the null for "an arbitrary level touched in the same market". If real
>> placebo, the zone is genuine S/R.

CLI: python study/swing_va_reversal.py [tf=1h] [D=0.005] [K=12] [nplacebo=3]
"""
import os, sys, math, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.candle_bias_1h import load as _load1h
from study.archive_loader import load_archive
from app import swing_lvn_detect as SL

random.seed(12345)


def load_tf(tf):
    if tf == "1h":
        return _load1h()
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0))
    for b in A:
        b["open"] = float(b.get("open_price", 0.0) or 0.0); b["close"] = float(b.get("close_price", 0.0) or 0.0)
    return A


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


def first_touch(H, L, C, t0, zlo, zhi, maxscan):
    """First bar t>t0 whose range enters [zlo,zhi], with a clear approach side (prev close outside). Returns
    (t, from_above) or None."""
    n = len(H)
    hi_lim = min(n, t0 + maxscan)
    for t in range(t0 + 1, hi_lim):
        if L[t] <= zhi and H[t] >= zlo:                 # range intersects the band
            pc = C[t - 1]
            if pc > zhi:
                return t, True                          # approached from ABOVE (support test)
            if pc < zlo:
                return t, False                         # approached from BELOW (resistance test)
            return None                                 # already inside at first touch -> ambiguous, drop
    return None


def resolve(H, L, t0, zlo, zhi, from_above, D, K):
    """SYMMETRIC first-passage from the band MID: barriers at mid*(1+-D), so the test is width-independent (D is the
    same distance up and down). Reversal = price reaches the APPROACH-side barrier first; break = the FAR side.
    Scans up to K bars. Returns 'rev' / 'brk' / None(unresolved|same-bar-both)."""
    mid = 0.5 * (zlo + zhi)
    up = mid * (1.0 + D); dn = mid * (1.0 - D)
    n = len(H)
    for t in range(t0, min(n, t0 + K + 1)):
        hit_up = H[t] >= up; hit_dn = L[t] <= dn
        if hit_up and hit_dn:
            return None
        if hit_up:
            return "rev" if from_above else "brk"       # up = approach side when approached from ABOVE (support)
        if hit_dn:
            return "brk" if from_above else "rev"
    return None


def zones(A):
    """Every confirmed swing leg's VA-zone: (zlo, zhi, live_from(confirm bar), ends_high, width_pct)."""
    r = SL._dev_leg(A)
    if not r:
        return []
    H, L, C, thr, piv, dev = r
    out = []
    for k in range(1, len(piv)):
        b0 = int(piv[k - 1][0]); b1 = int(piv[k][0]); cb = int(piv[k][3]); eh = bool(piv[k][2])
        if b1 <= b0:
            continue
        try:
            va = SL.va_lines(A, b0, b1)
        except Exception:
            va = None
        if not va:
            continue
        lv = [va[x] for x in ("buy_poc", "sell_poc", "lvn") if va.get(x) is not None]
        if len(lv) < 2:
            continue
        zlo, zhi = min(lv), max(lv)
        if zhi <= zlo:
            zhi = zlo * (1.0 + 1e-4)                      # give a hair of width to a coincident band
        mid = 0.5 * (zlo + zhi)
        out.append((zlo, zhi, cb, eh, (zhi - zlo) / mid * 100.0))
    return out, thr


def run(A, tf, D, K, nplac):
    H = [float(b.get("high", 0.0) or 0.0) for b in A]
    L = [float(b.get("low", 0.0) or 0.0) for b in A]
    C = [b["close"] for b in A]
    n = len(A)
    zs, thr = zones(A)
    MAXSCAN = 250
    widths = sorted(z[4] for z in zs)
    print("%s: %d candles | ZigZag thr=%.2f%% | swing zones=%d | median band width=%.3f%% | D=%.2f%% K=%d"
          % (tf, n, thr * 100, len(zs), widths[len(widths) // 2] if widths else 0.0, D * 100, K))

    def test(shift):
        rev = brk = un = touched = 0; sup_rev = sup_tot = res_rev = res_tot = 0
        for (zlo, zhi, cb, eh, _w) in zs:
            zl, zh = zlo * (1.0 + shift), zhi * (1.0 + shift)
            ft = first_touch(H, L, C, cb, zl, zh, MAXSCAN)
            if ft is None:
                continue
            t, fa = ft; touched += 1
            v = resolve(H, L, t, zl, zh, fa, D, K)
            if v is None:
                un += 1; continue
            if v == "rev":
                rev += 1
                if fa:
                    sup_rev += 1
                else:
                    res_rev += 1
            else:
                brk += 1
            if fa:
                sup_tot += 1
            else:
                res_tot += 1
        res = rev + brk
        return dict(touched=touched, resolved=res, rev=rev, brk=brk, un=un,
                    rate=(rev / res if res else 0.0),
                    sup=(sup_rev, sup_tot), resi=(res_rev, res_tot))

    real = test(0.0)
    # placebo: shift each zone to a random nearby non-special level (+/- 1%..3%), several draws averaged
    prates = []; prev = pres = 0
    for _ in range(nplac):
        s = random.uniform(0.01, 0.03) * random.choice((-1, 1))
        pr = test(s); prates.append(pr["rate"]); prev += pr["rev"]; pres += pr["resolved"]
    plac_rate = (prev / pres) if pres else 0.0
    p = binom_p(real["rev"], real["resolved"], plac_rate) if real["resolved"] else 1.0

    print("  REAL zones : touched=%d resolved=%d  reversal=%.1f%%  (rev %d / brk %d, unresolved %d)"
          % (real["touched"], real["resolved"], 100 * real["rate"], real["rev"], real["brk"], real["un"]))
    sr, st = real["sup"]; rr, rt = real["resi"]
    print("    support (approach from ABOVE): %.1f%% rev (n%d)   resistance (from BELOW): %.1f%% rev (n%d)"
          % (100 * sr / max(1, st), st, 100 * rr / max(1, rt), rt))
    print("  PLACEBO    : reversal=%.1f%%  (avg of %d random-offset draws, n~%d)  ->  real vs placebo p=%.4f"
          % (100 * plac_rate, nplac, pres // max(1, nplac), p))
    edge = 100 * (real["rate"] - plac_rate)
    print("  EDGE (real - placebo) = %+.1f pp   %s" % (edge, "<-- reversal confirmed" if (edge >= 3 and p < 0.05)
                                                       else "(not distinguishable from an arbitrary level)"))


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 else "1h"
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    nplac = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    A = load_tf(tf)
    for D in (0.005, 0.01, 0.015, 0.02):
        run(A, tf, D, K, nplac)
        print("")


if __name__ == "__main__":
    main()
