"""
Comp-chem plugin pack.

This is where the lab-specific smart-alert logic from the old notify-run
lives now — but behind the plugin contract, so the engine stays generic.
It's also the reference implementation proving the plugin system works.

Covers: DFTB+, Amber (pmemd), GROMACS, ORCA, Gaussian.
Each detector returns OK/WARN/FATAL; a few also emit progress ("step N/M").
"""
from __future__ import annotations

import re

from .base import JobState, Level, Plugin, Verdict

# Binaries we recognise on the command line -> claim the job.
_BINARIES = ("dftb+", "pmemd", "mdrun", "gmx", "orca", "g16", "g09")

# Domain alert patterns (ported from the v5.x notify-run engine).
_FATAL = [
    (re.compile(r"SCC did not converge", re.I), "SCC did not converge (DFTB+)"),
    (re.compile(r"GROMACS\s+Fatal\s+error", re.I), "GROMACS fatal error"),
    (re.compile(r"Error termination", re.I), "Gaussian error termination"),
    (re.compile(r"ORCA finished by error", re.I), "ORCA terminated with error"),
    (re.compile(r"forrtl:\s+severe", re.I), "Fortran runtime error"),
]
_WARN = [
    (re.compile(r"\bNaN\b"), "NaN detected in output"),
    (re.compile(r"Error in user input", re.I), "Input error"),
]

# Progress parsers (optional, opt-in). The engine never calls these directly;
# inspect() decides whether to populate Verdict.progress.
_AMBER_STEP = re.compile(r"NSTEP\s*=\s*(\d+)")
_GROMACS_STEP = re.compile(r"^\s*step\s+(\d+)", re.I)
_DFTB_STEP = re.compile(r"MD step:\s*(\d+)", re.I)


class CompChemPlugin(Plugin):
    name = "compchem"

    def matches(self, command, cwd) -> bool:
        joined = " ".join(command).lower()
        return any(b in joined for b in _BINARIES)

    def inspect(self, new_lines, state: JobState) -> Verdict:
        progress = None
        for line in new_lines:
            for pat, reason in _FATAL:
                if pat.search(line):
                    return Verdict(Level.FATAL, reason)
            for pat, reason in _WARN:
                if pat.search(line):
                    # Carry on but flag it.
                    return Verdict(Level.WARN, reason)
            # Progress is best-effort; last match in the chunk wins.
            for pat in (_AMBER_STEP, _GROMACS_STEP, _DFTB_STEP):
                m = pat.search(line)
                if m:
                    total = state.extra.get("total_steps")
                    progress = f"step {m.group(1)}" + (f"/{total}" if total else "")
        return Verdict(Level.OK, progress=progress)
