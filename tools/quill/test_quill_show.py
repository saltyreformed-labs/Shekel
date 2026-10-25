"""The ``quill show`` command (``_show``): a card's facts and the title of each card linked
to it -- for a step, each finding and ruling it owns (ruling ``balance:R-BAL395``) -- then
its text, its comments and the history of its ship marker, over the in-memory tracker
(:class:`_fake.FakeTracker`) and the ``code`` fixture's real git.  Split out of
``test_quill.py``, which they would take past the 1,000 lines ``too-many-lines`` allows.
"""
from __future__ import annotations

import pytest

from tools.quill._fake import FakeTracker, claim, run, ship, unship
from tools.quill._tracker import Child, Comment
from tools.quill.setup_tracker import FILING


def test_show_resolves_an_old_id_by_its_title_alias(code, capsys):
    """``R-BAL80`` finds the card titled ``[R-BAL80] ...``; two arcs' same id need the arc."""
    tracker = FakeTracker()
    tracker.add(1, "ruling", title="[R-GU] one", labels=("balance",), is_open=False)
    tracker.add(2, "ruling", title="[R-GU] two", labels=("bank_import",), is_open=False)
    tracker.add(3, "ruling", title="[R-GUX] other", labels=("balance",), is_open=False)
    assert run(tracker, code, "show", "R-GU") == 1
    assert "names 2 cards" in capsys.readouterr().err
    assert run(tracker, code, "show", "bank_import:R-GU") == 0
    assert capsys.readouterr().out.startswith("plan#2 [ruling, bank_import] [R-GU] two")


def test_show_prints_the_claim_and_the_markers_history(code, capsys):
    """R-BAL359: the claim's branch, when it was claimed and last committed; whether git says
    the card shipped; each commit on dev or main that added or deleted its marker, newest
    first, or that none did."""
    tracker = FakeTracker()
    tracker.add(1)
    tracker.add(2)
    claim(code, 1)
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert "\n  claim: card/1 (claimed 2026-10-04T12:00:00Z; last commit " in out
    assert out.endswith("\nno commit on origin/dev or origin/main adds, changes or deletes "
                        "plan/ships/1\n")
    shipped = ship(code, 2)
    assert run(tracker, code, "show", "plan#2") == 0
    out = capsys.readouterr().out
    assert "\n  shipped (git): yes\n" in out and "\n  claim: none\n" in out
    assert out.endswith(f"\n\nadded plan/ships/2: {shipped[:12]} ship\n")
    reverted = unship(code, 2)
    assert run(tracker, code, "show", "plan#2") == 0
    out = capsys.readouterr().out
    assert "\n  shipped (git): no\n" in out
    assert out.endswith(f"\n\ndeleted plan/ships/2: {reverted[:12]} Revert ship\n"
                        f"added plan/ships/2: {shipped[:12]} ship\n")


def test_show_names_each_links_title_and_prints_the_comments_after_the_text(code, capsys,
                                                                            monkeypatch):
    """Card plan#27, leaf B: a child's and a blocker's title follow their numbers, read by
    number once -- a step child the leaf rule read already is not read again -- and the
    card's comments follow its text, oldest first, each under its time and author (``?``
    when the account is gone), its trailing space trimmed and its leading space kept (an
    indented first line, a code block, keeps its indent); a card with none says so, its
    parent's aside."""
    tracker = FakeTracker()
    tracker.add(1, body="The spec.", blocked_by=(4,),
                children=(Child(2, "step", True), Child(3, "finding", True)))
    tracker.add(2, title="Leaf two", parent=1)
    tracker.add(3, "finding", title="A finding", parent=1)
    tracker.add(4, title="The blocker")
    tracker.notes[1] = [Comment("SaltyReformed", "2026-10-05T00:00:00Z",
                                "    indented first line\nFirst, by hand.\n\n"),
                        Comment(None, "2026-10-06T00:00:00Z", "Second.")]
    reads, read = [], tracker.cards
    monkeypatch.setattr(tracker, "cards", lambda numbers: reads.append(set(numbers)) or read(
        numbers))
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert ("\n  child: plan#2 (step, open) Leaf two\n  child: plan#3 (finding, open) A finding"
            "\n  blocked by: plan#4 The blocker\n\nThe spec.\n\n"
            "== comment, 2026-10-05T00:00:00Z by SaltyReformed\n    indented first line\n"
            "First, by hand.\n"
            "== comment, 2026-10-06T00:00:00Z by ?\nSecond.\n\nno commit on ") in out
    assert reads == [{1}, {2}, {3, 4}]
    reads.clear()
    assert run(tracker, code, "show", "plan#2") == 0
    out = capsys.readouterr().out
    assert "\n\nno comments\n\nno commit on " in out and "by hand" not in out
    assert reads == [{2}]


def test_show_lists_the_findings_and_rulings_a_step_owns_and_names_a_findings_owner(
        code, capsys, monkeypatch):
    """R-BAL395: a finding and a ruling are blocked by their owner step, never its sub-issues.
    A step's show lists each it owns, with its kind, state and title, read by number in the
    ONE read its other links' titles take; a step waiting on it is no card it owns, so it is
    neither listed nor read; and the read by number decides, so a card the blocking read
    still finds that is blocked by the step no longer (a lagging read, a link removed since)
    is not listed.  A finding's show names its one blocker as its owner, and a blocked step's
    blocker is no owner."""
    tracker = FakeTracker()
    tracker.add(1, title="The step", body="The spec.", blocked_by=(4,))
    tracker.add_task(2, 1, title="A piece")
    tracker.add(3, "finding", title="A finding", blocked_by=(1,))
    tracker.add(4, title="The blocker")
    tracker.add(5, "ruling", title="A ruling", blocked_by=(1,), is_open=False,
                state_reason="COMPLETED", closed_by_tool=True)
    tracker.add(6, title="A waiting step", blocked_by=(1,))
    tracker.add(7, "finding", title="Unlinked since")
    found = FakeTracker.blocking
    monkeypatch.setattr(tracker, "blocking", lambda number: (
        *found(tracker, number), *((Child(7, "finding", True),) if number == 1 else ())))
    reads, read = [], tracker.cards
    monkeypatch.setattr(tracker, "cards", lambda numbers: reads.append(set(numbers)) or read(
        numbers))
    assert run(tracker, code, "show", "plan#1") == 0
    out = capsys.readouterr().out
    assert ("\n  parent: none\n  child: plan#2 (task, open; done (git): no) A piece\n"
            "  blocked by: plan#4 The blocker\n  owns: plan#3 (finding, open) A finding\n"
            "  owns: plan#5 (ruling, closed) A ruling\n\nThe spec.\n") in out
    assert "plan#6" not in out and "plan#7" not in out
    assert reads == [{1}, {2, 3, 4, 5, 7}]
    reads.clear()
    assert run(tracker, code, "show", "plan#3") == 0
    out = capsys.readouterr().out
    assert "\n  parent: none\n  owner (blocked by): plan#1 The step\n\n" in out
    assert "owns:" not in out and reads == [{3}, {1}]
    assert run(tracker, code, "show", "plan#6") == 0
    out = capsys.readouterr().out
    assert "\n  blocked by: plan#1 The step\n" in out and "owner" not in out


def test_show_names_no_owner_of_a_finding_blocked_by_two_steps(code, capsys):
    """R-BAL395: an owner is a finding's ONE blocker (``_state.owner``, which ``sync``'s
    report reads).  Blocked by two steps it has none: its show names each as a blocker and
    neither as its owner, and neither step's show lists it as one it owns (R395's review,
    F5)."""
    tracker = FakeTracker()
    tracker.add(1, title="The step that fixes it")
    tracker.add(2, title="A step it waits on")
    tracker.add(3, "finding", title="A finding", blocked_by=(1, 2))
    assert run(tracker, code, "show", "plan#3") == 0
    out = capsys.readouterr().out
    assert ("\n  parent: none\n  blocked by: plan#1 The step that fixes it\n"
            "  blocked by: plan#2 A step it waits on\n\n") in out
    assert "owner" not in out
    for step in (1, 2):
        assert run(tracker, code, "show", f"plan#{step}") == 0
        assert "owns:" not in capsys.readouterr().out


def test_a_comment_quill_posts_is_shown_after_the_text(code, tmp_path, capsys):
    """``quill comment`` then ``quill show``: the comment the tool wrote is read back under
    the App's login."""
    tracker = FakeTracker()
    tracker.add(1, body="The spec.")
    note = tmp_path / "note"
    note.write_text("Noted.\n")
    assert run(tracker, code, "comment", "plan#1", "--body-file", str(note)) == 0
    assert run(tracker, code, "show", "plan#1") == 0
    assert (f"\nThe spec.\n\n== comment, 2026-10-06T00:00:00Z by {FakeTracker.app_login}\n"
            "Noted.\n\n") in capsys.readouterr().out


def test_show_fails_on_a_grandchild_the_leaf_rule_reads_and_no_read_holds(code, capsys):
    """Review M-1: a step closed while still being filed has its own linked steps read for
    the leaf rule (:func:`_state.leaves`); one of those no read by its number holds fails
    show, exit 2, naming it -- the refusal only the leaf rule's read makes, since the
    titles are read for the card's own links alone."""
    tracker = FakeTracker()
    tracker.add(1, children=(Child(2, "step", False),))
    tracker.add(2, parent=1, is_open=False, state_reason="NOT_PLANNED", on_board=False,
                labels=("balance", FILING), children=(Child(3, "step", True),))
    assert run(tracker, code, "show", "plan#1") == 2
    assert ("failed: plan#3 is linked below plan#1, blocks it or is blocked by it, but a read "
            "by its number does not hold it: run show again") in capsys.readouterr().err


@pytest.mark.parametrize("links", [{"children": (Child(2, "step", True),)},
                                   {"children": (Child(2, "finding", True),)},
                                   {"blocked_by": (2,)},
                                   {"blocking": (Child(2, "ruling", False),)}])
def test_show_fails_on_a_link_no_read_by_number_holds(code, capsys, monkeypatch, links):
    """A child, a blocker or a card the step's blocking read finds it owns (R-BAL395) that a
    read by its number does not hold (a read lagging the link, or a card deleted since) fails
    show, exit 2, naming it.  The titles' read refuses the finding child, the blocker and
    the owned ruling; the step child is refused first by the leaf rule's read, and with that
    refusal gone the titles' read refuses it in the same words.  The leaf rule's read alone
    is graded by the grandchild case above."""
    tracker = FakeTracker()
    tracker.add(1, **{link: value for link, value in links.items() if link != "blocking"})
    if "blocking" in links:
        monkeypatch.setattr(tracker, "blocking", lambda _number: links["blocking"])
    assert run(tracker, code, "show", "plan#1") == 2
    assert ("failed: plan#2 is linked below plan#1, blocks it or is blocked by it, but a read "
            "by its number does not hold it: run show again") in capsys.readouterr().err
