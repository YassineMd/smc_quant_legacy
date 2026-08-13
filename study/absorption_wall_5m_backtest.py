"""Backtest the 5m ABSORPTION WALL overlay (app/engulf5m_detect ENGULF + app/absorb2_detect ABSORB2, wall-bounce).
Same methodology as the 15m Engulfing Wall: windowed wall+signal detection (6000-bar chunks / 5000 step, dedup by
global bar; ENGULF wins a shared bar over ABSORB2 like the terminal), barrier first-passage (SL-first on same-bar tie),
non-overlapping for the P&L (net), both recon years, 0.04% RT fee, HORIZON=288 (~1 day on 5m).

Entry = signal close; SL/TP = the detectors' own (SL 0.1% widest{prev,entry}; engulf TP 1:1.5 / gold 1:2; absorb2 1:1.5).
REWARD/EFF FLOW gate (same as the 15m study): FLOW = median of the [20,30,50,75]-bar reward-per-effort BUY shares at
the signal bar (causal). LONG kept iff FLOW>50, SHORT iff FLOW<50. Reports BASELINE vs GATED, by source and tier.
WINNER(full)=TP before SL; WINNER(half)=>=50%-to-TP before SL."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL, engulf5m_detect as ENG, absorb2_detect as AB2, absorption as ABSM, reward_eff

FEE = 0.0004; HORIZON = 288
A = sorted(load_archive("5m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
n = len(A)
C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
H = np.array([_f(b.get("high")) for b in A]); L = np.array([_f(b.get("low")) for b in A])
YR = np.array([datetime.fromtimestamp(_f(b.get("start_time")), tz=timezone.utc).year for b in A])

print("bars=%d detecting walls+signals in windows..." % n, flush=True)
sigs = {}; c0 = 0
while c0 < n:
    c1 = min(n, c0 + 6000); S = A[c0:c1]; ns = len(S)
    walls = AL.detect(S, skip_last=False)
    absorp = []                                              # ONE shared absorption pass (engulf + absorb2)
    for _k in range(ns):
        try:
            absorp.append(ABSM.absorption(S, _k)[0])
        except Exception:
            absorp.append(None)
    for e in ENG.detect(S, walls=walls, skip_last=False, absorp=absorp):        # ENGULF first (priority on a shared bar)
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]),
                        "WALL", bool(e.get("gold")))
    for e in AB2.detect(S, walls=walls, skip_last=False, absorp=absorp):        # ABSORB2 only if the bar is free
        gi = int(e["i"]) + c0
        if gi not in sigs:
            sigs[gi] = (int(e["side"]), float(e["entry"]), float(e["sl"]), float(e["tp"]),
                        "ABSORB2", False)
    if c1 >= n:
        break
    c0 += 5000
print("signals=%d\n" % len(sigs), flush=True)


def flow(i):
    ws = [s for s, ok in (reward_eff.share(A, i - w + 1, i) for w in (20, 30, 50, 75)) if ok]
    if not ws:
        return None
    sw = sorted(ws); m = len(sw)
    return sw[m // 2] if (m % 2) else 0.5 * (sw[m // 2 - 1] + sw[m // 2])


def score(gi, side, entry, sl, tp):
    half = entry + 0.5 * (tp - entry); ho = fo = "T"; xi = min(n - 1, gi + HORIZON); dh = df = False
    for k in range(gi + 1, min(n, gi + 1 + HORIZON)):
        hs = (L[k] <= sl) if side > 0 else (H[k] >= sl)
        ht = (H[k] >= tp) if side > 0 else (L[k] <= tp)
        hh = (H[k] >= half) if side > 0 else (L[k] <= half)
        if not dh:
            ho = "L" if hs else ("W" if hh else ho); dh = hs or hh
        if not df:
            if hs:
                fo = "L"; xi = k; df = True
            elif ht:
                fo = "W"; xi = k; df = True
        if dh and df:
            break
    exitp = sl if fo == "L" else (tp if fo == "W" else C[xi])
    return ho, fo, side * (exitp - entry) / entry - FEE, xi


rows = []                                                    # (yr, side, ho, fo, ret, gi, xi, src, gold, flow_ok)
for gi in sorted(sigs):
    if gi + 1 >= n:
        continue
    side, entry, sl, tp, src, gold = sigs[gi]
    if entry <= 0:
        continue
    fs = flow(gi)
    if fs is None:
        continue
    fok = (fs > 50) if side > 0 else (fs < 50)
    ho, fo, ret, xi = score(gi, side, entry, sl, tp)
    rows.append((int(YR[gi]), side, ho, fo, ret, gi, xi, src, gold, fok))
print("scored=%d\n" % len(rows), flush=True)


def report(tag, sel):
    R = [r for r in rows if sel(r)]

    def cell(RR):
        if not RR:
            return "n=0"
        hw = sum(1 for r in RR if r[2] == "W"); hl = sum(1 for r in RR if r[2] == "L")
        fw = sum(1 for r in RR if r[3] == "W"); fl = sum(1 for r in RR if r[3] == "L")
        ov = -1; net = 0.0; nn = 0
        for r in sorted(RR, key=lambda x: x[5]):
            if r[5] <= ov:
                continue
            net += r[4]; ov = r[6]; nn += 1
        return "n=%-5d half=%.1f%% full=%.1f%% net=%+.1f%%/%d" % (
            len(RR), 100 * hw / max(1, hw + hl), 100 * fw / max(1, fw + fl), net * 100, nn)
    print("  %-24s BOTH %s" % (tag, cell(R)))
    print("  %-24s 2025 %s" % ("", cell([r for r in R if r[0] == 2025])))
    print("  %-24s 2026 %s" % ("", cell([r for r in R if r[0] == 2026])))


print("=== 5m ABSORPTION WALL (half=>=50%-to-TP, full=TP, net=non-overlap) ===")
report("all", lambda r: True)
report("+ reward/eff FLOW gate", lambda r: r[9])
report("engulf only (WALL)", lambda r: r[7] == "WALL")
report("absorb2 only", lambda r: r[7] == "ABSORB2")
report("gold engulf", lambda r: r[8])
report("gold + FLOW gate", lambda r: r[8] and r[9])
report("engulf + FLOW gate", lambda r: r[7] == "WALL" and r[9])
