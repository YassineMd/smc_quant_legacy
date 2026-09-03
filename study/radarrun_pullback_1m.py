"""RADAR RUNNER PULLBACK ENTRY — parent fire (30m bucket / 15m clock) -> wait for a SAME-SIDE
1m-clock Radar Runner fire inside the parent signal candle's [low, high] -> enter THAT badge.

PRE-REGISTERED RULES (user 2026-09-03, frozen before any result):
  * Parent = union-persist Radar Runner fires: 30m bucket = the CANONICAL cached union
    (rr30mbkt_live_fires_union.json, RADARRUN_CANONICAL_TEST.md); 15m clock = same replay
    (per-close detect over trailing W=2000, first-appearance freeze), built+cached here.
  * After the parent bar closes, scan 1m clock bars. Corridor ends (SKIP, no trade) at the first
    1m bar with (bull) low < parent_low  OR  high >= parent badge TP2 = entry*(1+0.0044)
    ("reached the 0.4%% TP without a 1m signal"); bear mirrored (high > parent_high OR
    low <= entry*(1-0.0044)). The aborting bar cannot also deliver the entry (ties AGAINST).
    Corridor hard cap 1440 bars (24h) -> EXPIRE (counted; treated as skip).
  * ENTRY = the FIRST same-side 1m-clock union fire whose badge close lies INSIDE
    [parent_low, parent_high] (inclusive), badge bar inside the corridor. Union semantics on the
    child too: per-close detect over trailing W1=2000, signal accepted at FIRST appearance;
    replay stops once the corridor entered/aborted.
  * SL = the 1m badge's own sl_trade (candle-anchored, 0.3%% buffer, radar-capped) — exactly the
    click-the-badge bracket. EXITS tested: fix 0.24%%/0.44%% gross ("0.2%%"/"0.4%%" net, maker) and
    RR 1:1 / 1:1.5 / 1:2 on |entry-SL| (taker-out).
  * Resolution 1m first-touch, same-bar SL+TP -> SL (against). Fees 0.04%% RT + 0.03%% slip per
    taker leg. NON-OVERLAP taken() per exit. Eras 2025 / 2026H1 separate. Prop = HyroTrader $200k
    day-block MC, R0.4.
PREDICTION ON RECORD: 18 RadarRun conditioning families are dead; expectancy after costs expected
<= 0. This one differs (entry mechanics, not a filter), but the prior stands until daemon OOS.
Harness: THIS file (study/radarrun_pullback_1m.py), extending study/radarrun_30mbkt_live_full.py.
python study/radarrun_pullback_1m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

if os.environ.get("RR_LOW_PRIO") and os.name == "nt":
    # PROCESS_MODE_BACKGROUND_BEGIN: lowest CPU + I/O + memory priority — the study must never
    # starve the live terminal/bridge again (it froze the whole box on 2026-09-03).
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
CACHE_30 = os.path.join(OUT, "rr30mbkt_live_fires_union.json")     # canonical parent cache
CACHE_15C = os.path.join(OUT, "rr_union_c15m_live.json")           # 15m CLOCK parent union (built here)
CACHE_TR = os.path.join(OUT, "rr_pullback_trades.json")            # phase-2 result cache
W15, W1, SLBUF = 2000, 2000, 0.003
FEE, SLIP = 0.0004, 0.0003
TP2G = 0.0044                     # the parent badge's 0.4% TP (RR_TP2_FRAC gross)
CORR_CAP = 1440                   # corridor hard cap (24h of 1m bars)
CAPMIN = 20000
EXITS = (("0.2%", "fix", 0.0024), ("0.4%", "fix", 0.0044),
         ("RR1:1", "rr", 1.0), ("RR1:1.5", "rr", 1.5), ("RR1:2", "rr", 2.0))

_A15 = None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _init15():
    global _A15
    from study.archive_loader import load_archive
    _A15 = sorted(load_archive("15m", root="study/clock_archive", drop_degenerate=False)[1],
                  key=lambda b: _f(b.get("start_time", 0)))


def _work15(rng):
    """Union live-persist over 15m-clock closes [k0,k1): canonical _work, clock archive."""
    from app import config, radar_breakout_detect as RB
    k0, k1 = rng
    seen = {}
    for k in range(k0, k1):
        lo = max(0, k - W15)
        for g in RB.detect(_A15[lo:k + 1], skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            b = lo + int(g["i"])
            key = (b, g["side"])
            if key not in seen:
                seen[key] = (b, _f(_A15[b].get("end_time")), g["side"], g["entry"], g["sl_trade"], k)
    return list(seen.values())


def fires_15m_clock():
    if os.path.exists(CACHE_15C):
        return json.load(open(CACHE_15C))
    import multiprocessing as mp
    from study.archive_loader import load_archive
    n = len(load_archive("15m", root="study/clock_archive", drop_degenerate=False)[1])
    chunks = [(a, min(a + 500, n)) for a in range(1, n, 500)]
    best = {}
    with mp.Pool(4, initializer=_init15) as pool:
        for i, res in enumerate(pool.imap(_work15, chunks), 1):
            for (b, et, s, e, sl, fk) in res:
                cur = best.get((b, s))
                if cur is None or fk < cur[5]:
                    best[(b, s)] = (b, et, s, e, sl, fk)
            if i % 10 == 0 or i == len(chunks):
                print("  15m-clock union: chunk %d/%d, badges %d" % (i, len(chunks), len(best)), flush=True)
    byet = {}                      # terminal persists keyed by end_time: first signal on a bar wins
    for rec in sorted(best.values(), key=lambda r: (r[5], r[0])):
        if rec[1] not in byet:
            byet[rec[1]] = rec
    fires = sorted([(b, et, s, e, sl) for (b, et, s, e, sl, fk) in byet.values()])
    json.dump(fires, open(CACHE_15C, "w"))
    return fires


def build_trades(parents, A1, T1S, H1, L1, C1):
    """Phase 2: per parent fire, walk the corridor; child union replay stops at entry/abort.
    parents: list of (tag, [(et, side, entry, lo, hi), ...]). Returns trades + corridor mix."""
    from app import config, radar_breakout_detect as RB
    n1 = len(A1)
    out = {}
    for tag, plist in parents:
        trades = []
        mix = {"entered": 0, "skip_range": 0, "skip_tp": 0, "expire": 0, "no_bars": 0}
        t0 = time.time()
        detects = 0
        for pi, (et, s, pe, plo, phi) in enumerate(plist):
            if pi % 400 == 0:
                print("  [%s] parent %d/%d  (detects %d, %.0fs)" % (tag, pi, len(plist), detects,
                                                                    time.time() - t0), flush=True)
            j0 = int(np.searchsorted(T1S, et - 0.5))
            if j0 >= n1:
                mix["no_bars"] += 1
                continue
            tp_far = pe * (1 + s * TP2G)
            jmax = min(n1, j0 + CORR_CAP)
            entered = None
            seen = set()
            for j in range(j0, jmax):
                if s > 0:
                    if L1[j] < plo:                  # breached the candle low -> skip (ties against)
                        mix["skip_range"] += 1; break
                    if H1[j] >= tp_far:              # ran to the 0.4% TP without us -> skip
                        mix["skip_tp"] += 1; break
                else:
                    if H1[j] > phi:
                        mix["skip_range"] += 1; break
                    if L1[j] <= tp_far:
                        mix["skip_tp"] += 1; break
                lo = max(0, j - W1)
                cands = []
                for g in RB.detect(A1[lo:j + 1], skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
                    b = lo + int(g["i"])
                    key = (b, g["side"])
                    if key in seen or b < j0 or b > j:
                        continue
                    seen.add(key)
                    if g["side"] == s and plo <= g["entry"] <= phi:
                        cands.append((b, g["entry"], g["sl_trade"]))
                detects += 1
                if cands:
                    b, ce, csl = min(cands)          # earliest badge bar at this first appearance
                    entered = dict(pt=et, t=float(T1S[b] + 60.0), s=int(s), e=float(ce), sl=float(csl))
                    break
            else:
                if j0 < jmax:
                    mix["expire"] += 1
            if entered is not None:
                mix["entered"] += 1
                trades.append(entered)
        print("  [%s] done: %s  (detects %d, %.0fs)" % (tag, mix, detects, time.time() - t0), flush=True)
        out[tag] = dict(trades=trades, mix=mix)
    return out


def resolve(s, e, sl, t0, kind, val, T1S, H1, L1, C1):
    """Single-bracket 1m first-touch; ties against. Returns (net_frac, exit_time)."""
    risk = abs(e - sl)
    tp = e * (1 + s * val) if kind == "fix" else e + s * val * risk
    i0 = int(np.searchsorted(T1S, t0 - 0.5))
    for j in range(i0, min(len(T1S), i0 + CAPMIN)):
        sl_hit = (L1[j] <= sl) if s > 0 else (H1[j] >= sl)
        tp_hit = (H1[j] >= tp) if s > 0 else (L1[j] <= tp)
        if sl_hit:                                   # same-bar both -> SL (against)
            return -risk / e - FEE - 2 * SLIP, T1S[j] + 60.0
        if tp_hit:
            cost = (FEE + SLIP) if kind == "fix" else (FEE + 2 * SLIP)   # fix TPs maker, RR taker
            return abs(tp - e) / e - cost, T1S[j] + 60.0
    j = min(len(T1S) - 1, i0 + CAPMIN - 1)
    return s * (C1[j] - e) / e - FEE - 2 * SLIP, T1S[j] + 60.0


def report_cell(tag, ename, trades, T1S, H1, L1, C1, kind, val, mc, day_blocks):
    split = 1767225600.0
    rows = []
    busy = -1.0
    for tr in sorted(trades, key=lambda x: x["t"]):
        if tr["t"] < busy:
            continue
        risk = abs(tr["e"] - tr["sl"]) / tr["e"]
        if risk <= 0:
            continue
        net, tx = resolve(tr["s"], tr["e"], tr["sl"], tr["t"], kind, val, T1S, H1, L1, C1)
        rows.append(dict(t=tr["t"], s=tr["s"], net=net, r=net / risk))
        busy = tx
    if not rows:
        print("  %-10s %-8s n=0" % (tag, ename), flush=True)
        return
    nets = np.array([x["net"] for x in rows]); rs = np.array([x["r"] for x in rows])
    W = int((nets > 0.0002).sum()); L = int((nets < -0.0002).sum()); BE = len(rows) - W - L
    nl = sum(1 for x in rows if x["s"] > 0)
    days = day_blocks([(x["t"], x["net"], x["r"]) for x in rows])
    m4 = mc(days, 0.4, 4.0, "R")
    era = {}
    for lab, sel in (("25", lambda t: t < split), ("26", lambda t: t >= split)):
        sub = nets[[i for i, x in enumerate(rows) if sel(x["t"])]]
        era[lab] = ("n=%-4d %+.3f%%" % (len(sub), sub.mean() * 100)) if len(sub) else "n=0"
    mm = len({datetime.fromtimestamp(x["t"], tz=timezone.utc).strftime("%Y-%m") for x in rows})
    print("  %-10s %-8s n=%-4d (L%3d/S%3d) W/BE/L %4d/%3d/%4d win %5.1f%%  avg %+.3f%%  avgR %+.3f  "
          "prop %4.1f%%  mo:%d | 25 %s | 26 %s"
          % (tag, ename, len(rows), nl, len(rows) - nl, W, BE, L, 100 * W / len(rows),
             nets.mean() * 100, rs.mean(), m4["p"], mm, era["25"], era["26"]), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    print("RADAR RUNNER PULLBACK 1m — parents 30m bucket + 15m clock | corridor [low,high], "
          "abort beyond-range / badge-TP2 | child = 1m union badge, badge SL | 5 exits\n", flush=True)
    t0 = time.time()

    f15 = fires_15m_clock()
    print("15m clock union badges: %d" % len(f15), flush=True)
    f30 = json.load(open(CACHE_30))
    print("30m bucket union badges (canonical cache): %d" % len(f30), flush=True)

    A15 = sorted(load_archive("15m", root="study/clock_archive", drop_degenerate=False)[1],
                 key=lambda b: _f(b.get("start_time", 0)))
    p15 = [(et, s, e, _f(A15[b].get("low")), _f(A15[b].get("high"))) for (b, et, s, e, sl) in f15]
    del A15
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1],
                 key=lambda b: _f(b.get("start_time", 0)))
    p30 = [(et, s, e, _f(A30[b].get("low")), _f(A30[b].get("high"))) for (b, et, s, e, sl) in f30]
    del A30

    A1 = sorted(load_archive("1m", root="study/clock_archive")[1],
                key=lambda b: _f(b.get("start_time", 0)))
    T1S = np.array([_f(b.get("start_time")) for b in A1])
    H1 = np.array([_f(b.get("high")) for b in A1])
    L1 = np.array([_f(b.get("low")) for b in A1])
    C1 = np.array([_f(b.get("close", b.get("close_price"))) for b in A1])
    print("1m clock bars: %d  (load done %.0fs)" % (len(A1), time.time() - t0), flush=True)

    if os.path.exists(CACHE_TR):
        res = json.load(open(CACHE_TR))
        print("phase-2 trades from cache", flush=True)
    else:
        res = build_trades([("30mBKT", p30), ("15mCLK", p15)], A1, T1S, H1, L1, C1)
        json.dump(res, open(CACHE_TR, "w"))
    del A1

    print("\n" + "=" * 132, flush=True)
    for tag in ("30mBKT", "15mCLK"):
        mix = res[tag]["mix"]
        trades = res[tag]["trades"]
        tot = sum(mix.values())
        print("%s: parents %d -> entered %d (%.0f%%) · skip range-breach %d · skip ran-to-TP %d · "
              "expire24h %d" % (tag, tot, mix["entered"], 100 * mix["entered"] / max(1, tot),
                                mix["skip_range"], mix["skip_tp"], mix["expire"]), flush=True)
        for ename, kind, val in EXITS:
            report_cell(tag, ename, trades, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
