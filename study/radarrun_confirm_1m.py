"""RADAR RUNNER 1m-CONFIRM — 30m bucket fire gated on a same-side 1m-clock fire INSIDE the bucket.

PRE-REGISTERED (user 2026-09-04), TWO readings of "the 1m clock also fired inside the 30m bucket":
  A) CONFIRMATION (primary): during the parent bucket's own time span, a same-side 1m-clock union
     fire occurred (its badge bar closes at/before the bucket end -> known at the parent's close;
     causal). Trade = the PARENT badge: entry = parent close, SL = parent badge SL. Exits:
     0.2%/0.4% gross fix + RR 1/1.5/2 on the parent risk. SCREEN ONLY: 8 random days 2025 +
     8 random days 2026 (seed 20260904, days drawn from days that have parent fires; 2026 spans
     Jan-Jun, the archive end). n is SMALL -> noise band ~±0.15%/trade; only a whale edge counts,
     and any candidate must re-run on the FULL data before being believed (agreed protocol).
  B) PULLBACK WITH PARENT SL (bonus, FULL 18mo — free from the caches): yesterday's corridor
     entry (first same-side 1m fire inside [low,high] after the parent close) but SL = the PARENT
     badge SL instead of the 1m badge SL. Post-processing of rr_pullback_trades.json (1m-CLOCK
     child) and rr_pullback_bkt_cor_30mBKT.json (1m-BUCKET child). 30m bucket parent only.
Fees/resolution/taken()/eras: identical to study/radarrun_pullback_1m.py (canonical gates).
python study/radarrun_confirm_1m.py"""
import os, sys, json, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import (_f, resolve, report_cell, CACHE_30, W1, SLBUF, EXITS, OUT)
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ, cor_cache, select_trades

SEED = int(os.environ.get("RR_SEED", "20260904"))   # override for fresh replication draws
N_DAYS = int(os.environ.get("RR_NDAYS", "8"))       # sampled days per year
NY_ONLY = bool(os.environ.get("RR_NY"))             # keep only fires in the NY session 13-21 UTC
CONF_CAP = 600                    # max 1m closes replayed per parent bucket (10h; buckets are ~30m)
CACHE_TR1 = os.path.join(OUT, "rr_pullback_trades.json")


def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    from app import config, radar_breakout_detect as RB
    t0 = time.time()
    print("RADAR RUNNER 1m-CONFIRM — A) sampled confirmation screen (8+8 days) | "
          "B) full-data pullback with parent SL\n", flush=True)

    parent_kind = os.environ.get("RR_PARENT", "30mbkt")   # "30mbkt" | "15mclk"
    if parent_kind == "15mclk":
        from study.radarrun_pullback_1m import fires_15m_clock as _f15c
        f30 = _f15c()                              # (b, et, side, entry, sl) — 15m CLOCK union
        print("parent = 15m CLOCK union (%d badges)" % len(f30), flush=True)
    else:
        f30 = json.load(open(CACHE_30))           # (b, et, side, entry, sl)
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]

    # ── day sampling (seeded, pre-registered) ──────────────────────────────────────────────
    def _eligible(d):
        if not NY_ONLY:
            return True                            # crypto trades 7d; only the NY cut is weekday-only
        return datetime.strptime(d, "%Y-%m-%d").weekday() < 5   # a weekend "NY session" isn't one
    days_by_year = {2025: sorted({d for f in f30 if (d := day_of(f[1]))[:4] == "2025" and _eligible(d)}),
                    2026: sorted({d for f in f30 if (d := day_of(f[1]))[:4] == "2026" and _eligible(d)})}
    rng = random.Random(SEED)
    if os.environ.get("RR_WEEKS"):
        # one session per DISTINCT ISO week (user 2026-09-04): guarantees non-consecutive sessions
        # (adjacent weeks' weekdays are >= 3 days apart) and spreads regimes across the year
        sample_days = []
        for yr in (2025, 2026):
            by_week = {}
            for d in days_by_year[yr]:
                by_week.setdefault(datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2], []).append(d)
            weeks = rng.sample(sorted(by_week), min(N_DAYS, len(by_week)))
            sample_days += [rng.choice(by_week[w]) for w in weeks]
        sample_days = sorted(sample_days)
    else:
        sample_days = sorted(rng.sample(days_by_year[2025], N_DAYS) + rng.sample(days_by_year[2026], N_DAYS))
    print("sampled days: %s" % ", ".join(sample_days), flush=True)
    sel = [f for f in f30 if day_of(f[1]) in set(sample_days)]
    if NY_ONLY:                                     # NY session = 13:00-21:00 UTC (project standard)
        sel = [f for f in sel if 13 * 3600 <= (f[1] % 86400) < 21 * 3600]
        print("NY-session filter ON (13-21 UTC)", flush=True)
    print("parents on sampled days: %d (of %d)\n" % (len(sel), len(f30)), flush=True)

    # ── A) confirmation replay inside each parent bar's time span ──────────────────────────
    if parent_kind == "15mclk":
        starts = {int(b_): et - 900.0 for (b_, et, s, e, sl) in sel}   # clock bar: span is exact
    else:
        A30 = sorted(load_archive("30m", root="study/recon_archive")[1],
                     key=lambda b: _f(b.get("start_time", 0)))
        starts = {int(b_): _f(A30[b_].get("start_time")) for (b_, et, s, e, sl) in sel}
        del A30
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1],
                key=lambda b: _f(b.get("start_time", 0)))
    print("1m clock dicts loaded (%.0fs)" % (time.time() - t0), flush=True)

    # 1m EMA20 (causal, continuous over the whole clock series) — gates the CONFIRMING candle when
    # RR_C1EMA is set: long confirm must CLOSE above its own EMA20, short below (user 2026-09-04).
    ema20 = np.empty(len(C1))
    ema20[0] = C1[0]
    a20 = 2.0 / 21.0
    for _i in range(1, len(C1)):
        ema20[_i] = a20 * C1[_i] + (1.0 - a20) * ema20[_i - 1]

    # 1m E/E/C zone (RR_C1ZONE): the pivot-study band convention (study/pivot_eec_15m.py) on the
    # 1m clock — legs = EMA20/50 cross segments; band live at a bar = last COMPLETED bull leg HIGH
    # / bear leg LOW before that bar's leg; thirds -> cheap/equilib/expensive (+beyond, inverted).
    # Codes: -1 unknown, 0 beyond-dn, 1 cheap, 2 equilib, 3 expensive, 4 beyond-up, 5 inverted.
    zone1m = None
    if os.environ.get("RR_C1ZONE"):
        a50 = 2.0 / 51.0
        e50a = np.empty(len(C1))
        e50a[0] = C1[0]
        for _i in range(1, len(C1)):
            e50a[_i] = a50 * C1[_i] + (1.0 - a50) * e50a[_i - 1]
        zone1m = np.full(len(C1), -1, dtype=np.int8)
        legs = []
        cur = st_ = None
        for _i in range(60, len(C1)):
            d = 1 if ema20[_i] > e50a[_i] else (-1 if ema20[_i] < e50a[_i] else 0)
            if d == 0:
                continue
            if cur is None:
                cur, st_ = d, _i
            elif d != cur:
                legs.append((cur, st_, _i - 1))
                cur, st_ = d, _i
        if cur is not None:
            legs.append((cur, st_, len(C1) - 1))    # open leg: its band is complete, its extreme is not
        lb_hi = lb_lo = None
        for (d, a_, b2) in legs:
            if lb_hi is not None and lb_lo is not None:
                seg = C1[a_:b2 + 1]
                if lb_hi <= lb_lo:
                    zone1m[a_:b2 + 1] = 5
                else:
                    t = (seg - lb_lo) / (lb_hi - lb_lo)
                    zone1m[a_:b2 + 1] = np.where(seg < lb_lo, 0, np.where(seg > lb_hi, 4,
                                                 np.where(t < 1 / 3, 1, np.where(t < 2 / 3, 2, 3))))
            ext = H1[a_:b2 + 1].max() if d > 0 else L1[a_:b2 + 1].min()
            if d > 0:
                lb_hi = ext
            else:
                lb_lo = ext

    trades_A = []
    n_conf = 0
    detects = 0
    for pi, (b_, et, s, e, sl) in enumerate(sel):
        st = starts[int(b_)]
        j0 = int(np.searchsorted(T1S, st - 0.5))
        confirmed = False
        conf_sl = None                             # the FIRST confirming 1m badge's own sl_trade
        conf_ema = None                            # that badge's close-vs-EMA20 side agreement
        seen = set()
        for j in range(j0, min(len(T1S), j0 + CONF_CAP)):
            if T1S[j] + 60.0 > et + 1e-6:          # 1m bar must CLOSE by the parent's close (causal)
                break
            lo = max(0, j - W1)
            hits = []
            for g in RB.detect(A1[lo:j + 1], skip_last=False, sl_buf=SLBUF,
                               tp_frac=config.RR_TP_FRAC):
                bb = lo + int(g["i"])
                key = (bb, g["side"])
                if key in seen or bb < j0 or bb > j:
                    continue                       # union: first appearance only, badge inside span
                seen.add(key)
                if g["side"] == s:
                    hits.append((bb, float(g["sl_trade"])))
            detects += 1
            if hits and not confirmed:
                confirmed = True
                bb0, conf_sl = min(hits)           # earliest confirming badge at this appearance
                conf_ema = bool((s > 0 and C1[bb0] > ema20[bb0])
                                or (s < 0 and C1[bb0] < ema20[bb0]))
                conf_zone = int(zone1m[bb0]) if zone1m is not None else -1
                break
        else:
            conf_zone = -1
        if not confirmed:
            conf_zone = -1
        n_conf += int(confirmed)
        trades_A.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), conf=confirmed,
                             csl=conf_sl, cema=conf_ema, cz=conf_zone))
        if pi % 40 == 0:
            print("  parent %d/%d (detects %d, %.0fs)" % (pi, len(sel), detects, time.time() - t0),
                  flush=True)
    del A1
    print("confirmed: %d/%d parents (%.0f%%)\n" % (n_conf, len(sel), 100 * n_conf / max(1, len(sel))),
          flush=True)

    print("=" * 132, flush=True)
    print("A) CONFIRMATION SCREEN — 30m bucket badge bracket (parent entry+SL), SAMPLED %d days "
          "(SEED %d) — noise band ~±0.15%%/trade at this n" % (2 * N_DAYS, SEED), flush=True)
    groups = [("ALL", lambda x: True),
              ("CONFIRMED", lambda x: x["conf"]),
              ("UNCONF", lambda x: not x["conf"])]
    if os.environ.get("RR_C1EMA"):                 # split the confirmed set by the 1m EMA20 side gate
        groups[2:2] = [("C+EMA-OK", lambda x: x["conf"] and x["cema"] is True),
                       ("C+EMA-NO", lambda x: x["conf"] and x["cema"] is False)]
    if os.environ.get("RR_C1ZONE"):                # E/E/C zone of the CONFIRMING candle (user hypothesis:
        _zn = {0: "bey-dn", 1: "cheap", 2: "equilib", 3: "expensive", 4: "bey-up", 5: "invert", -1: "n/a"}
        from collections import Counter
        _zc = Counter(_zn[x["cz"]] for x in trades_A if x["conf"])
        print("confirm zones: %s" % dict(_zc), flush=True)      # short-in-EXPENSIVE / long-in-CHEAP better)
        groups[2:2] = [
            ("C+Z-HYP", lambda x: x["conf"] and ((x["s"] < 0 and x["cz"] == 3)
                                                 or (x["s"] > 0 and x["cz"] == 1))),
            ("C+Z-HYPw", lambda x: x["conf"] and ((x["s"] < 0 and x["cz"] in (3, 4))
                                                  or (x["s"] > 0 and x["cz"] in (0, 1)))),
            ("C+Z-ANTI", lambda x: x["conf"] and ((x["s"] > 0 and x["cz"] in (3, 4))
                                                  or (x["s"] < 0 and x["cz"] in (0, 1))))]
    if os.environ.get("RR_SPLIT1H"):               # does AVOIDING the NY first hour (13-14 UTC) add?
        _h1 = lambda x: 13 * 3600 <= (x["t"] % 86400) < 14 * 3600
        groups[2:2] = [("C-SKIP1H", lambda x: x["conf"] and not _h1(x)),
                       ("C-1STH", lambda x: x["conf"] and _h1(x)),
                       ("A-SKIP1H", lambda x: not _h1(x))]
    for sub_tag, selr in groups:
        subset = [x for x in trades_A if selr(x)]
        for ename, kind, val in EXITS:
            report_cell("A %s" % sub_tag, ename, subset, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)

    # A2 (user 2026-09-04): same CONFIRMED trades, SL moved to the confirming 1m badge's own stop
    # ("like the terminal's 1m badge"). Entry/time unchanged (parent close); degenerate stops
    # (child SL on the wrong side of the parent entry) skipped + counted.
    a2 = []
    dropped = 0
    for x in trades_A:
        if not x["conf"] or x["csl"] is None:
            continue
        if (x["s"] > 0 and x["csl"] >= x["e"]) or (x["s"] < 0 and x["csl"] <= x["e"]):
            dropped += 1
            continue
        a2.append(dict(t=x["t"], s=x["s"], e=x["e"], sl=x["csl"]))
    print("A2 CONFIRMED with the 1m badge SL (dropped %d degenerate stops):" % dropped, flush=True)
    for ename, kind, val in EXITS:
        report_cell("A2 1mSL", ename, a2, T1S, H1, L1, C1, kind, val, mc, day_blocks)
    print("-" * 132, flush=True)

    # ── B) full-data pullback entries, SL swapped to the parent badge SL ───────────────────
    if os.environ.get("RR_SKIP_B"):
        print("done in %.0fs" % (time.time() - t0), flush=True)
        return
    psl = {round(f[1], 2): (f[3], f[4]) for f in f30}          # parent et -> (entry, sl)
    print("\nB) PULLBACK ENTRY + PARENT SL — FULL 18mo, 30m bucket parent (from caches)", flush=True)
    variants = []
    if os.path.exists(CACHE_TR1):
        tr = json.load(open(CACHE_TR1))["30mBKT"]["trades"]
        variants.append(("B clk-child", tr))
    if os.path.exists(cor_cache("30mBKT")):
        cors = json.load(open(cor_cache("30mBKT")))
        variants.append(("B bkt-child", select_trades(cors)[0]))
    for tag, trs in variants:
        swapped = []
        for x in trs:
            pe_sl = psl.get(round(x["pt"], 2))
            if pe_sl is None:
                continue
            sl_p = pe_sl[1]
            if (x["s"] > 0 and sl_p >= x["e"]) or (x["s"] < 0 and sl_p <= x["e"]):
                continue                            # degenerate: child entry beyond the parent stop
            swapped.append(dict(t=x["t"], s=x["s"], e=x["e"], sl=sl_p))
        for ename, kind, val in EXITS:
            report_cell(tag, ename, swapped, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
