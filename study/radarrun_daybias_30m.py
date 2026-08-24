"""DAY-BIAS x RADAR RUNNER, 30m bucket — HONEST study (user 2026-08-25). Canonical harness throughout:
union-persist fire sets (recon rr30mbkt_live_fires_union.json / daemon rr_union_b30m_daemon_m30.json),
1-MINUTE first-touch resolution, non-overlap taken(), fees 0.04% RT + 0.03% slip/leg, per-year recon split,
DAEMON 30m = the decisive OOS. Two exits: 0.5% net + RR 1:1 (the standing pair).

PRE-REGISTERED STATES (frozen before any result was seen; NO iteration beyond these cells):

AXIS A — price vs YESTERDAY's volume profile (causal):
  prev-day VP = per-price volume summed over all 30m buckets whose start_time falls in the fire bar's
  previous UTC day (levels b+s, prices as stored, 2dp). POC = heaviest price; VALUE AREA = classic 70%:
  grow from the POC, at each step adding whichever ADJACENT price row (above or below the current span)
  carries more volume, until >= 70% of the day's volume is inside. State at the fire = ENTRY price vs VA:
  ABOVE (> VAH) / INSIDE (VAL..VAH) / BELOW (< VAL). Days with < 10 buckets -> UNKNOWN (excluded, counted).

AXIS B — TODAY's wall ledger (causal, terminal-identical walls):
  walls = absorption_level_detect.detect over the trailing W=2000 window ENDING AT the fire bar
  (skip_last=False) — exactly the wall set the canonical union replay saw at that bar. today = the fire
  bar's UTC day. created_S/R = walls whose formation bucket (i0) starts today, up to the fire bar;
  mitig_S/R = broken walls whose close-through bucket (i1) starts today. LEDGER bias =
  (created_S - mitig_S) - (created_R - mitig_R); state UP (bias > 0) / NEUT (0) / DOWN (< 0).

CELLS: ALL control; A: ABOVE/INSIDE/BELOW; B: UP/NEUT/DOWN; ALIGNED/AGAINST per axis
(LONG&ABOVE|SHORT&BELOW aligned on A; LONG&UP|SHORT&DOWN aligned on B); ALIGNED/AGAINST on BOTH.
python study/radarrun_daybias_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
W = 2000
_A = None


def _day(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).date()


def build_day_vps(A):
    """{utc_date: (VAL, POC, VAH)} from per-price 30m footprints, classic 70% value area."""
    from study.candle_bias_1h import _f
    days = {}
    for b in A:
        d = _day(_f(b.get("start_time", 0)))
        acc = days.setdefault(d, {})
        for p, v in (b.get("levels") or {}).items():
            try:
                pf = round(float(p), 2)
            except (TypeError, ValueError):
                continue
            acc[pf] = acc.get(pf, 0.0) + float(v.get("b", 0.0)) + float(v.get("s", 0.0))
    out = {}
    for d, acc in days.items():
        if len(acc) < 5:
            continue
        prices = sorted(acc)
        vols = np.array([acc[p] for p in prices]); tot = vols.sum()
        if tot <= 0:
            continue
        poc = int(np.argmax(vols)); lo = hi = poc; cum = vols[poc]
        while cum < 0.70 * tot and (lo > 0 or hi < len(prices) - 1):
            vlo = vols[lo - 1] if lo > 0 else -1.0
            vhi = vols[hi + 1] if hi < len(prices) - 1 else -1.0
            if vhi >= vlo:
                hi += 1; cum += vols[hi]
            else:
                lo -= 1; cum += vols[lo]
        out[d] = (prices[lo], prices[poc], prices[hi])
    return out


def _init(daemon):
    global _A
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    if daemon:
        _A = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    else:
        _A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1],
                    key=lambda b: _f(b.get("start_time", 0)))


def _work(chunk):
    """Per-fire AXIS-B wall-ledger state (walls recomputed as-of the fire bar, terminal-identical)."""
    from app import absorption_level_detect as AL
    from study.candle_bias_1h import _f
    st_all = [_f(b.get("start_time", 0)) for b in _A]
    out = []
    for f in chunk:
        b = int(f[0])
        off = max(0, b - W)
        try:
            walls = AL.detect(_A[off:b + 1], skip_last=False)
        except Exception:
            walls = []
        today = _day(st_all[b])
        cs = cr = ms = mr = 0
        for m in walls:
            side = m.get("side")
            if side not in ("S", "R"):
                continue
            i0 = off + int(m.get("i0", 0))
            if 0 <= i0 <= b and _day(st_all[i0]) == today:
                if side == "S":
                    cs += 1
                else:
                    cr += 1
            i1 = m.get("i1")
            if bool(m.get("broken")) and i1 is not None:
                i1 = off + int(i1)
                if 0 <= i1 <= b and _day(st_all[i1]) == today:
                    if side == "S":
                        ms += 1
                    else:
                        mr += 1
        bias = (cs - ms) - (cr - mr)
        out.append((b, bias))
    return out


def features(fires, A, daemon):
    """Per-fire dict: f, A-state (vs prev-day VA), B-state (wall ledger sign)."""
    import multiprocessing as mp
    from study.candle_bias_1h import _f
    vps = build_day_vps(A)
    chunks = [fires[i:i + 400] for i in range(0, len(fires), 400)]
    ledger = {}
    with mp.Pool(6, initializer=_init, initargs=(daemon,)) as pool:
        for i, res in enumerate(pool.imap(_work, chunks), 1):
            for b, bias in res:
                ledger[b] = bias
            print("    ledger chunk %d/%d" % (i, len(chunks)), flush=True)
    recs = []
    for f in fires:
        b, t, s, e, sl = f
        d = _day(_f(A[int(b)].get("start_time", 0)))
        prev = vps.get(d - __import__("datetime").timedelta(days=1))
        if prev is None:
            astate = "UNKNOWN"
        else:
            val, poc, vah = prev
            astate = "ABOVE" if e > vah else ("BELOW" if e < val else "INSIDE")
        bias = ledger.get(int(b), 0)
        bstate = "UP" if bias > 0 else ("DOWN" if bias < 0 else "NEUT")
        recs.append(dict(f=tuple(f), s=int(s), A=astate, B=bstate))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("A ABOVE value", lambda r: r["A"] == "ABOVE"),
    ("A INSIDE value", lambda r: r["A"] == "INSIDE"),
    ("A BELOW value", lambda r: r["A"] == "BELOW"),
    ("B ledger UP", lambda r: r["B"] == "UP"),
    ("B ledger NEUT", lambda r: r["B"] == "NEUT"),
    ("B ledger DOWN", lambda r: r["B"] == "DOWN"),
    ("ALIGNED value", lambda r: (r["s"] > 0 and r["A"] == "ABOVE") or (r["s"] < 0 and r["A"] == "BELOW")),
    ("AGAINST value", lambda r: (r["s"] > 0 and r["A"] == "BELOW") or (r["s"] < 0 and r["A"] == "ABOVE")),
    ("ALIGNED ledger", lambda r: (r["s"] > 0 and r["B"] == "UP") or (r["s"] < 0 and r["B"] == "DOWN")),
    ("AGAINST ledger", lambda r: (r["s"] > 0 and r["B"] == "DOWN") or (r["s"] < 0 and r["B"] == "UP")),
    ("ALIGNED BOTH", lambda r: ((r["s"] > 0 and r["A"] == "ABOVE") or (r["s"] < 0 and r["A"] == "BELOW"))
                               and ((r["s"] > 0 and r["B"] == "UP") or (r["s"] < 0 and r["B"] == "DOWN"))),
    ("AGAINST BOTH", lambda r: ((r["s"] > 0 and r["A"] == "BELOW") or (r["s"] < 0 and r["A"] == "ABOVE"))
                               and ((r["s"] > 0 and r["B"] == "DOWN") or (r["s"] < 0 and r["B"] == "UP"))),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    nu = sum(1 for r in recs if r["A"] == "UNKNOWN")
    print("  (A-state UNKNOWN, no prev-day VP: %d fires — excluded from A cells)" % nu, flush=True)
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-16s %-8s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("DAY-BIAS x RADAR RUNNER 30m BUCKET — canonical harness | pre-registered states (see header) | 1m first-touch | non-overlap\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (in-sample era)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    recs = features(load_fires("bucket", "30m"), A, daemon=False)
    report(recs, T1, H1, L1)
    del A
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    recsd = features(frd, Ad, daemon=True)
    report(recsd, Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
