"""Does a CAUSAL unlocked-P2 stop earn more? Recompute e_sh as a TRAILING window [k-7, k] (== the real-time
live-edge value, NO future peek), then run the PIVOT-E2-TIER trades with a P2-death exit: exit at a bar's
close if aligned live P2 < T (checked AFTER the intrabar SL/TP), else fixed +0.5/-0.3, 6h cap -> mark-to-mkt.
Compare net vs the plain fixed exit (both m2m at cap, so all trades resolve -> fair). Sweep T.
Run: python study/pivot_p2exit_sim.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, region_state as R, config   # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003


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
    n = len(bks); LW = config.LIVE_PANEL_WINDOW; h = LW // 2
    # eff-agg bull/bear per bar, then CAUSAL trailing share [k-h, k] (real-time edge; centered would peek +h)
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    eb = np.asarray(eb, float); er_ = np.asarray(er_, float)
    Pb = np.concatenate([[0.0], np.cumsum(eb)]); Pr = np.concatenate([[0.0], np.cumsum(er_)])
    caus = np.empty(n)
    for k in range(n):
        a = max(0, k - h); sb = Pb[k + 1] - Pb[a]; sr = Pr[k + 1] - Pr[a]; tot = sb + sr
        caus[k] = (sb / tot) if tot > 0 else 0.5
    p2c = (2.0 * caus - 1.0) * 100.0                        # causal live P2 (%)
    # centered e_sh only for detection + tier + E-held/E2 gating (same as the shipped indicator)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    def spr(k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(e_sh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

    def walk(j0, buy, T, sustain=1):
        """T=None -> plain fixed. Else P2-death: exit at close after `sustain` CONSECUTIVE bars of (RED AND
        aligned CAUSAL p2 < T), checked after the intrabar SL/TP. 6h cap -> mark-to-market. (gross%, kind)."""
        entry = float(cl[j0]); slL = entry * (1 - SL) if buy else entry * (1 + SL)
        tpL = entry * 1.005 if buy else entry * 0.995; sg = 1.0 if buy else -1.0; te = float(et[j0]); jl = j0
        streak = 0
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            jl = j
            if (lo[j] <= slL) if buy else (hi[j] >= slL):
                return -0.3, "SL"
            if (hi[j] >= tpL) if buy else (lo[j] <= tpL):
                return 0.5, "TP"
            if T is not None and sg * p2c[j] < T and sg * (cl[j] - entry) < 0.0:   # RED + P2 breach
                streak += 1
                if streak >= sustain:
                    return (sg * (cl[j] - entry) / entry * 100.0), "P2"
            else:
                streak = 0
        g = sg * (cl[jl] - entry) / entry * 100.0
        return g, "CAP"

    # PIVOT-E2-TIER trades
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

    def run(T, sustain=1):
        g = []; kinds = {"TP": 0, "SL": 0, "P2": 0, "CAP": 0}
        for j0, buy in trades:
            gg, k = walk(j0, buy, T, sustain); g.append(gg); kinds[k] += 1
        a = np.array(g)
        return a.mean() - FEE, 100.0 * np.mean(a > 0), kinds, a

    print("PIVOT-E2-TIER, %d trades | CAUSAL red+P2 stop (real-time, no look-ahead), both m2m at 6h cap\n" % len(trades))
    net0, win0, k0, a0 = run(None)
    print("  FIXED (no P2 stop)        : net %+6.3f%% | win %4.1f%% | TP %d SL %d cap %d"
          % (net0, win0, k0["TP"], k0["SL"], k0["CAP"]))
    for sus in (1, 2, 3):
        print("  --- sustain %d bar(s) (RED + P2<T in a row) ---" % sus)
        for T in (-30, -40, -50):
            net, win, kk, a = run(T, sus)
            print("    red+P2 @ %-4d          : net %+6.3f%% | win %4.1f%% | TP %d SL %d P2 %d | vs fixed %+.3f pp"
                  % (T, net, win, kk["TP"], kk["SL"], kk["P2"], net - net0))


if __name__ == "__main__":
    main()
