"""
tally queue persistence (v1.5).

A thin SQLite layer for the admission queue. Schema is exactly the `queue`
table from docs/QUEUE_DESIGN.md — no extra columns, no ORM, stdlib only.

The same DB file is shared by the v1.0 system and the queue. There was no
pre-existing `notify.db` convention in the repo, so we default to the
XDG-style path `~/.local/share/tally/tally.db` (created on first use). An
explicit `db_path` argument always wins — the scheduler and the tests pass one
so they never touch a developer's real queue. `TALLY_DB_PATH` is honoured as an
ops/test convenience override for the default.

Every helper opens a short-lived connection (queue traffic is tiny: a handful
of rows, a tick every few seconds), so there is no shared global connection to
leak across the systemd one-shot / long-loop boundary.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Optional

SCHEMA = """
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
"""


def default_db_path() -> str:
    """Where the queue lives when no explicit path is given."""
    override = os.environ.get("TALLY_DB_PATH")
    if override:
        return override
    base = os.environ.get(
        "XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share")
    )
    return os.path.join(base, "tally", "tally.db")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open (and lazily create the directory for) the queue DB."""
    path = db_path or default_db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def insert_job(
    command: list[str],
    cwd: str,
    cores: int,
    *,
    ram_mb: int = 0,
    gpu: bool = False,
    name: Optional[str] = None,
    priority: int = 0,
    user: Optional[str] = None,
    state: str = "pending",
    reject_reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Insert a job row and return its id."""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO queue
                (name, command, cwd, cores, ram_mb, gpu, user, priority,
                 state, submitted_at, reject_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                json.dumps(command),
                cwd,
                cores,
                ram_mb,
                1 if gpu else 0,
                user if user is not None else os.environ.get("USER", ""),
                priority,
                state,
                time.time(),
                reject_reason,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_pending(db_path: Optional[str] = None) -> list[sqlite3.Row]:
    """Pending jobs in scheduling order: priority desc, then oldest first."""
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM queue WHERE state='pending' "
            "ORDER BY priority DESC, submitted_at ASC"
        ).fetchall()
    finally:
        conn.close()


def get_running(db_path: Optional[str] = None) -> list[sqlite3.Row]:
    conn = connect(db_path)
    try:
        return conn.execute(
            "SELECT * FROM queue WHERE state='running' ORDER BY started_at ASC"
        ).fetchall()
    finally:
        conn.close()


def get_job(job_id: int, db_path: Optional[str] = None) -> Optional[sqlite3.Row]:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT * FROM queue WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()


def mark_running(
    job_id: int, pid: int, log_path: str, db_path: Optional[str] = None
) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE queue SET state='running', pid=?, log_path=?, started_at=? "
            "WHERE id=?",
            (pid, log_path, time.time(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_finished(
    job_id: int, exit_code: Optional[int], db_path: Optional[str] = None
) -> None:
    """Move a running job to done (exit 0) or failed (anything else / None)."""
    state = "done" if exit_code == 0 else "failed"
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE queue SET state=?, exit_code=?, finished_at=? WHERE id=?",
            (state, exit_code, time.time(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_rejected(job_id: int, reason: str, db_path: Optional[str] = None) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE queue SET state='rejected', reject_reason=?, finished_at=? "
            "WHERE id=?",
            (reason, time.time(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_job(job_id: int, db_path: Optional[str] = None) -> None:
    """Delete a (pending) job outright. Running jobs are handled by the CLI:
    it SIGTERMs the pid and lets the next tick reap it as failed."""
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()
