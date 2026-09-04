"""STACK on the FULL 18 months, ALL sessions (24h, 7 days) + daemon OOS 24h (user 2026-09-04:
"actually I want you to run it on the full 18 month" after the weekday-NY-only run).

STACK = opposing >= $500K whale run over by the badge close (WHALE-OPP) AND same-side 1m-clock confirm
inside the parent bucket whose confirming candle sits in the cheap third (long) / expensive third (short)
of the 1m E/E/C band (C+Z-HYP). Parent = 30m VOLUME BUCKET (canonical union), entry = parent close,
SL = parent badge SL; exits 0.2/0.4% fix + RR 1/1.5/2; 1m first-touch ties-against; fees 0.04% RT +
0.03% slip/leg; non-overlap taken(); W/BE/L on net; prop MC.  Mechanics identical to
study/radarrun_confirm_1m.py (recon) and study/radarrun_confirm_daemon_oos.py (OOS).

CONFIRM FLAGS: the 2,057 weekday-NY recon parents + 262 OOS parents already carry conf/cz from the
earlier full runs (same code, same inputs -> reused; a random spot-check RECOMPUTES a sample of them and
asserts equality). The remaining ~4,000 recon + ~575 OOS parents are replayed here (checkpointed).
REPORT: FULL 18mo POOLED (the ask) -> 2025 / 2026H1 -> DAEMON OOS Jul-Aug 24h (virgin gate); cells
ALL / CONFIRMED / C+Z-HYP / WHALE-OPP / STACK / STACK-anyopp, plus STACK split NY-weekday vs off-NY.
python study/radarrun_stack_full24h.py"""
import os, sys, json, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
import numpy as np

from study.radarrun_pullback_1m import _f, report_cell, EXITS, CACHE_30, OUT, W1, SLBUF
from study.radarrun_pullback_1mbkt_ema import CLOCK_NPZ
from study.radarrun_confirm_1m import eec_zones
from study.radarrun_confirm_daemon_oos import OOS_ROOT, JUL1, SEP1
from study.radarrun_stack_whale_confirm import whale_flags, CELLS, BP_USD, CACHE_D30, SPLIT
from app import bigprint_store

bigprint_store.CACHE_FLOOR_USD = BP_USD
CONF_CAP = 600
OUT_R = os.path.join(OUT, "rr_confirm_full24h_trades.json")
OUT_D = os.path.join(OUT, "rr_confirm_daemon_oos24h_trades.json")
CKPT = os.path.join(OUT, "rr_confirm_24h.ckpt")
N_CHECK = 25


def ny_weekday(et):
    d = datetime.fromtimestamp(et, tz=timezone.utc)
    return d.weekday() < 5 and 13 * 3600 <= (et % 86400) < 21 * 3600


def confirm_one(st, et, s, A1, T1S, zone1m, RB, config):
    """Same-side 1m-clock union fire inside [st, et] (badge closes by the parent close). Returns
    (confirmed, zone code of the earliest confirming badge). Verbatim mechanics of the NY runs."""
    j0 = int(np.searchsorted(T1S, st - 0.5))
    seen = set()
    for j in range(j0, min(len(T1S), j0 + CONF_CAP)):
        if T1S[j] + 60.0 > et + 1e-6:
            break
        lo = max(0, j - W1)
        hits = []
        for g in RB.detect(A1[lo:j + 1], skip_last=False, sl_buf=SLBUF, tp_frac=config.RR_TP_FRAC):
            bb = lo + int(g["i"])
            key = (bb, g["side"])
            if key in seen or bb < j0 or bb > j:
                continue
            seen.add(key)
            if g["side"] == s:
                hits.append(bb)
        if hits:
            return True, int(zone1m[min(hits)])
    return False, -1


def build_flags(tag, fires, starts, cached, A1, T1S, zone1m, RB, config, out_path, t0, ckpt=None):
    """conf/cz for every parent in `fires`; reuse `cached` (by end_time) and replay the rest."""
    if os.path.exists(out_path):
        tr = json.load(open(out_path))
        print("%s: loaded %d parents from %s" % (tag, len(tr), os.path.basename(out_path)), flush=True)
        return tr
    todo = [f for f in fires if round(f[1], 2) not in cached]
    print("%s: %d parents; %d reused from the NY runs, %d to replay" % (
        tag, len(fires), len(fires) - len(todo), len(todo)), flush=True)
    # spot-check: recompute a random sample of the cached parents and assert identical conf/cz
    rng = random.Random(7)
    chk = rng.sample([f for f in fires if round(f[1], 2) in cached], min(N_CHECK, len(fires) - len(todo)))
    bad = 0
    for (b, et, s, e, sl) in chk:
        c, z = confirm_one(starts[int(b)], et, s, A1, T1S, zone1m, RB, config)
        old = cached[round(et, 2)]
        if bool(old["conf"]) != c or int(old.get("cz", -1)) != z:
            bad += 1
            print("  MISMATCH et=%s cached conf=%s cz=%s recomputed conf=%s cz=%s" % (
                et, old["conf"], old.get("cz"), c, z), flush=True)
    print("  spot-check: %d/%d cached parents recomputed identically (%.0fs)" % (
        len(chk) - bad, len(chk), time.time() - t0), flush=True)
    if bad:
        raise SystemExit("cached confirm flags do not reproduce — refusing to reuse them")
    done = {}
    if ckpt and os.path.exists(ckpt):
        _st = json.load(open(ckpt))
        if _st.get("tag") == tag:
            done = {round(x["t"], 2): x for x in _st["trades"]}
            print("  RESUME: %d replayed parents from checkpoint" % len(done), flush=True)
    new = []
    for pi, (b, et, s, e, sl) in enumerate(todo):
        k = round(et, 2)
        if k in done:
            new.append(done[k]); continue
        c, z = confirm_one(starts[int(b)], et, s, A1, T1S, zone1m, RB, config)
        new.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), conf=c, cz=z))
        if ckpt and pi % 200 == 0 and pi:
            json.dump({"tag": tag, "trades": new}, open(ckpt, "w"))
        if pi % 100 == 0:
            print("  %s replay %d/%d (%.0fs)" % (tag, pi, len(todo), time.time() - t0), flush=True)
    tr = []
    for (b, et, s, e, sl) in fires:
        k = round(et, 2)
        if k in cached:
            o = cached[k]
            tr.append(dict(t=et, s=int(s), e=float(e), sl=float(sl), conf=bool(o["conf"]), cz=int(o.get("cz", -1))))
    newk = {round(x["t"], 2): x for x in new}
    tr += [newk[round(f[1], 2)] for f in fires if round(f[1], 2) not in cached]
    tr.sort(key=lambda x: x["t"])
    json.dump(tr, open(out_path, "w"))
    if ckpt and os.path.exists(ckpt):
        os.remove(ckpt)
    print("%s: confirmed %d/%d (%.0f%%)  (%.0fs)" % (tag, sum(x["conf"] for x in tr), len(tr),
          100.0 * sum(x["conf"] for x in tr) / max(1, len(tr)), time.time() - t0), flush=True)
    return tr


def report(title, trades, arrs, mc, day_blocks):
    T1S, H1, L1, C1 = arrs
    print("=" * 132, flush=True)
    print(title, flush=True)
    cells = list(CELLS) + [("STACK NYwk", lambda x: CELLS[4][1](x) and ny_weekday(x["t"])),
                           ("STACK offNY", lambda x: CELLS[4][1](x) and not ny_weekday(x["t"]))]
    for name, sel in cells:
        sub = [x for x in trades if sel(x)]
        for ename, kind, val in EXITS:
            report_cell(name, ename, sub, T1S, H1, L1, C1, kind, val, mc, day_blocks)
        print("-" * 132, flush=True)


def main():
    from study.archive_loader import load_archive
    from study.radarrun_hyro_prop import mc, day_blocks
    from app import config, radar_breakout_detect as RB
    t0 = time.time()
    print("STACK FULL 18mo — ALL sessions 24h/7d | 30m bucket parent | whale-opp x 1m-confirm+zone | + daemon OOS 24h\n", flush=True)

    # ---- recon: parents + spans
    f30 = [tuple(f) for f in json.load(open(CACHE_30))]
    A30 = sorted(load_archive("30m", root="study/recon_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
    starts = {int(b): _f(A30[b].get("start_time")) for (b, et, s, e, sl) in f30}
    span = {round(et, 2): (starts[int(b)], et) for (b, et, s, e, sl) in f30}
    del A30
    z = np.load(CLOCK_NPZ)
    T1S, H1, L1, C1 = z["t"], z["h"], z["l"], z["c"]
    arr_r = (T1S, H1, L1, C1)
    cached_r = {round(x["t"], 2): x for x in json.load(open(os.path.join(OUT, "rr_confirm_full_trades.json")))}
    if not os.path.exists(OUT_R):
        A1 = sorted(load_archive("1m", root="study/clock_archive")[1], key=lambda b: _f(b.get("start_time", 0)))
        assert len(A1) == len(T1S), (len(A1), len(T1S))
        print("1m clock dicts loaded (%.0fs)" % (time.time() - t0), flush=True)
        zone1m = eec_zones(C1, H1, L1)
        tr = build_flags("RECON", f30, starts, cached_r, A1, T1S, zone1m, RB, config, OUT_R, t0, ckpt=CKPT)
        del A1
    else:
        tr = build_flags("RECON", f30, starts, cached_r, None, T1S, None, RB, config, OUT_R, t0)
    whale_flags(tr, span)

    # ---- daemon OOS 24h
    fd = [tuple(f) for f in json.load(open(CACHE_D30))]
    A30d = sorted(load_archive("30m", drop_degenerate=True)[1], key=lambda b: _f(b.get("start_time", 0)))
    A30d = [b for b in A30d if _f(b.get("start_time")) < SEP1]
    starts_d = {int(b): _f(A30d[b].get("start_time")) for (b, et, s, e, sl) in fd}
    span_d = {round(et, 2): (starts_d[int(b)], et) for (b, et, s, e, sl) in fd}
    del A30d
    A1d = sorted(load_archive("1m", root=OOS_ROOT, drop_degenerate=False)[1], key=lambda b: _f(b.get("start_time", 0)))
    A1d = [b for b in A1d if JUL1 <= _f(b.get("start_time")) < SEP1]
    T1D = np.array([_f(b.get("start_time")) for b in A1d]); H1D = np.array([_f(b.get("high")) for b in A1d])
    L1D = np.array([_f(b.get("low")) for b in A1d]); C1D = np.array([_f(b.get("close", b.get("close_price"))) for b in A1d])
    arr_d = (T1D, H1D, L1D, C1D)
    cached_d = {round(x["t"], 2): x for x in json.load(open(os.path.join(OUT, "rr_confirm_daemon_oos_trades.json")))}
    zone_d = eec_zones(C1D, H1D, L1D) if not os.path.exists(OUT_D) else None
    trd = build_flags("DAEMON", fd, starts_d, cached_d, A1d, T1D, zone_d, RB, config, OUT_D, t0)
    del A1d
    whale_flags(trd, span_d)

    tr25 = [x for x in tr if x["t"] < SPLIT]; tr26 = [x for x in tr if x["t"] >= SPLIT]
    for tag, pool in (("recon 18mo", tr), ("2025", tr25), ("2026H1", tr26), ("daemon OOS", trd)):
        print("%-11s parents %5d | conf %5d | C+Z-HYP %4d | whale-opp-all %4d | STACK %3d (NYwk %d / offNY %d)" % (
            tag, len(pool), sum(x["conf"] for x in pool), sum(1 for x in pool if CELLS[2][1](x)),
            sum(x["opp_all"] for x in pool), sum(1 for x in pool if CELLS[4][1](x)),
            sum(1 for x in pool if CELLS[4][1](x) and ny_weekday(x["t"])),
            sum(1 for x in pool if CELLS[4][1](x) and not ny_weekday(x["t"]))), flush=True)
    print(flush=True)
    report("FULL RECON 18mo POOLED — ALL sessions 24h/7d (%d parents)" % len(tr), tr, arr_r, mc, day_blocks)
    report("FULL RECON 2025 — 24h/7d (%d parents)" % len(tr25), tr25, arr_r, mc, day_blocks)
    report("FULL RECON 2026H1 — 24h/7d (%d parents)" % len(tr26), tr26, arr_r, mc, day_blocks)
    report("FULL DAEMON OOS Jul-Aug 2026 — 24h/7d (%d parents, virgin)" % len(trd), trd, arr_d, mc, day_blocks)
    print("done in %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
