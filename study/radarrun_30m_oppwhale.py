"""RADAR RUNNER 30m BUCKET x OPPOSING WHALE ABSORBED — full recon + daemon OOS in one pass.

RULE (user 2026-09-04, pre-registered): take a 30m-bucket Radar Runner badge only if its signal
candle contains an OPPOSING >= $500K print (long: a taker-SELL whale; short: a taker-BUY whale)
AND the badge CLOSE is BEYOND it (long: close > the whale's price; short: close < it) — the break
ran over the whale. HYP requires close beyond EVERY opposing whale in the candle (all absorbed);
also reported: beyond at least one, opposing whale HELD (present, not run over), SAME-side whale,
NO whale. Parent entry + parent badge SL; exits 0.2/0.4 fix + RR 1/1.5/2; 1m first-touch
ties-against; canonical fees; non-overlap taken().

DATA: no detection needed -> FULL 18 months (canonical cached 30m union, Jan-2025..2026-06-19) AND
the DAEMON OOS (Jul-Aug 2026: daemon 30m union built exactly as in radarrun_confirm_daemon_oos,
resolved on the reconstructed OOS 1m clock). Whale prints from study/bigprint_archive.
PREDICTION ON RECORD: absorbed-only died at OOS; 0/6 families survive that gate — expect no OOS edge.
python study/radarrun_30m_oppwhale.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, EXITS, CACHE_30
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ
from study.radarrun_confirm_daemon_oos import build_parent_union, OOS_ROOT, JUL1, SEP1
from app import bigprint_store

BP_USD = 500_000.0
bigprint_store.CACHE_FLOOR_USD = BP_USD           # cache only whale rows (RAM)


def classify(fires, starts, tag):
    """(trades, counts) — per badge: opposing/same whales inside the candle span and the beyond tests."""
    trades = []
    cnt = {"opp_all_beyond": 0, "opp_any_beyond": 0, "opp_held": 0, "same": 0, "none": 0}
    for (b, et, s, e, sl) in fires:
        st = starts[int(b)]
        prints = bigprint_store.load_prints(st, et, BP_USD)
        opp_side = 0 if s > 0 else 1
        opp = [p[1] for p in prints if p[3] == opp_side]
        same = any(p[3] != opp_side for p in prints)
        if opp:
            all_b = (e > max(opp)) if s > 0 else (e < min(opp))
            any_b = (e > min(opp)) if s > 0 else (e < max(opp))
        else:
            all_b = any_b = False
        if opp and all_b:
            cnt["opp_all_beyond"] += 1
        elif opp and any_b:
            cnt["opp_any_beyond"] += 1
        elif opp:
            cnt["opp_held"] += 1
        elif same:
            cnt["same"] += 1
        else:
            cnt["none"] += 1
        trades.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), opp=bool(opp), all_b=all_b,
                           any_b=any_b, same=same))
    print("[%s] %d badges: %s" % (tag, len(trades), cnt), flush=True)
    return trades


def report(tag, trades, T1S, H1, L1, C1, mc, day_blocks):
    print("=" * 132, flush=True)
    print(tag, flush=True)
    for name, sel in (("ALL", lambda x: True),
                      ("HYP all-beyond", lambda x: x["opp"] and x["all_b"]),
                      ("OPP any-beyond", lambda x: x["opp"] and x["any_b"]),
                      ("OPP-HELD", lambda x: x["opp"] and not x["all_b"]),
                      ("SAME-whale", lambda x: x["same"] and not x["opp"]),
                      ("NO-whale", lambda x: not x["same"] and not x["opp"])):
        sub = [x for x in trades if sel(x)]
        for ename, kind, val in EXITS:
            report_cell(name, ename, sub, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    t0 = time.time()
    print("RR 30m BUCKET x OPPOSING WHALE ABSORBED (>= $%.0fK, close beyond) | FULL RECON + DAEMON OOS\n"
          % (BP_USD / 1e3), flush=True)

    # ---- RECON, full 18 months: canonical cached union parents
    f30 = json.load(open(CACHE_30))
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    starts = {int(b): _f(A30[b].get("start_time")) for (b, et, s, e, sl) in f30}
    del A30
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]
    tr_recon = classify(f30, starts, "RECON 2025-01..2026-06")
    report("RECON FULL (Jan-2025 .. 2026-06-19) — 30m bucket parents, parent bracket, 24h", tr_recon,
           T1S, H1, L1, C1, mc, day_blocks)
    print("recon done %.0fs\n" % (time.time() - t0), flush=True)

    # ---- DAEMON OOS, Jul-Aug 2026 (virgin)
    A30d = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    A30d = [b for b in A30d if _f(b.get("start_time")) < SEP1]
    fires_d = build_parent_union(A30d, JUL1)
    starts_d = {int(b): _f(A30d[b].get("start_time")) for (b, et, s, e, sl) in fires_d}
    del A30d
    A1 = sorted(load_archive("1m", root=OOS_ROOT, drop_degenerate=False)[1],
                key=lambda b: _f(b.get("start_time", 0)))
    A1 = [b for b in A1 if JUL1 <= _f(b.get("start_time")) < SEP1]
    T1d = np.array([_f(b.get("start_time")) for b in A1]); H1d = np.array([_f(b.get("high")) for b in A1])
    L1d = np.array([_f(b.get("low")) for b in A1]); C1d = np.array([_f(b.get("close", b.get("close_price"))) for b in A1])
    del A1
    tr_oos = classify(fires_d, starts_d, "DAEMON OOS 2026-07..08")
    report("DAEMON OOS (Jul-Aug 2026, virgin) — 30m daemon-bucket parents, parent bracket, 24h", tr_oos,
           T1d, H1d, L1d, C1d, mc, day_blocks)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
