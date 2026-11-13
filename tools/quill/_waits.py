"""``quill sync``'s pass over the weekly update's waits (rulings plan#3619, plan#3620): a card
filed for each thing the update told that no card holds, so that no step of the update depends
on the developer reading a mention (plan#3552).

**What the update told is its own record**, read here and never judged again, so the queue
stays the one judge of what waits:

- each comment of the update App's (:data:`tools.update.app.APP`) on an open pull request of
  the code repository, :data:`CODE`, holding a marker (:func:`tools.update.app.markers`) for
  each thing the daily queue told (``tools/update/queue.py``): a pull request waiting for a
  card, red at its head, or removed from the merge queue there.  Every open pull request's
  comments are read, and only the App's count, as the queue reads its own.
- each open issue of the code repository the update's alert opened (``tools/update/alert.py``):
  one by the workflow's own bot (:data:`tools.update.app.ACTIONS`) titled as the alert titles
  one (:data:`tools.update.alert.FAILED`).  The alert writes no marker, so this pass makes the
  one its card holds from the issue's number (:func:`_alert`).

**One card for each comment or issue holding a marker no card holds**, the card holding every
marker of it: one comment can tell two things in one text (PR 669's, read 2026-11-12), which a
card per marker could not title apart.

**Which markers a card holds is read from every card's text each pass**, open and closed
(:func:`_held`), so a card shipped or dropped still holds what it was filed for.  Measured
2026-11-13 07:56 UTC on the tracker: 3,655 cards, 37 pages of 100, 4.8 MB in 29.8 s, the
slowest page 1.1 s.  GitHub's issue search is no way in: the same day it found neither a
marker's digest nor a head's short sha, ``8518a3f40f86``, in the App's comments on PR 667.  A
label marking these cards would be a second home of what their text holds (rule 14).  So a
person who deletes a card's markers from its text has it filed again at the next pass.  The
read is made only in a pass that finds something told.

**The card** (ruling plan#3620) is a step in the :data:`ARC` arc holding Priority High, at the
top of the board (:attr:`_filing._Filing.top`), so ``quill next`` hands it out after any Urgent
card and before every other; it holds no Effort, as no session estimated one, and the lane that
claims it sets one (``quill effort``).  It passes :func:`check.violations` first, as every card
quill files does, and is filed by ``quill file``'s own writes (:func:`_filing._write`): born
marked, its mark removed last (R-BAL202).  A card holding a told thing's markers whose filing
has not finished is finished, found by them (R-BAL186), and none is filed beside it.  Its text
names where the thing was told in a code span, which links nothing, so no reference to the
private tracker is written into the public repository's timeline, and quotes the told words in
a code block, which mentions no one: the queue's comments mentioned the developer until
plan#3627, and a mention copied into a card would reach him from the tracker.

Its own module: the command module stands within ten lines of the 1,000 ``too-many-lines``
allows, and the tracker's within two.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial

from tools.quill._fields import HIGH
from tools.quill._filing import _NO_MILESTONE, _NOTHING_READ, _Filing, _write
from tools.quill._github import GitHub, issue_listing, paged
from tools.quill._state import filing_unfinished
from tools.quill._tracker import Card, Tracker
from tools.quill.check import TITLE_CAP, Draft, violations
from tools.quill.setup_tracker import PLAN, Place
from tools.update.alert import FAILED
from tools.update.app import ACTIONS, APP, marker, markers

#: The code repository whose update's record the pass reads: the tracker's organization's
#: ``Shekel``, which is public, so quill's App token, narrowed to the tracker
#: (``_tracker.Tracker.connect``), reads it (measured 2026-11-12 on PR 668).
CODE = (PLAN.owner, "Shekel")
#: The arc each card the pass files is in (plan#3626's spec).
ARC = "tooling"

#: What the pass reads of a comment: who wrote it, its text, its link and when.
_COMMENT = "author { login } body url createdAt"
#: A page of the code repository's open pull requests, each with its first page of comments.
PULLS = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 25, after: $after) {
      nodes { number title comments(first: 100) { nodes { %s } pageInfo { hasNextPage } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" % _COMMENT
#: A page of one pull request's comments, for one whose first page says another follows.
COMMENTS = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(first: 100, after: $after) {
        nodes { %s }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}""" % _COMMENT
#: A page of the code repository's open issues, each with who opened it.
ISSUES = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: 100, after: $after) {
      nodes { number title body url createdAt author { login } }
      pageInfo { hasNextPage endCursor }
    }
  }
}"""


def every_text(place: Place) -> str:
    """The listing of every card of ``place``, open and closed, its number and text, 100 a page
    (measured 2026-11-13: the slowest page of 37 took 1.1 s, inside GitHub's limit of about ten
    seconds a request)."""
    return (
        """query($after: String) { repository(owner: "%s", name: "%s") {
  issues(first: 100, after: $after, states: [OPEN, CLOSED]) {
    pageInfo { hasNextPage endCursor } nodes { number body } } } }"""
        % (place.owner, place.name)
    )


@dataclass(frozen=True)
class Told:
    """One thing the update told, as its card is filed from it: ``where``, the pull request or
    issue it was told on, as the card names it (``PR 669``); ``title``, that one's title;
    ``link``, where it was told, the comment's or the issue's address; ``said``, what it told,
    word for word; ``when``, when, as GitHub gives it; and ``marks``, each marker it holds, in
    its order."""

    where: str
    title: str
    link: str
    said: str
    when: str
    marks: tuple[str, ...]


def _comments(github: GitHub, pull: dict) -> list[dict]:
    """Every comment of ``pull``, an open pull request as :data:`PULLS` reads it: its first
    page, or, where that page says another follows, every page read again
    (:data:`COMMENTS`)."""
    if not pull["comments"]["pageInfo"]["hasNextPage"]:
        return pull["comments"]["nodes"]
    owner, name = CODE
    return paged(partial(github.graphql, COMMENTS, owner=owner, name=name,
                         number=pull["number"]),
                 lambda answer: answer["repository"]["pullRequest"]["comments"],
                 f"the comments of {owner}/{name}'s pull request {pull['number']}")[0]


def _by(node: dict, login: str) -> bool:
    """Whether ``node``, a comment or issue, was written by the bot GraphQL names ``login``; a
    deleted author, which GitHub answers as null, is no bot."""
    return (node["author"] or {}).get("login") == login


def _alert(issue: dict) -> Told:
    """The alert's issue ``issue``, as told: its card holds the marker this pass makes from its
    number, as the alert writes none."""
    return Told(f"issue {issue['number']}", issue["title"], issue["url"], issue["body"] or "",
                issue["createdAt"], (marker(f"alert {issue['number']}"),))


def told(github: GitHub) -> list[Told]:
    """Everything the update told that is still open, oldest first: each comment of the App's
    on an open pull request of :data:`CODE` holding a marker, and each open issue there the
    alert opened (the module says which)."""
    owner, name = CODE
    found = []
    pulls, _ = paged(partial(github.graphql, PULLS, owner=owner, name=name),
                     lambda answer: answer["repository"]["pullRequests"],
                     f"the open pull requests of {owner}/{name}")
    for pull in pulls:
        for comment in _comments(github, pull):
            if _by(comment, APP) and (marks := markers(comment["body"])):
                found.append(Told(f"PR {pull['number']}", pull["title"], comment["url"],
                                  comment["body"], comment["createdAt"], marks))
    issues, _ = paged(partial(github.graphql, ISSUES, owner=owner, name=name),
                      lambda answer: answer["repository"]["issues"],
                      f"the open issues of {owner}/{name}")
    found += [_alert(issue) for issue in issues
              if _by(issue, ACTIONS) and issue["title"].startswith(FAILED)]
    return sorted(found, key=lambda item: item.when)


def _held(github: GitHub, place: Place) -> dict[int, frozenset[str]]:
    """Each card of ``place`` whose text holds a marker, open or closed, with the markers it
    holds (:func:`every_text`)."""
    held = {}
    for node in issue_listing(github, every_text(place), "every card's text"):
        if found := markers(node["body"] or ""):
            held[node["number"]] = frozenset(found)
    return held


def _title(item: Told) -> str:
    """The card's title: where ``item`` was told and that one's title, at most
    :data:`check.TITLE_CAP` characters: one longer is cut at the last word of that title that
    fits, or inside its first word where none does, three dots marking the cut."""
    head = f"Shekel {item.where}: "
    if len(title := head + item.title.strip()) <= TITLE_CAP:
        return title
    room = TITLE_CAP - len(head) - len("...")
    fits = item.title.strip()[:room + 1]
    kept = fits.rsplit(" ", 1)[0] if " " in fits else fits[:room]
    return head + kept.rstrip() + "..."


def _body(item: Told) -> str:
    """The card's text: where ``item`` was told, in a code span, which links nothing; its words
    but its markers, in a code block, which mentions no one, fenced by more backticks than any
    run in them; what filed the card and why; and its markers last."""
    words = item.said
    for mark in item.marks:
        words = words.replace(mark, "")
    words = "\n".join(line.rstrip() for line in words.strip().splitlines())
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall("`+", words)), default=0))
    return "\n".join([
        f"The weekly update told this on Shekel's {item.where}, \"{item.title}\", at "
        f"{item.when}: `{item.link}`", "", f"{fence}text", words, fence, "",
        "`quill sync` filed this card for it (ruling plan#3619), holding Priority High at the "
        "top of the board (ruling plan#3620). The words above say what waits; the session "
        "that claims this card takes it, and sets the card's Effort.", "", *item.marks])


def card_draft(item: Told) -> Draft:
    """The card filed for ``item``: a step at the top level, in :data:`ARC`, holding Priority
    High and no Effort (ruling plan#3620; the module says why)."""
    return Draft("step", _title(item), _body(item), (), ARC, priority=HIGH)


def _unfinished(item: Told, held: dict[int, frozenset[str]],
                cards: dict[int, Card]) -> Card | None:
    """The card an earlier pass began filing for ``item`` and did not finish (R-BAL186): the
    lowest numbered of ``cards`` still being filed (:func:`_state.filing_unfinished`) whose
    text holds ``item``'s markers alone; None for none."""
    return next((card for number, card in sorted(cards.items())
                 if filing_unfinished(card) and held[number] == set(item.marks)), None)


def _standing(item: Told, held: dict[int, frozenset[str]], cards: dict[int, Card]) -> set[str]:
    """Each of ``item``'s markers a card of ``cards`` holds whose filing is not unfinished: one
    filed, or closed since, shipped or dropped, so nothing is filed over it again."""
    return {mark for number, card in cards.items() if not filing_unfinished(card)
            for mark in held[number] & set(item.marks)}


def _holding(items: Iterable[Told], held: dict[int, frozenset[str]]) -> set[int]:
    """Each card of ``held`` whose text holds a marker of one of ``items``."""
    wanted = {mark for item in items for mark in item.marks}
    return {number for number, marks in held.items() if marks & wanted}


def file_waits(tracker: Tracker, dry_run: bool) -> int:
    """The pass (the module says what it reads and files): for each thing the update told,
    oldest first, whose markers some card holds whose filing is not unfinished, nothing; else
    its card filed, or one an earlier pass began finished, each write printed as it lands, or
    under ``dry_run`` printed as what would be written and not made.  Each card holding a told
    thing's marker is read by its number for whether its filing finished, as a listing may lag
    a write.  The number of REPORT lines, each a card :func:`check.violations` refuses, which
    makes ``sync`` exit 1.

    Raises:
        GitHubError, requests.RequestException, TrackerError: as the reads and the filing's
            writes raise them (a card filed but not read back, :func:`_filing._created`); a
            pass cut short leaves what the next finishes, its card found by its markers.
    """
    items = told(tracker.github)
    if not items:
        return 0
    held = _held(tracker.github, tracker.place)
    cards = tracker.cards(_holding(items, held))
    reports, filed = 0, set()
    for item in items:
        mine = {number: card for number, card in cards.items() if held[number] & set(item.marks)}
        if set(item.marks) <= _standing(item, held, mine) | filed:
            continue
        draft = card_draft(item)
        if problems := violations(draft):
            print(f"REPORT: the update's {item.where}, told at {item.when}, is filed no card: "
                  + "; ".join(problems))
            reports += 1
            continue
        earlier = _unfinished(item, held, mine)
        verb = "would " if dry_run else ""
        print(f"{verb}file the update's {item.where}, told at {item.when}, which no card holds "
              f"(ruling plan#3619): {draft.title!r}" if earlier is None else
              f"{verb}finish the filing of plan#{earlier.number}, which holds the update's "
              f"{item.where}, told at {item.when}")
        if not dry_run:
            _write(tracker, _Filing(draft, None, {}, _NOTHING_READ, None, _NO_MILESTONE,
                                    top=True), earlier, None)
        filed |= set(item.marks)
    return reports
