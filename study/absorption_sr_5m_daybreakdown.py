"""5m ABSORPTION S/R — per-position / per-badge-tier / per-ring breakdown over 7 random 2025 + 7 random 2026 days.

Causal, live-faithful: each selected UTC day is detected on a bounded window = [D-3d warm-up, D+3d forward] (≈ the
terminal's finite 5m window), so S/R/absorption see only a rolling context, not full history. Signals kept only if the
SIGNAL candle's date == D. Exit = walk forward on 5m OHLC, SL checked first on a same-bar touch (matches the live sim).
Net% = price-distance move in favour minus the 0.1% round-trip fee. Ring tier from the candle's 1m constituents.

Memory-safe: 5m is stream-loaded keeping only scalars (+ daily-VA profiles accumulated from footprints on the fly);
1m is pulled only for the 14 chosen days.
"""
import gzip, json, glob, os, sys, time, random, bisect, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import support_resistance as _sr
from app import absorption as _absm
from app import engulf5m_detect, absorb2_detect, finish_strength
from app.engulf_sr_detect import _va_poc

SEED = 20260730
N_PER_YEAR = 40                # random non-consecutive days sampled per calendar year
WARM_S = 3 * 86400
FWD_S = 3 * 86400
FEE = 0.1                      # round-trip %, 0.05%/side
SCALARS = ("high", "low", "open_price", "close_price", "buy_vol", "sell_vol", "start_time", "end_time")


def at_sr(win, wi, side, levels, K):
    """True if the signal candle at wi touches a SAME-SIDE ACTIVE S/R zone (long -> support, short -> resistance).
    Mirrors engulf5m_detect.touches(): widened zone [zlo,zhi], active = confirmed (i0+K<=wi) and unbroken (i1>wi)."""
    kind = "S" if side > 0 else "R"
    o = float(win[wi]["open_price"] or 0.0); h = float(win[wi]["high"] or 0.0); l = float(win[wi]["low"] or 0.0)
    for x in levels:
        if x["kind"] != kind or not (x["i0"] + K <= wi and (x["i1"] is None or x["i1"] > wi)):
            continue
        zlo = x["zlo"]; zhi = x["zhi"]
        if (l <= zhi and h >= zlo) or (zlo <= o <= zhi):
            return True
    return False


def stream_5m():
    """light5 (scalar dicts, chronological) + global dayva {date:(val,vah)} accumulated from footprints on the fly."""
    by_bid = {}
    day_prof = defaultdict(dict)
    files = sorted(glob.glob(os.path.join(ROOT, "study", "recon_archive", "5m", "5m_*.jsonl.gz")))
    for fn in files:
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                st = float(d.get("start_time", 0.0) or 0.0)
                if st > 0:                                            # accumulate the day's summed footprint for VA
                    day = dt.datetime.utcfromtimestamp(st).date()
                    prof = day_prof[day]
                    for pr, v in (d.get("levels") or {}).items():
                        try:
                            p = float(pr)
                        except (TypeError, ValueError):
                            continue
                        prof[p] = prof.get(p, 0.0) + float(v.get("b", 0) or 0) + float(v.get("s", 0) or 0)
                by_bid[int(r["bid"])] = {k: d.get(k) for k in SCALARS}
    light5 = [by_bid[b] for b in sorted(by_bid)]
    dayva = {}
    for day, prof in day_prof.items():
        va = _va_poc(prof)
        if va:
            dayva[day] = va
    return light5, dayva


def load_1m_for_days(day_starts):
    """1m OHLC dicts whose start_time falls in any [D_start, D_end+1800) — for the ring only. Returns (st1[], buckets[])."""
    windows = [(ds, ds + 86400 + 1800) for ds in day_starts]
    keep = []
    files = sorted(glob.glob(os.path.join(ROOT, "study", "recon_archive", "1m", "1m_*.jsonl.gz")))
    for fn in files:
        with gzip.open(fn, "rt", encoding="utf-8") as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
                st = float(d.get("start_time", 0.0) or 0.0)
                if st <= 0:
                    continue
                if any(a <= st < b for a, b in windows):
                    keep.append({"start_time": st, "open_price": d.get("open_price"), "close_price": d.get("close_price"),
                                 "high": d.get("high"), "low": d.get("low")})
    keep.sort(key=lambda x: x["start_time"])
    return [x["start_time"] for x in keep], keep


def pick_days(dates, n, spacing=2):
    """n distinct dates from `dates`, no two within `spacing` days (=> non-consecutive), random order.
    `sorted` first so the pool order is deterministic (a set of dates iterates in hash-randomized order)."""
    pool = sorted(dates)
    random.shuffle(pool)
    chosen = []
    for d in pool:
        if all(abs((d - c).days) >= spacing for c in chosen):
            chosen.append(d)
        if len(chosen) == n:
            break
    return sorted(chosen)


def main():
    random.seed(SEED)
    t0 = time.time()
    print("streaming 5m ...", flush=True)
    light5, dayva = stream_5m()
    st5 = [float(b["start_time"] or 0.0) for b in light5]
    print("  5m buckets: %d   span %s .. %s   dayva days: %d   (%.0fs)" % (
        len(light5), dt.datetime.utcfromtimestamp(st5[0]).date(), dt.datetime.utcfromtimestamp(st5[-1]).date(),
        len(dayva), time.time() - t0), flush=True)

    # eligible dates per year: need 3d warm-up before + 3d forward after
    lo_ok = st5[0] + WARM_S
    hi_ok = st5[-1] - FWD_S
    by_year = defaultdict(set)
    for s in st5:
        if lo_ok <= s <= hi_ok:
            d = dt.datetime.utcfromtimestamp(s).date()
            by_year[d.year].add(d)
    days = pick_days(by_year[2025], N_PER_YEAR) + pick_days(by_year[2026], N_PER_YEAR)
    print("chosen days (%d/yr): 2025=%d 2026=%d" % (
        N_PER_YEAR, sum(1 for d in days if d.year == 2025), sum(1 for d in days if d.year == 2026)), flush=True)
    print("  " + ", ".join(d.isoformat() for d in days), flush=True)
    day_starts = [int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) for d in days]

    print("loading 1m for the 14 days ...", flush=True)
    st1, subs1 = load_1m_for_days(day_starts)
    print("  1m buckets kept: %d   (%.0fs)" % (len(st1), time.time() - t0), flush=True)

    def ring_of(sig_start, sig_end, side):
        if sig_end <= sig_start:
            sig_end = sig_start + 300.0
        lo = bisect.bisect_left(st1, sig_start); hi = bisect.bisect_left(st1, sig_end)
        return finish_strength.ring_tier(subs1[lo:hi], side)

    trades = []          # each: dict(side, badge, ring, outcome, net, day, gbar, exitbar)
    for d, ds in zip(days, day_starts):
        de = ds + 86400
        a = bisect.bisect_left(st5, ds - WARM_S)
        b = bisect.bisect_left(st5, de + FWD_S)
        win = light5[a:b]
        if len(win) < 2 * _sr.SR_PIVOT_K + 2:
            continue
        levels = _sr.detect(win, _sr.SR_PIVOT_K, zone_mitigation=True)
        absorp = []
        for k in range(len(win)):
            try:
                absorp.append(_absm.absorption(win, k)[0])
            except Exception:
                absorp.append(None)
        eng = engulf5m_detect.detect(win, skip_last=False, levels=levels, absorp=absorp, dayva=dayva)
        ab = absorb2_detect.detect(win, skip_last=False, levels=levels, absorp=absorp, dayva=dayva)
        sigs = []
        eng_bars = set()
        for e in eng:
            wi = e["i"]; eng_bars.add(wi)
            sigs.append((wi, e["side"], e["entry"], e["sl"], e["tp"], ("gold" if e.get("gold") else "rg")))
        for e in ab:
            wi = e["i"]
            if wi in eng_bars:
                continue
            sigs.append((wi, e["side"], e["entry"], e["sl"], e["tp"], "ob"))
        sigs.sort()
        for wi, side, entry, sl, tp, badge in sigs:
            gs = st5[a + wi]
            if dt.datetime.utcfromtimestamp(gs).date() != d:        # SIGNAL candle must be on day D
                continue
            atsr = at_sr(win, wi, side, levels, _sr.SR_PIVOT_K)   # is the signal candle AT a same-side S/R zone?
            # exit walk on 5m OHLC (SL first)
            outcome = "none"; exitbar = None
            for j in range(wi + 1, len(win)):
                hj = float(win[j]["high"] or 0.0); lj = float(win[j]["low"] or 0.0)
                if side > 0:
                    if lj <= sl:
                        outcome = "L"; exitbar = j; break
                    if hj >= tp:
                        outcome = "W"; exitbar = j; break
                else:
                    if hj >= sl:
                        outcome = "L"; exitbar = j; break
                    if lj <= tp:
                        outcome = "W"; exitbar = j; break
            if outcome == "none":
                continue
            exitp = (sl if outcome == "L" else tp)
            net = (exitp - entry) / entry * 100.0 * side - FEE
            ring = ring_of(gs, float(win[wi]["end_time"] or 0.0), side)
            trades.append(dict(side=side, badge=badge, ring=ring, outcome=outcome, net=net, atsr=atsr,
                               day=d, gbar=a + wi, exitbar=(a + exitbar)))

    sr = [t for t in trades if t["atsr"]]
    print("\ntotal signals: %d   |   AT same-side S/R: %d   (%.0fs)" % (len(trades), len(sr), time.time() - t0), flush=True)
    print("\n" + "#" * 92)
    print("# (A) ALL SIGNALS  —  no S/R-location filter")
    print("#" * 92)
    report(trades)
    print("\n\n" + "#" * 92)
    print("# (B) AT SAME-SIDE S/R ONLY  —  long at a support zone / short at a resistance zone (SR indicator)")
    print("#" * 92)
    report(sr)


def cell(rows):
    n = len(rows); w = sum(1 for r in rows if r["outcome"] == "W"); l = n - w
    net = sum(r["net"] for r in rows)
    pos = sum(r["net"] for r in rows if r["net"] > 0); neg = -sum(r["net"] for r in rows if r["net"] < 0)
    wr = (100.0 * w / n) if n else 0.0
    pf = (pos / neg) if neg > 1e-9 else float("inf")
    return n, w, l, wr, (net / n if n else 0.0), net, pf


def report(trades):
    BADGES = [("rg", "Engulf R/G  (|A|1-2)"), ("gold", "Engulf GOLD (|A|>=2)"), ("ob", "Absorb2 B/O")]
    RINGS = [(0, "no ring"), (1, "R/G ring"), (2, "GOLD ring")]
    hdr = "%-22s %-10s %5s %5s %5s %7s %9s %9s %7s" % (
        "badge", "ring", "n", "W", "L", "win%", "avg net%", "sum net%", "PF")
    for side, sname in ((1, "LONG"), (-1, "SHORT")):
        print("=" * 92); print(sname); print("=" * 92); print(hdr); print("-" * 92)
        sub = [t for t in trades if t["side"] == side]
        for bk, blab in BADGES:
            brows = [t for t in sub if t["badge"] == bk]
            for rk, rlab in RINGS:
                rows = [t for t in brows if t["ring"] == rk]
                if not rows:
                    continue
                n, w, l, wr, an, sn, pf = cell(rows)
                print("%-22s %-10s %5d %5d %5d %6.1f%% %9.3f %9.2f %7s" % (
                    blab, rlab, n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))
            if brows:
                n, w, l, wr, an, sn, pf = cell(brows)
                print("%-22s %-10s %5d %5d %5d %6.1f%% %9.3f %9.2f %7s" % (
                    blab, "  ALL", n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))
            print("-" * 92)
        n, w, l, wr, an, sn, pf = cell(sub)
        print("%-22s %-10s %5d %5d %5d %6.1f%% %9.3f %9.2f %7s" % (
            sname + " TOTAL", "", n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))
        print()
    # grand + ring-only + badge-only pooled
    print("=" * 92); print("POOLED (both sides)"); print("=" * 92); print(hdr); print("-" * 92)
    for bk, blab in BADGES:
        for rk, rlab in RINGS:
            rows = [t for t in trades if t["badge"] == bk and t["ring"] == rk]
            if not rows:
                continue
            n, w, l, wr, an, sn, pf = cell(rows)
            print("%-22s %-10s %5d %5d %5d %6.1f%% %9.3f %9.2f %7s" % (
                blab, rlab, n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))
        print("-" * 92)
    n, w, l, wr, an, sn, pf = cell(trades)
    print("%-22s %-10s %5d %5d %5d %6.1f%% %9.3f %9.2f %7s" % (
        "GRAND TOTAL", "", n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))

    # taken() non-overlap (canonical basis): chronological, skip a signal opened before the prior trade closed
    print("\n" + "=" * 92); print("TAKEN (non-overlapping, canonical basis)"); print("=" * 92)
    tk = sorted(trades, key=lambda t: t["gbar"]); taken = []; last_exit = -1
    for t in tk:
        if t["gbar"] > last_exit:
            taken.append(t); last_exit = t["exitbar"]
    for side, sname in ((1, "LONG"), (-1, "SHORT"), (0, "BOTH")):
        rows = taken if side == 0 else [t for t in taken if t["side"] == side]
        if not rows:
            continue
        n, w, l, wr, an, sn, pf = cell(rows)
        print("  %-6s n=%-4d W=%-4d L=%-4d win=%.1f%%  avg net=%.3f%%  sum=%.2f%%  PF=%s" % (
            sname, n, w, l, wr, an, sn, ("inf" if pf == float("inf") else "%.2f" % pf)))


if __name__ == "__main__":
    main()
