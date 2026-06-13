"""
SLURM-style batch scripts for tally.

Instead of one long inline command:

    tally submit --cores 16 --ram 8000 --gpu --name md1 -- pmemd.cuda -i md.in

write a script with `#TALLY` directives up top and the job below, then:

    tally submit md1.sh

Mirrors `sbatch`: directives are ordinary shell comments, so the very same file
still runs under plain `bash md1.sh`. The top block is the job name +
specifications; the bottom block is the actual command(s).

    #!/usr/bin/env bash
    #TALLY --name md1
    #TALLY --cores 16
    #TALLY --ram 8000
    #TALLY --gpu
    #TALLY --priority 0

    module load amber
    pmemd.cuda -i md.in -o md.out -p sys.prmtop -c sys.rst

The body is run as `bash -c "<body>"`, which (a) supports multi-line scripts —
module loads, `cd`, pipes — and (b) keeps the real binary name in the job's
command string, so the comp-chem plugin still recognises `pmemd`/`gmx`/`dftb+`
and its smart failure detection keeps working. (Running `bash md1.sh` instead
would hide the binary behind "bash" and silently downgrade to generic.)
"""
from __future__ import annotations

import os
import shlex

DIRECTIVE = "#TALLY"


def parse_spec_flags(tokens: list[str]) -> dict:
    """Parse `--cores N --ram MB --gpu --name X --priority N` from a token list.

    Shared by batch directives and tallyd's `/submit`. `cores` comes back as
    None if not given, so callers can decide whether that's an error."""
    cores = None
    ram_mb = 0
    gpu = False
    name = None
    priority = 0
    n = len(tokens)

    def value(i: int) -> str:
        if i + 1 >= n:
            raise ValueError(f"{tokens[i]} needs a value")
        return tokens[i + 1]

    i = 0
    while i < n:
        t = tokens[i]
        if t == "--cores":
            cores = int(value(i)); i += 2
        elif t == "--ram":
            ram_mb = int(value(i)); i += 2
        elif t == "--gpu":
            gpu = True; i += 1
        elif t == "--name":
            name = value(i); i += 2
        elif t == "--priority":
            priority = int(value(i)); i += 2
        else:
            raise ValueError(f"unknown flag/directive: {t!r}")
    return {"cores": cores, "ram_mb": ram_mb, "gpu": gpu,
            "name": name, "priority": priority}


def parse_script(path: str) -> tuple[dict, list[str]]:
    """Read a batch script. Returns (specs, command) where command is the argv
    to enqueue (`["bash", "-c", <body>]`). Raises ValueError on bad input."""
    with open(path) as f:
        raw = f.read()

    directive_tokens: list[str] = []
    body_lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(DIRECTIVE):
            rest = s[len(DIRECTIVE):].strip()
            if rest:
                directive_tokens.extend(shlex.split(rest))
            continue
        if s.startswith("#!"):  # shebang — drop it, bash -c provides the shell
            continue
        body_lines.append(line)

    specs = parse_spec_flags(directive_tokens)
    if specs["cores"] is None:
        raise ValueError(f"no '#TALLY --cores N' directive found in {path}")

    body = "\n".join(body_lines).strip()
    if not body:
        raise ValueError(
            f"no job commands found in {path} "
            "(everything was a directive or comment)"
        )

    # Default the job name to the script's base name, like sbatch.
    if specs["name"] is None:
        specs["name"] = os.path.splitext(os.path.basename(path))[0]

    return specs, ["bash", "-c", body]
