#!/usr/bin/env bash

set -euo pipefail

ACTION="${1:-}"
EMAIL="${2:-}"
REASON="${3:-}"

LOCK_DB="${POLICYD_SCRIPT_LOCK_DB:-/tmp/policyd_locked_accounts.db}"
LOG_FILE="${POLICYD_SCRIPT_LOG_FILE:-/tmp/policyd_script_actions.log}"

log() {
  printf '%s account_action action=%s email=%s reason=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ACTION" "$EMAIL" "$REASON" >> "$LOG_FILE"
}

ensure_db() {
  touch "$LOCK_DB"
}

lock_account() {
  ensure_db
  grep -Fvx "$EMAIL" "$LOCK_DB" > "${LOCK_DB}.tmp" || true
  printf '%s\n' "$EMAIL" >> "${LOCK_DB}.tmp"
  mv "${LOCK_DB}.tmp" "$LOCK_DB"
  log
}

unlock_account() {
  ensure_db
  grep -Fvx "$EMAIL" "$LOCK_DB" > "${LOCK_DB}.tmp" || true
  mv "${LOCK_DB}.tmp" "$LOCK_DB"
  log
}

status_account() {
  ensure_db
  if grep -Fxq "$EMAIL" "$LOCK_DB"; then
    printf 'locked\n'
  else
    printf 'active\n'
  fi
}

if [[ -z "$ACTION" || -z "$EMAIL" ]]; then
  printf 'usage: %s <lock|unlock|status> <email> [reason]\n' "$0" >&2
  exit 2
fi

case "$ACTION" in
  lock)
    lock_account
    ;;
  unlock)
    unlock_account
    ;;
  status)
    status_account
    ;;
  *)
    printf 'unknown action: %s\n' "$ACTION" >&2
    exit 2
    ;;
esac
