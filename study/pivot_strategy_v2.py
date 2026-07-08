"""PIVOT-ZZTRAIL-v2 candidate: frozen ZZTRAIL entries+exit + composite-v2 TAKE filter (take if D OR entry zone
qualifies) + a hollow AVOID list (net-negative hollow D->fill combos found in-sample). Non-merged 4h wick.

TAKE (per tier, satisfied at the D zone OR the ENTRY zone):
  hollow       -> REVERSED-inzone (buy in sell wick / sell in buy wick) OR body OR OWN-beyond
  cyan/orange  -> OWN-inzone only
  red/green    -> OWN-inzone OR OWN-beyond
Then, for HOLLOW only, EXCLUDE these (side, D-zone, fill-zone) combos:
  Buy  D in Sell zone     -> fill Body           (buy, inzone-sell, body)
  Sell D in Sell zone     -> fill Sell zone       (sell, inzone-sell, inzone-sell)   [already v2-dropped]
  Buy  D in Beyond-down   -> fill Beyond-down     (buy, beyond-down, beyond-down)
  Sell D in Beyond-up     -> fill Beyond-up       (sell, beyond-up, beyond-up)
Reports ALL vs v2 vs v2+AVOID (three-outcome NET), per-tier, trades/day. Run: python study/pivot_strategy_v2.py
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

AVOID = {("buy", "inzone-sell", "body"), ("sell", "inzone-sell", "inzone-sell"),
         ("buy", "beyond-down", "beyond-down"), ("sell", "beyond-up", "beyond-up")}


def take_rule(zone, buy, tier):
    own_in = (zone == "inzone-buy") if buy else (zone == "inzone-sell")
    rev_in = (zone == "inzone-sell") if buy else (zone == "inzone-buy")
    own_bey = (zone == "beyond-down") if buy else (zone == "beyond-up")
    if tier == "hollow":
        return rev_in or (zone == "body") or own_bey
    if tier == "cyan/orange":
        return own_in
    return own_in or own_bey


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

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        if i4 < 0:
            return None
        return zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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
        zD = zone_at(det); zE = zone_at(j0)
        if zD is None or zE is None:
            continue
        v2 = take_rule(zD, buy, tier) or take_rule(zE, buy, tier)
        side = "buy" if buy else "sell"
        avoided = tier == "hollow" and (side, zD, zE) in AVOID
        rows.append(dict(g=walk(det, j0, buy), tier=tier, v2=v2, take=v2 and not avoided, avoided=avoided))

    G = np.array([r["g"] for r in rows]) - FEE
    V2 = np.array([r["v2"] for r in rows]); TAKE = np.array([r["take"] for r in rows]); TIER = np.array([r["tier"] for r in rows])
    span = (float(max(et)) - float(min(et))) / 86400.0

    def line(tag, mask):
        m = np.array(mask, bool); a = G[m]; nn = len(a)
        if nn == 0:
            print("  %-24s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum())
        print("  %-24s n=%-3d | W %2d (%5.1f%%) | BE %2d (%5.1f%%) | L %2d (%5.1f%%) | net %+.3f%% | TOT %+.2f%% ($%+.0f) | %.1f/day"
              % (tag, nn, w, 100.0 * w / nn, b, 100.0 * b / nn, l, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0, nn / span))

    print("PIVOT-ZZTRAIL-v2 candidate  (%d frozen trades over %.1f days)\n" % (len(rows), span))
    line("ALL (frozen ZZTRAIL)", np.ones(len(rows), bool))
    line("v2 filter (D or entry)", V2)
    line("v2 + hollow AVOID  <<<", TAKE)
    line("  removed by AVOID", V2 & ~TAKE)
    print("\n  PIVOT-ZZTRAIL-v2 per tier:")
    for t in ("hollow", "cyan/orange", "red/green"):
        line("  %-11s" % t, (TIER == t) & TAKE)
    print("\n  net effect vs frozen baseline: $%+.0f -> $%+.0f  (%+.0f) | win %.1f%% -> %.1f%% | loss %.1f%% -> %.1f%%"
          % (G.sum() * 10.0, G[TAKE].sum() * 10.0, (G[TAKE].sum() - G.sum()) * 10.0,
             100.0 * (G > BE).mean(), 100.0 * (G[TAKE] > BE).mean(),
             100.0 * (G < -BE).mean(), 100.0 * (G[TAKE] < -BE).mean()))

    # ---- write the v2 freeze marker (forward split = detection end_time > freeze_ts) ----
    a = G[TAKE]; wins = a[a > 0]; los = a[a < 0]
    pf = float(wins.sum() / abs(los.sum())) if los.sum() != 0 else float("inf")
    tstat = float(a.mean() / (a.std(ddof=1) / (len(a) ** 0.5))) if len(a) > 1 and a.std() > 0 else 0.0
    bal = pk = dd = 0.0
    for x in a:
        bal += x; pk = max(pk, bal); dd = max(dd, pk - bal)
    frz_ts = int(float(et.max()))
    import time as _t
    freeze = {
        "strategy": "PIVOT-ZZTRAIL-v2",
        "supersedes_candidate_of": "PIVOT-ZZTRAIL (v1 stays under its own forward test as control)",
        "freeze_ts": frz_ts, "freeze_utc": _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(frz_ts)),
        "params": {"zigzag_pct": 0.20, "arm_pct": 0.40, "lock_pct": 0.10, "sl_pad": 0.10, "trail": 0.05,
                   "e2_min": 30.0, "fee": 0.10},
        "take_rule": {"anchor": "D OR entry, non-merged 4h wick",
                      "hollow": "REVERSED-inzone OR body OR OWN-beyond",
                      "cyan/orange": "OWN-inzone", "red/green": "OWN-inzone OR OWN-beyond",
                      "hollow_avoid": sorted("%s|%s->%s" % a for a in AVOID)},
        "in_sample_baseline": {"n": int(len(a)), "net_per_trade": round(float(a.mean()), 4),
                               "net_win_pct": round(100.0 * float((a > BE).mean()), 1),
                               "loss_pct": round(100.0 * float((a < -BE).mean()), 1),
                               "total_pct": round(float(a.sum()), 2), "profit_factor": round(pf, 2),
                               "t_stat": round(tstat, 2), "max_dd_units": round(dd, 2),
                               "unfiltered_baseline_total_pct": round(float(G.sum()), 2)},
        "criteria": {"continue_below_n": 40, "pass": "forward n>=40 AND net/trade>0 AND t-stat>=1.5",
                     "fail": "forward n>=40 AND net/trade<=0",
                     "degrade_warn": "forward n>=30 AND net/trade < %.3f (half in-sample)" % (a.mean() / 2.0)},
        "note": "IN-SAMPLE-FIT on Jun28-Jul08 tape (rules + avoid cells derived from this same data; avoid cells "
                "n=4-8). FORWARD is the only test. A trade is forward iff detection end_time > freeze_ts."}
    out = os.path.join(REPO, "study", "out", "pivot_freeze_v2.json")
    with open(out, "w") as fh:
        _json_dump(freeze, fh)
    print("\n  wrote", out, "(freeze_ts %d = %s)" % (frz_ts, freeze["freeze_utc"]))


import json as _json_mod
def _json_dump(obj, fh):
    _json_mod.dump(obj, fh, indent=2)


if __name__ == "__main__":
    main()
