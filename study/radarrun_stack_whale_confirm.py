"""STACK: opposing-whale-absorbed x 1m-clock confirm + E/E/C zone (C+Z-HYP), 30m bucket, weekday NY.

User 2026-09-04: "big player opp filter + 1m clock confirmation z-hyp on the 30m bucket, NY weekdays".
Both parts died individually at the daemon-OOS gate; this tests their INTERSECTION.

INPUTS (all cached, no detection needed):
  * recon weekday-NY parents with conf/cz per parent: study/out/rr_confirm_full_trades.json (382 sessions)
  * daemon-OOS weekday-NY parents with conf/cz:        study/out/rr_confirm_daemon_oos_trades.json (Jul-Aug)
  * parent bucket spans: canonical 30m union (recon) / daemon 30m union (rebuilt, then cached)
  * whale prints >= $500K: study/bigprint_archive via app.bigprint_store
CELLS (parent entry + parent SL, canonical exits/resolution/taken()):
  ALL / CONFIRMED / C+Z-HYP / WHALE-OPP (all opposing >= $500K prints run over by the close) /
  STACK = C+Z-HYP AND WHALE-OPP (the ask) / STACK-anyopp = C+Z-HYP AND any opposing whale present.
REPORT: FULL data per period (RECON 2025 / RECON 2026H1 / DAEMON OOS Jul-Aug) — the primary answer —
then the user's literal 10+10+10 random-session draw (seed 20260923) as noise-band context.
PREDICTION ON RECORD: ~nothing; OOS stack n in the single digits.
python study/radarrun_stack_whale_confirm.py"""
import os, sys, json, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, EXITS, CACHE_30, OUT
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ
from study.radarrun_confirm_daemon_oos import build_parent_union, OOS_ROOT, JUL1, SEP1
from app import bigprint_store

BP_USD = 500_000.0
bigprint_store.CACHE_FLOOR_USD = BP_USD
SEED = 20260923
N_PER = 10
CACHE_D30 = os.path.join(OUT, "rr_union_b30m_daemon_oos.json")
SPLIT = 1767225600.0                                 # 2026-01-01


def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def whale_flags(trades, span_by_et):
    for x in trades:
        st_et = span_by_et.get(round(x["t"], 2))
        x["opp"] = x["opp_all"] = False
        if st_et is None:
            continue
        st, et = st_et
        prints = bigprint_store.load_prints(st, et, BP_USD)
        opp_side = 0 if x["s"] > 0 else 1
        opp = [p[1] for p in prints if p[3] == opp_side]
        if opp:
            x["opp"] = True
            x["opp_all"] = (x["e"] > max(opp)) if x["s"] > 0 else (x["e"] < min(opp))


CELLS = (("ALL", lambda x: True),
         ("CONFIRMED", lambda x: x["conf"]),
         ("C+Z-HYP", lambda x: x["conf"] and ((x["s"] < 0 and x["cz"] == 3) or (x["s"] > 0 and x["cz"] == 1))),
         ("WHALE-OPP", lambda x: x["opp_all"]),
         ("STACK", lambda x: x["opp_all"] and x["conf"] and ((x["s"] < 0 and x["cz"] == 3) or (x["s"] > 0 and x["cz"] == 1))),
         ("STACK-anyopp", lambda x: x["opp"] and x["conf"] and ((x["s"] < 0 and x["cz"] == 3) or (x["s"] > 0 and x["cz"] == 1))))


def report(title, trades, arrs, mc, day_blocks):
    T1S, H1, L1, C1 = arrs
    print("=" * 132, flush=True)
    print(title, flush=True)
    for name, sel in CELLS:
        sub = [x for x in trades if sel(x)]
        for ename, kind, val in EXITS:
            report_cell(name, ename, sub, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    t0 = time.time()
    print("STACK: whale-opp x 1m-confirm+zone | 30m bucket | weekday NY | FULL data + 10+10+10 sampled\n", flush=True)

    # ---- recon
    tr = json.load(open(os.path.join(OUT, "rr_confirm_full_trades.json")))
    f30 = json.load(open(CACHE_30))
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    span = {round(et, 2): (_f(A30[b].get("start_time")), et) for (b, et, s, e, sl) in f30}
    del A30
    whale_flags(tr, span)
    z = np.load(CLOCK_NPZ)
    arr_r = (z["t"], z["h"], z["l"], z["c"])
    tr25 = [x for x in tr if x["t"] < SPLIT]; tr26 = [x for x in tr if x["t"] >= SPLIT]
    print("recon: %d parents (2025 %d / 2026H1 %d); whale-opp-all %d; conf %d; C+Z-HYP %d; STACK %d" % (
        len(tr), len(tr25), len(tr26), sum(x["opp_all"] for x in tr), sum(x["conf"] for x in tr),
        sum(1 for x in tr if CELLS[2][1](x)), sum(1 for x in tr if CELLS[4][1](x))), flush=True)

    # ---- daemon OOS
    trd = json.load(open(os.path.join(OUT, "rr_confirm_daemon_oos_trades.json")))
    if os.path.exists(CACHE_D30):
        fd = json.load(open(CACHE_D30))
        A30d = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
        A30d = [b for b in A30d if _f(b.get("start_time")) < SEP1]
    else:
        A30d = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
        A30d = [b for b in A30d if _f(b.get("start_time")) < SEP1]
        fd = build_parent_union(A30d, JUL1)
        json.dump(fd, open(CACHE_D30, "w"))
    span_d = {round(et, 2): (_f(A30d[b].get("start_time")), et) for (b, et, s, e, sl) in fd}
    del A30d
    whale_flags(trd, span_d)
    A1 = sorted(load_archive("1m", root=OOS_ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    A1 = [b for b in A1 if JUL1 <= _f(b.get("start_time")) < SEP1]
    arr_d = (np.array([_f(b.get("start_time")) for b in A1]), np.array([_f(b.get("high")) for b in A1]),
             np.array([_f(b.get("low")) for b in A1]), np.array([_f(b.get("close", b.get("close_price"))) for b in A1]))
    del A1
    print("daemon OOS: %d parents; whale-opp-all %d; conf %d; C+Z-HYP %d; STACK %d  (%.0fs)\n" % (
        len(trd), sum(x["opp_all"] for x in trd), sum(x["conf"] for x in trd),
        sum(1 for x in trd if CELLS[2][1](x)), sum(1 for x in trd if CELLS[4][1](x)), time.time() - t0), flush=True)

    # ---- FULL reports (primary)
    report("FULL RECON 2025 — weekday NY (%d parents)" % len(tr25), tr25, arr_r, mc, day_blocks)
    report("FULL RECON 2026H1 — weekday NY (%d parents)" % len(tr26), tr26, arr_r, mc, day_blocks)
    report("FULL DAEMON OOS Jul-Aug 2026 — weekday NY (%d parents, virgin)" % len(trd), trd, arr_d, mc, day_blocks)

    # ---- the literal ask: 10 random sessions per period
    rng = random.Random(SEED)
    for title, pool, arrs in (("SAMPLED 10 sessions 2025", tr25, arr_r), ("SAMPLED 10 sessions 2026H1", tr26, arr_r),
                              ("SAMPLED 10 sessions DAEMON", trd, arr_d)):
        days = sorted({day_of(x["t"]) for x in pool})
        pick = set(rng.sample(days, min(N_PER, len(days))))
        sub = [x for x in pool if day_of(x["t"]) in pick]
        report("%s (%s) — %d parents" % (title, ", ".join(sorted(pick)), len(sub)), sub, arrs, mc, day_blocks)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
