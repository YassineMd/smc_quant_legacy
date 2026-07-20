"""MM×Skew v1.1  x  ORB (opening-range breakout, 30 min after NY open) — 1 trade/day.
NY open 9:30 ET; data is EDT (UTC-4) -> opening range = 13:30-14:00 UTC. Entries after 14:00 UTC.
Opening range = high/low of constant-volume buckets in that 30-min window; 'the open' = open of the first
OR-window bucket. Each ET day: take the FIRST post-open bucket that is a v1.1 MMxSkew signal (optionally
also an ORB breakout: long close>OR_high / short close<OR_low). Flat-to-flat (skip a day if still in a trade).
SL modes: FROZEN (0.1% beyond the signal candle extreme) vs ORB (0.1% beyond the session OPEN). TP=RR*SL.
Run:  python study/mm_skew_orb.py
"""
from __future__ import annotations
import os, sys, datetime as dt
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import study.mm_skew_poc as P
import study.mm_skew_strategy as S

FEE = 0.0008
SL_BUF = 0.001
OR_LO, OR_HI, POST, SESS_END = 13 * 60 + 30, 14 * 60, 14 * 60, 20 * 60   # UTC min: OR [13:30,14:00); ORB entry
#                                                                          only DURING NY RTH session 14:00-20:00 UTC


def build():
    M, span = P.build()
    for i in range(len(M)):
        b = M[i]; cv = float(b.get("curr_vol", 0)) or 1.0
        b["delta"] = (float(b.get("buy_vol", 0)) - float(b.get("sell_vol", 0))) / cv * 100
        u = dt.datetime.utcfromtimestamp(b.get("start_time", 0))
        et = u - dt.timedelta(hours=4)
        b["etdate"] = et.date(); b["utcmin"] = u.hour * 60 + u.minute
    return M, span


def sigf(b):                       # v1.1
    s = P.sig(b)
    if s == 1 and b["delta"] >= 15:
        return 0
    return s


def daily_entries(M, use_breakout):
    days = defaultdict(list)
    for i in range(len(M)):
        days[M[i]["etdate"]].append(i)
    entries = []; days_with_or = 0
    for d in sorted(days):
        idxs = days[d]
        orw = [i for i in idxs if OR_LO <= M[i]["utcmin"] < OR_HI]
        if not orw:
            continue
        days_with_or += 1
        orh = max(M[i]["h"] for i in orw); orl = min(M[i]["l"] for i in orw); sopen = M[orw[0]]["o"]
        for i in idxs:
            if not (POST <= M[i]["utcmin"] < SESS_END):     # ORB entry only during the NY RTH session
                continue
            s = sigf(M[i])
            if s == 0:
                continue
            if use_breakout and not ((s == 1 and M[i]["c"] > orh) or (s == -1 and M[i]["c"] < orl)):
                continue
            entries.append((i, s, sopen)); break        # first of the day
    return entries, days_with_or


def sim(M, i, side, rr, sl_mode, sopen):
    e = M[i]["c"]
    if sl_mode == "frozen":
        sl = M[i]["l"] * (1 - SL_BUF) if side > 0 else M[i]["h"] * (1 + SL_BUF)
    else:
        sl = sopen * (1 - SL_BUF) if side > 0 else sopen * (1 + SL_BUF)
    sld = (e - sl) if side > 0 else (sl - e)
    if sld <= 0:
        return None
    slf = sld / e; tp = e + rr * sld * side
    for j in range(i + 1, len(M)):
        hi = M[j]["h"]; lo = M[j]["l"]
        if side > 0:
            htp = hi >= tp; hsl = lo <= sl
        else:
            htp = lo <= tp; hsl = hi >= sl
        if htp and hsl:
            return "SL", -slf, j
        if htp:
            return "TP", rr * slf, j
        if hsl:
            return "SL", -slf, j
    return "OPEN", (M[-1]["c"] - e) / e * side, len(M) - 1


def run(M, entries, rr, sl_mode):
    bal = S.BAL0; last_close = -1; n = w = 0; inval = 0; peak = bal; dd = 0.0; rets = []
    for i, side, sopen in entries:
        if i <= last_close:               # flat-to-flat: prior trade still open
            continue
        res = sim(M, i, side, rr, sl_mode, sopen)
        if res is None:
            inval += 1; continue
        o, rf, jc = res; rets.append(rf)
        notl = S.POS_FRAC * bal * S.LEV; bal += notl * rf - notl * FEE
        n += 1; w += (o == "TP"); last_close = jc
        peak = max(peak, bal); dd = max(dd, (peak - bal) / peak if peak > 0 else 0.0)
    ret = (bal / S.BAL0 - 1) * 100
    return n, (100 * w / n if n else float("nan")), ret, dd * 100, inval


def main():
    M, span = build()
    print(f"mature 1h bars {len(M)}  span {span:.1f}d   (ORB: 13:30-14:00 UTC = 9:30-10:00 ET open range)\n")
    for gate, glab in ((True, "ORB-breakout GATE"), (False, "no gate (1st MMxSkew after open)")):
        entries, dwo = daily_entries(M, gate)
        print("=" * 92)
        print(f"{glab}:  {len(entries)} daily setups over {dwo} days-with-open")
        print("=" * 92)
        print(f"  {'SL mode':>8} {'RR':>5} | {'n':>3} {'win%':>6} {'net%':>7} {'maxDD':>6} {'invalidSL':>9}")
        for sl_mode in ("frozen", "orb"):
            for rr in (1.0, 1.5):
                n, win, ret, dd, inv = run(M, entries, rr, sl_mode)
                print(f"  {sl_mode:>8} 1:{rr:>3} | {n:>3} {win:>6.1f} {ret:>+6.1f}% {dd:>5.1f}% {inv:>9}")
        print()


if __name__ == "__main__":
    main()
