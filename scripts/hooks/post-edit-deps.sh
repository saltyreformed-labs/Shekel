#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): block when a dependency file is modified.
# New dependencies require developer approval (CLAUDE.md: "Dependencies declared
# in pyproject.toml and locked in uv.lock -- no new packages without approval").
# Reads the edited path from the stdin JSON payload (see _hooklib.sh). Exit 2
# surfaces the change to Claude so it pauses and confirms rather than proceeding
# silently.
#
# Matches both files (ruling balance:R-BAL294): pyproject.toml declares every
# package, the dev tools CI and the local venv install included, and uv.lock
# pins each one, indirect ones too, so a hand edit of either is a supply-chain
# event. A dev package counts exactly as a runtime one (polyglot audit
# 2026-06-12, HOOK/SH-10).

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)" || exit 2
case "$FILE" in
    pyproject.toml | uv.lock) ;;
    *) exit 0 ;;
esac

{
    echo "=== DEPENDENCY CHANGE: $FILE modified ==="
    echo "New dependencies require developer approval (runtime AND dev: CI and the"
    echo "local venv install the dev group too). Confirm this package (or version bump)"
    echo "was discussed before proceeding."
    echo ""
    # hook_repo_root forks git, but its exit status is printf's and its own
    # || absorbs a git failure, so it
    # effectively always succeeds; the captured root only feeds a read-only
    # `git diff` whose output is informational before the unconditional exit 2.
    # shellcheck disable=SC2312
    git -C "$(hook_repo_root)" diff -- "$FILE" 2>/dev/null
} >&2
exit 2
