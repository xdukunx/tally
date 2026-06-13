"""
tally queue scheduler (v1.5).

The queue decides WHEN a job runs; the v1.0 engine decides HOW. This module is
purely the "when": one pass of the loop is `tick()`. A caller (the standalone
`tally-scheduler` script or a systemd timer) invokes `tick()` every
`TALLY_SCHEDULER_INTERVAL` seconds — there is deliberately no sleep in here.

`tick()` does the four steps from QUEUE_DESIGN.md `## Scheduler loop`:

  1. Reap finished running jobs (pid gone) -> mark done/failed from the
     `{log_path}.exit` sentinel the wrapper wrote. The engine already fired the
     completion notification from inside the job process, so we do not re-notify.
  2. Recompute the running totals (cores in use, gpu in use).
  3. For each pending job in (priority desc, submitted_at asc), if gate 2
     (`resources.check_available`) passes, launch it and immediately add its
     cores/gpu to the in-memory totals so we don't double-book a freed slot.
  4. Return. Sleeping is the caller's job.

Launching reuses the v1.0 engine via `tally.runjob` (see that module for why a
wrapper is needed to capture exit codes). Nothing here forks or detaches.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

from . import db
from .resources import Capacity, Request, check_available


def _pid_alive(pid: Optional[int]) -> bool:
    """True if `pid` is still running. POSIX (Mindlab-01 / CI is Linux).

    Uses the os.kill(pid, 0) idiom; also opportunistically reaps the process if
    it's a zombie child of *this* process (the long-loop case). In the systemd
    one-shot case the job was reparented to init, so waitpid raises
    ChildProcessError and we fall back to the kill(0) result.
    """
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    # It exists — but if it's our zombie child, reap it so it really goes away.
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False
    except ChildProcessError:
        pass  # not our child (reparented to init under systemd one-shot)
    except OSError:
        pass
    return True


def _read_exit(log_path: Optional[str]) -> Optional[int]:
    """Read the `{log_path}.exit` sentinel written by tally.runjob.

    Returns the captured exit code, or None if the sentinel is missing or
    unreadable (e.g. the job was SIGTERM'd before the wrapper could write it —
    treated as a failure by mark_finished)."""
    if not log_path:
        return None
    try:
        with open(f"{log_path}.exit") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _log_path_for(job_id: int, db_path: Optional[str] = None) -> str:
    """Per-job log path under the queue's data dir, next to the DB."""
    resolved = db_path or db.default_db_path()
    base = os.path.dirname(os.path.abspath(resolved))
    logs = os.path.join(base, "logs")
    os.makedirs(logs, exist_ok=True)
    return os.path.join(logs, f"job-{job_id}.log")


def _launch(job, log_path: str) -> int:
    """Start a job via the runjob wrapper; return the wrapper's pid.

    Launched in its own session (start_new_session=True) so `tally cancel` can
    signal the whole process group and the real job dies with the wrapper."""
    command = json.loads(job["command"])
    argv = [sys.executable, "-m", "tally.runjob", "--log", log_path]
    if job["name"]:
        argv += ["--name", job["name"]]
    argv += ["--", *command]
    proc = subprocess.Popen(
        argv,
        cwd=job["cwd"],
        start_new_session=True,
    )
    return proc.pid


def tick(cap: Optional[Capacity] = None, db_path: Optional[str] = None) -> dict:
    """One scheduler pass. Returns a small summary dict (handy for tests/logs)."""
    cap = cap or Capacity()
    db.init_db(db_path)

    reaped: list[int] = []
    launched: list[int] = []

    # 1. Reap finished running jobs.
    for job in db.get_running(db_path):
        if not _pid_alive(job["pid"]):
            rc = _read_exit(job["log_path"])
            db.mark_finished(job["id"], rc, db_path)
            reaped.append(job["id"])

    # 2. Recompute running totals from what's *still* running.
    running = db.get_running(db_path)
    running_cores = sum(j["cores"] for j in running)
    gpu_in_use = any(j["gpu"] for j in running)

    # 3. Admit pending jobs in order; update totals as we go so a single tick
    #    never double-books the same freed slot.
    for job in db.get_pending(db_path):
        req = Request(cores=job["cores"], ram_mb=job["ram_mb"], gpu=bool(job["gpu"]))
        if not check_available(req, cap, running_cores, gpu_in_use):
            continue
        log_path = _log_path_for(job["id"], db_path)
        pid = _launch(job, log_path)
        db.mark_running(job["id"], pid, log_path, db_path)
        running_cores += req.cores
        if req.gpu:
            gpu_in_use = True
        launched.append(job["id"])

    # 4. No sleep — the caller paces us.
    return {
        "reaped": reaped,
        "launched": launched,
        "running_cores": running_cores,
        "gpu_in_use": gpu_in_use,
    }


def _interval() -> float:
    return float(os.environ.get("TALLY_SCHEDULER_INTERVAL", "5"))


def loop(db_path: Optional[str] = None) -> None:
    """Run tick() forever, pausing TALLY_SCHEDULER_INTERVAL seconds between
    passes. Used by the standalone `tally-scheduler` entrypoint."""
    interval = _interval()
    while True:
        try:
            tick(db_path=db_path)
        except Exception as e:  # one bad tick must not kill the scheduler
            print(f"[tally-scheduler] tick error: {e}", file=sys.stderr)
        time.sleep(interval)


def main(argv=None) -> int:
    """Entrypoint for the `tally-scheduler` script.

    No args  -> run the loop forever (use under a long-running systemd service).
    --once   -> run a single tick and exit (use under a systemd timer)."""
    argv = argv if argv is not None else sys.argv[1:]
    if "--once" in argv:
        tick()
    else:
        loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
