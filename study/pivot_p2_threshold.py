"""Extract a P2 (aligned eff-agg) EXIT threshold from the winner/loser hold behavior. For each PIVOT-E2-TIER
trade, take the MIN aligned P2 over the hold (entry+1 .. exit bar). Winners barely dip below 0; losers
collapse. Report the W vs L distribution and sweep candidate thresholds T (exit if aligned P2 < T during the
hold): winner-breach% (false cut) vs loser-breach% (good cut) -> the T with the cleanest split.

Run: python study/pivot_p2_threshold.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, config               # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


def load_tape(tf):
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
    return [by[b] for b in sorted(by)]


def main():
    raws = load_tape("1m")
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    p2arr = (2.0 * np.asarray(e_sh) - 1.0) * 100.0
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    def spr(k, buy):
        return (1.0 if buy else -1.0) * float(p2arr[k]) if 0 <= k < n else 0.0

    def walk(j0, buy):
        entry = float(cl[j0]); sl = entry * (1 - SL) if buy else entry * (1 + SL)
        tp = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= sl) if buy else (hi[j] >= sl):
                return "SL", j
            if (hi[j] >= tp) if buy else (lo[j] <= tp):
                return "TP", j
        return "UNRES", None

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
                trades.append((ent, buy))
        else:
            te = float(et[ent]); e2 = None
            for j in range(ent + 1, n):
                if st[j] > te + WIN:
                    break
                if spr(j, buy) >= E2_MIN:
                    e2 = j; break
            if e2 is not None:
                trades.append((e2, buy))

    LOCK = PD.LOCK
    p2_live = p2arr                                              # e_sh[k]      -> unlocked (live)
    p2_lock = np.array([p2arr[max(0, k - LOCK)] for k in range(n)])   # e_sh[k-7] -> locked (leg-2 view)

    # per trade: outcome, MIN aligned P2 over hold for BOTH series, and first-breach bar-from-entry (T=-40)
    def collect(series):
        W, L, tW, tL = [], [], [], []
        for j0, buy in trades:
            out, jx = walk(j0, buy)
            if out not in ("TP", "SL") or jx is None or jx <= j0:
                continue
            sg = 1.0 if buy else -1.0
            seg = sg * series[j0 + 1:jx + 1]
            (W if out == "TP" else L).append(float(np.min(seg)))
            br = np.where(seg < -40.0)[0]
            if len(br):
                (tW if out == "TP" else tL).append(int(br[0]) + 1)   # bars after entry
        return np.array(W), np.array(L), tW, tL

    for name, series in (("UNLOCKED (live, e_sh[k])", p2_live), ("LOCKED (e_sh[k-7], leg-2)", p2_lock)):
        W, L, tW, tL = collect(series)
        print("\n########## %s ##########" % name)
        print("  MIN aligned P2 over hold | winners=%d losers=%d" % (len(W), len(L)))
        for tag, a in (("winners", W), ("losers", L)):
            print("    %-8s  p25 %+6.1f  median %+6.1f  p75 %+6.1f  mean %+6.1f"
                  % (tag, np.percentile(a, 25), np.median(a), np.percentile(a, 75), a.mean()))
        print("  sweep (exit if aligned P2 < T):  T | win-cut | lose-catch | sep")
        for T in (-10, -20, -30, -40, -50):
            wc = float(np.mean(W < T)) * 100; lc = float(np.mean(L < T)) * 100
            print("     %+4d | %4.1f%% | %4.1f%% | %+5.1f pp" % (T, wc, lc, lc - wc))
        if tL:
            print("  breach timing (T=-40): losers first breach avg %.1f bars after entry (n=%d); "
                  "winners %.1f bars (n=%d)"
                  % (float(np.mean(tL)), len(tL), (float(np.mean(tW)) if tW else float("nan")), len(tW)))


if __name__ == "__main__":
    main()
