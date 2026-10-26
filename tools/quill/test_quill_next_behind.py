"""``quill next`` says when ``sync`` is behind (ruling plan#1815, plan#1809 item 6): "sync
is behind: N shipped cards still open" while git says work shipped, or a task is done, that
the tracker still shows open; never after a ``sync``, and never for a card a person reopened,
which ``sync`` reports and does not close.  Over :class:`_fake.FakeTracker` and a throwaway
git repository (the ``code`` fixture, ``conftest.py``).  Nothing here calls GitHub.
"""
from __future__ import annotations

from tools.quill._fake import FakeTracker, finish, run, ship


def _next(tracker, code, capsys) -> list[str]:
    """What ``quill next`` prints, a line each; it exits 0."""
    capsys.readouterr()
    assert run(tracker, code, "next") == 0
    return capsys.readouterr().out.splitlines()


def test_shipped_cards_still_open_are_counted_until_sync_closes_them(code, capsys):
    """plan#2 and plan#3 shipped and are still shown open, plan#1 is still work: next offers
    plan#1 and says sync is behind by two; after a sync it says nothing of sync."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    ship(code, 2, 3)
    lines = _next(tracker, code, capsys)
    assert lines[0].startswith("next: plan#1 ")
    assert "sync is behind: 2 shipped cards still open" in lines
    assert run(tracker, code, "sync") == 0
    assert not any(line.startswith("sync is behind") for line in _next(tracker, code, capsys))


def test_a_card_sync_closed_that_the_listing_still_shows_open_is_not_counted(
        code, capsys, monkeypatch):
    """Review s56 L9: GitHub's open listing can lag a close, as ``sync``'s own reads say, so
    a next run just after a sync read plan#2 and plan#3 as still open.  Each counted card is
    read again by its number, which decides: nothing is said of sync."""
    tracker = FakeTracker()
    for number in (1, 2, 3):
        tracker.add(number)
    ship(code, 2, 3)
    lagging = tracker.open_cards()
    assert run(tracker, code, "sync") == 0
    monkeypatch.setattr(tracker, "open_cards", lambda: lagging)
    assert not any(line.startswith("sync is behind") for line in _next(tracker, code, capsys))


def test_a_card_a_person_reopened_is_not_counted(code, capsys):
    """plan#2 shipped and a PERSON reopened it: sync reports it and never closes it, so next
    never counts it, however many syncs run."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2, state_reason="REOPENED", touched_by_hand=True)
    ship(code, 2)
    assert not any(line.startswith("sync is behind") for line in _next(tracker, code, capsys))
    assert run(tracker, code, "sync") == 1
    assert tracker.cards_by_number[2].is_open
    assert not any(line.startswith("sync is behind") for line in _next(tracker, code, capsys))


def test_a_done_task_still_open_is_counted(code, capsys):
    """Task plan#3 under plan#1 has its file on dev, and is still shown open: sync closes it,
    so next counts it."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add_task(3, 1)
    finish(code, 3)
    assert "sync is behind: 1 shipped cards still open" in _next(tracker, code, capsys)


def test_nothing_is_said_of_sync_when_it_is_not_behind(code, capsys):
    """No card git calls shipped is open: next says nothing of sync."""
    tracker = FakeTracker()
    tracker.add(1)
    assert _next(tracker, code, capsys) == [f"next: {_label_of(tracker, 1)}"]


def _label_of(tracker, number) -> str:
    """How next names card ``number`` of ``tracker``."""
    card = tracker.cards_by_number[number]
    return f"plan#{number} [{card.kind}, balance] {card.title}"
