# Handoff: implement tally queue (v1.5)

You're working in the `tally` repo on Mindlab-01. The engine (v1.0) and the
queue's resource-gating logic (v1.5 seed) are already implemented and tested
— do not redesign or rewrite them. Your job is the scheduler loop, CLI, and
DB layer that sit on top.

## Read first, in order
1. `docs/QUEUE_DESIGN.md` — the full design. This is the source of truth.
   Follow it exactly; don't introduce new config keys, table columns, or
   scheduling rules not listed there.
2. `tally/resources.py` — gate 1 (`check_feasible`) and gate 2
   (`check_available`) are DONE and tested in `tests/test_smoke.py`. Call
   them; do not modify their logic. If you find a bug, fix it minimally and
   keep the existing tests passing.
3. `tally/engine.py` — `run()` is the only way jobs execute. The scheduler
   launches jobs by calling this with `background=True`. Do not duplicate
   its detach/notify logic in the scheduler.
4. `tally/cli.py` — existing `tally run` command. Add new subcommands
   alongside it (`submit`, `queue`, `cancel`, `status`), same argparse style.

## What to build

### 1. DB layer (`tally/db.py`)
SQLite, schema exactly as in QUEUE_DESIGN.md `## Schema`. Use the same DB
file the v1.0 system already uses (check for an existing `notify.db` path
convention in the repo / config; if none exists, default to
`~/.local/share/tally/tally.db` and create the dir).

Functions needed: `init_db()`, `insert_job(...)`, `get_pending(order by
priority desc, submitted_at asc)`, `get_running()`, `mark_running(id, pid)`,
`mark_finished(id, exit_code)`, `mark_rejected(id, reason)`,
`cancel_job(id)`.

### 2. `tally submit` (in cli.py)
Flags: `--cores N` (required), `--ram MB` (default 0), `--gpu` (flag),
`--name`, `--priority` (default 0), plus `-- COMMAND ARGS...` same as `run`.

Flow:
- Build a `Request` from the flags.
- Call `check_feasible(req, Capacity())`.
- If infeasible: print the reason from QUEUE_DESIGN.md verbatim (it already
  contains "try lowering your specifications..."), exit 1, do NOT write to DB.
- If feasible: insert into `queue` table with state='pending', print
  `queued as job {id}`, exit 0.

### 3. Scheduler loop (`tally/scheduler.py`)
A function `tick()` doing the 4 steps in QUEUE_DESIGN.md `## Scheduler loop`:
1. Reap finished running jobs (check if pid still alive via `os.kill(pid, 0)`
   or `/proc/{pid}` existence). On exit, mark done/failed via exit code from
   a sentinel file the engine should write (see note below), fire notifier
   (reuse `notifier.from_env()`).
2. Compute running_cores (sum of `cores` over running jobs) and gpu_in_use
   (any running job with gpu=1).
3. For each pending job in order, call `check_available(req, cap,
   running_cores, gpu_in_use)`. If true: mark running, launch via
   `engine.run(cmd, plugins=registry(), notify=..., log_path=...,
   background=True)`, record the pid, and IMMEDIATELY add its cores/gpu to
   the in-memory running totals so the same tick doesn't double-book a
   second job into the same freed slot.
4. Return (no sleep inside `tick()` — sleeping is the caller's job).

**Exit code capture note**: `engine.run()` with `background=True` forks and
returns 0 immediately to the parent — the scheduler can't `wait()` on it
normally after detach. You'll need either (a) have the scheduler launch via
`subprocess.Popen` directly + a small wrapper script that calls into
`engine.run` with `background=False` and writes its exit code to
`{log_path}.exit`, or (b) extend `engine.run` minimally to optionally write
`{log_path}.exit` on completion (preferred — smallest diff, ask before
changing `engine.py` if the change is non-trivial). Pick whichever is the
smaller, cleanest diff and note your choice in the PR description.

A thin entrypoint script or systemd timer calls `tick()` every
`TALLY_SCHEDULER_INTERVAL` seconds (default 5). Provide both a standalone
`tally-scheduler` loop script AND document the systemd timer unit (pattern:
look at how `notify-logbook`'s existing systemd units are structured for
style consistency, e.g. `compchem-drain.timer`/`.service` if present in repo
history — match that naming convention).

### 4. `tally queue`, `tally cancel`, `tally status` (in cli.py)
- `tally queue`: print a table (id, name, state, cores, ram, gpu, age) for
  pending + running jobs. Plain text table, no extra deps.
- `tally cancel <id>`: if pending, delete from queue. If running, SIGTERM the
  pid and mark as failed/cancelled, free its resources on next tick.
- `tally status <id>`: print full row detail for one job.

## Constraints (do not violate)

- No new third-party dependencies — stdlib only, matching the rest of the
  repo's zero-dep philosophy.
- Do not touch `tally/plugins/*` — out of scope.
- Do not change the `tally run` (non-queue) command's existing behavior or
  flags — it must keep working unmodified for backward compat.
- Config reads from env vars exactly as named in QUEUE_DESIGN.md (don't
  rename `TALLY_TOTAL_CORES` etc.).
- Keep `resources.py`'s public function signatures (`check_feasible`,
  `check_available`, `Capacity`, `Request`) unchanged — other code and tests
  depend on them.

## Test plan — implement these in `tests/test_queue.py`

Port the scenarios from QUEUE_DESIGN.md `## Test plan` into real tests:
- submit 31 cores -> rejected, not in DB, correct message substring.
- submit 20 + 15 cores sequentially -> both enqueued; after one tick, only
  the 20-core job is running; after it "finishes" (simulate by killing/
  waiting), the 15-core job starts on the next tick.
- two gpu=1 submissions -> second stays pending while first runs.
- cancel a pending job -> removed from queue.
- cancel a running job -> SIGTERM sent, pid reaped, resources freed.

Use short-lived real commands (`sleep 2`, `sleep 0.5`) for running jobs in
tests rather than mocks — this is a small enough scope that real subprocess
behavior is more trustworthy than mocked `os.kill`/`/proc` checks. Existing
`tests/test_smoke.py` must keep passing unmodified.

## When done

- Run `PYTHONPATH=. python tests/test_smoke.py` and
  `PYTHONPATH=. python tests/test_queue.py` (or pytest if you set that up) —
  both must pass.
- Update `README.md`: add a `tally submit` / `tally queue` usage section
  under the existing roadmap, and **keep the RAM-gating caveat from
  QUEUE_DESIGN.md's "Caveats" section** — do not soften or drop it. State
  plainly that RAM checks are best-effort at admission time, not enforced
  isolation (cgroups v2 enforcement is v2 scope).
- Open a PR against `main` from `feat/queue` with a summary of what was
  built, the exit-code-capture approach chosen, and any deviations from
  QUEUE_DESIGN.md (there should ideally be none — flag and justify any).
