"""
Generic plugin — the default for ANY long-running job.

Knows nothing about chemistry. It watches for the universal signs of a
process going wrong that appear in almost any program's output, plus it
always defers final OK/FATAL to the process exit code (handled by engine).

This is what makes tally useful to someone running a 6-hour data migration
or a video encode — not just scientific codes.
"""
from __future__ import annotations

import re

from .base import JobState, Level, Plugin, Verdict

# Universal, language-agnostic distress signals. Deliberately conservative —
# we'd rather miss a warning than cry wolf on a healthy job.
_FATAL = [
    (re.compile(r"\bSegmentation fault\b", re.I), "Segmentation fault"),
    (re.compile(r"\bkilled\b.*\b(out of memory|oom)\b", re.I), "Out of memory (OOM killed)"),
    (re.compile(r"\bTraceback \(most recent call last\)", ), "Python traceback"),
    (re.compile(r"\bcore dumped\b", re.I), "Core dumped"),
]
_WARN = [
    (re.compile(r"\b(error|fatal)\b", re.I), "Error reported in output"),
]


class GenericPlugin(Plugin):
    name = "generic"

    def matches(self, command, cwd) -> bool:
        # The fallback. Always matches; selected only when nothing more
        # specific claims the job.
        return True

    def inspect(self, new_lines, state: JobState) -> Verdict:
        for line in new_lines:
            for pat, reason in _FATAL:
                if pat.search(line):
                    return Verdict(Level.FATAL, reason)
            for pat, reason in _WARN:
                if pat.search(line):
                    return Verdict(Level.WARN, reason)
        return Verdict(Level.OK)
