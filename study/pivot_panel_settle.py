"""Analyse the PANELS through the settle. Panels (signed, aligned to the trade): P1=absorption (1-2a)*100,
P2=eff-agg (2e-1)*100, P3=E/R (2r-1)*100, P4=exhaustion (trailing -> CAUSAL, no repaint), P0=SUM (sum0), and
P9=composite lean. P1/P2/P3 use the CENTERED rolling-share (repaint over 7 buckets); P4 is trailing (causal).
For every confirmed settled trade, take each panel's value at the entry bar j0 in SETTLED and CAUSAL (unlocked,
left-clamped) form, split by outcome (winner/breakeven/loser of the look-ahead entry), and report: mean aligned
CAUSAL value per outcome, corr(causal panel, trade PnL), and the repaint (settled-causal). Point: does any panel
predict the outcome CAUSALLY (P4 is the prime suspect since it doesn't repaint)? Run: python study/pivot_panel_settle.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05; LW = config.LIVE_PANEL_WINDOW; LK = LW // 2


def causal_share(bull, bear, window):
    h = max(1, window) // 2
    b = np.asarray(bull, float); r = np.asarray(bear, float)
    B = np.concatenate([[0.0], np.cumsum(b)]); Rr = np.concatenate([[0.0], np.cumsum(r)])
    out = np.empty(len(b))
    for i in range(len(b)):
        lo = max(0, i - h); sb = B[i + 1] - B[lo]; sr = Rr[i + 1] - Rr[lo]; tot = sb + sr
        out[i] = sb / tot if tot > 0 else 0.5
    return out


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


def zz(H, L, thr):
    n = len(H); piv = []; direction = 0; hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
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
    n = len(bks)
    _, e_sh_ref, _, sum0_ref = PD._p9_global(snaps)
    # rebuild the panel inputs (mirror _p9_global)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    rb = [s.get("buyer_er", 0.0) for s in snaps]; rs_ = [s.get("seller_er", 0.0) for s in snaps]
    ex = R.trailing_exhaustion(snaps, 0, n - 1, LW, config.EXH_MEASURE, config.EXH_SEL_MIN_WINDOW)
    s4 = np.empty(n); hold = 0.0
    for k, (b4, s4_) in enumerate(ex):
        inst = (s4_ - b4) * 100.0
        if abs(inst) > 1e-9:
            hold = inst
        s4[k] = hold
    a_s = np.array(R.rolling_share(ab, ar, LW)); e_s = np.array(R.rolling_share(eb, er_, LW)); r_s = np.array(R.rolling_share(rb, rs_, LW))
    a_c = causal_share(ab, ar, LW); e_c = causal_share(eb, er_, LW); r_c = causal_share(rb, rs_, LW)

    def panels(a, e, r):                                          # signed panel series from shares (P4/exh causal both)
        P1 = (1 - 2 * a) * 100.0; P2 = (2 * e - 1) * 100.0; P3 = (2 * r - 1) * 100.0; P4 = s4
        lean = P1 + P2 + P3; bull = (lean + s4) / 4.0; bear = (lean - s4) / 4.0
        idx0 = np.maximum(np.arange(n) - LK, 0)
        P0 = (bull + bull[idx0]) / 2.0 + (bear + bear[idx0]) / 2.0
        P9 = (P1 + P2 + P3 + P4) / 4.0
        return dict(P0=P0, P1=P1, P2=P2, P3=P3, P4=P4, P9=P9)
    PS = panels(a_s, e_s, r_s); PC = panels(a_c, e_c, r_c)        # settled / causal
    print("sanity: rebuilt settled P0 vs _p9_global sum0 max|diff|=%.2e" % np.max(np.abs(PS["P0"] - np.array(sum0_ref))))

    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    sw = zz(list(hi), list(lo), ZIGZAG_PCT / 100.0)
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
        return (1.0 if buy else -1.0) * (2.0 * float(e_s[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

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
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        j0 = None
        if e_held:
            if tier == "hollow":
                j0 = ent
        else:
            te = float(et[ent])
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    j0 = j; break
        if j0 is None:
            continue
        sg = 1.0 if buy else -1.0
        rec = dict(g=walk(det, j0, buy) - FEE)
        for p in ("P0", "P1", "P2", "P3", "P4", "P9"):
            rec[p + "c"] = sg * PC[p][j0]; rec[p + "s"] = sg * PS[p][j0]
        rows.append(rec)

    G = np.array([r["g"] for r in rows]); win = G > BE; los = G < -BE; beq = np.abs(G) <= BE
    print("\nPANELS at print (aligned to trade), CAUSAL value split by outcome + corr with PnL  (n=%d):\n" % len(rows))
    print("  panel | winners | breakev | losers  | corr(causal,PnL) | mean repaint |settled-causal|")
    for p in ("P0", "P1", "P2", "P3", "P4", "P9"):
        c = np.array([r[p + "c"] for r in rows]); sv = np.array([r[p + "s"] for r in rows])
        cw = c[win].mean() if win.any() else float("nan"); cb = c[beq].mean() if beq.any() else float("nan")
        clo = c[los].mean() if los.any() else float("nan")
        corr = np.corrcoef(c, G)[0, 1]; rep = np.mean(np.abs(sv - c))
        note = ""
        if p == "P4":
            note = "  <- CAUSAL (trailing, no repaint)"
        print("   %-4s | %+7.2f | %+7.2f | %+7.2f | %+15.2f | %12.2f |%s" % (p, cw, cb, clo, corr, rep, note))
    print("\n  (winners vs losers gap in the CAUSAL column = causal predictive power; corr sign should favour longs>0.)")


if __name__ == "__main__":
    main()
