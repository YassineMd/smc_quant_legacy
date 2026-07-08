"""Hypothesis: PIVOT-E2-TIER entries SUPPORTED by market structure are more reliable. Structure bias at the
detection bar D = the last CONFIRMED swing (i+K <= D, no look-ahead): HH/HL -> bullish, LH/LL -> bearish.
SUPPORTED = pivot side matches the bias (long+bull / short+bear); AGAINST = opposed. Split the strategy
trades and compare TP%/net (fixed +0.5/-0.3, taker 0.10). Also a stricter both-swings-aligned bias.
Run: python study/pivot_structure_filter.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import detect_structure                # noqa: E402
from app.liq_detect import K                              # k=5 pivot confirm lag

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


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


def main():
    raws = load_1m()
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    # structure swings -> (confirm_bar = i+K, label, is_high). Sorted by confirm_bar for causal lookup.
    sw = detect_structure(list(hi), list(lo))
    cbar = np.array([i + K for i, _p, _lab, _h in sw])
    labs = [lab for _i, _p, lab, _h in sw]
    order = np.argsort(cbar, kind="stable"); cbar = cbar[order]; labs = [labs[o] for o in order]
    ishigh = np.array([sw[o][3] for o in order])

    def bias(det):
        """(-1 bear / +1 bull / 0 none) from the last confirmed swing before det; and the strict both-aligned."""
        k = np.searchsorted(cbar, det, "right") - 1
        if k < 0:
            return 0, 0
        lab = labs[k]
        b = 1 if lab in ("HH", "HL") else -1
        # strict: last confirmed HIGH and last confirmed LOW must agree
        last_h = last_l = None
        for j in range(k, -1, -1):
            if ishigh[j] and last_h is None:
                last_h = labs[j]
            if not ishigh[j] and last_l is None:
                last_l = labs[j]
            if last_h and last_l:
                break
        strict = 0
        if last_h == "HH" and last_l == "HL":
            strict = 1
        elif last_h == "LH" and last_l == "LL":
            strict = -1
        return b, strict

    def side_bias(det, buy):
        """Last confirmed swing of the RELEVANT side before det: for a long -> the swing LOW label (HL/LL);
        for a short -> the swing HIGH label (LH/HH). None if none confirmed yet."""
        k = np.searchsorted(cbar, det, "right") - 1
        for j in range(k, -1, -1):
            if buy and not ishigh[j]:
                return labs[j]
            if (not buy) and ishigh[j]:
                return labs[j]
        return None

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, buy):
        entry = float(cl[j0]); sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                return -0.3
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return 0.5
        return None

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []            # (det, entry_bar, buy)
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
        if e_held:
            if tier == "hollow":
                trades.append((det, ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((det, e2, buy))

    def econ(events):
        res = [walk(j0, buy) for _d, j0, buy in events]
        res = [r for r in res if r is not None]
        if not res:
            return 0, float("nan"), float("nan")
        a = np.array(res)
        return len(res), 100.0 * np.mean(a > 0), a.mean() - FEE

    def split(mode):
        sup = []; agn = []; neu = []
        for det, j0, buy in trades:
            b, strict = bias(det)
            val = strict if mode == "strict" else b
            sgn = 1 if buy else -1
            (sup if val == sgn else (agn if val == -sgn else neu)).append((det, j0, buy))
        return sup, agn, neu

    # ---- the requested SETUP: long needs last swing LOW = HL; short needs last swing HIGH = LH ----
    sup = []; agn = []; neu = []
    for det, j0, buy in trades:
        lb = side_bias(det, buy); want = "HL" if buy else "LH"; opp = "LL" if buy else "HH"
        (sup if lb == want else (agn if lb == opp else neu)).append((det, j0, buy))
    print("== SETUP: LONG needs a HL / SHORT needs a LH (last confirmed side-swing) ==")
    for tag, ev in (("SUPPORTED (HL/LH)", sup), ("AGAINST (LL/HH)", agn), ("no side-swing yet", neu)):
        nn, tp, net = econ(ev)
        print("  %-22s n=%-3d | TP%% %5.1f | net %+6.3f%%" % (tag, nn, tp, net))

    for mode in ("simple (last swing)", "strict (both swings agree)"):
        sup, agn, neu = split("strict" if mode.startswith("strict") else "simple")
        print("\n== structure bias: %s ==" % mode)
        for tag, ev in (("SUPPORTED (aligned)", sup), ("AGAINST  (opposed)", agn), ("neutral/none", neu)):
            nn, tp, net = econ(ev)
            print("  %-20s n=%-3d | TP%% %5.1f | net %+6.3f%%" % (tag, nn, tp, net))
    print("\n  (baseline all trades: +0.128%%)")


if __name__ == "__main__":
    main()
