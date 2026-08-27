"""DUAL EMA-HL DELTA (20 + 50) x RADAR RUNNER, 30m bucket — HONEST test (user 2026-08-27: require BOTH the
EMA20 HL delta AND the EMA50 HL delta aligned — LONG only if both positive, SHORT only if both negative).
Canonical harness: cached union fire sets, NATIVE radar SL, 1-MINUTE first-touch, non-overlap taken(),
fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): per EMA period p in {20, 50} the shipped ema_ext spec at the fire
close — window = last p CLOSED bars INCLUDING the fire bar; window max high / min low (ties -> most
recent), each measured VERTICALLY to EMA(p) AT its own bar (EMA adjust=False, seeded first close);
delta_p = signed net. RULE = LONG iff d20>0 AND d50>0 / SHORT iff d20<0 AND d50<0. CELLS: ALL /
BOTH-WITH (the rule) / BOTH-AGAINST (both deltas OPPOSE the side) / WITH-LONG / WITH-SHORT.
EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_hld2050_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def ema(closes, p):
    a = 2.0 / (p + 1.0)
    y = np.empty(len(closes))
    y[0] = closes[0]
    for i in range(1, len(closes)):
        c = closes[i] if closes[i] > 0 else y[i - 1]
        y[i] = a * c + (1.0 - a) * y[i - 1]
    return y


def delta_at(b, p, E, H, L):
    hi_p = hi_i = lo_p = lo_i = None
    for i in range(max(0, b - p + 1), b + 1):
        if H[i] > 0 and (hi_p is None or H[i] >= hi_p):
            hi_p, hi_i = H[i], i
        if L[i] > 0 and (lo_p is None or L[i] <= lo_p):
            lo_p, lo_i = L[i], i
    if hi_p is None or lo_p is None or E[hi_i] <= 0 or E[lo_i] <= 0:
        return None
    return (hi_p - E[hi_i]) / E[hi_i] + (lo_p - E[lo_i]) / E[lo_i]


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E20, E50 = ema(C, 20), ema(C, 50)
    recs = []
    nex = 0
    for f in fires:
        b = int(f[0]); s = int(f[2])
        d20 = delta_at(b, 20, E20, H, L)
        d50 = delta_at(b, 50, E50, H, L)
        if d20 is None or d50 is None or d20 == 0.0 or d50 == 0.0:
            nex += 1
            recs.append(dict(f=tuple(f), s=s, w=False, a=False))
            continue
        recs.append(dict(f=tuple(f), s=s,
                         w=(s > 0 and d20 > 0 and d50 > 0) or (s < 0 and d20 < 0 and d50 < 0),
                         a=(s > 0 and d20 < 0 and d50 < 0) or (s < 0 and d20 > 0 and d50 > 0)))
    if nex:
        print("  (degenerate / zero delta: %d fires excluded from conditioned cells)" % nex, flush=True)
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("BOTH-WITH", lambda r: r["w"]),
    ("BOTH-AGAINST", lambda r: r["a"]),
    ("WITH-LONG", lambda r: r["w"] and r["s"] > 0),
    ("WITH-SHORT", lambda r: r["w"] and r["s"] < 0),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-13s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("DUAL EMA-HL DELTA (20+50) x RADAR RUNNER 30m BUCKET — long: d20>0 AND d50>0 / short mirror | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A, T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json"))), Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
