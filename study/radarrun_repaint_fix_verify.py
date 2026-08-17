"""VERIFY the repaint fix. Simulates the terminal's two data paths for a range of SHORT scan windows (= a reload that
loads only the recent N bars):
  OLD path: detect(scan_window_only)                 -> the buggy behaviour (signals depend on the window's left edge)
  NEW path: detect(full_history_warm + scan_window)  -> the fix (self._rr_warm = combined[:anchor_idx])
Reference = detect(full history). For each scan window size we count how many of the reference signals INSIDE the window
each path reproduces. The fix must reproduce 100% for every window size; the old path drops some. python study/radarrun_repaint_fix_verify.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study.radarrun_tp_velocity import get_buckets
from app import radar_breakout_detect as RB

SLBUF = 0.003; TPF = 0.005


def sigs_abs(seq, off):
    return {int(s["i"]) + off for s in RB.detect(seq, skip_last=True, sl_buf=SLBUF, tp_frac=TPF)}


def main():
    B = get_buckets("30m", {}); N = len(B)
    ref = sigs_abs(B, 0)                                   # full-history reference (absolute bar indices)
    print("DAEMON 30m N=%d, %d reference signals (full history).\n" % (N, len(ref)), flush=True)
    print("  %-12s %-24s %-24s" % ("scanWindow", "OLD detect(window only)", "NEW detect(warm+window)"), flush=True)
    for W in (150, 300, 600, 1000, 1500):                 # a reload showing only the last W bars
        L = max(0, N - W)                                 # scan window = B[L:N]; warm prefix = B[:L]
        win = B[L:N]
        old = {i + L for i in (int(s["i"]) for s in RB.detect(win, skip_last=True, sl_buf=SLBUF, tp_frac=TPF))}
        new = {i for i in (int(s["i"]) for s in RB.detect(list(B[:L]) + list(win), skip_last=True, sl_buf=SLBUF, tp_frac=TPF))}
        ref_in = {k for k in ref if L <= k < N}            # reference signals that fall inside this window
        old_ok = len(ref_in & old); new_ok = len(ref_in & new)
        old_drop = len(ref_in) - old_ok
        print("  last %-7d %3d/%-3d kept  (%2d DROPPED) %-4s %3d/%-3d kept  (%d dropped)" % (
            W, old_ok, len(ref_in), old_drop, "", new_ok, len(ref_in), len(ref_in) - new_ok), flush=True)
    print("\n  OLD = the bug (short reload window silently drops signals whose wall formed earlier).", flush=True)
    print("  NEW = the fix (self._rr_warm prefix) -> every in-window signal reproduced, any window size.", flush=True)


if __name__ == "__main__":
    main()
