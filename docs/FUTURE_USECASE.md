# Future use case (v2): "Drive the queue from your phone"

Status: planning — not implemented. This is the concrete next step after the
v1.5 admission queue, written so whoever picks it up has a real target instead
of the vague "v2: persistent daemon, Telegram control" roadmap line.

## The use case, told as a story

It's Saturday. Aisha is away from the lab but has three DFTB+ relaxations and a
GROMACS run she wants to get through the weekend on Mindlab-01. Today she'd have
to SSH in, `tally submit` each one, and SSH back periodically to check `tally
queue`. She already gets a Telegram ping when a job finishes (v1.0). The gap:
**she can be *notified* but she can't *act*.** When job 12 fails at 2 AM, she
wants to reorder the queue, bump a priority, cancel a doomed run, or resubmit —
without opening a terminal.

v2 closes that loop: the same Telegram bot that notifies her also **accepts
commands back**. The queue becomes drivable from the phone that's already
buzzing.

## Why this is the right next step (not the others)

The roadmap lists three v2 ideas: persistent daemon, Telegram queue control,
live progress. Telegram control is the highest-leverage because:

- It reuses two things v1.x already built and proved: the **notifier**
  (Telegram transport) and the **queue/DB + scheduler** (v1.5). It's mostly
  wiring, not new subsystems.
- It removes the one manual step that breaks the "walk away" promise — having to
  come *back* to a terminal to manage work.
- "Live progress" depends on plugins emitting structured progress (the
  `Verdict.progress` field is already reserved but unused); that's a bigger,
  fuzzier effort. Telegram control is sharply scoped.

## What v2 adds

A long-running **`tallyd` daemon** (one process, replaces the systemd-timer
one-shot) that:

1. Runs the scheduler `tick()` on its interval (already exists — just hosts it).
2. Long-polls the Telegram `getUpdates` API for inbound messages and maps them
   to queue operations. The bot becomes bidirectional.

### Command surface (mirrors the CLI, 1:1)

| Telegram message      | Maps to            | Notes                                  |
|-----------------------|--------------------|----------------------------------------|
| `/queue`              | `tally queue`      | replies with the squeue-style table    |
| `/status 42`          | `tally status 42`  | full job detail                        |
| `/cancel 42`          | `tally cancel 42`  | confirm if running                     |
| `/priority 42 9`      | new `db` op        | re-rank a pending job                  |
| `/resubmit 42`        | new `db` op        | clone a done/failed job back to pending|
| `/submit 8 -- cmd...` | `tally submit`     | guarded; see security below            |

The point: **no new scheduling logic.** Every command is an existing DB
mutation plus a formatted reply. The scheduler keeps deciding *when*; the engine
keeps deciding *how*. v2 only adds an *input channel*.

## Security — the load-bearing constraint

Letting an inbound chat message launch arbitrary commands on a workstation is a
remote-code-execution surface. Non-negotiables for v2:

- **Allowlist the chat id.** Only `TALLY_TELEGRAM_CHAT_ID` (already configured)
  may issue commands. Drop everything else silently.
- **`/submit` from chat is opt-in**, gated behind `TALLY_ALLOW_REMOTE_SUBMIT=1`,
  off by default. Read-only/queue-management commands (`/queue`, `/status`,
  `/cancel`, `/priority`) are the safe default surface.
- **No shell.** Inbound `/submit` argv is passed to the engine as a list (as the
  CLI already does) — never through a shell string. Reuse `check_feasible`; an
  infeasible remote submit is rejected with the same message.
- Token still lives only in `config.env`, never committed.

## Implementation sketch (smallest viable diff)

- `tally/daemon.py` — `tallyd`: a loop that interleaves `scheduler.tick()` and
  one `getUpdates` long-poll per interval. Single-threaded; no new deps
  (stdlib `urllib`, same as the notifier).
- `tally/notifier.py` — extend with a `poll_updates(offset)` reader. The send
  side already exists; this adds the receive side. Keep stdlib-only.
- `tally/db.py` — two small additions: `set_priority(id, n)` and a `resubmit(id)`
  that clones a terminal row back to `pending`. No schema change — the existing
  `queue` table already has every column needed.
- CLI gains `tally daemon` to launch `tallyd`; the v1.5 systemd unit flips from
  the one-shot timer to a long-running `tallyd` service (the
  `tally-scheduler.service` file already models this shape).
- Tests: a fake-updates feed (inject a list of messages instead of hitting
  Telegram) drives the command dispatcher; assert each maps to the right DB
  state. No network in tests — same philosophy as v1.5's real-subprocess tests.

## Explicitly still out of scope (defer to v2.x / v3)

- Live in-job progress bars over Telegram (needs plugins to populate
  `Verdict.progress` — separate effort).
- cgroups v2 RAM/CPU **enforcement** (the honest v1.5 caveat). Important, but
  orthogonal to the control channel — track it separately.
- Multi-user accounting / multi-node. Still firmly non-goals: tally is one
  workstation's helper, not a cluster manager.

## Definition of done

From her phone, Aisha can text `/queue`, `/priority 14 5`, and `/cancel 12` and
see the queue actually change — with an unauthorized chat id getting nothing and
remote `/submit` refused unless she explicitly enabled it. The "walk away"
promise becomes "walk away *and* stay in control."
