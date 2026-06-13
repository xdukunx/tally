"""
tally plugin contract.

This is the ONLY coupling point between the generic engine and any
domain-specific knowledge. The core never imports a concrete plugin and
never contains a domain string (no "SCC", no "pmemd", no "NaN"). All of
that lives behind this interface.

A plugin answers two questions:
  1. matches(command, cwd)  -> is this a job I understand?
  2. inspect(new_lines, st) -> given fresh log output, what's the verdict?

That's the whole surface. Adding a new scientific code = adding one plugin
file. The engine never changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Level(str, Enum):
    OK = "OK"        # nothing notable; keep running
    WARN = "WARN"    # suspicious, but not necessarily fatal; notify, keep running
    FATAL = "FATAL"  # job is doomed; notify and (optionally) kill


@dataclass
class Verdict:
    level: Level = Level.OK
    reason: str = ""
    # progress is OPTIONAL and domain-specific. The engine only forwards it;
    # it never parses it. v1.0 plugins may always leave this None.
    progress: Optional[str] = None


@dataclass
class JobState:
    """Mutable scratchpad handed back to the plugin on every inspect() call.

    The engine owns the lifecycle; the plugin owns the contents. Use it to
    carry parser state across chunks (e.g. last step seen, totals from header).
    """
    command: list[str]
    cwd: str
    started_at: float
    extra: dict = field(default_factory=dict)


class Plugin:
    """Base class. Subclass and override matches/inspect.

    Keep plugins pure: read lines, return a Verdict. No side effects, no
    network, no killing processes — the engine decides what to do with FATAL.
    """

    name: str = "base"

    def matches(self, command: list[str], cwd: str) -> bool:
        raise NotImplementedError

    def inspect(self, new_lines: list[str], state: JobState) -> Verdict:
        raise NotImplementedError
