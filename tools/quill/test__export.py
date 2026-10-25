"""``quill export``, the tracker's daily backup, against GitHub answers RECORDED from the
live tracker.

``recorded/export.json`` was kept 2026-10-21 09:15 EDT by a session that ran the export's
:func:`_export.snapshot` once against the real tracker (twelve cards, none of them
scratch, so every card's text in it is redacted), then three reads of card #2 the export
did not need -- its saved versions, its comments read with the export's fields, and its
first comment's saved versions -- which the tests of a connection longer than one page
start from.  Every read went through ``_recorded.Recorder``, which sends no write.  Its read
of the claims was deleted from it at X-cx's M1, when a claim left the tracker to become the
code repository's branch ``card/N`` (ruling ``balance:R-BAL359``).  A test that needs an
answer no card on the tracker gives (a hundred saved versions or comments, an edited
comment, a card cut short) starts from a recorded answer, changes the one field it is
about, and says so.  Nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import json
import os
from argparse import Namespace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tools.quill import quill
from tools.quill._export import cmd_export, snapshot, write
from tools.quill._fake import Sent
from tools.quill._github import API, GitHub
from tools.quill._queries import (
    COMMENT_EDITS,
    EXPORT_COMMENT_FIELDS,
    comments,
    edits,
    export_cards,
)
from tools.quill._recorded import Replay, recording
from tools.quill._tracker import Board, Tracker, board_of
from tools.quill.setup_tracker import APP_LOGIN, PLAN

#: The cards the tracker held when the recording was kept, and its board's drag order.
CARDS = [*range(1, 11), 26, 27]
BOARD = [2, 3, 4, 5, 6, 9, 7, 8, 10, 26, 27]
#: How many recorded answers the export's own run leaves unread: card #2's three reads.
UNREAD = 3
GRAPHQL = f"{API}/graphql"
#: When the tests' exports begin.
TAKEN = datetime(2026, 10, 21, 12, 44, 31, tzinfo=UTC)


def _key(method, url, body) -> str:
    """One request, as :class:`_recorded.Replay` matches it."""
    return json.dumps([method, url, body], sort_keys=True)


class _Recorded:
    """A ``requests`` session answering each request with its answer in
    ``recorded/export.json``, as often as it is sent (:class:`_recorded.Replay` answers
    each once), after ``change(body, answer)`` -- a test's one changed field -- has had it;
    keeping what was sent."""

    def __init__(self, change=lambda _body, answer: answer):
        """Index the recording's exchanges by request."""
        self.exchanges = {_key(e["method"], e["url"], e["body"]): e
                          for e in recording("export")["exchanges"]}
        self.change = change
        self.sent = []

    def request(self, method, url, **kwargs):
        """The recorded answer to this exact request, changed; an unrecorded one is an
        error."""
        body = kwargs.get("json")
        self.sent.append((method, url, body))
        exchange = self.exchanges[_key(method, url, body)]
        answer = self.change(body, copy.deepcopy(exchange["answer"]))
        return Sent(exchange["status"], answer).request(method, url, **kwargs)


def _tracker(session) -> Tracker:
    """The tracker over ``session``, its board found as ``Tracker.connect`` finds it."""
    github = GitHub("token", session)
    return Tracker(github, Board(github, PLAN, board_of(github, PLAN)), APP_LOGIN)


def _listed(change):
    """A change to the export's recorded listing: ``change(cards)`` edits its issue nodes
    in place."""
    def changed(body, answer):
        if (body or {}).get("query") == export_cards(PLAN):
            change(answer["data"]["repository"]["issues"]["nodes"])
        return answer
    return changed


def _card(nodes, number):
    """Card ``number`` among a listing's ``nodes``."""
    return next(node for node in nodes if node["number"] == number)


def _recorded_data(query, **variables):
    """The recorded GraphQL answer's data to ``query`` sent with ``variables``."""
    return next(copy.deepcopy(e["answer"]["data"]) for e in recording("export")["exchanges"]
                if (e["body"] or {}).get("query") == query
                and e["body"]["variables"] == variables)


def _walk(value):
    """Every dict ``value`` holds, itself included."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_the_recorded_export_holds_every_card_whole_in_number_order():
    """Over the recording: every card once, in number order, its saved versions and its
    comments each kept as every node the listing counted and no page cursor anywhere, card
    #2's seven saved versions and two comments, neither comment edited so neither carries
    versions of its own; no milestones; the board in its drag order; no claims, which are not
    the tracker's (R-BAL359); and the three reads of #2 the run had no need of left
    unread."""
    replay = Replay("export")
    held = snapshot(_tracker(replay), TAKEN)
    listed = _recorded_data(export_cards(PLAN), after=None)["repository"]["issues"]["nodes"]
    assert sorted(held) == ["board", "cards", "milestones", "taken_at", "tracker"]
    assert held["tracker"] == PLAN.full_name and held["taken_at"] == TAKEN.isoformat()
    assert [card["number"] for card in held["cards"]] == CARDS
    for card in held["cards"]:
        for name in ("userContentEdits", "comments"):
            assert set(card[name]) == {"nodes"}
            assert len(card[name]["nodes"]) == _card(listed, card["number"])[name]["totalCount"]
        assert "body" in card and "milestone" in card
    assert not [part for part in _walk(held) if "pageInfo" in part]
    two = _card(held["cards"], 2)
    assert (len(two["userContentEdits"]["nodes"]), len(two["comments"]["nodes"])) == (7, 2)
    assert all(comment["lastEditedAt"] is None and "userContentEdits" not in comment
               for comment in two["comments"]["nodes"])
    assert held["milestones"] == [] and held["board"] == BOARD
    assert replay.unused() == UNREAD


def test_cards_are_kept_in_number_order_whatever_order_github_lists_them_in():
    """GitHub listed the recorded cards in number order, and nothing it documents promises
    that order, so the recorded listing is reversed (the one thing changed): the file keeps
    them in number order still, so one day's file differs from the next's only where the
    cards did."""
    session = _Recorded(_listed(lambda nodes: nodes.reverse()))
    held = snapshot(_tracker(session), TAKEN)
    assert [card["number"] for card in held["cards"]] == CARDS


def test_the_export_sends_only_reads():
    """It writes nothing to the tracker: every request of the recorded run is a GET or a
    GraphQL query, none a mutation."""
    replay = Replay("export")
    snapshot(_tracker(replay), TAKEN)
    assert replay.sent
    for method, url, body in replay.sent:
        assert method == "GET" or (method == "POST" and url == GRAPHQL
                                   and "mutation" not in body["query"]), (method, url)


def test_saved_versions_past_one_page_are_read_again_to_the_end():
    """No card holds a hundred saved versions, so card #2's are cut to the first three in
    the recorded listing, GitHub saying more follow (the one field changed).  The export
    reads them again from the start, by the read ``quill spec-history`` takes, and keeps all
    seven that read holds."""
    def cut(nodes):
        versions = _card(nodes, 2)["userContentEdits"]
        versions.update(nodes=versions["nodes"][:3],
                        pageInfo={"hasNextPage": True, "endCursor": "CURSOR"})

    session = _Recorded(_listed(cut))
    held = snapshot(_tracker(session), TAKEN)
    again = _recorded_data(edits(PLAN), number=2, after=None)
    assert _card(held["cards"], 2)["userContentEdits"] == {
        "nodes": again["repository"]["issue"]["userContentEdits"]["nodes"]}
    assert ("POST", GRAPHQL, {"query": edits(PLAN), "variables": {"number": 2, "after": None}}
            ) in session.sent


def test_comments_past_one_page_are_read_again_with_the_exports_fields():
    """No card holds a hundred comments, so card #2's are cut to the first in the recorded
    listing, GitHub saying more follow (the one field changed).  The export reads them again
    from the start, with its own fields (each comment's id and last edit), and keeps both."""
    def cut(nodes):
        held = _card(nodes, 2)["comments"]
        held.update(nodes=held["nodes"][:1],
                    pageInfo={"hasNextPage": True, "endCursor": "CURSOR"})

    session = _Recorded(_listed(cut))
    held = snapshot(_tracker(session), TAKEN)
    query = comments(PLAN, EXPORT_COMMENT_FIELDS)
    again = _recorded_data(query, number=2, after=None)["repository"]["issue"]["comments"]
    assert _card(held["cards"], 2)["comments"] == {"nodes": again["nodes"]}
    assert ("POST", GRAPHQL, {"query": query, "variables": {"number": 2, "after": None}}
            ) in session.sent


def test_an_edited_comment_is_kept_with_every_saved_version_of_it():
    """No comment on the tracker was ever edited, so card #2's first comment is given a last
    edit in the recorded listing, and the recorded read of its saved versions (none) is given
    one: card #2's first recorded saved version, a version of the same kind with the same
    fields.  The edited comment is kept with it; the other comment, never edited, sends no
    read and carries none."""
    version = _recorded_data(edits(PLAN), number=2, after=None)[
        "repository"]["issue"]["userContentEdits"]["nodes"][0]

    def edited(body, answer):
        if (body or {}).get("query") == COMMENT_EDITS:
            answer["data"]["node"]["userContentEdits"]["nodes"] = [version]
        return _listed(lambda nodes: _card(nodes, 2)["comments"]["nodes"][0].update(
            lastEditedAt=version["editedAt"]))(body, answer)

    session = _Recorded(edited)
    held = snapshot(_tracker(session), TAKEN)
    first, second = _card(held["cards"], 2)["comments"]["nodes"]
    assert first["userContentEdits"] == {"nodes": [version]}
    assert "userContentEdits" not in second
    assert [body["variables"] for _, _, body in session.sent
            if (body or {}).get("query") == COMMENT_EDITS] == [{"id": first["id"], "after": None}]


def test_a_card_github_answers_cut_short_is_refused_and_no_file_is_written(tmp_path, capsys):
    """Card #5's labels answered as more than the read holds (the one field changed): quill
    refuses to read that card, so the export refuses the day's file, exit status 2, and
    leaves the directory empty."""
    session = _Recorded(_listed(lambda nodes: _card(nodes, 5)["labels"].update(totalCount=101)))
    tracker = _tracker(session)
    assert quill.main(["export", "--to", str(tmp_path)], connect=lambda: tracker) == 2
    assert capsys.readouterr().err == "failed: plan#5 has 101 labels; the read holds 1\n"
    assert not list(tmp_path.iterdir())


def test_the_days_file_is_written_whole_and_its_owners_only(tmp_path, capsys):
    """``quill export --to DIR`` over the recording: one file, named for the UTC day the
    export began, holding the snapshot, readable by its owner only; no temporary file
    left; one line saying what it wrote."""
    tracker = _tracker(Replay("export"))
    assert quill.main(["export", "--to", str(tmp_path)], connect=lambda: tracker) == 0
    (path,) = tmp_path.iterdir()
    held = json.loads(path.read_text(encoding="utf-8"))
    day = datetime.fromisoformat(held["taken_at"]).astimezone(UTC).date()
    assert path == tmp_path / f"shekel-plan-{day.isoformat()}.json"
    assert [card["number"] for card in held["cards"]] == CARDS and held["board"] == BOARD
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert capsys.readouterr().out == (
        f"wrote {path}: 12 cards, 0 milestones, 11 on the board\n")


def test_the_file_is_named_for_the_utc_day(tmp_path):
    """Begun at 23:30 in New York on 10-21, the export is 03:30 on 10-22 in UTC, and its
    file and ``taken_at`` say so."""
    begun = datetime(2026, 10, 21, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    cmd_export(Namespace(to=tmp_path), _tracker(Replay("export")), tmp_path,
               clock=lambda: begun)
    (path,) = tmp_path.iterdir()
    assert path == tmp_path / "shekel-plan-2026-10-22.json"
    assert json.loads(path.read_text(encoding="utf-8"))["taken_at"] == (
        "2026-10-22T03:30:00+00:00")


def test_a_second_export_the_same_day_replaces_that_days_file(tmp_path):
    """Run twice in one UTC day, the later file is the day's: one file, the second's."""
    write(tmp_path, PLAN, TAKEN, lambda: {"run": 1})
    path, _ = write(tmp_path, PLAN, TAKEN + timedelta(hours=1), lambda: {"run": 2})
    assert list(tmp_path.iterdir()) == [path]
    assert json.loads(path.read_text(encoding="utf-8")) == {"run": 2}


def test_a_write_that_fails_leaves_no_file_and_the_day_before_as_it_was(tmp_path,
                                                                         monkeypatch):
    """The rename into place fails (a full disk, a permission): the temporary file is
    removed, no file is named for the day, and the day before's file is untouched."""
    before, _ = write(tmp_path, PLAN, TAKEN - timedelta(days=1), lambda: {"day": "before"})
    saved = before.read_bytes()

    def refuse(_source, _target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="No space left"):
        write(tmp_path, PLAN, TAKEN, lambda: {"day": "today"})
    assert list(tmp_path.iterdir()) == [before] and before.read_bytes() == saved


def test_a_directory_that_is_not_there_fails_before_anything_is_read(tmp_path, capsys):
    """``--to`` naming no directory: exit status 2, nothing made, and nothing of the export
    read -- only the two reads that found the board, sent when the tracker connected."""
    missing = tmp_path / "missing"
    replay = Replay("export")
    tracker = _tracker(replay)
    assert quill.main(["export", "--to", str(missing)], connect=lambda: tracker) == 2
    assert capsys.readouterr().err.startswith("failed: [Errno 2] No such file or directory")
    assert not missing.exists() and len(replay.sent) == 2


def test_a_connection_github_answers_null_is_refused_and_no_file_is_written(tmp_path, capsys):
    """GitHub's schema lets it answer a card's saved versions as null; card #3's are, in the
    recorded listing (the one field changed): refused as quill refuses any null connection,
    exit status 2, and the directory left empty."""
    session = _Recorded(_listed(lambda nodes: _card(nodes, 3).update(userContentEdits=None)))
    tracker = _tracker(session)
    assert quill.main(["export", "--to", str(tmp_path)], connect=lambda: tracker) == 2
    assert capsys.readouterr().err == (
        "failed: GitHub answered plan#3's saved versions as null, so the read holds none "
        "of them\n")
    assert not list(tmp_path.iterdir())


def test_the_file_sorts_its_keys_one_to_a_line(tmp_path):
    """Keys sorted at every depth and one value to a line, so one day's file differs from
    the next only where the tracker did."""
    path, _ = write(tmp_path, PLAN, TAKEN, lambda: {"b": 1, "a": {"d": 2, "c": [3]}})
    assert path.read_text(encoding="utf-8") == (
        '{\n "a": {\n  "c": [\n   3\n  ],\n  "d": 2\n },\n "b": 1\n}\n')


def test_the_file_is_synced_before_its_rename_and_the_directory_after(tmp_path, monkeypatch):
    """What makes a file under a day's name whole after a power cut: its bytes reach the
    disk before the rename, and the rename itself after."""
    done = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(handle):
        done.append("fsync " + ("directory" if os.path.isdir(f"/proc/self/fd/{handle}")
                                else "file"))
        real_fsync(handle)

    def replace(source, target):
        done.append("rename")
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    write(tmp_path, PLAN, TAKEN, lambda: {})
    assert done == ["fsync file", "rename", "fsync directory"]
