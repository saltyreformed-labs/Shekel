#!/usr/bin/env bash
# SessionStart: print the multi-session context every Shekel session needs
# before its first action -- where it stands, who coordinates merges, and where
# the next step comes from.
#
# It used to report the shared suite slot too.  ``balance:X-br-4`` deleted the
# slot: ``scripts/test.sh`` gives every run a private cluster, so there is no
# shared postmaster left for two runs to serialise against.
#
# stdout on exit 0 is added to the session's context (the SessionStart hook
# contract). A status hook must never block a session, so every probe fails
# soft and the script always exits 0. Deliberately reads $PWD, not
# $CLAUDE_PROJECT_DIR: the latter resolves to the PRIMARY checkout whatever
# worktree the session runs in (measured by two sessions, 2026-08-29), and
# this hook reports the session's OWN checkout.
#
# **IT ALSO REPORTS A RELEASE PIN, and that is why** (developer ruling
# 2026-09-05, after it cost two sessions in one evening).  A constraint that
# lives only in coordinator messages is INVISIBLE to any session that clears
# its context, and clearing context is ordinary.  On 2026-09-05 the shared
# main checkout was pinned to a release branch: one session cleared, looked at
# the tree, and concluded IT was driving the release -- because every fact the
# tree offers is about the CHECKOUT (its branch, its HEAD, its slot label) and
# none is about the session reading them.  A second session could not learn
# why its browser pass was impossible, the same pin being the cause.  Neither
# could have found out by looking, and one of them is worktree-isolated and
# could not look at all.  The pin is read off `git worktree list`, so it
# cannot go stale and nobody has to remember to write it.  (A STATED half, a
# notice board the coordinator wrote, went with the planning registries at the
# tracker switch: ruling `balance:R-BAL347`.)
#
# The next step is the tracker's answer, `python -m tools.quill.quill next`, and
# this hook only points at it: reading the tracker needs the network and the
# App's credentials, and a status hook must never wait on either.

set -u

branch="$(git -C "$PWD" rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="not a git checkout"
toplevel="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null)" || toplevel="$PWD"

echo "Shekel session context (SessionStart hook):"
echo "- checkout: ${toplevel}, branch: ${branch}"
echo "- coordination doctrine: CLAUDE.md, Multi-session operation (stated once there, not here)"

# DERIVED: is the SHARED MAIN checkout pinned to a release branch?  Read from
# `git worktree list`, which answers for every worktree from inside any one of
# them -- no cross-directory git call, which a worktree-isolated session's
# harness would refuse anyway.  The main worktree is the FIRST entry.
worktrees="$(git -C "$PWD" worktree list 2>/dev/null)" || worktrees=""
main_line="$(printf '%s\n' "${worktrees}" | head -n 1)" || main_line=""
if [ -n "${main_line}" ]; then
    main_path="${main_line%% *}"
    main_branch="${main_line##*[}"
    main_branch="${main_branch%]*}"
    case "${main_branch}" in
        release/*)
            echo "- STANDING: the shared main checkout ${main_path} is on"
            echo "    ${main_branch} for a release. Do NOT check out, branch from, or"
            echo "    merge into it. Anything bind-mounting that path follows its branch."
            ;;
    esac
fi

echo "- next step: the tracker's, by python -m tools.quill.quill next (not read here)"

exit 0
