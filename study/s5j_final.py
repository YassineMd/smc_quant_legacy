"""S5j — FINAL LEG-5 (EXISTS form, = S5d leg5w VERBATIM) + S5i machinery. Supersedes S5i's fire
set (its priority-scan encoding was an architect error). Pre-registered; multiplicity +2 cells
(long/short) -> program counter 516. Everything except leg 5 is IDENTICAL to S5i.

LEG 5 (S5d leg5w, no rewrite — the rolling max/min form): LONG-eligible iff C(b) < max{open[b-99..
b-49]} (EXISTS N in [50,100] with O(b-N+1) > C(b)); SHORT-eligible iff C(b) > min of the zone.
Both-eligible is legal; locked legs 1'-4 pick the side (leg-2 sides are mutually exclusive).

UNCHANGED FROM S5i: locked legs 1'-4 verbatim (leg 2 = SHARE >= 65% as registered); 1h fire window
+ fire-search blackout; 1h excursion extraction per fire regardless of fill; entry router on the
moving POC baseline (at/through -> MARKET at fire close, else WAIT the hour for a touch of the
CURRENT baseline, no fire-bar self-touch); taker 0.10% net; EXIT FIXED ONLY TP+0.5/SL-0.3 (S1
walker, entry-bar SL flagged for touch entries, 6h cap -> UNRESOLVED counted).

CSV = S5i columns with first_red_N / first_green_N (the smallest EXISTS witness each way, blank if
none) replacing decN. Underpowered rule n < 20/side -> counts only. HARD STOP after the report.

S5j-r REDO (operator ruling 2026-07-04, the 14873 post-mortem): the study must read EXCLUSIVELY
LOCKED data on panels 0, 2 and 6. Panels 2 and 6 already did (locked badge index; locked phase
row). LEG 1 CORRECTED (leg 1''): among the LOCKED markers of the 16-bar selection, the two most
recent must be on-side AND the newest must be the on-side EXTREME cross (+50 up for long / -50
down for short) — a settling DOT at the extreme level does NOT count. Anchors asserted both ways:
20977 (locked +50 up newest) must PASS leg 1''; 14873 (+50 still a dot; newest locked = 0-line)
must FAIL. Multiplicity +2 for the re-judgment -> counter 518.
"""
import os, sys, csv, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from app.persistence import _bucket_from_dict                       # noqa: E402
from m10_sweep_s5b import load_merged, p9_full, sel_markers, _last_cross, LOCK  # noqa: E402

REPO = os.path.dirname(HERE); OUT = os.path.join(REPO, "study", "out")
WIN = 3600.0; H_S = 6 * 3600.0
FEE = 0.10
FIRST = 100
UNDER_N = 20
NULL, BREAKEVEN = 37.5, 50.0


def main():
    t0 = time.time()
    bids, raws, _pd, _g, _d = load_merged()
    n = len(raws)
    bks = [_bucket_from_dict(d) for d in raws]
    hi = np.array([b.high for b in bks]); lo_ = np.array([b.low for b in bks])
    cl = np.array([b.close_price for b in bks]); et = np.array([b.end_time for b in bks])
    st = np.array([float(d["start_time"]) for d in raws])
    op = np.array([b.open_price for b in bks])
    bid_arr = np.array(bids); b0 = bids[0]
    snaps = [b.full_snapshot() for b in bks]
    _, e_sh, _, _, _, _, _, sum0 = p9_full(snaps)
    df = pd.read_parquet(os.path.join(OUT, "m10_sweep_1m.parquet"))
    idx = np.arange(16, n)

    # legs 2-4 (all LOCKED). leg 2 = BADGE SPREAD >= 65 (14876 anchor). leg 3 TWO-SIDED per the r3
    # update: own-side table's dominant phase == START/DURING AND the opposite table's dominant
    # phase != START/DURING. leg 4 = locked P6 spread (sweep verbatim).
    sh = e_sh[np.maximum(0, idx - LOCK)]
    spr2 = (2.0 * sh - 1.0) * 100.0
    l2 = {"long": spr2 >= 65.0, "short": -spr2 >= 65.0}
    dom_u = df.phase_dom_up.to_numpy(); dom_d = df.phase_dom_dn.to_numpy()
    l3 = {"long": (dom_u == "STARTDUR") & (dom_d != "STARTDUR"),
          "short": (dom_d == "STARTDUR") & (dom_u != "STARTDUR")}
    legs34 = {s: l3[s] & df["leg4_" + s].to_numpy() for s in ("long", "short")}
    legs234 = {s: l2[s] & legs34[s] for s in ("long", "short")}
    r_14876 = (14876 - bids[0]) - 16
    assert not l2["long"][r_14876], "14876 leg-2 anchor must FAIL (spread < 65)"
    assert l2["long"][(20977 - bids[0]) - 16], "20977 must still pass leg 2 (spread 78.5)"

    # LEG 1'' on a 100-BAR selection (r3 update; was 16): two most recent LOCKED markers on-side
    # AND the newest is the on-side EXTREME cross (+50 long / -50 short). Dots never count.
    SELW = 100

    def sel_markers_w(b):
        """Panel markers on the [b-99, b] selection — terminal _last_cross verbatim, one most-
        recent locked cross per level, locked region = first SELW-LOCK bars; newest first."""
        vals = sum0[b - (SELW - 1):b + 1]; ex = list(range(SELW)); end = SELW - LOCK
        out = []
        for L in (50.0, 0.0, -50.0):
            m = _last_cross(vals, ex, 1, end, L, 1, -1, end)
            if m is not None:
                out.append(m)
        out.sort(key=lambda m: -m[0])
        return out

    def leg1pp(b, s):
        if b < SELW - 1:                                  # needs the full 100-bar selection
            return False
        mk = sel_markers_w(b)
        if len(mk) < 2:
            return False
        if s == "long":
            return mk[0][2] > 0 and mk[1][2] > 0 and mk[0][1] == 50.0
        return mk[0][2] < 0 and mk[1][2] < 0 and mk[0][1] == -50.0

    b0_ = bids[0]
    assert leg1pp(20977 - b0_, "long"), "20977 leg-1 anchor must PASS"
    assert not leg1pp(14873 - b0_, "long"), "14873 leg-1 anchor must FAIL"
    def leg1pp16(b, s):                                   # r2's 16-bar leg 1'' (comparison only)
        mk = sel_markers(sum0, b)
        if len(mk) < 2:
            return False
        if s == "long":
            return mk[0][2] > 0 and mk[1][2] > 0 and mk[0][1] == 50.0
        return mk[0][2] < 0 and mk[1][2] < 0 and mk[0][1] == -50.0

    # anchors under the r3 definitions: leg-level, as ruled
    assert leg1pp(20977 - b0_, "long"), "20977 leg-1'' (100-bar) must PASS"
    assert not leg1pp(14873 - b0_, "long"), "14873 leg-1'' (100-bar) must FAIL (+50 was a dot)"

    def apply_leg1(mask, s, fn):
        m = mask.copy()
        for r in np.flatnonzero(m):
            if not fn(int(idx[r]), s):
                m[r] = False
        return m
    legs14 = {s: apply_leg1(legs234[s], s, leg1pp) for s in ("long", "short")}
    r2_leg14 = {s: apply_leg1(l2[s] & df["leg3_" + s].to_numpy() & df["leg4_" + s].to_numpy(),
                              s, leg1pp16) for s in ("long", "short")}       # S5j-r2 set

    # LEG 5 — EXISTS form on the r3 zone N = 60..100 (opens b-99..b-59; was 50..100)
    rop = np.round(op * 100).astype(np.int64); rcl = np.round(cl * 100).astype(np.int64)
    zmax = pd.Series(rop).rolling(41).max().shift(59).to_numpy()
    zmin = pd.Series(rop).rolling(41).min().shift(59).to_numpy()
    zmax50 = pd.Series(rop).rolling(51).max().shift(49).to_numpy()   # r2 zone, comparison only
    zmin50 = pd.Series(rop).rolling(51).min().shift(49).to_numpy()
    okk = np.arange(n) >= FIRST
    rng_L = np.zeros(n, bool); rng_S = np.zeros(n, bool)
    rng_L[okk] = rcl[okk] < zmax[okk]; rng_S[okk] = rcl[okk] > zmin[okk]
    rng_L50 = np.zeros(n, bool); rng_S50 = np.zeros(n, bool)
    rng_L50[okk] = rcl[okk] < zmax50[okk]; rng_S50[okk] = rcl[okk] > zmin50[okk]

    fire_bars = {s: [int(idx[r]) for r in np.flatnonzero(
        legs14[s] & (rng_L if s == "long" else rng_S)[idx])] for s in ("long", "short")}

    def witness(b):
        """(first_red_N, first_green_N): smallest EXISTS witness each way in [50,100]."""
        red = green = ""
        c = rcl[b]
        for N in range(50, 101):
            o = rop[b - N + 1]
            if red == "" and o > c:
                red = N
            if green == "" and o < c:
                green = N
            if red != "" and green != "":
                break
        return red, green

    # comparison sets: S5j-r2 (16-bar leg1'', one-sided leg3, zone 50-100) and S5d-locked
    s5d_bars = {s: {int(idx[r]) for r in np.flatnonzero(
        df["fire_" + s].to_numpy() & (rng_L50 if s == "long" else rng_S50)[idx])}
        for s in ("long", "short")}
    s5j_old_bars = {s: {int(idx[r]) for r in np.flatnonzero(
        r2_leg14[s] & (rng_L50 if s == "long" else rng_S50)[idx])} for s in ("long", "short")}

    poc = np.array([float(d.get("poc_price", 0.0)) for d in raws])
    base = np.empty(n); base[0] = poc[0]
    for k in range(1, n):
        base[k] = poc[k] * 0.05 + base[k - 1] * 0.95
    rbase = np.round(base * 100).astype(np.int64)

    def walk_fixed(j_e, entry, side, entry_at_close):
        long = side == "long"
        sl_lvl = entry * (1 - 0.003) if long else entry * (1 + 0.003)
        tp_lvl = entry * (1 + 0.005) if long else entry * (1 - 0.005)
        t_e = float(et[j_e]) if entry_at_close else float(st[j_e])
        if not entry_at_close:
            if (lo_[j_e] <= sl_lvl) if long else (hi[j_e] >= sl_lvl):
                return "SL*", -0.3, 0.0
        j = j_e + 1
        while j < n and st[j] <= t_e + H_S:
            a = (hi[j] >= tp_lvl) if long else (lo_[j] <= tp_lvl)
            b_ = (lo_[j] <= sl_lvl) if long else (hi[j] >= sl_lvl)
            if a or b_:
                m = (st[j] - t_e) / 60.0
                return ("SL" + ("*" if a else ""), -0.3, m) if b_ else ("TP", 0.5, m)
            j += 1
        return "UNRESOLVED", None, None

    def rooms(zhp, zlp, long):
        """Profit-side room (toward the target extreme) / adverse-side room (toward the stop side),
        side-aware, and their ratio. Long: profit=up to high (zhp), adverse=down to low (|zlp|).
        Short: profit=down to low (|zlp|), adverse=up to high (zhp). Ratio>1 = favorable geometry;
        a negative room flags the entry is OUTSIDE the zone on that side (a breakout, not a pivot)."""
        pr, ar = (zhp, -zlp) if long else (-zlp, zhp)
        rt = round(pr / ar, 3) if abs(ar) > 1e-9 else ""
        return round(pr, 4), round(ar, 4), rt

    allf = sorted([(b, s) for s in ("long", "short") for b in fire_bars[s]])
    rows = []; n_blk = 0
    blackout_until = -1e18
    for b, s in allf:
        if et[b] < blackout_until:
            n_blk += 1
            continue
        blackout_until = et[b] + WIN
        long = s == "long"; c0 = float(cl[b]); B = float(base[b])
        j1 = int(np.searchsorted(et, et[b] + WIN, side="right"))
        red, green = witness(b)
        # r5b: characterize the leg-5 CONTEXT zone (bars b-99..b-59, i.e. N=60..100) — its highest
        # high and lowest low. r5c: expressed as % from the ENTRY price (set below once the entry is
        # known); CANCELLED rows have no entry -> fire-close counterfactual.
        zsl = slice(b - 99, b - 58)                        # 41 bars = N 60..100
        zhi = float(np.max(hi[zsl])); zlo = float(np.min(lo_[zsl]))
        row = dict(ts=round(float(et[b]), 3), fire_bid=int(bid_arr[b]), side=s,
                   first_red_N=red, first_green_N=green, baseline=round(B, 4),
                   zone_hi_pct="", zone_lo_pct="", zone_range="",
                   profit_room="", adverse_room="", profit_room_ratio="", route="",
                   status="", entry="", delay="", outcome="", pnl="", mins="",
                   w_max="", w_min="", t_max="", t_min="")
        # r4 entry qualification: the entry bar must close BULLISH for longs / BEARISH for shorts,
        # and every entry executes AT THE BAR CLOSE (the color is a close property; the operator's
        # "enter the close" generalized). MKT route requires the fire bar itself to qualify;
        # otherwise the fire FALLS THROUGH to the WAIT scan. WAIT entry bar (long): touches the
        # moving baseline AND closes above BOTH its open and the baseline — one condition covering
        # the normal pullback case and the reclaim exception (open below the line, close above).
        j_e = None; entry = None
        mkt_ok = ((rcl[b] <= rbase[b]) and (rcl[b] > rop[b])) if long else \
                 ((rcl[b] >= rbase[b]) and (rcl[b] < rop[b]))
        if mkt_ok:
            j_e = b; entry = c0
            row["route"] = "MKT"; row["status"] = "MKT"; row["delay"] = 0.0
        else:
            row["route"] = "WAIT"
            # r5 correction: bar COLOR is the only close requirement — a bullish bar under the
            # baseline (long) / bearish bar above it (short) is a valid entry; close vs baseline
            # does not matter. Entry bar = first bar touching/at the line with the on-side color.
            for j in range(b + 1, j1):
                if long:
                    if lo_[j] <= base[j] and rcl[j] > rop[j]:
                        j_e = j
                        break
                else:
                    if hi[j] >= base[j] and rcl[j] < rop[j]:
                        j_e = j
                        break
            if j_e is not None:
                entry = float(cl[j_e])
                row["status"] = "TOUCH"
                row["delay"] = round((et[j_e] - et[b]) / 60.0, 2)
        if j_e is None:
            row["status"] = "CANCELLED"
            row["zone_hi_pct"] = round((zhi - c0) / c0 * 100.0, 4)   # no entry -> fire-close ref
            row["zone_lo_pct"] = round((zlo - c0) / c0 * 100.0, 4)
            row["zone_range"] = round((zhi - zlo) / c0 * 100.0, 4)
            row["profit_room"], row["adverse_room"], row["profit_room_ratio"] = \
                rooms(row["zone_hi_pct"], row["zone_lo_pct"], long)
            if j1 > b + 1:                                # counterfactual stays FIRE-referenced
                w = slice(b + 1, j1)
                k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
                row["w_max"] = round((float(np.max(hi[w])) - c0) / c0 * 100.0, 4)
                row["w_min"] = round((float(np.min(lo_[w])) - c0) / c0 * 100.0, 4)
                row["t_max"] = round((float(et[b + 1 + k_up]) - et[b]) / 60.0, 2)
                row["t_min"] = round((float(et[b + 1 + k_dn]) - et[b]) / 60.0, 2)
            rows.append(row)
            continue
        # r5c: zone range now ENTRY-referenced (the zone bars are fixed; only the reference moves)
        row["zone_hi_pct"] = round((zhi - entry) / entry * 100.0, 4)
        row["zone_lo_pct"] = round((zlo - entry) / entry * 100.0, 4)
        row["zone_range"] = round((zhi - zlo) / entry * 100.0, 4)   # AO = hi - lo (as % of entry)
        row["profit_room"], row["adverse_room"], row["profit_room_ratio"] = \
            rooms(row["zone_hi_pct"], row["zone_lo_pct"], long)
        # r4 excursions: ENTRY-referenced — % from the entry price over 1h from the entry bar
        j1e = int(np.searchsorted(et, et[j_e] + WIN, side="right"))
        if j1e > j_e + 1:
            w = slice(j_e + 1, j1e)
            k_up = int(np.argmax(hi[w])); k_dn = int(np.argmin(lo_[w]))
            row["w_max"] = round((float(np.max(hi[w])) - entry) / entry * 100.0, 4)
            row["w_min"] = round((float(np.min(lo_[w])) - entry) / entry * 100.0, 4)
            row["t_max"] = round((float(et[j_e + 1 + k_up]) - et[j_e]) / 60.0, 2)
            row["t_min"] = round((float(et[j_e + 1 + k_dn]) - et[j_e]) / 60.0, 2)
        row["entry"] = round(entry, 4)
        out, pnl, mins = walk_fixed(j_e, entry, s, entry_at_close=True)
        row["outcome"] = out
        row["pnl"] = round(pnl, 4) if pnl is not None else ""
        row["mins"] = round(mins, 2) if mins is not None else ""
        rows.append(row)

    with open(os.path.join(OUT, "s5j_episodes.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["fire_bid", "side", "outcome", "w_max", "w_min",
                                          "zone_hi_pct", "zone_lo_pct", "zone_range",
                                          "profit_room", "adverse_room", "profit_room_ratio",
                                          "pnl", "mins", "entry", "baseline", "route", "status",
                                          "delay", "t_max", "t_min", "first_red_N", "first_green_N",
                                          "ts"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    md = ["# S5j-r5 — Fully-Locked Confluence + color-qualified close-entries (corrected)", "",
          "_**S5j-r5 (operator correction of r4's entry translation): the bar COLOR is the only"
          " close requirement — a bullish bar UNDER the baseline is a valid long entry, a bearish"
          " bar ABOVE it a valid short entry; close-vs-baseline does not matter (r4 wrongly"
          " required the close beyond the line). WAIT entry bar (long) = first bar touching/at the"
          " moving baseline (low <= line) that closes bullish; short mirrored. Every entry still"
          " executes AT THE BAR CLOSE; a MKT-route fire bar failing the color test falls through"
          " to the WAIT scan. w_max/w_min stay ENTRY-referenced (CANCELLED rows keep the"
          " fire-close counterfactual). Legs unchanged from r3 (100-bar leg 1'', spread-65 leg 2,"
          " two-sided leg 3, zone 60-100); anchors hold (20977 / 14873 / 14876). Multiplicity +2"
          " -> counter 526.**"
          " 1h windows + fire-search blackout (%d fires absorbed); taker %.2f%% net; fixed"
          " TP+0.5/SL-0.3 exits from the entry close (S1, * = ambiguous). References: %.1f%% null"
          " / %.1f%% breakeven. Underpowered: n < %d -> counts only._"
          % (n_blk, FEE, NULL, BREAKEVEN, UNDER_N), "",
          "## 1. Funnel (deltas vs S5j-r2 and S5d-locked)", "",
          "| side | fire bars | vs S5j-r2 | vs S5d-locked | episodes | MKT | WAIT | "
          "touched | CANCELLED |", "|---|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        fb = set(fire_bars[s])
        ep_all = [r for r in rows if r["side"] == s]
        md.append("| %s | %d | +%d / -%d (n=%d) | +%d / -%d (n=%d) | %d | %d | %d | %d | %d |" % (
            s, len(fb), len(fb - s5j_old_bars[s]), len(s5j_old_bars[s] - fb), len(s5j_old_bars[s]),
            len(fb - s5d_bars[s]), len(s5d_bars[s] - fb), len(s5d_bars[s]),
            len(ep_all), sum(1 for r in ep_all if r["status"] == "MKT"),
            sum(1 for r in ep_all if r["status"] in ("TOUCH", "CANCELLED")),
            sum(1 for r in ep_all if r["status"] == "TOUCH"),
            sum(1 for r in ep_all if r["status"] == "CANCELLED")))
    md += ["", "## 2. Economics per side (fixed exit; filled entries)", "",
           "| side | n | TP/SL/unres | TP rate | vs null | vs BE | exp gross | exp net | med hold | "
           "delay med/p90 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        e = [r for r in rows if r["side"] == s and r["status"] in ("MKT", "TOUCH")]
        pnls = [r["pnl"] for r in e if r["pnl"] != ""]
        ntp = sum(1 for r in e if r["outcome"] == "TP")
        nsl = sum(1 for r in e if r["outcome"].startswith("SL"))
        nun = sum(1 for r in e if r["outcome"] == "UNRESOLVED")
        res = ntp + nsl
        p = 100.0 * ntp / res if res else float("nan")
        dls = sorted(r["delay"] for r in e if r["status"] == "TOUCH")
        under = " _(under)_" if len(e) < UNDER_N else ""
        if pnls:
            arr = np.array(pnls, float)
            md.append("| %s%s | %d | %d/%d/%d | %s | %s | %s | %+.3f%% | %+.3f%% | %s | %s |" % (
                s, under, len(e), ntp, nsl, nun, "%.1f%%" % p if res else "-",
                "%+.1f" % (p - NULL) if res else "-", "%+.1f" % (p - BREAKEVEN) if res else "-",
                float(arr.mean()), float(arr.mean()) - FEE,
                "%.1f" % float(np.median([r["mins"] for r in e if r["mins"] != ""])) if res else "-",
                "%.1f/%.1f" % (float(np.median(dls)), float(np.percentile(dls, 90))) if dls else "-"))
        else:
            md.append("| %s | %d | 0/0/%d | - | - | - | - | - | - | - |" % (s, len(e), nun))
    md += ["", "## 3. 1h excursions (ENTRY-referenced for filled rows; CANCELLED = fire-close"
           " counterfactual), split by route", "",
           "| side | rows | status | med max | p25/p75 max | med min | p25/p75 min |",
           "|---|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        for stt in ("MKT", "TOUCH", "CANCELLED"):
            sub = [r for r in rows if r["side"] == s and r["status"] == stt and r["w_max"] != ""]
            if not sub:
                md.append("| %s | 0 | %s | - | - | - | - |" % (s, stt))
                continue
            mx = np.array([r["w_max"] for r in sub], float)
            mn = np.array([r["w_min"] for r in sub], float)
            md.append("| %s | %d | %s | %+.3f | %+.3f/%+.3f | %+.3f | %+.3f/%+.3f |" % (
                s, len(sub), stt, float(np.median(mx)), float(np.percentile(mx, 25)),
                float(np.percentile(mx, 75)), float(np.median(mn)),
                float(np.percentile(mn, 25)), float(np.percentile(mn, 75))))
    md += ["", "## 4. Leg-5 CONTEXT zone range (bars b-99..b-59 = N 60..100), % from ENTRY price",
           "_The reach of the context window relative to the actual entry: highest high above /"
           " lowest low below the entry price. Split by episode outcome to see whether a bigger"
           " prior swing precedes a better result._", "",
           "| side | outcome | n | med zone HIGH % | med zone LOW % | med zone RANGE % |",
           "|---|---|---|---|---|---|"]
    for s in ("long", "short"):
        for grp, pred in (("TP", lambda r: r["outcome"] == "TP"),
                          ("SL", lambda r: str(r["outcome"]).startswith("SL")),
                          ("all filled", lambda r: r["status"] in ("MKT", "TOUCH"))):
            sub = [r for r in rows if r["side"] == s and r["status"] in ("MKT", "TOUCH") and pred(r)]
            if not sub:
                md.append("| %s | %s | 0 | - | - | - |" % (s, grp))
                continue
            zh = np.array([r["zone_hi_pct"] for r in sub], float)
            zl = np.array([r["zone_lo_pct"] for r in sub], float)
            md.append("| %s | %s | %d | %+.3f | %+.3f | %.3f |" % (
                s, grp, len(sub), float(np.median(zh)), float(np.median(zl)),
                float(np.median(zh - zl))))
    md += ["", "## Honest flags",
           "- Leg 2 stays the registered share form; leg 5 EXISTS is the loosest context in the"
           " program — the fire set is broad by design.",
           "- Touch entries fill on a bar-low touch of the moving line (no slippage); taker fees"
           " are the honesty floor.",
           "- Spent tape; the blackout makes episodes disjoint, trades may overlap beyond the hour.",
           "", "## HARD STOP", "Judged once; forward snapshots are the judge."]
    with open(os.path.join(OUT, "analysis_report_S5j.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("[%3.0fs] %d episodes (%d blacked out) | report + CSV written"
          % (time.time() - t0, len(rows), n_blk), flush=True)


if __name__ == "__main__":
    main()
