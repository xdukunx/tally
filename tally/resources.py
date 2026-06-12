"""
tally resource gating (queue v1.5 seed).

Implements the two-gate admission model from docs/QUEUE_DESIGN.md:
  - feasibility (gate 1): is this request EVER satisfiable on this box?
  - availability (gate 2): can it run RIGHT NOW given what's already running?

This module is pure and testable — it does not launch anything. The scheduler
loop calls it; the engine does the actual running.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Capacity:
    total_cores: int = int(os.environ.get("TALLY_TOTAL_CORES", "32"))
    reserve_cores: int = int(os.environ.get("TALLY_SYSTEM_RESERVE_CORES", "2"))
    max_cores_per_job: int = int(os.environ.get("TALLY_MAX_CORES_PER_JOB", "30"))
    total_ram_mb: int = int(os.environ.get("TALLY_TOTAL_RAM_MB", "32000"))
    reserve_ram_mb: int = int(os.environ.get("TALLY_SYSTEM_RESERVE_RAM_MB", "2000"))
    gpu_exclusive: bool = os.environ.get("TALLY_GPU_EXCLUSIVE", "1") == "1"

    @property
    def usable_cores(self) -> int:
        return self.total_cores - self.reserve_cores


@dataclass
class Request:
    cores: int
    ram_mb: int = 0
    gpu: bool = False


def has_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def free_ram_mb() -> int:
    """Read MemAvailable from /proc/meminfo (kB -> MB)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def check_feasible(req: Request, cap: Capacity) -> tuple[bool, str]:
    """Gate 1. Returns (ok, reason). reason is the user-facing reject message."""
    if req.cores > cap.max_cores_per_job:
        return False, (
            f"try lowering your specifications "
            f"(requested {req.cores} cores, max per job is {cap.max_cores_per_job})"
        )
    if req.cores > cap.usable_cores:
        return False, (
            f"try lowering your specifications "
            f"(requested {req.cores} cores, only {cap.usable_cores} usable — "
            f"{cap.reserve_cores} reserved for the OS)"
        )
    max_ram = cap.total_ram_mb - cap.reserve_ram_mb
    if req.ram_mb > max_ram:
        return False, (
            f"try lowering your specifications "
            f"(requested {req.ram_mb} MB RAM, only {max_ram} MB usable)"
        )
    if req.gpu and not has_gpu():
        return False, "no GPU available on this machine"
    return True, ""


def check_available(
    req: Request,
    cap: Capacity,
    running_cores: int,
    gpu_in_use: bool,
) -> bool:
    """Gate 2. running_cores/gpu_in_use describe the CURRENT running set."""
    if running_cores + req.cores > cap.usable_cores:
        return False
    if req.ram_mb and free_ram_mb() < req.ram_mb + cap.reserve_ram_mb:
        return False
    if req.gpu and cap.gpu_exclusive and gpu_in_use:
        return False
    return True
