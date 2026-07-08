"""Hypothesis: for FILLED Ds (cyan/orange OR red/green) that print IN THEIR OWN zone (buy D in Inzone-buy /
sell D in Inzone-sell), entering AT D (detection-bar close) beats waiting for E2. Motivation: a filled D badge
literally encodes 'D beats E' (aligned panel-2 spread at D is high), so at a structural wick the pullback-to-E2
may only give worse price / miss the move.

Test (same frozen exit for all): for every filled-D-in-own-zone SETUP (after the sequential per-side scan),
compute the outcome entering at D vs at E2 (frozen flip-rescue bar) vs at E (raw entry bar).
  - PAIRED book = the setups the frozen strategy actually trades at E2 (E flipped & E2 found): D vs E2, head-to-head.
  - FULL D book = D-entry on ALL filled-in-zone setups (incl. the E-held ones the frozen rule currently DROPS).
Three-outcome on NET (winner>+0.05%% / breakeven |.|<=0.05%% / loser<-0.05%%). Run: python study/pivot_d_vs_e2_inzone.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05


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


def load_4h():
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        for (x,) in con.execute("SELECT data FROM closed_buckets WHERE tf='4h' ORDER BY id"):
            b = json.loads(x)
            if b.get("levels"):
                by[float(b["end_time"])] = b
        con.close()
    b4 = [by[k] for k in sorted(by)]
    et = [float(b["end_time"]) for b in b4]; vlo = []; vhi = []; lw = []; hg = []
    for b in b4:
        q = bar_quantiles.vq(b["levels"]); vlo.append(float(q[0])); vhi.append(float(q[2]))
        lw.append(float(b["low"])); hg.append(float(b["high"]))
    return et, vlo, vhi, lw, hg


def zone5(px, low, vlo, vhi, high):
    if px < low:
        return "beyond-down"
    if px <= vlo:
        return "inzone-buy"
    if px < vhi:
        return "body"
    if px <= high:
        return "inzone-sell"
    return "beyond-up"


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
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()
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

    def find_e2(ent, buy):
        te = float(et[ent])
        for j in range(ent + 1, n):
            if st[j] > te + WIN:
                break
            if spr(j, buy) >= E2_MIN:
                return j
        return None

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; setups = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"; p2d = spr(det, buy)
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        if tier == "hollow":
            continue                                              # hypothesis is filled Ds only
        i4 = bisect.bisect_right(z_et, et[det]) - 1
        if i4 < 0:
            continue
        z = zone5(float(cl[det]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])
        own = (z == "inzone-buy") if buy else (z == "inzone-sell")
        if not own:
            continue                                              # only D printing IN ITS OWN zone
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        e2 = None if e_held else find_e2(ent, buy)                # frozen E2 = flip-rescue (only when E flipped)
        gD = walk(det, det, buy)                                  # enter AT D
        gE = walk(det, ent, buy)                                  # enter at E (raw entry bar)
        gE2 = walk(det, e2, buy) if e2 is not None else None      # frozen entry (E2), when it exists
        setups.append(dict(buy=buy, tier=tier, e_held=e_held, gD=gD, gE=gE, gE2=gE2, frozen=(e2 is not None)))

    def three(arr):
        a = np.array(arr) - FEE
        if not len(a):
            return "n=0"
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum()); nn = len(a)
        return ("n=%-2d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f)"
                % (nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0))

    tot = len(setups); frozen = [x for x in setups if x["frozen"]]; held = [x for x in setups if not x["frozen"]]
    print("D-vs-E2 for FILLED-D printing IN ITS OWN ZONE  (buy D in Inzone-buy / sell D in Inzone-sell)\n")
    print("  filled-in-own-zone setups: %d total" % tot)
    print("    - frozen-tradeable (E flipped & E2 found -> taken at E2 today): %d" % len(frozen))
    print("    - E-held (no E2 -> the frozen strategy DROPS these today):      %d\n" % len(held))

    print("  (1) PAIRED head-to-head on the %d frozen E2 setups (same setups, different entry bar):" % len(frozen))
    print("      enter at D  :", three([x["gD"] for x in frozen]))
    print("      enter at E2 :", three([x["gE2"] for x in frozen]))
    print("      enter at E  :", three([x["gE"] for x in frozen]))
    if frozen:
        dD = np.array([x["gD"] for x in frozen]); dE2 = np.array([x["gE2"] for x in frozen])
        wins = int((dD > dE2).sum()); ties = int((dD == dE2).sum()); loss = int((dD < dE2).sum())
        print("      D beats E2 on %d/%d setups (tie %d, worse %d) | mean per-trade delta D-E2 %+.3f%%"
              % (wins, len(frozen), ties, loss, (dD - dE2).mean()))

    print("\n  (2) FULL D-entry book on ALL %d filled-in-own-zone setups (D-entry needs no E2):" % tot)
    print("      enter at D  :", three([x["gD"] for x in setups]))
    print("      (of which the %d E-held ones the frozen rule drops):" % len(held))
    if held:
        print("      D-entry on the dropped set:", three([x["gD"] for x in held]))

    print("\n  by tier (D-entry, all filled-in-own-zone):")
    for t in ("cyan/orange", "red/green"):
        print("      %-12s" % t, three([x["gD"] for x in setups if x["tier"] == t]))
    print("  by side (D-entry, all filled-in-own-zone):")
    for sd, bb in (("buy", True), ("sell", False)):
        print("      %-5s" % sd, three([x["gD"] for x in setups if x["buy"] == bb]))


if __name__ == "__main__":
    main()
