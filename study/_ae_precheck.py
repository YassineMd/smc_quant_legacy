import os, sys, time; sys.path.insert(0, os.getcwd())
t0=time.time()
print("importing...", flush=True)
import study.absorb_engulf_bias_15m as S
import study.mom_absorb_1h as MA
print("import done %.1fs; precompute..." % (time.time()-t0), flush=True)
S.precompute("15m")
print("precompute done %.1fs" % (time.time()-t0), flush=True)
X, recs = S._PRE["15m"]
print("n bars=%d  recs=%d" % (X["n"], len(recs)), flush=True)
