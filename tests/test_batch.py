"""
Batch-script parsing tests (tally/batch.py). Pure parsing — no DB, no network,
no subprocesses — so this runs on any platform.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tally import batch  # noqa: E402


def write(text):
    d = tempfile.mkdtemp(prefix="tally_b_")
    path = os.path.join(d, "md1.sh")
    with open(path, "w") as f:
        f.write(text)
    return path


# -------------------------------------------------------------------------
# parse_spec_flags
specs = batch.parse_spec_flags(
    ["--cores", "16", "--ram", "8000", "--gpu", "--name", "md1", "--priority", "5"])
assert specs == {"cores": 16, "ram_mb": 8000, "gpu": True, "name": "md1", "priority": 5}
print("PASS: parse_spec_flags full set")

assert batch.parse_spec_flags([])["cores"] is None  # cores optional at this layer
print("PASS: parse_spec_flags empty -> cores None")

for bad, why in [(["--cores"], "missing value"), (["--bogus", "1"], "unknown flag")]:
    try:
        batch.parse_spec_flags(bad)
        assert False, f"should have raised for {why}"
    except ValueError:
        pass
print("PASS: parse_spec_flags rejects bad input")


# -------------------------------------------------------------------------
# parse_script: the README example
path = write(
    "#!/usr/bin/env bash\n"
    "#TALLY --name md1\n"
    "#TALLY --cores 16\n"
    "#TALLY --ram 8000\n"
    "#TALLY --gpu\n"
    "#TALLY --priority 0\n"
    "\n"
    "module load amber\n"
    "pmemd.cuda -i md.in -o md.out -p sys.prmtop -c sys.rst\n"
)
specs, command = batch.parse_script(path)
assert specs == {"cores": 16, "ram_mb": 8000, "gpu": True, "name": "md1", "priority": 0}, specs
assert command[0] == "bash" and command[1] == "-c"
body = command[2]
assert "module load amber" in body and "pmemd.cuda -i md.in" in body
# shebang and #TALLY lines must NOT leak into the body
assert "#!" not in body and "#TALLY" not in body
print("PASS: parse_script reads directives + multi-line body")

# The binary name survives into the command string -> comp-chem plugin still
# matches (the whole point of `bash -c body` over `bash script.sh`).
from tally.plugins.compchem import CompChemPlugin  # noqa: E402
assert CompChemPlugin().matches(command, os.path.dirname(path)), \
    "compchem plugin must still claim a pmemd batch job"
print("PASS: comp-chem plugin still matches a batch job")

# directives can also share one line, in any order
path = write("#TALLY --cores 4 --name quick\necho hi\n")
specs, command = batch.parse_script(path)
assert specs["cores"] == 4 and specs["name"] == "quick"
print("PASS: multiple directives on one line")

# name defaults to the script basename
path = write("#TALLY --cores 2\nsleep 1\n")
specs, _ = batch.parse_script(path)
assert specs["name"] == "md1", specs  # file is md1.sh
print("PASS: name defaults to script basename")

# missing --cores directive -> clear error
path = write("#TALLY --ram 1000\nsleep 1\n")
try:
    batch.parse_script(path)
    assert False
except ValueError as e:
    assert "--cores" in str(e)
print("PASS: missing --cores directive errors")

# directive-only / empty body -> error (an ordinary # comment is kept as valid
# shell, so use a script that is nothing but directives + blank lines)
path = write("#TALLY --cores 2\n\n   \n")
try:
    batch.parse_script(path)
    assert False
except ValueError as e:
    assert "no job commands" in str(e)
print("PASS: empty body errors")

print("\nALL BATCH TESTS PASSED")
