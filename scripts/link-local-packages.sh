#!/usr/bin/env bash
# link-local-packages.sh — create .pth files in every venv so local packages
# are importable without pip install -e.  Run once after creating venvs.
#
#   ./scripts/link-local-packages.sh          # link all
#   ./scripts/link-local-packages.sh --check  # verify only
#
# Each venv gets one .pth per local package pointing to its src/ directory.
# Works because Python reads *.pth files in site-packages at startup.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=true
fi

LOCAL_PACKAGES=(
    "Shared:gamedev_shared"
)

CROSS_DEPS=(
    "Text3D:Text2D"
)

ok=0
fail=0
skip=0
incr() { eval "$1=\$((\$$1 + 1))"; }

for pkg_dir in "$REPO_ROOT"/*/; do
    pkg_name="$(basename "$pkg_dir")"
    venv="$pkg_dir.venv"
    if [[ ! -d "$venv" ]]; then
        continue
    fi

    site_packages="$(find "$venv/lib" -name "site-packages" -type d 2>/dev/null | head -1)"
    if [[ -z "$site_packages" ]]; then
        echo "SKIP $pkg_name: no site-packages found"
        incr skip
        continue
    fi

    for entry in "${LOCAL_PACKAGES[@]}"; do
        IFS=':' read -r dir name <<< "$entry"
        src_dir="$REPO_ROOT/$dir/src"
        if [[ ! -d "$src_dir" ]]; then
            continue
        fi

        pth_file="$site_packages/_monorepo_${name}.pth"
        if $CHECK_ONLY; then
            if [[ -f "$pth_file" ]]; then
                existing="$(cat "$pth_file")"
                if [[ "$existing" == "$src_dir" ]]; then
                    incr ok
                else
                    echo "MISMATCH $pkg_name: $pth_file has '$existing', expected '$src_dir'"
                    incr fail
                fi
            else
                echo "MISSING $pkg_name: $pth_file"
                incr fail
            fi
            continue
        fi

        rm -f "$site_packages/__editable__.gamedev_shared"*.pth
        rm -rf "$site_packages/gamedev_shared"*.dist-info

        echo "$src_dir" > "$pth_file"
        echo "LINK  $pkg_name ← $src_dir"
        incr ok
    done

    for entry in "${CROSS_DEPS[@]}"; do
        IFS=':' read -r owner dep <<< "$entry"
        [[ "$pkg_name" != "$owner" ]] && continue

        dep_src="$REPO_ROOT/$dep/src"
        if [[ ! -d "$dep_src" ]]; then
            continue
        fi

        dep_name="$(basename "$dep" | tr '[:upper:]' '[:lower:]' | tr '-' '_')"
        pth_file="$site_packages/_monorepo_${dep_name}.pth"

        if $CHECK_ONLY; then
            if [[ -f "$pth_file" ]]; then
                incr ok
            else
                echo "MISSING $pkg_name (cross-dep $dep): $pth_file"
                incr fail
            fi
            continue
        fi

        echo "$dep_src" > "$pth_file"
        echo "LINK  $pkg_name ← $dep_src (cross-dep)"
        incr ok
    done
done

if $CHECK_ONLY; then
    echo "--- Check complete: $ok ok, $fail missing/mismatch, $skip skipped ---"
    exit $((fail > 0 ? 1 : 0))
else
    echo "--- Done: $ok linked, $skip skipped ---"
fi
