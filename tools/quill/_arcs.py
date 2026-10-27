"""A card's arc: the organization's issue field Arc, its one home (rulings ``balance:R-BAL349``,
``R-BAL354``), whose matching arc label quill keeps as display.  This module holds the field's
name, how a card's value is read, from a GraphQL read (:func:`arc_of`) and from GitHub's
answer to a REST write (:func:`arc_answered`), and the arc labels that display it
(:func:`arc_labels`), for the tracker's configuration (:mod:`setup_tracker`, which declares
the field), its reads (:mod:`_queries`), its cards and the create that files one
(:mod:`_tracker`): every read of a card reads its labels and Arc by one text
(:data:`LABELS_AND_ARC`) and one parse (:func:`labels_and_arc`).  And the one read of EVERY
card's labels and Arc (:func:`card_arcs`), with the one rule of what a card's arc labels and
Arc call for (:func:`arc_verdict`): what a person must fix in its Arc, which no label can,
else what puts its arc labels back to display it (:func:`label_corrections`, their one
producer).  ``quill sync``'s label pass writes and reports by it (:mod:`_relabel`), and
:mod:`setup_tracker` reports each card it finds anything for (views_spec s.3, s.6).

A leaf but for :mod:`_github`, itself a leaf: it imports no other module of quill's, so
:mod:`setup_tracker`, which every other module imports, can import it.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tools.ci.arcs import ARCS
from tools.quill._github import GitHub, counted, issue_listing

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
#: What every read of a card reads of its labels and Arc (``_queries.CARD_FIELDS`` and the
#: every-card :func:`listing`), parsed by :func:`labels_and_arc`: up to a hundred labels, a
#: read GitHub cut short refused by its count.
LABELS_AND_ARC = "  labels(first: 100) { totalCount nodes { name } }\n" + FIELD_VALUES


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


def labels_and_arc(node: dict) -> tuple[tuple[str, ...], str | None]:
    """A card's labels and Arc from an issue node read by :data:`LABELS_AND_ARC`, each
    connection read whole or refused (:func:`_github.counted`): the one parse of them, which
    every read of a card makes (``_tracker.card_from``, :func:`arc_card`)."""
    number = node["number"]
    return (tuple(label["name"] for label in counted(node["labels"], "labels", number)),
            arc_of(counted(node["issueFieldValues"], "issue field values", number)))


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
    producer, read by the one rule :func:`arc_verdict`, by which ``quill sync`` writes them
    (:mod:`_relabel`) and :mod:`setup_tracker` reports them."""
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


def _arc_fault(card: ArcCard, is_task: bool) -> str | None:
    """What a person must fix in ``card``'s Arc, which no label can, worded to follow its
    name; None when nothing.  A card but a task holding none, from which no label can be
    derived (views_spec s.3): its labels are named, as they say what its Arc should be.  One
    holding an Arc that is none of :data:`tools.ci.arcs.ARCS`: no arc label displays it, so
    no put-back would ever settle.  And a task (``is_task``) holding one: its arc is its
    card's (ruling ``balance:R-BAL386``), so a value of its own would be a second home of it
    (rule 14)."""
    if is_task:
        if card.arc is None:
            return None
        return (f"is a task, whose arc is its card's (R-BAL386), and holds the Arc "
                f"{card.arc!r}: clear it on the web")
    if card.arc is None:
        return (f"holds no Arc, its arc's one home (R-BAL349), so no arc label can display "
                f"it: set its Arc on the web (its labels: {list(card.labels)})")
    if card.arc not in ARCS:
        return (f"holds the Arc {card.arc!r}, none of quill's arcs ({', '.join(ARCS)}), so no "
                "arc label can display it: set its Arc on the web to one of them")
    return None


def arc_verdict(card: ArcCard, is_task: bool) -> tuple[str | None, tuple[str, ...],
                                                         tuple[str, ...]]:
    """The one rule of what ``card``'s arc labels and Arc call for (ruling ``R-BAL354``,
    views_spec s.3 and s.6), ``is_task`` saying it is a task: what a person must fix in its
    Arc, which no label can (:func:`_arc_fault`), with nothing to write, its labels kept as
    they may be the only record of what its Arc should be; else no fault, and the arc labels
    to add and to remove that put them back to display its Arc (:func:`label_corrections`),
    none when they do.  ``quill sync``'s label pass reports and writes by it
    (:mod:`_relabel`), and :mod:`setup_tracker` reports each card it finds anything for."""
    if fault := _arc_fault(card, is_task):
        return fault, (), ()
    return (None, *label_corrections(card.labels, card.arc))


#: How many cards one page of :func:`listing` holds.  Measured 2026-10-27 on the tracker,
#: 1,833 cards read whole with their type, labels and Arc, 100 a page: 19 pages in 18.3 s,
#: at most 1.19 s a page (``tracker_fix/build_1811/measure_listing_100.out``), inside
#: GitHub's limit of about ten seconds a request.  A card's every field
#: (``_queries.CARD_FIELDS``) is read 25 a page, as it was cut off at 100.
_PAGE = 100


def listing(owner: str, name: str) -> str:
    """The listing of every card of the repository ``owner/name``, open and closed, a page of
    :data:`_PAGE` at a time: each card's number, type, title, labels and Arc
    (:data:`LABELS_AND_ARC`), the lighter read spec item 4 asks for."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: %d, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes {
  number title issueType { name }
%s} } } }"""
        % (owner, name, _PAGE, LABELS_AND_ARC)
    )


def arc_card(node: dict) -> ArcCard:
    """A card from one node of :func:`listing`, its labels and Arc read whole or refused
    (:func:`labels_and_arc`)."""
    return ArcCard(node["number"], (node["issueType"] or {}).get("name"), node["title"],
                   *labels_and_arc(node))


def card_arcs(github: GitHub, owner: str, name: str) -> list[ArcCard]:
    """Every card of the repository ``owner/name``, open and closed, by :func:`listing`,
    in GitHub's order: the one read of every card's labels and Arc, which ``quill sync``'s
    label pass (``_tracker.Tracker.card_arcs``) and :mod:`setup_tracker`'s check both make.

    Raises:
        TrackerError: When a card's labels or field values are answered null or cut short
            (:func:`_github.counted`).
    """
    return [arc_card(node) for node in
            issue_listing(github, listing(owner, name), "every card's labels and Arc")]
