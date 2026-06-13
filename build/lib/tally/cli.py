"""
tally CLI.

Backward-compatible with the old notify-run invocation so existing run_*.sh
templates keep working:

    notify-run --bg --log run.log --name my_md -- dftb+ dftb_in.hsd

New canonical form:

    tally run --bg --log run.log --name my_md -- dftb+ dftb_in.hsd

Queue subcommands (v1.5) sit alongside `run` and never alter its behavior:

    tally submit job.sh                 # SLURM-style batch script (preferred)
    tally submit --cores 16 --gpu -- pmemd.cuda -i md.in   # inline form
    tally queue
    tally cancel 42
    tally status 42
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

from . import batch, db, notifier
from .engine import run
from .plugins.compchem import CompChemPlugin
from .plugins.generic import GenericPlugin
from .resources import Capacity, Request, check_feasible

QUEUE_COMMANDS = {"submit", "queue", "cancel", "status", "daemon"}


def _registry():
    # Plain list, hardcoded for v1.0. Real plugin discovery (entry points)
    # is a v2 concern — don't gold-plate a six-plugin system.
    return [CompChemPlugin(), GenericPlugin()]


# --------------------------------------------------------------------------
# `tally run` (unchanged v1.0 behavior)
# --------------------------------------------------------------------------
def _run(argv) -> int:
    p = argparse.ArgumentParser(prog="tally", description="Run a job, get told when it ends or breaks.")
    p.add_argument("--bg", action="store_true", help="detach to background (survives shell exit)")
    p.add_argument("--log", default="tally.log", help="log file path")
    p.add_argument("--name", default=None, help="job name for notifications")
    p.add_argument("--kill-on-fatal", action="store_true", help="SIGTERM the job on a FATAL verdict")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...]")
    args = p.parse_args(argv)

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        p.error("no command given (use: tally run -- your_command args)")

    return run(
        cmd,
        plugins=_registry(),
        notify=notifier.from_env(),
        log_path=args.log,
        name=args.name,
        background=args.bg,
        kill_on_fatal=args.kill_on_fatal,
    )


# --------------------------------------------------------------------------
# `tally submit`  (gate 1 + enqueue)
# --------------------------------------------------------------------------
def _submit(argv) -> int:
    p = argparse.ArgumentParser(
        prog="tally submit",
        description="Queue a job to run when resources free up.",
        epilog="Batch form (SLURM-style): tally submit job.sh  "
               "(specs come from #TALLY directives in the file).",
    )
    # --cores is required only in the inline form; a batch script supplies it
    # via a #TALLY directive, so it's optional at the parser level.
    p.add_argument("--cores", type=int, default=None, help="CPU cores the job needs")
    p.add_argument("--ram", type=int, default=0, help="RAM in MB the job needs (default 0)")
    p.add_argument("--gpu", action="store_true", help="job needs the GPU (exclusive)")
    p.add_argument("--name", default=None, help="job name")
    p.add_argument("--priority", type=int, default=0, help="higher runs first (default 0)")
    p.add_argument("command", nargs=argparse.REMAINDER,
                   help="a batch script (job.sh), or  -- COMMAND [ARGS...]")
    args = p.parse_args(argv)

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    # Batch mode: no --cores on the command line -> read everything from a
    # #TALLY-directive script (sbatch-style).
    if args.cores is None:
        if len(cmd) != 1 or cmd[0].startswith("-"):
            p.error("give a batch script (tally submit job.sh) or use the "
                    "inline form (tally submit --cores N -- your_command args)")
        script = cmd[0]
        if not os.path.isfile(script):
            print(f"no such script file: {script}", file=sys.stderr)
            return 1
        try:
            specs, command = batch.parse_script(script)
        except (ValueError, OSError) as e:
            print(str(e), file=sys.stderr)
            return 1
        rc, msg = submit_op(command, cwd=os.getcwd(), **specs)
        print(msg, file=sys.stderr if rc else sys.stdout)
        return rc

    # Inline mode (unchanged): tally submit --cores N [...] -- cmd args
    if not cmd:
        p.error("no command given (use: tally submit --cores N -- your_command args)")
    rc, msg = submit_op(
        cmd,
        cwd=os.getcwd(),
        cores=args.cores,
        ram_mb=args.ram,
        gpu=args.gpu,
        name=args.name,
        priority=args.priority,
    )
    print(msg, file=sys.stderr if rc else sys.stdout)
    return rc


def submit_op(cmd, *, cwd, cores, ram_mb=0, gpu=False, name=None, priority=0):
    """Gate-1 + enqueue. Shared by the CLI and tallyd (v2 remote submit).
    Returns (rc, message)."""
    db.init_db()
    req = Request(cores=cores, ram_mb=ram_mb, gpu=gpu)
    ok, reason = check_feasible(req, Capacity())
    if not ok:
        # Infeasible: never enqueue (it would wait forever). Message verbatim.
        return 1, reason
    job_id = db.insert_job(
        cmd, cwd=cwd, cores=cores, ram_mb=ram_mb, gpu=gpu,
        name=name, priority=priority,
    )
    return 0, f"queued as job {job_id}"


# --------------------------------------------------------------------------
# `tally queue`  (squeue-style list)
# --------------------------------------------------------------------------
def _fmt_age(submitted_at) -> str:
    secs = int(time.time() - (submitted_at or time.time()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60}m"
    return f"{secs // 86400}d"


def format_queue() -> str:
    """The squeue-style table as a string. Shared by the CLI and tallyd."""
    db.init_db()
    rows = list(db.get_running()) + list(db.get_pending())
    if not rows:
        return "(no pending or running jobs)"
    headers = ["ID", "NAME", "STATE", "CORES", "RAM", "GPU", "AGE"]
    widths = [4, 16, 9, 5, 7, 3, 6]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    for r in rows:
        cells = [
            str(r["id"]),
            (r["name"] or "")[:16],
            r["state"],
            str(r["cores"]),
            str(r["ram_mb"]),
            "yes" if r["gpu"] else "no",
            _fmt_age(r["submitted_at"]),
        ]
        lines.append("  ".join(c.ljust(w) for c, w in zip(cells, widths)))
    return "\n".join(lines)


def _queue(argv) -> int:
    argparse.ArgumentParser(prog="tally queue").parse_args(argv)
    print(format_queue())
    return 0


# --------------------------------------------------------------------------
# `tally cancel <id>`
# --------------------------------------------------------------------------
def cancel_op(job_id: int):
    """Cancel a job by id. Shared by the CLI and tallyd. Returns (rc, message)."""
    db.init_db()
    job = db.get_job(job_id)
    if job is None:
        return 1, f"no such job {job_id}"

    state = job["state"]
    if state == "pending":
        db.cancel_job(job_id)
        return 0, f"cancelled pending job {job_id}"
    if state == "running":
        pid = job["pid"]
        if pid:
            try:
                # We launched the wrapper in its own session, so the pgid == pid;
                # signal the whole group to take the real job down with it.
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        # Mark it terminal now; resources are freed the moment it leaves the
        # 'running' set (the next tick recomputes totals without it).
        db.mark_finished(job_id, None)
        return 0, f"SIGTERM sent to job {job_id} (pid {pid}); marked failed"
    return 1, f"job {job_id} is {state}; nothing to cancel"


def _cancel(argv) -> int:
    p = argparse.ArgumentParser(prog="tally cancel")
    p.add_argument("id", type=int)
    args = p.parse_args(argv)
    rc, msg = cancel_op(args.id)
    print(msg, file=sys.stderr if rc else sys.stdout)
    return rc


# --------------------------------------------------------------------------
# `tally status <id>`
# --------------------------------------------------------------------------
def format_status(job) -> str:
    """Full row detail as a string. Shared by the CLI and tallyd."""

    def fmt_time(t):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "-"

    fields = [
        ("id", job["id"]),
        ("name", job["name"] or "-"),
        ("state", job["state"]),
        ("command", " ".join(json.loads(job["command"]))),
        ("cwd", job["cwd"]),
        ("cores", job["cores"]),
        ("ram_mb", job["ram_mb"]),
        ("gpu", "yes" if job["gpu"] else "no"),
        ("priority", job["priority"]),
        ("user", job["user"] or "-"),
        ("pid", job["pid"] if job["pid"] else "-"),
        ("log_path", job["log_path"] or "-"),
        ("submitted_at", fmt_time(job["submitted_at"])),
        ("started_at", fmt_time(job["started_at"])),
        ("finished_at", fmt_time(job["finished_at"])),
        ("exit_code", job["exit_code"] if job["exit_code"] is not None else "-"),
        ("reject_reason", job["reject_reason"] or "-"),
    ]
    width = max(len(k) for k, _ in fields)
    return "\n".join(f"{k.ljust(width)} : {v}" for k, v in fields)


def _status(argv) -> int:
    p = argparse.ArgumentParser(prog="tally status")
    p.add_argument("id", type=int)
    args = p.parse_args(argv)

    db.init_db()
    job = db.get_job(args.id)
    if job is None:
        print(f"no such job {args.id}", file=sys.stderr)
        return 1
    print(format_status(job))
    return 0


# --------------------------------------------------------------------------
# `tally daemon`  (v2: scheduler + Telegram remote control)
# --------------------------------------------------------------------------
def _daemon(argv) -> int:
    from . import daemon  # local import: the daemon pulls in this module's ops
    return daemon.main(argv)


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if argv and argv[0] in QUEUE_COMMANDS:
        return {
            "submit": _submit,
            "queue": _queue,
            "cancel": _cancel,
            "status": _status,
            "daemon": _daemon,
        }[argv[0]](argv[1:])

    # Default path: `tally run ...` or bare `notify-run ...`.
    if argv and argv[0] == "run":
        argv = argv[1:]
    return _run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
