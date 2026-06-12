# tally queue (v1.5) — design doc

Status: design complete, ready to implement.
Audience: whoever implements this (Claude Code on Mindlab-01).
Prerequisite: tally v1.0 engine + plugin contract already merged.

## Goal

Add a single-node resource-admission queue. NOT a time scheduler. A job
declares the resources it needs; the queue runs it only when those resources
are free. No time limits, no partitions, no fairshare. Think "the useful 20%
of SLURM for one workstation."

## Non-goals (explicitly out of scope for v1.5)

- Time limits / wall-clock partitions
- Multi-node / networked scheduling
- Fairshare or per-user accounting
- True RAM isolation (see Caveats)

## The two-gate model (the core correctness decision)

A "can't run" condition splits into two cases that must NOT be conflated:

1. **Infeasible** — the request can never be satisfied by this machine
   (e.g. asks for 31 cores on a 30-usable box, or more RAM than installed).
   -> REJECT at submit time. Error: "try lowering your specifications
   (requested {x}, max available {y})". Never enqueue — it would wait forever.

2. **Temporarily unavailable** — request is valid but resources are busy now.
   -> ENQUEUE as pending. The scheduler loop runs it when resources free up.

Gate 1 checks the request against TOTAL capacity (at submit).
Gate 2 checks the request against FREE capacity (every scheduler tick).

## Resource model

Config (in config.env, with these defaults for Mindlab-01):

    TALLY_TOTAL_CORES=32
    TALLY_SYSTEM_RESERVE_CORES=2     # keep idle for the OS; never schedule onto these
    TALLY_MAX_CORES_PER_JOB=30       # hard cap per single job
    TALLY_TOTAL_RAM_MB=32000
    TALLY_SYSTEM_RESERVE_RAM_MB=2000
    TALLY_GPU_EXCLUSIVE=1            # at most one GPU job at a time

Usable cores = TOTAL_CORES - SYSTEM_RESERVE_CORES = 30.

Gate 1 (feasibility) rejects if ANY of:
  - cores_requested > MAX_CORES_PER_JOB
  - cores_requested > (TOTAL_CORES - SYSTEM_RESERVE_CORES)
  - ram_mb_requested > (TOTAL_RAM_MB - SYSTEM_RESERVE_RAM_MB)
  - gpu_requested and no GPU exists

Gate 2 (availability) admits a pending job only if ALL hold right now:
  - sum(cores of running jobs) + cores_requested <= usable cores
  - free RAM (from /proc/meminfo MemAvailable) >= ram_mb_requested + reserve
  - if gpu_requested and GPU_EXCLUSIVE: no other running job holds the GPU

GPU detection: parse `nvidia-smi --query-gpu=memory.used --format=csv,noheader`
or check for a running job flagged gpu=1. Keep it simple — exclusivity, not
fractional GPU sharing (that's a v2 concern).

## Schema (add to the existing tally SQLite db)

    CREATE TABLE IF NOT EXISTS queue (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      name         TEXT,
      command      TEXT NOT NULL,          -- json-encoded argv
      cwd          TEXT NOT NULL,
      cores        INTEGER NOT NULL,
      ram_mb       INTEGER NOT NULL DEFAULT 0,
      gpu          INTEGER NOT NULL DEFAULT 0,
      user         TEXT,
      priority     INTEGER NOT NULL DEFAULT 0,
      state        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|rejected
      pid          INTEGER,
      log_path     TEXT,
      submitted_at REAL NOT NULL,
      started_at   REAL,
      finished_at  REAL,
      exit_code    INTEGER,
      reject_reason TEXT
    );

## CLI surface

    tally submit --cores 16 --ram 8000 --gpu --name md1 -- pmemd.cuda -i md.in ...
        -> gate 1; on pass prints "queued as job 42"; on fail prints reject reason, exit 1

    tally queue            # list pending + running, like squeue
    tally cancel 42        # remove pending / SIGTERM running
    tally status 42        # detail on one job

## Scheduler loop (tallyd, or a systemd timer calling a one-shot)

Every N seconds (default 5):
  1. read running jobs; reap any whose pid has exited -> mark done/failed,
     fire notifier (reuse v1.0 notifier).
  2. recompute free resources.
  3. for each pending job in (priority desc, submitted_at asc):
       if gate 2 passes: mark running, launch via the v1.0 engine.run()
       (background=True), record pid. Subtract its resources from the free
       pool so the same tick doesn't double-book.
  4. sleep.

Reuse, do not reinvent: launching a job is engine.run(...) with the same
plugins and notifier. The queue decides WHEN; the engine handles HOW.

## Caveats to document honestly in the README

- RAM gating is best-effort: tally checks free RAM at admission but cannot
  prevent a running job from later ballooning and triggering the OOM killer.
  True enforcement needs cgroups v2 memory limits — that's v2. Say so plainly.
- Core counts are advisory unless you also pin with taskset/cpuset. v1.5 gates
  on declared cores; actual pinning (cpuset cgroup) is a v2 hardening step.

## Test plan (Claude Code: write these against real hardware)

- submit 31 cores -> rejected with the specs message, not enqueued.
- submit 20 + 15 cores -> second waits until first finishes.
- submit two gpu=1 jobs -> second waits (exclusivity).
- submit valid job on idle box -> runs within one tick.
- cancel a pending job -> removed; cancel a running job -> SIGTERM + reaped.
- crash a running job -> reaped as failed, notifier fires, resources freed.
