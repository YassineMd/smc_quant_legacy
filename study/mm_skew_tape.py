"""Approximate SPEED OF TAPE from 1m constant-volume buckets and test if it adds a W/L edge to MMXSKEW.
True tape speed = prints/sec (NOT stored in buckets). Proxy = volume/sec = curr_vol/duration, computed on the
finer 1m buckets, split combined / bulls (buy_vol/dur) / bears (sell_vol/dur). For each 1h MMXSKEW signal we
take the 1m buckets inside its window and use the LAST 1m tape speed (most recent urgency) and the MEAN.
Tests W/L separation per side on the v1.1 signals (RR 1.5). CAUSAL: only 1m buckets ending <= the 1h close.
Run:  python study/mm_skew_tape.py
"""
from __future__ import annotations
import os, sys, statistics
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive
import study.mm_skew_poc as P
import study.mm_skew_rr_sweep as RR
import study.mm_skew_winloss as WL


def load_1m():
    _, raws, _ = load_archive("1m")
    et = []; comb = []; bull = []; bear = []
    for r in raws:
        st = r.get("start_time"); en = r.get("end_time")
        if st is None or en is None:
            continue
        dur = max(0.5, en - st)
        bv = float(r.get("buy_vol", 0)); sv = float(r.get("sell_vol", 0))
        cv = float(r.get("curr_vol", 0)) or (bv + sv)
        et.append(en); comb.append(cv / dur); bull.append(bv / dur); bear.append(sv / dur)
    o = np.argsort(et)
    return (np.array(et)[o], np.array(comb)[o], np.array(bull)[o], np.array(bear)[o])


def main():
    M, span = P.build()
    for i in range(len(M)):
        b = M[i]; cv = float(b.get("curr_vol", 0)) or 1.0
        b["delta"] = (float(b.get("buy_vol", 0)) - float(b.get("sell_vol", 0))) / cv * 100
    et, comb, bull, bear = load_1m()
    print(f"1m buckets: {len(et)}   median 1m tape (vol/s): {np.median(comb):.1f}   1h signals below.\n")

    def sigf(b):
        s = P.sig(b); return 0 if (s == 1 and b["delta"] >= 15) else s

    rows = []
    for i in range(len(M) - 1):
        s = sigf(M[i])
        if s == 0:
            continue
        res = RR.simulate_rr(M, i, s, 1.5, "sl")
        if res is None:
            continue
        t0 = M[i].get("start_time"); t1 = M[i].get("end_time")
        lo = np.searchsorted(et, t0, "left"); hi = np.searchsorted(et, t1, "right")
        if hi <= lo:
            continue
        seg_c, seg_bu, seg_be = comb[lo:hi], bull[lo:hi], bear[lo:hi]
        rows.append(dict(side=s, win=(res[0] == "TP"),
                         comb_last=seg_c[-1], comb_mean=seg_c.mean(),
                         bull_last=seg_bu[-1], bull_mean=seg_bu.mean(),
                         bear_last=seg_be[-1], bear_mean=seg_be.mean(),
                         align_last=(seg_bu[-1] if s > 0 else seg_be[-1]),      # with-trade-direction tape
                         net_last=(seg_bu[-1] - seg_be[-1]) * s))               # net tape aggression in dir
    print(f"MMXSKEW v1.1 signals with 1m coverage: {len(rows)}  win={100*np.mean([r['win'] for r in rows]):.1f}%\n")
    FEATS = [("comb_last", "combined tape (last 1m)"), ("comb_mean", "combined tape (mean)"),
             ("bull_last", "BULLS tape (last 1m)"), ("bear_last", "BEARS tape (last 1m)"),
             ("align_last", "with-direction tape (last)"), ("net_last", "net tape aggression·side")]
    for side, sn in ((+1, "LONG"), (-1, "SHORT"), (0, "BOTH")):
        ss = rows if side == 0 else [r for r in rows if r["side"] == side]
        W = [r for r in ss if r["win"]]; L = [r for r in ss if not r["win"]]
        print(f"  {sn} (n={len(ss)}, {len(W)}W/{len(L)}L)  feature: WIN med / LOSE med (MW p)")
        for f, lab in FEATS:
            wv = [r[f] for r in W]; lv = [r[f] for r in L]
            p, _ = WL.mann_whitney(wv, lv)
            print(f"    {lab:28s}: {statistics.median(wv):8.1f} / {statistics.median(lv):8.1f}  p={p:.3f}{'*' if p<0.05 else ''}")
        print()


if __name__ == "__main__":
    main()
