# tally

**Run a long job. Walk away. Get told when it finishes — or the moment it breaks.**

It's 2 AM. Your 14-hour simulation is on hour 9. You've checked the terminal
forty times. tally is the tool that lets you stop checking: wrap any
long-running command, and tally pushes you a Telegram message when it
completes, fails, or starts emitting trouble.

```bash
tally run --bg --log run.log --name nightly_md -- ./my_simulation input.dat
# -> returns to your shell immediately; job runs detached
# -> phone buzzes when it's done or if it dies
```

No daemon to install. No config required to start (falls back to printing
locally). Pure Python standard library — zero dependencies.

## Why tally instead of `nohup` / `tmux` / SLURM

- `nohup &` detaches but tells you nothing.
- `tmux` keeps a session but you still have to *look*.
- SLURM is a multi-node cluster scheduler — massive overkill for one machine.

tally fills the gap: a lightweight single-machine job runner that actually
notifies you, and understands *why* a job failed, not just that it did.

## Smart failure detection (plugins)

tally watches a job's output through a plugin. The default `generic` plugin
catches universal failure signs (segfaults, OOM kills, tracebacks) on *any*
program. Optional plugins add domain awareness:

- **comp-chem pack** — DFTB+ SCC non-convergence, GROMACS fatal errors,
  Gaussian error termination, NaN detection, plus MD step progress.

Writing a plugin is two methods (`matches`, `inspect`). The engine never
changes — see `tally/plugins/base.py`.

## Install

```bash
pip install -e .          # provides `tally` and the `notify-run` alias
cp examples/config.env.example config.env   # add your Telegram token
```

## Queue: `tally submit` (v1.5)

A single-node **resource-admission queue** — not a time scheduler. A job
declares what it needs; the queue runs it only when those resources are free.
Think "the useful 20% of SLURM for one workstation." No time limits, no
partitions, no fairshare.

```bash
# Queue a job. It runs as soon as 16 cores + 8 GB + the GPU are free.
tally submit --cores 16 --ram 8000 --gpu --name md1 -- pmemd.cuda -i md.in

tally queue          # list pending + running jobs (squeue-style)
tally status 42      # full detail for one job
tally cancel 42      # drop a pending job, or SIGTERM a running one
```

Submit applies **two gates**:

1. **Feasible?** If the request can never fit this machine (e.g. 31 cores on a
   30-usable box), it's rejected at submit time — never queued, since it would
   wait forever.
2. **Available now?** A feasible job is enqueued; the scheduler starts it on the
   first tick where the cores/RAM/GPU it asked for are actually free.

Run the scheduler with the bundled `tally-scheduler` loop (or a systemd timer —
see `examples/systemd/`). Capacity is configured via env vars
(`TALLY_TOTAL_CORES`, `TALLY_SYSTEM_RESERVE_CORES`, `TALLY_GPU_EXCLUSIVE`, …);
see `examples/config.env.example`.

### Caveats (read before you trust the numbers)

- **RAM gating is best-effort.** tally checks free RAM at admission but cannot
  prevent a running job from later ballooning and triggering the OOM killer.
  True enforcement needs cgroups v2 memory limits — that's v2. We say so plainly
  rather than imply isolation we don't provide.
- **Core counts are advisory** unless you also pin with `taskset`/`cpuset`. v1.5
  gates on *declared* cores; actual CPU pinning (cpuset cgroup) is a v2
  hardening step.

## Roadmap

- **v1.x** — runner + plugin verdicts.
- **v1.5** — single-node admission queue: `tally submit` jobs that wait for
  GPU/CPU/RAM to free up before running (this). The lightweight SLURM
  alternative.
- **v2** — persistent daemon, Telegram queue control, live progress, cgroups v2
  RAM/CPU enforcement. See `docs/FUTURE_USECASE.md` for the next concrete step.

MIT licensed.
