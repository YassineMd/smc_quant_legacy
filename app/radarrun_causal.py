"""Causal per-close Radar Runner replay for HISTORY — the replay/hindsight fix (2026-09-04).

WHY: the terminal draws Radar Runner from ONE batch detect over the whole loaded history. That batch is NOT causal:
the wall layer (absorption_level_detect) re-evaluates a wall with the bars that came AFTER a breakout, and 69% of the
badges that fired at a bar's close are erased by the very next bar (proven on the Jul-Aug 2026 daemon 30m set:
study union 837 fires, hindsight batch keeps 281). Live, the terminal persists every fire the instant it appears
(data/radarrun_fired.json), so the LIVE chart is right — but history it never watched live (replay, reload, the days
the terminal was closed or on another tf) only shows the hindsight third.

WHAT: replay the live terminal's per-close detection over history — at each close k the detectors see ONLY the bars
up to k (a trailing RR_CAUSAL_WARM window, = the canonical study harness), and every signal that appears for the
first time is frozen by its bar's end_time, exactly like the live persist step. The terminal runs this in a
BACKGROUND PROCESS (never on the UI thread; lowest OS priority) and unions the result into the persisted record, so
the existing draw path shows it. Coverage (which closes were replayed) is tracked per tf|source on disk, so each close
is computed once, ever.

CLI (spawned by the terminal):  python -m app.radarrun_causal <in.pkl> <out.json> <tf> <k_from> <k_to>
  in.pkl = {"buckets": [...], "seen": [end_times already persisted], "warm": W}
  out.json = {"tf", "k_from", "k_to", "fires": [{i, et, side, entry, sl, tp, kind, hc, absorbed, k}]}
"""
import os
import sys
import json
import pickle

W_DEFAULT = 2000          # trailing bars each per-close detect sees (== study harness W; walls older than this are rare)


def sl_buf_for(tf) -> float:
    """The terminal's forward-optimised candle-SL buffer: 1h 0.2% / everything else 0.3%."""
    return 0.002 if tf == "1h" else 0.003


def conviction(buckets, i, side) -> bool:
    """HIGH-CONVICTION flag (frozen at fire): breakout bar strength forceful (effort z >= STR_EFFORT_HI, aligned) AND
    the recent reward/eff (last 50, aligned) favours it. Shared by the live terminal and the causal replay."""
    try:
        from . import reward_eff
        up = side > 0
        base = reward_eff.strength_baseline(buckets, i)
        bo = 0.0
        if base and base.get("vol"):
            st = reward_eff.strength(buckets, i, i, base=base)
            if st.get("ok"):
                bo = st["buy" if up else "sell"]["effort_z"]
        sh, ok = reward_eff.share(buckets, i - 49, i)
        rf = (sh if up else 100.0 - sh) if ok else 50.0
        return bool(bo >= reward_eff.STR_EFFORT_HI and rf > 50.0)
    except Exception:
        return False


def absorbed(buckets, i) -> bool:
    """ABSORBED-breakout flag (frozen at fire): the breakout bar's Absorption R >= 0."""
    try:
        from . import absorption
        a = absorption.absorption(buckets, i)[0]
        return bool(a is not None and a >= 0.0)
    except Exception:
        return False


def causal_union(buckets, tf, k_from, k_to, warm=W_DEFAULT, tp_frac=None, seen=None, progress=None, flags=True):
    """Per-close first-appearance union over buckets[k_from..k_to] (absolute indices, inclusive).

    At close k the detectors see buckets[max(0, k-warm) : k+1] — nothing after k. Every triangle (detect) and diamond
    (detect_wick, same geometry the terminal merges) whose bar end_time is not yet in `seen` is frozen and returned.
    `seen` = end_times already persisted (live fires) so nothing is duplicated; it is extended in place.
    Returns [{i, et, side, entry, sl, tp, kind, hc, absorbed, k}] with i absolute in `buckets`."""
    from . import config, radar_breakout_detect as RB, absorption_level_detect as AL
    tp = config.RR_TP_FRAC if tp_frac is None else tp_frac
    slb = sl_buf_for(tf)
    if seen is None:
        seen = set()
    out = []
    n = len(buckets)
    k_to = min(int(k_to), n - 1)
    k_from = max(0, int(k_from))
    for k in range(k_from, k_to + 1):
        lo = max(0, k - warm)
        bs = buckets[lo:k + 1]
        try:
            walls = AL.detect(bs, skip_last=False)
            ents = [(0, e) for e in RB.detect(bs, walls=walls, skip_last=False, sl_buf=slb, tp_frac=tp)]
            ents += [(1, e) for e in RB.detect_wick(bs, walls=walls, skip_last=False, same_dir=True, wick_min=0.5)]
        except Exception:
            continue
        for src, e in ents:                                    # triangles first, then diamonds: same bar -> triangle wins
            i = lo + int(e.get("i", -1))
            if not (0 <= i <= k):
                continue
            b = buckets[i]
            et = float(b.get("end_time", 0.0) or 0.0)
            if et <= 0 or et in seen:
                continue
            side = int(e.get("side", 0))
            if side == 0:
                continue
            if src == 1:                                       # RADAR DIAMOND: the terminal's merge geometry, verbatim
                entry = float(e.get("entry", b.get("close", b.get("close_price", 0.0))) or 0.0)
                rlo = float(e.get("radar_lo", 0.0) or 0.0); rhi = float(e.get("radar_hi", 0.0) or 0.0)
                if entry <= 0 or rlo <= 0 or rhi <= 0:
                    continue
                blo = float(b.get("low", 0.0) or 0.0); bhi = float(b.get("high", 0.0) or 0.0)
                sl = max(blo * (1 - slb), rlo) if side > 0 else min(bhi * (1 + slb), rhi)
                tpv = entry * (1.0 + side * tp); kind = "wick"
            else:
                entry = float(e.get("entry", 0.0) or 0.0)
                sl = float(e.get("sl_trade", e.get("sl", 0.0)) or 0.0)
                tpv = float(e.get("tp_trade", 0.0) or 0.0); kind = "run"
            seen.add(et)
            rec = dict(i=i, et=et, side=side, entry=entry, sl=sl, tp=tpv, kind=kind, k=k)
            if flags:
                rec["hc"] = conviction(buckets, i, side)
                rec["absorbed"] = absorbed(buckets, i)
            out.append(rec)
        if progress is not None and (k - k_from) % 25 == 0:
            progress(k, k_from, k_to)
    return out


def _lowest_priority() -> None:
    """Never starve the live terminal: PROCESS_MODE_BACKGROUND_BEGIN on Windows (lowest CPU + I/O + memory
    priority — the same mode the heavy studies run under), nice(19) elsewhere. Best effort."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
        else:
            os.nice(19)
    except Exception:
        pass


def _main(argv) -> int:
    if len(argv) < 5:
        print("usage: python -m app.radarrun_causal <in.pkl> <out.json> <tf> <k_from> <k_to>", file=sys.stderr)
        return 2
    in_path, out_path, tf, k_from, k_to = argv[0], argv[1], argv[2], int(argv[3]), int(argv[4])
    _lowest_priority()
    with open(in_path, "rb") as f:
        payload = pickle.load(f)
    buckets = payload["buckets"]
    seen = set(float(x) for x in payload.get("seen", ()))

    def prog(k, a, b):
        print("PROG %d %d %d" % (k, a, b), flush=True)

    fires = causal_union(buckets, tf, k_from, k_to, warm=int(payload.get("warm", W_DEFAULT)), seen=seen, progress=prog)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tf": tf, "k_from": k_from, "k_to": k_to, "fires": fires}, f)
    os.replace(tmp, out_path)
    print("DONE %d" % len(fires), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
