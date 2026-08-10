# -*- coding: utf-8 -*-
"""RE-DEFENSE edge test. The event = a bar where a fresh absorption/aggression fires that WOULD birth a wall but is
SUPPRESSED because it coincides (same side, <=0.3%) with an existing ACTIVE wall — i.e. price is at a wall and the
order-flow re-defends it. Question: does fading that level (R->short, S->long) from the re-defense bar's close have
an edge? Fully causal: the event uses only bar i; the outcome uses bars > i. Structural stop = the wall's radar edge
(a body-close beyond it is the wall's own invalidation). Both recon years, non-overlapping trades, exact-binomial p,
plus two controls: (a) ALL wall visits faded the same way (is the re-defense subset better than a generic touch?),
(b) a random-direction null (does the side matter?). Descriptive first; net-after-fee last (user asked for position edge)."""
import os, sys, math
os.chdir(r"C:\Users\Yassine Mdouari\Desktop\Coding\12. Trading Indicators\smc_quant_legacy")
sys.path.insert(0, os.getcwd())
from datetime import datetime, timezone
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

FEE = 0.0005            # per side (round trip = 0.10%)
H_BARS = 64             # forward barrier horizon (bars)
RRS = (1.0, 1.5, 2.0)   # TP as a multiple of the entry->SL distance
EPS2 = AL.EPS * 2.0     # same-side proximity that suppresses a new wall (0.3%)


def binom_p(w, n):
    """two-sided binomial vs p=0.5: exact for small n, normal approx (overflow-safe) for large n."""
    if n == 0:
        return 1.0
    if n <= 1000:
        from math import comb
        k = min(w, n - w)
        tail = sum(comb(n, j) for j in range(0, k + 1)) * (0.5 ** n)
        return min(1.0, 2.0 * tail)
    z = abs(w - n / 2.0) / math.sqrt(n / 4.0)               # normal approx (continuity-uncorrected)
    return math.erfc(z / math.sqrt(2.0))


def load(tf):
    _a = load_archive(tf, root="study/recon_archive")
    A = _a[1] if isinstance(_a, tuple) else _a
    A = sorted(A, key=lambda b: _f(b.get("start_time", 0)))
    return A


def redefense_events(A):
    """Causal replay of AL.detect's active-wall tracking; yield the SUPPRESSED re-detections (the re-defense events)
    AND, separately, EVERY radar visit's first bar (for the 'all visits' control). Returns (events, visits)."""
    n = len(A)
    O = [0.0] * n; C = [0.0] * n; Hi = [0.0] * n; Lo = [0.0] * n; DP = [0.0] * n
    for i, b in enumerate(A):
        O[i], C[i], Hi[i], Lo[i] = AL._ohlc(b)
        cv = _f(b.get("curr_vol"))
        if cv > 0:
            DP[i] = (_f(b.get("buy_vol")) - _f(b.get("sell_vol"))) / cv * 100.0
    vpct = [0.0] * n; _s = 0.0
    for i in range(n):
        _s += (Hi[i] - Lo[i]) / C[i] if C[i] > 0 else 0.0
        if i >= AL.ATR_WIN:
            _s -= (Hi[i - AL.ATR_WIN] - Lo[i - AL.ATR_WIN]) / C[i - AL.ATR_WIN] if C[i - AL.ATR_WIN] > 0 else 0.0
        vpct[i] = _s / min(i + 1, AL.ATR_WIN)
    active = []; events = []; visits = []
    for i in range(n):
        still = []
        for w in active:
            P = w["P"]
            if i - w["i0"] <= AL.EJ_WIN:
                fav = (P - Lo[i]) / P if w["side"] == "R" else (Hi[i] - P) / P
                if fav > w["ej"]:
                    w["ej"] = fav
            base = min(1.0, w["ej"] / (AL.EJ_ATR_MULT * w["v0"])) if w["v0"] > 0 else 0.0
            band = P * w["v0"] * (AL.BAND_MIN + base * AL.BAND_RANGE)
            r_lo = P - 3.0 * band; r_hi = P + 3.0 * band
            if (w["side"] == "R" and C[i] > r_hi) or (w["side"] == "S" and C[i] < r_lo):
                w["broken"] = True; continue                 # wall broken -> drop from active
            inside = (Lo[i] <= r_hi and Hi[i] >= r_lo)
            if inside:
                if not w["inzone"] and w["ever_left"]:
                    visits.append((i, w["side"], P, band))    # a fresh re-entry = a visit (control population)
                    w["inzone"] = True
                elif not w["ever_left"]:
                    w["inzone"] = True
            else:
                w["inzone"] = False; w["ever_left"] = True
            w["band"] = band
            still.append(w)
        active = still
        hit = AL._wall_at(i, O, C, Hi, Lo, DP, A)
        if hit is not None:
            price, side, src = hit
            near = None
            for w in active:
                if w["side"] == side and abs(w["P"] - price) <= price * EPS2:
                    near = w; break
            if near is None:
                active.append({"P": price, "side": side, "src": src, "i0": i, "ej": 0.0,
                               "v0": vpct[i], "inzone": True, "ever_left": False, "broken": False,
                               "band": price * vpct[i] * AL.BAND_MIN})
            else:
                # RE-DEFENSE: a fresh abs/agg suppressed at an existing same-side wall.
                events.append((i, near["side"], near["P"], near.get("band", price * vpct[i] * AL.BAND_MIN), src))
    return events, visits, (O, C, Hi, Lo)


def sim(pop, ohlc, A, rr, fade=True, flip=False):
    """Non-overlapping barrier sim. pop = [(i, side, P, band, ...)]. Fade: R->short, S->long (flip reverses).
    entry=C[i]; SL=wall radar edge on the far side; TP=rr x (entry->SL). Returns (n, wins, losses, scratch, net%)."""
    O, C, Hi, Lo = ohlc
    n = len(C)
    open_until = -1
    res = []
    for ev in pop:
        i, side, P, band = ev[0], ev[1], ev[2], ev[3]
        if i <= open_until or i + 1 >= n:
            continue
        entry = C[i]
        d = (-1 if side == "R" else 1)                        # fade direction
        if flip:
            d = -d
        # structural stop = the wall's radar edge (a close beyond = the wall itself is broken)
        sl_price = (P + 3.0 * band) if side == "R" else (P - 3.0 * band)
        sl_dist = abs(sl_price - entry)
        if sl_dist <= 0:
            continue
        tp_price = entry + d * rr * sl_dist
        outcome = None; exit_i = min(n - 1, i + H_BARS)
        for k in range(i + 1, min(n, i + 1 + H_BARS)):
            hit_sl = (Hi[k] >= sl_price) if d < 0 else (Lo[k] <= sl_price)
            hit_tp = (Lo[k] <= tp_price) if d < 0 else (Hi[k] >= tp_price)
            if hit_sl and hit_tp:                             # same bar -> assume SL first (conservative)
                outcome = "L"; exit_i = k; break
            if hit_sl:
                outcome = "L"; exit_i = k; break
            if hit_tp:
                outcome = "W"; exit_i = k; break
        if outcome == "W":
            gross = rr * sl_dist / entry
        elif outcome == "L":
            gross = -sl_dist / entry
        else:                                                 # timeout -> close at horizon
            gross = d * (C[exit_i] - entry) / entry
        net = gross - 2 * FEE
        res.append((net, outcome if outcome else ("W" if gross > 0 else "L")))
        open_until = exit_i                                   # non-overlap
    wins = sum(1 for _, o in res if o == "W")
    losses = sum(1 for _, o in res if o == "L")
    scratch = sum(1 for net, _ in res if abs(net) < 1e-9)
    netsum = sum(net for net, _ in res)
    return len(res), wins, losses, scratch, netsum * 100.0


def yr(A, i):
    return datetime.fromtimestamp(_f(A[i].get("start_time")), tz=timezone.utc).year


def run(tf):
    A = load(tf)
    events, visits, ohlc = redefense_events(A)
    print("\n================  TF=%s  buckets=%d  ================" % (tf, len(A)), flush=True)
    print("re-defense events: %d   |   all wall-visits: %d" % (len(events), len(visits)), flush=True)
    for ylabel, yfilt in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
        ev = [e for e in events if (yfilt is None or yr(A, e[0]) == yfilt)]
        vs = [v for v in visits if (yfilt is None or yr(A, v[0]) == yfilt)]
        if not ev:
            continue
        print("\n  --- %s (re-defense n=%d) ---" % (ylabel, len(ev)), flush=True)
        for rr in RRS:
            n, w, l, sc, net = sim(ev, ohlc, A, rr, fade=True)
            nv, wv, lv, scv, netv = sim(vs, ohlc, A, rr, fade=True)          # control: ALL visits faded
            nf, wf, lf, scf, netf = sim(ev, ohlc, A, rr, fade=True, flip=True)  # null: opposite direction
            wr = 100.0 * w / n if n else 0.0
            wrv = 100.0 * wv / nv if nv else 0.0
            p = binom_p(w, w + l)
            print("   RR 1:%.1f  redef n=%2d W%%=%5.1f net=%+6.2f%% p=%.3f  ||  allvisits n=%3d W%%=%5.1f net=%+6.2f%%  ||  flip net=%+6.2f%%"
                  % (rr, n, wr, net, p, nv, wrv, netv, netf), flush=True)
    # descriptive: does the wall HOLD the re-defense visit? (fade thesis = it holds -> price ejects back)
    O, C, Hi, Lo = ohlc
    for ylabel, yfilt in (("BOTH", None), ("2025", 2025), ("2026", 2026)):
        ev = [e for e in events if (yfilt is None or yr(A, e[0]) == yfilt)]
        if not ev:
            continue
        f5 = f10 = 0; tot = 0
        for i, side, P, band, _src in ev:
            if i + 10 >= len(C):
                continue
            d = (-1 if side == "R" else 1)
            r5 = d * (C[i + 5] - C[i]) / C[i]; r10 = d * (C[i + 10] - C[i]) / C[i]
            f5 += 1 if r5 > 0 else 0; f10 += 1 if r10 > 0 else 0; tot += 1
        if tot:
            print("   [%s] forward fade-direction UP-share: 5b=%4.1f%% 10b=%4.1f%% (n=%d)  <-- >50%% = the level rejects" %
                  (ylabel, 100.0 * f5 / tot, 100.0 * f10 / tot, tot), flush=True)


if __name__ == "__main__":
    for tf in ("15m", "1h", "5m"):
        try:
            run(tf)
        except Exception as e:
            import traceback; print("TF %s FAILED: %s" % (tf, e)); traceback.print_exc()
