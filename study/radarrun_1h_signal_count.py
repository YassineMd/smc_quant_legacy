"""Signal-frequency census for the 1h RADAR RUNNER, NO filter. Counts raw signals + tradeable (non-overlap, quick-TP
exit) and breaks the rate down by side, by month, per-day distribution, and recency -- so it maps onto a prop-challenge
window. Detection identical to the tradeability studies (resisted wall -> radar breakout, MINVISIT=3)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np
from study.archive_loader import load_archive
from study.candle_bias_1h import _f
from app import absorption_level_detect as AL

RM = float(getattr(AL, "RADAR_MULT", 3.0)); MINVISIT = 3; TP = 0.005; H = 200


def sim_off(s, entry, tp, sl, ph, pl):
    for off in range(len(ph)):
        if (pl[off] <= sl) if s > 0 else (ph[off] >= sl):
            return off + 1
        if (ph[off] >= tp) if s > 0 else (pl[off] <= tp):
            return off + 1
    return len(ph)


def main():
    A = sorted(load_archive("1h", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    n = len(A)
    O = np.array([_f(b.get("open", b.get("open_price"))) for b in A])
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    Hi = np.array([_f(b.get("high")) for b in A]); Lo = np.array([_f(b.get("low")) for b in A])
    ST = np.array([_f(b.get("start_time")) for b in A])

    ev = {}; c0 = 0
    while c0 < n:
        c1 = min(n, c0 + 6000); S = A[c0:c1]
        for w in AL.detect(S, skip_last=False):
            side = w.get("side"); P = _f(w.get("price")); band = _f(w.get("band"))
            if band <= 0 or P <= 0:
                continue
            rlo = P - RM * band; rhi = P + RM * band
            for r in w.get("radar_runs", ()):
                if len(r) < 2:
                    continue
                a = int(r[0]) + c0; b = int(r[1]) + c0
                for k in range(b, min(b + 2, n - 1) + 1):
                    if not (rlo <= O[k] <= rhi):
                        continue
                    broke = (C[k] > rhi) if side == "S" else (C[k] < rlo)
                    if not broke or (k - a) < MINVISIT or (k, side) in ev:
                        continue
                    ev[(k, side)] = (rlo, rhi); break
        if c1 >= n:
            break
        c0 += 5000

    keys = sorted(ev)
    raw_long = sum(1 for (k, s) in keys if s == "S"); raw_short = len(keys) - raw_long
    # tradeable = non-overlap by the quick-TP exit
    taken = []; last = -1
    for (k, side) in keys:
        if k + 1 >= n or k <= last:
            continue
        rlo, rhi = ev[(k, side)]; up = side == "S"; s = 1 if up else -1; entry = C[k]
        sl = max(Lo[k] * (1 - 0.003), rlo) if up else min(Hi[k] * (1 + 0.003), rhi)
        j0 = k + 1; j1 = min(n, k + 1 + H)
        off = sim_off(s, entry, entry * (1 + s * TP), sl, Hi[j0:j1], Lo[j0:j1])
        taken.append((k, side)); last = k + int(off)

    t0 = datetime.fromtimestamp(ST[0], tz=timezone.utc); t1 = datetime.fromtimestamp(ST[-1], tz=timezone.utc)
    span_days = (ST[-1] - ST[0]) / 86400.0; span_mo = span_days / 30.437
    print("1h RADAR RUNNER signal census (NO filter)")
    print("  data span: %s -> %s  (%.0f days, %.1f months)" % (t0.date(), t1.date(), span_days, span_mo))
    print("  RAW signals    : %d   (long %d / short %d)" % (len(keys), raw_long, raw_short))
    print("  TRADEABLE      : %d   (after non-overlap quick-TP exit)" % len(taken))
    print("  RATE (tradeable): %.1f / month   %.2f / week   %.2f / day" %
          (len(taken) / span_mo, len(taken) / (span_days / 7), len(taken) / span_days))

    # per-day distribution (tradeable), among calendar days in span
    byday = {}
    for (k, side) in taken:
        d = int(ST[k] // 86400); byday[d] = byday.get(d, 0) + 1
    total_days = int(span_days) + 1
    from collections import Counter
    dist = Counter(byday.values())
    active = len(byday)
    print("  per-DAY (tradeable): active days=%d/%d (%.0f%%)   0=%d  1=%d  2=%d  3+=%d   max/day=%d" %
          (active, total_days, 100 * active / total_days, total_days - active,
           dist.get(1, 0), dist.get(2, 0), sum(v for kk, v in dist.items() if kk >= 3), max(byday.values())))

    # per-month table (tradeable)
    bymo = {}
    for (k, side) in taken:
        dt = datetime.fromtimestamp(ST[k], tz=timezone.utc); key = "%04d-%02d" % (dt.year, dt.month)
        bymo[key] = bymo.get(key, 0) + 1
    print("  per-MONTH (tradeable):")
    mos = sorted(bymo)
    line = "    "
    for i, m in enumerate(mos):
        line += "%s:%-3d " % (m, bymo[m])
        if (i + 1) % 6 == 0:
            print(line); line = "    "
    if line.strip():
        print(line)
    cnts = [bymo[m] for m in mos]
    print("    monthly: mean=%.1f  min=%d  max=%d   (last 3 mo: %s)" %
          (np.mean(cnts), min(cnts), max(cnts), ", ".join("%s=%d" % (m, bymo[m]) for m in mos[-3:])))


if __name__ == "__main__":
    main()
