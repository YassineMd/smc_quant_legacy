# -*- coding: utf-8 -*-
"""Crazy star + BREAK-BIAS state machine (user's spec), NY, 15m both recon yr.
 bias = rolling S:R of wall BREAKS: S-breaks dominate -> SHORT ; R-breaks dominate -> LONG (carry-forward on a tie).
 EXHAUSTION: 2 consecutive breaks 'from your bias' (short->S-break, long->R-break) -> STOP trading.
 RESET (resume): after a stop, need a BIAS wall created (short->Resistance, long->Support) AND THEN an aligned
   continuation break (short->S-break, long->R-break).
 Trade a star (NY) in the bias direction only when NOT stopped. Reports the CONDITIONED trades' win/net/both-yr/DD."""
import os, sys, bisect
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from collections import defaultdict
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, crazy_wall_detect as CW

W = 96; FEE = 0.0004
A = sorted(load_archive("15m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A); C = [_f(b.get("close", b.get("close_price"))) for b in A]; Hh = [_f(b.get("high")) for b in A]; Ll = [_f(b.get("low")) for b in A]
DT = [datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc) for b in A]
YR = [d.year for d in DT]; HR = [d.hour for d in DT]
walls = AL.detect(A)
ev_at = defaultdict(list)
for e in CW.detect(A, walls):
    if e["i"] + 1 < n and 13 <= HR[e["i"]] < 21:
        ev_at[e["i"]].append(e)
cre_at = defaultdict(list); brk_at = defaultdict(list)
for w in walls:
    cre_at[int(w["i0"])].append(w["side"])
    if w.get("broken"):
        brk_at[int(w["i1"])].append(w["side"])
brk = sorted(((int(w["i1"]), w["side"]) for w in walls if w.get("broken")), key=lambda t: t[0]); bi1 = [b[0] for b in brk]


def bias_roll(i):
    lo = bisect.bisect_left(bi1, i - W); hi = bisect.bisect_right(bi1, i); s = r = 0
    for k in range(lo, hi):
        if brk[k][1] == "S": s += 1
        else: r += 1
    return -1 if s > r else (1 if r > s else 0)


def run(tp, sl, max_per_session=2):
    cur = 0; stopped = False; have_biaswall = False; last2 = []; ou = -1; res = []; ntrig = ntrade = 0
    day_count = defaultdict(int)
    for i in range(n):
        for s in brk_at.get(i, ()):                       # BREAKS at bar i
            b = bias_roll(i);  cur = b if b != 0 else cur
            last2.append(s); last2[:] = last2[-2:]
            fb = (cur < 0 and s == "S") or (cur > 0 and s == "R")   # break 'from bias'
            if not stopped and len(last2) == 2 and \
               all((cur < 0 and x == "S") or (cur > 0 and x == "R") for x in last2):
                stopped = True; have_biaswall = False                # EXHAUSTION -> stop
            elif stopped and have_biaswall and fb:                    # RESET step2: aligned continuation break
                stopped = False; last2 = []
        for s in cre_at.get(i, ()):                        # CREATIONS at bar i
            if stopped and not have_biaswall:
                if (cur < 0 and s == "R") or (cur > 0 and s == "S"):  # bias wall created
                    have_biaswall = True
        for e in ev_at.get(i, ()):                         # star events at bar i
            ntrig += 1
            b = bias_roll(i);  d = b if b != 0 else cur
            if stopped or d == 0 or i <= ou or i + 1 >= n:
                continue
            day = DT[i].date()
            if day_count[day] >= max_per_session:          # cap trades per NY session (UTC day)
                continue
            ntrade += 1; day_count[day] += 1
            E = C[i]; tpx = E * (1 + d * tp); slx = E * (1 - d * sl); out = None; xi = min(n - 1, i + 192)
            for k in range(i + 1, min(n, i + 193)):
                hs = (Ll[k] <= slx) if d > 0 else (Hh[k] >= slx); ht = (Hh[k] >= tpx) if d > 0 else (Ll[k] <= tpx)
                if hs: out = "L"; xi = k; break
                if ht: out = "W"; xi = k; break
            r = (tp - FEE) if out == "W" else ((-sl - FEE) if out == "L" else d * (C[xi] - E) / E - FEE)
            res.append((YR[i], r, out)); ou = xi
    return res, ntrig, ntrade


def rep(tag, res):
    for yl, yf in (("BOTH", None), ("25", 2025), ("26", 2026)):
        r = [x for x in res if (yf is None or x[0] == yf)]
        if not r: continue
        N = len(r); w = sum(1 for x in r if x[2] == "W"); net = sum(x[1] for x in r) * 100
        bal = 1.0; pk = 1.0; mdd = 0.0
        for _, rv, _o in r: bal *= (1 + rv); pk = max(pk, bal); mdd = min(mdd, bal / pk - 1)
        print("   %-24s [%s] n=%4d win=%5.1f%% net=%+7.2f%% comp=%+6.1f%% DD=%.1f%%" % (tag, yl, N, 100 * w / N, net, (bal - 1) * 100, mdd * 100), flush=True)


for tp, sl in ((0.003, 0.015),):
    res, ntrig, ntrade = run(tp, sl, max_per_session=2)
    print("=== SM break-bias  TP%.1f%%/SL%.1f%%  max2/NYsession  (of %d NY star, %d traded) ===" % (tp * 100, sl * 100, ntrig, ntrade), flush=True)
    rep("SM tp%.1f/sl%.1f max2" % (tp * 100, sl * 100), res)
