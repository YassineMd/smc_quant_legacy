"""FORWARD AUDIT of the pivot strategies — the periodic out-of-sample check.
Runs BOTH frozen configs on the latest tape and splits trades at each config's freeze line
(detection end_time > freeze_ts = FORWARD):
  * PIVOT-ZZTRAIL  (v1, the CONTROL)  — base C + breakeven lock, params from study/out/pivot_freeze.json.
  * PIVOT-ZZTRAIL-v2 (the DEFAULT)    — v1 + composite-v2 zone TAKE filter (D or entry) + hollow AVOID list,
                                        params/rules from study/out/pivot_freeze_v2.json.
Reports forward-only stats vs the in-sample baseline for each, plus (v2) per-tier and per-entry-zone forward
breakdowns so we can see WHICH fitted cells hold up as tape accrues. Prints PASS/FAIL/CONTINUE and appends one
dated row to pivot_forward_log.md (v1) and pivot_forward_log_v2.md (v2). NEVER re-tunes — only OBSERVES the
frozen rules. Run: python study/pivot_forward_audit.py
"""
import os, sys, glob, json, sqlite3, time, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles         # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

FRZ = json.load(open(os.path.join(REPO, "study", "out", "pivot_freeze.json")))
FREEZE_TS = float(FRZ["freeze_ts"]); BASE = FRZ["in_sample_baseline"]; CR = FRZ["criteria"]
FRZ2 = json.load(open(os.path.join(REPO, "study", "out", "pivot_freeze_v2.json")))
FREEZE_TS2 = float(FRZ2["freeze_ts"]); BASE2 = FRZ2["in_sample_baseline"]; CR2 = FRZ2["criteria"]
WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; H_S = 6 * 3600.0; BE = 0.05
LOG = os.path.join(REPO, "study", "out", "pivot_forward_log.md")
LOG2 = os.path.join(REPO, "study", "out", "pivot_forward_log_v2.md")
AVOID = {("buy", "inzone-sell", "body"), ("sell", "inzone-sell", "inzone-sell"),
         ("buy", "beyond-down", "beyond-down"), ("sell", "beyond-up", "beyond-up")}


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


def take_rule(zone, buy, tier):
    own_in = (zone == "inzone-buy") if buy else (zone == "inzone-sell")
    rev_in = (zone == "inzone-sell") if buy else (zone == "inzone-buy")
    own_bey = (zone == "beyond-down") if buy else (zone == "beyond-up")
    if tier == "hollow":
        return rev_in or (zone == "body") or own_bey
    if tier == "cyan/orange":
        return own_in
    return own_in or own_bey


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


def summ3(net):
    """three-outcome on NET: winner>+BE, breakeven |.|<=BE, loser<-BE."""
    a = np.array(net); s = summ(net)
    if not len(a):
        s.update(wp=float("nan"), bep=float("nan"), lp=float("nan")); return s
    s.update(wp=100.0 * (a > BE).mean(), bep=100.0 * (np.abs(a) <= BE).mean(), lp=100.0 * (a < -BE).mean())
    return s


def main():
    raws = load_1m(); bks = [_bucket_from_dict(d) for d in raws]; snaps = [b.full_snapshot() for b in bks]
    n = len(bks); _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        if i4 < 0:
            return None
        return zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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
    v2_is = []; v2_fw = []; v2_tier = {}; v2_zone = {}          # v2 forward per-tier / per-entry-zone nets
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        fwd = float(et[det]) > FREEZE_TS                         # detection AFTER the v1 freeze = forward
        fwd2 = float(et[det]) > FREEZE_TS2                       # ... after the v2 freeze
        if ent is not None:                                     # raw ENTRY probe: every E at the fixed exit
            rf = walk_fixed(ent, s == "long")
            if rf is not None:
                (ent_fw if fwd else ent_is).append(rf - FEE)
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
        net = walk(det, j0, buy) - FEE
        (strat_fw if fwd else strat_is).append(net)
        # ---- v2 overlay: zone TAKE filter (D or entry) + hollow AVOID ----
        zD = zone_at(det); zE = zone_at(j0)
        if zD is not None and zE is not None:
            take = take_rule(zD, buy, tier) or take_rule(zE, buy, tier)
            side = "buy" if buy else "sell"
            if tier == "hollow" and (side, zD, zE) in AVOID:
                take = False
            if take:
                (v2_fw if fwd2 else v2_is).append(net)
                if fwd2:
                    v2_tier.setdefault(tier, []).append(net)
                    v2_zone.setdefault(zE, []).append(net)

    S_is = summ(strat_is); S_fw = summ(strat_fw); E_is = summ(ent_is); E_fw = summ(ent_fw)
    V_is = summ3(v2_is); V_fw = summ3(v2_fw)
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    tape_end = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(et[-1]))

    # ---- v1 verdict ----
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

    # ---- v2 verdict ----
    nf2 = V_fw["n"]; netf2 = V_fw["mean"]; tf2 = V_fw["t"]
    if nf2 < CR2["continue_below_n"]:
        verdict2 = "CONTINUE (forward n=%d < %d)" % (nf2, CR2["continue_below_n"])
    elif netf2 <= 0:
        verdict2 = "FAIL (forward net/trade <= 0)"
    elif tf2 >= 1.5:
        verdict2 = "PASS (edge holding, t=%.2f)" % tf2
    else:
        verdict2 = "BORDERLINE (positive but weak, t=%.2f)" % tf2
    warn2 = (nf2 >= 30 and netf2 < BASE2["net_per_trade"] / 2.0)

    print("PIVOT FORWARD AUDIT  (%s UTC)   tape now: %s UTC\n" % (now, tape_end))
    print("== PIVOT-ZZTRAIL v1 (CONTROL) ==  freeze %s | in-sample %+.3f%%/tr, %.1f%% win, PF %.2f"
          % (FRZ["freeze_utc"], BASE["net_per_trade"], BASE["net_win_pct"], BASE["profit_factor"]))
    for tag, d in (("in-sample", S_is), ("forward  ", S_fw)):
        print("  %s : n=%-3d | win %5.1f%% | net %+.3f%%/tr | cum %+7.2f%% | t=%s | PF %s"
              % (tag, d["n"], d["win"], d["mean"], d["cum"],
                 ("%.2f" % d["t"]) if d["n"] > 1 else " --", ("%.2f" % d["pf"]) if d["n"] else "--"))
    print("  ENTRY-only (raw fires, fixed exit): forward win %5.1f%% (n=%d) | in-sample %5.1f%% (n=%d)"
          % (E_fw["win"], E_fw["n"], E_is["win"], E_is["n"]))
    print("  VERDICT: %s%s\n" % (verdict, "   [!] DEGRADATION WARNING" if warn else ""))

    print("== PIVOT-ZZTRAIL-v2 (DEFAULT) ==  freeze %s | in-sample n=%d %+.3f%%/tr, %.1f%%W/%.1f%%L, PF %.2f"
          % (FRZ2["freeze_utc"], BASE2["n"], BASE2["net_per_trade"], BASE2["net_win_pct"], BASE2["loss_pct"],
             BASE2["profit_factor"]))
    for tag, d in (("in-sample", V_is), ("forward  ", V_fw)):
        print("  %s : n=%-3d | W %5.1f%% | BE %5.1f%% | L %5.1f%% | net %+.3f%%/tr | cum %+7.2f%% | t=%s"
              % (tag, d["n"], d["wp"], d["bep"], d["lp"], d["mean"], d["cum"], ("%.2f" % d["t"]) if d["n"] > 1 else " --"))
    if nf2:
        print("  forward by tier:  " + " | ".join(
            "%s n=%d net%+.3f%%" % (t, len(v2_tier.get(t, [])), summ(v2_tier[t])["mean"])
            for t in ("hollow", "cyan/orange", "red/green") if v2_tier.get(t)))
        print("  forward by entry-zone:  " + " | ".join(
            "%s n=%d net%+.3f%%" % (z, len(v2_zone[z]), summ(v2_zone[z])["mean"]) for z in sorted(v2_zone)))
    print("  VERDICT: %s%s" % (verdict2, "   [!] DEGRADATION WARNING" if warn2 else ""))

    # ---- append v1 log (unchanged format) ----
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

    # ---- append v2 log ----
    new2 = not os.path.exists(LOG2)
    with open(LOG2, "a", encoding="utf-8") as fh:
        if new2:
            fh.write("# PIVOT-ZZTRAIL-v2 forward audit log (append-only — never edit past rows)\n\n")
            fh.write("Freeze: %s UTC. In-sample baseline: n=%d, %+.3f%%/trade, %.1f%%W / %.1f%%L, PF %.2f, t %.2f "
                     "(HEAVILY in-sample-fit — rules+avoid cells from the same tape). A trade is FORWARD iff its "
                     "detection end_time > freeze_ts. Criteria: continue<%d, pass=n>=%d & net>0 & t>=1.5, "
                     "fail=n>=%d & net<=0.\n\n"
                     % (FRZ2["freeze_utc"], BASE2["n"], BASE2["net_per_trade"], BASE2["net_win_pct"],
                        BASE2["loss_pct"], BASE2["profit_factor"], BASE2["t_stat"],
                        CR2["continue_below_n"], CR2["continue_below_n"], CR2["continue_below_n"]))
            fh.write("| audit UTC | tape end UTC | fwd n | fwd W% | fwd BE% | fwd L% | fwd net/trade | fwd cum% | "
                     "t | verdict |\n")
            fh.write("|---|---|---|---|---|---|---|---|---|---|\n")
        fh.write("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s |\n" % (
            now, time.strftime("%Y-%m-%d %H:%M", time.gmtime(et[-1])), nf2,
            ("%.1f%%" % V_fw["wp"]) if nf2 else "--", ("%.1f%%" % V_fw["bep"]) if nf2 else "--",
            ("%.1f%%" % V_fw["lp"]) if nf2 else "--", ("%+.3f%%" % netf2) if nf2 else "--",
            ("%+.2f%%" % V_fw["cum"]) if nf2 else "--", ("%.2f" % tf2) if nf2 > 1 else "--",
            verdict2 + (" [!]" if warn2 else "")))
    print("\n  -> appended v1 -> %s | v2 -> %s" % (os.path.relpath(LOG, REPO), os.path.relpath(LOG2, REPO)))


if __name__ == "__main__":
    main()
