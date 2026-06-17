"""SPACE_LEFT GUARD — process_tick must never add a NEGATIVE chunk.

The bug: when target_vol recalibrates DOWN below the active bucket's curr_vol,
`space_left = target_vol - curr_vol` goes negative; the chunk loop's else-branch then
calls `_add_to_bucket(price, space_left, ...)` with a negative chunk_vol, which subtracts
from every vector (opL/opS/clL/clS/churn) and from buy/sell — driving them negative while
*conservation still holds* (the negative chunk hits curr_vol and the vectors proportionally).
So the broken invariant is NON-NEGATIVITY, not conservation.

This test fills a bucket with BUY opens (opL high, opS=0), recalibrates target BELOW
curr_vol, then feeds a SELL-open whose negative chunk lands on opS (which was 0) -> opS<0.

Asserts the FIXED contract: (a) no vector ever negative, (b) conservation Σvectors==curr_vol
per bucket, (c) the over-full bucket grandfather-closes at curr_vol >= target, (d) the
trade spills into the fresh next bucket. Reproduces (FAIL) before the guard, resolves
(PASS) after.
"""
import os
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.quant_engine import QuantEngine

VEC = ("opL", "opS", "clL", "clS", "churn")


def check(b):
    errs = []
    for k in VEC + ("buy_vol", "sell_vol", "curr_vol"):
        if getattr(b, k) < -1e-9:
            errs.append(f"NEG {k}={getattr(b, k):.2f}")
    s = sum(getattr(b, k) for k in VEC)
    if abs(s - b.curr_vol) > 1e-6:
        errs.append(f"CONSERVATION Σ={s:.2f}!=curr_vol={b.curr_vol:.2f}")
    return errs


eng = QuantEngine(target_vol=1000.0)
fps = {}
t = 1000.0

# Fill the active bucket to 800 with BUY opens (opL=800, opS=0); target 1000 -> no close.
for i in range(4):
    eng.process_tick(100.0 + i * 0.01, 200.0, 200.0, 200.0, fps, tick_time=t); t += 1
assert abs(eng.active_bucket.curr_vol - 800.0) < 1e-6, f"setup curr_vol={eng.active_bucket.curr_vol}"
assert eng.active_bucket.opS == 0.0, f"setup opS={eng.active_bucket.opS}"

# RECALIBRATE DOWN: target drops below the active bucket's curr_vol (800).
eng.target_vol = 500.0

# A SELL-open (taker_buy=0, delta_oi>0), vol=100 -> space_left = 500-800 = -300.
eng.process_tick(100.5, 100.0, 0.0, 100.0, fps, tick_time=t); t += 1

buckets = eng.closed_buckets + [eng.active_bucket]
print(f"{len(buckets)} bucket(s) after the recalibrate-down trade:")
fail = 0
for j, b in enumerate(buckets):
    errs = check(b)
    print(f"  [{j}] curr_vol={b.curr_vol:7.1f}  opL={b.opL:7.1f} opS={b.opS:7.1f} "
          f"clL={b.clL:6.1f} clS={b.clS:6.1f} churn={b.churn:7.1f}   {'OK' if not errs else 'XX ' + '; '.join(errs)}")
    if errs:
        fail += 1

beh = []
ov = eng.closed_buckets[-1] if eng.closed_buckets else None
if ov is None or ov.curr_vol < 500.0 - 1e-6:
    beh.append(f"over-full bucket should grandfather-close >= target(500); got {ov.curr_vol if ov else None}")
if not (eng.active_bucket.curr_vol > 0.0):
    beh.append(f"spillover trade should land in the fresh bucket; active curr_vol={eng.active_bucket.curr_vol}")

print()
if fail == 0 and not beh:
    print("RESULT: PASS — no negative vector, conservation holds, over-full grandfathered, spillover carried")
    sys.exit(0)
for b_ in beh:
    print("  XX", b_)
print(f"RESULT: FAIL — {fail} bucket(s) broke non-negativity/conservation"
      + (f"; {len(beh)} behavioral miss" if beh else ""))
sys.exit(1)
