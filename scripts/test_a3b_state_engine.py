"""A3b STATE ENGINE — synthetic verdict test.

Feeds hand-built buckets that match each state's textbook signature and asserts the
engine returns the RIGHT verdict + a roughly-right confidence. The exact state is
asserted hard; the confidence is checked against a generous band (it's a calibration,
tuned live). Also pins the gradient-preserving squeeze floor (violent > marginal >= 80).
"""
import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import bucket_state as BS

SW = BS.SWEEP_WINDOW
_fail = 0


def mk(**kw):
    d = dict(open=100.0, high=100.0, low=100.0, close=100.0,
             curr_vol=1000.0, buy_vol=500.0, sell_vol=500.0,
             opL=0.0, opS=0.0, clL=0.0, clS=0.0, churn=1000.0,
             poc_price=100.0, vol_mult=1.0, liq_short=0.0, liq_long=0.0)
    d.update(kw)
    return d


def run(test_bucket, b_mult=1.0, s_mult=1.0):
    # SW flat prior buckets [99,101] -> prev_high=101, prev_low=99, atr=2.0
    bks = [mk(high=101.0, low=99.0) for _ in range(SW)] + [test_bucket]
    return BS.classify_bucket(bks, len(bks) - 1, b_mult, s_mult)


def check(label, got_state, got_conf, want_state, lo, hi):
    global _fail
    ok_state = got_state == want_state
    ok_conf = lo <= got_conf <= hi
    flag = "OK " if (ok_state and ok_conf) else "XX "
    if not (ok_state and ok_conf):
        _fail += 1
    print(f"  {flag}{label:<16} -> {got_state:<16} {got_conf:>3}%   "
          f"(want {want_state}, conf {lo}-{hi})")


print("A3b state-engine synthetic verdicts:")

s, c = run(mk(open=100, high=102, low=100, close=102, buy_vol=800, sell_vol=200,
              opL=500, churn=500, vol_mult=1.6))
check("strong-bull", s, c, "STRONG BULL", 70, 100)

s, c = run(mk(open=100, high=100, low=98, close=98, buy_vol=200, sell_vol=800,
              opS=500, churn=500, vol_mult=1.6))
check("strong-bear", s, c, "STRONG BEAR", 70, 100)

s, c = run(mk(open=100, high=101.5, low=100, close=101, buy_vol=600, sell_vol=400,
              clS=500, churn=500, vol_mult=1.2), b_mult=1.7)
check("bull-exhaust", s, c, "BULL EXHAUSTION", 45, 95)

s, c = run(mk(open=100, high=100, low=98.5, close=99, buy_vol=400, sell_vol=600,
              clL=500, churn=500, vol_mult=1.2), s_mult=1.7)
check("bear-exhaust", s, c, "BEAR EXHAUSTION", 45, 95)

s, c = run(mk(open=101, high=102, low=99.5, close=99.6, buy_vol=750, sell_vol=250,
              opL=200, churn=550, poc_price=101.5, vol_mult=1.4), b_mult=1.6)
check("bull-trap", s, c, "BULL TRAP", 65, 100)

s, c = run(mk(open=99, high=100.5, low=98, close=100.4, buy_vol=250, sell_vol=750,
              opS=200, churn=550, poc_price=98.5, vol_mult=1.4), s_mult=1.6)
check("bear-trap", s, c, "BEAR TRAP", 65, 100)

s, c = run(mk(open=99.5, high=101, low=98.5, close=101, buy_vol=700, sell_vol=300,
              clS=300, churn=700, vol_mult=2.5, liq_short=100))
check("squeeze-marg", s, c, "SHORT SQUEEZE", 78, 90)
sq_marginal = c

s, c = run(mk(open=99.5, high=101, low=98.5, close=101, buy_vol=700, sell_vol=300,
              clS=300, churn=700, vol_mult=3.2, liq_short=300))
check("squeeze-viol", s, c, "SHORT SQUEEZE", 88, 100)
sq_violent = c

s, c = run(mk(open=100.5, high=101.5, low=99, close=99, buy_vol=300, sell_vol=700,
              clL=300, churn=700, vol_mult=2.5, liq_long=180))
check("long-squeeze", s, c, "LONG SQUEEZE", 80, 100)

s, c = run(mk(open=100, high=100.3, low=99.8, close=100, buy_vol=500, sell_vol=500,
              churn=840, clL=80, clS=80, liq_short=80, liq_long=80, vol_mult=0.9))
check("liq-coil", s, c, "LIQUIDITY COIL", 55, 100)

s, c = run(mk(open=100, high=102, low=98, close=100, buy_vol=510, sell_vol=490,
              churn=950, opL=20, opS=20, clL=5, clS=5, vol_mult=1.1))
check("rotation", s, c, "ROTATION", 70, 100)

# Directional-opening guard: a clearly OI-BUILDING bucket (opL 30% of vol, slow, inside-bar,
# low delta) must NOT read CHOP or ROTATION — both neutral states are killed by the building
# guards (CHOP notCommitted's building arm, ROTATION tightened oiNeutral) -> falls to NEUTRAL.
s, c = run(mk(open=100, high=100.3, low=99.9, close=100.1, curr_vol=10000.0,
              buy_vol=5200.0, sell_vol=4800.0, opL=3000.0, churn=7000.0, vol_mult=0.8))
check("oi-building", s, c, "NEUTRAL", 1, 40)

# Movement guard (the 3rd notCommitted arm): a DECISIVE directional close (|result|~0.87, a churn-driven
# markdown) with NO fresh OI and NO absorption must NOT read CHOP or ROTATION — both neutral states are
# blind to |result| without this guard. inside-bar + quiet + mild sell-lean, but it MOVED -> NEUTRAL.
s, c = run(mk(open=100.8, high=100.9, low=99.3, close=99.4, buy_vol=420, sell_vol=580,
              opL=50, opS=60, clL=30, clS=30, churn=830, vol_mult=0.8))
check("decisive-move", s, c, "NEUTRAL", 1, 40)

s, c = run(mk(open=100, high=100.4, low=99.7, close=100.0, buy_vol=500, sell_vol=500,
              churn=500, clL=250, clS=250, vol_mult=0.8))
check("chop", s, c, "CHOP", 70, 100)

s, c = run(mk(open=100, high=101.5, low=99, close=100.05, buy_vol=510, sell_vol=490,
              opL=180, opS=170, clL=180, clS=170, churn=300, vol_mult=1.0))
check("neutral", s, c, "NEUTRAL", 1, 35)

print(f"\n  squeeze gradient: marginal {sq_marginal}%  <  violent {sq_violent}%")
if not (sq_violent > sq_marginal >= 80):
    _fail += 1
    print("  XX gradient-preserving floor FAILED")
else:
    print("  OK floor preserves the gradient above 0.80")

print()
if _fail == 0:
    print("RESULT: ALL ASSERTIONS PASSED")
    sys.exit(0)
else:
    print(f"RESULT: {_fail} ASSERTION(S) FAILED")
    sys.exit(1)
