"""DA2-REVERSION v1.0 — FORWARD AUDIT. Runs the FROZEN rule unchanged and splits its taken trades at freeze_ts.

Bootstraps study/out/da2_reversion_freeze.json on first run (freeze_ts = the last in-sample trade's start_time,
so forward n starts at 0), then on every later run re-runs the frozen rule, asserts the in-sample half still
reproduces the recorded baseline, and appends ONE dated row to study/out/da2_reversion_forward_log.md.

NEVER RE-TUNE. Any change to SL_PCT / TP_PCT / the signal means a NEW freeze — bump freeze_ts and discard the
prior forward log. The gate CONSTANTS are stored in the freeze and asserted here, because a threshold can move
while the trade set stays identical and the baseline numbers alone would not catch it.

Pre-declared verdict: PASS at fwd n>=40 & net>0 & t>=1.5 · FAIL at n>=40 & net<=0 · degrade-warn n>=25 & exp<=0.
(n>=40 not 20: this line's in-sample edge is ~+0.13%/trade with a ~0.9% per-trade SD, so 20 trades cannot
separate it from zero.)

Run: python study/da2_reversion_forward_audit.py   (pull a fresh archive first: study/pull_archive.ps1)
"""
import os, sys, json, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import study.da2_reversion_validate as V

OUT = os.path.join(HERE, "out")
FREEZE = os.path.join(OUT, "da2_reversion_freeze.json")
LOG = os.path.join(OUT, "da2_reversion_forward_log.md")
PRE = "PASS fwd n>=40 & net>0 & t>=1.5 | FAIL n>=40 & net<=0 | degrade-warn n>=25 & exp<=0"


def stats(trs):
    n = len(trs)
    if n == 0:
        return dict(n=0, w=0, win=float("nan"), net=0.0, exp=0.0, t=0.0)
    nets = np.array([t["net"] * 100.0 for t in trs], float)
    w = int(sum(1 for t in trs if t["win"])); sd = nets.std(ddof=1) if n > 1 else 0.0
    tt = nets.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return dict(n=n, w=w, win=100.0 * w / n, net=float(nets.sum()), exp=float(nets.mean()), t=float(tt))


def gate_params():
    return {"SL_PCT": float(V.SL_PCT), "TP_PCT": float(V.TP_PCT), "FEE": float(V.FEE)}


def verdict(sf):
    if sf["n"] >= 40:
        if sf["exp"] > 0 and sf["t"] >= 1.5:
            return "PASS"
        return "FAIL" if sf["exp"] <= 0 else "CONTINUE(t<1.5)"
    return "CONTINUE(n<40)" + (" DEGRADE" if (sf["n"] >= 25 and sf["exp"] <= 0) else "")


def main():
    bars, sigs = V.build()
    trades = V.taken(bars, sigs)
    if not trades:
        raise SystemExit("da2_reversion: no trades — archive missing or 1m coverage absent.")

    if not os.path.exists(FREEZE):
        ft = max(t["t"] for t in trades)
        fz = dict(freeze_ts=ft,
                  freeze_utc=datetime.datetime.utcfromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S UTC"),
                  gate="da2 OPPOSED to candle · fixed SL %.3f / TP %.3f" % (V.SL_PCT, V.TP_PCT),
                  gate_params=gate_params(), baseline=stats(trades), predeclared=PRE)
        os.makedirs(OUT, exist_ok=True)
        json.dump(fz, open(FREEZE, "w"), indent=1)
        print("  (bootstrapped freeze)")
    fz = json.load(open(FREEZE)); ft = fz["freeze_ts"]

    ins = [t for t in trades if t["t"] <= ft]; fwd = [t for t in trades if t["t"] > ft]
    si, sf = stats(ins), stats(fwd)

    drift = []
    fp = fz.get("gate_params") or {}; cur = gate_params()
    for k in sorted(set(fp) | set(cur)):
        if k not in fp or k not in cur or abs(float(fp[k]) - float(cur[k])) > 0:
            drift.append("GATE PARAM %s: frozen %s now %s" % (k, fp.get(k), cur.get(k)))
    b = fz.get("baseline") or {}
    for k, tol in (("n", 0), ("net", 5e-6), ("exp", 5e-6), ("win", 5e-6)):
        if k in b and abs(float(si[k]) - float(b[k])) > tol:
            drift.append("BASELINE %s: frozen %s now %s" % (k, b[k], si[k]))

    print("=" * 92)
    print("DA2-REVERSION v1.0   [%s]" % fz["gate"])
    print("=" * 92)
    print("  freeze_ts %d (%s)" % (int(ft), fz["freeze_utc"]))
    print("  break-even win rate at this bracket: %.1f%%" % V.breakeven())
    print("  in-sample n=%-3d win %4.1f%% exp %+.4f%% net %+.1f%% t=%+.2f"
          % (si["n"], si["win"], si["exp"], si["net"], si["t"]))
    print("  FORWARD   n=%-3d win %s exp %+.4f%% net %+.1f%% t=%+.2f   ->  %s"
          % (sf["n"], ("%4.1f%%" % sf["win"]) if sf["n"] else " -- ", sf["exp"], sf["net"], sf["t"], verdict(sf)))
    print("  baseline guard: %s" % ("OK (in-sample reproduces the freeze)" if not drift else "*** DRIFT ***"))

    if drift:
        print("\n" + "!" * 92)
        print("FREEZE DRIFT — the frozen rule no longer reproduces. Forward log NOT appended.")
        for d in drift:
            print("  " + d)
        print("Fix the drift or re-freeze deliberately; do NOT re-tune.")
        print("!" * 92)
        raise SystemExit(1)

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fresh = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if fresh:
            f.write("# DA2-REVERSION v1.0 — forward log (append-only)\n\n")
            f.write("Gate: da2 OPPOSED to candle direction · fixed SL %.1f%% / TP %.1f%% · break-even %.1f%%.\n"
                    % (V.SL_PCT * 100, V.TP_PCT * 100, V.breakeven()))
            f.write("Freeze_ts %d = %s. %s\n\n" % (int(ft), fz["freeze_utc"], PRE))
            f.write("| run (UTC) | forward summary |\n|---|---|\n")
        f.write("| %s | n=%d win %s exp %+.4f%% net %+.1f%% t=%+.2f %s |\n"
                % (stamp, sf["n"], ("%.1f%%" % sf["win"]) if sf["n"] else "--",
                   sf["exp"], sf["net"], sf["t"], verdict(sf)))
    print("\n  forward log -> %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
