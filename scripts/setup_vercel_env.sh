#!/usr/bin/env bash
# Push every var from local .env into Vercel project env (production + preview).
# Run once after `vercel link`. Re-running is safe — adds missing, skips existing.
#
# Usage:  bash scripts/setup_vercel_env.sh
# Skip:   prefix lines in .env with `# ` to exclude them.

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
TARGETS="${TARGETS:-production preview development}"
PROJECT_NAME="lead-gen"

if [ ! -f "$ENV_FILE" ]; then
  echo "no $ENV_FILE found"
  exit 1
fi

# What's already in Vercel — skip those.
existing=$(vercel env ls 2>/dev/null | awk 'NR>3 {print $1}' | grep -E '^[A-Z_]' || true)

count_pushed=0
count_skipped=0

while IFS= read -r line; do
  # Skip blanks + comments
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  # Only KEY=VALUE
  [[ ! "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] && continue

  key="${line%%=*}"
  value="${line#*=}"
  # Strip surrounding quotes if present
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"

  if echo "$existing" | grep -qx "$key"; then
    echo "↷ skip $key (already set)"
    count_skipped=$((count_skipped + 1))
    continue
  fi

  for target in $TARGETS; do
    printf "%s" "$value" | vercel env add "$key" "$target" --sensitive >/dev/null 2>&1 \
      && echo "✓ pushed $key → $target" \
      || echo "✗ failed $key → $target"
  done
  count_pushed=$((count_pushed + 1))
done < "$ENV_FILE"

echo ""
echo "Done. pushed=$count_pushed skipped=$count_skipped"
echo "Next: vercel deploy --prod (or git push to trigger auto-deploy)"
