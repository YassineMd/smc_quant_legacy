"""EMA-HL DELTA x RADAR RUNNER, 30m + 15m bucket — HONEST test (user 2026-08-27: LONG only if the EMA HL
delta is positive, SHORT only if negative). Canonical harness: cached union fire sets (recon
rr30mbkt_live_fires_union / rr_union_bucket_15m_s1; daemon rr_union_b30m_daemon_m30 /
rr_union_b15m_daemon_m30), 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg,
prop MC; recon per-year + DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): the SHIPPED readout's final spec on the fire bar's close — EMA(20)
of closes (adjust=False, seeded first close); window = the last 20 CLOSED bars INCLUDING the fire bar
[b-19..b]; hi = window max high, lo = window min low (ties -> most recent); each distance measured
VERTICALLY to the EMA AT THAT EXTREME'S BAR: u=(hi-E[hi_i])/E[hi_i], d=(lo-E[lo_i])/E[lo_i]; DELTA = u+d
(signed net). RULE = LONG iff delta>0 / SHORT iff delta<0 (delta==0 excluded). Applied identically to the
30m and the 15m bucket (both fire sets cached). CELLS per tf: ALL / WITH-DLT (the rule) / AGAINST-DLT /
WITH-LONG / WITH-SHORT. EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_hldelta.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]
P = 20


def ema20(closes):
    a = 2.0 / (P + 1.0)
    y = np.empty(len(closes))
    y[0] = closes[0]
    for i in range(1, len(closes)):
        c = closes[i] if closes[i] > 0 else y[i - 1]
        y[i] = a * c + (1.0 - a) * y[i - 1]
    return y


def features(fires, A):
    from study.candle_bias_1h import _f
    C = np.array([_f(b.get("close", b.get("close_price"))) for b in A])
    H = np.array([_f(b.get("high")) for b in A])
    L = np.array([_f(b.get("low")) for b in A])
    E = ema20(C)
    recs = []
    nex = 0
    for f in fires:
        b = int(f[0]); s = int(f[2])
        hi_p = hi_i = lo_p = lo_i = None
        for i in range(max(0, b - P + 1), b + 1):            # the readout's window: 20 closed bars incl. the fire bar
            if H[i] > 0 and (hi_p is None or H[i] >= hi_p):
                hi_p, hi_i = H[i], i
            if L[i] > 0 and (lo_p is None or L[i] <= lo_p):
                lo_p, lo_i = L[i], i
        if hi_p is None or lo_p is None or E[hi_i] <= 0 or E[lo_i] <= 0:
            nex += 1
            recs.append(dict(f=tuple(f), s=s, w=False, a=False))
            continue
        delta = (hi_p - E[hi_i]) / E[hi_i] + (lo_p - E[lo_i]) / E[lo_i]
        if delta == 0.0:
            nex += 1
            recs.append(dict(f=tuple(f), s=s, w=False, a=False))
            continue
        recs.append(dict(f=tuple(f), s=s,
                         w=(s > 0 and delta > 0) or (s < 0 and delta < 0),
                         a=(s > 0 and delta < 0) or (s < 0 and delta > 0)))
    if nex:
        print("  (degenerate / delta==0: %d fires excluded from conditioned cells)" % nex, flush=True)
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("WITH-DLT", lambda r: r["w"]),
    ("AGAINST-DLT", lambda r: r["a"]),
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
            print("  %-12s %-9s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("EMA-HL DELTA x RADAR RUNNER — 30m + 15m bucket | long iff delta>0 / short iff delta<0 | shipped-readout spec | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    for tf in ("30m", "15m"):
        print("=" * 120, flush=True)
        print("RECON %s BUCKET 2025-01 .. 2026-06 (per-year split in rows)" % tf, flush=True)
        A = sorted(load_archive(tf, root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
        report(features(load_fires("bucket", tf), A), T1, H1, L1)
        del A
    del T1, H1, L1
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    for tf, fn in (("30m", "rr_union_b30m_daemon_m30.json"), ("15m", "rr_union_b15m_daemon_m30.json")):
        print("=" * 120, flush=True)
        print("DAEMON %s (TRUE OOS, 2026-06-20 ..)" % tf, flush=True)
        Ad = sorted(load_archive(tf, drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
        report(features(json.load(open(os.path.join(OUT, fn))), Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
