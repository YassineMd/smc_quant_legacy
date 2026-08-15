"""HYPOTHESIS (user): an order-flow WALL that sits (fully/partially) INSIDE a same-side REWARD/EFF AREA is a
higher-quality S/R -- price RESISTS/REVERSES at it more than a wall with no such confluence.

WALL (absorption_level_detect): price P, band, side R(resistance)/S(support), radar_runs = re-entry VISIT windows.
REWARD/EFF AREA (reward_eff.switches): flip candle band [ylo,yhi], GREEN buy=support / RED sell=resistance, live until a
close breaks it. MATCH = the terminal's 'Match Reward/eff' rule: SAME side (S wall <-> buy zone, R wall <-> sell zone) AND
the wall band [P-band,P+band] OVERLAPS an ACTIVE (unmitigated) zone at the visit bar.

Per wall VISIT: reversal = SYMMETRIC first-passage from P (support: +D first / resistance: -D first) within K bars. Split
three groups: BASELINE (all visits), REAL-overlap, PLACEBO-overlap (zones shifted +-1..3%). A real confluence edge must
beat BOTH a plain wall (baseline) AND a random-band overlap (placebo), in BOTH years. CLI: [tf ...]"""
import os, sys, math, random, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from app import reward_eff, absorption_level_detect as AL

random.seed(12345)
K = 48; DS = (0.005, 0.010); NPLAC = 4; MAXLIFE = 500; STRONG = 40.0


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


def reward_zones(A):
    """(i, is_support_zone, zlo, zhi, mit, strength). buy zone -> is_support_zone True (matches an S wall)."""
    H = [b["high"] for b in A]; L = [b["low"] for b in A]; C = [b["close"] for b in A]; n = len(A)
    out = []
    for (i, side, strength) in reward_eff.switches(A):
        hi = H[i]; lo = L[i]
        if hi <= 0 or lo <= 0:
            continue
        mid = 0.5 * (hi + lo); half = max(0.5 * (hi - lo), mid * 0.00025)
        ylo = mid - half; yhi = mid + half; is_sup = side == "buy"; mit = n
        if is_sup:
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if C[kk] < ylo:
                    mit = kk; break
        else:
            for kk in range(i + 1, min(n, i + 1 + MAXLIFE)):
                if C[kk] > yhi:
                    mit = kk; break
        out.append((i, is_sup, ylo, yhi, mit, strength))
    return out


def wall_visits(A):
    """(k0, P, band, is_support, year) for every radar re-entry visit of every detected wall (chunked detect)."""
    n = len(A); vis = []; seen = set(); c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 8000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            P = float(w.get("price", 0.0) or 0.0); band = float(w.get("band", 0.0) or 0.0)
            side = w.get("side"); i0 = int(w.get("i0", 0)) + c0
            if P <= 0 or band <= 0:
                continue
            key = (side, round(P, 4), i0)
            if key in seen:
                continue
            seen.add(key)
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                k0 = int(r[0]) + c0
                if 0 <= k0 < n:
                    yr = datetime.fromtimestamp(float(A[k0].get("start_time", 0) or 0), tz=timezone.utc).year
                    vis.append((k0, P, band, side == "S", yr))
        if c1 >= n:
            break
        c0 += 7000                                            # 1000-bar overlap so boundary walls aren't lost
    return vis


def resolve(H, L, k0, P, D, is_support):
    up = P * (1.0 + D); dn = P * (1.0 - D); n = len(H)
    for t in range(k0, min(n, k0 + K + 1)):
        hu = H[t] >= up; hd = L[t] <= dn
        if hu and hd:
            return None
        if hu:
            return "rev" if is_support else "brk"
        if hd:
            return "brk" if is_support else "rev"
    return None


def overlaps(vis, zones, shift, strong_only):
    """Boolean per visit: overlaps a SAME-SIDE active (shifted) reward zone at its visit bar."""
    mark = [False] * len(vis)
    for want_sup in (True, False):
        idx = [(vis[j][0], j) for j in range(len(vis)) if vis[j][3] == want_sup]
        idx.sort(); k0s = [t[0] for t in idx]
        for (i, is_sup, zlo, zhi, mit, strength) in zones:
            if is_sup != want_sup or (strong_only and strength < STRONG):
                continue
            zl = zlo * (1.0 + shift); zh = zhi * (1.0 + shift)
            hi_bar = min(mit, i + MAXLIFE)
            lo_j = bisect.bisect_left(k0s, i); hi_j = bisect.bisect_right(k0s, hi_bar)
            for jj in range(lo_j, hi_j):
                j = idx[jj][1]
                if mark[j]:
                    continue
                P = vis[j][1]; band = vis[j][2]
                if (P - band) <= zh and (P + band) >= zl:
                    mark[j] = True
    return mark


def run(tf):
    A = load_tf(tf); n = len(A)
    H = [b["high"] for b in A]; L = [b["low"] for b in A]
    zones = reward_zones(A); vis = wall_visits(A)
    print("\n================  TF = %s  (bars=%d, reward-zones=%d, wall-visits=%d)  ================"
          % (tf, n, len(zones), len(vis)), flush=True)
    # precompute outcomes per D per visit
    outc = {D: [resolve(H, L, v[0], v[1], D, v[3]) for v in vis] for D in DS}
    yrs = [v[4] for v in vis]
    for strong_only in (False, True):
        real_mark = overlaps(vis, zones, 0.0, strong_only)
        plac_marks = []
        for _ in range(NPLAC):
            s = random.uniform(0.01, 0.03) * random.choice((-1, 1))
            plac_marks.append(overlaps(vis, zones, s, strong_only))
        novl = sum(real_mark)
        print("  ----- %s  (real-overlap visits=%d) -----"
              % ("STRONG zones only" if strong_only else "ALL zones", novl), flush=True)
        for D in DS:
            o = outc[D]
            for Y in (2025, 2026):
                base_rev = base_res = ro_rev = ro_res = po_rev = po_res = 0
                for j in range(len(vis)):
                    if yrs[j] != Y or o[j] is None:
                        continue
                    r = 1 if o[j] == "rev" else 0
                    base_rev += r; base_res += 1
                    if real_mark[j]:
                        ro_rev += r; ro_res += 1
                    for pm in plac_marks:
                        if pm[j]:
                            po_rev += r; po_res += 1
                if ro_res < 20:
                    print("    D=%.1f%% %d  real-overlap n=%d (<20, skip)" % (D * 100, Y, ro_res)); continue
                brate = base_rev / base_res; rrate = ro_rev / ro_res; prate = (po_rev / po_res) if po_res else 0.0
                pv = binom_p(ro_rev, ro_res, prate) if prate > 0 else 1.0
                e_pl = 100 * (rrate - prate); e_ba = 100 * (rrate - brate)
                flag = "  <== EDGE" if (e_pl >= 3 and e_ba >= 3 and pv < 0.05) else ""
                print("    D=%.1f%% %d  baseline=%.1f%%(n%d)  REAL-ovl=%.1f%%(n%d)  placebo-ovl=%.1f%%  "
                      "edge_vs_plac=%+.1f  edge_vs_base=%+.1f  p=%.3f%s"
                      % (D * 100, Y, 100 * brate, base_res, 100 * rrate, ro_res, 100 * prate,
                         e_pl, e_ba, pv, flag), flush=True)


if __name__ == "__main__":
    for tf in (sys.argv[1:] or ["15m", "1h"]):
        try:
            run(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %r" % (tf, e)); traceback.print_exc()
