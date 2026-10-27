"""A card's arc: the organization's issue field Arc, its one home (rulings ``balance:R-BAL349``,
``R-BAL354``), whose matching arc label quill keeps as display.  This module holds the field's
name, how a card's value is read, from a GraphQL read (:func:`arc_of`) and from GitHub's
answer to a REST write (:func:`arc_answered`), and the arc labels that display it
(:func:`arc_labels`), for the tracker's configuration (:mod:`setup_tracker`, which declares
the field), its reads (:mod:`_queries`), its cards and the create that files one
(:mod:`_tracker`).  And the one read of EVERY card's labels and Arc (:func:`card_arcs`), with
the one producer of what puts a card's arc labels back to display its Arc
(:func:`label_corrections`): ``quill sync``'s label pass writes them (:mod:`_relabel`), and
:mod:`setup_tracker` reports each card that needs any (views_spec s.3, s.6).

A leaf but for :mod:`_github`, itself a leaf: it imports no other module of quill's, so
:mod:`setup_tracker`, which every other module imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial

from tools.ci.arcs import ARCS
from tools.quill._github import GitHub, counted, paged

#: The organization's issue field holding a card's arc, a single select whose options are the
#: arcs (``setup_tracker.ISSUE_FIELDS``).
ARC_FIELD = "Arc"

#: What quill reads of a card's issue fields: how many it holds, and each single select's
#: value with its field's name.  The organization declares five fields
#: (``setup_tracker.ISSUE_FIELDS``), so ten leave room, and a read GitHub cut short is refused
#: by its count (``_tracker.card_from``).  A value of another type answers ``{}``: GitHub
#: answers an inline fragment that does not match so.
FIELD_VALUES = """\
  issueFieldValues(first: 10) { totalCount nodes { ... on IssueFieldSingleSelectValue {
    name field { ... on IssueFieldSingleSelect { name } } } } }
"""


def arc_of(values: Iterable[dict]) -> str | None:
    """A card's arc: the value of its :data:`ARC_FIELD` among ``values``, its field values as
    read by :data:`FIELD_VALUES`; None when it holds none (measured 2026-10-27 on plan#1811: a
    value is ``{"name": "balance", "field": {"name": "Arc"}}``)."""
    return next((value["name"] for value in values
                 if (value.get("field") or {}).get("name") == ARC_FIELD), None)


def arc_answered(values: Iterable[dict]) -> str | None:
    """A card's arc from GitHub's answer to a REST write of it, its ``issue_field_values``:
    the option named in the value of its :data:`ARC_FIELD`; None when it holds none
    (measured 2026-10-27 on plan#1811: a value is ``{"issue_field_name": "Arc",
    "single_select_option": {"name": "balance"}, ...}``, and GitHub's REST description
    answers an issue's create in the shape of its read)."""
    return next((value["single_select_option"]["name"] for value in values
                 if value.get("issue_field_name") == ARC_FIELD), None)


def arc_labels(arc: str | None) -> tuple[str, ...]:
    """The arc labels a card holding ``arc`` carries, as display of its Arc (ruling
    ``R-BAL354``): its arc's own, and none when it holds none, as a task does.  The one
    producer of a card's arc labels, which the create that files a card writes
    (:func:`_tracker.filed_labels`)."""
    return () if arc is None else (arc,)


def other_labels(labels: Iterable[str]) -> tuple[str, ...]:
    """``labels`` but the arc labels, which display a card's Arc and decide nothing
    (R-BAL354): what a filing compares and writes beside its Arc."""
    return tuple(label for label in labels if label not in ARCS)


def label_corrections(labels: Iterable[str], arc: str | None) -> tuple[tuple[str, ...],
                                                                         tuple[str, ...]]:
    """What puts the arc labels of a card holding ``labels`` and the Arc ``arc`` back to
    display it (ruling ``R-BAL354``): the arc labels to add, :func:`arc_labels`' that it
    lacks, and the ones to remove, each it holds that those are not; its other labels
    (:func:`other_labels`) are kept.  Neither, when they display it already.  The one
    producer, which ``quill sync`` writes (:mod:`_relabel`) and :mod:`setup_tracker`
    reports."""
    labels = tuple(labels)
    kept, shown = other_labels(labels), arc_labels(arc)
    held = tuple(label for label in labels if label not in kept)
    return (tuple(label for label in shown if label not in held),
            tuple(label for label in held if label not in shown))


@dataclass(frozen=True)
class ArcCard:
    """A card as the every-card listing reads it (:func:`card_arcs`): its number, type
    (``kind``, None for an issue with none), title, labels and Arc (None when it holds
    none), and nothing more."""

    number: int
    kind: str | None
    title: str
    labels: tuple[str, ...]
    arc: str | None


#: How many cards one page of :func:`listing` holds.  Measured 2026-10-27 on the tracker,
#: 1,833 cards read whole with their type, labels and Arc, 100 a page: 19 pages in 18.3 s,
#: at most 1.19 s a page (``tracker_fix/build_1811/measure_listing_100.out``), inside
#: GitHub's limit of about ten seconds a request.  A card's every field
#: (``_queries.CARD_FIELDS``) is read 25 a page, as it was cut off at 100.
_PAGE = 100


def listing(owner: str, name: str) -> str:
    """The listing of every card of the repository ``owner/name``, open and closed, a page of
    :data:`_PAGE` at a time: each card's number, type, title, labels and Arc
    (:data:`FIELD_VALUES`), the lighter read spec item 4 asks for."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: %d, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes {
  number title issueType { name }
  labels(first: 100) { totalCount nodes { name } }
%s} } } }"""
        % (owner, name, _PAGE, FIELD_VALUES)
    )


def arc_card(node: dict) -> ArcCard:
    """A card from one node of :func:`listing`, each connection read whole or refused
    (:func:`_github.counted`)."""
    number = node["number"]
    return ArcCard(number, (node["issueType"] or {}).get("name"), node["title"],
                   tuple(label["name"] for label in counted(node["labels"], "labels", number)),
                   arc_of(counted(node["issueFieldValues"], "issue field values", number)))


def card_arcs(github: GitHub, owner: str, name: str) -> list[ArcCard]:
    """Every card of the repository ``owner/name``, open and closed, by :func:`listing`,
    in GitHub's order: the one read of every card's labels and Arc, which ``quill sync``'s
    label pass (``_tracker.Tracker.card_arcs``) and :mod:`setup_tracker`'s check both make.

    Raises:
        TrackerError: When a card's labels or field values are answered null or cut short
            (:func:`_github.counted`).
    """
    nodes, _ = paged(partial(github.graphql, listing(owner, name)),
                     lambda answer: answer["repository"]["issues"], "every card's labels and Arc")
    return [arc_card(node) for node in nodes]
