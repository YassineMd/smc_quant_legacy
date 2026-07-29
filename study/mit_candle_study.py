"""What do S/R-MITIGATION candles have in common? Full per-candle stats for the break candle + the one before & after.
5m, 30 random days. Directional features are sign-aligned to the break direction (up-break=+, down-break=-) so R & S
mitigations combine; magnitude features are left raw. Reports break-aligned mean + %-aligned (directional) and
mean/median/CV (magnitude), for PREV / MIT / NEXT, ranked to surface the most consistent signature.
"""
import os, sys, datetime as dt, random
import numpy as np
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from study.archive_loader import load_archive
from app import support_resistance as SR
from app import absorption as ABS
from app.footprint_panel import profile_skewness
from app.engulf_sr_detect import _ohlc

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
sys.stdout.write("loaded %d 5m buckets\n" % n); sys.stdout.flush()

alldays = sorted({d for d in DAY if d is not None})
random.seed(30)
seldays = set(random.sample(alldays, 30))
sys.stdout.write("30 random days: %s\n" % sorted(seldays)); sys.stdout.flush()

levels = SR.detect(A)
mit = {}                                             # i1 -> kind (the mitigation candle & the level it broke)
for lv in levels:
    if lv["i1"] is not None:
        mit.setdefault(lv["i1"], lv["kind"])
events = [(i, k) for i, k in mit.items() if DAY[i] in seldays and 1 <= i < n - 1]
events.sort()
nR = sum(1 for _, k in events if k == "R"); nS = len(events) - nR
sys.stdout.write("mitigation candles in sample: %d  (R-break/up %d, S-break/down %d)\n" % (len(events), nR, nS)); sys.stdout.flush()

SIGNED0 = {"dir", "delta", "dvpin", "da2", "skew", "tick_imb", "oi_open", "oi_open_n", "A", "A_h2"}
SHARE5 = {"buy_share", "close_loc", "er_share"}


def feats(i):
    b = A[i]; o, c, h, l = O[i], C[i], H[i], Lo[i]; bv = buy[i]; sv = sell[i]; vol = bv + sv or 1e-9
    d = bv - sv; dh1 = float(b.get("delta_h1", 0) or 0); rng = max(h - l, 1e-9)
    up = float(b.get("up_ticks", 0) or 0); dn = float(b.get("dn_ticks", 0) or 0)
    ber = float(b.get("buyer_er", 0) or 0); ser = float(b.get("seller_er", 0) or 0)
    oi = (float(b.get("opL", 0) or 0) + float(b.get("opS", 0) or 0)) - (float(b.get("clL", 0) or 0) + float(b.get("clS", 0) or 0))
    try: Aab = ABS.absorption(A, i)[0]
    except Exception: Aab = None
    try: Ah2 = ABS.absorption_halves(A, i)[1]
    except Exception: Ah2 = None
    sk = profile_skewness(b.get("levels"))
    return {
        "dir": 1.0 if c > o else (-1.0 if c < o else 0.0),
        "body_pct": abs(c - o) / o * 100, "range_pct": rng / o * 100, "body_to_range": abs(c - o) / rng,
        "upper_wick": (h - max(o, c)) / o * 100, "lower_wick": (min(o, c) - l) / o * 100,
        "close_loc": (c - l) / rng, "vpin": abs(d) / vol, "dvpin": d / vol, "buy_share": bv / vol,
        "delta": d, "da2": (d - 2 * dh1) / vol, "A": Aab, "A_h2": Ah2, "skew": sk,
        "oi_open": oi, "oi_open_n": oi / vol, "buyer_er": ber, "seller_er": ser,
        "er_share": ber / (ber + ser) if (ber + ser) > 0 else 0.5,
        "tick_imb": (up - dn) / (up + dn) if (up + dn) > 0 else 0.0,
        "cvd_range_n": (float(b.get("cvd_hi", 0) or 0) - float(b.get("cvd_lo", 0) or 0)) / vol,
        "vel_ratio": float(b.get("vel_ratio", 0) or 0), "churn_n": float(b.get("churn", 0) or 0) / vol,
    }


FKEYS = list(feats(events[0][0]).keys())
# collect aligned features per role
data = {r: {f: [] for f in FKEYS} for r in ("prev", "mit", "next")}
for i, k in events:
    bs = 1.0 if k == "R" else -1.0
    for role, j in (("prev", i - 1), ("mit", i), ("next", i + 1)):
        fd = feats(j)
        for f, v in fd.items():
            if v is None:
                continue
            if f in SIGNED0:
                data[role][f].append(v * bs)
            elif f in SHARE5:
                data[role][f].append((v - 0.5) * bs)
            else:
                data[role][f].append(v)


def summ(vals):
    a = np.array([x for x in vals if x is not None], float)
    if len(a) == 0:
        return None
    return dict(n=len(a), mean=a.mean(), med=np.median(a), cv=(a.std() / abs(a.mean()) if a.mean() else 0),
               pos=100 * np.mean(a > 0))


directional = [f for f in FKEYS if f in SIGNED0 or f in SHARE5]
magnitude = [f for f in FKEYS if f not in SIGNED0 and f not in SHARE5]

print("=" * 96)
print("MITIGATION-candle signature | 5m | %d break candles (up %d / down %d) | break-aligned" % (len(events), nR, nS))
print("=" * 96)
print("\nDIRECTIONAL features (sign-aligned to break dir; %%-aligned = share pointing WITH the break):")
print("  %-13s %20s %20s %20s" % ("feature", "PREV mean/%aln", "MIT mean/%aln", "NEXT mean/%aln"))
rows_dir = []
for f in directional:
    s = {r: summ(data[r][f]) for r in ("prev", "mit", "next")}
    if s["mit"]:
        rows_dir.append((abs(s["mit"]["pos"] - 50), f, s))
for _, f, s in sorted(rows_dir, reverse=True):
    def cell(x): return "%+8.3f /%4.0f%%" % (x["mean"], x["pos"]) if x else "     --    "
    print("  %-13s %20s %20s %20s" % (f, cell(s["prev"]), cell(s["mit"]), cell(s["next"])))
print("\nMAGNITUDE features (raw; mean / median / CV):")
print("  %-13s %22s %22s %22s" % ("feature", "PREV", "MIT", "NEXT"))
for f in magnitude:
    s = {r: summ(data[r][f]) for r in ("prev", "mit", "next")}
    def cell(x): return "%7.3f/%7.3f/%4.2f" % (x["mean"], x["med"], x["cv"]) if x else "        --        "
    print("  %-13s %22s %22s %22s" % (f, cell(s["prev"]), cell(s["mit"]), cell(s["next"])))
