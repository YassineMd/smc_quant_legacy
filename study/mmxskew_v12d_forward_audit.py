"""MMXSKEW v1.2-Dynamic gate — FORWARD AUDIT (mirrors study/mmxskew_v12r_forward_audit.py).

Frozen gate: v1.1 + run_pos<=4 + mov_mag_ratio>=1.30 (see study/mm_skew_v12d_validate.py). Splits taken trades at
freeze_ts (bootstrapped first run = newest in-sample entry), prints in-sample vs FORWARD per RR + verdict, appends
study/out/mmxskew_v12d_forward_log.md. No 1m dependency — a plain snapshot/archive pull is enough forward.

CANDIDATE with in-sample-tuned T (curve-fit) — THIS decides it. NEVER re-tune (any change = new freeze).
Pre-declared: PASS = fwd n>=20 & net>0 & t>=1.5 ; FAIL = fwd n>=20 & net<=0 ; degrade-warn = fwd n>=15 & exp<=0.
Run: python study/mmxskew_v12d_forward_audit.py
"""
import os, sys, json, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import study.mm_skew_v12d_validate as V12D

FREEZE = os.path.join(HERE, "out", "mmxskew_v12d_freeze.json")
LOG = os.path.join(HERE, "out", "mmxskew_v12d_forward_log.md")


def stats(trs):
    n = len(trs)
    if n == 0:
        return dict(n=0, w=0, l=0, win=float("nan"), net=0.0, exp=0.0, t=0.0)
    nets = np.array([t["net"] * 100.0 for t in trs], float)
    w = int(sum(1 for t in trs if t["win"])); sd = nets.std(ddof=1) if n > 1 else 0.0
    tt = nets.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return dict(n=n, w=w, l=n - w, win=100.0 * w / n, net=float(nets.sum()), exp=float(nets.mean()), t=float(tt))


def line(tag, s):
    return ("  %-16s n=%-3d | W %-2d L %-2d | win %5.1f%% | net %+.2f%% | exp %+.3f%%/tr | t=%+.2f"
            % (tag, s["n"], s["w"], s["l"], s["win"], s["net"], s["exp"], s["t"]))


def main():
    A, sigs = V12D.build()
    trades = {rr: V12D.taken(A, sigs, rr) for rr in (1.0, 1.5)}

    if not os.path.exists(FREEZE):
        allt = [t["t"] for rr in trades for t in trades[rr]]
        ft = max(allt) if allt else 0.0
        fz = dict(freeze_ts=ft, freeze_utc=datetime.datetime.utcfromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S UTC"),
                  gate="v1.1 MINUS POC + run_pos<=4 + mov_mag_ratio>=%.2f (v1.2-Dynamic)" % V12D.T_OPT,
                  baseline={str(rr): stats(trades[rr]) for rr in (1.0, 1.5)},
                  predeclared="PASS fwd n>=20 & net>0 & t>=1.5 | FAIL n>=20 & net<=0 | degrade-warn n>=15 & exp<=0")
        os.makedirs(os.path.dirname(FREEZE), exist_ok=True)
        json.dump(fz, open(FREEZE, "w"), indent=1)
        print("BOOTSTRAPPED freeze -> %s  (freeze_ts %d = %s)\n" % (os.path.relpath(FREEZE, REPO), int(ft), fz["freeze_utc"]))

    fz = json.load(open(FREEZE)); ft = fz["freeze_ts"]
    print("MMXSKEW v1.2-Dynamic gate forward audit  |  freeze_ts %d (%s)" % (int(ft), fz["freeze_utc"]))
    row_bits = []
    for rr in (1.0, 1.5):
        ins = [t for t in trades[rr] if t["t"] <= ft]; fwd = [t for t in trades[rr] if t["t"] > ft]
        si, sf = stats(ins), stats(fwd)
        print("  --- RR 1:%s ---" % rr)
        print(line("in-sample", si)); print(line("FORWARD", sf))
        v = "CONTINUE (fwd n<20)"
        if sf["n"] >= 20:
            v = "PASS" if (sf["exp"] > 0 and sf["t"] >= 1.5) else ("FAIL" if sf["exp"] <= 0 else "CONTINUE (t<1.5)")
        warn = " DEGRADE-WARN" if (sf["n"] >= 15 and sf["exp"] <= 0) else ""
        print("  VERDICT RR1:%s: %s%s" % (rr, v, warn))
        row_bits.append("1:%s fwd n%d win%.0f%% exp%+.3f%% %s%s" % (rr, sf["n"], sf["win"], sf["exp"], v, warn))

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fresh = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8") as f:
        if fresh:
            f.write("# MMXSKEW v1.2-Dynamic gate - forward log (append-only)\n\n")
            f.write("Gate: v1.1 + run_pos<=4 + mov_mag_ratio>=%.2f (T tuned in-sample). Freeze_ts %d = %s.\n"
                    % (V12D.T_OPT, int(ft), fz["freeze_utc"]))
            for rr in ("1.0", "1.5"):
                b = fz["baseline"][rr]
                f.write("  RR 1:%s in-sample n=%d win %.1f%% exp %+.3f%%/tr (T curve-fit, n<20).\n" % (rr, b["n"], b["win"], b["exp"]))
            f.write("%s\n\n| run (UTC) | forward summary |\n|---|---|\n" % fz["predeclared"])
        f.write("| %s | %s |\n" % (stamp, " ; ".join(row_bits)))
    print("  appended -> %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
