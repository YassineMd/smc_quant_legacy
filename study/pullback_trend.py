"""STANDALONE — PULLBACK-IN-TREND test (trend-following). NOT Pivot V3. SEQUENTIAL (one trade at a time).

Idea (the thing that separated V3 Path A from every losing test): don't buy extension, buy the PULLBACK to a
structural level in the trend direction.
  TREND  = the LOCKED HM cycle (bull cycle -> longs only, bear cycle -> shorts only).
  ENTRY  = price pulls back to (touches within TOUCH% of) the last CONFIRMED swing low (long) / high (short),
           closes still on the right side of it, AND the LOCKED eff-agg spread is still in favour of the trend.
  STOP   = just below that swing low (long) / above the swing high (short)  [structural].
  EXIT   = ride to the trend reversal = the LOCKED HM cycle flips to the opposite side (or the stop).
Fee 0.10. Causal (locked values = data up to j-LOCK). Run: python study/pullback_trend.py
"""
import os, sys, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import region_state as R, config                # noqa: E402
from app.structure import ZIGZAG_PCT                      # noqa: E402
import pivot_v3_de_zone_pdf as B                          # 1m loader + zz  # noqa: E402

FEE = 0.10; BE = 0.05; LW = config.LIVE_PANEL_WINDOW; LOCK = LW // 2; MIN_CYC = 4
TOUCH = 0.0015          # how close price must come to the swing to count as a pull-back "touch"
STOP_PAD = 0.001        # stop this far beyond the swing


def build_cycles(sh, min_len):
    n = len(sh); cyc = []; i0 = 0; dom = sh[0] >= 0.5
    for k in range(1, n):
        dk = sh[k] >= 0.5
        if dk != dom:
            cyc.append([i0, k - 1, dom]); i0 = k; dom = dk
    cyc.append([i0, n - 1, dom])
    while len(cyc) > 1:
        si = min(range(len(cyc)), key=lambda i: cyc[i][1] - cyc[i][0])
        if (cyc[si][1] - cyc[si][0] + 1) >= min_len:
            break
        cyc[si][2] = not cyc[si][2]
        merged = [cyc[0]]
        for c in cyc[1:]:
            if c[2] == merged[-1][2]:
                merged[-1][1] = c[1]
            else:
                merged.append(c)
        cyc = merged
    return cyc


def stats(nets):
    a = np.asarray(nets, float); nn = len(a)
    if nn == 0:
        return dict(n=0, w=0, b=0, l=0, mean=0.0, tot=0.0, t=0.0)
    w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
    t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if (nn > 1 and a.std(ddof=1) > 1e-9) else 0.0
    return dict(n=nn, w=w, b=b, l=l, mean=float(a.mean()), tot=float(a.sum()), t=float(t))


def main():
    raws = B.load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    ebu, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh = np.asarray(R.rolling_share(ebu, er_, LW), float)                       # settled eff-agg share
    cl = np.array([b.close_price for b in bks]); hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])

    cyc = build_cycles(e_sh, MIN_CYC); cyc_dom = np.zeros(n, bool)
    for a, b, dom in cyc:
        cyc_dom[a:b + 1] = dom
    locked_side = np.array([cyc_dom[max(0, j - LOCK)] for j in range(n)])         # LOCKED HM cycle side at bar j

    # last CONFIRMED swing low / high price per bar (ZigZag; cb = confirm bar)
    sw = B.zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    lows = []; highs = []
    for pb, p, ih, cb in sw:
        (highs if ih else lows).append((cb, p))
    lows.sort(); highs.sort()
    last_low = np.full(n, np.nan); last_high = np.full(n, np.nan); li = hj = 0; c_lo = c_hi = np.nan
    for j in range(n):
        while li < len(lows) and lows[li][0] <= j:
            c_lo = lows[li][1]; li += 1
        while hj < len(highs) and highs[hj][0] <= j:
            c_hi = highs[hj][1]; hj += 1
        last_low[j] = c_lo; last_high[j] = c_hi

    et = np.array([b.end_time for b in bks])
    z_et, z_vlo, z_vhi, z_low, z_high = B.load_4h()                               # 4h volume-profile wick bands

    def zone4h(j):                                                                # last COMPLETED 4h bucket as-of bar j
        i4 = bisect.bisect_right(z_et, et[j]) - 1
        return None if i4 < 0 else (z_low[i4], z_vlo[i4], z_vhi[i4], z_high[i4])

    def eff_aligned(j, bull):
        spr = (2.0 * float(e_sh[j - LOCK]) - 1.0) * 100.0 if j - LOCK >= 0 else 0.0
        return spr if bull else -spr

    def walk_flip(eb, bull, sl):                                                  # exit = opposite cycle lock OR stop
        entry = float(cl[eb])
        for j in range(eb + 1, n):
            if (lo[j] <= sl) if bull else (hi[j] >= sl):
                return ((sl - entry) if bull else (entry - sl)) / entry * 100.0, "SL", j
            if bool(locked_side[j]) != bull:
                return ((cl[j] - entry) if bull else (entry - cl[j])) / entry * 100.0, "flip", j
        return ((cl[-1] - entry) if bull else (entry - cl[-1])) / entry * 100.0, "edge", n - 1

    def walk_trail(eb, bull, sl0):                                               # exit = TRAILING structural stop
        entry = float(cl[eb]); sl = sl0
        for j in range(eb + 1, n):
            cand = (last_low[j] * (1 - STOP_PAD)) if bull else (last_high[j] * (1 + STOP_PAD))
            if np.isfinite(cand) and ((cand > sl) if bull else (cand < sl)):     # ratchet under each new HL / above each LH
                sl = cand
            if (lo[j] <= sl) if bull else (hi[j] >= sl):
                return ((sl - entry) if bull else (entry - sl)) / entry * 100.0, "trailSL", j
        return ((cl[-1] - entry) if bull else (entry - cl[-1])) / entry * 100.0, "edge", n - 1

    def scan(entry_mode, walk, eff_min):
        nets = []; why = {}; holds = []; longs = []; shorts = []; n_touch = 0
        j = LOCK + 2
        while j < n - 1:
            bull = bool(locked_side[j]); entered = False
            if eff_aligned(j, bull) > eff_min:
                touch = False; sl0 = 0.0
                if entry_mode == "swing":                                        # pull-back to the ZigZag swing
                    sw_ = last_low[j] if bull else last_high[j]
                    if np.isfinite(sw_):
                        touch = (lo[j] <= sw_ * (1 + TOUCH) and cl[j] > sw_) if bull else (hi[j] >= sw_ * (1 - TOUCH) and cl[j] < sw_)
                        sl0 = sw_ * (1 - STOP_PAD) if bull else sw_ * (1 + STOP_PAD)
                else:                                                            # pull-back into the 4h volume wick
                    z = zone4h(j)
                    if z is not None:
                        bl, vlo, vhi, bh = z
                        touch = (lo[j] <= vlo and cl[j] > bl) if bull else (hi[j] >= vhi and cl[j] < bh)
                        sl0 = bl * (1 - STOP_PAD) if bull else bh * (1 + STOP_PAD)
                if touch:
                    n_touch += 1
                    if (sl0 < cl[j]) if bull else (sl0 > cl[j]):
                        g, r, xb = walk(j, bull, sl0)
                        net = g - FEE
                        nets.append(net); why[r] = why.get(r, 0) + 1; holds.append(xb - j)
                        (longs if bull else shorts).append(net)
                        j = xb + 1; entered = True
            if not entered:
                j += 1
        return dict(o=stats(nets), why=why, holds=holds, longs=longs, shorts=shorts, touch=n_touch)

    def report(tag, res):
        o = res["o"]
        print(tag)
        if o["n"] == 0:
            print("  no trades.\n"); return
        print("  touches %d -> taken %d | W %d/BE %d/L %d (%.1f%% win) | net %+.4f%%/tr | GROSS %+.4f%% | sum %+.1f%% | t=%+.2f | hold %.0f"
              % (res["touch"], o["n"], o["w"], o["b"], o["l"], 100 * o["w"] / o["n"], o["mean"], o["mean"] + FEE,
                 o["tot"], o["t"], np.mean(res["holds"])))
        print("  exit: %s  |  long net %+.4f%% (n%d,t%+.2f) short net %+.4f%% (n%d,t%+.2f)\n"
              % ("  ".join("%s=%.0f%%" % (k, 100 * v / o["n"]) for k, v in sorted(res["why"].items(), key=lambda kv: -kv[1])),
                 stats(res["longs"])["mean"], len(res["longs"]), stats(res["longs"])["t"],
                 stats(res["shorts"])["mean"], len(res["shorts"]), stats(res["shorts"])["t"]))

    print("PULLBACK-IN-TREND variants (trend=LOCKED HM cycle; SEQUENTIAL; fee 0.10). tape n=%d bars.\n" % n)
    report("[baseline] swing pull-back + cycle-flip exit + eff>0", scan("swing", walk_flip, 0.0))
    report("[A] swing pull-back + TRAILING-stop exit + eff>0", scan("swing", walk_trail, 0.0))
    report("[B] 4h-WICK pull-back + TRAILING-stop exit + eff>=40 (selective)", scan("wick", walk_trail, 40.0))
    report("[C] 4h-WICK pull-back + cycle-FLIP exit + eff>=40 (selective + better exit)", scan("wick", walk_flip, 40.0))
    print("eff-threshold sweep (4h-WICK + cycle-flip exit) — does more selectivity clear the fee?")
    for em in (0, 20, 40, 55, 70):
        r = scan("wick", walk_flip, float(em)); o = r["o"]; ol = stats(r["longs"])
        print("  eff>=%-3d | n=%-3d net %+.4f%% GROSS %+.4f%% t%+.2f  ||  LONG n=%-3d net %+.4f%% GROSS %+.4f%% t%+.2f"
              % (em, o["n"], o["mean"], o["mean"] + FEE, o["t"], ol["n"], ol["mean"], ol["mean"] + FEE, ol["t"]))


if __name__ == "__main__":
    main()
