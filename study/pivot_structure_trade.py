"""PIVOT-E2-TIER + full market-structure trade management.
ENTRY filter : LONG only if the last confirmed swing LOW is a HL; SHORT only if the last swing HIGH is a LH.
SL           : LONG = 0.1% below the last confirmed LL; SHORT = 0.1% above the last confirmed HH.
TRAIL exit   : LONG ratchets the stop to 0.05% below each NEW HL (swing low after entry); SHORT to 0.05%
               above each NEW LH. No fixed TP -> ride until the trailing structure stop is hit (m2m at end).
Swings are k=5, confirmed at bar i+K (causal). Fee 0.10. Compare vs the fixed +0.5/-0.3 on the same trades.
Run: python study/pivot_structure_trade.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import detect_structure                # noqa: E402
from app.liq_detect import K                              # k=5

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

    sw = detect_structure(list(hi), list(lo))
    lows = sorted((i + K, i, p, lab) for i, p, lab, h in sw if not h)     # (confirm, pivot_i, price, label)
    highs = sorted((i + K, i, p, lab) for i, p, lab, h in sw if h)

    def last_low(det, label=None):
        r = None
        for c, i, p, lab in lows:
            if c > det:
                break
            if label is None or lab == label:
                r = (i, p, lab)
        return r

    def last_high(det, label=None):
        r = None
        for c, i, p, lab in highs:
            if c > det:
                break
            if label is None or lab == label:
                r = (i, p, lab)
        return r

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk_fixed(j0, buy):
        entry = float(cl[j0]); slv = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= slv) if buy else (hi[j] >= slv):
                return -0.3
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return 0.5
        return None

    def walk_struct(det, j0, buy, sl0):
        """structure trailing: LONG stop ratchets to 0.05% below each NEW HL (swing low with pivot > det);
        SHORT to 0.05% above each new LH. Exit at the level; ride to end -> m2m. -> (gross%, hold_min, kind)."""
        entry = float(cl[j0])
        if buy:
            trail = sorted((i + K, p * (1 - 0.0005)) for i, p, lab, h in sw
                           if (not h) and lab == "HL" and i > det)         # new HLs
        else:
            trail = sorted((i + K, p * (1 + 0.0005)) for i, p, lab, h in sw
                           if h and lab == "LH" and i > det)               # new LHs
        exitlvl = sl0; tp = 0; hit_trail = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                lvl = trail[tp][1]
                exitlvl = max(exitlvl, lvl) if buy else min(exitlvl, lvl)
                hit_trail = True
                tp += 1
            if (lo[j] <= exitlvl) if buy else (hi[j] >= exitlvl):
                g = (exitlvl - entry) / entry * 100.0 if buy else (entry - exitlvl) / entry * 100.0
                return g, (st[j] - st[j0]) / 60.0, ("TRAIL" if hit_trail and (exitlvl != sl0) else "SL")
        g = (cl[-1] - entry) / entry * 100.0 if buy else (entry - cl[-1]) / entry * 100.0
        return g, (st[-1] - st[j0]) / 60.0, "OPEN"

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; trades = []
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

    # apply the ENTRY filter (long last-low HL / short last-high LH) + build SL
    kept = []
    for det, j0, buy in trades:
        if buy:
            ll = last_low(det)                              # last swing low of any label
            if not ll or ll[2] != "HL":
                continue                                     # entry needs last low = HL
            lll = last_low(det, "LL")                        # last confirmed LL for the stop
            sl0 = lll[1] * (1 - 0.001) if lll else float(cl[j0]) * (1 - SL)
        else:
            lh = last_high(det)
            if not lh or lh[2] != "LH":
                continue
            hhh = last_high(det, "HH")
            sl0 = hhh[1] * (1 + 0.001) if hhh else float(cl[j0]) * (1 + SL)
        kept.append((det, j0, buy, sl0))

    gs = []; gf = []; kinds = {"TRAIL": 0, "SL": 0, "OPEN": 0}; holds = []
    for det, j0, buy, sl0 in kept:
        g, hold, kind = walk_struct(det, j0, buy, sl0)
        gs.append(g); kinds[kind] += 1; holds.append(hold)
        rf = walk_fixed(j0, buy)
        if rf is not None:
            gf.append(rf)
    a = np.array(gs); af = np.array(gf) if gf else np.array([0.0])
    print("PIVOT-E2-TIER + structure filter (long HL / short LH), n=%d of %d trades" % (len(kept), len(trades)))
    print("  STRUCTURE exit (SL@LL/HH, trail 0.05%% below HL / above LH):")
    print("    net %+6.3f%% | win %.1f%% | avg gross %+.3f%% | exits: TRAIL %d / SL %d / open %d | avg hold %.0f min"
          % (a.mean() - FEE, 100.0 * np.mean(a > 0), a.mean(), kinds["TRAIL"], kinds["SL"], kinds["OPEN"], float(np.mean(holds))))
    print("  FIXED +0.5/-0.3 (same trades): net %+6.3f%% | win %.1f%% | TP %d SL %d"
          % (af.mean() - FEE, 100.0 * np.mean(af > 0), int(np.sum(af > 0)), int(np.sum(af < 0))))
    print("  (baseline all E2-tier trades = +0.128%%)")


if __name__ == "__main__":
    main()
