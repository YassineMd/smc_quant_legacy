"""MMXSKEW forward-audit BOARD — run every MMXSKEW candidate's forward audit in one shot.

Lines:
  v1.3         — v1.1 (minus POC) + mov_mag>=39 + raw da2>0 (asymmetric)     [needs 1m for da2]
  v1.2-Relaxed — v1.1 + run_pos<=4 + mov_mag>=25
  v1.2-Dynamic — v1.1 + run_pos<=4 + mov_mag_ratio>=1.30

For each: builds the current tape via the candidate's own validate module, splits its TAKEN trades at its frozen
freeze_ts (bootstrapped on first run to match the standalone audits), prints in-sample vs FORWARD per RR + verdict,
and rolls up into one SUMMARY BOARD. Appends one combined row to study/out/mmxskew_audit_all_log.md (its own
board-history log — the per-line logs stay owned by the standalone audits). Pull a fresh snapshot/archive first
(study/pull_archive.ps1) so forward bars exist. (PIVOT V3 is a separate line — see study/pivot_v3_forward_audit.py.)

Note: rebuilds the tape once per candidate (v1.3 also loads the 1m archive), so a full run takes a few minutes.
Pre-declared per line: PASS fwd n>=20 & net>0 & t>=1.5 | FAIL n>=20 & net<=0 | degrade-warn n>=15 & exp<=0.
Run: python study/mmxskew_audit_all.py
"""
import os, sys, json, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO); sys.path.insert(0, HERE)
import study.mm_skew_v13_validate as V13
import study.mm_skew_v12r_validate as V12R
import study.mm_skew_v12d_validate as V12D

OUT = os.path.join(HERE, "out")
BOARD_LOG = os.path.join(OUT, "mmxskew_audit_all_log.md")
# Gate descriptions are DERIVED from each module's own constants, never hardcoded — get_freeze() writes this
# string into the freeze JSON, so a hardcoded copy silently records the wrong gate the moment a threshold moves
# (exactly what happened on the 2026-07-21 T=1.25 -> 1.30 re-freeze).
LINES = [
    ("v1.3",         V13,  "mmxskew_v13_freeze.json",
     "v1.1-noPOC + mov_mag>=%g + raw da2>0 (asymmetric)" % V13.MM_MIN),
    ("v1.2-Relaxed", V12R, "mmxskew_v12r_freeze.json",
     "v1.1-noPOC + run_pos<=%d + mov_mag>=%g" % (V12R.RUN_MAX, V12R.MM_MIN)),
    ("v1.2-Dynamic", V12D, "mmxskew_v12d_freeze.json",
     "v1.1-noPOC + run_pos<=%d + mov_mag_ratio>=%.2f" % (V12D.RUN_MAX, V12D.T_OPT)),
]


def stats(trs):
    n = len(trs)
    if n == 0:
        return dict(n=0, w=0, l=0, win=float("nan"), net=0.0, exp=0.0, t=0.0)
    nets = np.array([t["net"] * 100.0 for t in trs], float)
    w = int(sum(1 for t in trs if t["win"])); sd = nets.std(ddof=1) if n > 1 else 0.0
    tt = nets.mean() / (sd / np.sqrt(n)) if sd > 0 else 0.0
    return dict(n=n, w=w, l=n - w, win=100.0 * w / n, net=float(nets.sum()), exp=float(nets.mean()), t=float(tt))


def verdict(sf):
    if sf["n"] >= 20:
        if sf["exp"] > 0 and sf["t"] >= 1.5:
            return "PASS"
        return "FAIL" if sf["exp"] <= 0 else "CONTINUE(t<1.5)"
    return "CONTINUE(n<20)" + (" DEGRADE" if (sf["n"] >= 15 and sf["exp"] <= 0) else "")


def gate_params(V):
    """The module's gate CONSTANTS (all module-level UPPERCASE numerics) — the machine-checkable identity of a
    gate. Compared instead of the free-text description, which legitimately differs in wording between the board
    and the standalone audits ('v1.1 MINUS POC' vs 'v1.1-noPOC') and would raise false drift."""
    return {k: float(v) for k, v in vars(V).items()
            if k.isupper() and isinstance(v, (int, float)) and not isinstance(v, bool)}


def baseline_drift(fz, ins_stats, params=None):
    """Freeze guard: the in-sample half must still reproduce the numbers recorded AT freeze time. Any drift means
    the gate/feature/archive moved under a frozen candidate, so every forward comparison below it is meaningless.
    (At bootstrap freeze_ts = max trade t, so baseline == stats(all trades then) == stats(in-sample) forever.)

    Also compares the gate CONSTANTS. A threshold can move while the trade set stays bit-identical (the
    T=1.25 -> 1.30 re-freeze did exactly that), so the baseline numbers alone would pass and the stale gate
    would go unnoticed. Freezes predating this field skip the check rather than false-alarm."""
    out = []
    fp = fz.get("gate_params")
    if params and fp:
        for k in sorted(set(fp) | set(params)):
            if abs(float(fp.get(k, float("nan"))) - float(params.get(k, float("nan")))) > 0 or k not in fp or k not in params:
                out.append("GATE PARAM %s: frozen %s  now %s  -> re-freeze required" % (k, fp.get(k), params.get(k)))
    for rr in (1.0, 1.5):
        b = (fz.get("baseline") or {}).get(str(rr))
        if not b:
            continue
        s = ins_stats[rr]
        for k, tol in (("n", 0), ("net", 5e-6), ("exp", 5e-6), ("win", 5e-6)):
            if abs(float(s[k]) - float(b[k])) > tol:
                out.append("%s @1:%s  frozen %s=%s  now %s=%s" % (fz.get("gate", "?"), rr, k, b[k], k, s[k]))
    return out


def get_freeze(trades, path, gate_desc, params=None):
    if not os.path.exists(path):
        allt = [t["t"] for rr in trades for t in trades[rr]]; ft = max(allt) if allt else 0.0
        fz = dict(freeze_ts=ft, freeze_utc=datetime.datetime.utcfromtimestamp(ft).strftime("%Y-%m-%d %H:%M:%S UTC"),
                  gate=gate_desc, gate_params=(params or {}),
                  baseline={str(rr): stats(trades[rr]) for rr in (1.0, 1.5)},
                  predeclared="PASS fwd n>=20 & net>0 & t>=1.5 | FAIL n>=20 & net<=0 | degrade-warn n>=15 & exp<=0")
        os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(fz, open(path, "w"), indent=1)
        print("  (bootstrapped freeze)")
    return json.load(open(path))


def main():
    board = []; drift = []
    for name, V, fzn, desc in LINES:
        print("=" * 92); print("%s   [%s]" % (name, desc)); print("=" * 92)
        A, sigs = V.build(); trades = {rr: V.taken(A, sigs, rr) for rr in (1.0, 1.5)}
        gp = gate_params(V)
        fz = get_freeze(trades, os.path.join(OUT, fzn), desc, gp); ft = fz["freeze_ts"]
        print("  freeze_ts %d (%s)" % (int(ft), fz["freeze_utc"]))
        row = dict(name=name); ins_stats = {}
        for rr in (1.0, 1.5):
            ins = [t for t in trades[rr] if t["t"] <= ft]; fwd = [t for t in trades[rr] if t["t"] > ft]
            si, sf = stats(ins), stats(fwd); v = verdict(sf); ins_stats[rr] = si
            print("  RR 1:%s | in-sample n=%-3d win %4.0f%% exp %+.3f%%  ||  FORWARD n=%-3d win %s exp %+.3f%% t=%+.2f  ->  %s"
                  % (rr, si["n"], si["win"], si["exp"], sf["n"], ("%3.0f%%" % sf["win"] if sf["n"] else " -- "),
                     sf["exp"], sf["t"], v))
            row[rr] = (sf, v)
        _d = baseline_drift(fz, ins_stats, gp); drift += _d
        print("  baseline guard: %s" % ("OK (in-sample reproduces the freeze)" if not _d else "*** DRIFT ***"))
        board.append(row); print()

    print("=" * 92); print("SUMMARY BOARD — FORWARD (out-of-sample) only"); print("=" * 92)
    print("| candidate | fwd n (1.0/1.5) | fwd win% (1.0/1.5) | fwd exp% (1.0/1.5) | verdict (1.0/1.5) |")
    print("|---|---|---|---|---|")
    for row in board:
        (s10, v10), (s15, v15) = row[1.0], row[1.5]
        wf = lambda s: ("%.0f" % s["win"] if s["n"] else "--")
        print("| %s | %d / %d | %s / %s | %+.3f / %+.3f | %s / %s |"
              % (row["name"], s10["n"], s15["n"], wf(s10), wf(s15), s10["exp"], s15["exp"], v10, v15))

    if drift:                       # GUARD: never log a forward row against a baseline the code no longer reproduces
        print("\n" + "!" * 92)
        print("FREEZE DRIFT - in-sample no longer matches the recorded baseline. Board log NOT appended.")
        for d in drift:
            print("  " + d)
        print("A frozen gate/feature/archive moved. Fix the drift or re-freeze deliberately; do NOT re-tune.")
        print("!" * 92)
        raise SystemExit(1)

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fresh = not os.path.exists(BOARD_LOG)
    with open(BOARD_LOG, "a", encoding="utf-8") as f:
        if fresh:
            f.write("# MMXSKEW audit board - history (append-only)\n\n| run (UTC) | board (fwd n/win/exp/verdict @1:1.5) |\n|---|---|\n")
        cells = "  ".join("%s: n%d win%s exp%+.3f %s"
                          % (row["name"], row[1.5][0]["n"], ("%.0f%%" % row[1.5][0]["win"] if row[1.5][0]["n"] else "--"),
                             row[1.5][0]["exp"], row[1.5][1]) for row in board)
        f.write("| %s | %s |\n" % (stamp, cells))
    print("\n  board history -> %s" % os.path.relpath(BOARD_LOG, REPO))


if __name__ == "__main__":
    main()
