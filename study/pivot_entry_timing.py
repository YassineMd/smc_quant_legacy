"""PIVOT entry-timing x D-fill-tier test, on 1m AND 5m.

For every taken setup (detect_pivots + INDEPENDENT per-side walk), classify the D badge by the aligned LIVE
panel-2 spread at D (p2d): CYAN/ORANGE = strongest (p2d>80), GREEN/RED = mid (63<p2d<=80), HOLLOW (p2d<=63).
Then, within each tier, compare three ENTRY TIMINGS under the SAME fixed exit (+0.5/-0.3, 6h cap, SL-first,
fee 0.10):
  * D  = enter at the detection bar close
  * E  = the WAIT-baseline-touch entry (the shipped entry); 'held' vs 'grey' per the panel-2 rule
  * E2 = the flip-rescue: only when E greyed (live spread @E<=0 or min over [D,E]<=-50), the first bar within
         1h of E whose aligned live spread re-confirms to >= 30.

Answers all of: D vs E, D vs E2, E vs E2, per tier. Run: python study/pivot_entry_timing.py
NOTE: cells split 3 tiers x 3 entries x 2 tf -> most are TINY (single digits); in-sample. Read as anecdote.
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402

WIN = 3600.0; H_S = 6 * 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


def load_tape(tf):
    """(bids, raws) merged over every snapshot for one tf (load_local_tape scheme, parameterized)."""
    by = {}
    for db in sorted(glob.glob(os.path.join(REPO, "study", "data", "history_snapshot_*.db"))):
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        row = con.execute("SELECT value FROM meta WHERE key=?", ("total_closed_%s" % tf,)).fetchone()
        if row is not None:
            raw = [json.loads(x[0]) for x in con.execute(
                "SELECT data FROM closed_buckets WHERE tf=? ORDER BY id", (tf,))]
            base = int(row[0]) - len(raw)
            for j, d in enumerate(raw):
                by[base + j + 1] = d
        con.close()
    bids = sorted(by)
    return bids, [by[b] for b in bids]


def analyze(tf):
    bids, raws = load_tape(tf)
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk_fixed(j0, entry, buy):
        """fixed +0.5/-0.3 from `entry` close, SL-first, 6h cap. -> +0.5 / -0.3 / None(unres)."""
        sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995
        te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            slh = (lo[j] <= sl) if buy else (hi[j] >= sl)
            tph = (hi[j] >= tp) if buy else (lo[j] <= tp)
            if slh:
                return -0.3
            if tph:
                return 0.5
        return None

    def excursion(j0, entry, buy, hz):
        """(MFE, MAE) over the next `hz` seconds from entry close: max FAVORABLE and max ADVERSE excursion %
        (side-aware — buy runs up = favorable, sell runs down = favorable)."""
        te = float(et[j0]); mx = -1e18; mn = 1e18; any_ = False
        for j in range(j0 + 1, n):
            if st[j] > te + hz:
                break
            mx = max(mx, float(hi[j])); mn = min(mn, float(lo[j])); any_ = True
        if not any_:
            return None, None
        if buy:
            return (mx - entry) / entry * 100.0, (mn - entry) / entry * 100.0
        return (entry - mn) / entry * 100.0, (entry - mx) / entry * 100.0

    # detect + independent per-side walk -> taken setups
    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; setups = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is not None:
            setups.append((det, ent, s))

    # bucket per tier -> {entry_type: [pnls]}
    tiers = {"cyan/orange (>80)": {}, "green/red (63-80)": {}, "hollow (<=63)": {}}
    for t in tiers:
        tiers[t] = {"D": [], "E": [], "E_held": [], "E2": []}
    exc = {t: {"1h": {"mfe": [], "mae": []}, "2h": {"mfe": [], "mae": []}} for t in tiers}
    for det, ent, s in setups:
        buy = s == "long"
        p2d = spr(det, buy)
        tier = "cyan/orange (>80)" if p2d > P2D_VHI else ("green/red (63-80)" if p2d > P2D_HI else "hollow (<=63)")
        # D entry
        r = walk_fixed(det, float(cl[det]), buy)
        if r is not None:
            tiers[tier]["D"].append(r)
        # E entry
        r = walk_fixed(ent, float(cl[ent]), buy)
        if r is not None:
            tiers[tier]["E"].append(r)
        # E2 entry (only if E greyed, then first re-confirm >=30 within 1h of E)
        liv = [spr(k, buy) for k in range(det, ent + 1)]
        e_held = (liv[-1] > 0.0 and min(liv) > -50.0) if liv else True
        if e_held:                                       # E VALID (panel-2 held @E) == the PIVOT-P2HELD filter
            r = walk_fixed(ent, float(cl[ent]), buy)
            if r is not None:
                tiers[tier]["E_held"].append(r)
        if not e_held:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                r = walk_fixed(e2, float(cl[e2]), buy)
                if r is not None:
                    tiers[tier]["E2"].append(r)
                for hz, key in ((3600.0, "1h"), (7200.0, "2h")):
                    mfe, mae = excursion(e2, float(cl[e2]), buy, hz)
                    if mfe is not None:
                        exc[tier][key]["mfe"].append(mfe); exc[tier][key]["mae"].append(mae)
    return n, len(setups), tiers, exc


def line(tag, pnls):
    if not pnls:
        return "%-3s n=0" % tag
    a = np.array(pnls); ntp = int(np.sum(a > 0)); nsl = len(a) - ntp
    return "%-3s n=%-3d TP%% %5.1f  net %+6.3f%%" % (tag, len(a), 100.0 * ntp / len(a), a.mean() - FEE)


def main():
    for tf in ("1m", "5m"):
        n, nset, tiers, exc = analyze(tf)
        print("\n########## %s tape: %d bars | %d taken setups ##########" % (tf, n, nset))
        print("  E-held (panel-2 held @E == PIVOT-P2HELD)  vs  E2  (fixed +0.5/-0.3 exit):")
        allh = []; alle2 = []
        for tier in tiers:
            eh = tiers[tier]["E_held"]; e2 = tiers[tier]["E2"]; allh += eh; alle2 += e2
            print("    %-18s  E-held %-28s || E2 %s" % (tier, line("", eh), line("", e2)))
        print("    %-18s  E-held %-28s || E2 %s" % ("ALL", line("", allh), line("", alle2)))
        # PROPOSED STRATEGY: cyan/orange -> E2, red/green -> E2, hollow -> E-held OR E2 (both subsets)
        strat = (tiers["cyan/orange (>80)"]["E2"] + tiers["green/red (63-80)"]["E2"]
                 + tiers["hollow (<=63)"]["E_held"] + tiers["hollow (<=63)"]["E2"])
        print("  STRATEGY (cyan E2 | red/green E2 | hollow E-held+E2): %s" % line("ALL", strat))


if __name__ == "__main__":
    main()
