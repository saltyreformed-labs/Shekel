#!/usr/bin/env bash
# Shared helpers for the Claude Code hooks in this directory.
# SOURCE this file (". _hooklib.sh"); do not execute it.
#
# The harness delivers tool data as a single JSON object on the hook's stdin.
# Verified empirically for this environment (claude_code 2.1.x remote): the
# edited file arrives as tool_input.file_path, an ABSOLUTE path, and there is NO
# TOOL_INPUT_PATH environment variable. The previous "$TOOL_INPUT_PATH" argument
# convention therefore passed an empty string, which made every per-edit hook a
# silent no-op. These helpers parse the payload the documented way.
#
# FAIL-CLOSED CONTRACT (polyglot audit 2026-06-12, findings HOOK/SH-02 and
# HOOK/SH-03 in docs/audits/polyglot-cleanup/findings.md): a gate that cannot
# see its target must not pass it. The original helper returned empty on ANY
# internal failure (malformed payload, python3 missing, payload-shape change)
# and every caller treated empty as "not my file" -- so all four per-edit gates
# failed OPEN on infrastructure error, the same failure mode the header above
# documents already happening once in the TOOL_INPUT_PATH era. Now an
# infrastructure failure returns 1 after stderr diagnostics, and callers exit 2.

# Echo the project root with no trailing slash -- the single authority for
# repo-root resolution across every hook (finding HOOK/SH-12: this was
# previously spelled three different ways across three scripts, and a
# trailing slash in CLAUDE_PROJECT_DIR silently defeated the relpath strip).
#
# **$PWD FIRST, and CLAUDE_PROJECT_DIR only as a fallback.** That order is the
# reverse of what stood here, and the reversal is MEASURED rather than tidy.
# ``CLAUDE_PROJECT_DIR`` is not the tree a session edits. Claude Code's hooks
# reference (read 2026-10-27) calls it "the project root where the session
# started", and says it "stays put" when the session then enters a worktree,
# while the hook's ``cwd`` follows the session. Both measurements agree:
# sessions working in worktrees on 2026-08-29 and 2026-09-04 got the PRIMARY
# checkout, and a session LAUNCHED in a worktree on 2026-10-26 (Claude Code
# 2.1.310) got that worktree. Either way the tree being edited is the one the
# hook runs in. ``session-start.sh`` read ``$PWD`` from
# its first commit, and that knowledge was never carried into this shared
# helper, so every other hook inherited the bug:
#
#   * ``hook_target_relpath`` normalized each edited file against the primary
#     root. A worktree file is not under it, so the function returned an
#     ABSOLUTE path and every caller's ``case`` pattern missed -- silently
#     SKIPPING the gate. The comment below still calls that outcome "correct",
#     which it is for a file genuinely outside the project and is not for a
#     worktree file. Five files sourced this helper then --
#     ``post-edit-python.sh``, ``post-edit-template.sh``, ``post-edit-deps.sh``,
#     ``stop-check.sh`` and ``guard-migrations.sh`` (deleted 2026-10-11: the
#     developer ruled that a migration edit needs no approval), whose
#     developer-approval prompt on a hand-edited ``migrations/versions/*.py``
#     was therefore also inoperative in every worktree -- and, inside this
#     file, the ``_hook_venv_bin`` prefix below was a further consumer asking
#     a different question.
#   * ``stop-check.sh`` cd'd to the primary checkout and linted a tree the
#     session was not editing -- reporting another lane's uncommitted work as
#     this lane's failure, and passing this lane's real one.
#
# Measured 2026-09-04 in ``~/projects/shekel-xauf``: ``Decimal(0.1)`` written
# into ``app/`` through BOTH Write and Edit was not blocked, though pylint
# reports W9901 (``shekel-decimal-from-float``) and exits 4 on that file. On the
# same night a Stop hook in that worktree reported E1120s and unused imports
# that existed only in a peer's checkout. So all four per-edit gates and the
# Stop floor were inoperative for every session working in a worktree, which
# under this project's Multi-session doctrine is every session. CI and
# pre-commit were unaffected throughout, which is why nothing reached ``main``.
#
# ``git rev-parse`` rather than a string strip: it answers for the WORKTREE the
# session is in, which is the question, and it fails cleanly to the old
# behaviour outside a checkout.
hook_repo_root() {
    local root
    root="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" \
        || root="${CLAUDE_PROJECT_DIR:-$PWD}"
    printf '%s\n' "${root%/}"
}

# Echo the MAIN worktree of the repository that holds directory $1, or
# nothing outside a repository: the one reading of it every hook uses.
# `git worktree list` names the main worktree FIRST from inside any worktree
# of the repository; the porcelain form keeps a path with spaces whole.
hook_main_checkout() {
    # shellcheck disable=SC2312 # a failing git (not a repository) prints nothing, and every caller tests for the empty value.
    git -C "$1" worktree list --porcelain 2>/dev/null \
        | sed -n '1s/^worktree //p'
}

# The directory this library sits in: inside a checkout of this repository
# whatever directory the session is working in, since every hook sources it by
# its own path.
_hooklib_dir="${BASH_SOURCE[0]%/*}"

# Echo the checkout that holds the TOOLCHAIN: the repository's MAIN worktree.
# Deliberately NOT hook_repo_root: "which tree is being edited" and "where
# does pylint live" are two questions, and one function answering both is
# what made the worktree fix above a regression before review caught it
# (2026-09-04).
#
# **Worktrees borrow the main checkout's venv and have none of their own.**
# Measured 2026-09-04 across the eight worktrees on this machine: a real .venv
# in the primary, a SYMLINK to it in one worktree, and nothing in the other
# six. .gitignore ignores .venv, so a worktree never acquires one by checkout;
# `tools.quill.lane` links one into each lane it makes (plan#1824). A linked
# .venv borrows the main checkout's too; it is not an exception.
#
# **So the answer is git's, never the launch directory's** (ruling plan#1848,
# finding plan#1849). Until 2026-10-27 this answered CLAUDE_PROJECT_DIR, on
# the belief that it is always the primary checkout. It is the directory a
# session was launched in (above), so a session launched in a worktree with
# no .venv link got no PATH prefix below, and from a terminal without the venv
# the two gates that invoke pylint, the Python per-edit gate
# (post-edit-python.sh) and the Stop floor (stop-check.sh), ran whatever
# pylint PATH held. Measured 2026-10-27: the system's unpinned
# /usr/bin/pylint crashed (F0002, an astroid mismatch) on an untouched
# app/__init__.py, and the per-edit gate blocked the edit while blaming a
# code rule. (On 2026-09-04 no pylint existed outside the venv, so the same
# miss would have read "command not found", with the same block.)
#
# Git is asked from this library's own directory, not the session's: a
# session whose working directory is outside any repository still sources the
# library from a checkout. Only a library outside any checkout (a copy with no
# .git) falls back to the old answer, and there is no venv beside it to find.
hook_toolchain_root() {
    local root
    root="$(hook_main_checkout "${_hooklib_dir}")"
    [ -n "$root" ] || root="${CLAUDE_PROJECT_DIR:-$PWD}"
    printf '%s\n' "${root%/}"
}

# Put the repo venv's bin first on PATH so every hook resolves the PINNED
# toolchain (pylint et al. from uv.lock), not whatever the launching
# shell happens to expose. Observed 2026-07-08: a session started without the
# venv on PATH made bare `pylint` unresolvable inside the hooks, so the
# fail-closed gates blocked every Python edit on infrastructure error rather
# than on a finding. Guarded so a missing venv (e.g. CI, which installs the
# toolchain globally) leaves PATH untouched.
_hook_venv_bin="$(hook_toolchain_root)/.venv/bin"
if [ -d "$_hook_venv_bin" ]; then
    PATH="$_hook_venv_bin:$PATH"
fi

# Echo the edited file's path normalized to repo-relative (e.g. app/foo.py).
# Consumes stdin, so call it at most once per hook invocation.
#
# Outcomes:
#   * return 0, repo-relative output -- normal case; callers match on app/ etc.
#   * return 0, ABSOLUTE output      -- file lies outside the project; callers'
#     case patterns will not match and the hook skips, which is correct.
#   * return 1 after stderr output   -- infrastructure failure (malformed JSON,
#     python3 unavailable, or a Write|Edit|MultiEdit payload with no
#     tool_input.file_path, which the tool schemas guarantee cannot happen
#     legitimately). Callers MUST exit 2: fail closed, never silently open.
#
# realpath-based normalization makes the result independent of trailing
# slashes, symlinked invocation paths, and the hook's working directory
# (finding HOOK/SH-03: the old exact-prefix strip returned an absolute path --
# and therefore silently skipped every check -- when CLAUDE_PROJECT_DIR carried
# a trailing slash; verified empirically before the fix).
hook_target_relpath() {
    local out root
    root="$(hook_repo_root)"
    if ! out="$(python3 -c '
import json
import os
import sys

root = os.path.realpath(sys.argv[1])
try:
    payload = json.load(sys.stdin)
except ValueError as exc:
    print(f"hook payload is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)
path = payload.get("tool_input", {}).get("file_path", "")
if not path:
    print(
        "hook payload carries no tool_input.file_path "
        "(matcher and payload shape disagree -- harness contract change?)",
        file=sys.stderr,
    )
    sys.exit(1)
real = os.path.realpath(path)
rel = os.path.relpath(real, root)
print(path if rel.startswith("..") else rel)
' "$root")"; then
        {
            echo "hook infrastructure error: could not resolve the edited file from the"
            echo "stdin payload (diagnostics above). Failing CLOSED -- the edit is blocked"
            echo "until the hook plumbing is fixed (docs/coding-standards.md: gates fail"
            echo "with a clear message, never with silent defaults)."
        } >&2
        return 1
    fi
    printf '%s\n' "$out"
}
