"""15m version of the cyan/magenta LVN strategy — full contiguous span (all regimes).

Same rules as the current 1m variant: enter on a CYAN/MAGENTA absorption candle (engulf1m kind 'cm', |A|>=2) AT the
matching LVN zone (cyan=long at a support / magenta=short at a resistance) AND WITH the LVN bias (candle side ==
bias dir); SL 0.1% BEYOND the entry-side LVN; TP 1:2. Causal (zones over trailing W buckets), non-overlapping, 0.1% fee.
"""
import gzip, json, glob, os, sys, time, statistics, math, datetime as dt


def binom_sf(k, n, p):
    """P(X >= k) for X~Binom(n,p) — exact, one-sided upper tail."""
    return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import swing_lvn_detect as S
from app import engulf1m_detect as AC
from app import absorption as ABS

W = 800                # trailing window the LVN zones see (~8 days on 15m)
SL_PAD = 0.001         # SL 0.1% beyond the ENTRY-side LVN (below for long / above for short)
RR = 2.0               # TP 1:2
FEE = 0.1
MAXH = 96              # 15m bars (~24h) for a trade to resolve
# Take the trade WITH the LVN bias: the cyan/magenta candle's side must agree with the bias direction (with-trend).


def load_15m():
    by_bid = {}
    for fn in sorted(glob.glob(os.path.join(ROOT, "study", "recon_archive", "15m", "15m_*.jsonl.gz"))):
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                by_bid[int(r["bid"])] = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
    return [by_bid[b] for b in sorted(by_bid)]


def rep(rows, title):
    if not rows:
        print("%-14s (no trades)" % title); return
    w = sum(1 for r in rows if r["outcome"] == "win"); l = sum(1 for r in rows if r["outcome"] == "loss")
    net = sum(r["net"] for r in rows); pos = sum(r["net"] for r in rows if r["net"] > 0)
    neg = -sum(r["net"] for r in rows if r["net"] < 0)
    n = w + l
    wr = 100.0 * w / n if n else 0.0
    pf = pos / neg if neg > 1e-9 else float("inf")
    mr = statistics.mean(r["risk_pct"] for r in rows)                 # mean stop distance % (per-side)
    # NET (fee-inclusive) break-even win rate: p*(RR*r - f) + (1-p)*(-r - f) = 0
    rbar = mr / 100.0; f = FEE / 100.0
    p_be = (rbar + f) / (rbar * (1 + RR))
    p_val = binom_sf(w, n, p_be) if n else 1.0                        # one-sided: wins >= observed under net-BE null
    # t-stat for mean net per trade vs 0
    mean_net = net / len(rows)
    sd = statistics.pstdev(r["net"] for r in rows) if len(rows) > 1 else 0.0
    tstat = mean_net / (sd / math.sqrt(len(rows))) if sd > 0 else 0.0
    print("%-10s n=%-4d W=%-4d L=%-4d  win%%=%.1f%%  avg net=%+.3f%%  sum=%+.1f%%  PF=%s  stop=%.3f%%  netBE=%.1f%%  binom_p=%.3f  t=%+.2f" % (
        title, len(rows), w, l, wr, mean_net, net, ("inf" if pf == float("inf") else "%.2f" % pf),
        mr, 100 * p_be, p_val, tstat))


def main():
    t0 = time.time()
    print("loading all 15m with footprints ...", flush=True)
    buck = load_15m()
    n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close_price", b.get("close", 0.0)) or 0.0) for b in buck]
    print("  %d buckets, span %s .. %s   (%.0fs)" % (
        n, dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date(), time.time() - t0), flush=True)

    absorp = []
    for i in range(n):
        try:
            absorp.append(ABS.absorption(buck, i)[0])
        except Exception:
            absorp.append(None)
    ac_bar = {e["i"]: e["side"] for e in AC.detect(buck, skip_last=False, absorp=absorp) if e["kind"] == "cm"}
    print("  cyan/magenta candles: %d   (%.0fs)" % (len(ac_bar), time.time() - t0), flush=True)

    trades = []; next_free = W
    for t in sorted(ac_bar):
        if t < W or t >= n - 5 or t < next_free:
            continue
        candle_side = ac_bar[t]                          # cyan=+1 / magenta=-1
        win = buck[t - W:t + 1]
        zones = S.detect(win) or []
        b = S.bias(win, zones=zones)                     # take the trade WITH the LVN bias
        if not b or b["dir"] is None:
            continue
        bias_side = 1 if b["dir"] == "long" else -1
        if bias_side != candle_side:                     # candle must agree with the bias (with-trend) -> else skip
            continue
        dirside = candle_side
        entry = C[t]; lo = L[t]; hi = H[t]
        aligned = [z for z in zones if z["ends_high"] == (dirside > 0)]   # cyan->support / magenta->resistance
        touched = [z for z in aligned if lo <= z["zhi"] and hi >= z["zlo"]]
        if not touched:
            continue
        zone = min(touched, key=lambda z: abs(z["lvn"] - entry))          # nearest touched LVN zone
        ent_lvn = zone["lvn"]
        sl0 = ent_lvn * (1 - SL_PAD) if dirside > 0 else ent_lvn * (1 + SL_PAD)   # SL 0.1% BEYOND the entry LVN
        if (dirside > 0 and sl0 >= entry) or (dirside < 0 and sl0 <= entry):      # LVN on wrong side of entry -> skip
            continue
        risk = abs(entry - sl0)
        tp = entry + dirside * RR * risk                                       # TP 1:2
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
        yr = dt.datetime.utcfromtimestamp(float(buck[t]["start_time"])).year
        trades.append(dict(side=dirside, outcome=outcome, net=net, risk_pct=risk / entry * 100.0, year=yr))
        next_free = exitbar
    print("\ntotal trades: %d   (%.0fs)\n" % (len(trades), time.time() - t0), flush=True)
    rep(trades, "ALL")
    rep([r for r in trades if r["side"] > 0], "LONG")
    rep([r for r in trades if r["side"] < 0], "SHORT")
    print("  -- year split (durability) --")
    rep([r for r in trades if r["year"] == 2025], "2025")
    rep([r for r in trades if r["year"] == 2026], "2026")


if __name__ == "__main__":
    main()
