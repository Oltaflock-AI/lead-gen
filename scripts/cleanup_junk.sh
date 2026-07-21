#!/bin/bash
# Junk-file auto-cleanup. Runs on Claude Code SessionStart (see .claude/settings.json)
# or manually: bash scripts/cleanup_junk.sh
# Scope: repo only. Never touches .git, .venv, node_modules.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 0

# -exec rm (not -delete): -delete implies depth-first, which breaks -prune on BSD find.
PRUNE=(-path ./.git -prune -o -path ./.venv -prune -o -path ./node_modules -prune -o)

# Always junk: caches + OS droppings
find . "${PRUNE[@]}" -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ipynb_checkpoints \) -prune -exec rm -rf {} + 2>/dev/null
find . "${PRUNE[@]}" -type f \( -name "*.pyc" -o -name ".DS_Store" -o -name "*.tmp" -o -name "*~" -o -name "*.orig" -o -name "*.rej" \) -exec rm -f {} + 2>/dev/null

# Age-gated junk (>7 days): backups + rotated logs. Current .log files kept.
find . "${PRUNE[@]}" -type f \( -name "*.bak" -o -name "*.bak.*" -o -name "*.log.[0-9]*" \) -mtime +7 -exec rm -f {} + 2>/dev/null

exit 0
