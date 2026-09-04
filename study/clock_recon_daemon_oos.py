"""Build TRUE-OOS 1m clock candles for the daemon period (Jul-Aug 2026) into a SEPARATE dir, so the
recon clock_archive (2025-01..2026-06, used by the full run) is never touched. Same faithful pipeline
as study/clock_recon.py (Binance aggTrades -> production ClockEngine primitives). 2026-07/08 are 100%
out-of-sample vs everything screened. Sept is skipped (no monthly dump yet on data.binance.vision).
python study/clock_recon_daemon_oos.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from study import clock_recon

OOS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clock_daemon_oos")


def main():
    os.makedirs(OOS_ROOT, exist_ok=True)
    clock_recon.OUT_ROOT = OOS_ROOT                       # redirect writers away from the recon archive
    clock_recon._SCRATCH = os.path.join(OOS_ROOT, "_raw")
    months = ["2026-07", "2026-08"]
    print("clock_recon DAEMON-OOS build -> %s  months=%s" % (OOS_ROOT, months), flush=True)
    clock_recon.run(months, keep_raw=False)


if __name__ == "__main__":
    main()
