"""5m ENGULFING success vs failure: what separates the signal bars whose trade hits TP from those that hit SL?
Full feature set per signal — candle stats (signal + prev + next, side-aligned), position relative to S/R zones, and
the signal's location in YESTERDAY's and TODAY's-developing volume profile (VAL/VAH/POC). Normal (bias) and Reversal
(Case-1/Case-2) signals reported SEPARATELY. Ranked by AUC(win vs loss). Whole 5m recon (all signals, per-signal unit).
"""
import os, sys, datetime as dt
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app import absorption as ABS
from app import engulf5m_detect as E5
from app.footprint_panel import profile_skewness
from app.engulf_sr_detect import _ohlc

K = SR.SR_PIVOT_K
_, rows, _ = load_archive("5m", root=os.path.join("study", "recon_archive"))
A = sorted(rows, key=lambda b: float(b.get("start_time", 0) or 0)); n = len(A)
O = np.empty(n); C = np.empty(n); H = np.empty(n); Lo = np.empty(n); DAY = [None] * n
buy = np.empty(n); sell = np.empty(n)
for i, b in enumerate(A):
    o, c, h, l = _ohlc(b); O[i] = o; C[i] = c; H[i] = h; Lo[i] = l
    buy[i] = float(b.get("buy_vol", 0) or 0); sell[i] = float(b.get("sell_vol", 0) or 0)
    st = float(b.get("start_time", 0) or 0)
    if st > 0:
        DAY[i] = dt.datetime.utcfromtimestamp(st).date()
cbuy = np.concatenate([[0], np.cumsum(buy)]); csell = np.concatenate([[0], np.cumsum(sell)])
sys.stdout.write("loaded %d\n" % n); sys.stdout.flush()

day_start = [0] * n; cur = None
for i in range(n):
    if DAY[i] != cur:
        cur = DAY[i]; s = i
    day_start[i] = s


def va_poc3(prof, pct=0.70):
    items = sorted((p, w) for p, w in (prof or {}).items() if w > 0)
    if len(items) < 3:
        return None
    total = sum(w for _, w in items); tgt = pct * total
    pi = max(range(len(items)), key=lambda k: items[k][1]); poc = items[pi][0]; lo = hi = pi; va = items[pi][1]
    while va < tgt and (lo > 0 or hi < len(items) - 1):
        up = items[hi + 1][1] if hi < len(items) - 1 else -1
        dn = items[lo - 1][1] if lo > 0 else -1
        if up >= dn: hi += 1; va += items[hi][1]
        else: lo -= 1; va += items[lo][1]
    return items[lo][0], items[hi][0], poc


def day_profiles():
    days = {}
    for b in A:
        st = float(b.get("start_time", 0) or 0)
        if st <= 0: continue
        d = dt.datetime.utcfromtimestamp(st).date(); prof = days.setdefault(d, {})
        for pr, v in (b.get("levels") or {}).items():
            try: p = float(pr)
            except (TypeError, ValueError): continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return days


DAYS = day_profiles()
YVA = {d: va_poc3(p) for d, p in DAYS.items()}


def dev_va(i):
    prof = {}
    for j in range(day_start[i], i + 1):
        for pr, v in (A[j].get("levels") or {}).items():
            try: p = float(pr)
            except (TypeError, ValueError): continue
            prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
    return va_poc3(prof)


sigs = E5.detect(A)
levels = SR.detect(A, K, zone_mitigation=True)
SUP = [x for x in levels if x["kind"] == "S"]; RES = [x for x in levels if x["kind"] == "R"]
sys.stdout.write("engulf signals: %d\n" % len(sigs)); sys.stdout.flush()


def active(levs, i):
    return [x for x in levs if x["i0"] + K <= i and (x["i1"] is None or x["i1"] > i)]


def outcome(s):
    i = s["i"]; side = s["side"]; tp = s["tp"]; sl = s["sl"]
    for j in range(i + 1, n):
        if side > 0:
            hs = Lo[j] <= sl; ht = H[j] >= tp
        else:
            hs = H[j] >= sl; ht = Lo[j] <= tp
        if hs and ht: return 0
        if ht: return 1
        if hs: return 0
    return None


SIGNED0 = {"dir", "delta", "dvpin", "da2", "skew", "tick_imb", "oi_open_n", "A", "A_h2"}
SHARE5 = {"buy_share", "close_loc", "er_share"}


def cfeat(i):
    b = A[i]; o, c, h, l = O[i], C[i], H[i], Lo[i]; bv = buy[i]; sv = sell[i]; vol = bv + sv or 1e-9
    d = bv - sv; dh1 = float(b.get("delta_h1", 0) or 0); rng = max(h - l, 1e-9)
    up = float(b.get("up_ticks", 0) or 0); dn = float(b.get("dn_ticks", 0) or 0)
    ber = float(b.get("buyer_er", 0) or 0); ser = float(b.get("seller_er", 0) or 0)
    oi = (float(b.get("opL", 0) or 0) + float(b.get("opS", 0) or 0)) - (float(b.get("clL", 0) or 0) + float(b.get("clS", 0) or 0))
    try: Aab = ABS.absorption(A, i)[0]
    except Exception: Aab = None
    try: Ah2 = ABS.absorption_halves(A, i)[1]
    except Exception: Ah2 = None
    return {"dir": 1.0 if c > o else (-1.0 if c < o else 0.0), "body_pct": abs(c - o) / o * 100,
            "range_pct": rng / o * 100, "body_to_range": abs(c - o) / rng, "upper_wick": (h - max(o, c)) / o * 100,
            "lower_wick": (min(o, c) - l) / o * 100, "close_loc": (c - l) / rng, "vpin": abs(d) / vol, "dvpin": d / vol,
            "buy_share": bv / vol, "delta": d, "da2": (d - 2 * dh1) / vol, "A": Aab if Aab is not None else 0.0,
            "A_h2": Ah2 if Ah2 is not None else 0.0, "skew": profile_skewness(b.get("levels")) or 0.0,
            "oi_open_n": oi / vol, "er_share": ber / (ber + ser) if (ber + ser) > 0 else 0.5,
            "tick_imb": (up - dn) / (up + dn) if (up + dn) > 0 else 0.0, "vel_ratio": float(b.get("vel_ratio", 0) or 0)}


def aln(f, v, side):
    if f in SIGNED0: return v * side
    if f in SHARE5: return (v - 0.5) * side
    return v


def flow12(i, side, W=12):
    a = max(0, i - W + 1); bs = cbuy[i + 1] - cbuy[a]; ss = csell[i + 1] - csell[a]
    return ((bs - ss) / (bs + ss) if (bs + ss) > 0 else 0.0) * side


def zones_dist(i, c, side):
    own = active(SUP if side > 0 else RES, i); opp = active(RES if side > 0 else SUP, i)
    in_own = 0.0; oe = 10.0
    for x in own:
        zlo, zhi = x["zlo"], x["zhi"]
        if zlo <= c <= zhi: in_own = 1.0; oe = 0.0
        else: oe = min(oe, min(abs(c - zlo), abs(c - zhi)) / c * 100)
    od = 10.0
    for x in opp:
        zlo, zhi = x["zlo"], x["zhi"]
        if side > 0 and zlo > c: od = min(od, (zlo - c) / c * 100)
        elif side < 0 and zhi < c: od = min(od, (c - zhi) / c * 100)
    return in_own, oe, od, float(len(active(SUP, i))), float(len(active(RES, i)))


def sfeat(s):
    i = s["i"]; side = s["side"]; c = C[i]; f = {}
    for role, j in (("sig", i), ("prev", i - 1), ("nxt", i + 1)):
        for k, v in cfeat(j).items():
            f["%s_%s" % (role, k)] = aln(k, v, side)
    in_own, oe, od, nsup, nres = zones_dist(i, c, side)
    f.update(in_own_zone=in_own, own_edge_dist=oe, opp_dist=od, n_sup=nsup, n_res=nres,
             conf=1.0 if s["conf"] else 0.0, gold=1.0 if s["gold"] else 0.0,
             mru_only=1.0 if s["mru_only"] else 0.0, c2=1.0 if s["c2"] else 0.0, flow12_aln=flow12(i, side))
    yv = YVA.get(DAY[i] - dt.timedelta(days=1)); tv = dev_va(i)
    for tag, va in (("y", yv), ("t", tv)):
        if va:
            val, vah, poc = va; span = max(vah - val, 1e-9)
            pos = (c - val) / span; pos = pos if side > 0 else (1 - pos)         # side-aligned VA position
            f["%s_va_pos" % tag] = pos
            f["%s_poc_aln" % tag] = (c - poc) / c * 100 * side
            f["%s_inside_va" % tag] = 1.0 if val <= c <= vah else 0.0
        else:
            f["%s_va_pos" % tag] = np.nan; f["%s_poc_aln" % tag] = np.nan; f["%s_inside_va" % tag] = np.nan
    f["hour_utc"] = float(dt.datetime.utcfromtimestamp(float(A[i].get("start_time", 0) or 0)).hour)
    return f


from scipy.stats import rankdata


def auc(pos, neg):
    pos = np.array([x for x in pos if x == x], float); neg = np.array([x for x in neg if x == x], float)
    if len(pos) < 6 or len(neg) < 6: return None
    r = rankdata(np.concatenate([pos, neg]))                 # AVERAGE ranks for ties (correct for binary/tied features)
    u = r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2.0
    return u / (len(pos) * len(neg))


records = []
for s in sigs:
    oc = outcome(s)
    if oc is None: continue
    if s["i"] < 1 or s["i"] >= n - 1: continue
    records.append((s, oc, sfeat(s)))
FKEYS = list(records[0][2].keys())


def report(label, recs):
    win = [r for r in recs if r[1] == 1]; los = [r for r in recs if r[1] == 0]
    print("\n" + "=" * 100)
    print("%s | n=%d  WIN %d (%.1f%%)  LOSS %d" % (label, len(recs), len(win), 100 * len(win) / max(1, len(recs)), len(los)))
    print("=" * 100)
    if len(win) < 6 or len(los) < 6:
        print("  (too few to score)"); return
    rowso = []
    for f in FKEYS:
        wv = [r[2][f] for r in win]; lv = [r[2][f] for r in los]
        a = auc(wv, lv)
        if a is None: continue
        wm = np.nanmean(wv); lm = np.nanmean(lv)
        rowso.append((abs(a - 0.5), f, wm, lm, a))
    print("  %-18s %10s %10s %8s" % ("feature", "win-mean", "loss-mean", "AUC"))
    for _, f, wm, lm, a in sorted(rowso, reverse=True):
        print("  %-18s %10.3f %10.3f %8.3f" % (f, wm, lm, a))


def mgmt(label, recs):
    """Actionable payoff: does the NEXT bar's follow-through decide the trade? Split win% by whether the bar AFTER
    entry closes WITH the trade side. (Observable one bar after entry -> a management rule, not an entry filter.)"""
    aln = [r for r in recs if (C[r[0]["i"] + 1] - O[r[0]["i"] + 1]) * r[0]["side"] > 0]
    agn = [r for r in recs if (C[r[0]["i"] + 1] - O[r[0]["i"] + 1]) * r[0]["side"] < 0]
    def wr(g): return (100 * sum(1 for r in g if r[1] == 1) / len(g)) if g else 0.0
    print("  %-40s next-bar ALIGNED: %4d @ %.1f%% win  |  next-bar AGAINST: %4d @ %.1f%% win"
          % (label, len(aln), wr(aln), len(agn), wr(agn)))


report("ALL 5m ENGULF", records)
report("NORMAL (bias, rev=0)", [r for r in records if not r[0]["rev"]])
report("REVERSAL (rev=1: Case-1 held-zone + Case-2 SE2)", [r for r in records if r[0]["rev"]])
print("\n" + "=" * 100)
print("MANAGEMENT: win% conditioned on the NEXT bar's follow-through (observable 1 bar after entry)")
print("=" * 100)
mgmt("ALL", records)
mgmt("NORMAL", [r for r in records if not r[0]["rev"]])
mgmt("REVERSAL", [r for r in records if r[0]["rev"]])
print("\nAUC>0.5 => feature higher in WINNERS. Directional candle/flow/POC features side-aligned (long+/short-).")
print("va_pos side-aligned: 0=at the favorable VA edge (long=VAL/short=VAH), 1=far edge. poc_aln>0 = entry on the")
print("continuation side of POC. Per-signal unit (signals may overlap) — descriptive, in-sample, whole recon.")
