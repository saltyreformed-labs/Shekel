"""The one card check: what a card may hold before anyone writes it; no GitHub calls."""
from __future__ import annotations

import pytest

from tools.quill.check import (
    BODY,
    FINDING_CAP,
    OWNER,
    RULING_CAP,
    TITLE,
    TITLE_CAP,
    Draft,
    Owner,
    card_title,
    in_ruling_shape,
    ruling_body,
    ruling_question,
    title_alias,
    violations,
)

#: A made-up finding of the shape R-BAL136 gives as its example.
SENTENCE = "The report counts a purchase twice when its refund lands in a later pay period."
#: A change that writes only the labels (or the type): nothing capped is graded.
LABELS_ONLY = frozenset()


def _finding(body=SENTENCE, **changes):
    """A finding that passes every rule, with ``changes`` applied."""
    fields = {"kind": "finding", "title": "Refund counted twice", "body": body,
              "labels": ("balance",), "owner": Owner("step", True)}
    return Draft(**{**fields, **changes})


def _ruling(body=None, **changes):
    """A ruling that passes every rule, with ``changes`` applied."""
    fields = {"kind": "ruling", "title": "Where the plan lives",
              "body": body if body is not None else ruling_body("Where?", 'Picked "Here".'),
              "labels": ("balance",), "owner": Owner("step", False)}
    return Draft(**{**fields, **changes})


@pytest.mark.parametrize("draft", [
    _finding(),
    _ruling(),
    Draft("question", "Which day?", "Which day should a transfer show?", ("recurrence",)),
    Draft("step", "Split the walk", "Any spec at all.", ("balance", "moves-money")),
    Draft("step", "A leaf", "", ("salary",), Owner("step", True)),
])
def test_a_new_card_of_each_kind_that_keeps_the_rules_passes(draft):
    """Each kind's own rules, and no more: a step and a question have no text cap."""
    assert not violations(draft)


def test_a_type_outside_the_four_kinds_is_refused_whatever_the_change():
    """A card the plan cannot classify cannot be ordered, owned or shipped."""
    for kind in (None, "Bug", "Step"):
        (problem,) = violations(Draft(kind, "t", "b", ("balance",)), LABELS_ONLY)
        assert problem.startswith(f"its type is {kind!r}")


@pytest.mark.parametrize("labels", [(), ("moves-money",), ("balance", "salary")])
def test_a_card_carries_exactly_one_arc_label(labels):
    """None or two arcs is a card no arc's lane would find; other labels do not count."""
    problems = violations(_finding(labels=labels), LABELS_ONLY)
    assert len(problems) == 1 and "exactly one arc label" in problems[0]


def test_the_release_labels_ride_beside_the_arc():
    """``moves-money`` and ``deploy-together`` are not arcs."""
    assert not violations(_finding(labels=("balance", "moves-money", "deploy-together")))


def test_a_title_written_is_a_short_name_of_at_most_100_characters():
    """R-BAL178 and R-BAL180: present, and at most 100 characters (surrounding space aside)."""
    assert "has none" in violations(_finding(title="  "), {TITLE})[0]
    assert not violations(_finding(title=" " + "t" * TITLE_CAP + " "), {TITLE})
    assert f"it is {TITLE_CAP + 1}" in violations(_finding(title="t" * 101), {TITLE})[0]
    assert not violations(_finding(title="t" * 500), LABELS_ONLY)


@pytest.mark.parametrize(("owner", "said"), [
    (None, "its owner is none"),
    (Owner("finding", True), "its owner is finding"),
    (Owner("step", False), "its owner is a step that is done"),
])
def test_a_finding_set_under_an_owner_needs_a_live_step(owner, said):
    """R-BAL395: the owner is the step it is blocked by, never its parent; a finding names a
    LIVE owner."""
    (problem,) = violations(_finding(owner=owner), {OWNER})
    assert problem.endswith(said) and "blocked by a LIVE step, its owner (R-BAL395)" in problem


def test_a_ruling_set_under_an_owner_needs_a_step_done_or_not():
    """A ruling outlives the step that asked it, so its owner may be done."""
    assert not violations(_ruling(owner=Owner("step", False)), {OWNER})
    (problem,) = violations(_ruling(owner=None), {OWNER})
    assert problem == "a ruling is blocked by a step, its owner (R-BAL395); its owner is none"


def test_the_owner_is_graded_only_when_the_change_sets_it():
    """A migrated ruling naming no owner, or a finding whose owner has since shipped, is not
    refused when only its labels change (the review of 2026-10-04, finding H1)."""
    assert not violations(_ruling(owner=None), LABELS_ONLY)
    assert not violations(_finding(owner=Owner("step", False)), {TITLE, BODY})


def test_a_step_splits_only_a_step_and_a_question_needs_no_owner():
    """A leaf's parent is the step it splits (R-BAL179); a question's owner is the developer."""
    leaf = Draft("step", "leaf", "", ("balance",), Owner("finding", True))
    assert violations(leaf) == ["a step's parent is the step it splits, not a finding"]
    assert not violations(Draft("question", "q", "Why?", ("balance",)))


@pytest.mark.parametrize(("body", "said"), [
    ("The walk drops a leg\nwhen the transfer settles.", "on one line"),
    ("The walk drops a leg when the transfer", "ending in '.', '!' or '?'"),
    ("The walk drops a leg. It also double counts.", "a second begins at 'It also"),
    ("The walk drops a leg. `calculate()` double counts.", "a second begins at '`calculate"),
    ('The walk drops a leg. "Paid" is wrong.', "a second begins at '\"Paid"),
    ("**The walk drops a leg.** It is wrong.", "a second begins at 'It is"),
    ("x" * FINDING_CAP + ".", f"it is {FINDING_CAP + 1}"),
])
def test_a_new_finding_is_one_sentence_of_at_most_400_characters(body, said):
    """R-BAL136: a finding's text is one sentence of at most 400 characters."""
    problems = violations(_finding(body), {BODY})
    assert len(problems) == 1 and said in problems[0], problems


def test_a_sentence_may_close_with_a_quote_bracket_or_mark():
    """The end is the punctuation, wherever a closing mark follows it."""
    for body in ('It reads "paid."', "It reads the leg (BAL-1).", "It is `wrong.`",
                 "It is **wrong!**", "x" * (FINDING_CAP - 1) + "."):
        assert not violations(_finding(body), {BODY}), body


def test_an_ellipsis_a_number_and_surrounding_space_are_not_a_second_sentence():
    """``... `x` `` and ``P.L. 119`` stay one sentence; space around the text is not counted."""
    for body in ("The walk waits... `settle()` never runs.", "It misreads P.L. 119 as law.",
                 "\n  " + "x" * (FINDING_CAP - 1) + ".  \n"):
        assert not violations(_finding(body), {BODY}), body


def test_a_body_stored_with_crlf_or_as_null_is_read_as_its_text():
    """A web form may send CRLF, and GitHub answers an empty body as null."""
    crlf = ruling_body("Where?", "Here.").replace("\n", "\r\n")
    assert not violations(_ruling(crlf), {BODY})
    assert "complete sentence" in violations(_finding(None), {BODY})[0]
    empty = Draft("ruling", "t", None, ("balance",), Owner("step", False))
    assert "question and his answer" in violations(empty, {BODY})[0]


def test_text_nobody_touched_keeps_its_length():
    """R-BAL136: "rows nobody edits keep their text until they close" -- a migrated
    finding of 1,100 characters passes while only its labels or parent change."""
    long_text = "One. Two. " * 110
    assert not violations(_finding(long_text), {TITLE, OWNER})
    assert not violations(_ruling("anything at all " * 200), {TITLE, OWNER})


def test_ruling_body_is_the_question_then_the_answer_word_for_word():
    """The shape the check reads back is the one the tool writes."""
    body = ruling_body("  Where should it live?\n", ' Picked "Here": "One page."  ')
    assert body == 'Question: Where should it live?\n\nAnswer: Picked "Here": "One page."'
    assert not violations(_ruling(body), {BODY})


@pytest.mark.parametrize("body", [
    "Where should it live? Here.",
    "Question: Where should it live?",
    "Question: \n\nAnswer: Here.",
    "Question: Where?\n\nAnswer:  ",
    "Q: Where?\n\nAnswer: Here.",
])
def test_a_new_ruling_without_both_question_and_answer_is_refused(body):
    """R-BAL136: the question AND the answer, each under its own mark."""
    (problem,) = violations(_ruling(body), {BODY})
    assert "question and his answer" in problem


def test_a_new_ruling_is_at_most_2000_characters_in_all():
    """R-BAL138: a hard cap on question and answer together."""
    fits = ruling_body("q" * (RULING_CAP - len(ruling_body("", "")) - 1), "a")
    assert len(fits) == RULING_CAP
    assert not violations(_ruling(fits), {BODY})
    (problem,) = violations(_ruling(fits + "a"), {BODY})
    assert f"it is {RULING_CAP + 1}" in problem


@pytest.mark.parametrize("body", [
    "It fails. 'Paid' is wrong.",
    "It fails. (See the walk.)",
    "It fails. _Paid_ is wrong.",
    "It fails. [The walk](x) is wrong.",
    "It reads plan A. The total is wrong.",
])
def test_a_second_sentence_opening_with_a_quote_bracket_or_mark_is_seen(body):
    """Review L3: each passed as one sentence; a single letter's dot still ends one."""
    (problem,) = violations(_finding(body), {BODY})
    assert "a second begins" in problem


@pytest.mark.parametrize("body", [
    "The U.S. Treasury rate is read wrong.",
    "The rate is read wrong, e.g. The Treasury's, in every period.",
    "It fails in the U.S.",
])
def test_an_initialism_is_not_a_sentence_end(body):
    """Review L3: ``U.S.`` and ``e.g.`` were read as the end of a first sentence."""
    assert not violations(_finding(body), {BODY})


def test_a_leaf_splits_only_a_live_step():
    """Review H2: a step already done with has no work left to split."""
    leaf = Draft("step", "leaf", "", ("balance",), Owner("step", False))
    assert violations(leaf) == [
        "a step's parent is the LIVE step it splits; its parent is a step that is done"]


def test_ruling_question_reads_back_only_a_body_of_the_rulings_shape():
    """R-BAL186: a conversion cut short leaves the question in the ruling's shape."""
    assert ruling_question(ruling_body(" Ship it? ", "Yes.").replace("\n", "\r\n"),
                           "Yes.") == "Ship it?"
    for body in ("Ship it tonight?", "Question: Ship it?", None):
        assert ruling_question(body, "Yes.") is None


def test_ruling_question_reads_back_only_what_this_answer_wrote_whatever_marks_it_holds():
    """Review cp3 H-1: a question QUOTING a ruling's shape was cut at its first mark and
    lost the rest.  Only a body ending in THIS answer is a conversion already written,
    and its question is everything between, marks and all."""
    quoting = ("Question: Your ruling reads, word for word:\n\nAnswer: \"A short name\".\n\n"
               "Should the cap be 100 characters or 200?")
    assert ruling_question(quoting, "100.") is None
    assert ruling_question(ruling_body(quoting, "100."), "100.") == quoting
    assert ruling_question(ruling_body("Where?", "Here."), "There.") is None
    assert ruling_question(ruling_body("Where?", "Here.\r\nAnd there."),
                           "Here.\nAnd there.") == "Where?"
    for body in ("Question: Ship it? Yes.", "Ship it?\n\nAnswer: Yes."):
        assert ruling_question(body, "Yes.") is None, body


def test_a_card_under_a_parent_with_no_type_is_refused_never_read_as_having_none():
    """Review cp3 M-4: an untyped parent passed as "no parent", so a leaf was filed under a
    card that is no step."""
    untyped = Owner(None, True)
    assert violations(Draft("step", "leaf", "", ("balance",), untyped)) == [
        "a step's parent is the step it splits; its parent is a card with no type"]
    for draft in (_finding(owner=untyped), _ruling(owner=untyped)):
        (problem,) = violations(draft, {OWNER})
        assert problem.endswith("its owner is a card with no type")
    assert not violations(Draft("step", "top", "", ("balance",)))


def test_in_ruling_shape_needs_both_marks():
    """Review cp4b C1: the question mark alone is no ruling's shape."""
    assert in_ruling_shape(ruling_body("Where?", "Here."))
    for body in ("Question: Where?", "Where?\n\nAnswer: Here.", None):
        assert not in_ruling_shape(body), body


@pytest.mark.parametrize("alias", ["X-bi-6-4d", "N-391 question", "R-BAL80", "A"])
def test_a_migrated_title_gives_back_the_alias_it_was_written_with(alias):
    """X-cx's migration finds an item's card by the alias its title carries (L7 draft 4
    s.4); one with a space in it (``N-391 question``) included."""
    assert title_alias(card_title(alias, "A short name")) == alias


@pytest.mark.parametrize("title", ["X-cx move the plan", "[X-cx]move", "[] name", "[a]b] name",
                                   " [A] name", "[A]"])
def test_a_title_carrying_no_alias_mark_gives_none(title):
    """No mark first, no space after it, an empty one, a ``]`` inside it, a space before it,
    nothing after it: none is :func:`card_title`'s shape."""
    assert title_alias(title) is None


def _task(**changes):
    """A task that passes every rule (R-BAL386), with ``changes`` applied."""
    fields = {"kind": "task", "title": "4d-4a: the links' migration", "body": "The migration.",
              "labels": (), "owner": Owner("step", True)}
    return Draft(**{**fields, **changes})


@pytest.mark.parametrize("owner", [Owner("step", True), Owner("finding", True)])
def test_a_task_under_a_live_piece_of_work_carrying_no_arc_passes(owner):
    """R-BAL386: a task is a piece of one step or finding, still work, and carries no arc
    label, its arc being its card's (rule 14); the filing mark and a release label are no
    arc."""
    assert not violations(_task(owner=owner))
    assert not violations(_task(owner=owner, labels=("filing", "moves-money")))


def test_a_task_carrying_an_arc_label_is_refused_whatever_the_change():
    """An arc on a task is a second home for its card's arc."""
    (problem,) = violations(_task(labels=("balance",)), LABELS_ONLY)
    assert problem == "a task carries no arc label: its arc is its card's; it has ['balance']"


@pytest.mark.parametrize(("owner", "said"), [
    (None, "none"),
    (Owner(None, True), "a card with no type"),
    (Owner("step", False), "a step that is done"),
    (Owner("finding", False), "a finding that is done"),
    (Owner("ruling", False), "ruling"),
    (Owner("question", True), "question"),
    (Owner("task", True), "task"),
])
def test_a_task_set_under_a_parent_needs_a_live_step_or_finding(owner, said):
    """A task under no card, a card of no type, work that is done, or a card that is no work
    is refused: no change ships its card, so it is never done."""
    assert violations(_task(owner=owner)) == [
        "a task is a sub-issue of a LIVE step or finding, the piece of work it is part of "
        f"(R-BAL386); its parent is {said}"]
    assert not violations(_task(owner=owner), {TITLE, BODY})


def test_a_tasks_title_is_held_as_any_cards_is():
    """R-BAL178 and R-BAL180 hold a task's title too, when the change writes it."""
    assert violations(_task(title=" "), {TITLE}) == ["its title is a short name, and it has none"]
    assert violations(_task(title="t" * (TITLE_CAP + 1)), {TITLE}) == [
        f"its title is a short name of at most {TITLE_CAP} characters; it is {TITLE_CAP + 1}"]
    assert not violations(_task(title="t" * (TITLE_CAP + 1)), LABELS_ONLY)
