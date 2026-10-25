"""The ``quill export`` command: the plan's tracker backed up whole, one file a day.

Ruling ``balance:R-BAL328`` (item 3): a daily backup of the cards runs from the cutover's
first day -- one export of every card, edit and comment per day, to the disk restic backs
up.  ``quill export --to DIR`` writes ``DIR/shekel-plan-<date>.json``, the date the export
began in UTC; run again the same day, its file replaces that day's.  It is meant to run
daily from a timer on the host, whose unit files live with the host's other timers, not
in this repository.

**What a file holds.**  The cards and milestones as GitHub answered them, so no reader's
idea of a card stands between the backup and the tracker; the board as quill reads it:

- ``cards``: every card, open and closed, in number order (:func:`_queries.export_cards`):
  what quill reads of a card, its text, who filed it and when, when it closed, its
  milestone's number, every saved version of its text, and every comment, with every
  saved version of its own when it was edited.  Its saved versions and its comments are
  each kept as ``{"nodes": [...]}``, every node, read to the end
  (:func:`_github.paged`).
- ``milestones``: every milestone, open and closed, whole (:func:`_queries.milestones`).
- ``board``: the tracker's cards on the board, in the developer's drag order (R-BAL177),
  which no card holds (:meth:`_tracker.Board.order`).
- ``tracker`` and ``taken_at``: whose cards, and when the export began.

**What it does not hold:** a card's timeline -- its title's renames, its labels and its
state over time -- which each day's file shows as its difference from the day before's;
a label's colour and description, which :mod:`setup_tracker` states in code; an item on
the board that is not one of the tracker's cards; and the claims, which are no longer the
tracker's: since the ship marker (ruling ``balance:R-BAL359``, X-cx's M1) a claim is the
code repository's branch ``card/N``, which git and its backups hold.

**A file under a day's name is always whole.**  It is written under a hidden temporary
name beside it, synced, then renamed into place.  The temporary file is made before
anything is read, so a directory that cannot be written fails at once; a run that fails
before the rename leaves no file and every earlier day's as it was, and a run killed by
a signal mid-write leaves its hidden ``.partial`` file, never a file under a day's name.
Any failed read -- GitHub, the network, or a card or connection GitHub answers cut short
or null (:func:`_tracker.card_from`, :func:`_tracker.connection_nodes`) -- and any
failed write end the command with exit status 2 (``quill.py``'s module docstring): one
bad read costs that night's file, and the timer's alert says so.  The file is its
owner's to read only: the tracker is private, and its cards may hold real figures
(R-BAL172).

It reads through quill's App, as every quill command does, and writes nothing to the
tracker.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from tools.quill import _queries
from tools.quill._github import GitHub
from tools.quill._tracker import (
    Tracker,
    card_from,
    comment_nodes,
    comment_versions,
    connection_nodes,
    listing,
    milestone_nodes,
    saved_versions,
)
from tools.quill.setup_tracker import Place


def _to_the_end(connection: dict | None, what: str, number: int,
                read_all: Callable[[], list[dict]]) -> dict:
    """``connection``, card ``number``'s ``what``, as a file keeps it: every node, read
    again from its start by ``read_all`` when GitHub says more follow the first page.

    Raises:
        TrackerError: When GitHub answers the connection, or a node of it, as null
            (:func:`_tracker.connection_nodes`).
    """
    nodes = connection_nodes(connection, what, number)
    if connection["pageInfo"]["hasNextPage"]:
        nodes = connection_nodes({"nodes": read_all()}, what, number)
    return {"nodes": nodes}


def _with_versions(github: GitHub, comment: dict) -> dict:
    """``comment`` and, when it was ever edited, every saved version of it, as
    ``userContentEdits``."""
    if comment["lastEditedAt"] is None:
        return comment
    return {**comment, "userContentEdits": {"nodes": comment_versions(github, comment["id"])}}


def _whole(github: GitHub, place: Place, card: dict) -> dict:
    """``card``, one issue node of :func:`_queries.export_cards`, with every saved version
    of its text and every comment, each comment with its own saved versions."""
    number = card["number"]
    comments = _to_the_end(card["comments"], "comments", number, partial(
        comment_nodes, github, place, number, _queries.EXPORT_COMMENT_FIELDS))
    return {
        **card,
        "userContentEdits": _to_the_end(card["userContentEdits"], "saved versions", number,
                                        lambda: saved_versions(github, place, number)[0]),
        "comments": {"nodes": [_with_versions(github, comment)
                               for comment in comments["nodes"]]},
    }


def snapshot(tracker: Tracker, taken_at: datetime) -> dict:
    """Every card of ``tracker`` whole, its milestones and its board's order, as one file
    holds them (the module docstring), the export having begun at
    ``taken_at``.

    Raises:
        TrackerError: When GitHub answers a card cut short or null
            (:func:`_tracker.card_from`, :func:`_to_the_end`).
    """
    github, place = tracker.github, tracker.place
    cards = listing(github, _queries.export_cards(place))
    for card in cards:
        card_from(card, place, tracker.board_id, tracker.app_login)
    return {
        "tracker": place.full_name,
        "taken_at": taken_at.astimezone(UTC).isoformat(),
        "cards": sorted((_whole(github, place, card) for card in cards),
                        key=lambda card: card["number"]),
        "milestones": sorted(milestone_nodes(github, place),
                             key=lambda milestone: milestone["number"]),
        "board": [number for number, _ in tracker.board.order()],
    }


def write(directory: Path, place: Place, taken_at: datetime,
          read: Callable[[], dict]) -> tuple[Path, dict]:
    """Write what ``read`` returns (:func:`snapshot`) as
    ``directory/<place's name>-<UTC date>.json``, its keys sorted so one day's file differs
    from the next only where the tracker did: under a temporary name beside it that no
    other run shares (``tempfile.mkstemp``, readable by its owner only), made BEFORE
    ``read`` is called, then synced, renamed into place, and the directory synced.  The
    temporary file is removed on any failure.  The path, and what was written."""
    path = directory / f"{place.name}-{taken_at.astimezone(UTC).date().isoformat()}.json"
    handle, name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".partial")
    temporary, written = Path(name), False
    try:
        with open(handle, "w", encoding="utf-8") as out:
            held = read()
            out.write(json.dumps(held, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        written = True
    finally:
        if not written:
            temporary.unlink(missing_ok=True)
    folder = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    return path, held


def cmd_export(args, tracker: Tracker, _root: Path,
               clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Back the tracker up whole to ``args.to`` (the module docstring)."""
    taken_at = clock()
    path, held = write(args.to, tracker.place, taken_at, partial(snapshot, tracker, taken_at))
    print(f"wrote {path}: {len(held['cards'])} cards, {len(held['milestones'])} milestones, "
          f"{len(held['board'])} on the board")
    return 0
