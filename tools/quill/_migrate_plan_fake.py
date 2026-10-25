"""The tracker states the migration plan's tests read (:mod:`_migrate_plan`), on the small
corpus (:mod:`_migrate_fake`) and the in-memory tracker (:mod:`_fake`): every item's card as
a finished filing leaves it, and what one run reads of a tracker.  Nothing here writes to a
tracker but the in-memory one it builds.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from tools.quill._fake import FakeTracker
from tools.quill._migrate_bodies import Built
from tools.quill._migrate_fake import complete_input
from tools.quill._migrate_plan import Plan, Tracked, plan
from tools.quill._migrate_source import Item, Registries, read_source
from tools.quill._tracker import Comment
from tools.quill.migrate import prepare
from tools.quill.setup_tracker import FILING

OUTCOME = "O1 the first"
#: The corpus's one milestone, as :func:`_migrate_fake.complete_input` names it.
MILESTONES = {OUTCOME: ("The first", "The first outcome.")}


def corpus_items(corpus: Registries) -> tuple[Item, ...]:
    """The corpus's items, in the source's order."""
    return read_source(corpus).items


def corpus_texts(corpus: Registries) -> dict[str, Built]:
    """Each item's card text as filed, as the input file :func:`_migrate_fake.complete_input`
    writes makes it (:func:`migrate.prepare`: built, then its numbers written)."""
    prepared = prepare(corpus, complete_input(corpus))
    assert not prepared.refusals and prepared.decisions.milestones == MILESTONES
    return dict(prepared.built)


def corpus_plan(corpus: Registries, tracker: FakeTracker,
                items: Sequence[Item] | None = None) -> Plan:
    """The plan for the corpus's items (or ``items``) on ``tracker`` as a run reads it."""
    return plan(corpus_items(corpus) if items is None else items, corpus_texts(corpus),
                MILESTONES, tracked(tracker))


def tracked(tracker: FakeTracker) -> Tracked:
    """What a run reads of ``tracker``."""
    return Tracked(tracker.all_cards(), tracker.milestones(),
                   tuple(number for number, _ in tracker.board.order()), tracker.app_login)


def filed_tracker(corpus: Registries, *, marked: Sequence[str] = (), skip: Sequence[str] = (),
                  milestone: bool = True, start: int = 2) -> tuple[FakeTracker, dict[str, int]]:
    """A tracker holding every item's card as a finished filing would leave it: numbered
    from ``start`` in the source's order (the numbers below it are the tracker's own cards',
    filed before the migration), titled, written and labelled as built, linked, on the
    board in the desired order, the scope step in its milestone, each question carrying its
    As filed comment, each ruling closed; the keys ``marked`` still carry the mark and
    ``skip`` have no card."""
    tracker = FakeTracker()
    if milestone:
        tracker.create_milestone(*MILESTONES[OUTCOME])
    texts = corpus_texts(corpus)
    items = [item for item in corpus_items(corpus) if item.key not in skip]
    numbers = {item.key: index + start for index, item in enumerate(items)}
    for item in items:
        number = numbers[item.key]
        tracker.add(number, item.kind, texts[item.key].body, on_board=False,
                    title=texts[item.key].title,
                    labels=tuple(sorted({*item.labels, *([FILING] if item.key in marked
                                                         else [])})),
                    is_open=item.kind != "ruling",
                    state_reason="COMPLETED" if item.kind == "ruling" else None,
                    parent=numbers.get(item.links.parent),
                    blocked_by=tuple(numbers[key] for key in item.links.blockers
                                     if key in numbers))
        if item.outcome is not None and milestone:
            tracker.milestone_of[number] = 1
        if item.kind == "question":
            tracker.notes[number] = [Comment(tracker.app_login, "t", texts[item.key].comment)]
    for item in sorted((item for item in items if item.links.place is not None),
                       key=lambda item: item.links.place):
        tracker.board.items.append(numbers[item.key])
    return tracker, numbers


def with_change(tracker: FakeTracker, number: int, **fields) -> None:
    """Card ``number`` of ``tracker`` with ``fields`` changed: the state a test reads."""
    tracker.cards_by_number[number] = replace(tracker.cards_by_number[number], **fields)
