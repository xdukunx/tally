"""
tally engine — the generic core.

Responsibilities (and NOTHING else):
  - spawn a command, optionally detached (fork + setsid)
  - capture its output to a log file
  - tail that output, hand new lines to whichever plugin claimed the job
  - on FATAL verdict or non-zero exit, fire a notification
  - on clean exit, fire a success notification

It contains no domain knowledge. Every chemistry-aware decision happens
inside a plugin via the Verdict contract. Swap the notifier, swap the
plugins — this file never needs to change.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from typing import Callable, Optional

from .plugins.base import JobState, Level, Plugin, Verdict

# Notifier is injected: a callable taking (title, body). Keeps the engine
# decoupled from Telegram specifically — you could wire Slack, email, ntfy.
Notifier = Callable[[str, str], None]


def select_plugin(plugins: list[Plugin], command: list[str], cwd: str) -> Plugin:
    """First non-generic plugin that claims the job wins; generic is the floor."""
    specific = [p for p in plugins if p.name != "generic"]
    for p in specific:
        if p.matches(command, cwd):
            return p
    # Generic is guaranteed present and always matches.
    for p in plugins:
        if p.name == "generic":
            return p
    raise RuntimeError("no generic plugin registered")


def _detach() -> None:
    """fork + setsid so the job survives shell exit / SSH disconnect.

    Ported from the proven v5.x notify-run --bg path. The double-fork
    prevents the daemon from re-acquiring a controlling terminal.
    """
    if os.fork() > 0:
        os._exit(0)        # parent returns to the shell immediately
    os.setsid()
    if os.fork() > 0:
        os._exit(0)        # ensure we're not a session leader


def run(
    command: list[str],
    *,
    plugins: list[Plugin],
    notify: Notifier,
    log_path: str,
    name: Optional[str] = None,
    background: bool = False,
    kill_on_fatal: bool = False,
    poll_interval: float = 2.0,
) -> int:
    """Run COMMAND under tally. Returns the child's exit code (0 if detached)."""
    cwd = os.getcwd()
    job_name = name or os.path.basename(command[0])
    plugin = select_plugin(plugins, command, cwd)

    if background:
        _detach()

    state = JobState(command=command, cwd=cwd, started_at=time.time())

    with open(log_path, "ab", buffering=0) as logf:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # unbuffered; line iteration handled below
            text=False,
        )
        last_warn = None
        assert proc.stdout is not None
        for raw in proc.stdout:
            logf.write(raw)
            line = raw.decode("utf-8", "replace").rstrip("\n")
            verdict = plugin.inspect([line], state)

            if verdict.level is Level.FATAL:
                notify(
                    f"❌ {job_name} — masalah terdeteksi",
                    f"{verdict.reason}\nLog: {log_path}",
                )
                if kill_on_fatal:
                    proc.send_signal(signal.SIGTERM)
                    break
            elif verdict.level is Level.WARN and verdict.reason != last_warn:
                last_warn = verdict.reason
                notify(
                    f"⚠️ {job_name} — peringatan",
                    f"{verdict.reason}\nLog: {log_path}",
                )

        rc = proc.wait()

    elapsed = int(time.time() - state.started_at)
    if rc == 0:
        notify(f"✅ {job_name} — selesai", f"Durasi: {elapsed//60}m {elapsed%60}s")
    else:
        notify(
            f"❌ {job_name} — gagal (exit {rc})",
            f"Durasi: {elapsed//60}m {elapsed%60}s\nLog: {log_path}",
        )
    return rc
