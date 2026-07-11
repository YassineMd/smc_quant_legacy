"""Diagnostic: for every FIRED D, print the three aligned P2 (eff-agg) spread views side by side —
  LOCKED  = the leg-2 badge = aligned (2*e_sh[det-LOCK]-1)*100  (settled, 7 buckets back; the >=65 FIRE gate)
  SETTLED = aligned (2*e_sh[det]-1)*100                          (centered value AT D, settles by det+7; the
                                                                  original tier read used by the decision study
                                                                  + the terminal in normal mode)
  LIVE    = aligned (2*e_sh_c[det]-1)*100                        (causal FIRST-PRINT at D; what you see live and
                                                                  what the 'N' terminal + the causal studies use)
Groups the fired D's by their SETTLED tier (>80 cyan/orange | 63-80 red/green | <=63 hollow) and reports the mean
of each view + how many KEEP vs LOSE their tier when you switch settled->live. Point: show that every D fired on
LOCKED>=65, that the tier is read off a DIFFERENT (unlocked) value, and how far the live value decays per tier.
Run: python study/pivot_tier_lockunlock.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402

P2D_HI = 63.0; P2D_VHI = 80.0; LW = config.LIVE_PANEL_WINDOW; LOCK = LW // 2


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


def tier(v):
    return "cyan/orange" if v > P2D_VHI else ("red/green" if v > P2D_HI else "hollow")


def main():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps); e_sh = np.asarray(e_sh, float)          # settled centered
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh_c = causal_share(eb, er_, LW)                                             # causal first-print

    def al(arr, k, buy):                                                           # aligned spread (+ = with trade)
        return (1.0 if buy else -1.0) * (2.0 * float(arr[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        buy = s == "long"
        locked = al(e_sh, det - LOCK, buy)          # leg-2 badge (settled, 7 back) — the >=65 fire gate
        settled = al(e_sh, det, buy)                # centered value AT D (original tier read)
        live = al(e_sh_c, det, buy)                 # causal first-print AT D (live tier read)
        rows.append(dict(locked=locked, settled=settled, live=live,
                         ts=tier(settled), tl=tier(live)))

    print("FIRED D's: %d  |  LOCK=%d  |  fire gate: LOCKED >= 65  |  tier split: 63 / 80\n" % (len(rows), LOCK))
    print("  grouped by SETTLED tier          n   | mean LOCKED | mean SETTLED@D | mean LIVE@D | live still same tier")
    for t in ("cyan/orange", "red/green", "hollow"):
        g = [r for r in rows if r["ts"] == t]
        if not g:
            print("    %-14s              n=0" % t); continue
        same = sum(1 for r in g if r["tl"] == t)
        print("    %-14s            %3d   | %+9.1f   | %+11.1f    | %+9.1f   | %2d / %2d (%.0f%%)"
              % (t, len(g), np.mean([r["locked"] for r in g]), np.mean([r["settled"] for r in g]),
                 np.mean([r["live"] for r in g]), same, len(g), 100.0 * same / len(g)))
    print("\n  same, grouped by LIVE (causal first-print) tier — the tier you actually see live:")
    for t in ("cyan/orange", "red/green", "hollow"):
        g = [r for r in rows if r["tl"] == t]
        if not g:
            print("    %-14s              n=0" % t); continue
        print("    %-14s            %3d   | %+9.1f   | %+11.1f    | %+9.1f   |"
              % (t, len(g), np.mean([r["locked"] for r in g]), np.mean([r["settled"] for r in g]),
                 np.mean([r["live"] for r in g])))
    print("\n  LOCKED >= 65 holds for %d / %d fired D's (%.0f%%); the rest cleared the gate on a nearby bar."
          % (sum(1 for r in rows if r["locked"] >= 65.0), len(rows),
             100.0 * sum(1 for r in rows if r["locked"] >= 65.0) / max(1, len(rows))))


if __name__ == "__main__":
    main()
