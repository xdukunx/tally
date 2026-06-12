"""
tally CLI.

Backward-compatible with the old notify-run invocation so existing run_*.sh
templates keep working:

    notify-run --bg --log run.log --name my_md -- dftb+ dftb_in.hsd

New canonical form:

    tally run --bg --log run.log --name my_md -- dftb+ dftb_in.hsd
"""
from __future__ import annotations

import argparse
import sys

from . import notifier
from .engine import run
from .plugins.compchem import CompChemPlugin
from .plugins.generic import GenericPlugin


def _registry():
    # Plain list, hardcoded for v1.0. Real plugin discovery (entry points)
    # is a v2 concern — don't gold-plate a six-plugin system.
    return [CompChemPlugin(), GenericPlugin()]


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # Allow `tally run ...` or bare `notify-run ...`
    if argv and argv[0] == "run":
        argv = argv[1:]

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


if __name__ == "__main__":
    raise SystemExit(main())
