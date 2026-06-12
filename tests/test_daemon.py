"""
tallyd (v2) dispatch tests — drive `daemon.handle_command` with fake inbound
messages and assert on real DB state. No Telegram, no network, no subprocesses,
so this runs on any platform (unlike test_queue.py, which needs POSIX).
"""
import os
import sys
import tempfile

# Pin capacity + isolate the DB *before* importing tally.resources.
os.environ.setdefault("TALLY_TOTAL_CORES", "32")
os.environ.setdefault("TALLY_SYSTEM_RESERVE_CORES", "2")
os.environ.setdefault("TALLY_MAX_CORES_PER_JOB", "30")
os.environ.setdefault("TALLY_TOTAL_RAM_MB", "32000")
os.environ.setdefault("TALLY_SYSTEM_RESERVE_RAM_MB", "2000")
os.environ.pop("TALLY_ALLOW_REMOTE_SUBMIT", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tally import daemon, db  # noqa: E402


def fresh_db():
    d = tempfile.mkdtemp(prefix="tally_d_")
    os.environ["TALLY_DB_PATH"] = os.path.join(d, "tally.db")
    db.init_db()


# -------------------------------------------------------------------------
fresh_db()

# /help and unknown commands
assert "/queue" in daemon.handle_command("/help")
assert "/help" in daemon.handle_command("/frobnicate")
assert "/queue" in daemon.handle_command("")  # empty -> help
print("PASS: /help and unknown-command fallback")

# /queue on an empty box
assert "no pending or running jobs" in daemon.handle_command("/queue")
print("PASS: /queue empty")

# Seed a pending job, see it in /queue and /status
jid = db.insert_job(["sleep", "5"], cwd="/tmp", cores=8, ram_mb=1000, name="md1")
out = daemon.handle_command("/queue")
assert "md1" in out and "pending" in out, out
out = daemon.handle_command(f"/status {jid}")
assert "md1" in out and "pending" in out and "sleep 5" in out, out
assert "no such job" in daemon.handle_command("/status 9999")
print("PASS: /queue and /status show the job")

# /priority re-ranks a pending job
out = daemon.handle_command(f"/priority {jid} 9")
assert "priority set to 9" in out, out
assert db.get_job(jid)["priority"] == 9
assert "no such job" in daemon.handle_command("/priority 9999 1")
print("PASS: /priority re-ranks pending")

# /priority refuses non-pending jobs
db.mark_running(jid, 4242, "/tmp/x.log")
out = daemon.handle_command(f"/priority {jid} 1")
assert "only pending jobs" in out, out
print("PASS: /priority refuses a running job")

# /resubmit clones a terminal job back to pending; refuses a live one
out = daemon.handle_command(f"/resubmit {jid}")
assert "not finished" in out, out
db.mark_finished(jid, 1)  # now it's failed
out = daemon.handle_command(f"/resubmit {jid}")
assert "queued as job" in out, out
new_id = int(out.rsplit(" ", 1)[1])
clone = db.get_job(new_id)
assert clone["state"] == "pending" and clone["name"] == "md1" and clone["cores"] == 8
print("PASS: /resubmit clones failed job to pending")

# /cancel a pending job removes it
out = daemon.handle_command(f"/cancel {new_id}")
assert "cancelled pending" in out, out
assert db.get_job(new_id) is None
print("PASS: /cancel pending removes it")

# /submit is refused by default (opt-in)
out = daemon.handle_command("/submit --cores 4 -- sleep 1")
assert "disabled" in out and "TALLY_ALLOW_REMOTE_SUBMIT" in out, out
assert db.get_pending() == []
print("PASS: /submit disabled by default")

# /submit with the opt-in set: gate-1 still applies, feasible job enqueues
os.environ["TALLY_ALLOW_REMOTE_SUBMIT"] = "1"
out = daemon.handle_command("/submit --cores 31 -- sleep 1")
assert "lowering your specifications" in out, out
assert db.get_pending() == [], "infeasible remote submit must not enqueue"
out = daemon.handle_command("/submit --cores 4 --ram 1000 --name remote1 --priority 2 -- sleep 1")
assert "queued as job" in out, out
pend = db.get_pending()
assert len(pend) == 1 and pend[0]["name"] == "remote1" and pend[0]["priority"] == 2
print("PASS: /submit honors gate-1 and enqueues when allowed")

# malformed input never raises, always replies
for bad in ("/submit --cores -- x", "/status abc", "/cancel", "/priority 1",
            '/submit --cores 4 -- "unclosed'):
    out = daemon.handle_command(bad)
    assert isinstance(out, str) and out, bad
print("PASS: malformed input gets a reply, never a crash")

os.environ.pop("TALLY_ALLOW_REMOTE_SUBMIT", None)
print("\nALL DAEMON TESTS PASSED")
