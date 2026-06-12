import time
from tally.engine import select_plugin, run
from tally.plugins.compchem import CompChemPlugin
from tally.plugins.generic import GenericPlugin
from tally.plugins.base import Level

plugins = [CompChemPlugin(), GenericPlugin()]

# 1. Plugin selection: comp-chem claims a dftb job, generic claims a random one
assert select_plugin(plugins, ["dftb+", "in.hsd"], "/tmp").name == "compchem"
assert select_plugin(plugins, ["python", "train.py"], "/tmp").name == "generic"
print("PASS: plugin selection")

# 2. Comp-chem FATAL detection
from tally.plugins.base import JobState
st = JobState(command=["dftb+"], cwd="/tmp", started_at=time.time())
v = CompChemPlugin().inspect(["SCC did not converge after 1000 iterations"], st)
assert v.level is Level.FATAL and "SCC" in v.reason
print("PASS: comp-chem FATAL")

# 3. Generic OK on benign output
v = GenericPlugin().inspect(["iteration 500 ok"], st)
assert v.level is Level.OK
print("PASS: generic OK")

# 4. End-to-end: a job that succeeds, captured notifications
notes = []
run(["bash", "-c", "echo hello; echo step 100; exit 0"],
    plugins=plugins, notify=lambda t,b: notes.append((t,b)),
    log_path="/tmp/tally_test.log", name="smoke")
assert any("selesai" in t for t,_ in notes), notes
print("PASS: e2e success notify")

# 5. End-to-end: a job that fails
notes.clear()
run(["bash", "-c", "echo boom; exit 3"],
    plugins=plugins, notify=lambda t,b: notes.append((t,b)),
    log_path="/tmp/tally_test.log", name="boom")
assert any("gagal" in t for t,_ in notes), notes
print("PASS: e2e failure notify")
print("\nALL TESTS PASSED")

# ---- queue resource gating (v1.5 seed) ----
from tally.resources import Capacity, Request, check_feasible, check_available

cap = Capacity(total_cores=32, reserve_cores=2, max_cores_per_job=30,
               total_ram_mb=32000, reserve_ram_mb=2000, gpu_exclusive=True)

# Gate 1: 31 cores rejected with the specs message
ok, reason = check_feasible(Request(cores=31), cap)
assert not ok and "lowering your specifications" in reason, reason
print("PASS: gate1 rejects 31 cores")

# Gate 1: 30 cores is the max allowed
ok, _ = check_feasible(Request(cores=30), cap)
assert ok
print("PASS: gate1 allows 30 cores")

# Gate 1: impossible RAM rejected
ok, reason = check_feasible(Request(cores=4, ram_mb=40000), cap)
assert not ok and "lowering your specifications" in reason
print("PASS: gate1 rejects impossible RAM")

# Gate 2: 20 + 15 cores can't both run (35 > 30 usable)
assert check_available(Request(cores=20), cap, running_cores=0, gpu_in_use=False)
assert not check_available(Request(cores=15), cap, running_cores=20, gpu_in_use=False)
print("PASS: gate2 makes 15-core job wait behind 20-core job")

# Gate 2: GPU exclusivity
assert not check_available(Request(cores=4, gpu=True), cap, running_cores=0, gpu_in_use=True)
print("PASS: gate2 enforces GPU exclusivity")

print("\nALL QUEUE TESTS PASSED")
