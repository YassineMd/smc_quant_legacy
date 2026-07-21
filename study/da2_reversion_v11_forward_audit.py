"""DA2-REVERSION v1.1 — FORWARD AUDIT. Same discipline as the v1.0 audit; runs BESIDE it, never instead of it.

v1.0 and v1.1 each hold their own freeze and their own append-only log, so the pair can be compared on forward
tape without either one being re-tuned. Bootstraps study/out/da2_reversion_v11_freeze.json on first run.

Pre-declared: PASS fwd n>=40 & net>0 & t>=1.5 · FAIL n>=40 & net<=0 · degrade-warn n>=25 & exp<=0.
NEVER RE-TUNE — a change to SPREAD_MAX / SL / TP / the signal means a NEW freeze and a discarded forward log.

Run: python study/da2_reversion_v11_forward_audit.py   (pull a fresh archive first: study/pull_archive.ps1)
"""
import os, sys, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import study.da2_reversion_v11_validate as V
from study.da2_reversion_forward_audit import stats, verdict     # shared, identical accounting

OUT = os.path.join(HERE, "out")
FREEZE = os.path.join(OUT, "da2_reversion_v11_freeze.json")
LOG = os.path.join(OUT, "da2_reversion_v11_forward_log.md")
PRE = "PASS fwd n>=40 & net>0 & t>=1.5 | FAIL n>=40 & net<=0 | degrade-warn n>=25 & exp<=0"


def gate_params():
    return {"SPREAD_MAX": float(V.SPREAD_MAX), "SL_PCT": float(V.SL_PCT),
            "TP_PCT": float(V.TP_PCT), "FEE": float(V.FEE)}


def main():
    bars, sigs = V.build()
    trades = V.taken(bars, sigs)
    if not trades:
        raise SystemExit("da2_reversion_v11: no trades — archive missing or 1m coverage absent.")

    if not os.path.exists(FREEZE):
        ft = max(t["t"] for t in trades)
        json.dump(dict(freeze_ts=ft,
                       freeze_utc=datetime.datetime.utcfromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S UTC"),
                       gate="v1.0 + |eff-agg spread| <= %.0f (non-locked, causal)" % V.SPREAD_MAX,
                       gate_params=gate_params(), baseline=stats(trades), predeclared=PRE),
                  open(FREEZE, "w"), indent=1)
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

    print("=" * 92); print("DA2-REVERSION v1.1   [%s]" % fz["gate"]); print("=" * 92)
    print("  freeze_ts %d (%s)   break-even %.1f%%" % (int(ft), fz["freeze_utc"], V.breakeven()))
    print("  in-sample n=%-3d win %4.1f%% exp %+.4f%% net %+.1f%% t=%+.2f" % (si["n"], si["win"], si["exp"], si["net"], si["t"]))
    print("  FORWARD   n=%-3d win %s exp %+.4f%% net %+.1f%% t=%+.2f   ->  %s"
          % (sf["n"], ("%4.1f%%" % sf["win"]) if sf["n"] else " -- ", sf["exp"], sf["net"], sf["t"], verdict(sf)))
    print("  baseline guard: %s" % ("OK (in-sample reproduces the freeze)" if not drift else "*** DRIFT ***"))
    if drift:
        print("\n" + "!" * 92)
        print("FREEZE DRIFT — the frozen rule no longer reproduces. Forward log NOT appended.")
        for d in drift:
            print("  " + d)
        print("!" * 92)
        raise SystemExit(1)

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fresh = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if fresh:
            f.write("# DA2-REVERSION v1.1 — forward log (append-only)\n\n")
            f.write("Gate: da2 opposed to candle + |eff-agg spread| <= %.0f · fixed SL %.1f%% / TP %.1f%% · break-even %.1f%%.\n"
                    % (V.SPREAD_MAX, V.SL_PCT * 100, V.TP_PCT * 100, V.breakeven()))
            f.write("Freeze_ts %d = %s. %s\n\n" % (int(ft), fz["freeze_utc"], PRE))
            f.write("| run (UTC) | forward summary |\n|---|---|\n")
        f.write("| %s | n=%d win %s exp %+.4f%% net %+.1f%% t=%+.2f %s |\n"
                % (stamp, sf["n"], ("%.1f%%" % sf["win"]) if sf["n"] else "--", sf["exp"], sf["net"], sf["t"], verdict(sf)))
    print("\n  forward log -> %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
