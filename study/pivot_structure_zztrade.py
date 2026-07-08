"""PIVOT-E2-TIER + market-structure trade management, on the ZIGZAG swing engine (ZIGZAG_PCT=0.20%).
ENTRY filter : LONG only if the last confirmed swing LOW is a HL; SHORT only if the last swing HIGH is a LH.
SL (initial) : LONG = 0.1% below the last confirmed LL; SHORT = 0.1% above the last confirmed HH.
EXIT         : NO fixed TP. Trailing stop only — LONG ratchets to 0.05% below each newly-confirmed HL; SHORT
               to 0.05% above each newly-confirmed LH. Ride until the stop is hit (m2m at tape end).
Causality: a ZigZag swing is usable only at its CONFIRM bar (price retraced 0.20% from it). Filter+SL read
swings confirmed by the detection bar; the trail ratchets only on swings confirmed AFTER entry. Fee 0.10.
Compare vs the fixed +0.5/-0.3 on the same trades and the +0.128% baseline. Run: python study/pivot_structure_zztrade.py
"""
import os, sys, glob, json, sqlite3
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
from app.persistence import _bucket_from_dict            # noqa: E402
from app import pivot_detect as PD                        # noqa: E402
from app.structure import ZIGZAG_PCT, ZIGZAG_SWING_PCT     # noqa: E402

H_S = 6 * 3600.0; WIN = 3600.0; FEE = 0.10; P2D_HI = 63.0; P2D_VHI = 80.0; E2_MIN = 30.0; SL = 0.003
TRAIL = 0.0005; SL_PAD = 0.001                             # 0.05% trail pad, 0.10% initial-SL pad


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


def zigzag_confirmed(H, L, thr):
    """ZigZag pivots WITH the causal confirm bar. -> [(pivot_bar, price, is_high, confirm_bar)] in bar order.
    confirm_bar = the bar where the opposite wick retraced `thr` from the extreme (when the swing is known)."""
    n = len(H)
    if n < 2:
        return []
    piv = []; direction = 0
    hi = H[0]; hi_i = 0; lo = L[0]; lo_i = 0
    for i in range(1, n):
        h = H[i]; l = L[i]
        if direction >= 0:
            if h > hi:
                hi, hi_i = h, i
            elif l <= hi * (1.0 - thr):
                piv.append((hi_i, hi, True, i)); direction = -1; lo, lo_i = l, i
                continue
        if direction <= 0:
            if l < lo:
                lo, lo_i = l, i
            elif h >= lo * (1.0 + thr):
                piv.append((lo_i, lo, False, i)); direction = 1; hi, hi_i = h, i
    return piv


def main():
    raws = load_1m()
    bks = [_bucket_from_dict(d) for d in raws]
    snaps = [b.full_snapshot() for b in bks]
    n = len(bks)
    _, e_sh, _, _ = PD._p9_global(snaps)
    hi = np.array([b.high for b in bks]); lo = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks])
    et = np.array([b.end_time for b in bks]); st = np.array([float(d["start_time"]) for d in raws])

    thr = ZIGZAG_PCT / 100.0
    sw = zigzag_confirmed(list(hi), list(lo), thr)
    # label + split into confirmed lows/highs sorted by confirm bar
    lows = []; highs = []                                  # (confirm_bar, pivot_bar, price, label)
    prev_hi = prev_lo = None
    for pb, p, is_high, cb in sw:
        if is_high:
            lab = None if prev_hi is None else ("HH" if p > prev_hi else "LH"); prev_hi = p
            if lab:
                highs.append((cb, pb, p, lab))
        else:
            lab = None if prev_lo is None else ("HL" if p > prev_lo else "LL"); prev_lo = p
            if lab:
                lows.append((cb, pb, p, lab))
    lows.sort(); highs.sort()

    # SWING (coarse) ZigZag levels for the location filter — is entry closer to a swing high or a swing low?
    sw_swing = zigzag_confirmed(list(hi), list(lo), ZIGZAG_SWING_PCT / 100.0)
    swing_hi = sorted((cb, p) for pb, p, ih, cb in sw_swing if ih)          # (confirm_bar, price)
    swing_lo = sorted((cb, p) for pb, p, ih, cb in sw_swing if not ih)
    sw_lab = []                                                             # (confirm_bar, label) SWING zigzag
    _ph = _pl = None
    for pb, p, ih, cb in sw_swing:
        if ih:
            lab = None if _ph is None else ("HH" if p > _ph else "LH"); _ph = p
        else:
            lab = None if _pl is None else ("HL" if p > _pl else "LL"); _pl = p
        if lab:
            sw_lab.append((cb, lab))
    sw_lab.sort()

    def last_swing(det):
        """label of the LAST printed/confirmed SWING (0.60%) zigzag swing at or before `det` (causal)."""
        r = None
        for cb, lab in sw_lab:
            if cb > det:
                break
            r = lab
        return r

    def _last_by(arr, bar):
        r = None
        for c, p in arr:
            if c > bar:
                break
            r = p
        return r

    scalp_hi = sorted((cb, p) for pb, p, ih, cb in sw if ih)               # scalp (0.20%) swing levels too
    scalp_lo = sorted((cb, p) for pb, p, ih, cb in sw if not ih)

    def loc_ok(j0, buy, hi_arr, lo_arr, invert=False):
        """LONG kept if entry is closer to the last confirmed swing LOW (HL/LL) than the last swing HIGH
        (HH/LH); SHORT the mirror. invert=True flips it -> long near the HIGH / short near the LOW (momentum).
        Uses the LAST CONFIRMED swing of each side (confirm<=entry). Both must exist, else excluded."""
        entry = float(cl[j0])
        shp = _last_by(hi_arr, j0); slp = _last_by(lo_arr, j0)
        if shp is None or slp is None:
            return False
        near_low = abs(entry - slp) < abs(entry - shp)         # entry closer to the swing LOW (HL/LL)
        want_low = buy if not invert else (not buy)            # long->low / short->high ; invert flips
        return near_low == want_low

    def last_low(det, label=None):
        r = None
        for c, pb, p, lab in lows:
            if c > det:
                break
            if label is None or lab == label:
                r = (pb, p, lab)
        return r

    def last_high(det, label=None):
        r = None
        for c, pb, p, lab in highs:
            if c > det:
                break
            if label is None or lab == label:
                r = (pb, p, lab)
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

    def walk_struct(j0, buy, sl0, trail):
        """trail = sorted [(confirm_bar, level)] ; LONG level = HL*(1-0.05%), SHORT level = LH*(1+0.05%).
        Ratchet the stop as each confirms (confirm>entry), exit at the level. -> (gross%, hold_min, kind)."""
        entry = float(cl[j0]); exitlvl = sl0; tp = 0; ratcheted = False
        for j in range(j0 + 1, n):
            while tp < len(trail) and trail[tp][0] <= j:
                lvl = trail[tp][1]
                new = max(exitlvl, lvl) if buy else min(exitlvl, lvl)
                if new != exitlvl:
                    exitlvl = new; ratcheted = True
                tp += 1
            if (lo[j] <= exitlvl) if buy else (hi[j] >= exitlvl):
                g = (exitlvl - entry) / entry * 100.0 if buy else (entry - exitlvl) / entry * 100.0
                return g, (st[j] - st[j0]) / 60.0, ("TRAIL" if ratcheted else "SL")
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

    kept = []; allt = []; allt_fx = []; allt_e = []            # +allt_e = struct-SL kept only if risk <= 0.3%
    for det, j0, buy in trades:
        entry = float(cl[j0])
        if buy:
            lll = last_low(det, "LL")
            sl0 = lll[1] * (1 - SL_PAD) if lll else entry * (1 - SL)
            sl_fx = entry * (1 - SL)                            # fixed 0.3%
            trail = sorted((c, p * (1 - TRAIL)) for c, pb, p, lab in lows if lab == "HL" and c > j0)
            aligned = (last_low(det) or (0, 0, None))[2] == "HL"
            risk = (entry - sl0) / entry                        # entry->structural stop distance
        else:
            hhh = last_high(det, "HH")
            sl0 = hhh[1] * (1 + SL_PAD) if hhh else entry * (1 + SL)
            sl_fx = entry * (1 + SL)
            trail = sorted((c, p * (1 + TRAIL)) for c, pb, p, lab in highs if lab == "LH" and c > j0)
            aligned = (last_high(det) or (0, 0, None))[2] == "LH"
            risk = (sl0 - entry) / entry
        allt.append((det, j0, buy, sl0, trail))
        allt_fx.append((det, j0, buy, sl_fx, trail))
        if risk <= SL:                                          # only take trades risking <= 0.3% to structure
            allt_e.append((det, j0, buy, sl0, trail))
        if aligned:
            kept.append((det, j0, buy, sl0, trail))

    def run_struct(recs):
        gs = []; kinds = {"TRAIL": 0, "SL": 0, "OPEN": 0}; holds = []
        for det, j0, buy, sl0, trail in recs:
            g, hold, kind = walk_struct(j0, buy, sl0, trail)
            gs.append(g); kinds[kind] += 1; holds.append(hold)
        a = np.array(gs) if gs else np.array([0.0])
        return a - FEE, kinds, holds

    # BASE = ALL E2-tier trades, fixed +0.5/-0.3 (what we run today)
    base = [walk_fixed(j0, buy) for _d, j0, buy in trades]; base = [r for r in base if r is not None]
    nb = np.array(base) - FEE
    # last-printed SWING zigzag LABEL filter (judged at the detection bar)
    LIT = {True: {"HH", "LH"}, False: {"LH", "LL"}}        # literal as written: long HH/LH, short LH/LL
    TRD = {True: {"HH", "HL"}, False: {"LH", "LL"}}        # bullish/bearish trend: long HH/HL, short LH/LL
    Hset = [r for r in allt if last_swing(r[0]) in LIT[r[2]]]
    Iset = [r for r in allt if last_swing(r[0]) in TRD[r[2]]]
    nolab = sum(1 for r in allt if last_swing(r[0]) is None)
    n_all, _k, _h = run_struct(allt)
    n_H, _, _ = run_struct(Hset); n_I, _, _ = run_struct(Iset)

    def row(tag, arr):
        return "  %-48s |  %3d   | %+6.3f%%  | %4.1f | %+7.2f%%" % (
            tag, len(arr), (arr.mean() if len(arr) else 0.0),
            100.0 * np.mean(arr > 0) if len(arr) else 0.0, arr.sum())

    print("PIVOT-ZZTRAIL base + last-SWING-zigzag(%.2f%%) LABEL filter, judged at detection\n" % ZIGZAG_SWING_PCT)
    print("  variant                                          | trades | per-trade | win%% | CUMULATIVE")
    print(row("C) BASE — all trades, no filter", n_all))
    print("  -- LITERAL (as written): long=HH/LH, short=LH/LL --")
    print(row("H) literal filter", n_H))
    print("  -- TREND (likely intended): long=HH/HL, short=LH/LL --")
    print(row("I) bullish/bearish trend filter", n_I))
    print("\n  (%d of %d trades have no confirmed swing label yet -> excluded by both filters)" % (nolab, len(allt)))


if __name__ == "__main__":
    main()
