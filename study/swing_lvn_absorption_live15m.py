"""FRESH OOS: the exact 15m with-bias LVN strategy, run on the LIVE DAEMON cold-archive (study/archive_data/15m)
— real 2026 buckets captured 6-hourly, span 2026-06-20 .. 2026-07-30, entirely AFTER the recon cutoff (2026-06-19).

Identical rules to swing_lvn_absorption_15m.py: enter on a CYAN/MAGENTA absorption candle (engulf1m 'cm', |A|>=2) AT
the matching LVN zone AND WITH the LVN bias; SL 0.1% beyond the entry-side LVN; TP 1:2. Causal, non-overlap, 0.1% fee.
This is out-of-sample vs everything tuned so far — if the 15m result is a 2025 regime artifact it should NOT hold here.
"""
import json, os, sys, time, statistics, math, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "study"))
from app import swing_lvn_detect as S
from app import engulf1m_detect as AC
from app import absorption as ABS
from archive_loader import load_archive

W = 800
SL_PAD = 0.001         # SL 0.1% beyond the entry-side LVN
RR = 2.0               # TP 1:2
FEE = 0.1
MAXH = 96


def binom_sf(k, n, p):
    return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))


def rep(rows, title):
    if not rows:
        print("%-10s (no trades)" % title); return
    w = sum(1 for r in rows if r["outcome"] == "win"); l = sum(1 for r in rows if r["outcome"] == "loss")
    net = sum(r["net"] for r in rows); pos = sum(r["net"] for r in rows if r["net"] > 0)
    neg = -sum(r["net"] for r in rows if r["net"] < 0)
    n = w + l
    wr = 100.0 * w / n if n else 0.0
    pf = pos / neg if neg > 1e-9 else float("inf")
    mr = statistics.mean(r["risk_pct"] for r in rows)
    rbar = mr / 100.0; f = FEE / 100.0
    p_be = (rbar + f) / (rbar * (1 + RR))
    p_val = binom_sf(w, n, p_be) if n else 1.0
    mean_net = net / len(rows)
    sd = statistics.pstdev(r["net"] for r in rows) if len(rows) > 1 else 0.0
    tstat = mean_net / (sd / math.sqrt(len(rows))) if sd > 0 else 0.0
    print("%-10s n=%-4d W=%-4d L=%-4d  win%%=%.1f%%  avg net=%+.3f%%  sum=%+.1f%%  PF=%s  stop=%.3f%%  netBE=%.1f%%  binom_p=%.3f  t=%+.2f" % (
        title, len(rows), w, l, wr, mean_net, net, ("inf" if pf == float("inf") else "%.2f" % pf),
        mr, 100 * p_be, p_val, tstat))


def main():
    t0 = time.time()
    print("loading LIVE daemon 15m cold-archive ...", flush=True)
    _, raws, _ = load_archive("15m")
    buck = [json.loads(r) if isinstance(r, str) else r for r in raws]
    buck.sort(key=lambda x: float(x["start_time"]))
    n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close_price", b.get("close", 0.0)) or 0.0) for b in buck]
    print("  %d buckets, span %s .. %s   (%.0fs)" % (
        n, dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).isoformat(timespec="minutes"),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).isoformat(timespec="minutes"), time.time() - t0), flush=True)

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
        candle_side = ac_bar[t]
        win = buck[t - W:t + 1]
        zones = S.detect(win) or []
        b = S.bias(win, zones=zones)
        if not b or b["dir"] is None:
            continue
        bias_side = 1 if b["dir"] == "long" else -1
        if bias_side != candle_side:
            continue
        dirside = candle_side
        entry = C[t]; lo = L[t]; hi = H[t]
        aligned = [z for z in zones if z["ends_high"] == (dirside > 0)]
        touched = [z for z in aligned if lo <= z["zhi"] and hi >= z["zlo"]]
        if not touched:
            continue
        zone = min(touched, key=lambda z: abs(z["lvn"] - entry))
        ent_lvn = zone["lvn"]
        sl0 = ent_lvn * (1 - SL_PAD) if dirside > 0 else ent_lvn * (1 + SL_PAD)
        if (dirside > 0 and sl0 >= entry) or (dirside < 0 and sl0 <= entry):
            continue
        risk = abs(entry - sl0)
        tp = entry + dirside * RR * risk
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
        mon = dt.datetime.utcfromtimestamp(float(buck[t]["start_time"])).strftime("%Y-%m")
        trades.append(dict(side=dirside, outcome=outcome, net=net, risk_pct=risk / entry * 100.0, mon=mon))
        next_free = exitbar
    print("\ntotal trades: %d   (%.0fs)\n" % (len(trades), time.time() - t0), flush=True)
    rep(trades, "ALL")
    rep([r for r in trades if r["side"] > 0], "LONG")
    rep([r for r in trades if r["side"] < 0], "SHORT")
    print("  -- month split --")
    for m in sorted({r["mon"] for r in trades}):
        rep([r for r in trades if r["mon"] == m], m)


if __name__ == "__main__":
    main()
