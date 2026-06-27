#!/usr/bin/env bash
# PreToolUse guard: block edits to lockfiles and secret/env files.
# Exit 2 => deny the tool call and feed stderr back to Claude.
set -uo pipefail

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)"

[ -z "$file" ] && exit 0
base="$(basename "$file")"

case "$base" in
  uv.lock|bun.lockb|bun.lock|package-lock.json|Cargo.lock|poetry.lock|.env|.env.*)
    echo "BLOCKED: '$base' is a lockfile or secret file. Edit the source manifest (pyproject.toml / package.json / Cargo.toml) and regenerate, or edit env files manually outside Claude." >&2
    exit 2
    ;;
esac

exit 0
