"""quill's suggested Priority on a card (:mod:`_suggestion`), against GitHub answers RECORDED once.

``recorded/suggestion.json`` was kept 2026-11-10 on scratch card #265 of the throwaway
``plan-views-demo`` (board "Plan views demo"), deleted after, with the developer's own token
(ruling plan#3260), as ``status.json`` was: :func:`_suggestion_round` is every call it holds,
which the recording session ran once against GitHub and the test replays
(``_recorded.Replay``).  With the developer's token the suggestion's actor is his login,
:data:`SUGGESTER`, where quill's App's is ``shekel-quill``: the App's own writes and reads were
measured live on plan#3178 the same day (``_suggestion``'s module docstring).  A test that needs
an answer no scratch card can give starts from a recorded answer and changes the one field it
is about, and says so.  Nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import json

import pytest

from tools.quill._draft import Draft, Suggestion
from tools.quill._github import GitHub, TrackerError
from tools.quill._recorded import SCRATCH, Replay, Scratch, recording
from tools.quill._suggestion import SUGGEST, WITHDRAW, Pending
from tools.quill._tracker import Board, Tracker

#: The login the recording's suggestions were made by: the developer's own token's.
SUGGESTER = "SaltyReformed"
#: What the recording suggests.
SUGGESTED = Suggestion("High", "A scratch card's reason: nothing goes wrong, it is deleted after.")


def _suggestion_round(tracker):
    """Every call ``recorded/suggestion.json`` holds, in its order: a scratch step filed and
    unmarked; its pending suggestion read (none); :data:`SUGGESTED` put, which reads it back;
    the card read; its suggestion read again; taken back, which reads it back; read again
    (none); the card closed.  The card as read after the put, and each read of its pending
    suggestion by when it was read."""
    number = tracker.create(Draft("step", f"{SCRATCH}: a card quill suggests a Priority for",
                                  "Scratch for plan#2798's recording; deleted when it ends.", (),
                                  "balance"))
    tracker.unmark(number)
    card = tracker.cards([number])[number]
    reads = {"filed": tracker.suggestions.pending(card)}
    tracker.suggestions.put(card, SUGGESTED)
    held = tracker.cards([number])[number]
    reads["put"] = tracker.suggestions.pending(card)
    tracker.suggestions.withdraw(card, reads["put"])
    reads["withdrawn"] = tracker.suggestions.pending(card)
    tracker.close(number, "not_planned")
    return held, reads


def _replayed():
    """The tracker over the suggestion recording, and the replay."""
    replay = Replay("suggestion")
    scratch = Scratch.from_json(recording("suggestion")["scratch"])
    github = GitHub("token", replay)
    return Tracker(github, Board(github, scratch.place, scratch.board), SUGGESTER), replay


def test_a_suggestion_is_put_read_and_taken_back_and_the_priority_holds_none():
    """Recorded 2026-11-10 (ruling plan#3260): a card filed holds no suggestion; a put of a
    suggested High with its reason is read back as written, by its suggester, the card's
    Priority still holding none; taken back, the card holds none again."""
    tracker, replay = _replayed()
    held, reads = _suggestion_round(tracker)
    assert reads["filed"] is None and reads["withdrawn"] is None
    assert reads["put"].suggestion == SUGGESTED
    assert reads["put"].actor.startswith("U_")
    assert held.priority is None
    assert replay.unused() == 0


class _Answers:
    """A ``requests`` session answering each request with the next of ``answers`` (200, no
    headers), for a test of what :class:`_suggestion.Suggestions` does with an answer no
    scratch card gave; what was sent is kept."""

    def __init__(self, *answers):
        """Hold the answers, in order."""
        self.answers, self.sent = list(answers), []

    def request(self, method, url, **kwargs):
        """The next answer; what was sent kept."""
        self.sent.append((method, url, kwargs.get("json")))
        answer = self.answers.pop(0)
        content = json.dumps(answer).encode()
        return type("Response", (), {"status_code": 200, "content": content,
                                     "text": content.decode(), "headers": {},
                                     "json": lambda _self: answer})()


def _recorded_answer(holds: str) -> dict:
    """The first recorded GraphQL answer whose request's text holds ``holds``, copied."""
    exchanges = recording("suggestion")["exchanges"]
    return copy.deepcopy(next(exchange["answer"] for exchange in exchanges
                              if holds in str((exchange["body"] or {}).get("query"))))


def _pending_read(*suggestions):
    """The recorded read of the card's pending suggestions after the put, holding
    ``suggestions`` in place of the one recorded."""
    answer = _recorded_answer("pendingSuggestions")
    issue = answer["data"]["repository"]["issue"]
    put = next(node for node in _all_pending_reads() if node)[0]
    issue["pendingSuggestions"] = [{**put, **change} for change in suggestions]
    return answer


def _all_pending_reads():
    """Every recorded read of the card's pending suggestions, each as its list."""
    return [exchange["answer"]["data"]["repository"]["issue"]["pendingSuggestions"]
            for exchange in recording("suggestion")["exchanges"]
            if "pendingSuggestions" in str((exchange["body"] or {}).get("query"))]


def _suggestions(*answers):
    """A tracker's suggestions answered by ``answers`` in order, as :data:`SUGGESTER`, and the
    session."""
    session = _Answers(*answers)
    scratch = Scratch.from_json(recording("suggestion")["scratch"])
    github = GitHub("token", session)
    return Tracker(github, Board(github, scratch.place, scratch.board), SUGGESTER).suggestions, \
        session


def _card():
    """The recording's scratch card, as the replay's round read it after the put."""
    tracker, _ = _replayed()
    return _suggestion_round(tracker)[0]


def test_two_suggestions_of_quills_or_a_card_answered_null_are_refused_unread():
    """A read holding two suggestions by the suggester (the recorded one, twice) is refused,
    as quill makes one; a suggestion by another account, or of another kind, is not
    quill's and is passed over; a card GitHub answers as null is refused."""
    card = _card()
    suggestions, _ = _suggestions(_pending_read({}, {}))
    with pytest.raises(TrackerError, match="holds 2 pending suggestions"):
        suggestions.pending(card)
    others = _pending_read({"actor": {"login": "someone-else", "id": "U_other"}},
                           {"__typename": "PendingLabelSuggestion"})
    suggestions, _ = _suggestions(others)
    assert suggestions.pending(card) is None
    null = _pending_read()
    null["data"]["repository"]["issue"] = None
    suggestions, _ = _suggestions(null)
    with pytest.raises(TrackerError, match="answered it as null"):
        suggestions.pending(card)


def test_a_put_names_the_card_and_the_option_and_is_refused_when_it_does_not_read_back():
    """A put sends the suggestion's write through its door, naming the card's node id, the
    field's and the option's ids and the reason; refused, before any write, for an option
    Priority does not hold; and refused after it when the read back holds another value."""
    card = _card()
    options = _recorded_answer("options")
    wrong = _pending_read({"suggestedValue": "Low"})
    suggestions, session = _suggestions(options, {"data": {"setIssueFieldValue": {}}}, wrong)
    with pytest.raises(TrackerError, match="holds the pending suggestion"):
        suggestions.put(card, SUGGESTED)
    sent = session.sent[1][2]
    assert sent["query"] == SUGGEST
    assert sent["variables"]["issue"] == card.node_id
    assert sent["variables"]["why"] == SUGGESTED.why
    suggestions, session = _suggestions(options)
    with pytest.raises(TrackerError, match="has no option 'Someday'"):
        suggestions.put(card, Suggestion("Someday", SUGGESTED.why))
    assert len(session.sent) == 1


def test_a_take_back_names_its_suggester_and_is_refused_while_one_stays():
    """A take-back sends its write through the door, naming the card and the suggester's node
    id as read; refused when the read after it still holds quill's suggestion."""
    card = _card()
    held = _pending_read({})
    pending = Pending(SUGGESTED, held["data"]["repository"]["issue"]["pendingSuggestions"][0][
        "actor"]["id"])
    suggestions, session = _suggestions(_recorded_answer("options"),
                                        {"data": {"rejectPendingIssueSuggestions": {}}}, held)
    with pytest.raises(TrackerError, match="still holds quill's pending suggestion"):
        suggestions.withdraw(card, pending)
    sent = session.sent[1][2]
    assert sent["query"] == WITHDRAW
    assert sent["variables"] == {"issue": card.node_id, "actor": pending.actor,
                                 "field": sent["variables"]["field"]}
    assert sent["variables"]["field"].startswith("IFSS_")
