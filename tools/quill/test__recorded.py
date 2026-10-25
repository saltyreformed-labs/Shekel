"""The recorder keeping GitHub's answers for the tests, and the redaction keeping them public.

The recordings live in the PUBLIC code repository, and the tracker they read is
private, so a recording session writes to scratch cards only and keeps the text
of scratch cards only (_recorded).  These tests grade that guard -- which
requests it sends, which strings it keeps -- on crafted exchanges, and take a
census of every recording in recorded/.  Nothing here calls GitHub.
"""
from __future__ import annotations

import json

import pytest

from tools.quill._fake import Sent
from tools.quill._recorded import (
    REDACTED,
    RECORDINGS,
    SCRATCH,
    Recorder,
    Scratch,
    recording,
    redacted,
    refusal,
)
from tools.quill._tracker import BOARD_ADD, BOARD_AFTER, BOARD_REMOVE, BOARD_TOP
from tools.quill.setup_tracker import FILING, PLAN, REHEARSAL

#: The real tracker's ``owner/name``, as every recording here names it.
TRACKER = PLAN.full_name


def test_a_recording_never_keeps_a_token():
    """An installation token is minted through a client the recorder never wraps; if one
    ever reached it, saving refuses."""
    recorder = Recorder(Scratch())
    recorder.exchanges.append({"method": "POST", "url": "u", "body": None, "status": 201,
                               "answer": {"token": "secret"}})
    with pytest.raises(ValueError, match="token"):
        recorder.save("never-written")
    assert not (RECORDINGS / "never-written.json").exists()


def test_a_recording_keeps_a_scratch_cards_text_and_redacts_every_other_cards():
    """L10: the recording is in the public repository and the tracker is private.  A card's
    text is kept only for a scratch card; the plan board's own title stays (its
    configuration); a text tied to no card is redacted."""
    exchanges = [
        _graphql({"data": {"repository": {
            "c2": {"number": 2, "title": "Real figure: $1,234.56", "body": "secret"},
            "c11": {"number": 11, "title": f"{SCRATCH} card", "body": "kept text"}}}}),
        _graphql({"data": {"repository": {"issue": {
            "number": 2, "body": "secret body",
            "userContentEdits": {"nodes": [{"diff": "secret diff"}]}}}}}, {"number": 2}),
        _graphql({"data": {"repository": {"issue": {
            "number": 11, "body": "scratch body",
            "userContentEdits": {"nodes": [{"diff": "scratch diff"}]}}}}}, {"number": 11}),
        {"method": "GET", "url": "https://api.github.com/x", "body": None, "status": 200,
         "answer": {"body": "tied to no card"}},
        _graphql({"data": {"organization": {"projectsV2": {"nodes": [
            {"id": "PVT_1", "title": "Shekel plan"}]}}}}),
    ]
    kept = json.dumps(redacted(exchanges, _scratch(11)))
    for private in ("Real figure", "secret", "tied to no card"):
        assert private not in kept, private
    for public in (f"{SCRATCH} card", "kept text", "scratch body", "scratch diff", "Shekel plan"):
        assert public in kept, public


def test_a_cards_comments_are_its_own_text():
    """X-cx leaf B: a card's comments are its own text, as its saved versions are
    (``_recorded._OWN_CONTENT``): kept for a scratch card, redacted for any other card and
    for a related card's comments read through an object without its number; an author's
    login is a kept key, whoever's card."""
    kept = json.dumps(redacted([
        _graphql({"data": {"repository": {"issue": {"number": 2, "comments": {"nodes": [
            {"author": {"login": "SaltyReformed"}, "createdAt": "2026-10-06T00:00:00Z",
             "body": "secret comment"}]}}}}}, {"number": 2}),
        _graphql({"data": {"repository": {"issue": {"number": 11, "comments": {"nodes": [
            {"author": {"login": "shekel-quill"}, "createdAt": "2026-10-06T00:00:01Z",
             "body": "scratch comment"}]}}}}}, {"number": 11}),
        _graphql({"data": {"repository": {"issue": {"number": 11, "parent": {
            "id": "I_real", "comments": {"nodes": [{"body": "secret parent comment"}]}}}}}},
                 {"number": 11}),
    ], _scratch(11)))
    for private in ("secret comment", "secret parent comment"):
        assert private not in kept, private
    for public in ("scratch comment", "shekel-quill", "SaltyReformed"):
        assert public in kept, public


#: A query reading the tracker alone, so a number its answer names no repository for is
#: the tracker's card.
_TRACKER_QUERY = ('query { repository(owner: "saltyreformed-labs", name: "shekel-plan") '
                  '{ c11: issue(number: 11) { number title } } }')


def _graphql(answer, variables=None, query=_TRACKER_QUERY):
    """One recorded GraphQL exchange, by default a read of the tracker alone."""
    return {"method": "POST", "url": "https://api.github.com/graphql",
            "body": {"query": query, "variables": variables or {}}, "status": 200,
            "answer": answer}


def _scratch(*numbers):
    """Scratch cards numbered ``numbers``: REST id 900 + number, node id S_<number>, and a
    board item SI_<number> each, on the plan board PVT_1."""
    return Scratch({*numbers}, {900 + n for n in numbers}, {f"S_{n}" for n in numbers},
                   {f"SI_{n}" for n in numbers}, "PVT_1")


def test_text_the_reviews_found_kept_is_redacted():
    """Review cp3 M-3 (a)-(c), cp4 M-4 and cp4b M1, M2: a titled object with no number took
    the scratch card around it, and then the card the request named; a blocker carrying its
    own number took its scratch parent; only title, body and diff were redacted; a card was
    its number alone, of any repository, and a number naming no repository was the
    tracker's whatever the request read; a related card's edit history, read through an
    object without its number, was the scratch card's own; and every board's title was
    kept.  Only a kept key's string survives outside a scratch card's own text."""
    kept = json.dumps(redacted([
        _graphql({"data": {"repository": {"c11": {
            "number": 11, "title": f"{SCRATCH} x", "parent": {"title": "secret A"},
            "blockedBy": {"nodes": [{"number": 2, "body": "secret B"}]},
            "subIssues": {"nodes": [{"number": 12, "title": "secret C",
                                     "repository": {"nameWithOwner": "o/elsewhere"}}]}}}}}),
        _graphql({"data": {"repository": {"issue": {
            "number": 11, "body": "scratch body",
            "parent": {"title": "secret D", "body": "secret E"}}}}}, {"number": 11}),
        _graphql({"data": {"repository": {"c2": {"number": 2, "title": "t", "bodyText": "secret F",
                                                  "titleHTML": "secret G"}}}}),
        _graphql({"data": {"repository": {"issue": {"number": 2, "timelineItems": {"nodes": [
            {"previousTitle": "secret M", "currentTitle": "secret N"}]}}}}}, {"number": 2}),
        _graphql({"data": {"repository": {"issue": {
            "number": 11, "body": "scratch body",
            "parent": {"id": "I_real", "userContentEdits": {"nodes": [
                {"id": "UCE_1", "diff": "secret O"}]}},
            "subIssues": {"nodes": [{"userContentEdits": {"nodes": [
                {"id": "UCE_2", "diff": "secret P"}]}}]}}}}}, {"number": 11}),
        _graphql({"data": {"node": {"number": 11, "title": "secret Q"}}},
                 query='query { node(id: "I_other") { ... on Issue { number title } } }'),
        _graphql({"data": {"repository": {"issue": {"number": 11, "subIssues": {"nodes": [
            {"number": 11, "title": "secret R", "repository": {"name": "Shekel"}}]}}}}}),
        _graphql({"data": {"repository": {"issue": {"number": 12, "subIssues": {"nodes": [
            {"number": 11, "title": "secret T"}]}}}, "node": {"number": 11, "title": "secret U"}}},
                 query='query { repository(owner: "saltyreformed-labs", name: "shekel-plan") '
                       '{ issue(number: 12) { number subIssues { nodes { number title } } } } '
                       'node(id: "I_other") { ... on Issue { number title } } }'),
        _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret V"}}}},
                 query='query { repository: node(id: "I_other") { ... on Issue { c11: parent '
                       '{ number title } } } r: repository(owner: "saltyreformed-labs", '
                       'name: "shekel-plan") { id } }'),
        {"method": "GET", "url": f"https://api.github.com/repos/{TRACKER}-other/issues/11",
         "body": None, "status": 200, "answer": {"number": 11, "title": "secret W"}},
        {"method": "GET", "url": f"{_ISSUES}/2", "body": None, "status": 200,
         "answer": {"number": 2, "milestone": {"number": 11, "title": "secret X"}}},
        _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret Y"}}}},
                 query="query { viewer { login } }"),
        {"method": "GET", "url": f"{_ISSUES}/11/comments", "body": None, "status": 200,
         "answer": [{"id": 1, "body": "secret Z1"}]},
        {"method": "GET", "url": f"{_ISSUES}/11/comments/1", "body": None, "status": 200,
         "answer": {"body": "kept comment", "quoted": {"body": "secret Z5"}}},
        _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret Z2"}}}},
                 query='query { repository # alias\n: node(id: "I_other") { ... on Issue '
                       '{ c11: parent { number title } } } r: repository(owner: '
                       '"saltyreformed-labs", name: "shekel-plan") { id } }'),
        _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret Z3"}}}},
                 query='query { repository : node(id: "I_other") { ... on Issue { c11: parent '
                       '{ number title } } } r: repository(owner: "saltyreformed-labs", '
                       'name: "shekel-plan") { id } }'),
        _graphql({"data": {"repository": {"issues": {"nodes": [{"number": 12, "parent": {
            "number": 11, "title": "secret Z4"}}]}}}},
                 query='query { repository(owner: "saltyreformed-labs", name: "shekel-plan") '
                       '{ issues(first: 1) { nodes { number parent { number title } } } } }'),
        {"method": "GET", "url": f"https://api.github.com/repos/{TRACKER}/issues/11", "body": None,
         "status": 200, "answer": {"number": 11, "title": "secret S",
                                   "repository": {"full_name": "o/elsewhere"}}},
        {"method": "GET", "url": f"https://api.github.com/repos/{TRACKER}/issues/2", "body": None,
         "status": 200, "answer": {"number": 2, "title": "t", "body_text": "secret H",
                                   "state": "open", "labels": [{"name": "balance"}]}},
        {"method": "GET", "url": "https://api.github.com/repos/o/elsewhere/issues/12",
         "body": None, "status": 200, "answer": {"number": 12, "title": "secret I"}},
        {"method": "GET", "url": "https://api.github.com/search/issues?q=x", "body": None,
         "status": 200, "answer": {"items": [{
             "number": 12, "title": "secret J",
             "repository_url": "https://api.github.com/repos/o/elsewhere"}]}},
        _graphql({"data": {"organization": {"projectsV2": {"nodes": [
            {"id": "PVT_1", "title": "Shekel plan"}, {"id": "PVT_2", "title": "secret K"}]}}}}),
        _graphql({"data": {"repository": {"c12": {"number": 12, "title": "secret L"}}}},
                 query='query { repository(owner: "o", name: "elsewhere") { c12: issue(number: '
                       '12) { number title } } }'),
    ], _scratch(11, 12)))
    assert "secret" not in kept, [w for w in kept.split('"') if "secret" in w]
    for public in (f"{SCRATCH} x", "scratch body", '"open"', '"balance"', "Shekel plan",
                   "kept comment"):
        assert public in kept, public


_ISSUES = f"https://api.github.com/repos/{TRACKER}/issues"
_REFS = f"https://api.github.com/repos/{TRACKER}/git/refs"


@pytest.mark.parametrize(("method", "url", "body"), [
    ("POST", "https://api.github.com/graphql",
     {"query": "mutation($b: String!) { updateIssue(input: {id: \"I_2\", body: $b}) "
               "{ issue { number } } }", "variables": {"b": "secret"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": "# the board\nmutation { updateIssue(input: {id: \"I_2\", body: \"x\"}) "
               "{ issue { id } } }", "variables": {}}),
    ("POST", "https://api.github.com/graphql",
     {"query": "query Q { viewer { login } } mutation M { updateIssue(input: {id: \"I_2\", "
               "body: \"x\"}) { issue { id } } }", "variables": {}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_REMOVE, "variables": {"p": "PVT_1", "i": "PVTI_of_a_real_card"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_ADD, "variables": {"p": "PVT_1", "c": "I_a_real_card"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_ADD, "variables": {"p": "PVT_1", "c": "SI_11"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_ADD, "variables": {"p": "PVT_another_project", "c": "S_11"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_ADD + ' b: updateIssue(input: {id: "I_2", body: "x"}) { issue { id } }',
      "variables": {"p": "PVT_1", "c": "S_11"}}),
    ("POST", f"{_ISSUES}/11/sub_issues", {"sub_issue_id": 911, "replace_parent": True}),
    ("POST", f"{_ISSUES}/11/comments", {"body": "b", "title": "x"}),
    ("POST", f"{_REFS[:-5]}/commits", {"message": "m", "tree": "t", "parents": ["p"]}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_TOP, "variables": {"p": "PVT_1", "i": "S_11"}}),
    ("PATCH", f"{_ISSUES}/2", {"state": "closed"}),
    ("PATCH", f"{_ISSUES}/11", {"title": "Real title"}),
    ("POST", _ISSUES, {"title": "Real title", "body": "b"}),
    ("POST", _ISSUES, {"title": "Real title", "body": "b", "type": "step", "labels": ["balance"]}),
    ("POST", _ISSUES, {"body": "b", "type": "step", "labels": ["balance"]}),
    ("PATCH", f"{_ISSUES}/11", {"state_reason": "not_planned"}),
    ("POST", f"{_ISSUES}/11/sub_issues", {"sub_issue_id": 5698680001}),
    ("POST", f"{_ISSUES}/11/dependencies/blocked_by", {"issue_id": 5698680001}),
    ("DELETE", f"{_ISSUES}/11/dependencies/blocked_by/5698680001", None),
    ("POST", _REFS, {"ref": "refs/claims/2", "sha": "a" * 40}),
    ("DELETE", f"{_REFS}/claims/2", None),
    ("PATCH", "https://api.github.com/repos/o/elsewhere/issues/11", {"body": "anything"}),
    ("PATCH", f"https://api.github.com/repos/{TRACKER}-other/issues/11", {"body": "anything"}),
    ("POST", _ISSUES, {"title": f"{SCRATCH} new", "body": "b", "type": "step",
                       "labels": ["balance"], "assignees": ["x"]}),
    ("PATCH", f"{_ISSUES}/11", {"body": "b", "milestone": 1}),
    ("PATCH", f"{_ISSUES}/11", {"title": f"{SCRATCH} renamed", "body": "b"}),
    ("POST", f"{_ISSUES}/11/dependencies/blocked_by", {"issue_id": 911, "x": 1}),
    ("DELETE", f"{_ISSUES}/11/dependencies/blocked_by/911", {"x": 1}),
    ("POST", _REFS, {"ref": "refs/claims/11", "sha": "a" * 40, "force": True}),
    ("DELETE", f"{_REFS}/claims/11", {"x": 1}),
    ("PUT", f"{_ISSUES}/11/lock", None),
    ("DELETE", f"{_ISSUES}/2/labels/{FILING}", None),
    ("DELETE", f"{_ISSUES}/11/labels/balance", None),
    ("DELETE", f"{_ISSUES}/11/labels", None),
    ("DELETE", f"{_ISSUES}/11/labels/{FILING}", {"x": 1}),
    ("DELETE", f"{_ISSUES}/2/sub_issue", {"sub_issue_id": 911}),
    ("DELETE", f"{_ISSUES}/11/sub_issue", {"sub_issue_id": 5698680001}),
    ("DELETE", f"{_ISSUES}/11/sub_issue", {"sub_issue_id": 911, "x": 1}),
    ("DELETE", f"{_ISSUES}/11/sub_issue", None),
])
def test_a_write_that_is_not_a_known_write_to_a_scratch_card_is_refused(method, url, body):
    """Review cp3 M-3 (d), (e) and review cp4 M-3: a text-writing GraphQL mutation slipped
    past (after a comment, or as a second operation), and the Recorder sent writes that
    change real cards -- re-parenting one by its REST id, a claim on one, its board item
    removed -- or that write another repository's issue numbered like a scratch card.  Only a
    known route to scratch cards, or a board write moving a scratch card's item, is sent."""
    assert refusal(method, url, body, _scratch(11)) is not None
    with pytest.raises(ValueError, match="only reads and known writes"):
        redacted([{"method": method, "url": url, "body": body, "status": 200, "answer": None}],
                 _scratch(11))


@pytest.mark.parametrize(("method", "url", "body"), [
    ("POST", _ISSUES, {"title": f"{SCRATCH} new", "body": "b", "type": "step",
                       "labels": ["balance"]}),
    ("PATCH", f"{_ISSUES}/11", {"title": f"{SCRATCH} renamed"}),
    ("PATCH", f"{_ISSUES}/11", {"state": "closed", "state_reason": "not_planned"}),
    ("POST", f"{_ISSUES}/11/sub_issues", {"sub_issue_id": 911}),
    ("POST", f"{_ISSUES}/11/dependencies/blocked_by", {"issue_id": 911}),
    ("DELETE", f"{_ISSUES}/11/dependencies/blocked_by/911", None),
    ("DELETE", f"{_ISSUES}/11/labels/{FILING}", None),
    ("DELETE", f"{_ISSUES}/11/sub_issue", {"sub_issue_id": 911}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_ADD, "variables": {"p": "PVT_1", "c": "S_11"}}),
    ("POST", "https://api.github.com/graphql",
     {"query": BOARD_AFTER, "variables": {"p": "PVT_1", "i": "SI_11", "a": "PVTI_real"}}),
    ("GET", f"{_ISSUES}/2", None),
])
def test_a_known_write_to_a_scratch_card_or_any_read_is_sent(method, url, body):
    """Each route the recording sessions take, to a scratch card; and every read."""
    assert refusal(method, url, body, _scratch(11)) is None


@pytest.mark.parametrize(("method", "url", "body"), [
    ("POST", f"{_REFS[:-5]}/commits", {"message": "claim plan#11\n\nbranch: feat/x\n",
                                       "tree": "t", "parents": []}),
    ("POST", _REFS, {"ref": "refs/claims/11", "sha": "a" * 40}),
    ("POST", _REFS, {"ref": "refs/claims/recording-scratch", "sha": "a" * 40}),
    ("DELETE", f"{_REFS}/claims/11", None),
])
def test_a_claim_write_is_refused_even_for_a_scratch_card(method, url, body):
    """X-cx's M1 (R-BAL359): a claim is the code repository's branch ``card/N``, so quill
    writes no claim's commit or reference to the tracker, and the recorder no longer knows
    those routes.  Each claim write it sent before -- a parentless commit, a scratch card's
    claim reference, the recording's own stray one, a claim's delete -- is now refused."""
    assert refusal(method, url, body, _scratch(11)) is not None
    with pytest.raises(ValueError, match="only reads and known writes"):
        redacted([{"method": method, "url": url, "body": body, "status": 201, "answer": None}],
                 _scratch(11))


def test_a_recorder_refuses_a_write_before_sending_it_and_counts_what_it_files_as_scratch():
    """A write a recording may not keep is never sent at all; a card filed with a scratch
    title, and the board item added for a scratch card, join what it may write to."""
    session = Sent(201, {"number": 16, "id": 916, "node_id": "S_16"})
    recorder = Recorder(_scratch(11), session)
    with pytest.raises(ValueError, match="not sent"):
        recorder.request("PATCH", f"{_ISSUES}/2", json={"body": "a new spec"})
    assert not session.sent
    recorder.request("POST", _ISSUES, json={"title": f"{SCRATCH} new", "body": "b", "type": "step",
                           "labels": ["balance"]})
    assert (16 in recorder.scratch.numbers, 916 in recorder.scratch.ids,
            "S_16" in recorder.scratch.nodes) == (True, True, True)
    session.answer = {"data": {"addProjectV2ItemById": {"item": {"id": "SI_16"}}}}
    recorder.request("POST", "https://api.github.com/graphql",
                     json={"query": BOARD_ADD, "variables": {"p": "PVT_1", "c": "S_16"}})
    assert "SI_16" in recorder.scratch.items


def test_a_create_github_refused_counts_nothing_as_scratch():
    """Review cp4 M52: only a filing GitHub took makes a scratch card."""
    recorder = Recorder(_scratch(11), Sent(422, {"message": "Validation Failed"}))
    recorder.request("POST", _ISSUES, json={"title": f"{SCRATCH} new", "body": "b", "type": "step",
                           "labels": ["balance"]})
    assert recorder.scratch == _scratch(11)


#: Every recording in ``recorded/``, and whether it read the real cards (it must then hold a
#: redacted title on a card node): ``tracker.json`` and ``export.json`` read them;
#: ``label_missing.json`` read only its scratch card, and ``blocking.json`` and ``show.json``
#: only their scratch cards on the throwaway ``plan-views-demo`` (R-BAL395; ``show.json``'s
#: board read holds item numbers, no title).  A recording added or removed must be named
#: here (the next test).
RECORDINGS_READ_REAL_CARDS = {"blocking": False, "comments": False, "export": True,
                              "label_missing": False, "show": False, "tracker": True,
                              "twice": False, "unlink": False}


def test_every_recording_is_in_the_census():
    """The census below is parametrized by a fixed list, not by the directory, so a recording
    added, removed or moved fails here instead of being skipped there."""
    assert sorted(path.stem for path in RECORDINGS.glob("*.json")) == sorted(
        RECORDINGS_READ_REAL_CARDS)


@pytest.mark.parametrize("name", sorted(RECORDINGS_READ_REAL_CARDS))
def test_the_recording_the_tests_replay_shows_no_title_but_a_scratch_cards(name):
    """A census of each recording's ANSWERS, apart from the redactor's own rules: every title
    in them is redacted, a scratch card's, or the plan board's.  The walk is shown to reach
    every title the answers hold (counted apart from it, in a serialization of the
    answers), and a recording that read the real cards holds a redacted title on a card
    node (a GraphQL issue's ``I_`` id; a pull request's redacted title does not count).
    Requests are not walked: :func:`_recorded.refusal`, through ``redacted``, guards what a
    recording sends.  And the file is exactly what the current redactor writes from it --
    which proves only that it was saved by this redactor and not edited by hand since, not
    that the redactor is right."""
    saved = recording(name)
    titles = []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("title"), str):
                titles.append((value.get("id", ""), value["title"]))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    answers = [exchange["answer"] for exchange in saved["exchanges"]]
    walk(answers)
    assert titles and len(titles) == json.dumps(answers).count('"title": "'), (
        "the walk must reach every title the recording holds")
    for node, title in titles:
        assert title == REDACTED or title.startswith(SCRATCH) or (
            str(node).startswith("PVT_") and title == "Shekel plan"), title
    assert any(title == REDACTED and str(node).startswith("I_")
               for node, title in titles) == RECORDINGS_READ_REAL_CARDS[name]
    scratch = Scratch.from_json(saved["scratch"])
    assert redacted(saved["exchanges"], scratch) == saved["exchanges"]


def test_a_saved_recording_is_redacted(tmp_path):
    """L10 where it is enforced: ``save`` writes only the redacted exchanges, with the
    scratch cards they were redacted against."""
    recorder = Recorder(Scratch())
    recorder.exchanges.append({"method": "GET", "url": f"{_ISSUES}/2", "body": None,
                               "status": 200,
                               "answer": {"number": 2, "title": "Real figure", "body": "secret"}})
    saved = recorder.save("redacted", tmp_path).read_text(encoding="utf-8")
    assert "Real figure" not in saved and "secret" not in saved and REDACTED in saved
    assert Scratch.from_json(json.loads(saved)["scratch"]) == Scratch()


def test_a_recorder_sends_a_body_only_as_json():
    """Review cp4b L1: a body in ``data=`` was read as no body, so a mutation riding there was
    sent as a read."""
    session = Sent(200, {})
    recorder = Recorder(_scratch(11), session)
    with pytest.raises(ValueError, match="only as JSON"):
        recorder.request("POST", "https://api.github.com/graphql", data='{"query": "mutation"}')
    assert not session.sent


def test_a_board_add_github_refuses_in_a_200_is_not_counted_and_does_not_crash():
    """Review cp4b L4: GraphQL answers a refusal 200 with the field null; the Recorder indexed
    into it."""
    recorder = Recorder(_scratch(11), Sent(200, {"data": {"addProjectV2ItemById": None},
                                                  "errors": [{"message": "no"}]}))
    recorder.request("POST", "https://api.github.com/graphql",
                     json={"query": BOARD_ADD, "variables": {"p": "PVT_1", "c": "S_11"}})
    assert recorder.scratch.items == {"SI_11"}


def test_a_numbered_object_naming_the_tracker_by_full_name_is_its_card():
    """A REST answer may name its repository by ``full_name`` (review cp4b R20)."""
    exchange = {"method": "GET", "url": f"{_ISSUES}/11", "body": None, "status": 200,
                "answer": {"number": 11, "title": f"{SCRATCH} x",
                           "repository": {"full_name": TRACKER}}}
    assert f"{SCRATCH} x" in json.dumps(redacted([exchange], _scratch(11)))


@pytest.mark.parametrize("query", [
    _TRACKER_QUERY,
    'query { repository(owner: "saltyreformed-labs" name: "shekel-plan") '
    '{ c11: issue(number: 11) { number title } } }',
    'query { repository(owner: "saltyreformed-labs", # the tracker\n name: "shekel-plan") '
    '{ c11: issue(number: 11) { number title } } }',
    'query { repository(owner: "saltyreformed-labs", # the tracker\r name: "shekel-plan") '
    '{ c11: issue(number: 11) { number title } } }',
    'query { repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: "#x, y") { id } c11: issue(number: 11) { number title } } }',
])
def test_a_scratch_cards_text_is_kept_where_the_request_reads_the_trackers_cards(query):
    """Review cp4d R11, L3, cp4e M2: the keep direction of the card slots -- a GraphQL field
    of the tracker's ``repository``, however its arguments are separated (a comment ends at
    a lone ``\\r`` too) and with a ``#`` or a comma in its strings, and the answer to a REST
    request under the tracker -- names its card by number alone."""
    graphql = _graphql({"data": {"repository": {"c11": {"number": 11, "title": "kept A"}}}},
                       query=query)
    rest = {"method": "GET", "url": f"{_ISSUES}/11", "body": None, "status": 200,
            "answer": {"number": 11, "title": "kept B"}}
    kept = json.dumps(redacted([graphql, rest], _scratch(11)))
    assert "kept A" in kept and "kept B" in kept


@pytest.mark.parametrize("query", [
    'query { repository(owner: "saltyreformed-labs", name: "shekel-plan") { id } '
    's: search(query: "#", type: ISSUE, first: 1) { issueCount } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: "#x") { id } } repository: node(id: "I_other") '
    '{ ... on Issue { c11: parent { number title } } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") { id } '
    '# the tracker\rrepository: node(id: "I_other") { ... on Issue { c11: parent '
    '{ number title } } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") { id } '
    'repository,: node(id: "I_other") { ... on Issue { c11: parent { number title } } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") { id } '
    'repository\ufeff: node(id: "I_other") { ... on Issue { c11: parent { number title } } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") { id } '
    'a,repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: """a"#b""") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: "a\\"#b") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: """a""b"#c""") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: """a\\"""#b""") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: """a""#b""") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { r: repository(owner: "saltyreformed-labs", name: "shekel-plan") '
    '{ l: label(name: """\n# x""") { id } } '
    'repository(owner: "elsewhere", name: "x") { c11: issue(number: 11) { number title } } }',
    'query { repository(owner: "saltyreformed-labs", name: "Shekel") '
    '{ c11: issue(number: 11) { number title } } }',
    'query { repository(owner: "elsewhere", name: "shekel-plan") '
    '{ c11: issue(number: 11) { number title } } }',
])
def test_a_foreign_card_the_repository_checks_cannot_see_past_is_redacted(query):
    """Review cp4e M2, J, L, cp5 R1, R2, R6, R11, R12: GraphQL reads a ``#`` or a comma in a
    string -- a block string too, whatever quotes, escaped quotes or line breaks it holds --
    as text, ends a comment at a lone ``\\r`` too, and reads a comma or a byte-order mark
    between two tokens as nothing.  A ``#`` in a string read as a comment, a comment run
    past a lone ``\\r``, or a block string ended early hid a foreign ``repository(...)`` or
    an alias named ``repository`` from the checks, and a foreign issue's text was kept as
    scratch card #11's; a comma or a byte-order mark not read as a space would hide one too.
    A repository of the tracker's owner but another name, or another owner, is not the
    tracker."""
    exchange = _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret"}}}},
                        query=query)
    assert "secret" not in json.dumps(redacted([exchange], _scratch(11)))


def test_a_string_in_a_list_takes_no_card_from_the_url():
    """Review cp4e M2's residual (Q): a bare string in a list answer, or one in a list under
    an item with no string of its own, took the card the URL names, so a scratch card's URL
    kept it; nothing encloses the answer, so neither belongs to any card."""
    exchange = {"method": "GET", "url": f"{_ISSUES}/11/comments", "body": None, "status": 200,
                "answer": ["secret A", {"id": 1, "lines": ["secret B"]}]}
    kept = json.dumps(redacted([exchange], _scratch(11)))
    assert "secret A" not in kept and "secret B" not in kept


def _rehearsal_scratch(*numbers):
    """Scratch cards numbered ``numbers`` on X-cx's rehearsal tracker and its board PVT_R."""
    return Scratch({*numbers}, {900 + n for n in numbers}, {f"S_{n}" for n in numbers},
                   {f"SI_{n}" for n in numbers}, "PVT_R", REHEARSAL)


_REHEARSAL_ISSUES = f"https://api.github.com/repos/{REHEARSAL.full_name}/issues"


def test_a_recording_on_the_rehearsal_tracker_keeps_its_scratch_text_and_none_of_the_reals():
    """X-cx L7: recordings of the rehearsal's reads name its place.  Its own scratch card's
    text is kept; the real tracker's card of the same number is another repository's, so
    its text is redacted; the rehearsal board's title is kept."""
    scratch = _rehearsal_scratch(11)
    kept = json.dumps(redacted([
        {"method": "GET", "url": f"{_REHEARSAL_ISSUES}/11", "body": None, "status": 200,
         "answer": {"number": 11, "title": "rehearsal scratch text"}},
        {"method": "GET", "url": f"{_ISSUES}/11", "body": None, "status": 200,
         "answer": {"number": 11, "title": "secret real card"}},
        _graphql({"data": {"repository": {"c11": {"number": 11, "title": "secret A"}}}}),
        _graphql({"data": {"organization": {"projectsV2": {"nodes": [
            {"id": "PVT_R", "title": REHEARSAL.board_title}]}}}}),
    ], scratch))
    assert "rehearsal scratch text" in kept and REHEARSAL.board_title in kept
    assert "secret" not in kept


@pytest.mark.parametrize(("scratch", "url"), [
    (_scratch(11), f"{_REHEARSAL_ISSUES}/11"),
    (_rehearsal_scratch(11), f"{_ISSUES}/11"),
])
def test_a_write_to_the_other_tracker_is_refused_whichever_place_the_scratch_names(scratch, url):
    """The real tracker's URL is a prefix of the rehearsal's, so a write is matched on the
    path under the scratch place's URL: the other tracker's issue #11 is never a scratch
    card, whichever way round."""
    assert refusal("PATCH", url, {"title": f"{SCRATCH} x"}, scratch) is not None
    assert refusal("PATCH", url.replace("/11", "/12"), {"title": f"{SCRATCH} x"},
                   scratch) is not None


def test_a_scratch_set_keeps_its_place_and_an_old_recording_reads_as_the_real_trackers():
    """Every recording kept before L7 named no place and was made on the real tracker."""
    assert Scratch.from_json(_rehearsal_scratch(11).as_json()) == _rehearsal_scratch(11)
    old = {key: value for key, value in _scratch(11).as_json().items() if key != "place"}
    assert Scratch.from_json(old) == _scratch(11) and _scratch(11).place == PLAN


class _Kept(Sent):
    """A session answering as :class:`Sent` does that also keeps each request's keywords."""

    def __init__(self, status, answer):
        """Hold the answer; keep every request's keywords."""
        super().__init__(status, answer)
        self.keywords = []

    def request(self, method, url, **kwargs):
        """Keep the keywords; answer."""
        self.keywords.append(kwargs)
        return super().request(method, url, **kwargs)


def test_a_recorder_passes_on_the_no_redirect_flag_every_request_carries():
    """``_github.GitHub`` sends every request with ``allow_redirects=False``; the recorder
    passes it on to the session it wraps, so a recording session follows no redirect
    either, and still refuses a body sent other than as JSON."""
    session = _Kept(200, {})
    recorder = Recorder(_scratch(11), session)
    recorder.request("GET", f"{_ISSUES}/2", allow_redirects=False)
    assert session.keywords == [{"allow_redirects": False}]
    with pytest.raises(ValueError, match="only as JSON"):
        recorder.request("GET", f"{_ISSUES}/2", allow_redirects=False, data="x")
    assert len(session.sent) == 1
