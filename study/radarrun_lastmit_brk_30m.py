"""LAST-MITIGATED S/R x RADAR RUNNER + LOSS CIRCUIT-BREAKER, 30m bucket — HONEST test (user 2026-08-27:
same rule as radarrun_lastmit_30m — broken R -> long only / broken S -> short only — PLUS: after a LOSING
position, take NO position until one more S/R level gets mitigated). Canonical harness: cached union fire
sets, 1-MINUTE first-touch, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, prop MC; recon per-year +
DAEMON OOS decisive.

PRE-REGISTERED (frozen; no iteration): S/R + tag exactly as radarrun_lastmit_30m (app/support_resistance
k=8 strict close-through on the same 30m series; tag = kind of the latest mitigation <= fire bar; ambiguous/
none -> excluded). BREAKER: a trade FAILS iff its 1m-resolved net < 0; from that trade's EXIT time, every
candidate fire is skipped until a NEW mitigation event (known at its 30m bucket's close time) occurs
STRICTLY AFTER the exit; the first fire at/after that re-arm is eligible again (direction rule unchanged).
Initial state: armed. CELLS: WITH-MIT no-breaker baseline / WITH-MIT+BRK (the rule) / ALL+BRK / AGAINST+BRK.
EXITS: 0.2% net, 0.4% net, RR 1:0.5, RR 1:1.
python study/radarrun_lastmit_brk_30m.py"""
import os, sys, json, time, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TPS = [("0.2% net", "fix", 0.0024), ("0.4% net", "fix", 0.0044), ("RR 1:0.5", "rr", 0.5), ("RR 1:1", "rr", 1.0)]


def features_and_mits(fires, A):
    """Per-fire tags (identical semantics to radarrun_lastmit_30m.features) + sorted mitigation-known TIMES
    (end_time of the breaking bucket; fallback next bucket's start / start+1800)."""
    from app import support_resistance as srm
    from study.candle_bias_1h import _f
    levels = srm.detect(A)
    by_i1 = {}
    for lv in levels:
        if lv["i1"] is not None:
            by_i1.setdefault(int(lv["i1"]), set()).add(lv["kind"])
    idxs = sorted(by_i1)
    mtimes = []
    for i1 in idxs:
        et = _f(A[i1].get("end_time"))
        if et <= 0:
            et = _f(A[i1 + 1].get("start_time")) if i1 + 1 < len(A) else (_f(A[i1].get("start_time")) + 1800.0)
        mtimes.append(et)
    mtimes.sort()
    recs = []
    for f in fires:
        b = int(f[0]); s = int(f[2])
        j = bisect.bisect_right(idxs, b) - 1
        tag = None
        if j >= 0:
            kinds = by_i1[idxs[j]]
            tag = next(iter(kinds)) if len(kinds) == 1 else None
        recs.append(dict(f=tuple(f), s=s,
                         with_mit=(tag == "R" and s > 0) or (tag == "S" and s < 0),
                         against=(tag == "S" and s > 0) or (tag == "R" and s < 0)))
    return recs, mtimes


def eval_1m_brk(fires, kind, val, T1, H1, L1, mtimes, breaker):
    """eval_1m mechanics (non-overlap walk, resolve_1m, same aggregates) + the loss circuit-breaker."""
    from study.radarrun_hyro_prop import mc, day_blocks
    from study.radarrun_bkt1h_deltapct_confirm import resolve_1m
    taken = []; busy = -1.0; unres = 0; disarm = None; skipped = 0
    for (b, t, s, e, sl) in fires:
        if t < busy:
            continue
        if breaker and disarm is not None:
            if bisect.bisect_right(mtimes, t) <= bisect.bisect_right(mtimes, disarm):
                skipped += 1; continue                       # no NEW mitigation in (exit, fire] -> stay flat
            disarm = None
        sld = abs(e - sl) / e
        if sld <= 0:
            continue
        g = val if kind == "fix" else val * sld
        net, tx = resolve_1m(s, e, sl, g, t, T1, H1, L1)
        if net is None:
            unres += 1; continue
        taken.append((t, net, net / sld, datetime.fromtimestamp(t, tz=timezone.utc).year)); busy = tx
        if net < 0:
            disarm = tx                                      # loss -> disarmed until the next mitigation
    if len(taken) < 10:
        return dict(n=len(taken)), skipped
    nets = np.array([x[1] for x in taken]); rs = np.array([x[2] for x in taken]); yrs = np.array([x[3] for x in taken])
    eq = np.cumsum(0.4 * rs); dd = float((np.maximum.accumulate(eq) - eq).max())
    d = dict(n=len(taken), W=int((nets > 0).sum()), L=int((nets < 0).sum()), win=100 * (nets > 0).mean(),
             avg=nets.mean() * 100, avgR=rs.mean(), dd=dd,
             y25=nets[yrs == 2025].mean() * 100 if (yrs == 2025).any() else float("nan"),
             y26=nets[yrs == 2026].mean() * 100 if (yrs == 2026).any() else float("nan"))
    m = mc(day_blocks([(x[0], x[1], x[2]) for x in taken]), 0.4, 4.0, "R"); d["prop"] = m["p"]; d["propd"] = m["d50"]
    return d, skipped


def report(recs, mtimes, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    cells = [("WITH-MIT", lambda r: r["with_mit"], False),
             ("WITH+BRK", lambda r: r["with_mit"], True),
             ("ALL+BRK", lambda r: True, True),
             ("AGAINST+BRK", lambda r: r["against"], True)]
    for name, keep, brk in cells:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in TPS:
            d, skipped = eval_1m_brk(fs, kind, val, T1, H1, L1, mtimes, brk)
            print("  %-12s %-9s %s  (blocked %d)" % (name, cname, fmt(d), skipped), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("LAST-MITIGATED S/R x RADAR RUNNER + LOSS BREAKER 30m BUCKET — canonical harness | loss -> flat until next mitigation | pre-registered\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (per-year split in rows)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    recs, mts = features_and_mits(load_fires("bucket", "30m"), A)
    report(recs, mts, T1, H1, L1)
    del A, T1, H1, L1
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    recs, mts = features_and_mits(frd, Ad)
    report(recs, mts, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
