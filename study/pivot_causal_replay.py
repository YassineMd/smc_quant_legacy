"""CAUSAL REPLAY — does the pivot edge survive when you CAN'T see the future? The eff-agg (panel-2) is a CENTERED
15-bucket window (+/-7), so the value at a bar keeps SETTLING for ~7 more buckets. Detection is already causal
(it reads the 7-lagged LOCKED value, so fires DON'T repaint), but the strategy's D-fill TIER, E-HELD test and E2
re-confirmation read the UNSETTLED live value -> they repaint, and our backtest used the SETTLED (look-ahead)
values.

This re-runs the SAME fires but computes tier / E-held / E2 from the CAUSAL eff-agg = the value at each bar using
ONLY data up to that bar (left-clamped window [k-7, k] = what the terminal shows the instant bar k prints, frozen).
It reports: repaint flip-rates (tier filled<->hollow, E-held, entry bar), and v1 + v2 P&L SETTLED (backtest) vs
CAUSAL (live-tradeable). Exit (structural SL/trail/lock) is price-only -> unchanged; only the entry decision moves.
Run: python study/pivot_causal_replay.py
"""
import os, sys, glob, json, sqlite3, bisect
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD, bar_quantiles, region_state as R, config   # noqa: E402
from app.structure import ZIGZAG_PCT                       # noqa: E402

WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001; ARM = 0.0040; LOCK = 0.0010; BE = 0.05; LW = config.LIVE_PANEL_WINDOW
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


def causal_share(bull, bear, window):
    """eff-agg share at each bar using ONLY data up to that bar: window [max(0,i-h), i], h=window//2. This is the
    LEFT-CLAMPED value the terminal shows when bar i is the live edge (i.e. before the +7 forward buckets settle it)."""
    h = max(1, window) // 2
    b = np.asarray(bull, float); r = np.asarray(bear, float)
    B = np.concatenate([[0.0], np.cumsum(b)]); Rr = np.concatenate([[0.0], np.cumsum(r)])
    out = np.empty(len(b))
    for i in range(len(b)):
        lo = max(0, i - h)
        sb = B[i + 1] - B[lo]; sr = Rr[i + 1] - Rr[lo]; tot = sb + sr
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
    n = len(bks)
    a_sh, e_sh, r_sh, sum0 = PD._p9_global(snaps)                 # SETTLED (centered) — detection + backtest
    e_sh = np.asarray(e_sh, float)
    # CAUSAL eff-agg share from the same eff-agg inputs
    ab, ar, sval = R.absorption_series(snaps, 0, n - 1, config.ABSORP_VOL_WINDOW)
    eb, er_, _ = R.eff_agg_from_absorption(snaps, 0, n - 1, config.EFF_AGG_FORCE_WINDOW, sval)
    e_sh_chk = np.asarray(R.rolling_share(eb, er_, LW), float)    # should ~match e_sh (sanity)
    e_sh_c = causal_share(eb, er_, LW)
    print("sanity: settled recompute vs _p9_global e_sh  max|diff|=%.2e  (should be ~0)" % np.max(np.abs(e_sh_chk - e_sh)))
    print("mean |causal - settled| eff-agg share = %.4f (0=no repaint, 0.5=max)\n" % np.mean(np.abs(e_sh_c - e_sh)))

    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks]); cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])
    z_et, z_lo, z_hi, z_low, z_high = load_4h()

    def zone_at(bar):
        i4 = bisect.bisect_right(z_et, et[bar]) - 1
        return None if i4 < 0 else zone5(float(cl[bar]), z_low[i4], z_lo[i4], z_hi[i4], z_high[i4])

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

    def spr(esh, k, buy):
        return (1.0 if buy else -1.0) * (2.0 * float(esh[k]) - 1.0) * 100.0 if 0 <= k < n else 0.0

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

    def entry_of(det, ent, buy, esh):
        """tier + entry index j0 under a given eff-agg array (settled or causal)."""
        p2d = spr(esh, det, buy)
        tier = "cyan/orange" if p2d > P2D_VHI else ("red/green" if p2d > P2D_HI else "hollow")
        liv = [spr(esh, k, buy) for k in range(det, ent + 1)]
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
                if spr(esh, j, buy) >= E2_MIN:
                    j0 = j; break
        return tier, e_held, j0

    fires = sorted(PD.detect_pivots(snaps), key=lambda f: (f["det_i"], f["side"]))
    scan = {"long": 0, "short": 0}; rows = []
    for f in fires:
        s = f["side"]; det = f["det_i"]; ent = f["entry_i"]
        if det < scan[s]:
            continue
        scan[s] = (ent + 1) if ent is not None else f["wait_end_i"]
        if ent is None:
            continue
        buy = s == "long"
        ts, ehs, j0s = entry_of(det, ent, buy, e_sh)              # SETTLED
        tc, ehc, j0c = entry_of(det, ent, buy, e_sh_c)            # CAUSAL
        rec = dict(buy=buy, ts=ts, tc=tc, ehs=ehs, ehc=ehc, j0s=j0s, j0c=j0c)
        for which, tier, j0 in (("s", ts, j0s), ("c", tc, j0c)):
            if j0 is None:
                rec[which] = None; continue
            zD = zone_at(det); zE = zone_at(j0)
            g = walk(det, j0, buy) - FEE
            take = None
            if zD is not None and zE is not None:
                take = take_rule(zD, buy, tier) or take_rule(zE, buy, tier)
                if tier == "hollow" and (("buy" if buy else "sell"), zD, zE) in AVOID:
                    take = False
            rec[which] = dict(g=g, tier=tier, take=bool(take))
        rows.append(rec)

    def book(sel):
        a = np.array([r[sel]["g"] for r in rows if r[sel] is not None])
        return a

    def show(tag, a):
        if not len(a):
            print("  %-26s n=0" % tag); return
        w = int((a > BE).sum()); b = int((np.abs(a) <= BE).sum()); l = int((a < -BE).sum()); nn = len(a)
        t = a.mean() / (a.std(ddof=1) / np.sqrt(nn)) if nn > 1 and a.std(ddof=1) > 0 else 0.0
        print("  %-26s n=%-3d | W %5.1f%% | BE %5.1f%% | L %5.1f%% | net %+.3f%% | TOT %+.2f%% ($%+.0f) | t=%.2f"
              % (tag, nn, 100.0 * w / nn, 100.0 * b / nn, 100.0 * l / nn, a.mean(), a.sum(), a.sum() * 10.0, t))

    both = [r for r in rows if r["s"] is not None and r["c"] is not None]
    print("REPAINT (settled vs causal), on setups that produce a trade under BOTH (n=%d of %d fires w/ entry):" % (len(both), len(rows)))
    tier_flip = sum(1 for r in both if r["ts"] != r["tc"])
    f2h = sum(1 for r in both if r["ts"] != "hollow" and r["tc"] == "hollow")
    h2f = sum(1 for r in both if r["ts"] == "hollow" and r["tc"] != "hollow")
    eh_flip = sum(1 for r in rows if r["ehs"] != r["ehc"])
    j0_diff = sum(1 for r in both if r["j0s"] != r["j0c"])
    take_flip = sum(1 for r in both if r["s"]["take"] != r["c"]["take"])
    print("  D-tier flip: %d/%d (%.0f%%)  [filled->hollow %d | hollow->filled %d]" % (tier_flip, len(both), 100.0 * tier_flip / max(1, len(both)), f2h, h2f))
    print("  E-held flip: %d/%d fires (%.0f%%)" % (eh_flip, len(rows), 100.0 * eh_flip / max(1, len(rows))))
    print("  entry bar j0 differs: %d/%d (%.0f%%) | v2 take/skip flips: %d/%d\n" % (j0_diff, len(both), 100.0 * j0_diff / max(1, len(both)), take_flip, len(both)))

    print("v1 PIVOT-ZZTRAIL (ALL trades, no zone filter):")
    show("  SETTLED (backtest)", book("s"))
    show("  CAUSAL (live-tradeable)", book("c"))
    print("\nv2 PIVOT-ZZTRAIL-v2 (zone TAKE filter + hollow avoid):")
    v2s = np.array([r["s"]["g"] for r in rows if r["s"] is not None and r["s"]["take"]])
    v2c = np.array([r["c"]["g"] for r in rows if r["c"] is not None and r["c"]["take"]])
    show("  SETTLED (backtest)", v2s)
    show("  CAUSAL (live-tradeable)", v2c)


if __name__ == "__main__":
    main()
