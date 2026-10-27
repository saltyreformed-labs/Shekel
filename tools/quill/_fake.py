"""An in-memory tracker for the ``quill`` command's tests, and the helpers they share.

``_tracker`` is graded against RECORDED GitHub answers (``test__tracker.py``);
the command's tests grade what each command decides to read and write, so they
drive the commands against :class:`FakeTracker`, which keeps cards in memory.
It keeps what the commands depend on the way the recordings show GitHub keeping
it -- a body edit saves a full version, and the first edit also saves the body
as filed; a parent's read lists each sub-issue's kind and state as they stand
now, not as they were when it was linked; a mark removed from a card that
does not carry it is answered as GitHub answers it, 404; a card added to the board
again keeps the item it has (and, unmeasured, its place), and an unlink of a card
that is no sub-issue of the parent named is refused 403, as GitHub answers both
(``recorded/twice.json``, ``recorded/unlink.json``) -- and FAILS LOUDLY on a write
the tool must never send
(re-parenting a card, under a parent inside the tracker or out), rather than
guessing GitHub's answer to it.  Its board shows a
placement at once unless told to lag (:attr:`FakeBoard.lagging`); the lag
itself is graded against a recording.
Nothing here calls GitHub.

What git says -- a card shipped, a card claimed, a task done -- is never faked:
:func:`ship`, :func:`unship` and :func:`claim` write the ship markers and claims (ruling
``balance:R-BAL359``), :func:`finish`, :func:`unfinish`, :func:`piece` and
:func:`unpiece` the task files (ruling ``balance:R-BAL386``), and :func:`named` a commit naming
its task (ruling plan#1872), to the ``code`` fixture's bare
``origin`` (``conftest.py``), which every command then reads as it reads the real one.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime

from tools.ci.gitcmd import DEV, tracking
from tools.ci.scratch import commit_files, elsewhere
from tools.ci.scratch import run as _run
from tools.quill import _git, quill
from tools.quill._command import on_board as goes_on_board
from tools.quill._github import GitHubError
from tools.quill._tracker import Card, Child, Comment, Edit, in_order
from tools.quill.setup_tracker import APP_LOGIN, FILING, PLAN, unmarked


class FakeBoard:
    """The board in memory: card numbers in their positions, each card's kind read from the
    tracker's ``cards``; its writes go to the tracker's log."""

    def __init__(self, writes, cards):
        """An empty board logging into ``writes``, reading kinds from ``cards``; it shows each
        placement at once."""
        self.items: list[int] = []
        self.writes = writes
        self.cards = cards
        self.lagging = False

    def held(self):
        """Every card on the board, in its positions: ``(card number, item id, kind)``."""
        return [(n, f"PVTI_{n}", self.cards[n].kind) for n in self.items]

    def order(self):
        """The board's order, by the one projection :class:`_tracker.Board` reads
        (:func:`_tracker.in_order`): ``(card number, item id)``."""
        return in_order(self.held())

    def add(self, card):
        """Add a card at the bottom; one already on the board keeps its item, as GitHub
        answers a second add (``recorded/twice.json``: the same item, no new one) -- a read
        that lags an add makes the tool send one.  That it keeps its place too is not
        measured (the recording reads no order after the adds); the fake leaves it there."""
        self.writes.append(("board_add", card.number))
        if card.number not in self.items:
            self.items.append(card.number)
        return f"PVTI_{card.number}"

    def remove(self, item):
        """Take a card off the board."""
        number = int(item.removeprefix("PVTI_"))
        self.writes.append(("board_remove", number))
        self.items.remove(number)

    def place(self, item, after):
        """Move an item to just after ``after`` (the top when None); whether the board
        shows it there (not while :attr:`lagging`)."""
        number = int(item.removeprefix("PVTI_"))
        self.writes.append(("board_place", number, after))
        self.items.remove(number)
        index = 0 if after is None else self.items.index(int(after.removeprefix("PVTI_"))) + 1
        self.items.insert(index, number)
        return not self.lagging



class FakeTracker:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """The tracker in memory: cards, bodies, comments, its board, and every write.

    Pylint: ``too-many-public-methods`` (24/20, measured at X-cx's S3b) -- it
    stands in for :class:`_tracker.Tracker`, so it has each of that class's 22
    reads and writes but ``connect`` (its own disable says why there are so many),
    and ``add`` and ``add_task``, which put a card or a task in.
    ``too-many-instance-attributes`` (8/7) -- **one per kind
    of fact it keeps**: seven a command reads back (cards, bodies, saved
    versions, comments, the board, the milestones that exist, and each card's
    milestone) and the log of writes the tests read.  The claims and the merged
    pull requests it kept left it at X-cx's M1, when a claim became a branch of
    the code repository (R-BAL359); the deleted numbers and how GitHub renders a
    text, which only X-cx's migration read, left with it at X-cx's S3b.
    """

    app_login = APP_LOGIN
    place = PLAN

    def __init__(self):
        """An empty tracker."""
        self.cards_by_number: dict[int, Card] = {}
        self.bodies: dict[int, str] = {}
        self.versions: dict[int, list[Edit]] = {}
        self.notes: dict[int, list[Comment]] = {}
        self.milestones: set[int] = set()
        self.milestone_of: dict[int, int] = {}
        self.writes: list[tuple] = []
        self.board = FakeBoard(self.writes, self.cards_by_number)

    def add(self, number, kind="step", body="", on_board=True, **fields):
        """Put a card in the tracker (open, unclaimed, and by default on the board's bottom when
        quill would put it there, :func:`_command.on_board`)."""
        values = {"number": number, "id": 1000 + number, "node_id": f"I_{number}",
                  "title": f"card {number}", "kind": kind, "labels": ("balance",),
                  "is_open": True, "state_reason": None, "parent": None, "children": (),
                  "blocked_by": (), "board_item": None, "closed_by_tool": False,
                  "touched_by_hand": False, "outside": (), **fields}
        self.cards_by_number[number] = Card(**values)
        self.bodies[number] = body
        parent = values["parent"]
        if (on_board and values["is_open"] and goes_on_board(
                kind, self._view(parent) if parent in self.cards_by_number else None)):
            self.board.items.append(number)
        return self.cards_by_number[number]

    def add_task(self, number, parent, **fields):
        """Put a task in the tracker (R-BAL386): a sub-issue of type ``task`` under the card
        ``parent``, which lists it, as GitHub links one; open and carrying no label by
        default, and on the board under a step the board holds (:func:`_command.on_board`)
        unless ``on_board=False`` says it was filed off the board."""
        task = self.add(number, "task", labels=(), parent=parent, **fields)
        held = self.cards_by_number[parent]
        self._set(parent, children=(*held.children, Child(number, "task", task.is_open)))
        return task

    def _set(self, number, **fields):
        """Change a held card."""
        self.cards_by_number[number] = dataclasses.replace(self.cards_by_number[number], **fields)

    def _view(self, number):
        """A card as a read returns it: its board item filled in, and each sub-issue it
        holds listed with that card's kind and state as they stand."""
        card = self.cards_by_number[number]
        item = f"PVTI_{number}" if number in self.board.items else None
        children = tuple(
            Child(child.number, held.kind, held.is_open)
            if (held := self.cards_by_number.get(child.number)) else child
            for child in card.children
        )
        return dataclasses.replace(card, board_item=item, children=children)

    # -- reads
    def open_cards(self):
        """Every open card."""
        return {n: self._view(n) for n, c in self.cards_by_number.items() if c.is_open}

    def marked(self):
        """Every card carrying the filing mark, open or closed."""
        return {n: self._view(n) for n, c in self.cards_by_number.items() if FILING in c.labels}

    def cards(self, numbers):
        """The cards that exist among ``numbers``."""
        return {n: self._view(n) for n in numbers if n in self.cards_by_number}

    def milestones_of(self, numbers):
        """The milestone each card that exists among ``numbers`` is in, None for none."""
        return {n: self.milestone_of.get(n) for n in numbers if n in self.cards_by_number}

    def blocking(self, number):
        """The cards ``number`` blocks: each whose blocked-by edges name it, with its kind and
        state as they stand, in card order (GitHub lists the newest link first, which no
        command reads)."""
        return tuple(Child(n, card.kind, card.is_open)
                     for n, card in sorted(self.cards_by_number.items())
                     if number in card.blocked_by)

    def body(self, number):
        """A card's body."""
        return self.bodies[number]

    def edits(self, number):
        """A card's body and its versions (the body as filed when it was never edited).  A
        number nobody filed is GitHub's NOT_FOUND error (:meth:`_refuse_unfiled`)."""
        self._refuse_unfiled(number)
        versions = self.versions.get(number) or [
            Edit(None, "2026-10-01T00:00:00Z", self.app_login, self.bodies[number])]
        return self.bodies[number], list(versions)

    def comments(self, number):
        """A card's comments, oldest first.  A number nobody filed is GitHub's NOT_FOUND
        error (:meth:`_refuse_unfiled`)."""
        self._refuse_unfiled(number)
        return list(self.notes.get(number, []))

    def _refuse_unfiled(self, number):
        """GitHub's NOT_FOUND error, which the strict read raises
        (:meth:`_github.GitHub.graphql`), for a read by number of a card nobody filed, as
        the read of its versions and the read of its comments each ask for it, at
        ``repository.issue``: the type and message ``recorded/tracker.json`` records for a
        missing issue (at the by-number lookup's path), without the recorded ``locations``.
        Neither read's own answer is recorded; the strict read raises on any error, so what
        either command does with it holds whatever the path."""
        if number not in self.cards_by_number:
            raise GitHubError(200, 'graphql: [{"type": "NOT_FOUND", "path": ["repository", '
                              f'"issue"], "message": "Could not resolve to an Issue with the '
                              f'number of {number}."}}]')

    def find_titles(self, text):
        """Cards whose title holds ``text``."""
        return [(n, c.title) for n, c in self.cards_by_number.items() if text in c.title]

    # -- writes
    def create(self, kind, title, body, labels, milestone=None):
        """File a card at the next number, marked :data:`setup_tracker.FILING` beside
        ``labels``, as :meth:`_tracker.Tracker.create` files every card, in ``milestone``
        when one is given.  One the tracker lacks is a write the tool must never send, so it
        fails loudly rather than guess GitHub's answer."""
        number = max(self.cards_by_number, default=0) + 1
        labels = tuple(sorted({*labels, FILING}))
        assert milestone is None or milestone in self.milestones, (
            f"no milestone #{milestone} to file plan#{number} in")
        self.writes.append(("create", number, kind, title, labels)
                           + (() if milestone is None else (milestone,)))
        self.add(number, kind, body, on_board=False, title=title, labels=labels)
        if milestone is not None:
            self.milestone_of[number] = milestone
        return number

    def set_milestone(self, number, milestone):
        """Put a card in milestone number ``milestone``, or with None in none."""
        self.writes.append(("set_milestone", number, milestone))
        if milestone is None:
            self.milestone_of.pop(number, None)
        else:
            self.milestone_of[number] = milestone

    def retype(self, number, kind):
        """Change a card's kind."""
        self.writes.append(("retype", number, kind))
        self._set(number, kind=kind)

    def retitle(self, number, title):
        """Rename a card."""
        self.writes.append(("retitle", number, title))
        self._set(number, title=title)

    def set_body(self, number, body):
        """Replace a card's body, saving a version as GitHub does: the full body, and on
        the first edit the body as filed before it (measured 2026-10-04)."""
        self.writes.append(("set_body", number, body))
        versions = self.versions.setdefault(number, [])
        if not versions:
            versions.append(Edit(f"E{number}.0", "2026-10-04T00:00:00Z", self.app_login,
                                 self.bodies[number]))
        versions.append(Edit(f"E{number}.{len(versions)}", "2026-10-04T00:00:01Z",
                             self.app_login, body))
        self.bodies[number] = body

    def comment(self, number, text):
        """Comment on a card as the tool."""
        self.writes.append(("comment", number, text))
        self.notes.setdefault(number, []).append(
            Comment(self.app_login, "2026-10-06T00:00:00Z", text))

    def close(self, number, reason):
        """Close a card as the tool."""
        self.writes.append(("close", number, reason))
        self._set(number, is_open=False, state_reason=reason.upper(), closed_by_tool=True,
                  touched_by_hand=False)

    def reopen(self, number):
        """Reopen a card as the tool."""
        self.writes.append(("reopen", number))
        self._set(number, is_open=True, state_reason="REOPENED", closed_by_tool=False,
                  touched_by_hand=False)

    def unmark(self, number):
        """Remove a card's filing mark.  One the card no longer carries -- something outside
        the tool removed it mid-filing -- is answered as GitHub answers it, 404 "Label does
        not exist" (``test__tracker.py``), and nothing is written."""
        labels = self.cards_by_number[number].labels
        if FILING not in labels:
            raise GitHubError(404, f'DELETE .../issues/{number}/labels/{FILING} -> 404: '
                                   '{"message":"Label does not exist"}')
        self.writes.append(("unmark", number))
        self._set(number, labels=unmarked(labels))

    def add_child(self, parent, child):
        """Make ``child`` a sub-issue of ``parent``; the tool never re-parents a card, in the
        tracker or outside it."""
        held = self.cards_by_number[child.number]
        assert held.parent is None, f"plan#{child.number} already has parent plan#{held.parent}"
        assert not [link for link in held.outside if link.what == "parent"], (
            f"plan#{child.number} already has a parent outside the tracker")
        self.writes.append(("add_child", parent, child.number))
        held = self.cards_by_number[parent]
        self._set(parent, children=(*held.children, Child(child.number, child.kind, True)))
        self._set(child.number, parent=parent)

    def place_child(self, parent, child, before):
        """Move ``child``, a sub-issue of ``parent``, to just above ``before``, another, in
        ``parent``'s order of them; the same move sent again changes nothing
        (``recorded/priority.json``)."""
        self.writes.append(("place_child", parent, child.number, before.number))
        held = self.cards_by_number[parent].children
        moved = next(c for c in held if c.number == child.number)
        rest = [c for c in held if c.number != child.number]
        at = next(i for i, c in enumerate(rest) if c.number == before.number)
        self._set(parent, children=(*rest[:at], moved, *rest[at:]))

    def remove_child(self, parent, child):
        """Unlink ``child`` from ``parent``.  One that is no sub-issue of ``parent`` -- a read
        that lags an unlink makes the tool send it -- is refused as GitHub refuses it, 403
        "Resource not accessible by integration", open parent or closed
        (``recorded/unlink.json``), and nothing is written."""
        held = self.cards_by_number[child.number]
        if held.parent != parent:
            raise GitHubError(403, f"DELETE .../issues/{parent}/sub_issue -> 403: "
                                   '{"message":"Resource not accessible by integration"}')
        self.writes.append(("remove_child", parent, child.number))
        above = self.cards_by_number[parent]
        self._set(parent, children=tuple(c for c in above.children if c.number != child.number))
        self._set(child.number, parent=None)

    def block(self, number, blocker):
        """Record a blocked-by edge."""
        self.writes.append(("block", number, blocker.number))
        held = self.cards_by_number[number]
        self._set(number, blocked_by=(*held.blocked_by, blocker.number))

    def unblock(self, number, blocker):
        """Remove a blocked-by edge."""
        self.writes.append(("unblock", number, blocker.number))
        held = self.cards_by_number[number]
        self._set(number, blocked_by=tuple(b for b in held.blocked_by if b != blocker.number))


class Sent:
    """A ``requests`` session that answers each request with ``status`` and ``answer`` (and
    no headers), for a test of what :class:`_github.GitHub` or :class:`_recorded.Recorder`
    does with one answer."""

    def __init__(self, status, answer):
        """Hold the answer; count what is sent."""
        self.status, self.answer, self.sent = status, answer, []

    def request(self, method, url, **kwargs):
        """Answer, and remember what was sent."""
        self.sent.append((method, url, kwargs.get("json")))
        content = json.dumps(self.answer).encode()
        return type("Response", (), {"status_code": self.status, "content": content,
                                     "text": content.decode(), "headers": {},
                                     "json": lambda _self: self.answer})()


class FailOnce:
    """One write of ``owner`` (the tracker or its board) failing the first time, as a timeout
    or a 502 would; it writes every time after."""

    def __init__(self, owner, name):
        """Stand in for ``owner.<name>``."""
        self.real, self.failed = getattr(owner, name), False
        setattr(owner, name, self)

    def __call__(self, *args, **kwargs):
        """Fail once, then write."""
        if not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return self.real(*args, **kwargs)


class AnswerLostOnce:
    """One write of ``owner`` (the tracker or its board) that LANDS and whose answer is then
    lost, the first time -- a 502 or a timeout after GitHub made the write; it writes and
    answers every time after.  For ``board.place``: the move lands, and a read of the board
    back then fails, as one of :meth:`_tracker.Board.shows`'s reads can."""

    def __init__(self, owner, name):
        """Stand in for ``owner.<name>``."""
        self.real, self.failed = getattr(owner, name), False
        setattr(owner, name, self)

    def __call__(self, *args, **kwargs):
        """Write, then fail once."""
        answer = self.real(*args, **kwargs)
        if not self.failed:
            self.failed = True
            raise GitHubError(502, "bad gateway")
        return answer


#: When a claim :func:`claim` makes says it was made, unless a test names another instant.
CLAIMED = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _onto_dev(root, message, files):
    """A commit on ``origin``'s ``dev`` writing or deleting ``files``, made on the ``dev``
    ``origin`` holds and pushed there from another clone (:func:`scratch.elsewhere`), as
    GitHub's merge of a ``card/N`` moves ``dev``: ``root`` sees it only through a fetch of
    its own; its sha."""
    other = elsewhere(root)
    sha = commit_files(other, _run(other, "rev-parse", tracking(DEV)), message, files)
    _run(other, "push", "--quiet", "origin", f"{sha}:refs/heads/dev")
    return sha


def ship(root, *numbers):
    """Land on ``origin``'s ``dev`` the ship marker of each card in ``numbers`` (R-BAL359), as
    a merged ``card/N`` branch lands it; its sha."""
    return _onto_dev(root, "ship", {_git.marker(number): f"claimed {_git.instant(CLAIMED)}\n"
                                    for number in numbers})


def unship(root, *numbers):
    """Delete from ``origin``'s ``dev`` the ship marker of each card in ``numbers``, as a
    revert of the change that shipped it does; its sha."""
    return _onto_dev(root, "Revert ship", dict.fromkeys(map(_git.marker, numbers)))


def finish(root, *numbers):
    """Land on ``origin``'s ``dev`` the file of each task in ``numbers`` (R-BAL386), as the
    merge of its card's branch lands it; its sha."""
    return _onto_dev(root, "finish", {_git.task_file(number): f"done {_git.instant(CLAIMED)}\n"
                                      for number in numbers})


def unfinish(root, *numbers):
    """Delete from ``origin``'s ``dev`` the file of each task in ``numbers``, as a revert of the
    commit that wrote it does; its sha."""
    return _onto_dev(root, "Revert finish", dict.fromkeys(map(_git.task_file, numbers)))


def _onto_claim(root, card, message, files):
    """A commit on ``origin``'s ``card/<card>`` (a claim :func:`claim` made) writing or deleting
    ``files``, pushed there from another clone (:func:`scratch.elsewhere`), as a session pushes
    the commit finishing a piece; its sha."""
    other, branch = elsewhere(root), _git.card_branch(card)
    _run(other, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    sha = commit_files(other, _run(other, "rev-parse", f"refs/remotes/origin/{branch}"), message,
                       files)
    _run(other, "push", "--quiet", "origin", f"{sha}:refs/heads/{branch}")
    return sha


def piece(root, card, *numbers):
    """Push onto ``card/<card>`` the file of each task in ``numbers`` (R-BAL386), as the commit
    finishing each piece writes it; its sha."""
    return _onto_claim(root, card, "piece", {
        _git.task_file(number): f"done {_git.instant(CLAIMED)}\n" for number in numbers})


def named(root, card, task):
    """Push onto ``card/<card>`` a commit naming ``task`` by its trailer ``Task: plan#<task>``
    (ruling plan#1872), as a commit of its piece names it, writing no task file; its sha."""
    return _onto_claim(root, card, f"work\n\n{_git.TASK_TRAILER}: plan#{task}",
                       {f"work-{task}.txt": "work\n"})


def unpiece(root, card, *numbers):
    """Push onto ``card/<card>`` a commit deleting the file of each task in ``numbers``, as a
    revert of the commit finishing the piece does on the branch; its sha."""
    return _onto_claim(root, card, "Revert piece", dict.fromkeys(map(_git.task_file, numbers)))


def claim(root, number, when=CLAIMED):
    """Claim card ``number`` on ``origin`` as a session in another clone would
    (:func:`_git.claim` on the ``dev`` ``origin`` holds, from :func:`scratch.elsewhere`):
    ``root`` learns of it only through a read of its own, and its HEAD and branches are left
    as they were; the claim's sha."""
    return _git.claim(elsewhere(root), number, tracking(DEV), when)



def leaf_filing(tmp_path, title, parent="plan#1"):
    """``quill file step`` filing a leaf named ``title`` under ``parent``, its spec written to
    a file."""
    spec = tmp_path / f"{title}.md"
    spec.write_text(f"Build {title}.")
    return ("file", "step", "--parent", parent, "--arc", "balance", "--title", title,
            "--body-file", str(spec))


def finding_filing(owner, title="Twice", text="The report counts a refund twice."):
    """``quill file finding`` filing the finding ``title``, saying ``text``, for ``owner``."""
    return ("file", "finding", "--arc", "balance", "--title", title, "--owner", owner,
            "--text", text)


def ruling_filing(tmp_path, owner):
    """``quill file ruling`` filing the ruling "Home" under ``owner``, its question and answer
    written to files."""
    question, answer = tmp_path / "q", tmp_path / "a"
    question.write_text("Where?")
    answer.write_text("Here.")
    return ("file", "ruling", "--arc", "balance", "--title", "Home", "--owner", owner,
            "--question-file", str(question), "--answer-file", str(answer))


def run(tracker, root, *argv):
    """Run one command against ``tracker`` and ``root``; its exit status."""
    return quill.main(list(argv), connect=lambda: tracker, root=root)
