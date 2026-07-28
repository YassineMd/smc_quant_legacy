"""FULLER delta-accel family (da3, terminal_burst) reconstructed per 1h bar from 1m constituents.
Does the finer last-third burst predict NEXT-bar continuation where the 50% da2 did not?

da3 = (dT3-dT1)/vol ; tb = (dT3-(dT1+dT2)/2)/vol  (vol-weighted tercile split, exactly like mm_skew_subbucket._accel)
Directionalize by candle sign s: da3d=da3*s, tbd=tb*s  (>0 = aggression accelerating INTO the move toward close).
Disjoint deciles -> P(next continues) + net ret; both sides + combined; 2025/26; exact-binomial two-sided p.
"""
from __future__ import annotations
import os, sys, math, bisect, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from study.archive_loader import load_archive

RECON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "study", "recon_archive")
FEE = 0.0008
MIN_SUB = 12


def binom_p(k, n, p):
    if n <= 0:
        return float("nan")
    k = int(round(k))
    if n <= 500:
        pv = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    else:
        mu = n * p; sd = math.sqrt(n * p * (1 - p)); pv = 0.5 * math.erfc((k - 0.5 - mu) / (sd * math.sqrt(2.0)))
    return min(pv, 1 - pv) * 2


def _accel(subs):
    vols = [float(x.get("curr_vol", 0)) for x in subs]
    dels = [float(x.get("buy_vol", 0)) - float(x.get("sell_vol", 0)) for x in subs]
    tot = sum(vols)
    if tot <= 0 or len(subs) < MIN_SUB:
        return None

    def split(fracs):
        bounds = [f * tot for f in fracs]; groups = [0.0] * (len(fracs) + 1); cum = 0.0
        for v, d in zip(vols, dels):
            mid = cum + v / 2; g = sum(1 for b in bounds if mid > b); groups[g] += d; cum += v
        return groups
    dH1, dH2 = split([0.5]); dT1, dT2, dT3 = split([1 / 3, 2 / 3])
    return (dH2 - dH1) / tot, (dT3 - dT1) / tot, (dT3 - (dT1 + dT2) / 2) / tot, dH1 + dH2


def build():
    _, rows, _ = load_archive("1h", root=RECON)
    _, r1, _ = load_archive("1m", root=RECON)
    sub = sorted(r1, key=lambda r: float(r.get("start_time", 0)))
    sub_start = [float(r.get("start_time", 0)) for r in sub]

    def constituents(s0, s1):
        j = bisect.bisect_left(sub_start, s0); out = []
        while j < len(sub) and sub_start[j] <= s1:
            out.append(sub[j]); j += 1
        return out

    recs = []; recon = []
    for i in range(len(rows) - 1):
        b = rows[i]; o = float(b.get("open_price", 0) or 0); c = float(b.get("close_price", 0) or 0)
        bv = float(b.get("buy_vol", 0) or 0); sv = float(b.get("sell_vol", 0) or 0)
        vol = bv + sv
        on = float(rows[i + 1].get("open_price", 0) or 0); cn = float(rows[i + 1].get("close_price", 0) or 0)
        if o <= 0 or c <= 0 or cn <= 0 or on <= 0 or c == o or vol <= 0:
            continue
        ac = _accel(constituents(float(b["start_time"]), float(b["end_time"])))
        if ac is None:
            continue
        a2, a3, tb, rd = ac
        recon.append((rd, bv - sv))
        s = 1 if c > o else -1; delta = bv - sv
        da1 = delta / vol
        recs.append(dict(s=s, da1=da1, da1d=da1 * s, da2d=(a2) * s, da3=a3, da3d=a3 * s, tb=tb, tbd=tb * s,
                         ndir=1 if cn > on else (-1 if cn < on else 0), nup=int(cn > on),
                         cont=int((1 if cn > on else (-1 if cn < on else 0)) == s),
                         ret_c=(cn - c) / c * s, ret_L=(cn - c) / c,
                         yr=dt.datetime.utcfromtimestamp(float(b["start_time"])).year))
    return recs, np.array(recon)


def _a(recs, k):
    return np.array([r[k] for r in recs], float)


def deciles(recs, vkey, hkey, rkey, base, title, hlab):
    if not recs:
        print("  (no rows)"); return
    arr = [recs[i] for i in np.argsort(_a(recs, vkey), kind="mergesort")]
    print("\n%s   baseline %s=%.1f%%  (n=%d)" % (title, hlab, base * 100, len(recs)))
    print("  %-4s %15s %6s %8s %9s %9s   %-9s %-9s %6s" % ("dec", "band", "n", hlab, "ret%", "net%", "2025", "2026", "p"))
    best = None
    for j, ch in enumerate(np.array_split(arr, 10)):
        if len(ch) == 0:
            continue
        vv = _a(ch, vkey); hh = _a(ch, hkey); rr = _a(ch, rkey)
        c25 = [r for r in ch if r["yr"] == 2025]; c26 = [r for r in ch if r["yr"] == 2026]
        p = binom_p(hh.sum(), len(ch), base)
        net = rr.mean() * 100 - FEE * 100
        print("  %-4d %6.3f-%6.3f %6d %7.1f%% %+8.4f%% %+8.4f%%   %-9s %-9s %6.3f" %
              (j + 1, vv.min(), vv.max(), len(ch), hh.mean() * 100, rr.mean() * 100, net,
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c25]) * 100, len(c25))) if c25 else "-",
               ("%.0f%%n%d" % (np.mean([r[hkey] for r in c26]) * 100, len(c26))) if c26 else "-", p))
        if best is None or p < best[0]:
            best = (p, j + 1, len(ch), hh.mean() * 100, net)
    print("  -> strongest: dec%d n=%d hit%.1f%% net%+.4f%% p=%.4f" % (best[1], best[2], best[3], best[4], best[0]))
    return best


def main():
    recs, recon = build()
    cc = np.corrcoef(recon[:, 0], recon[:, 1])[0, 1]
    bull = [r for r in recs if r["s"] == 1]; bear = [r for r in recs if r["s"] == -1]
    b_up = np.mean([r["nup"] for r in recs]); b_all = np.mean([r["cont"] for r in recs])
    b_cb = np.mean([r["cont"] for r in bull]); b_cs = np.mean([r["cont"] for r in bear])
    yrs = {}
    for r in recs:
        yrs[r["yr"]] = yrs.get(r["yr"], 0) + 1
    print("=" * 110)
    print("FULLER delta-accel (da3, terminal_burst) -> next-bar | 1h recon w/ 1m coverage, n=%d bars" % len(recs))
    print("  reconstruction corr(sum-sub-delta, bucket-delta)=%.4f  years=%s" % (cc, yrs))
    print("  P(nextUP)=%.1f%% | all-cont %.1f%% | bull-cont %.1f%% | bear-cont %.1f%% | fee %.2f%%" %
          (b_up * 100, b_all * 100, b_cb * 100, b_cs * 100, FEE * 100))
    print("=" * 110)

    r = {}
    r["da3_all"] = deciles(recs, "da3d", "cont", "ret_c", b_all, "[1] da3_dir (last-3rd vs first-3rd, INTO move) -> P(continue), ALL", "cont%")
    r["da3_bull"] = deciles(bull, "da3d", "cont", "ret_c", b_cb, "[1b] BULL: da3_dir -> P(continue up)", "cont%")
    r["da3_bear"] = deciles(bear, "da3d", "cont", "ret_c", b_cs, "[1c] BEAR: da3_dir -> P(continue down)", "cont%")
    r["da3_raw"] = deciles(recs, "da3", "nup", "ret_L", b_up, "[2] da3 raw (signed) -> P(next UP), LONG-next ret", "nextUP%")
    r["tb_all"] = deciles(recs, "tbd", "cont", "ret_c", b_all, "[3] terminal_burst_dir (final 3rd vs earlier baseline) -> P(continue), ALL", "cont%")
    r["tb_bull"] = deciles(bull, "tbd", "cont", "ret_c", b_cb, "[3b] BULL: tb_dir -> P(continue up)", "cont%")
    r["tb_bear"] = deciles(bear, "tbd", "cont", "ret_c", b_cs, "[3c] BEAR: tb_dir -> P(continue down)", "cont%")
    r["tb_raw"] = deciles(recs, "tb", "nup", "ret_L", b_up, "[4] terminal_burst raw (signed) -> P(next UP)", "nextUP%")

    # extremes: top/bottom decile as disjoint bands with net return sign for tradeability
    print("\n" + "=" * 110)
    print("SUMMARY: strongest cell per framing (p is exact-binomial two-sided, NOT multiplicity-corrected)")
    ncells = 0
    for k, v in r.items():
        if v:
            ncells += 10
            print("  %-10s dec%d n=%d hit%.1f%% net%+.4f%% p=%.4f" % (k, v[1], v[2], v[3], v[4], v[0]))
    print("  ~%d cells tested across framings; Bonferroni-ish alpha for p<0.05 ~ %.4f" % (ncells, 0.05 / ncells))


if __name__ == "__main__":
    main()
