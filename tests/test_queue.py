"""
tally queue (v1.5) tests — the QUEUE_DESIGN.md `## Test plan`, against real
subprocesses (no mocked os.kill/proc). Per the handoff, jobs are short-lived
real commands (`sleep ...`); we tick the scheduler and assert on real state.

Capacity env is pinned to the Mindlab-01 defaults BEFORE importing tally so the
dataclass field defaults pick them up. Each test runs against its own fresh
temp DB (via TALLY_DB_PATH) so they don't interfere or touch a real queue.

POSIX-only (os.killpg / SIGTERM / `sleep`). On a non-POSIX host it self-skips
with exit 0 — CI (ubuntu-latest) runs the real thing.
"""
import os
import shutil
import sys
import tempfile
import time

# --- pin capacity + isolate the DB *before* importing tally.resources ---
os.environ.setdefault("TALLY_TOTAL_CORES", "32")
os.environ.setdefault("TALLY_SYSTEM_RESERVE_CORES", "2")
os.environ.setdefault("TALLY_MAX_CORES_PER_JOB", "30")
os.environ.setdefault("TALLY_TOTAL_RAM_MB", "32000")
os.environ.setdefault("TALLY_SYSTEM_RESERVE_RAM_MB", "2000")
os.environ.setdefault("TALLY_GPU_EXCLUSIVE", "1")

_POSIX = os.name == "posix" and shutil.which("sleep") is not None
if not _POSIX:
    print("SKIP: test_queue.py requires a POSIX host with `sleep` "
          "(scheduler uses os.killpg/SIGTERM). CI runs it on ubuntu-latest.")
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tally import cli, db, scheduler                      # noqa: E402
from tally.resources import Capacity                       # noqa: E402

CAP = Capacity()  # reads the pinned env above: 30 usable cores, gpu exclusive


def fresh_db():
    d = tempfile.mkdtemp(prefix="tally_q_")
    path = os.path.join(d, "tally.db")
    os.environ["TALLY_DB_PATH"] = path
    db.init_db()
    return path


def wait_until(pred, timeout=10.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def states():
    return {r["id"]: r["state"] for r in
            (list(db.get_running()) + list(db.get_pending()))}


# -------------------------------------------------------------------------
def test_reject_31_cores_not_enqueued():
    fresh_db()
    import io, contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = cli.main(["submit", "--cores", "31", "--", "sleep", "1"])
    assert rc == 1, "31 cores must be rejected"
    assert "lowering your specifications" in err.getvalue(), err.getvalue()
    assert db.get_pending() == [], "rejected job must NOT be enqueued"
    print("PASS: 31 cores rejected, not enqueued")


def test_20_then_15_sequencing():
    fresh_db()
    assert cli.main(["submit", "--cores", "20", "--name", "big", "--", "sleep", "2"]) == 0
    assert cli.main(["submit", "--cores", "15", "--name", "small", "--", "sleep", "1"]) == 0
    assert len(db.get_pending()) == 2

    scheduler.tick(cap=CAP)
    running = db.get_running()
    assert len(running) == 1 and running[0]["name"] == "big", states()
    assert len(db.get_pending()) == 1, "15-core job must wait (20+15 > 30)"
    print("PASS: 20-core runs, 15-core waits behind it")

    # When the 20-core job finishes, the next tick reaps it and admits the 15.
    def small_running():
        scheduler.tick(cap=CAP)
        r = db.get_running()
        return len(r) == 1 and r[0]["name"] == "small"

    assert wait_until(small_running, timeout=12), states()
    print("PASS: 15-core starts after 20-core finishes")
    wait_until(lambda: (scheduler.tick(cap=CAP), db.get_running() == [])[1], timeout=8)


def test_gpu_exclusivity():
    fresh_db()
    # Insert gpu jobs directly: gate-1 (check_feasible) would reject gpu jobs on
    # a box without nvidia-smi, but the *scheduler's* exclusivity (gate-2) is the
    # thing under test and depends only on the gpu=1 flag of running jobs. This
    # keeps the test meaningful on GPU and GPU-less hosts alike.
    cwd = os.getcwd()
    db.insert_job(["sleep", "2"], cwd=cwd, cores=4, gpu=True, name="gpuA")
    db.insert_job(["sleep", "2"], cwd=cwd, cores=4, gpu=True, name="gpuB")

    scheduler.tick(cap=CAP)
    running = db.get_running()
    assert len(running) == 1, f"only one GPU job may run, got {states()}"
    assert sum(1 for r in db.get_running() if r["gpu"]) == 1
    assert len(db.get_pending()) == 1, "second gpu job must wait"
    print("PASS: second GPU job waits (exclusivity)")
    wait_until(lambda: (scheduler.tick(cap=CAP), db.get_running() == [])[1], timeout=10)


def test_cancel_pending_removes_it():
    fresh_db()
    # Park a job behind a bigger one so it stays pending, then cancel it.
    cli.main(["submit", "--cores", "20", "--", "sleep", "3"])
    cli.main(["submit", "--cores", "20", "--name", "victim", "--", "sleep", "3"])
    scheduler.tick(cap=CAP)
    pend = db.get_pending()
    assert len(pend) == 1 and pend[0]["name"] == "victim"
    vid = pend[0]["id"]

    assert cli.main(["cancel", str(vid)]) == 0
    assert db.get_job(vid) is None, "cancelled pending job must be gone"
    print("PASS: cancel pending removes it from the queue")
    wait_until(lambda: (scheduler.tick(cap=CAP), db.get_running() == [])[1], timeout=8)


def test_cancel_running_sigterm_and_free():
    fresh_db()
    cli.main(["submit", "--cores", "10", "--name", "longrun", "--", "sleep", "30"])
    scheduler.tick(cap=CAP)
    running = db.get_running()
    assert len(running) == 1, states()
    job = running[0]
    pid = job["pid"]
    assert pid and scheduler._pid_alive(pid)

    assert cli.main(["cancel", str(job["id"])]) == 0
    # SIGTERM'd: the wrapper (process-group leader) and its child must die.
    assert wait_until(lambda: not scheduler._pid_alive(pid), timeout=6), "pid should die"
    # Marked terminal and resources freed (no longer in the running set).
    assert db.get_job(job["id"])["state"] == "failed"
    scheduler.tick(cap=CAP)
    assert db.get_running() == [], "resources freed after cancel"
    print("PASS: cancel running SIGTERMs, reaps, frees resources")


ALL = [
    test_reject_31_cores_not_enqueued,
    test_20_then_15_sequencing,
    test_gpu_exclusivity,
    test_cancel_pending_removes_it,
    test_cancel_running_sigterm_and_free,
]

if __name__ == "__main__":
    for t in ALL:
        t()
    print("\nALL QUEUE TESTS PASSED")
