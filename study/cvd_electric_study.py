"""Does a CVD ELECTRIC bar carry directional intent? (5m, causal)

HYPOTHESIS (operator): an electric PINK CVD bar printed at the TOP of a swing -> price goes DOWN;
the mirror (electric GREEN at the BOTTOM of a swing) -> price goes UP.

METHOD — the two rules this study lives or dies by:

1. CAUSAL. Every input is known at the bar's CLOSE, nothing later:
   - the electric flag uses the production trailing-30 ratios (backward window only);
   - "swing top" = this bar's high is the HIGHEST of the trailing N bars (itself included). A
     centered/symmetric swing window would peek at future bars — that is precisely the look-ahead
     that invalidated the whole pre-V3 PIVOT line, so it is not used here.
   - the forward return is measured strictly AFTER the bar's close.

2. MARGINAL vs BASE RATE. "Price falls after a swing top" may just be mean reversion. The flag only
   has intent if the electric cohort beats the SAME-LOCATION baseline (every bar at a swing top).
   The headline number is therefore the EDGE = cohort - baseline, never the cohort alone.

Run:  python study/cvd_electric_study.py
Data: study/out/cvd_5m.csv  (dumped from the VM's history.db closed_buckets, tf='5m')
"""
from __future__ import annotations

import csv
import os
import statistics

# --- production constants (mirror app/terminal.py) --------------------------------
CVD_RE_RATIO = 1.5
CVD_RE_WINDOW = 30
CVD_RE_EFFORT_FLOOR = 1.0

SWING_WINDOWS = (10, 20, 50)          # trailing bars that define a causal local extreme
HORIZONS = (1, 3, 6, 12, 24)          # bars forward, measured from the bar's close

_HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(_HERE, "out", "cvd_5m.csv")


def re_ratios(vals, n):
    """Each value over its OWN trailing-n mean (causal; identical to terminal._re_ratios)."""
    out = []
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        m = s / (n if i >= n else i + 1)
        out.append((v / m) if m > 0 else 0.0)
    return out


def load():
    rows = list(csv.DictReader(open(CSV)))
    return [{k: float(r[k]) for k in ("start_time", "open", "high", "low", "close", "buy_vol", "sell_vol")}
            for r in rows]


def build(rows):
    """Per-bar: the CVD candle (UTC-anchored), the electric flag, and the causal swing location."""
    n = len(rows)
    # --- CVD candles, re-anchored at each UTC midnight (production _cvd_candles) ---
    cvd_o, cvd_c = [], []
    run = 0.0
    day = None
    for r in rows:
        d = int(r["start_time"] // 86400.0)
        if day is not None and d != day:
            run = 0.0
        day = d
        cvd_o.append(run)
        run += (r["buy_vol"] - r["sell_vol"])
        cvd_c.append(run)
    # --- electric flag (production rule) ---
    pxr = re_ratios([abs(r["close"] - r["open"]) for r in rows], CVD_RE_WINDOW)
    cvr = re_ratios([abs(cvd_c[i] - cvd_o[i]) for i in range(n)], CVD_RE_WINDOW)
    for i in range(n):
        cheap = pxr[i] >= CVD_RE_RATIO * max(cvr[i], CVD_RE_EFFORT_FLOOR)
        cvd_up = cvd_c[i] >= cvd_o[i]
        rows[i]["elec_green"] = cheap and cvd_up
        rows[i]["elec_pink"] = cheap and not cvd_up
        rows[i]["cvd_up"] = cvd_up
    # --- causal swing location: highest high / lowest low of the TRAILING window ---
    for w in SWING_WINDOWS:
        for i in range(n):
            lo_i = max(0, i - w + 1)
            win = rows[lo_i:i + 1]
            rows[i][f"top{w}"] = rows[i]["high"] >= max(x["high"] for x in win)
            rows[i][f"bot{w}"] = rows[i]["low"] <= min(x["low"] for x in win)
    return rows


def fwd(rows, i, h):
    """Forward % return from bar i's CLOSE to bar i+h's close. None when it runs off the data."""
    if i + h >= len(rows):
        return None
    c0 = rows[i]["close"]
    return None if c0 <= 0 else (rows[i + h]["close"] - c0) / c0 * 100.0


def stats(vals, sign):
    """sign=-1 when the hypothesis predicts DOWN. Returns (n, hit%, mean%, median%)."""
    if not vals:
        return 0, float("nan"), float("nan"), float("nan")
    hits = sum(1 for v in vals if v * sign > 0)
    return len(vals), 100.0 * hits / len(vals), statistics.mean(vals), statistics.median(vals)


def cohort(rows, pred, h, sign):
    return stats([v for i in range(len(rows)) if pred(rows[i])
                  for v in (fwd(rows, i, h),) if v is not None], sign)


def main():
    rows = build(load())
    n = len(rows)
    ep = sum(1 for r in rows if r["elec_pink"])
    eg = sum(1 for r in rows if r["elec_green"])
    print(f"5m buckets: {n}   electric pink: {ep} ({100*ep/n:.1f}%)   electric green: {eg} ({100*eg/n:.1f}%)")
    print(f"rule: result >= {CVD_RE_RATIO} x max(effort, {CVD_RE_EFFORT_FLOOR}), each vs its own trailing-{CVD_RE_WINDOW} mean\n")

    for w in SWING_WINDOWS:
        print("=" * 104)
        print(f"CAUSAL SWING WINDOW = trailing {w} bars")
        print("=" * 104)
        for label, flag, loc, sign in (
            ("ELECTRIC PINK @ swing TOP  (predict DOWN)", "elec_pink", f"top{w}", -1),
            ("ELECTRIC GREEN @ swing BOT (predict UP)", "elec_green", f"bot{w}", +1),
        ):
            print(f"\n{label}")
            print(f"  {'h':>3} | {'n':>5} {'hit%':>6} {'mean%':>7} | {'BASE n':>6} {'hit%':>6} {'mean%':>7} "
                  f"| {'EDGE hit':>8} {'EDGE mean':>9}")
            print("  " + "-" * 96)
            for h in HORIZONS:
                cn, ch, cm, _ = cohort(rows, lambda r, f=flag, l=loc: r[f] and r[l], h, sign)
                bn, bh, bm, _ = cohort(rows, lambda r, l=loc: r[l], h, sign)          # SAME location, any bar
                if cn == 0:
                    print(f"  {h:>3} |     0      -       - |");  continue
                print(f"  {h:>3} | {cn:>5} {ch:>6.1f} {cm:>+7.3f} | {bn:>6} {bh:>6.1f} {bm:>+7.3f} "
                      f"| {ch-bh:>+8.1f} {cm-bm:>+9.3f}")

    # --- is the flag directional AT ALL, ignoring swing location? ---
    print("\n" + "=" * 104)
    print("CONTROL — the flag ANYWHERE (no swing filter). If the edge lives here, 'at the swing' is not the driver.")
    print("=" * 104)
    print(f"  {'h':>3} | {'PINK n':>6} {'hit%(dn)':>8} {'mean%':>7} | {'GREEN n':>7} {'hit%(up)':>8} {'mean%':>7} "
          f"| {'ALL BARS mean%':>14}")
    print("  " + "-" * 96)
    for h in HORIZONS:
        pn, ph, pm, _ = cohort(rows, lambda r: r["elec_pink"], h, -1)
        gn, gh, gm, _ = cohort(rows, lambda r: r["elec_green"], h, +1)
        an, ah, am, _ = cohort(rows, lambda r: True, h, +1)
        print(f"  {h:>3} | {pn:>6} {ph:>8.1f} {pm:>+7.3f} | {gn:>7} {gh:>8.1f} {gm:>+7.3f} | {am:>+14.3f}")


if __name__ == "__main__":
    main()
