"""Quill's one public door: each name another package reads from quill's private modules.

``shekel-private-module-import`` refuses another package's import of a private module,
so a name another package must read is re-exported here, and this module holds no logic
and no name of its own (the shape of ``tools/plan_gate/registries.py``, ruling
``balance:R-BAL244``; this door decided by the coordinator under ruling
``balance:R-BAL207``, 2026-10-13, as ``plan_labels``, and renamed when the release
tool's card reader joined it, 2026-10-14).  ``quill.py`` is a public module and is
imported directly, never through here.

Each name, and who reads it:

- ``step_labels``: ``tools/release``'s pre-cutover arm (``_carried``), for each step row
  it reads: its money flag and whether it is a deploy-together container, by the ONE
  producer the migration writes a step card's labels with (CLAUDE.md rule 14).  The
  cutover (L8) deletes this name with ``_migrate_*.py`` and that arm.
- ``Tracker``: the type of the tracker ``tools/release`` reads for a cut after the
  cutover (``_plan``'s and ``release``'s connection to it, ``_cards``' reads of it); the
  connection itself is ``quill.CONNECT``.  ``Card``: the type of a card it reads
  (``_cards``' walk up, ``_line_up``).
- ``with_closure``: ``tools/release._cards``, for the cards a release's markers name and
  every card a decision over them reads, by quill's one closure read.
- ``leaves_below`` and ``dropped``: ``tools/release._cards``, for a deploy-together
  group's leaves by quill's own leaf rule, less each one dropped.
- ``above`` and ``dropped_above``: ``tools/release._cards``, for the deploy-together
  groups a card a release names is under, by quill's one walk up the steps a card splits,
  and for a leaf no step above which was dropped (R-BAL185: a person's close of a split
  step is a drop its leaves inherit, never copied onto them).
- ``owner`` and ``OWNED``: ``tools/release._cards``, for the groups a finding a release
  names is under, its owner's: the one step it is blocked by, never a parent (ruling
  ``balance:R-BAL395``), by quill's one spelling of a finding's or a ruling's owner.
- ``SHIPPABLE``: ``tools/release._cards``, which refuses a marker naming a card of a
  kind no commit ships (a ruling, a question, a card of no type), by quill's one list
  of the kinds a commit ships.
- ``MARKERS``, ``card_of``, ``markers_in``, ``merged_tree``, ``MarkerConflict``,
  ``changes`` and ``marker_changes``: ``tools/release._cards``, for what a release changes
  in the ship markers (ruling ``balance:R-BAL359``; step X-cx's M3): the tree a release of
  a cut into ``main`` leaves production running, and the error saying it conflicts on a
  marker (an earlier cut offered in the release's place is refused for one, never the
  whole release judged on nothing: M3's review, its 1), the cards a tree's markers name,
  the entries under :data:`MARKERS` that differ between two trees, and the commits of a
  range that changed each marker, by quill's one reading of each.
- ``GitHubError``, ``TrackerError`` and ``CredentialsError``: ``tools/release.release``,
  which reports a failed read of the tracker, or App credentials it cannot use, as a
  release judged on nothing.
- ``FakeTracker`` and ``OutsideLink``: ``tools/release``'s tests, which drive the card
  reader against quill's own in-memory tracker, a card's link to an issue outside the
  tracker built as quill's read builds one.  The fake's cost, accepted so quill keeps ONE
  door: every importer of this module also loads quill's fake (``_fake``, which imports
  ``quill`` and ``tools.ci.scratch``); nothing calls it outside the tests.
"""
from __future__ import annotations

from tools.quill._command import with_closure
from tools.quill._fake import FakeTracker
from tools.quill._git import (
    MARKERS,
    MarkerConflict,
    card_of,
    changes,
    marker_changes,
    markers_in,
    merged_tree,
)
from tools.quill._github import CredentialsError, GitHubError
from tools.quill._migrate_source import step_labels
from tools.quill._state import (
    OWNED,
    SHIPPABLE,
    above,
    dropped,
    dropped_above,
    leaves_below,
    owner,
)
from tools.quill._tracker import Card, OutsideLink, Tracker, TrackerError

__all__ = [
    "MARKERS",
    "SHIPPABLE",
    "Card",
    "CredentialsError",
    "FakeTracker",
    "GitHubError",
    "MarkerConflict",
    "OWNED",
    "OutsideLink",
    "Tracker",
    "TrackerError",
    "above",
    "card_of",
    "changes",
    "dropped",
    "dropped_above",
    "leaves_below",
    "marker_changes",
    "markers_in",
    "merged_tree",
    "owner",
    "step_labels",
    "with_closure",
]
