"""What GitHub keeps of a suggestion's reason (review of plan#2798, finding 8), against answers
RECORDED once.

``_suggestion.Suggestions.put`` compares the reason it reads back with the one it wrote, exactly,
so a reason GitHub kept otherwise than as written would fail every run of its filing, the card
left marked.  ``recorded/reasons.json`` was kept on a scratch card of the throwaway
``plan-views-demo`` (board "Plan views demo"), deleted after, with the developer's own token
(ruling plan#3260), as ``suggestion.json`` was: :func:`reasons_round` is every call it holds,
which the recording session ran once against GitHub and the test replays
(``_recorded.Replay``).  Nothing here calls GitHub.

What it measured, 2026-11-10 19:28 UTC on scratch card #266: GitHub keeps a reason as written,
characters outside ASCII and a run of spaces alike, so ``put``'s exact read-back holds; and it
counts a reason's cap in characters, not bytes, taking 280 and refusing 281, so the check's
cap (``check.REASON_CAP``, counted by ``len``) refuses what GitHub would and no more.  A
character outside the Basic Multilingual Plane, which UTF-16 spells as two, is not measured.
"""
from __future__ import annotations

import pytest

from tools.quill._draft import Draft, Suggestion
from tools.quill._github import GitHub, GitHubError, TrackerError
from tools.quill._recorded import SCRATCH, Replay, Scratch, recording
from tools.quill._tracker import Board, Tracker
from tools.quill.check import violations

#: The login the recording's suggestions were made by: the developer's own token's.
SUGGESTER = "SaltyReformed"
#: The Priority each of the recording's suggestions names.
PRIORITY = "Low"
_OUTSIDE = "A scratch card\u2019s reason outside ASCII, café and all, padded to the cap: "
_OVER = "A scratch card's reason one character over the cap, all ASCII: "
#: Each reason the recording puts, by what it measures, in the order it puts them: one holding
#: characters outside ASCII (a right single quote, three bytes in UTF-8, and an e with an acute,
#: two); one of exactly :data:`check.REASON_CAP` characters holding the same two, 283 bytes in
#: UTF-8, so a cap counted in bytes refuses it; one holding a run of two spaces; and one of 281
#: characters, all ASCII.
REASONS = {
    "outside ASCII": "A scratch card\u2019s reason: café.",
    "at the cap outside ASCII": _OUTSIDE + "a" * (279 - len(_OUTSIDE)) + ".",
    "two spaces": "A scratch card's reason holding a run of  two spaces.",
    "over the cap": _OVER + "a" * (280 - len(_OVER)) + ".",
}


def reasons_round(tracker):
    """Every call ``recorded/reasons.json`` holds, in its order: a scratch step filed and
    unmarked; then for each of :data:`REASONS` in turn, a put of it (:meth:`_suggestion.
    Suggestions.put`, which reads it back), its card's pending suggestion read, and that
    suggestion taken back when one is pending; the card closed.  Each reason's outcome by its
    name: the refusal the put raised, or None, and the suggestion read after it."""
    number = tracker.create(Draft("step", f"{SCRATCH}: a card whose reasons quill reads back",
                                  "Scratch for plan#3301's recording; deleted when it ends.", (),
                                  "balance"))
    tracker.unmark(number)
    card = tracker.cards([number])[number]
    outcomes = {}
    for name, why in REASONS.items():
        try:
            tracker.suggestions.put(card, Suggestion(PRIORITY, why))
            refused = None
        except (GitHubError, TrackerError) as error:
            refused = str(error)
        held = tracker.suggestions.pending(card)
        outcomes[name] = (refused, held)
        if held is not None:
            tracker.suggestions.withdraw(card, held)
    tracker.close(number, "not_planned")
    return outcomes


def _replayed():
    """The tracker over the reasons recording, and the replay."""
    replay = Replay("reasons")
    scratch = Scratch.from_json(recording("reasons")["scratch"])
    github = GitHub("token", replay)
    return Tracker(github, Board(github, scratch.place, scratch.board), SUGGESTER), replay


def test_github_keeps_a_reason_as_written_and_refuses_one_over_its_cap():
    """Recorded 2026-11-10 (ruling plan#3260): each reason within the cap, outside ASCII, at
    the cap in 283 bytes, or holding a run of two spaces, is put, read back as written by its
    suggester (``put`` raised nothing) and taken back; the one of 281 characters is refused by
    GitHub, nothing pending after it."""
    tracker, replay = _replayed()
    outcomes = reasons_round(tracker)
    for name in ("outside ASCII", "at the cap outside ASCII", "two spaces"):
        refused, held = outcomes[name]
        assert refused is None, name
        assert held.suggestion == Suggestion(PRIORITY, REASONS[name])
        assert held.actor.startswith("U_")
    refused, held = outcomes["over the cap"]
    assert "rationale is too long (maximum is 280)" in refused
    assert held is None
    assert replay.unused() == 0


@pytest.mark.parametrize("name", REASONS)
def test_the_check_refuses_a_reason_exactly_when_github_does(name):
    """The check's cap is GitHub's as measured: a reason GitHub took passes it, so no filing it
    passes is refused at the put, and the one GitHub refused is refused before anything is
    written, which a filing would otherwise reach only after its create."""
    refused, _ = reasons_round(_replayed()[0])[name]
    draft = Draft("step", "New", "New work.", (), "balance",
                  suggestion=Suggestion(PRIORITY, REASONS[name]))
    problems = violations(draft, frozenset())
    if refused is None:
        assert problems == []
    else:
        assert problems == ["a suggestion's reason is at most 280 characters (GitHub's cap); it "
                            "is 281"]
