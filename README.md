# tally

**A lightweight SLURM alternative for one machine — with your phone as the
control panel.**

Submit jobs, let them queue until the CPU/RAM/GPU they need is free, get a
Telegram message the moment each one finishes or breaks — and check or manage
the whole queue from chat, without ever opening a terminal.

Describe a job in a SLURM-style batch script — specs on top, command below:

```bash
# md1.sh
#!/usr/bin/env bash
#TALLY --name md1
#TALLY --cores 16
#TALLY --ram 8000
#TALLY --gpu

module load amber
pmemd.cuda -i md.in -o md.out -p sys.prmtop -c sys.rst
```

```bash
tally submit md1.sh
# -> "queued as job 42" — runs as soon as 16 cores + 8 GB + the GPU are free
# -> phone buzzes when it finishes (or the moment it starts failing)
```

Directives are plain shell comments, so the same file still runs with
`bash md1.sh`. Prefer one-liners? The inline form works too:

```bash
tally submit --cores 16 --ram 8000 --gpu --name md1 -- pmemd.cuda -i md.in
```

From your phone, to the same bot that pinged you:

```
/queue                 -> live table of pending + running jobs
/status 42             -> full detail for one job
/cancel 42             -> drop it (pending) or stop it (running)
/priority 43 9         -> bump a pending job up the queue
/resubmit 42           -> re-queue a finished/failed job
```

Pure Python standard library. **Zero dependencies. No root. No cluster.**

## Why tally instead of `nohup` / `tmux` / SLURM

- `nohup &` detaches but tells you nothing, and lets ten jobs trample each other.
- `tmux` keeps a session, but you still have to *look*.
- SLURM is a multi-node cluster scheduler — installing it on one workstation is
  a weekend of pain for features you'll never use.

tally is the useful 20% of SLURM for a single machine: a resource-admission
queue (jobs wait until the cores/RAM/GPU they declared are actually free),
plus the part SLURM never gave you — push notifications and remote control
through the bot you already chat with.

## Install (two minutes)

```bash
git clone https://github.com/xdukunx/tally.git
cd tally
pip install .        # provides: tally, tallyd, tally-scheduler, notify-run

# Telegram (optional but the whole point):
# 1. make a bot with @BotFather, get the token
# 2. get your chat id from @userinfobot
mkdir -p ~/.config/tally
cp examples/config.env.example ~/.config/tally/config.env   # edit token + chat id
```

Then start the daemon (scheduler + Telegram control in one process):

```bash
source ~/.config/tally/config.env
tallyd
```

or install it properly so it survives reboots — see
`examples/systemd/tallyd.service` (copy, `systemctl --user enable --now tallyd`).
No Telegram configured? Everything still works; notifications print to stdout
and `tallyd` runs the scheduler only.

## Easy commands

```bash
tally submit job.sh                                   # queue a batch script
tally submit --cores 8 -- ./my_simulation input.dat   # queue a one-liner
tally queue                                           # what's running / waiting
tally status 42                                       # one job, full detail
tally cancel 42                                       # remove / stop a job
tally run --bg --log run.log -- ./quick_job           # v1.0 style: run NOW, just notify
```

### Batch scripts (SLURM-style)

`tally submit job.sh` reads `#TALLY` directives from the top of the file for the
job name and resource specs, then runs the rest as the job. See
[`examples/md1.sh`](examples/md1.sh). Available directives mirror the inline
flags exactly:

| Directive | Meaning |
|-----------|---------|
| `#TALLY --name X` | job name (defaults to the script's filename) |
| `#TALLY --cores N` | cores required (required) |
| `#TALLY --ram MB` | RAM required in MB |
| `#TALLY --gpu` | needs the GPU (exclusive) |
| `#TALLY --priority N` | higher runs first |

Submit applies **two gates**:

1. **Feasible?** A request that can *never* fit this machine (31 cores on a
   30-usable box) is rejected at submit time — never queued, since it would
   wait forever.
2. **Available now?** A feasible job waits in the queue; the scheduler starts
   it on the first tick where the cores/RAM/GPU it asked for are actually free.
   Priority decides ties; the GPU is exclusive (one GPU job at a time).

Capacity is configured in `config.env` (`TALLY_TOTAL_CORES`,
`TALLY_SYSTEM_RESERVE_CORES`, `TALLY_GPU_EXCLUSIVE`, …) — set it once per
machine.

## Checking jobs straight from your phone

`tallyd` makes the notification bot bidirectional. Text it `/queue` from the
bus, `/cancel 12` from bed when the 2 AM failure ping arrives, `/resubmit 12`
after you realize it just needed the retry. Replies come back as aligned
monospace tables.

**Security, because a chat that controls your workstation deserves some:**

- Only messages from **your** configured chat id are processed. Anything else
  is dropped silently.
- `/submit` from chat — starting *new* commands remotely — is **off by
  default**. Set `TALLY_ALLOW_REMOTE_SUBMIT=1` to opt in. Queue management
  (list, status, cancel, priority, resubmit) is the safe default surface.
- Submitted commands are passed as argv lists, never through a shell, and go
  through the same feasibility gate as local submits.
- Your bot token lives in `config.env`, which is never committed.

## Smart failure detection (plugins)

tally watches a job's output through a plugin. The default `generic` plugin
catches universal failure signs (segfaults, OOM kills, tracebacks) on *any*
program. Optional plugins add domain awareness:

- **comp-chem pack** — DFTB+ SCC non-convergence, GROMACS fatal errors,
  Gaussian error termination, NaN detection, plus MD step progress.

Writing a plugin is two methods (`matches`, `inspect`). The engine never
changes — see `tally/plugins/base.py`.

## Caveats (read before you trust the numbers)

- **RAM gating is best-effort.** tally checks free RAM at admission but cannot
  prevent a running job from later ballooning and triggering the OOM killer.
  True enforcement needs cgroups v2 memory limits — that's a future hardening
  step. We say so plainly rather than imply isolation we don't provide.
- **Core counts are advisory** unless you also pin with `taskset`/`cpuset`.
  tally gates on *declared* cores; actual CPU pinning (cpuset cgroup) is a
  future hardening step.
- The queue/daemon target **Linux** (the scheduler uses POSIX process groups
  and `/proc`; the daemon is meant to live under systemd).

## Roadmap

- **v1.x** — runner + plugin verdicts. ✅
- **v1.5** — single-node admission queue (`tally submit`). ✅
- **v2** — `tallyd`: persistent daemon + Telegram queue control. ✅ (this)
- **next** — cgroups v2 RAM/CPU enforcement, live in-job progress over
  Telegram. See `docs/FUTURE_USECASE.md`.

MIT licensed.
