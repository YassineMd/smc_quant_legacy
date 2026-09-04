"""RADAR RUNNER 1m-BUCKET-CONFIRM screen — the confirmation test with a 1m BUCKET child.

PRE-REGISTERED (user 2026-09-04): identical to study/radarrun_confirm_1m.py section A except:
  * child = 1m BUCKET union fires (study/recon_archive/1m, trailing W=2000 buckets, first
    appearance) whose badge bar closes inside the parent 30m bucket's time span (causal at the
    parent's close);
  * SL = the PARENT 30m badge SL (the screen's winning bracket);
  * FRESH 8+8 random days, NEW seed 20260905 — an independent draw, so it doubles as a soft
    replication of the clock-child screen (which hit +0.19..+0.27%/trade on RR exits).
Entry = parent close; exits 0.2%/0.4% fix + RR 1/1.5/2; resolution 1m clock first-touch,
ties against; fees canonical; non-overlap taken(); eras separate. SCREEN ONLY (n small).
python study/radarrun_confirm_1mbkt.py"""
import os, sys, json, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, CACHE_30, W1, SLBUF, EXITS
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ

SEED = 20260905
N_DAYS = 8
CONF_CAP = 600


def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    from app import config, radar_breakout_detect as RB
    t0 = time.time()
    print("RADAR RUNNER 1m-BUCKET-CONFIRM screen — fresh 8+8 days (SEED %d), parent SL\n" % SEED,
          flush=True)

    f30 = json.load(open(CACHE_30))
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]

    days_by_year = {2025: sorted({day_of(f[1]) for f in f30 if day_of(f[1])[:4] == "2025"}),
                    2026: sorted({day_of(f[1]) for f in f30 if day_of(f[1])[:4] == "2026"})}
    rng = random.Random(SEED)
    sample_days = sorted(rng.sample(days_by_year[2025], N_DAYS) + rng.sample(days_by_year[2026], N_DAYS))
    print("sampled days: %s" % ", ".join(sample_days), flush=True)
    sel = [f for f in f30 if day_of(f[1]) in set(sample_days)]
    print("parents on sampled days: %d (of %d)\n" % (len(sel), len(f30)), flush=True)

    A30 = sorted(load_archive("30m", root="study/recon_archive")[1],
                 key=lambda b: _f(b.get("start_time", 0)))
    starts = {int(b_): _f(A30[b_].get("start_time")) for (b_, et, s, e, sl) in sel}
    del A30

    AB = sorted(load_archive("1m", root="study/recon_archive")[1],
                key=lambda b: _f(b.get("start_time", 0)))
    TBE = np.array([_f(b.get("end_time")) or (_f(b.get("start_time")) + 60.0) for b in AB])
    print("1m BUCKET bars: %d (%.0fs)" % (len(AB), time.time() - t0), flush=True)

    trades = []
    n_conf = 0
    detects = 0
    for pi, (b_, et, s, e, sl) in enumerate(sel):
        st = starts[int(b_)]
        k0 = int(np.searchsorted(TBE, st, side="right"))
        confirmed = False
        seen = set()
        for k in range(k0, min(len(TBE), k0 + CONF_CAP)):
            if TBE[k] > et + 1e-6:                 # child badge must CLOSE by the parent's close
                break
            lo = max(0, k - W1)
            for g in RB.detect(AB[lo:k + 1], skip_last=False, sl_buf=SLBUF,
                               tp_frac=config.RR_TP_FRAC):
                bb = lo + int(g["i"])
                key = (bb, g["side"])
                if key in seen or bb < k0 or bb > k:
                    continue                       # union: first appearance, badge inside the span
                seen.add(key)
                if g["side"] == s:
                    confirmed = True
            detects += 1
            if confirmed:
                break
        n_conf += int(confirmed)
        trades.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), conf=confirmed))
        if pi % 40 == 0:
            print("  parent %d/%d (detects %d, %.0fs)" % (pi, len(sel), detects, time.time() - t0),
                  flush=True)
    del AB
    print("confirmed: %d/%d parents (%.0f%%)\n" % (n_conf, len(trades), 100 * n_conf / max(1, len(trades))),
          flush=True)

    print("=" * 132, flush=True)
    print("1m-BUCKET-CONFIRM — parent badge bracket, SAMPLED %d days (SEED %d) — SCREEN ONLY"
          % (2 * N_DAYS, SEED), flush=True)
    for sub_tag, selr in (("ALL", lambda x: True),
                          ("CONFIRMED", lambda x: x["conf"]),
                          ("UNCONF", lambda x: not x["conf"])):
        subset = [x for x in trades if selr(x)]
        for ename, kind, val in EXITS:
            report_cell("bk %s" % sub_tag, ename, subset, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
