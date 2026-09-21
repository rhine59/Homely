#!/bin/bash
set -euo pipefail

LABEL="com.richard.homely"
PLIST_NAME="${LABEL}.plist"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_PLIST="${SCRIPT_DIR}/${PLIST_NAME}"
TARGET_PLIST="${HOME}/Library/LaunchAgents/${PLIST_NAME}"
DOMAIN="gui/$(id -u)"

echo "Installing Homely LaunchAgent"
echo "Repository: ${SCRIPT_DIR}"

if [[ ! -f "${SOURCE_PLIST}" ]]; then
    echo "ERROR: Cannot find ${SOURCE_PLIST}" >&2
    exit 1
fi

plutil -lint "${SOURCE_PLIST}"

mkdir -p "${HOME}/Library/LaunchAgents"
cp "${SOURCE_PLIST}" "${TARGET_PLIST}"

launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "${TARGET_PLIST}"
launchctl enable "${DOMAIN}/${LABEL}"

echo
echo "LaunchAgent installed."
launchctl print "${DOMAIN}/${LABEL}"

echo
echo "To run it immediately:"
echo "  launchctl kickstart -k ${DOMAIN}/${LABEL}"
echo
echo "To inspect it:"
echo "  launchctl print ${DOMAIN}/${LABEL}"
echo
echo "To remove it:"
echo "  launchctl bootout ${DOMAIN}/${LABEL}"
