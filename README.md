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

## Roadmap

- **v1.x** — runner + plugin verdicts (this).
- **v1.5** — optional single-node queue: `tally submit` jobs that wait for
  GPU/CPU/RAM to free up before running. The lightweight SLURM alternative.
- **v2** — persistent daemon, Telegram queue control, live progress.

MIT licensed.
