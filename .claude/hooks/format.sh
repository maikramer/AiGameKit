#!/usr/bin/env bash
# PostToolUse formatter: routes the edited file to the right formatter by
# extension/location. Best-effort; never blocks (always exits 0).
set -uo pipefail

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"

[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

case "$file" in
  *.py)
    ruff format "$file" >/dev/null 2>&1
    ruff check --fix "$file" >/dev/null 2>&1
    ;;
  *.rs)
    rustfmt "$file" >/dev/null 2>&1
    ;;
  "$repo_root"/VibeGame/*.ts|"$repo_root"/VibeGame/*.tsx|"$repo_root"/VibeGame/*.js|"$repo_root"/VibeGame/*.mjs|"$repo_root"/VibeGame/*.json|"$repo_root"/VibeGame/*.css|"$repo_root"/VibeGame/*.md)
    "$repo_root/VibeGame/node_modules/.bin/prettier" --write "$file" >/dev/null 2>&1
    ;;
esac

exit 0
