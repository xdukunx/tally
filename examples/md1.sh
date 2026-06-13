#!/usr/bin/env bash
#
# tally batch script (SLURM-style). Submit with:  tally submit md1.sh
#
# Top block = job name + resource specs (#TALLY directives).
# Bottom block = the actual job. Directives are plain shell comments, so this
# same file also runs directly with:  bash md1.sh

#TALLY --name md1
#TALLY --cores 16
#TALLY --ram 8000
#TALLY --gpu
#TALLY --priority 0

# --- job ---
# Runs in the directory you submitted from (relative paths resolve there).
# Multi-line setup is fine — module loads, cd, env vars, pipes, etc.
pmemd.cuda -i md.in -o md.out -p sys.prmtop -c sys.rst
