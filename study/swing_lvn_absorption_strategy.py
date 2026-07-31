"""Causal backtest — LVN-bias + Absorption-candle-at-LVN strategy (the user's spec):

ENTRY (5m, causal; bias/zones over the trailing W buckets like the live terminal):
  * LVN bias confidence >= 0.60
  * the LAST candle is an ABSORPTION-CANDLE (app/engulf1m_detect) WITH the trend (its side == bias dir)
  * that candle is AT the ALIGNED LVN zone (long -> a support zone / short -> a resistance zone it touches)
EXIT:
  * SL = 0.1% beyond that zone's LVN (below for long / above for short)
  * TP  = 1:1.2 the stop distance
  * move SL to ENTRY (breakeven) once price reaches the OPPOSITE LVN zone
Net% = the move minus a 0.1% round-trip fee. Non-overlapping (one trade at a time).
"""
import gzip, json, glob, os, sys, time, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import swing_lvn_detect as S
from app import engulf1m_detect as AC
from app import absorption as ABS

NCHUNKS = 6
W = 800               # trailing window the bias/zones see
CONF_MIN = 0.60
RR = 1.2
SL_PAD = 0.001        # 0.1% beyond the LVN
FEE = 0.1             # round-trip %, 0.05%/side
MAXH = 240            # give a trade up to this many bars to resolve


def load_contiguous(nchunks):
    root = os.path.join(ROOT, "study", "recon_archive", "5m")
    want = nchunks * 10000
    by_bid = {}
    for fn in sorted(glob.glob(os.path.join(root, "5m_*.jsonl.gz"))):
        base = os.path.basename(fn)[3:-9]
        try:
            lo, _hi = (int(x) for x in base.split("_"))
        except ValueError:
            continue
        if lo > want:
            continue
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line); bid = int(r["bid"])
                if bid <= want:
                    by_bid[bid] = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
    return [by_bid[b] for b in sorted(by_bid)]


def main():
    t0 = time.time()
    print("loading %d chunks with footprints ..." % NCHUNKS, flush=True)
    buck = load_contiguous(NCHUNKS)
    n = len(buck)
    H = [float(b.get("high", 0.0) or 0.0) for b in buck]
    L = [float(b.get("low", 0.0) or 0.0) for b in buck]
    C = [float(b.get("close_price", b.get("close", 0.0)) or 0.0) for b in buck]
    import datetime as dt
    print("  %d buckets, span %s .. %s   (%.0fs)" % (
        n, dt.datetime.utcfromtimestamp(float(buck[0]["start_time"])).date(),
        dt.datetime.utcfromtimestamp(float(buck[-1]["start_time"])).date(), time.time() - t0), flush=True)

    # absorption (trailing-30, causal) then absorption-candles over the whole series (local/causal)
    absorp = []
    for i in range(n):
        try:
            absorp.append(ABS.absorption(buck, i)[0])
        except Exception:
            absorp.append(None)
    ac = AC.detect(buck, skip_last=False, absorp=absorp)          # [{i, side, kind, a}]
    ac_bar = {}
    for e in ac:
        ac_bar[e["i"]] = e["side"]
    print("  absorption candles: %d   (%.0fs)" % (len(ac_bar), time.time() - t0), flush=True)

    trades = []
    considered = 0
    next_free = W
    for t in sorted(ac_bar):
        if t < W or t >= n - 5 or t < next_free:
            continue
        side_ac = ac_bar[t]
        win = buck[t - W:t + 1]
        zones = S.detect(win) or []
        b = S.bias(win, zones=zones)
        if not b or b["dir"] is None:
            continue
        dirside = 1 if b["dir"] == "long" else -1
        if dirside != side_ac or b["confidence"] < CONF_MIN:
            continue
        considered += 1
        entry = C[t]; lo = L[t]; hi = H[t]
        aligned = [z for z in zones if z["ends_high"] == (dirside > 0)]        # support(long)/resistance(short)
        touched = [z for z in aligned if lo <= z["zhi"] and hi >= z["zlo"]]
        if not touched:
            continue
        zone = min(touched, key=lambda z: abs(z["lvn"] - entry))
        lvn = zone["lvn"]
        sl0 = lvn * (1 - SL_PAD) if dirside > 0 else lvn * (1 + SL_PAD)
        if (dirside > 0 and sl0 >= entry) or (dirside < 0 and sl0 <= entry):
            continue                                                          # LVN not on the stop side -> skip
        risk = abs(entry - sl0); tp = entry + dirside * RR * risk
        opp = [z for z in zones if z["ends_high"] != (dirside > 0)]
        if dirside > 0:
            cand = [z["zlo"] for z in opp if z["zlo"] > entry]; opp_trig = min(cand) if cand else None
        else:
            cand = [z["zhi"] for z in opp if z["zhi"] < entry]; opp_trig = max(cand) if cand else None
        cur_sl = sl0; be = False; outcome = None; exitbar = None
        for j in range(t + 1, min(n, t + 1 + MAXH)):
            hj = H[j]; lj = L[j]
            if dirside > 0:
                if lj <= cur_sl:
                    outcome = "be" if be else "loss"; exitbar = j; break
                if hj >= tp:
                    outcome = "win"; exitbar = j; break
                if (not be) and opp_trig is not None and hj >= opp_trig:
                    be = True; cur_sl = entry
            else:
                if hj >= cur_sl:
                    outcome = "be" if be else "loss"; exitbar = j; break
                if lj <= tp:
                    outcome = "win"; exitbar = j; break
                if (not be) and opp_trig is not None and lj <= opp_trig:
                    be = True; cur_sl = entry
        if outcome is None:
            continue
        risk_pct = risk / entry * 100.0
        net = (RR * risk_pct - FEE) if outcome == "win" else (-FEE if outcome == "be" else -risk_pct - FEE)
        trades.append(dict(t=t, side=dirside, conf=b["confidence"], outcome=outcome, net=net,
                           risk_pct=risk_pct, exitbar=exitbar))
        next_free = exitbar
    print("\nconfidence>=%.0f%% + with-trend absorption candle: %d bars; AT aligned LVN + resolved -> %d trades   (%.0fs)\n"
          % (CONF_MIN * 100, considered, len(trades), time.time() - t0), flush=True)

    def rep(rows, title):
        if not rows:
            print("%-22s (no trades)" % title); return
        w = sum(1 for r in rows if r["outcome"] == "win"); l = sum(1 for r in rows if r["outcome"] == "loss")
        be = sum(1 for r in rows if r["outcome"] == "be")
        net = sum(r["net"] for r in rows); pos = sum(r["net"] for r in rows if r["net"] > 0)
        neg = -sum(r["net"] for r in rows if r["net"] < 0)
        wr = 100.0 * w / (w + l) if (w + l) else 0.0
        pf = pos / neg if neg > 1e-9 else float("inf")
        print("%-22s n=%-3d  W=%-3d L=%-3d BE=%-3d  win%%(W/(W+L))=%.1f%%  avg net=%+.3f%%  sum=%+.2f%%  PF=%s  avg risk=%.3f%%" % (
            title, len(rows), w, l, be, wr, net / len(rows), net, ("inf" if pf == float("inf") else "%.2f" % pf),
            statistics.mean(r["risk_pct"] for r in rows)))

    rep(trades, "ALL")
    rep([r for r in trades if r["side"] > 0], "LONG")
    rep([r for r in trades if r["side"] < 0], "SHORT")
    rep([r for r in trades if r["conf"] >= 0.70], "conf>=70%")
    print("\nBE (breakeven-scratch) rate: %d / %d = %.0f%%  (opposite-LVN stop-move saved these from full losses)" % (
        sum(1 for r in trades if r["outcome"] == "be"), len(trades),
        100.0 * sum(1 for r in trades if r["outcome"] == "be") / len(trades) if trades else 0))
    print("break-even win rate at 1:%.1f (gross) = %.1f%%" % (RR, 100.0 / (1 + RR)))


if __name__ == "__main__":
    main()
