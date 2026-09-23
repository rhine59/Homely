#!/bin/bash
#
# run-homely-scheduled.sh
# Retry once, one hour later, only when Octopus Agile prices are unavailable.
#
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/Users/richardhine/.pyenv/versions/octopus/bin/python"
HOMELY="${SCRIPT_DIR}/homely.py"
RETRY_DELAY=3600

run_homely() {
    local tmp rc
    tmp="$(mktemp)"
    "$PYTHON" "$HOMELY" --all-profiles 2>&1 | tee "$tmp"
    rc=${PIPESTATUS[0]}
    if [[ $rc -eq 0 ]]; then rm -f "$tmp"; return 0; fi
    if grep -q "No Agile prices available for" "$tmp"; then rm -f "$tmp"; return 75; fi
    rm -f "$tmp"
    return "$rc"
}

run_homely
rc=$?

if [[ $rc -eq 75 ]]; then
    echo
    echo "Octopus Agile prices are not yet available."
    echo "Retrying Homely in one hour."
    sleep "$RETRY_DELAY"
    echo
    echo "Retrying Homely after one-hour delay..."
    run_homely
    rc=$?
    if [[ $rc -eq 75 ]]; then
        echo "ERROR: Octopus Agile prices are still unavailable after the one-hour retry." >&2
        exit 1
    fi
fi
exit "$rc"
