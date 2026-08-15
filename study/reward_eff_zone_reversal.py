"""HYPOTHESIS (user): a signal that fires when price RETURNS TO a REWARD/EFF AREA has a directional edge -- price bounces
at a GREEN buy zone (support -> LONG) / rejects at a RED sell zone (resistance -> SHORT).

The reward/eff AREA = reward_eff.switches() flip candle's high-low band (same construction the terminal draws:
mid +- max(halfrange, mid*0.00025)); GREEN buy = support, RED sell = resistance; strength = depth of the regime the
flip reversed. Signal = price LEAVES the band by >=SEP then first RETURNS to it from the labeled side. Outcome = SYMMETRIC
first-passage from the band mid (mid*(1+-D)): reversal = the approach-side barrier first (support: up / resistance: down).

CONTROL = the [[swing-va-zone-reversal]] gold-standard PLACEBO: the SAME band shifted to a random nearby level (+-1..3%),
same side-role, same leave-then-return logic. EDGE = real - placebo reversal rate. Split BOTH recon years (both must
clear) + a STRONG-switch cut. CLI: python study/reward_eff_zone_reversal.py [tf ...]"""
import os, sys, math, random
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from app import reward_eff

random.seed(12345)
SEP = 0.0015          # price must clear the band by 0.15% to count as "left" (a genuine retrace, not sitting on it)
MAXSCAN = 250         # bars to wait for the return-touch
K = 48                # bars to resolve the barrier
NPLAC = 4             # placebo draws
DS = (0.005, 0.010)   # symmetric barrier half-widths
STRONG = 40.0         # "strong switch" strength cut


def load_tf(tf):
    _, rows, _ = load_archive(tf, root="study/recon_archive")
    A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0))
    for b in A:
        b["open"] = float(b.get("open", b.get("open_price", 0.0)) or 0.0)
        b["close"] = float(b.get("close", b.get("close_price", 0.0)) or 0.0)
        b["high"] = float(b.get("high", 0.0) or 0.0); b["low"] = float(b.get("low", 0.0) or 0.0)
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


def build_zones(A):
    """[(i, is_support, strength, ylo, yhi, year)] from reward_eff.switches() (causal sliding window)."""
    H = [b["high"] for b in A]; L = [b["low"] for b in A]
    ev = reward_eff.switches(A)
    zs = []
    for (i, side, strength) in ev:
        hi = H[i]; lo = L[i]
        if hi <= 0.0 or lo <= 0.0:
            continue
        mid = 0.5 * (hi + lo); half = max(0.5 * (hi - lo), mid * 0.00025)
        yr = datetime.fromtimestamp(float(A[i].get("start_time", 0) or 0), tz=timezone.utc).year
        zs.append((i, side == "buy", strength, mid - half, mid + half, yr))
    return zs, H, L


def first_return(H, L, i, ylo, yhi, is_support):
    """First bar t>i where price RETURNS to the band from the labeled side, after having LEFT it by >=SEP. None if it
    never leaves-then-returns within MAXSCAN."""
    n = len(H); left = False; end = min(n, i + 1 + MAXSCAN)
    for t in range(i + 1, end):
        if is_support:
            if not left:
                if L[t] > yhi * (1.0 + SEP):
                    left = True
            elif L[t] <= yhi:
                return t
        else:
            if not left:
                if H[t] < ylo * (1.0 - SEP):
                    left = True
            elif H[t] >= ylo:
                return t
    return None


def resolve(H, L, t0, mid, D, is_support):
    up = mid * (1.0 + D); dn = mid * (1.0 - D); n = len(H)
    for t in range(t0, min(n, t0 + K + 1)):
        hu = H[t] >= up; hd = L[t] <= dn
        if hu and hd:
            return None
        if hu:
            return "rev" if is_support else "brk"
        if hd:
            return "brk" if is_support else "rev"
    return None


def test(zs, H, L, shift, D, strong_only):
    """Per year: (rev, resolved, sup_rev, sup_tot, res_rev, res_tot)."""
    acc = {2025: [0, 0, 0, 0, 0, 0], 2026: [0, 0, 0, 0, 0, 0]}
    for (i, is_sup, strength, ylo, yhi, yr) in zs:
        if strong_only and strength < STRONG:
            continue
        if yr not in acc:
            continue
        zl = ylo * (1.0 + shift); zh = yhi * (1.0 + shift); mid = 0.5 * (zl + zh)
        t = first_return(H, L, i, zl, zh, is_sup)
        if t is None:
            continue
        v = resolve(H, L, t, mid, D, is_sup)
        if v is None:
            continue
        a = acc[yr]; a[1] += 1
        if v == "rev":
            a[0] += 1
            a[2 if is_sup else 4] += 1
        a[3 if is_sup else 5] += 1
    return acc


def run(tf):
    A = load_tf(tf)
    zs, H, L = build_zones(A)
    ny = {2025: sum(1 for z in zs if z[5] == 2025), 2026: sum(1 for z in zs if z[5] == 2026)}
    print("\n================  TF = %s  (bars=%d, switches=%d  [2025:%d 2026:%d])  ================"
          % (tf, len(A), len(zs), ny[2025], ny[2026]), flush=True)
    for strong_only in (False, True):
        print("  ----- %s -----" % ("STRONG switches only (strength>=%.0f)" % STRONG if strong_only else "ALL switches"),
              flush=True)
        for D in DS:
            real = test(zs, H, L, 0.0, D, strong_only)
            plac = {2025: [0, 0], 2026: [0, 0]}                 # (rev, resolved) accumulated over draws
            for _ in range(NPLAC):
                s = random.uniform(0.01, 0.03) * random.choice((-1, 1))
                pt = test(zs, H, L, s, D, strong_only)
                for Y in (2025, 2026):
                    plac[Y][0] += pt[Y][0]; plac[Y][1] += pt[Y][1]
            for Y in (2025, 2026):
                rev, res, sr, st, rr, rt = real[Y]
                if res < 20:
                    print("    D=%.1f%%  %d  n=%d (<20, skip)" % (D * 100, Y, res)); continue
                rrate = rev / res
                prate = (plac[Y][0] / plac[Y][1]) if plac[Y][1] else 0.0
                p = binom_p(rev, res, prate) if prate > 0 else 1.0
                edge = 100 * (rrate - prate)
                flag = "  <== EDGE" if (edge >= 3 and p < 0.05) else ""
                print("    D=%.1f%%  %d  n=%-4d real=%.1f%%  placebo=%.1f%%  edge=%+.1fpp  p=%.3f"
                      "   [sup %.0f%%/n%d  res %.0f%%/n%d]%s"
                      % (D * 100, Y, res, 100 * rrate, 100 * prate, edge, p,
                         100 * sr / max(1, st), st, 100 * rr / max(1, rt), rt, flag), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h", "5m"]):
        try:
            run(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
