"""BIG BODY directional-bias study on 15m CLOCK candles (user hypothesis 2026-09-03).

QUESTION (pre-specified, no mining): after a Big Body candle closes (body strictly > each of the
last 5 bodies, app/big_body_detect), does price continue in the candle's direction — and is the
bias conditioned on the close being above/below BOTH EMA20 and EMA50? Does the shipped bubble
filter (RR rule: clean close-side wick of bubbles + >=1 big/medium bubble on the trade side)
add anything on top?

DESIGN (honest-gates aware; DESCRIPTIVE — no tradeability verdict):
  - Signal side = candle direction. Outcomes = (a) next-bar direction agreement, (b) mean SIGNED
    close->close return at +1/+2/+4/+8 bars (% of signal close). No SL/TP sim — bias only.
  - BASELINE: the same measures over EVERY closed bar (side = that bar's own direction) — i.e.
    generic 1-bar momentum. A Big Body number only means something RELATIVE to this.
  - EMA condition baseline: every bar with the same EMA alignment — separates "EMA regime does it"
    from "Big Body adds something".
  - Eras reported separately: 2025 (recon), 2026H1 (recon), DAEMON (live store, ~14d).
  - Binomial two-sided p of agreement vs the matching baseline rate (normal approx). h>1 returns
    OVERLAP between nearby marks — means are fine, don't trust p there (flagged, not printed).
  - Fee context for any later trade idea: ~0.10% round trip + slippage.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import big_body_detect as bb
from app import crazy_wall_detect as cw
from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive

HORIZONS = (1, 2, 4, 8)


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    a = 2.0 / (n + 1.0)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = e + a * (vals[i] - e)
        out[i] = e
    return out


def bubble_keep(bars, thresholds, i, side):
    """The shipped RR bubble rule on the signal candle: side-clean wick + >=1 big/medium bubble on
    the trade side; can't-tier / no footprint -> KEPT (never silently hidden)."""
    th = thresholds[i] if 0 <= i < len(thresholds) else None
    if th is None:
        return True
    b = bars[i]
    o, c, _h, _l = _ohlc(b)
    if o <= 0 or c <= 0:
        return True
    bt, bbot = max(o, c), min(o, c)
    bubs = cw._bubbles(b)
    if not bubs:
        return False
    big = False
    for pr, tot, _bu, _se in bubs:
        if side > 0:
            if pr > bt:
                return False
            if tot >= th[0] and pr <= c:
                big = True
        else:
            if pr < bbot:
                return False
            if tot >= th[0] and pr >= c:
                big = True
    return big


def study(bars, label):
    n = len(bars)
    O = [0.0] * n; C = [0.0] * n
    for i, b in enumerate(bars):
        O[i], C[i], _, _ = _ohlc(b)
    e20 = ema(C, 20); e50 = ema(C, 50)
    marks = bb.detect(bars, skip_last=False)
    thr = cw.bubble_thresholds(bars)

    def align(i, side):
        if e20[i] is None or e50[i] is None:
            return "warm"
        above = C[i] > e20[i] and C[i] > e50[i]
        below = C[i] < e20[i] and C[i] < e50[i]
        if above:
            return "aligned" if side > 0 else "against"
        if below:
            return "aligned" if side < 0 else "against"
        return "mixed"

    def measures(events):
        """events = [(i, side)] -> dict of agree%, n, mean signed fwd % per horizon."""
        out = {"n": len(events)}
        for h in HORIZONS:
            rets = []
            agree = 0; na = 0
            for i, s in events:
                if i + h >= n:
                    continue
                r = s * (C[i + h] - C[i]) / C[i] * 100.0
                rets.append(r)
                if h == 1:
                    d = C[i + 1] - O[i + 1]
                    if d != 0:
                        na += 1
                        if (d > 0) == (s > 0):
                            agree += 1
            out[h] = (len(rets), sum(rets) / len(rets) if rets else 0.0)
            if h == 1:
                out["agree"] = (na, agree / na * 100.0 if na else 0.0)
        return out

    ev_all = [(i, 1 if C[i] >= O[i] else -1) for i in range(50, n - 1) if C[i] != O[i]]
    ev_mark = [(m["i"], m["side"]) for m in marks if m["i"] >= 50]
    ev_al = [(i, s) for i, s in ev_mark if align(i, s) == "aligned"]
    ev_ag = [(i, s) for i, s in ev_mark if align(i, s) == "against"]
    ev_mx = [(i, s) for i, s in ev_mark if align(i, s) == "mixed"]
    base_al = [(i, s) for i, s in ev_all if align(i, s) == "aligned"]
    ev_al_bub = [(i, s) for i, s in ev_al if bubble_keep(bars, thr, i, s)]
    ev_mark_bub = [(i, s) for i, s in ev_mark if bubble_keep(bars, thr, i, s)]

    def prow(name, m, base=None):
        n1, ag = m.get("agree", (0, 0.0))
        line = "%-26s n=%5d  next-bar agree %5.1f%% (n=%d)" % (name, m["n"], ag, n1)
        if base is not None and n1 > 0:
            p0 = base.get("agree", (0, 50.0))[1] / 100.0
            k = round(ag / 100.0 * n1)
            mu, sd = n1 * p0, math.sqrt(max(n1 * p0 * (1 - p0), 1e-9))
            z = (k - mu) / sd
            p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
            line += "  vs base %5.1f%%  z=%+.2f p=%.4f" % (p0 * 100, z, p)
        line += "  | fwd%%: " + "  ".join("+%db %+.4f" % (h, m[h][1]) for h in HORIZONS)
        print(line)
        return m

    print("\n=== %s ===  bars=%d  marks=%d (%.1f%% of bars)" % (label, n, len(ev_mark),
                                                                100.0 * len(ev_mark) / max(1, n)))
    b_all = measures(ev_all)
    prow("ALL bars (baseline)", b_all)
    b_alb = measures(base_al)
    prow("EMA-aligned bars (base)", b_alb)
    prow("BigBody ALL", measures(ev_mark), b_all)
    prow("BigBody EMA-ALIGNED", measures(ev_al), b_alb)
    prow("BigBody EMA-AGAINST", measures(ev_ag), b_all)
    prow("BigBody mixed-EMA", measures(ev_mx), b_all)
    prow("BigBody ALL + bubble", measures(ev_mark_bub), b_all)
    prow("BigBody ALIGNED + bubble", measures(ev_al_bub), b_alb)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    _bids, raws, gaps = load_archive("15m", root=os.path.join(here, "clock_archive"),
                                     drop_degenerate=False)
    assert not gaps, gaps
    split = None
    for k, r in enumerate(raws):
        if float(r.get("start_time", 0)) >= 1767225600.0:      # 2026-01-01 UTC
            split = k
            break
    study(raws[:split], "RECON 2025 (Jan-Dec)")
    study(raws[split:], "RECON 2026 H1 (Jan-Jun)")

    # DAEMON live era: the terminal's stored 15m clock candles (levels included)
    import socket, json as _json, time as _t
    try:
        s = socket.create_connection(("127.0.0.1", 9999), timeout=5); s.settimeout(1.0)
        s.sendall((_json.dumps({"action": "get_time_candles", "tf": "15m"}) + "\n").encode())
        buf = b""; cands = []
        t0 = _t.time(); last_rx = _t.time()
        while _t.time() - t0 < 25:
            try:
                chunk = s.recv(1 << 20)
            except socket.timeout:
                if cands and _t.time() - last_rx > 2.5:
                    break
                continue
            if not chunk:
                break
            last_rx = _t.time(); buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "TIME_CANDLES" and d.get("tf") == "15m":
                    cands.extend(d.get("candles") or [])
        s.close()
        seen = {}
        for c in cands:
            st = int(c.get("start_time", 0) or 0)
            if st and not c.get("empty"):
                seen[st] = c
        live = [seen[k] for k in sorted(seen)][:-1]            # drop the forming last
        if len(live) > 200:
            study(live, "DAEMON LIVE (~last 14d)")
        else:
            print("\n(daemon era skipped: only %d candles)" % len(live))
    except OSError as e:
        print("\n(daemon era skipped: %s)" % e)


if __name__ == "__main__":
    main()
