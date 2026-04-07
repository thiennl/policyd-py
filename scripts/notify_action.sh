#!/usr/bin/env bash

set -euo pipefail

EVENT="${1:-}"
EMAIL="${2:-}"
MESSAGE="${3:-}"

LOG_FILE="${POLICYD_SCRIPT_NOTIFY_LOG_FILE:-/tmp/policyd_script_notifications.log}"

if [[ -z "$EVENT" || -z "$EMAIL" ]]; then
  printf 'usage: %s <event> <email> [message]\n' "$0" >&2
  exit 2
fi

printf '%s notify_action event=%s email=%s message=%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$EVENT" "$EMAIL" "$MESSAGE" >> "$LOG_FILE"
