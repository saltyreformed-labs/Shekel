"""``spec-history`` and ``spec-revert``: a card's body as each saved edit left it, for the review
R-BAL174 requires of every spec change since a branch's work started, and the restore of an
earlier version by hand.

Split out of the command module (plan#3797), which ``scan`` took past the 1,000 lines
``too-many-lines`` allows (plan#3544).
"""
from __future__ import annotations

import difflib
from datetime import UTC, datetime
from pathlib import Path

from tools.quill import _git
from tools.quill._command import _one
from tools.quill._exits import Refused
from tools.quill._tracker import Tracker


def _since(root: Path, text: str) -> datetime:
    """``--since``: an ISO date or time, or a branch (when its work started,
    :func:`_git.started`)."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(_git.started(root, text))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _diff(before: str, after: str, label: str) -> str:
    """A unified diff of two bodies."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        "before", label, lineterm="\n",
    ))


def cmd_spec_history(args, tracker: Tracker, root: Path) -> int:
    """Every saved change to a card's body in a window, for the review R-BAL174 requires."""
    _one(tracker, args.card)
    _, versions = tracker.edits(args.card)
    since = _since(root, args.since) if args.since else None
    shown = 0
    for index, version in enumerate(versions):
        if since and datetime.fromisoformat(version.edited_at) < since:
            continue
        before = versions[index - 1].body if index else ""
        what = "filed" if index == 0 else f"edit {version.edit_id}"
        print(f"== {what}, {version.edited_at} by {version.editor}")
        print(_diff(before, version.body, version.edited_at) or "(no change to the text)")
        shown += 1
    if not shown:
        last = versions[-1]
        print(f"plan#{args.card}'s body has not changed since {args.since} (last saved "
              f"{last.edited_at} by {last.editor})")
    return 0


def cmd_spec_revert(args, tracker: Tracker, _root: Path) -> int:
    """Restore a card's body to an earlier saved version, by hand.

    The body is read again just before the write, and the write is refused if
    it changed since the history was read: the diff printed is then still the
    change made.  (GitHub offers no conditional issue write, so a change in the
    instant between that read and the write is not seen.)
    """
    _one(tracker, args.card)
    body, versions = tracker.edits(args.card)
    chosen = [version for version in versions if version.edit_id == args.to]
    if not chosen:
        raise Refused(f"plan#{args.card} has no saved version {args.to!r}; "
                      f"`quill spec-history plan#{args.card}` lists them")
    if chosen[0].body == body:
        raise Refused(f"plan#{args.card}'s body already is that version")
    print(_diff(body, chosen[0].body, f"version {args.to}"))
    if tracker.body(args.card) != body:
        raise Refused(f"plan#{args.card} changed while its history was read; nothing was "
                      "written: run it again")
    tracker.set_body(args.card, chosen[0].body)
    print(f"restored plan#{args.card}'s body to the version saved {chosen[0].edited_at} "
          f"by {chosen[0].editor}")
    return 0
