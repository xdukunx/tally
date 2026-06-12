"""
tally queue job wrapper (v1.5).

This is the small foreground process the scheduler launches per job:

    python -m tally.runjob --log run.log --name md1 -- pmemd.cuda -i md.in ...

It exists for one reason: capture the engine's exit code into a sentinel file.

`engine.run(background=True)` double-forks and returns 0 to the parent, so the
scheduler can never `wait()` for the real exit code — and across a systemd
one-shot tick the launching process is gone anyway. So the scheduler runs jobs
via this wrapper as a normal `subprocess.Popen` child (which gives it a stable
pid to record) and we call `engine.run(..., background=False)` here in the
foreground. When the engine returns, we write the exit code to
`{log_path}.exit`; the next scheduler tick reads it to mark the job done/failed.

This is option (a) from the handoff (small wrapper script) — chosen over
patching `engine.py` so the v1.0 engine stays byte-for-byte unchanged. The
engine still fires the completion notification itself (it always has), so the
scheduler does not re-notify and there is no duplicate ping.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import notifier
from .engine import run
from .plugins.compchem import CompChemPlugin
from .plugins.generic import GenericPlugin


def _registry():
    # Same registry the v1.0 CLI uses — launching a queued job must behave
    # identically to `tally run`.
    return [CompChemPlugin(), GenericPlugin()]


def _write_exit(log_path: str, rc: int) -> None:
    exit_path = f"{log_path}.exit"
    tmp = f"{exit_path}.tmp"
    # Write-then-rename so a tick never reads a half-written sentinel.
    with open(tmp, "w") as f:
        f.write(str(rc))
    os.replace(tmp, exit_path)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="tally-runjob")
    p.add_argument("--log", required=True)
    p.add_argument("--name", default=None)
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        p.error("no command given")

    rc = 1
    try:
        rc = run(
            cmd,
            plugins=_registry(),
            notify=notifier.from_env(),
            log_path=args.log,
            name=args.name,
            background=False,
        )
    finally:
        # Always leave a sentinel, even if the engine raised, so the scheduler
        # never waits forever on a job whose wrapper died.
        _write_exit(args.log, rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
