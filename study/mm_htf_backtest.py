"""1h Mov.Magnitude>=50 SL/TP backtest.

RULE (operator, 2026-07-18):
  Signal : a 1h constant-volume bar with Mov.Magnitude >= 50  (~ |close-vs-far-extreme| >= 0.707%).
  Entry  : at the CLOSE of the signal bar (market).
  Stop   : below the LOW (long) / above the HIGH (short) of the signal bar  [+1 tick buffer].
  Target : R:R = 1 : 0.5  ->  TP distance = 0.5 x SL distance  (SL~1% -> TP~0.5%).
  Exit   : walk forward bar-by-bar; first of {TP high/low touch, SL high/low touch} wins.

Mov.Magnitude (production): bull -> ref=low, bear -> ref=high, doji -> open;  ((c*100/ref-100)^2)*100.

CAUSAL: features at signal-bar close, forward path strictly after. Constant-volume bars => 'hold' in BARS.
Ambiguity: when a single later bar spans BOTH TP and SL, order is unknowable without ticks -> report the
BAND: SL-first (pessimistic) and TP-first (optimistic). Break-even win-rate at R:R 1:0.5 is 66.7% gross.

Run:  python study/mm_htf_backtest.py
"""
from __future__ import annotations
import os, sys, statistics, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.archive_loader import load_archive

TICK = 0.01
MM_THR = 50.0
RR = 0.5                       # reward as fraction of risk
# total round-trip COST %: 0.04 = fee-only optimistic; 0.08 = fee+light slippage (realistic);
# 0.12 = fee+slippage on a market entry into a hot bar (pessimistic).
FEES = (0.0, 0.04, 0.08, 0.12)
MATURE_TV = 100000.0           # target_vol floor: exclude cold-start warmup (tv pinned at 5000)


def mov_mag(o, h, l, c):
    ref = l if c > o else (h if c < o else o)
    return (((c * 100.0 / ref) - 100.0) ** 2) * 100.0 if ref > 0 else 0.0


def load_bars():
    _, raws, _ = load_archive("1h")
    B = []
    for r in raws:
        o = r.get("open_price"); h = r.get("high"); l = r.get("low"); c = r.get("close_price")
        bv = r.get("buy_vol", 0.0) or 0.0; sv = r.get("sell_vol", 0.0) or 0.0
        cv = r.get("curr_vol", 0.0) or 0.0
        tv = r.get("target_vol", 0.0) or 0.0
        st = r.get("start_time", 0.0)
        if not o or not c or o <= 0 or h is None or l is None:
            continue
        B.append(dict(o=o, h=h, l=l, c=c, bv=bv, sv=sv, cv=cv, tv=tv, st=st,
                      mm=mov_mag(o, h, l, c), up=c > o, dn=c < o,
                      dpct=((bv - sv) / cv * 100.0) if cv > 0 else 0.0))
    # keep the mature-target_vol tail only (drop cold-start warmup); walk-forward paths stay clean
    first = next((i for i in range(len(B))
                  if B[i]["tv"] >= MATURE_TV
                  and statistics.median([b["tv"] for b in B[i:i + 30]]) >= MATURE_TV), len(B))
    return B[first:]


def binom_p(k, n, p0):
    """two-sided normal approx of P(win-rate != p0) for k wins of n at null p0."""
    if n < 10:
        return float("nan")
    se = math.sqrt(p0 * (1 - p0) / n)
    z = (k / n - p0) / se if se > 0 else 0.0
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def simulate(B, i, side, both_first):
    """side: +1 long / -1 short.  both_first: 'sl' or 'tp' resolution when one bar spans both.
    Returns (outcome, gross_ret_pct, bars_held, sl_dist_pct)."""
    b = B[i]; entry = b["c"]
    if side > 0:
        sl = b["l"] - TICK
        sl_dist = entry - sl
        tp = entry + RR * sl_dist
    else:
        sl = b["h"] + TICK
        sl_dist = sl - entry
        tp = entry - RR * sl_dist
    if sl_dist <= 0:
        return None
    sl_pct = sl_dist / entry * 100.0
    for j in range(i + 1, len(B)):
        hi = B[j]["h"]; lo = B[j]["l"]
        if side > 0:
            hit_tp = hi >= tp; hit_sl = lo <= sl
        else:
            hit_tp = lo <= tp; hit_sl = hi >= sl
        if hit_tp and hit_sl:
            win = (both_first == "tp")
            return ("TP" if win else "SL", (RR if win else -1.0) * sl_pct, j - i, sl_pct)
        if hit_tp:
            return ("TP", RR * sl_pct, j - i, sl_pct)
        if hit_sl:
            return ("SL", -1.0 * sl_pct, j - i, sl_pct)
    # unresolved at data end -> mark to market at last close
    mtm = (B[-1]["c"] - entry) / entry * 100.0 * side
    return ("OPEN", mtm, len(B) - 1 - i, sl_pct)


def run(B, direction, mm_thr, both_first, delta_gate=None):
    """direction: 'cont' (bull->long/bear->short) or 'fade'. delta_gate: None|'agree'|'oppose'."""
    trades = []
    for i in range(len(B) - 1):
        b = B[i]
        if b["mm"] < mm_thr or (not b["up"] and not b["dn"]):
            continue
        if delta_gate == "agree" and not ((b["up"] and b["dpct"] > 0) or (b["dn"] and b["dpct"] < 0)):
            continue
        if delta_gate == "oppose" and not ((b["up"] and b["dpct"] < 0) or (b["dn"] and b["dpct"] > 0)):
            continue
        base_side = +1 if b["up"] else -1
        side = base_side if direction == "cont" else -base_side
        res = simulate(B, i, side, both_first)
        if res:
            trades.append((i,) + res)
    return trades


def stats(trades, fee):
    n = len(trades)
    if not n:
        return dict(n=0)
    res = [t[1] for t in trades]           # outcome label
    gr = [t[2] for t in trades]            # gross %
    nets = [g - fee for g in gr]
    tp = sum(1 for t in trades if t[1] == "TP")
    sl = sum(1 for t in trades if t[1] == "SL")
    op = sum(1 for t in trades if t[1] == "OPEN")
    resolved = tp + sl
    win = 100.0 * tp / resolved if resolved else float("nan")
    held = statistics.median(t[3] for t in trades)
    sld = statistics.mean(t[4] for t in trades)
    return dict(n=n, tp=tp, sl=sl, op=op, win=win, exp_g=sum(gr)/n, exp_n=sum(nets)/n,
                tot_n=sum(nets), held=held, sl_dist=sld)


def report_block(B, title, mm_thr, delta_gate=None):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)
    for direction in ("cont", "fade"):
        dl = "CONTINUATION (bull->LONG SL<low, bear->SHORT SL>high)" if direction == "cont" \
             else "FADE (bull->SHORT SL>high, bear->LONG SL<low)"
        print(f"\n  {dl}")
        for both in ("sl", "tp"):
            tr = run(B, direction, mm_thr, both, delta_gate)
            s = stats(tr, 0.0)
            band = "SL-first (pessimistic)" if both == "sl" else "TP-first (optimistic)"
            if not s["n"]:
                print(f"    {band:24s}  no trades"); continue
            resolved = s["tp"] + s["sl"]
            p = binom_p(s["tp"], resolved, 2.0 / 3.0)
            line = (f"    {band:24s} n={s['n']:<3} TP={s['tp']:<3} SL={s['sl']:<3} OPEN={s['op']:<2} "
                    f"win={s['win']:5.1f}%  avgSL={s['sl_dist']:.2f}%  medHold={s['held']:.0f}bar  "
                    f"p(vs66.7%)={p:.3f}")
            print(line)
            fees_str = "   ".join(
                f"cost{f:.2f}: exp={stats(tr, f)['exp_n']:+.3f}%/t tot={stats(tr, f)['tot_n']:+.1f}%"
                for f in FEES)
            print("      " + fees_str)
    print("    [break-even win-rate @ R:R 1:0.5 = 66.7% (== driftless random-walk win-rate); "
          "above => real drift]")


def main():
    B = load_bars()
    span = (B[-1]["st"] - B[0]["st"]) / 86400
    print(f"MATURE 1h bars: {len(B)}   span: {span:.1f} days   (cold-start warmup dropped, tv>= {MATURE_TV:.0f})")
    sig = [b for b in B if b["mm"] >= MM_THR and (b["up"] or b["dn"])]
    print(f"MM>=50 signals: {len(sig)}  ({100*len(sig)/len(B):.1f}%)   "
          f"bull={sum(1 for b in sig if b['up'])} bear={sum(1 for b in sig if b['dn'])}")

    report_block(B, "PRIMARY  --  MM>=50, all signals", MM_THR)

    # THE CONTROL: same continuation geometry on ALL directional bars (does MM>=50 beat generic?)
    print("\n" + "=" * 96)
    print("CONTROL  --  continuation on ALL directional bars (any MM) vs MM>=50  [SL-first, cost 0.04]")
    print("=" * 96)
    for thr, lbl in ((0.0, "ALL dir bars (control)"), (MM_THR, "MM>=50")):
        tr = run(B, "cont", thr, "sl"); s = stats(tr, 0.04)
        resolved = s["tp"] + s["sl"]; p = binom_p(s["tp"], resolved, 2.0 / 3.0)
        print(f"  {lbl:24s} n={s['n']:<4} win={s['win']:5.1f}%  p(vs66.7%)={p:.3f}  "
              f"avgSL={s['sl_dist']:.3f}%  exp_net={s['exp_n']:+.3f}%/t  tot={s['tot_n']:+.1f}%")
    print("  (if control win% ~ MM>=50 win%, the hit-rate edge is generic momentum, not MM-specific;")
    print("   MM>=50 still differs by making avgSL large enough that fixed fees hurt proportionally less)")

    # threshold sensitivity
    print("\n" + "=" * 96); print("MM THRESHOLD SENSITIVITY  (continuation, SL-first, cost 0.04)"); print("=" * 96)
    for thr in (0, 20, 30, 50, 75, 100, 150):
        tr = run(B, "cont", thr, "sl"); s = stats(tr, 0.04)
        if s["n"]:
            resolved = s["tp"] + s["sl"]; p = binom_p(s["tp"], resolved, 2.0 / 3.0)
            print(f"  MM>={thr:<4} n={s['n']:<4} win={s['win']:5.1f}%  p={p:.3f}  "
                  f"exp_net={s['exp_n']:+.3f}%/t  tot={s['tot_n']:+.1f}%  avgSL={s['sl_dist']:.2f}%")

    # delta gate
    print("\n" + "=" * 96); print("DELTA GATE  (continuation, MM>=50, SL-first, cost 0.04)"); print("=" * 96)
    for g, lbl in ((None, "all"), ("agree", "delta AGREES w/ dir"), ("oppose", "delta OPPOSES (absorb)")):
        tr = run(B, "cont", MM_THR, "sl", g); s = stats(tr, 0.04)
        if s["n"]:
            print(f"  {lbl:24s} n={s['n']:<3} win={s['win']:5.1f}%  exp_net={s['exp_n']:+.3f}%/t  tot={s['tot_n']:+.1f}%")

    # split-half stability (mature region halves + thirds -> regime robustness)
    print("\n" + "=" * 96); print("SPLIT STABILITY  (continuation, MM>=50, SL-first, cost 0.04)"); print("=" * 96)
    for k, name in ((2, "half"), (3, "third")):
        step = len(B) // k
        for j in range(k):
            lo = j * step; hi = len(B) if j == k - 1 else (j + 1) * step
            sub = B[lo:hi]
            d0 = dt_str(sub[0]["st"]); d1 = dt_str(sub[-1]["st"])
            tr = run(sub, "cont", MM_THR, "sl"); s = stats(tr, 0.04)
            if s["n"]:
                print(f"  {name} {j+1}/{k} [{d0}->{d1}]: n={s['n']:<3} win={s['win']:5.1f}%  "
                      f"exp_net={s['exp_n']:+.3f}%/t  tot={s['tot_n']:+.1f}%")
        print()


def dt_str(ts):
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(ts).strftime("%m-%d")


if __name__ == "__main__":
    main()
