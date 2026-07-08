"""Trade stats for the FROZEN PIVOT-ZZTRAIL strategy (base C + breakeven lock arm +0.40% -> +0.10%), on
$1000 per trade (fixed notional, no leverage). In-sample 1m tape. Run: python study/pivot_stats_1k.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; CAP = 1000.0


def load_1m():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='total_closed_1m'").fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf='1m' ORDER BY id")]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    return [by[b] for b in sorted(by)]


def zigzag(H, L, thr):
    n = len(H)
    piv = []; direction = 0; hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        h = H[i]; l = L[i]
        if direction >= 0:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi * (1 - thr):
                piv.append((hi_i, hi, True, i)); direction = -1; lo, lo_i = l, i; continue
        if direction <= 0:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo * (1 + thr):
                piv.append((lo_i, lo, False, i)); direction = 1; hi, hi_i = h, i
    return piv


def main():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    sw = zigzag(list(hi), list(lo), ZIGZAG_PCT / 100.0)
    lows = []; highs = []; ph = pl = None
    for pb, p, ih, cb in sw:
        if ih:
            lab = None if ph is None else ("HH" if p > ph else "LH"); ph = p
            if lab:
                highs.append((cb, pb, p, lab))
        else:
            lab = None if pl is None else ("HL" if p > pl else "LL"); pl = p
            if lab:
                lows.append((cb, pb, p, lab))
    lows.sort(); highs.sort()

    def last(arr, det, label):
        r = None
        for c, pb, p, lab in arr:
            if c > det:
                break
            if lab == label:
                r = p
        return r

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(det, j0, buy):
        entry = float(cl[j0])
        if buy:
            sl0 = last(lows, det, "LL"); sl0 = sl0 * (1 - SL_PAD) if sl0 else entry * (1 - SL)
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            arm_lvl = entry * (1 + ARM); lock_lvl = entry * (1 + LOCK)
        else:
            sl0 = last(highs, det, "HH"); sl0 = sl0 * (1 + SL_PAD) if sl0 else entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            arm_lvl = entry * (1 - ARM); lock_lvl = entry * (1 - LOCK)
        exitlvl = sl0; tp = 0; armed = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                l2 = trail[tp][1]; exitlvl = max(exitlvl, l2) if buy else min(exitlvl, l2); tp += 1
            e = exitlvl
            if armed:
                e = max(e, lock_lvl) if buy else min(e, lock_lvl)
            if (lo[j] <= e) if buy else (hi[j] >= e):
                return ((e - entry) if buy else (entry - e)) / entry * 100.0
            if (hi[j] >= arm_lvl) if buy else (lo[j] <= arm_lvl):
                armed = True
        return ((cl[-1] - entry) if buy else (entry - cl[-1])) / entry * 100.0

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []          # (entry_bar, net%)
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan" if p2d > P2D_VHI else ("green" if p2d > P2D_HI else "hollow")
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = None
        if e_held:
            if tier == "hollow":
                j0 = ent
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                j0 = e2
        if j0 is None:
            continue
        rows.append((j0, walk(det, j0, buy) - FEE))

    rows.sort()
    r = np.array([x[1] for x in rows]); nT = len(r)
    days = (et[rows[-1][0]] - et[rows[0][0]]) / 86400.0
    wins = r[r > 0]; los = r[r < 0]
    eq = np.cumsum(r); peak = np.maximum.accumulate(eq); ddp = eq - peak       # % drawdown of summed equity
    bal = CAP; hi_bal = CAP; maxdd_usd = 0.0
    for x in r:
        bal *= (1 + x / 100.0); hi_bal = max(hi_bal, bal); maxdd_usd = max(maxdd_usd, hi_bal - bal)
    pf = wins.sum() / abs(los.sum()) if len(los) else float("inf")
    d = CAP / 100.0                                                            # $ per 1% at $1000 notional/trade

    print("PIVOT-ZZTRAIL (frozen: base C + lock arm 0.40%%/0.10%%) — in-sample stats, $%.0f per trade\n" % CAP)
    print("  tape span         : %.1f days  (%d trades, %.1f/day)" % (days, nT, nT / days if days else 0))
    print("  win rate          : %.1f%%   (%d win / %d loss)" % (100.0 * len(wins) / nT, len(wins), len(los)))
    print("  net per trade      : %+.3f%%   -> $%+.2f" % (r.mean(), r.mean() * d))
    print("  avg WIN / avg LOSS : %+.3f%% / %+.3f%%   ($%+.2f / $%+.2f)"
          % (wins.mean(), los.mean(), wins.mean() * d, los.mean() * d))
    print("  best / worst trade : %+.3f%% / %+.3f%%   ($%+.2f / $%+.2f)"
          % (r.max(), r.min(), r.max() * d, r.min() * d))
    print("  profit factor      : %.2f   (gross win $%.2f / gross loss $%.2f)"
          % (pf, wins.sum() * d, abs(los.sum()) * d))
    print("  max drawdown       : %.2f%%   (~$%.2f peak-to-trough)" % (abs(ddp.min()), maxdd_usd))
    print("\n  RESULT on $%.0f over the ~%.0f-day test (NO leverage):" % (CAP, days))
    print("    fixed $%.0f/trade  : total %+.2f%%  ->  profit $%+.2f  (end $%.2f)"
          % (CAP, r.sum(), r.sum() * d, CAP + r.sum() * d))
    print("    compounded         : end balance $%.2f  (%+.2f%%)" % (bal, (bal / CAP - 1) * 100.0))

    LEV = 10                                                                   # 10x leverage on the $1000 MARGIN
    balL = CAP; hiL = CAP; ddL = 0.0; liq = False
    for x in r:
        balL *= (1 + LEV * x / 100.0)
        if balL <= 0:
            liq = True; balL = 0.0; break
        hiL = max(hiL, balL); ddL = max(ddL, (hiL - balL) / hiL * 100.0)
    worst_marg = LEV * r.min()
    print("\n  WITH %dx LEVERAGE ($%.0f margin -> $%.0f notional/trade):" % (LEV, CAP, CAP * LEV))
    print("    fixed              : total %+.2f%% on margin  ->  profit $%+.2f  (end $%.2f)"
          % (LEV * r.sum(), LEV * r.sum() * d, CAP + LEV * r.sum() * d))
    print("    compounded         : end balance $%.2f  (%+.2f%%)%s"
          % (balL, (balL / CAP - 1) * 100.0, "   <-- ACCOUNT LIQUIDATED" if liq else ""))
    print("    worst single trade : %+.2f%% of margin  (%s)"
          % (worst_marg, "survives" if worst_marg > -100 else "WOULD LIQUIDATE"))
    print("    max drawdown       : fixed %.1f%% (~$%.0f) | compounded %.1f%% of the account"
          % (LEV * abs(ddp.min()), LEV * maxdd_usd, ddL))
    print("    liquidation buffer : worst trade is %.1f pts from the -100%% wipeout line" % (100.0 + worst_marg))

    # scenario: $200k balance, 10% of balance as MARGIN per trade, 10x leverage -> notional = 10%*10 = 100% of
    # balance (effective 1x full-account exposure), compounded.
    BAL = 200000.0; MFRAC = 0.10; LEV2 = 10; EXPO = MFRAC * LEV2
    b2 = BAL; hi2 = BAL; dd2 = 0.0; dd2usd = 0.0
    for x in r:
        b2 *= (1 + EXPO * x / 100.0); hi2 = max(hi2, b2)
        dd2 = max(dd2, (hi2 - b2) / hi2 * 100.0); dd2usd = max(dd2usd, hi2 - b2)
    print("\n  SCENARIO: $%.0fk balance, 10%% margin/trade x %dx = %.0f%% notional exposure (effective %.0fx), compounded:"
          % (BAL / 1000, LEV2, EXPO * 100, EXPO))
    print("    margin/trade start : $%.0f   ->   notional/trade start : $%.0f" % (BAL * MFRAC, BAL * MFRAC * LEV2))
    print("    end balance        : $%.0f   (%+.2f%%,  profit $%+.0f)" % (b2, (b2 / BAL - 1) * 100.0, b2 - BAL))
    print("    max drawdown       : %.2f%%  (~$%.0f)" % (dd2, dd2usd))
    print("    worst single trade : %+.3f%% of account  (~$%+.0f)  — margin/trade easily covers it, no liquidation"
          % (r.min(), r.min() / 100.0 * BAL))


if __name__ == "__main__":
    main()
