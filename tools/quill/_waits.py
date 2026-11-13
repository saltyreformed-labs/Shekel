"""``quill sync``'s pass over the weekly update's waits (rulings plan#3619, plan#3620,
plan#3722): a card filed for each pull request or issue the update told a thing on while no card
for it is open, and each later thing told there put on that card, so that no step of the update
depends on the developer reading a mention (plan#3552).

**What the update told is its own record**, read here and never judged again, so the queue
stays the one judge of what waits:

- each comment of the update App's (:data:`tools.update.app.APP`) on an open pull request of
  the code repository, :data:`CODE`, holding a marker (:func:`tools.update.app.markers`) for
  each thing the daily queue told (``tools/update/queue.py``): a pull request waiting for a
  card, red at its head, or removed from the merge queue there.  Every open pull request's
  comments are read, and only the App's count, a bot's by its type and not its login alone
  (:func:`tools.update.app.bot_of`), as the queue reads its own.
- each open issue of the code repository the update's alert opened (``tools/update/alert.py``):
  one by the workflow's own bot (:data:`tools.update.app.ACTIONS`) titled as the alert titles
  one (:data:`tools.update.alert.FAILED`).  The alert writes no marker, so this pass makes the
  one its card holds from the issue's number (:func:`_alert`).

**A card holds each thing told as where it was told and the queue's marker for it**, one to a
line of the pass's own form (:func:`told_line`) in the run of such lines that ends its text
(:func:`holds`).  A marker alone names what was told, not where: PR 669's two are digests of
the walk's refusals ``card pyproject.toml pins no version`` and ``card uv.lock changes shekel's
metadata``, which any pull request moving a capped requirement repeats (gunicorn 27's, first
offered 2026-11-23, the census of plan#3615).  So a thing is held for the pull request it was
told on, the queue's own scope: the queue tells a thing once on each pull request, reading
that one's comments alone.  And only a line ending a text holds anything: a marker or a line
quoted in prose, in a code span or in a code block, which its fence follows, holds nothing
(plan#3676's own text quoted all five of PR 667-669's on 2026-11-13).

**One open card for each pull request or issue** (ruling plan#3722): a thing told where no
card is open for it is filed a card holding every thing that comment told (one comment can
tell two things in one text, PR 669's, read 2026-11-12, which a card per thing could not title
apart); a later thing told where one is open is put on it, in a comment of quill's holding
that thing's lines as a card's text does (:func:`_carry`).  So a red lock refresh, told once
for each head and so each Monday it stays red, adds to its one card.  Once that card is closed,
shipped or dropped, a thing told there afresh is filed a card of its own, and what the closed
card holds stays held.

**What the cards hold is read from every card's text each pass**, open and closed
(:func:`_held`), and from quill's comments on each card for a pull request or issue the pass
read something told on (:func:`_carried`).  Measured 2026-11-13 07:56 UTC on the tracker:
3,655 cards, 37 pages of 100, 4.8 MB in 29.8 s, the slowest page 1.1 s.  GitHub's issue search
is no way in: the same day it found neither a marker's digest nor a head's short sha,
``8518a3f40f86``, in the App's comments on PR 667.  A label marking these cards would be a
second home of what their text holds (rule 14).  So a person who deletes a card's lines from
its text has what the card held filed again at the next pass.  The reads are made only in a pass
that finds something told.

**The card** (ruling plan#3620) is a step in the :data:`ARC` arc holding Priority High, at the
top of the board (:attr:`_filing._Filing.top`), so ``quill next`` hands it out after any Urgent
card and before every other; it holds no Effort, as no session estimated one, and the lane that
claims it sets one (``quill effort``).  It passes :func:`check.violations` first, as every card
quill files does, and is filed by ``quill file``'s own writes (:func:`_filing._write`): born
marked, its mark removed last (R-BAL202).  A card holding a told thing's lines whose filing
has not finished is finished, found by them (R-BAL186), and none is filed beside it.  Its text,
and each comment putting a later thing on it, names where the thing was told in a code span,
which links nothing, so that the private tracker writes no reference into the public
repository's timeline, as a link there might (unmeasured: the reason, not a measured cost).
It quotes the title there and the told words in a code block, which mentions no one: the
queue's comments mentioned the developer until plan#3627, and a title can hold a mention as
words can, which copied into a card would reach its person from the tracker.

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
from tools.update.app import ACTIONS, APP, bot_of, marker, markers

#: The code repository whose update's record the pass reads: the tracker's organization's
#: ``Shekel``, which is public, so quill's App token, narrowed to the tracker
#: (``_tracker.Tracker.connect``), reads it (measured 2026-11-12 on PR 668).
CODE = (PLAN.owner, "Shekel")
#: The arc each card the pass files is in (plan#3626's spec).
ARC = "tooling"
#: The line holding one thing told: where it was told, as :class:`Told` names it, then the
#: queue's marker for it (:func:`tools.update.app.marker`, its one form), each an HTML comment,
#: which GitHub renders as nothing.
_TOLD = "<!-- quill: told on {} --> {}"
#: :data:`_TOLD` read back: where, and what stands for the marker, which :func:`holds` checks
#: is one marker whole.
_TOLD_LINE = re.compile(
    re.escape(_TOLD).replace(r"\{\}", "(.+?)", 1).replace(r"\{\}", "(.+)", 1))
#: One thing told as a card holds it: where it was told and the queue's marker for it.
Thing = tuple[str, str]

#: What the pass reads of a comment: who wrote it, a bot or a person
#: (:func:`tools.update.app.bot_of`), its text, its link and when.
_COMMENT = "author { __typename login } body url createdAt"
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
      nodes { number title body url createdAt author { __typename login } }
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

    @property
    def things(self) -> frozenset[Thing]:
        """Each thing it told, as a card holds it (:func:`holds`)."""
        return frozenset((self.where, mark) for mark in self.marks)


def told_line(where: str, mark: str) -> str:
    """The line holding the thing told on ``where`` (``PR 669``, ``issue 680``) that ``mark``,
    the queue's marker, names: the one form :func:`holds` reads."""
    return _TOLD.format(where, mark)


def holds(text: str) -> frozenset[Thing]:
    """Each thing told that ``text``, a card's or a comment's, holds: one to a line of
    :func:`told_line`'s form in the run of such lines ending it, blank lines after them aside.
    No other line holds anything, so a line or a marker quoted in prose, in a code span, or in
    a code block, indented or fenced, is not read."""
    found = set()
    for row in reversed(text.rstrip().splitlines()):
        match = _TOLD_LINE.fullmatch(row.rstrip())
        if match is None or markers(match[2]) != (match[2],):
            break
        found.add((match[1], match[2]))
    return frozenset(found)


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
            if bot_of(comment) == APP and (marks := markers(comment["body"])):
                found.append(Told(f"PR {pull['number']}", pull["title"], comment["url"],
                                  comment["body"], comment["createdAt"], marks))
    issues, _ = paged(partial(github.graphql, ISSUES, owner=owner, name=name),
                      lambda answer: answer["repository"]["issues"],
                      f"the open issues of {owner}/{name}")
    found += [_alert(issue) for issue in issues
              if bot_of(issue) == ACTIONS and issue["title"].startswith(FAILED)]
    return sorted(found, key=lambda item: item.when)


def edit_refusal(label: str, old: str, new: str) -> str | None:
    """Why ``new`` may not replace ``old`` as the text of the card ``label`` names: it drops a
    line holding a thing told (:func:`holds`) that ``old`` holds, which renders as nothing, so
    the next pass would file that thing again while it is still told (finding 5 of
    plan#3613's review); None when it keeps each one.  ``quill edit``'s refusal
    (``quill._text_refusal``), fail closed: a refusal costs a re-run, a dropped line a second
    card at the top of the board."""
    if not (dropped := holds(old) - holds(new)):
        return None
    lines = "\n  ".join(told_line(*thing) for thing in sorted(dropped))
    return (f"{label}'s text ends with lines holding what the weekly update told, which `quill "
            f"sync` reads (ruling plan#3619), and this text drops {len(dropped)} of them, so "
            "the next pass would file each again: end the new text with them as they stand, "
            f"one to a line:\n  {lines}")


def _held(github: GitHub, place: Place) -> dict[int, frozenset[Thing]]:
    """Each card of ``place`` whose text holds a thing told (:func:`holds`), open or closed,
    with each it holds (:func:`every_text`)."""
    held = {}
    for node in issue_listing(github, every_text(place), "every card's text"):
        if found := holds(node["body"] or ""):
            held[node["number"]] = found
    return held


def _carried(tracker: Tracker, numbers: Iterable[int]) -> dict[int, frozenset[Thing]]:
    """Each thing told that quill's comments on each card of ``numbers`` hold, by number: the
    later things the pass put on it (:func:`_carry`).  A person's comment holds nothing, as
    only quill writes these lines."""
    return {number: frozenset(thing for comment in comments
                              if comment.author == tracker.app_login
                              for thing in holds(comment.body))
            for number, comments in tracker.comments_of(numbers).items()}


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


def _quoted(item: Told, *above: str) -> list[str]:
    """``item``'s words but its markers, each line of ``above`` before them, as lines of a code
    block, which mentions no one, fenced by more backticks than any run in them."""
    words = item.said
    for mark in item.marks:
        words = words.replace(mark, "")
    words = "\n".join(line.rstrip() for line in "\n".join([*above, words.strip()]).splitlines())
    fence = "`" * max(3, 1 + max((len(run) for run in re.findall("`+", words)), default=0))
    return [f"{fence}text", words, fence]


def _lines(item: Told) -> list[str]:
    """The lines holding each thing ``item`` told (:func:`told_line`), in its markers' order."""
    return [told_line(item.where, mark) for mark in item.marks]


def _body(item: Told) -> str:
    """The card's text: where ``item`` was told, in a code span, which links nothing; the title
    there and its words, in a code block (:func:`_quoted`); what filed the card and why; and
    its lines last."""
    return "\n".join([
        f"The weekly update told this on Shekel's {item.where} at {item.when}: `{item.link}`. "
        "The title there, then what was told:", "", *_quoted(item, item.title.strip(), ""), "",
        "`quill sync` filed this card for it (ruling plan#3619), holding Priority High at the "
        "top of the board (ruling plan#3620). The words above say what waits; the session "
        "that claims this card takes it, and sets the card's Effort.", "", *_lines(item)])


def _later(item: Told) -> str:
    """The comment putting ``item`` on the card open for where it was told: where, in a code
    span; its words in a code block (:func:`_quoted`); why it is a comment; its lines last."""
    return "\n".join([
        f"The weekly update told this later on Shekel's {item.where}, at {item.when}: "
        f"`{item.link}`", "", *_quoted(item), "",
        f"`quill sync` put it on this card, the one open for {item.where}, rather than filing "
        "another (ruling plan#3722).", "", *_lines(item)])


def card_draft(item: Told) -> Draft:
    """The card filed for ``item``: a step at the top level, in :data:`ARC`, holding Priority
    High and no Effort (ruling plan#3620; the module says why)."""
    return Draft("step", _title(item), _body(item), (), ARC, priority=HIGH)


def _unfinished(item: Told, held: dict[int, frozenset[Thing]],
                mine: dict[int, Card]) -> Card | None:
    """The card an earlier pass began filing for ``item`` and did not finish (R-BAL186): the
    lowest numbered of ``mine`` still being filed (:func:`_state.filing_unfinished`) whose
    text holds ``item``'s things alone; None for none."""
    return next((card for number, card in sorted(mine.items())
                 if filing_unfinished(card) and held[number] == item.things), None)


def _standing(mine: dict[int, Card], held: dict[int, frozenset[Thing]],
              carried: dict[int, frozenset[Thing]]) -> set[Thing]:
    """Each thing told that a card of ``mine`` whose filing is not unfinished holds, in its
    text or in quill's comments on it: one filed, or closed since, shipped or dropped, so
    nothing is filed or put over it again."""
    return {thing for number, card in mine.items() if not filing_unfinished(card)
            for thing in held[number] | carried.get(number, frozenset())}


def _open(mine: dict[int, Card]) -> Card | None:
    """The card of ``mine`` open for where a thing was told, its filing finished (ruling
    plan#3722): the lowest numbered, should a person have filed a second; None for none."""
    return next((card for _, card in sorted(mine.items())
                 if card.is_open and not filing_unfinished(card)), None)


def _holding(items: Iterable[Told], held: dict[int, frozenset[Thing]]) -> set[int]:
    """Each card of ``held`` holding a thing told where one of ``items`` was: every card for a
    pull request or issue the pass read something told on."""
    places = {item.where for item in items}
    return {number for number, things in held.items() if places & {where for where, _ in things}}


def _file(tracker: Tracker, item: Told, draft: Draft, earlier: Card | None,
          dry_run: bool) -> int | None:
    """``item``'s card filed from ``draft``, or ``earlier``, the one an earlier pass began for
    it, finished, each write printed as it lands; under ``dry_run`` printed as what would be
    written and not made.  The card's number, None under ``dry_run``."""
    verb = "would " if dry_run else ""
    print(f"{verb}file the update's {item.where}, told at {item.when}, which no card holds "
          f"(ruling plan#3619): {draft.title!r}" if earlier is None else
          f"{verb}finish the filing of plan#{earlier.number}, which holds the update's "
          f"{item.where}, told at {item.when}")
    if dry_run:
        return None
    return _write(tracker, _Filing(draft, None, {}, _NOTHING_READ, None, _NO_MILESTONE,
                                   top=True), earlier, None).number


def _carry(tracker: Tracker, item: Told, number: int | None, dry_run: bool) -> None:
    """``item`` put on card ``number``, the one open for where it was told, in a comment of
    quill's (:func:`_later`, ruling plan#3722), printed as it lands; under ``dry_run`` printed
    as what would be written and not made, ``number`` None for the card the same dry run
    would file for it."""
    if number is None:
        print(f"would put the update's {item.where}, told at {item.when}, on the card this "
              "pass would file for it above (ruling plan#3722)")
        return
    print(f"{'would ' if dry_run else ''}put the update's {item.where}, told at {item.when}, "
          f"on plan#{number}, the card open for it (ruling plan#3722)")
    if not dry_run:
        tracker.comment(number, _later(item))


def file_waits(tracker: Tracker, dry_run: bool) -> int:
    """The pass (the module says what it reads and files): for each thing the update told,
    oldest first, nothing where a card whose filing is not unfinished holds it; else put on
    the card open for where it was told (:func:`_carry`), or, with none open, its card filed,
    or one an earlier pass began finished (:func:`_file`).  Each card for where a thing was
    told is read by its number, for whether it is open and its filing finished, as a listing
    may lag a write, and its comments with it.  The number of REPORT lines, each a card
    :func:`check.violations` refuses, which makes ``sync`` exit 1.  A pass finishing a filing
    an earlier one began leaves ``sync`` exiting 1 too: its own REPORT of that card, unfinished
    as it read it, is printed before the pass runs, and the next ``sync``, reading it
    finished, makes none.

    Raises:
        GitHubError, requests.RequestException, TrackerError: as the reads and the writes
            raise them (a card filed but not read back, :func:`_filing._created`); a pass cut
            short leaves what the next finishes, its card found by its lines.
    """
    items = told(tracker.github)
    if not items:
        return 0
    held = _held(tracker.github, tracker.place)
    cards = tracker.cards(_holding(items, held))
    carried = _carried(tracker, cards)
    reports, done, open_for = 0, set(), {}
    for item in items:
        mine = {number: card for number, card in cards.items()
                if item.where in {where for where, _ in held[number]}}
        if item.things <= _standing(mine, held, carried) | done:
            continue
        if item.where not in open_for and (card := _open(mine)) is not None:
            open_for[item.where] = card.number
        if item.where in open_for:
            _carry(tracker, item, open_for[item.where], dry_run)
        elif problems := violations(draft := card_draft(item)):
            print(f"REPORT: the update's {item.where}, told at {item.when}, is filed no card: "
                  + "; ".join(problems))
            reports += 1
            continue
        else:
            open_for[item.where] = _file(tracker, item, draft, _unfinished(item, held, mine),
                                         dry_run)
        done |= item.things
    return reports
