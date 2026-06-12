#!/usr/bin/env bash
set -euo pipefail
source ./config.env 2>/dev/null || true
tally run --bg --log dftb.log --name "dftb_md" -- dftb+ dftb_in.hsd
echo "Job detached. tail -f dftb.log to follow."
