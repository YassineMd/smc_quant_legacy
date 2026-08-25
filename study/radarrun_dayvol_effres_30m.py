"""DAY VOLUME x DAY EFF/RES x RADAR RUNNER, 30m bucket — HONEST study (user 2026-08-25, Day Compass axis
candidates). Canonical harness: union fire sets, 1m first-touch, non-overlap, fees+slip, per-year recon,
DAEMON 30m = decisive OOS. Exits: 0.5% net + RR 1:1.

PRE-REGISTERED (frozen before any result; no iteration):
AXIS V — TODAY'S RELATIVE VOLUME (causal): cumulative 30m volume from 00:00Z up to and including the fire
  bucket, pane-style-ranked against the SAME ELAPSED TIME on the trailing 20 days (share of prior days
  strictly below). HIGH >= 0.80 / LOW <= 0.20 / MID else; < 5 baseline days -> UNKNOWN (excluded, counted).
AXIS E — TODAY'S DAY-SCALE EFF/RES (causal, effort_result recipe at day scale): side = sign(today's cum
  delta to the fire bar); result_t = side*(fire close - day open)/TICK; expected_t = |cum delta| * median
  over the trailing 20 FULL days of (|day close - day open|/TICK / |day net delta|), only days whose |net
  delta| >= 2% of day volume, >= 10 obs else n/a. eff = result/expected: ABSORBED <= 0.35 / EASY >= 1.5 /
  NORMAL else (module constants).
FLOW: WITH-FLOW = fire side == sign(today's cum delta) (zero delta excluded), AGAINST-FLOW = opposite.
CELLS: ALL; V HIGH/MID/LOW; E ABSORBED/NORMAL/EASY; WITH/AGAINST-FLOW; the four FLOW x E interactions.
python study/radarrun_dayvol_effres_30m.py"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "study", "out")
TICK = 0.01
ABSORBED_MAX, EASY_MIN = 0.35, 1.5          # == app.effort_result constants
BASE_DAYS, MIN_OBS, MIN_DFRAC = 20, 10, 0.02


def _day(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).date()


def _day0(ts):
    d = datetime.fromtimestamp(float(ts), timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def day_series(A):
    """Per-day preprocessed series: {day: {'t': [end offsets], 'cv': [cum vol], 'cd': [cum delta],
    'open': first open, 'close': last close, 'vol': total, 'nd': net delta}} + ordered day list."""
    from study.candle_bias_1h import _f
    days = {}
    for b in A:
        st = _f(b.get("start_time", 0)); et = _f(b.get("end_time", 0)) or st + 1800
        d = _day(st)
        o = _f(b.get("open", b.get("open_price"))); c = _f(b.get("close", b.get("close_price")))
        v = _f(b.get("curr_vol")); dl = _f(b.get("buy_vol")) - _f(b.get("sell_vol"))
        e = days.setdefault(d, {"t": [], "cv": [], "cd": [], "open": o, "close": c, "vol": 0.0, "nd": 0.0})
        if not e["t"]:
            e["open"] = o
        e["vol"] += v; e["nd"] += dl; e["close"] = c
        e["t"].append(et - _day0(st)); e["cv"].append(e["vol"]); e["cd"].append(e["nd"])
    return days, sorted(days)


def cum_at(e, elapsed, key):
    """Cumulative value of `key` ('cv'/'cd') at elapsed seconds into that day (last bucket ending <= elapsed)."""
    import bisect
    i = bisect.bisect_right(e["t"], elapsed + 1e-6) - 1
    return e[key][i] if i >= 0 else 0.0


def features(fires, A):
    from study.candle_bias_1h import _f
    days, dlist = day_series(A)
    dpos = {d: i for i, d in enumerate(dlist)}
    recs = []
    for f in fires:
        b, t, s, e_, sl = f
        bkt = A[int(b)]
        st = _f(bkt.get("start_time", 0)); et = _f(bkt.get("end_time", 0)) or st + 1800
        d = _day(st); elapsed = et - _day0(st)
        di = dpos.get(d, 0)
        prior = dlist[max(0, di - BASE_DAYS):di]
        # AXIS V — today's cum volume vs same elapsed on trailing days
        cv = cum_at(days[d], elapsed, "cv")
        base = [cum_at(days[p], elapsed, "cv") for p in prior]
        base = [x for x in base if x > 0]
        if len(base) < 5:
            V = "UNKNOWN"
        else:
            rk = sum(1 for x in base if x < cv) / len(base)
            V = "HIGH" if rk >= 0.80 else ("LOW" if rk <= 0.20 else "MID")
        # AXIS E — day-scale eff/res
        cd = cum_at(days[d], elapsed, "cd")
        ratios = []
        for p in prior:
            pe = days[p]
            if pe["vol"] > 0 and abs(pe["nd"]) >= MIN_DFRAC * pe["vol"]:
                ratios.append(abs(pe["close"] - pe["open"]) / TICK / abs(pe["nd"]))
        if len(ratios) < MIN_OBS or cd == 0.0 or days[d]["open"] <= 0:
            E = "NA"; eff = None
        else:
            ratios.sort(); m = len(ratios) // 2
            tpd = ratios[m] if len(ratios) % 2 else 0.5 * (ratios[m - 1] + ratios[m])
            side = 1 if cd > 0 else -1
            close_now = _f(bkt.get("close", bkt.get("close_price")))
            result_t = side * (close_now - days[d]["open"]) / TICK
            expected_t = abs(cd) * tpd
            eff = (result_t / expected_t) if expected_t > 0 else None
            E = "NA" if eff is None else ("ABSORBED" if eff <= ABSORBED_MAX else ("EASY" if eff >= EASY_MIN else "NORMAL"))
        flow = 0 if cd == 0.0 else (1 if cd > 0 else -1)
        recs.append(dict(f=tuple(f), s=int(s), V=V, E=E, flow=flow))
    return recs


CELLS = [
    ("ALL", lambda r: True),
    ("V HIGH", lambda r: r["V"] == "HIGH"),
    ("V MID", lambda r: r["V"] == "MID"),
    ("V LOW", lambda r: r["V"] == "LOW"),
    ("E ABSORBED", lambda r: r["E"] == "ABSORBED"),
    ("E NORMAL", lambda r: r["E"] == "NORMAL"),
    ("E EASY", lambda r: r["E"] == "EASY"),
    ("WITH-FLOW", lambda r: r["flow"] != 0 and r["s"] == r["flow"]),
    ("AGAINST-FLOW", lambda r: r["flow"] != 0 and r["s"] == -r["flow"]),
    ("WITH-FLOW & EASY", lambda r: r["flow"] != 0 and r["s"] == r["flow"] and r["E"] == "EASY"),
    ("WITH-FLOW & ABSORBED", lambda r: r["flow"] != 0 and r["s"] == r["flow"] and r["E"] == "ABSORBED"),
    ("AGAINST-FLOW & EASY", lambda r: r["flow"] != 0 and r["s"] == -r["flow"] and r["E"] == "EASY"),
    ("AGAINST-FLOW & ABSORBED", lambda r: r["flow"] != 0 and r["s"] == -r["flow"] and r["E"] == "ABSORBED"),
]


def report(recs, T1, H1, L1):
    from study.radarrun_honest_deltapct_tp import fmt
    from study.radarrun_bkt1h_deltapct_confirm import eval_1m
    nu = sum(1 for r in recs if r["V"] == "UNKNOWN"); ne = sum(1 for r in recs if r["E"] == "NA")
    print("  (V UNKNOWN: %d | E n/a: %d — excluded from their cells)" % (nu, ne), flush=True)
    for name, keep in CELLS:
        fs = [r["f"] for r in recs if keep(r)]
        for cname, kind, val in (("0.5% net", "fix", 0.0054), ("RR 1:1", "rr", 1.0)):
            d, _ = eval_1m(fs, kind, val, T1, H1, L1)
            print("  %-24s %-8s %s" % (name, cname, fmt(d)), flush=True)


def main():
    from study.archive_loader import load_archive
    from study.candle_bias_1h import _f
    from study.radarrun_honest_deltapct_tp import load_fires
    print("DAY VOLUME x DAY EFF/RES x RADAR RUNNER 30m BUCKET — canonical harness | pre-registered (see header)\n", flush=True)
    t0 = time.time()
    A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    T1 = np.array([_f(b.get("start_time")) for b in A1]); H1 = np.array([_f(b.get("high")) for b in A1]); L1 = np.array([_f(b.get("low")) for b in A1])
    del A1
    print("=" * 120, flush=True)
    print("RECON 2025-01 .. 2026-06 (in-sample era)", flush=True)
    A = sorted(load_archive("30m", root="study/recon_archive", drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    report(features(load_fires("bucket", "30m"), A), T1, H1, L1)
    del A
    print("=" * 120, flush=True)
    print("DAEMON 30m (TRUE OOS, 2026-06-20 ..)", flush=True)
    Ad1 = sorted(load_archive("1m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    Td = np.array([_f(b.get("start_time")) for b in Ad1]); Hd = np.array([_f(b.get("high")) for b in Ad1]); Ld = np.array([_f(b.get("low")) for b in Ad1])
    del Ad1
    Ad = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    frd = json.load(open(os.path.join(OUT, "rr_union_b30m_daemon_m30.json")))
    report(features(frd, Ad), Td, Hd, Ld)
    print("\ndone in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
