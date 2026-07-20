"""MMXSKEW v1.2 CANDIDATE GATE — FORWARD AUDIT (periodic check; mirrors study/pivot_v3_forward_audit.py).

Runs the frozen v1.2 gate (run_pos<=4 AND mov_mag>=39) on the current tape, splits each TAKEN trade at freeze_ts
(bootstrapped on the FIRST run = the newest in-sample signal's entry), and prints in-sample vs FORWARD
n/W/L/net/exp/t per RR, a verdict, and appends one dated row to study/out/mmxskew_v12_forward_log.md.

The gate is a CANDIDATE (study/MMXSKEW_V12.md) — mined in-sample n=13-14, so this forward test is what decides it.
NEVER re-tune: any threshold change = a NEW freeze (delete the json + log). Pull a fresh snapshot first
(study/pull_snapshot.ps1 / the archive) so forward bars exist.

Pre-declared: PASS = fwd n>=20 & net>0 & t>=1.5 ; FAIL = fwd n>=20 & net<=0 ; degrade-warn = fwd n>=15 & exp<=0.
Run: python study/mmxskew_v12_forward_audit.py
"""
import os, sys, json, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import study.mm_skew_feature_matrix as FM
import study.mm_skew_rr_sweep as RR   # noqa: F401 (used via mm_skew_gate_v12.walk)
import study.mm_skew_gate_v12 as G

FREEZE = os.path.join(HERE, "out", "mmxskew_v12_freeze.json")
LOG = os.path.join(HERE, "out", "mmxskew_v12_forward_log.md")
RUN_MAX, MM_MIN = 4, 39.0
GATE = lambda sg: sg["run"] <= RUN_MAX and sg["mm"] >= MM_MIN


def stats(trs):
    n = len(trs)
    if n == 0:
        return dict(n=0, w=0, l=0, win=float("nan"), net=0.0, exp=0.0, t=0.0)
    nets = np.array([x["net"] for x in trs], float)
    w = int(sum(1 for x in trs if x["win"])); sd = nets.std(ddof=1) if n > 1 else 0.0
    t = nets.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return dict(n=n, w=w, l=n - w, win=100.0 * w / n, net=float(nets.sum()), exp=float(nets.mean()), t=float(t))


def line(tag, s):
    return ("  %-16s n=%-3d | W %-2d L %-2d | win %5.1f%% | net %+.2f%% | exp %+.3f%%/tr | t=%+.2f"
            % (tag, s["n"], s["w"], s["l"], s["win"], s["net"], s["exp"], s["t"]))


def main():
    A, first, _, _ = FM.build()
    sigs = G.all_signals(A, first)
    trades = {rr: G.walk(A, sigs, GATE, rr) for rr in (1.0, 1.5)}

    if not os.path.exists(FREEZE):
        allt = [x["t"] for rr in trades for x in trades[rr]]
        ft = max(allt) if allt else 0.0
        fz = dict(freeze_ts=ft, freeze_utc=datetime.datetime.utcfromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S UTC"),
                  gate=dict(run_max=RUN_MAX, mm_min=MM_MIN),
                  baseline={str(rr): stats(trades[rr]) for rr in (1.0, 1.5)},
                  predeclared="PASS fwd n>=20 & net>0 & t>=1.5 | FAIL n>=20 & net<=0 | degrade-warn n>=15 & exp<=0")
        os.makedirs(os.path.dirname(FREEZE), exist_ok=True)
        json.dump(fz, open(FREEZE, "w"), indent=1)
        print("BOOTSTRAPPED freeze -> %s  (freeze_ts %d = %s)\n" % (os.path.relpath(FREEZE, REPO), int(ft), fz["freeze_utc"]))

    fz = json.load(open(FREEZE)); ft = fz["freeze_ts"]
    print("MMXSKEW v1.2 gate forward audit  |  gate run_pos<=%d & mov_mag>=%.0f  |  freeze_ts %d (%s)"
          % (RUN_MAX, MM_MIN, int(ft), fz["freeze_utc"]))
    row_bits = []
    for rr in (1.0, 1.5):
        ins = [x for x in trades[rr] if x["t"] <= ft]; fwd = [x for x in trades[rr] if x["t"] > ft]
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
            f.write("# MMXSKEW v1.2 gate - forward log (append-only)\n\n")
            f.write("Candidate gate run_pos<=%d & mov_mag>=%.0f. Freeze_ts %d = %s. In-sample baseline (n~13-14, MINED):\n"
                    % (RUN_MAX, MM_MIN, int(ft), fz["freeze_utc"]))
            for rr in ("1.0", "1.5"):
                b = fz["baseline"][rr]
                f.write("  RR 1:%s in-sample n=%d win %.1f%% exp %+.3f%%/tr.\n" % (rr, b["n"], b["win"], b["exp"]))
            f.write("%s\n\n| run (UTC) | forward summary |\n|---|---|\n" % fz["predeclared"])
        f.write("| %s | %s |\n" % (stamp, " ; ".join(row_bits)))
    print("  appended -> %s" % os.path.relpath(LOG, REPO))


if __name__ == "__main__":
    main()
