"""RADAR RUNNER PULLBACK v2 — child = 1m VOLUME BUCKET badges; parent EMA50 side gate.

Same pre-registered corridor rules as study/radarrun_pullback_1m.py (user 2026-09-03), two changes
(user follow-up, frozen before any result):
  * CHILD = Radar Runner union fires on the 1m BUCKET scale (study/recon_archive/1m), union-persist
    replay (trailing W=2000 buckets, first-appearance freeze), badge SL (sl_buf 0.003). Corridor
    aborts still evaluated on the 1m CLOCK truth path (low < parent_low / high >= parent-badge TP2,
    ties AGAINST: a child badge must CLOSE at/before the aborting minute STARTS). Resolution
    unchanged: 1m clock first-touch.
  * EMA50 SIDE GATE on the PARENT: long parents kept only when the badge close > EMA50 of the
    parent's own series (30m bucket / 15m clock closes, causal at the badge bar); shorts only
    below. Reported AGAINST THE UNFILTERED CONTROL (ALL) plus the counter-aligned complement —
    a filter only counts if it beats ALL (standing rule; 4 EMA families died inverted).
PREDICTION ON RECORD: EMA alignment subtracts or inverts (ema20 / ema-stack / ema-bias / hl-delta
precedent); expectancy after costs <= 0 overall.
Harness: THIS file, extending study/radarrun_pullback_1m.py + the canonical gates.
RR_LOW_PRIO=1 python study/radarrun_pullback_1mbkt_ema.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

if os.environ.get("RR_LOW_PRIO") and os.name == "nt":
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)

from study.radarrun_pullback_1m import (_f, resolve, report_cell, fires_15m_clock,
                                        CACHE_30, W1, SLBUF, TP2G, CORR_CAP, EXITS, OUT)

CACHE_COR = os.path.join(OUT, "rr_pullback_bkt_corridors.json")
EMA_N = 50
EMA_WARM = 60                     # parents earlier than this bar index get aligned=None (excluded)


def ema_flags(fires, closes):
    """aligned[i] for fires[i]=(b, et, s, e, sl): side agrees with close-vs-EMA50 at the badge bar
    (causal — EMA of closes up to and including b; the fire itself is at b's close)."""
    a = 2.0 / (EMA_N + 1.0)
    ema = np.empty(len(closes))
    ema[0] = closes[0]
    for i in range(1, len(closes)):
        ema[i] = ema[i - 1] + a * (closes[i] - ema[i - 1])
    out = []
    for (b, et, s, e, sl) in fires:
        if b < EMA_WARM:
            out.append(None)
        else:
            out.append(bool((s > 0 and e > ema[b]) or (s < 0 and e < ema[b])))
    return out


def build_corridors_bkt(parents, AB, TBE, T1S, H1, L1):
    """RECORD-EVERYTHING corridor replay (agreed 2026-09-03: one expensive pass, then every rule
    variant is post-processing). Aborts on the 1m CLOCK truth path; child = 1m BUCKET union replay
    over the FULL corridor (no early stop). Each corridor record keeps EVERY first-appearance
    child union fire (both sides, in-range or not): {pt,s,pe,plo,phi,al,end,fires:[(t,side,e,sl)]}.
    parents: (tag, [(et,s,e,lo,hi,al), ...])."""
    from app import config, radar_breakout_detect as RB
    n1 = len(T1S)
    out = {}
    for tag, plist in parents:
        cors = []
        t0 = time.time()
        detects = 0
        for pi, (et, s, pe, plo, phi, al) in enumerate(plist):
            if pi % 400 == 0:
                print("  [%s] parent %d/%d  (detects %d, %.0fs)" % (tag, pi, len(plist), detects,
                                                                    time.time() - t0), flush=True)
            j0 = int(np.searchsorted(T1S, et - 0.5))
            if j0 >= n1:
                cors.append(dict(pt=et, s=int(s), pe=pe, plo=plo, phi=phi, al=al,
                                 end="no_bars", fires=[]))
                continue
            tp_far = pe * (1 + s * TP2G)
            jmax = min(n1, j0 + CORR_CAP)
            abort = None
            for j in range(j0, jmax):                       # truth-path corridor end (1m clock)
                if s > 0:
                    if L1[j] < plo:
                        abort = ("skip_range", j); break
                    if H1[j] >= tp_far:
                        abort = ("skip_tp", j); break
                else:
                    if H1[j] > phi:
                        abort = ("skip_range", j); break
                    if L1[j] <= tp_far:
                        abort = ("skip_tp", j); break
            t_hi = T1S[abort[1]] if abort else (T1S[jmax - 1] + 60.0 if jmax > j0 else et)
            k0 = int(np.searchsorted(TBE, et, side="right"))
            k1 = int(np.searchsorted(TBE, t_hi, side="right"))   # bucket must CLOSE <= abort start
            seen = {}
            for k in range(k0, k1):
                lo = max(0, k - W1)
                for g in RB.detect(AB[lo:k + 1], skip_last=False, sl_buf=SLBUF,
                                   tp_frac=config.RR_TP_FRAC):
                    b = lo + int(g["i"])
                    key = (b, g["side"])
                    if key in seen or b < k0 or b > k:
                        continue                            # union: freeze at FIRST appearance
                    seen[key] = (float(TBE[b]), int(g["side"]), float(g["entry"]),
                                 float(g["sl_trade"]))
                detects += 1
            cors.append(dict(pt=et, s=int(s), pe=pe, plo=plo, phi=phi, al=al,
                             end=(abort[0] if abort else ("expire" if k0 < k1 or j0 < jmax
                                                          else "no_bars")),
                             fires=sorted(seen.values())))
            del seen
        print("  [%s] done: %d corridors (detects %d, %.0fs)" % (tag, len(cors), detects,
                                                                 time.time() - t0), flush=True)
        out[tag] = cors
    return out


def select_trades(cors):
    """The user's pre-registered rule as post-processing: FIRST same-side fire whose badge close
    is inside [plo, phi]. Returns (trades, mix)."""
    trades = []
    mix = {"entered": 0, "skip_range": 0, "skip_tp": 0, "expire": 0, "no_bars": 0}
    for c in cors:
        hit = None
        for (t, side, e, sl) in c["fires"]:
            if side == c["s"] and c["plo"] <= e <= c["phi"]:
                hit = (t, e, sl)
                break
        if hit is not None:
            mix["entered"] += 1
            trades.append(dict(pt=c["pt"], t=hit[0], s=c["s"], e=hit[1], sl=hit[2], al=c["al"]))
        else:
            mix[c["end"]] += 1
    return trades, mix


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    print("RADAR RUNNER PULLBACK v2 — child 1m BUCKET badges | parent EMA50 side gate "
          "(vs unfiltered control) | same corridor rules\n", flush=True)
    t0 = time.time()

    f15 = fires_15m_clock()
    f30 = json.load(open(CACHE_30))
    print("parents: 30m bucket %d · 15m clock %d" % (len(f30), len(f15)), flush=True)

    A15 = sorted(load_archive("15m", root="study/clock_archive", drop_degenerate=False)[1],
                 key=lambda b: _f(b.get("start_time", 0)))
    c15 = np.array([_f(b.get("close", b.get("close_price"))) for b in A15])
    al15 = ema_flags(f15, c15)
    p15 = [(et, s, e, _f(A15[b].get("low")), _f(A15[b].get("high")), al15[i])
           for i, (b, et, s, e, sl) in enumerate(f15)]
    del A15
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1],
                 key=lambda b: _f(b.get("start_time", 0)))
    c30 = np.array([_f(b.get("close", b.get("close_price"))) for b in A30])
    al30 = ema_flags(f30, c30)
    p30 = [(et, s, e, _f(A30[b].get("low")), _f(A30[b].get("high")), al30[i])
           for i, (b, et, s, e, sl) in enumerate(f30)]
    del A30

    A1 = sorted(load_archive("1m", root="study/clock_archive")[1],
                key=lambda b: _f(b.get("start_time", 0)))
    T1S = np.array([_f(b.get("start_time")) for b in A1])
    H1 = np.array([_f(b.get("high")) for b in A1])
    L1 = np.array([_f(b.get("low")) for b in A1])
    C1 = np.array([_f(b.get("close", b.get("close_price"))) for b in A1])
    del A1
    print("1m clock arrays ready (%.0fs)" % (time.time() - t0), flush=True)

    if os.path.exists(CACHE_COR):
        res = json.load(open(CACHE_COR))
        print("corridor cache loaded", flush=True)
    else:
        AB = sorted(load_archive("1m", root="study/recon_archive")[1],
                    key=lambda b: _f(b.get("start_time", 0)))
        TBE = np.array([_f(b.get("end_time")) or (_f(b.get("start_time")) + 60.0) for b in AB])
        print("1m BUCKET bars: %d  (%.0fs)" % (len(AB), time.time() - t0), flush=True)
        res = build_corridors_bkt([("30mBKT", p30), ("15mCLK", p15)], AB, TBE, T1S, H1, L1)
        json.dump(res, open(CACHE_COR, "w"))
        del AB, TBE

    print("\n" + "=" * 132, flush=True)
    for tag in ("30mBKT", "15mCLK"):
        trades, mix = select_trades(res[tag])
        tot = sum(mix.values())
        n_al = sum(1 for x in trades if x.get("al") is True)
        n_ag = sum(1 for x in trades if x.get("al") is False)
        print("%s: parents %d -> entered %d (%.0f%%) · skip range %d · skip TP %d · expire %d | "
              "entered EMA-aligned %d / counter %d"
              % (tag, tot, mix["entered"], 100 * mix["entered"] / max(1, tot), mix["skip_range"],
                 mix["skip_tp"], mix["expire"], n_al, n_ag), flush=True)
        for sub_tag, sel in (("ALL", lambda x: True),
                             ("EMA-ALIGN", lambda x: x.get("al") is True),
                             ("EMA-CNTR", lambda x: x.get("al") is False)):
            subset = [x for x in trades if sel(x)]
            for ename, kind, val in EXITS:
                report_cell("%s %s" % (tag[:6], sub_tag), ename, subset, T1S, H1, L1, C1,
                            kind, val, mc, day_blocks)
            print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
