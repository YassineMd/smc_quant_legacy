"""FULL breakdown of the 15m Engulf S/R strategy — driving the SHIPPED detector app/momentum_detect.detect()
DIRECTLY (parity-guaranteed: AB2=-0.3, tier-dependent skew, body-breakout, relaxed-absorption path, per-signal
sl/tp/tier). Recon (2025-01..2026-06-19) AND fresh live daemon buckets (2026-06-20..2026-07-30).

Exit model: entry at signal-bar close; walk forward, SL-adverse-first on a bar; non-overlapping; 0.08% round-trip
fee. Tiers are DISJOINT (gold |A|<=-2 > blue at-S/R > US pocket > normal); gold/blue TP 1:2, US/normal TP 1:1.2.
"""
import os, sys, math, statistics, json, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app import momentum_detect as M
from app.engulf_sr_detect import _ohlc
from study.archive_loader import load_archive, ARCHIVE_DIR

RECON = os.path.join(ROOT, "study", "recon_archive")
FEE = 0.0008          # 0.08% round trip (== mom_absorb_1h.FEE)


def binom_sf(k, n, p):
    if n == 0:
        return 1.0
    if n <= 150:
        return sum(math.comb(n, i) * p**i * (1 - p)**(n - i) for i in range(k, n + 1))
    mu = n * p; sd = math.sqrt(n * p * (1 - p))
    if sd == 0:
        return 1.0 if k <= mu else 0.0
    return 0.5 * math.erfc(((k - 0.5 - mu) / sd) / math.sqrt(2))


def _rr(tier):
    return 2.0 if tier in ("gold", "conf") else 1.2


def load_buckets(root):
    _, raws, _ = load_archive("15m", root=root)
    B = [json.loads(r) if isinstance(r, str) else r for r in raws]
    B.sort(key=lambda x: float(x.get("start_time", 0) or 0))
    return B


def evaluate(buckets):
    n = len(buckets)
    H = [0.0] * n; Lo = [0.0] * n
    for i, b in enumerate(buckets):
        _, _, h, l = _ohlc(b); H[i] = h; Lo[i] = l
    sigs = sorted(M.detect(buckets, skip_last=False), key=lambda s: s["i"])
    rows = []; last = -1
    for s in sigs:
        i = s["i"]
        if i <= last:
            continue
        side = s["side"]; entry = float(s["entry"]); sl = float(s["sl"]); tp = float(s["tp"])
        outcome = None; ej = None
        for j in range(i + 1, n):
            hj = H[j]; lj = Lo[j]
            if side > 0:
                if lj <= sl:
                    outcome = False; ej = j; break
                if hj >= tp:
                    outcome = True; ej = j; break
            else:
                if hj >= sl:
                    outcome = False; ej = j; break
                if lj <= tp:
                    outcome = True; ej = j; break
        if outcome is None:
            continue                          # unresolved at data end -> drop
        exitp = tp if outcome else sl
        net = side * (exitp - entry) / entry - FEE
        yr = dt.datetime.utcfromtimestamp(float(buckets[i]["start_time"])).year
        rows.append(dict(net=net, side=side, tier=s["tier"], yr=yr, dist=abs(entry - sl) / entry))
        last = ej
    return rows


def rep(rows, title):
    if not rows:
        print("  %-16s n=0" % title); return
    n = len(rows); w = sum(1 for r in rows if r["net"] > 0)
    net = sum(r["net"] for r in rows) * 100.0
    pos = sum(r["net"] for r in rows if r["net"] > 0); neg = -sum(r["net"] for r in rows if r["net"] < 0)
    pf = pos / neg if neg > 1e-12 else float("inf")
    wr = 100.0 * w / n
    mean_net = statistics.mean(r["net"] for r in rows)
    sd = statistics.pstdev(r["net"] for r in rows) if n > 1 else 0.0
    tstat = mean_net / (sd / math.sqrt(n)) if sd > 0 else 0.0
    rrs = {_rr(r["tier"]) for r in rows}
    if len(rrs) == 1:                                  # single-tier cohort -> exact net break-even + binomial p
        rr = next(iter(rrs)); dbar = statistics.mean(r["dist"] for r in rows)
        p_be = (dbar + FEE) / (dbar * (1 + rr))
        be_s = "%.1f%%" % (100 * p_be); p_s = "%.3f" % binom_sf(w, n, p_be)
    else:
        be_s = "  mix"; p_s = "  -"
    print("  %-16s n=%-4d W=%-4d  win%%=%.1f%%  netBE=%6s  sum=%+.1f%%  PF=%s  t=%+.2f  binom_p=%s" % (
        title, n, w, wr, be_s, net, ("inf" if pf == float("inf") else "%.2f" % pf), tstat, p_s))


def breakdown(rows, label, year_split=True):
    print("\n" + "=" * 98)
    print("%s  |  %d trades taken" % (label, len(rows)))
    print("=" * 98)
    rep(rows, "ALL")
    rep([r for r in rows if r["side"] > 0], "  LONG")
    rep([r for r in rows if r["side"] < 0], "  SHORT")
    print("  -- disjoint tiers (gold > blue@S/R > US > normal) --")
    for tier, name in (("gold", "GOLD |A|<=-2"), ("conf", "BLUE at-S/R"), ("us", "US pocket"), ("normal", "NORMAL")):
        tr = [r for r in rows if r["tier"] == tier]
        rep(tr, name)
        if year_split and tr:
            for y in (2025, 2026):
                yr = [r for r in tr if r["yr"] == y]
                if yr:
                    rep(yr, "    %d" % y)


def main():
    print("SHIPPED momentum_detect.detect | AB2=-0.3 | gold/blue 1:2, US/normal 1:1.2 | SL 0.1%% off c1 | fee %.2f%%" % (FEE * 100))
    t0 = dt.datetime(2000, 1, 1)  # placeholder (no wall clock in-script fine here)
    recon = load_buckets(RECON)
    print("recon: %d buckets" % len(recon), flush=True)
    breakdown(evaluate(recon), "RECON  (2025-01-01 .. 2026-06-19)", year_split=True)

    live = load_buckets(ARCHIVE_DIR)
    st = [float(b["start_time"]) for b in live]
    print("\nlive: %d buckets, span %s .. %s" % (
        len(live), dt.datetime.utcfromtimestamp(min(st)).date(), dt.datetime.utcfromtimestamp(max(st)).date()), flush=True)
    breakdown(evaluate(live), "FRESH LIVE  (2026-06-20 .. 2026-07-30, real daemon buckets)", year_split=False)


if __name__ == "__main__":
    main()
