"""FORWARD AUDIT of the FROZEN PIVOT-ZZTRAIL strategy — the periodic out-of-sample check.
Runs the frozen strategy (base C + breakeven lock arm 0.40%/0.10%, ALL params read from study/out/pivot_freeze.json)
on the latest tape, splits trades at the freeze line (detection end_time > freeze_ts = FORWARD), and reports
forward-only stats vs the in-sample baseline. Also isolates the ENTRY signal quality (raw pivot fires at a fixed
exit) — the linchpin. Prints PASS / FAIL / CONTINUE and APPENDS one dated row to study/out/pivot_forward_log.md.
NEVER re-tune anything here; this script only OBSERVES the frozen rule. Run: python study/pivot_forward_audit.py
"""
import os, sys, glob, json, sqlite3, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

FRZ = json.load(open(os.path.join(REPO, "study", "out", "pivot_freeze.json")))
FREEZE_TS = float(FRZ["freeze_ts"]); BASE = FRZ["in_sample_baseline"]; CR = FRZ["criteria"]
WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; H_S = 6 * 3600.0
LOG = os.path.join(REPO, "study", "out", "pivot_forward_log.md")


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


def summ(net):
    net = np.array(net)
    if not len(net):
        return dict(n=0, win=float("nan"), mean=float("nan"), cum=0.0, t=float("nan"), pf=float("nan"))
    w = net[net > 0]; l = net[net < 0]
    t = net.mean() / (net.std(ddof=1) / np.sqrt(len(net))) if len(net) > 1 and net.std(ddof=1) > 0 else 0.0
    pf = w.sum() / abs(l.sum()) if len(l) else float("inf")
    return dict(n=len(net), win=100.0 * len(w) / len(net), mean=net.mean(), cum=net.sum(), t=t, pf=pf)


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

    def walk(det, j0, buy):                                      # FROZEN base C + breakeven lock
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

    def walk_fixed(j0, buy):                                     # raw ENTRY-quality probe: fixed +0.5/-0.3, 6h
        entry = float(cl[j0]); slv = entry * (1 - SL) if buy else entry * (1 + SL)
        tpv = entry * 1.005 if buy else entry * 0.995; te = float(et[j0])
        for j in range(j0 + 1, n):
            if st[j] > te + H_S:
                break
            if (lo[j] <= slv) if buy else (hi[j] >= slv):
                return -0.3
            if (hi[j] >= tpv) if buy else (lo[j] <= tpv):
                return 0.5
        return None

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}
    strat_is = []; strat_fw = []; ent_is = []; ent_fw = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        fwd = float(et[det]) > FREEZE_TS                         # detection AFTER the freeze = forward
        if ent is not None:                                     # raw ENTRY probe: every E at the fixed exit
            rf = walk_fixed(ent, s == "long")
            if rf is not None:
                (ent_fw if fwd else ent_is).append(rf - FEE)
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
        (strat_fw if fwd else strat_is).append(walk(det, j0, buy) - FEE)

    S_is = summ(strat_is); S_fw = summ(strat_fw); E_is = summ(ent_is); E_fw = summ(ent_fw)
    nf = S_fw["n"]; netf = S_fw["mean"]; tf = S_fw["t"]
    if nf < CR["continue_below_n"]:
        verdict = "CONTINUE (forward n=%d < %d)" % (nf, CR["continue_below_n"])
    elif netf <= 0:
        verdict = "FAIL (forward net/trade <= 0)"
    elif tf >= 1.5:
        verdict = "PASS (edge holding, t=%.2f)" % tf
    else:
        verdict = "BORDERLINE (positive but weak, t=%.2f)" % tf
    warn = (nf >= 30 and netf < 0.093)
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    print("PIVOT-ZZTRAIL FORWARD AUDIT  (%s UTC)" % now)
    print("  freeze: %s UTC | tape now: %s UTC | in-sample baseline: %+.3f%%/trade, %.1f%% win, PF %.2f\n"
          % (FRZ["freeze_utc"], time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(et[-1])),
             BASE["net_per_trade"], BASE["net_win_pct"], BASE["profit_factor"]))
    for tag, d in (("STRATEGY in-sample", S_is), ("STRATEGY forward ", S_fw)):
        print("  %s : n=%-3d | win %5.1f%% | net %+.3f%%/trade | cum %+7.2f%% | t=%s | PF %s"
              % (tag, d["n"], d["win"], d["mean"], d["cum"],
                 ("%.2f" % d["t"]) if d["n"] > 1 else " --", ("%.2f" % d["pf"]) if d["n"] else "--"))
    print("  ENTRY-only (raw fires, fixed exit)  in-sample win %5.1f%% (n=%d) | forward win %5.1f%% (n=%d)"
          % (E_is["win"], E_is["n"], E_fw["win"], E_fw["n"]))
    print("\n  VERDICT: %s%s" % (verdict, "   [!] DEGRADATION WARNING" if warn else ""))

    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# PIVOT-ZZTRAIL forward audit log (append-only — never edit past rows)\n\n")
            fh.write("Freeze: %s UTC (idx %d). In-sample baseline: %+.3f%%/trade, %.1f%% net win, PF %.2f.\n"
                     "A trade is FORWARD iff its detection end_time > freeze_ts. Criteria: continue<%d, "
                     "pass=n>=%d & net>0 & t>=1.5, fail=n>=%d & net<=0.\n\n"
                     % (FRZ["freeze_utc"], FRZ["freeze_idx_1m"], BASE["net_per_trade"], BASE["net_win_pct"],
                        BASE["profit_factor"], CR["continue_below_n"], CR["continue_below_n"], CR["continue_below_n"]))
            fh.write("| audit UTC | tape end UTC | fwd n | fwd win% | fwd net/trade | fwd cum% | t | "
                     "entry-only win% (fwd/IS) | verdict |\n")
            fh.write("|---|---|---|---|---|---|---|---|---|\n")
        fh.write("| %s | %s | %d | %s | %s | %s | %s | %s / %s | %s |\n" % (
            now, time.strftime("%Y-%m-%d %H:%M", time.gmtime(et[-1])), nf,
            ("%.1f%%" % S_fw["win"]) if nf else "--", ("%+.3f%%" % netf) if nf else "--",
            ("%+.2f%%" % S_fw["cum"]) if nf else "--", ("%.2f" % tf) if nf > 1 else "--",
            ("%.0f%%" % E_fw["win"]) if E_fw["n"] else "--", ("%.0f%%" % E_is["win"]) if E_is["n"] else "--",
            verdict + (" [!]" if warn else "")))
    print("  -> appended to %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
