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

def cor_cache(tag):
    return os.path.join(OUT, "rr_pullback_bkt_cor_%s.json" % tag)


CLOCK_NPZ = os.path.join(OUT, "clock1m_ohlc.npz")
PARENTS_JSON = os.path.join(OUT, "rr_pullback_parents.json")


def prep_clock_npz():
    """Extract the 1m CLOCK OHLC arrays into a tiny npz — run in a SUBPROCESS so the multi-GB
    dict load never shares a heap with the bucket archive (the combined load segfaulted 2026-09-03)."""
    from study.archive_loader import load_archive
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1],
                key=lambda b: _f(b.get("start_time", 0)))
    np.savez(CLOCK_NPZ,
             t=np.array([_f(b.get("start_time")) for b in A1]),
             h=np.array([_f(b.get("high")) for b in A1]),
             l=np.array([_f(b.get("low")) for b in A1]),
             c=np.array([_f(b.get("close", b.get("close_price"))) for b in A1]))
    print("clock npz saved: %d bars" % len(A1), flush=True)
    del A1

    f15 = fires_15m_clock()
    f30 = json.load(open(CACHE_30))
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
    json.dump({"p30": p30, "p15": p15}, open(PARENTS_JSON, "w"))
    print("parents prepped: 30m %d · 15m %d" % (len(p30), len(p15)), flush=True)


CKPT_EVERY = 500                  # checkpoint the corridor build every N parents (crash/kill-safe)
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


def build_corridors_bkt(tag, plist, AB, TBE, T1S, H1, L1):
    """RECORD-EVERYTHING corridor replay for ONE parent tag (agreed 2026-09-03: one expensive
    pass, then every rule variant is post-processing). Aborts on the 1m CLOCK truth path; child =
    1m BUCKET union replay over the FULL corridor (no early stop). Each corridor record keeps
    EVERY first-appearance child union fire: {pt,s,pe,plo,phi,al,end,fires:[(t,side,e,sl)]}.
    Checkpoints every CKPT_EVERY parents to <cache>.part and resumes from it after a kill."""
    from app import config, radar_breakout_detect as RB
    n1 = len(T1S)
    part = cor_cache(tag) + ".part"
    cors = []
    start = 0
    if os.path.exists(part):
        st = json.load(open(part))
        cors = st["cors"]
        start = st["done"]
        print("  [%s] RESUME from checkpoint: %d parents done" % (tag, start), flush=True)
    t0 = time.time()
    detects = 0
    if True:
        for pi, (et, s, pe, plo, phi, al) in enumerate(plist):
            if pi < start:
                continue
            if pi % 400 == 0:
                print("  [%s] parent %d/%d  (detects %d, %.0fs)" % (tag, pi, len(plist), detects,
                                                                    time.time() - t0), flush=True)
            if pi and pi % CKPT_EVERY == 0 and pi > start:
                json.dump({"done": pi, "cors": cors}, open(part, "w"))
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
    json.dump(cors, open(cor_cache(tag), "w"))
    if os.path.exists(part):
        os.remove(part)
    return cors


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

    if not (os.path.exists(CLOCK_NPZ) and os.path.exists(PARENTS_JSON)):
        import subprocess                          # heavy dict loads quarantined in a subprocess
        subprocess.check_call([sys.executable, os.path.abspath(__file__), "--prep-clock"])
    pj = json.load(open(PARENTS_JSON))
    p30 = [tuple(x) for x in pj["p30"]]
    p15 = [tuple(x) for x in pj["p15"]]
    print("parents: 30m bucket %d · 15m clock %d" % (len(p30), len(p15)), flush=True)
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]
    print("1m clock arrays ready: %d bars (%.0fs)" % (len(T1S), time.time() - t0), flush=True)

    tags = (("30mBKT", p30), ("15mCLK", p15))
    AB = TBE = None
    if any(not os.path.exists(cor_cache(t)) for t, _ in tags):
        AB = sorted(load_archive("1m", root="study/recon_archive")[1],
                    key=lambda b: _f(b.get("start_time", 0)))
        TBE = np.array([_f(b.get("end_time")) or (_f(b.get("start_time")) + 60.0) for b in AB])
        print("1m BUCKET bars: %d  (%.0fs)" % (len(AB), time.time() - t0), flush=True)

    for tag, plist in tags:                        # each half reports the moment it completes
        if os.path.exists(cor_cache(tag)):
            cors = json.load(open(cor_cache(tag)))
            print("[%s] corridors from cache: %d" % (tag, len(cors)), flush=True)
        else:
            cors = build_corridors_bkt(tag, plist, AB, TBE, T1S, H1, L1)
        trades, mix = select_trades(cors)
        tot = sum(mix.values())
        n_al = sum(1 for x in trades if x.get("al") is True)
        n_ag = sum(1 for x in trades if x.get("al") is False)
        print("\n" + "=" * 132, flush=True)
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
        print("### %s REPORT COMPLETE ###" % tag, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    if "--prep-clock" in sys.argv:
        prep_clock_npz()
    else:
        main()
