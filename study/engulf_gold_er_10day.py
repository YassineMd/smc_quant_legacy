"""5m ENGULF spot-check. Take ANY engulf signal (continuation OR reversal, any badge tier) that carries a
GOLD RING (finish_strength.ring_tier==2) AND 1m-Eff (unsigned Kaufman ER of its 1m closes) >= 0.80. Evaluate
at FIXED brackets 1:1.2 and 1:2 (R = |entry - signal stop|, TP = entry +/- RR*R), TP-first vs SL-first.
Sample 10 days, each a different month (=> different week), one random mid-month day each (seeded, dates
printed). Report the funnel + every qualifying trade at both RRs + the FULL-18mo figure beside it.
Only the small candidate set is forward-walked, so it's fast.
"""
import os, sys, datetime as dt
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import engulf5m_detect as E5
from app import finish_strength as FIN
from app.engulf_sr_detect import _ohlc

SEED = 42
ER_MIN = 0.80
RRS = [1.2, 2.0]
FEE = 0.08          # % round-trip cost applied to net

_, r5, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(r5, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
C = np.empty(n); H = np.empty(n); Lo = np.empty(n); ST = np.empty(n); ET = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); C[i] = c; H[i] = h; Lo[i] = l
    ST[i] = float(b.get("start_time", 0) or 0); ET[i] = float(b.get("end_time", 0) or 0)
sigs = E5.detect(A)
print("5m loaded %d; engulf signals %d" % (n, len(sigs))); sys.stdout.flush()

z = np.load(os.path.join("study", "_1m_arrays.npz"))
st1, o1, c1, h1, l1, bv1, sv1 = z["st1"], z["o1"], z["c1"], z["h1"], z["l1"], z["bv1"], z["sv1"]
print("1m loaded from cache %d" % len(st1)); sys.stdout.flush()
del r5


def er_unsigned(cl):
    if len(cl) < 3: return None
    g = float(np.abs(np.diff(cl)).sum())
    return abs(cl[-1] - cl[0]) / g if g > 1e-12 else None


# ---- pass 1: ring + ER for EVERY engulf signal (no forward walk) -> gold-ring & ER>=.8 candidates ----
n_gold = 0
cand = []   # (i, start, side, er, entry, sl, rev, gold_badge)
for s in sigs:
    i = s["i"]
    if not (1 <= i < n - 1): continue
    p0 = int(np.searchsorted(st1, ST[i], "left")); p1 = int(np.searchsorted(st1, ET[i], "left"))
    if p1 - p0 < 3: continue
    sub = [{"open": o1[j], "close": c1[j], "high": h1[j], "low": l1[j],
            "buy_vol": bv1[j], "sell_vol": sv1[j]} for j in range(p0, p1)]
    if FIN.ring_tier(sub, s["side"]) != 2:                 # GOLD RING only (any badge tier / rev)
        continue
    n_gold += 1
    er = er_unsigned(c1[p0:p1])
    if er is None or er < ER_MIN:
        continue
    entry = float(s.get("entry", C[i]) or C[i]); sl = float(s["sl"])
    if abs(entry - sl) <= 1e-9:
        continue
    cand.append((i, ST[i], s["side"], er, entry, sl, bool(s["rev"]), bool(s.get("gold", False))))
print("gold-ring engulf: %d  ->  gold-ring & 1mEff>=%.2f: %d" % (n_gold, ER_MIN, len(cand))); sys.stdout.flush()


def outcome_rr(i, side, entry, sl, rr):
    R = abs(entry - sl); tp = entry + rr * R * side
    for j in range(i + 1, n):
        hs = (Lo[j] <= sl) if side > 0 else (H[j] >= sl)
        ht = (H[j] >= tp) if side > 0 else (Lo[j] <= tp)
        if hs and ht: return 0
        if ht: return 1
        if hs: return 0
    return None


# ---- sample 10 days: 10 distinct months, one random mid-month day (8..22) each ----
months = sorted({(d.year, d.month) for d in (dt.datetime.utcfromtimestamp(t) for t in ST)})
rs = np.random.RandomState(SEED)
pick = sorted(rs.choice(len(months), size=min(10, len(months)), replace=False))
sampled = [dt.datetime(months[k][0], months[k][1], int(rs.randint(8, 23))) for k in pick]
wins_day = [(d.replace(hour=0).timestamp(), (d + dt.timedelta(days=1)).replace(hour=0).timestamp()) for d in sampled]
print("\nSampled 10 days (seed=%d), one per distinct month:" % SEED)
for d in sampled:
    print("  %s  (%d-W%02d)" % (d.strftime("%Y-%m-%d %a"), d.isocalendar()[0], d.isocalendar()[1]))
in_sample = lambda t: any(a <= t < b for a, b in wins_day)


def evalset(label, rows):
    print("\n" + "=" * 92); print(label); print("=" * 92)
    if not rows:
        print("  NO qualifying signals."); return
    # per-RR outcomes
    res = {}
    for rr in RRS:
        rr_rows = []
        for (i, t, side, er, entry, sl, rev, gb) in rows:
            oc = outcome_rr(i, side, entry, sl, rr)
            if oc is None: continue
            rpct = abs(entry - sl) / entry * 100.0
            net = (rr * rpct if oc else -rpct) - FEE
            rr_rows.append((t, side, er, rev, oc, net, rpct))
        res[rr] = rr_rows
    for rr in RRS:
        rw = res[rr]
        if not rw:
            print("  RR 1:%.1f -> no resolved trades" % rr); continue
        w = sum(r[4] for r in rw); nn = len(rw); nets = [r[5] for r in rw]
        revn = [r for r in rw if r[3]]; cont = [r for r in rw if not r[3]]
        print("  RR 1:%.1f | n=%d  win %.1f%%  net/trade %+.3f%%  TOTAL %+.2f%%   [cont %d/%d win  rev %d/%d win]"
              % (rr, nn, 100.0 * w / nn, float(np.mean(nets)), float(np.sum(nets)),
                 sum(r[4] for r in cont), len(cont), sum(r[4] for r in revn), len(revn)))
    # per-signal detail (outcome at both RRs side by side) — use RR list order
    base = res[RRS[0]]
    if base:
        idx = {(r[0], r[1]): r for r in base}
        print("  %-17s %4s %3s %6s %10s %10s" % ("date(UTC)", "sd", "rev", "1mEff", "1:%.1f" % RRS[0], "1:%.1f" % RRS[1]))
        seen = set()
        for rr in RRS:
            for (t, side, er, rev, oc, net, rpct) in res[rr]:
                seen.add((t, side))
        for (t, side) in sorted(seen):
            o1r = next((r for r in res[RRS[0]] if r[0] == t and r[1] == side), None)
            o2r = next((r for r in res[RRS[1]] if r[0] == t and r[1] == side), None)
            er = (o1r or o2r)[2]; rev = (o1r or o2r)[3]
            f = lambda r: ("--" if r is None else ("WIN %+.2f" % r[5] if r[4] else "loss %+.2f" % r[5]))
            print("  %-17s %4s %3s %6.2f %10s %10s"
                  % (dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M"),
                     "L" if side > 0 else "S", "Y" if rev else "-", er, f(o1r), f(o2r)))


sample_cand = [c for c in cand if in_sample(c[1])]
day_gold = sum(1 for s in sigs if 1 <= s["i"] < n - 1 and in_sample(ST[s["i"]]))
print("\nFunnel on the 10 sampled days: gold-ring&ER>=%.2f candidates = %d" % (ER_MIN, len(sample_cand)))
evalset("(1) REQUESTED — gold ring + 1mEff>=%.2f, ANY tier incl reversal, on the 10 sampled days" % ER_MIN, sample_cand)
evalset("(2) CONTEXT — SAME filter over ALL 18 months (the honest read)", cand)
print("\nNOTE: 10 spread days at this tightness is a SPOT CHECK (near-zero power) -> read (1) as anecdote, (2) as real.")
print("R = |entry - signal stop|; TP = entry +/- RR*R; net%% = move minus %.2f%% cost; win = TP-first." % FEE)
