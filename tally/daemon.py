"""
tallyd — the persistent tally daemon (v2).

One process, two jobs:

  1. Run the v1.5 scheduler `tick()` on its interval (TALLY_SCHEDULER_INTERVAL).
  2. Long-poll the Telegram Bot API for inbound messages and map them to queue
     operations — the same bot that notifies you when a job finishes now lets
     you act from your phone:

        /queue              list pending + running (like `tally queue`)
        /status 42          full detail for one job
        /cancel 42          drop pending / SIGTERM running
        /priority 42 9      re-rank a pending job
        /resubmit 42        clone a finished/failed job back into the queue
        /submit ...         enqueue a new job  (opt-in, see security below)
        /help               this list

No new scheduling logic lives here: every command is an existing CLI/DB
operation (`cli.submit_op`, `cli.cancel_op`, `cli.format_queue`, ...) plus a
formatted reply. The scheduler still decides WHEN, the engine still decides
HOW — v2 only adds an input channel.

Security model (the load-bearing part):
  - Only messages from TALLY_TELEGRAM_CHAT_ID are processed. Anything from any
    other chat is dropped silently — no reply, no error, no oracle.
  - /submit is disabled unless TALLY_ALLOW_REMOTE_SUBMIT=1. Queue *management*
    (list/status/cancel/priority/resubmit) is the safe default surface; letting
    a chat message start an arbitrary command on your workstation is a choice
    you must make explicitly.
  - Submitted argv is passed as a list (shlex), never through a shell, and goes
    through the same gate-1 `check_feasible` as a local submit.

If no Telegram credentials are configured, tallyd degrades gracefully to a
plain scheduler loop (identical to `tally-scheduler`).
"""
from __future__ import annotations

import html
import os
import shlex
import sys

from . import cli, db, notifier, scheduler

HELP = (
    "tally commands:\n"
    "/queue — list pending + running jobs\n"
    "/status <id> — full detail for one job\n"
    "/cancel <id> — drop pending / stop running\n"
    "/priority <id> <n> — re-rank a pending job (higher runs first)\n"
    "/resubmit <id> — re-queue a finished/failed job\n"
    "/submit --cores N [--ram MB] [--gpu] [--name X] [--priority N] -- CMD...\n"
    "    (only if TALLY_ALLOW_REMOTE_SUBMIT=1 on the host)\n"
    "/help — this message"
)


def _allow_remote_submit() -> bool:
    return os.environ.get("TALLY_ALLOW_REMOTE_SUBMIT", "0") == "1"


def _parse_submit(parts: list[str]):
    """Parse /submit flags by hand (argparse would sys.exit on bad input).
    Returns (kwargs, cmd) or raises ValueError with a user-facing message."""
    cores = None
    ram = 0
    gpu = False
    name = None
    priority = 0
    cmd: list[str] = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok == "--":
            cmd = parts[i + 1:]
            break
        if tok == "--cores":
            cores = int(parts[i + 1]); i += 2
        elif tok == "--ram":
            ram = int(parts[i + 1]); i += 2
        elif tok == "--gpu":
            gpu = True; i += 1
        elif tok == "--name":
            name = parts[i + 1]; i += 2
        elif tok == "--priority":
            priority = int(parts[i + 1]); i += 2
        else:
            raise ValueError(f"unknown flag {tok!r} — see /help")
    if cores is None:
        raise ValueError("--cores is required")
    if not cmd:
        raise ValueError("no command given (use: /submit --cores N -- cmd args)")
    return dict(cores=cores, ram_mb=ram, gpu=gpu, name=name, priority=priority), cmd


def handle_command(text: str) -> str:
    """Map one inbound message to a queue operation; return the reply text.

    Pure dispatch over the shared CLI ops — this is the function the tests
    drive directly, with no Telegram or network involved."""
    try:
        parts = shlex.split(text)
    except ValueError as e:
        return f"could not parse that: {e}"
    if not parts:
        return HELP
    cmd, args = parts[0].lower(), parts[1:]

    try:
        if cmd in ("/help", "/start"):
            return HELP

        if cmd == "/queue":
            return cli.format_queue()

        if cmd == "/status":
            job = db.get_job(int(args[0]))
            return cli.format_status(job) if job else f"no such job {args[0]}"

        if cmd == "/cancel":
            _, msg = cli.cancel_op(int(args[0]))
            return msg

        if cmd == "/priority":
            job_id, prio = int(args[0]), int(args[1])
            if db.set_priority(job_id, prio):
                return f"job {job_id} priority set to {prio}"
            job = db.get_job(job_id)
            return (f"job {job_id} is {job['state']} — only pending jobs can be re-ranked"
                    if job else f"no such job {job_id}")

        if cmd == "/resubmit":
            new_id = db.resubmit(int(args[0]))
            return (f"queued as job {new_id}" if new_id is not None
                    else f"job {args[0]} not found or not finished yet")

        if cmd == "/submit":
            if not _allow_remote_submit():
                return ("remote submit is disabled on this host "
                        "(set TALLY_ALLOW_REMOTE_SUBMIT=1 to allow it)")
            try:
                kwargs, argv = _parse_submit(args)
            except (ValueError, IndexError) as e:
                return f"bad /submit: {e}"
            _, msg = cli.submit_op(argv, cwd=os.path.expanduser("~"), **kwargs)
            return msg

        return f"unknown command {cmd!r} — try /help"
    except (IndexError, ValueError):
        return f"bad arguments for {cmd} — try /help"


def loop() -> None:
    """tick() + Telegram long-poll, forever. The poll's timeout doubles as the
    inter-tick pause, so the daemon is responsive to messages without ever
    ticking faster than the configured interval."""
    db.init_db()
    interval = scheduler._interval()
    token = os.environ.get("TALLY_TELEGRAM_TOKEN")
    chat_id = os.environ.get("TALLY_TELEGRAM_CHAT_ID")

    if not (token and chat_id):
        print("[tallyd] no TALLY_TELEGRAM_TOKEN/CHAT_ID — running scheduler "
              "only (no remote control)", file=sys.stderr)
        scheduler.loop()
        return

    def send(reply: str) -> None:
        # <pre> keeps the queue table's columns aligned on a phone screen.
        notifier._send_telegram(token, chat_id, f"<pre>{html.escape(reply)}</pre>")

    offset = None
    print(f"[tallyd] up — scheduling every {interval:g}s, "
          f"listening for commands from chat {chat_id}", file=sys.stderr)
    while True:
        try:
            scheduler.tick()
        except Exception as e:  # one bad tick must not kill the daemon
            print(f"[tallyd] tick error: {e}", file=sys.stderr)

        for update in notifier.poll_updates(token, offset, timeout=int(interval)):
            offset = update["update_id"] + 1
            msg = update.get("message") or {}
            # Allowlist: only the configured chat may drive the queue. Drop
            # everything else silently — no reply, no existence oracle.
            if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                reply = handle_command(text)
            except Exception as e:
                reply = f"error: {e}"
            send(reply)


def main(argv=None) -> int:
    loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
