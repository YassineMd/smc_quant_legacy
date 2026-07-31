"""1m LVN + absorption strategy — CYAN/MAGENTA-only variant, 5 random days.

ENTRY: a CYAN/MAGENTA absorption candle (engulf1m kind 'cm', |A|>=2) AT the matching LVN zone — cyan (long) at a
       support zone / magenta (short) at a resistance zone. Direction comes from the candle; no bias/confidence gate.
EXIT:  SL 0.1% beyond the ENTRY-side LVN; TP = a FIXED 0.3% in favour. Realized RR = 0.3% / stop-distance.
1m timeframe, 20 random well-spaced days. Causal: zones over the trailing W buckets. Non-overlapping.
"""
import gzip, json, glob, os, sys, time, random, statistics, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import swing_lvn_detect as S
from app import engulf1m_detect as AC
from app import absorption as ABS

SEED = 20260731
N_DAYS = 20
SPACING = 3                       # chosen days at least this many days apart (windows won't overlap)
WARMUP_S = int(1.0 * 86400)       # 1m buckets to load before each day (for the trailing window)
FWD_S = int(0.5 * 86400)          # load after each day (for exits; MAXH<<0.5d)
W = 800                           # trailing window the LVN zones see
SL_PAD = 0.001                    # SL 0.1% BELOW/above the ENTRY-side LVN
TP_FIXED = 0.003                  # fixed 0.3% take-profit
FEE = 0.1
MAXH = 240                        # 1m bars a trade gets to resolve (~4h)
# This variant: enter on CYAN/MAGENTA absorption candles ONLY (engulf1m kind 'cm', |A|>=2), direction from the candle
# (cyan=long / magenta=short) AT the matching LVN zone. No bias/confidence gate. SL 0.1% beyond the ENTRY LVN; TP =
# a FIXED 0.3% in favour. Realized RR = 0.3% / stop-distance, varies per trade.


def pick_days(seed, n, spacing):
    random.seed(seed)
    lo = dt.date(2025, 2, 1); hi = dt.date(2026, 5, 15)
    alld = [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]   # sorted -> deterministic
    random.shuffle(alld)
    chosen = []
    for d in alld:
        if all(abs((d - c).days) >= spacing for c in chosen):
            chosen.append(d)
        if len(chosen) == n:
            break
    return sorted(chosen)


def load_1m_days(day_starts):
    """Stream the 1m recon once; bucket each 1m bar into whichever day-window [D-WARMUP, D_end+FWD] contains it."""
    windows = [(ds - WARMUP_S, ds + 86400 + FWD_S) for ds in day_starts]
    per = {ds: [] for ds in day_starts}
    for fn in sorted(glob.glob(os.path.join(ROOT, "study", "recon_archive", "1m", "1m_*.jsonl.gz"))):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                st = float(d.get("start_time", 0.0) or 0.0)
                if st <= 0:
                    continue
                for k, (a, b) in enumerate(windows):
                    if a <= st < b:
                        per[day_starts[k]].append(d)
                        break
    for ds in per:
        per[ds].sort(key=lambda x: float(x.get("start_time", 0.0) or 0.0))
    return per


def run_day(dbuck, day_date):
    n = len(dbuck)
    if n < W + 20:
        return []
    H = [float(b.get("high", 0.0) or 0.0) for b in dbuck]
    L = [float(b.get("low", 0.0) or 0.0) for b in dbuck]
    C = [float(b.get("close_price", b.get("close", 0.0)) or 0.0) for b in dbuck]
    absorp = []
    for i in range(n):
        try:
            absorp.append(ABS.absorption(dbuck, i)[0])
        except Exception:
            absorp.append(None)
    ac_bar = {e["i"]: e["side"] for e in AC.detect(dbuck, skip_last=False, absorp=absorp) if e["kind"] == "cm"}
    trades = []; next_free = W
    for t in sorted(ac_bar):
        if t < W or t >= n - 5 or t < next_free:
            continue
        st = float(dbuck[t].get("start_time", 0.0) or 0.0)
        if dt.datetime.utcfromtimestamp(st).date() != day_date:       # only ENTER on the target day
            continue
        dirside = ac_bar[t]                                            # direction from the cyan(+1)/magenta(-1) candle
        win = dbuck[t - W:t + 1]
        zones = S.detect(win) or []
        entry = C[t]; lo = L[t]; hi = H[t]
        aligned = [z for z in zones if z["ends_high"] == (dirside > 0)]     # cyan->support / magenta->resistance
        touched = [z for z in aligned if lo <= z["zhi"] and hi >= z["zlo"]]
        if not touched:
            continue
        zone = min(touched, key=lambda z: abs(z["lvn"] - entry)); ent_lvn = zone["lvn"]
        sl0 = ent_lvn * (1 - SL_PAD) if dirside > 0 else ent_lvn * (1 + SL_PAD)   # SL 0.1% beyond the ENTRY LVN
        if (dirside > 0 and sl0 >= entry) or (dirside < 0 and sl0 <= entry):
            continue
        tp = entry * (1 + dirside * TP_FIXED)                                     # fixed 0.3% take-profit
        risk = abs(entry - sl0)
        outcome = None; exitbar = None
        for j in range(t + 1, min(n, t + 1 + MAXH)):
            hj = H[j]; lj = L[j]
            if dirside > 0:
                if lj <= sl0:
                    outcome = "loss"; exitbar = j; break
                if hj >= tp:
                    outcome = "win"; exitbar = j; break
            else:
                if hj >= sl0:
                    outcome = "loss"; exitbar = j; break
                if lj <= tp:
                    outcome = "win"; exitbar = j; break
        if outcome is None:
            continue
        exitp = tp if outcome == "win" else sl0
        net = (exitp - entry) / entry * 100.0 * dirside - FEE
        trades.append(dict(side=dirside, outcome=outcome, net=net, risk_pct=risk / entry * 100.0,
                           rr=abs(tp - entry) / risk if risk > 0 else 0.0))
        next_free = exitbar
    return trades


def rep(rows, title):
    if not rows:
        print("%-14s (no trades)" % title); return
    w = sum(1 for r in rows if r["outcome"] == "win"); l = sum(1 for r in rows if r["outcome"] == "loss")
    net = sum(r["net"] for r in rows); pos = sum(r["net"] for r in rows if r["net"] > 0)
    neg = -sum(r["net"] for r in rows if r["net"] < 0)
    wr = 100.0 * w / (w + l) if (w + l) else 0.0
    pf = pos / neg if neg > 1e-9 else float("inf")
    arr = statistics.mean(r["rr"] for r in rows)
    print("%-14s n=%-3d W=%-3d L=%-3d  win%%=%.1f%%  avg net=%+.3f%%  sum=%+.2f%%  PF=%s  avg risk=%.3f%%  avg RR=%.2f  (BE win@RR=%.0f%%)" % (
        title, len(rows), w, l, wr, net / len(rows), net, ("inf" if pf == float("inf") else "%.2f" % pf),
        statistics.mean(r["risk_pct"] for r in rows), arr, 100.0 / (1 + arr)))


def main():
    t0 = time.time()
    days = pick_days(SEED, N_DAYS, SPACING)
    print("chosen 1m days:", ", ".join(d.isoformat() for d in days), flush=True)
    day_starts = [int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) for d in days]
    print("streaming 1m recon for the 5 day-windows ...", flush=True)
    per = load_1m_days(day_starts)
    print("  loaded  (%.0fs)" % (time.time() - t0), flush=True)
    allt = []
    for d, ds in zip(days, day_starts):
        db = per[ds]
        tr = run_day(db, d)
        allt += tr
        print("  %s : %5d 1m buckets -> %d trades" % (d.isoformat(), len(db), len(tr)), flush=True)
    print("\ntotal trades: %d   (%.0fs)\n" % (len(allt), time.time() - t0), flush=True)
    rep(allt, "ALL")
    rep([r for r in allt if r["side"] > 0], "LONG")
    rep([r for r in allt if r["side"] < 0], "SHORT")
    if allt:
        print("\n(TP = opposite LVN, >0.2% away; SL 0.1% beyond entry LVN; realized RR varies -> see per-row 'avg RR' + BE win%%)")


if __name__ == "__main__":
    main()
