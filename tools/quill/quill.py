"""The ``quill`` command: how every session reads and writes Shekel's plan in its private tracker.

The plan lives in the private repository ``saltyreformed-labs/shekel-plan``
(ruling ``balance:R-BAL170``): each step, finding, ruling and question is one
issue, a "card", and one board holds the steps and questions in the order the
developer drags them (``R-BAL177``), each step's tasks nested under it outside that order,
in its card's order of its tasks (rulings plan#1814, plan#1868).  Every write goes through quill's
GitHub App, and every card a session files passes :mod:`check` first.

Whether a card SHIPPED is git's answer, never the card's state: its ship
marker ``plan/ships/N`` in the merge of ``dev`` and ``main`` (ruling
``balance:R-BAL359``, :mod:`_git`).  The card's open or closed state is
display, which ``sync`` writes from git.  A card is CLAIMED while the code
repository's ``origin`` holds a branch ``card/N`` whose work neither ``dev``
nor ``main`` holds: ``claim`` makes it, a commit adding the card's marker
pushed create-only, and ``release`` keeps its work on ``released/N/<instant>``
and deletes it.  So every command that asks git -- ``next``, ``show``,
``claim``, ``release``, ``move``, ``drop``, ``sync``, and ``file`` given an
owner or a parent or finding a card of its own closed while still marked --
first reads it once (:func:`_git.read`): it lists the ``card/N`` branches
``origin`` holds, then fetches ``dev``, ``main`` and each of them, which moves
this checkout's ``origin/dev``, ``origin/main`` and ``origin/card/N``.
(``move`` and ``drop`` ask it whether a step is split: a leaf dropped before
its filing finished is no leaf unless git says it shipped, ``_state.leaves``.)
A TASK (ruling ``balance:R-BAL386``) is a piece of one step or finding, a sub-issue
of it done when its file ``plan/tasks/N`` is in git (``_state.done_tasks``):
``file task`` files one, on the board under a step the board holds (ruling plan#1814,
``_command.on_board``; ``sync`` puts on one that is not, ruling plan#1842), ``claim`` files a
card's two finishing tasks (ruling plan#1875, ``_filing.finishing_filings``),
``start`` begins it with a commit naming it (ruling plan#1872), the commit hook stamps each
commit of the piece with it (:mod:`commit_hook`), ``task-file`` writes its file for the commit
that finishes the piece (:mod:`_tasks`), and ``sync``, ``drop`` and ``show`` read it; ``sync``
writes the board's Status of each card from git (:mod:`_status`).
A FINDING fixed inside its owner step's pull request ships in it (ruling plan#1777):
``finding-file`` writes its ship marker on its owner's branch, for the commit that fixes it,
and ``hand`` hands an open finding from its one owner to another step, a step a branch
builds (ruling plan#1784, :mod:`_findings`); ``block``, ``drop`` and the split keep that one
owner, and CI's landing check refuses the owner's pull request while it owes one neither
marked nor handed on, and a leaf's while a step above it owns one (``landing``, rule 7).
A LEAF (``file step --parent``) is filed in its split step's milestone while the split
step holds one, which the filing then clears; else in none, the milestones its leaves
hold named for a person to set one on the web (``_filing._leaf_milestone``).  A leaf
dropped before its filing finished gives it back (``_unsplit``).
``sync`` runs every three hours on this host (``shekel-plan-sync.timer``, its units' sources in
``/opt/docker/scripts``) from its own clone ``~/projects/shekel-plan-sync`` at dev's tip; a run
that fails, or exits 1 on a REPORT, pushes its last fifteen log lines (plan#1809), and ``next``
says when sync is behind.  ``watch`` runs every minute from the same clone, never beside
``sync`` (``shekel-plan-watch.timer``): the same producer over each card whose branch moved
since its last pass, so the board shows a push within a minute (rulings plan#1871, plan#1874,
:mod:`_watch`).  ``answer`` answers the developer's comments on a card, and ``next``
names each claimed card, and each task under one, holding one no answer has followed
(plan#1822, ruling plan#1924, :mod:`_comments`).  ``note`` posts a stop's note on the task a
claimed card's next session starts and prints its prompt (rulings plan#1921, plan#1922,
:mod:`_notes`).
``pr`` opens a card's pull request into ``dev``, its text naming the card so the card links it,
and ``file question --owner`` links a question to the step it is asked for, a link its ruling
keeps (plan#1861's item 10, :mod:`_pr`, :mod:`_filing`).

Usage, from the repository root (``plan#N`` or ``N`` names a card)::

    python -m tools.quill.quill next [--arc ARC]
    python -m tools.quill.quill claim plan#N [--here]
    python -m tools.quill.quill release plan#N
    python -m tools.quill.quill show plan#N | OLD-ID
    python -m tools.quill.quill file step --arc ARC --title NAME --body-file SPEC [--parent plan#P]
    python -m tools.quill.quill file finding --arc ARC --title NAME --owner plan#S --text SENTENCE
    python -m tools.quill.quill file ruling --arc ARC --title NAME
                                (--owner plan#S --question-file Q | [--owner plan#S]
                                 --from-question plan#Q) --answer-file A
    python -m tools.quill.quill file question --arc ARC --title NAME --body-file QUESTION
                                             [--owner plan#S]
    python -m tools.quill.quill file task --parent plan#N --title NAME --body-file PIECE
                                         [--before plan#T]
    python -m tools.quill.quill start plan#T
    python -m tools.quill.quill task-file plan#T
    python -m tools.quill.quill finding-file plan#F
    python -m tools.quill.quill hand plan#F --to plan#S
    python -m tools.quill.quill edit plan#N [--title NAME] [--body-file TEXT]
    python -m tools.quill.quill comment plan#N --body-file TEXT
    python -m tools.quill.quill answer plan#N --body-file TEXT
    python -m tools.quill.quill note plan#N --body-file NOTE
    python -m tools.quill.quill block plan#N --by plan#M [--remove]
    python -m tools.quill.quill move plan#N (--top | --bottom | --after plan#M)
    python -m tools.quill.quill drop plan#N --why REASON
    python -m tools.quill.quill sync [--dry-run]
    python -m tools.quill.quill watch [--dry-run]
    python -m tools.quill.quill pr plan#N --title TITLE --body-file TEXT
    python -m tools.quill.quill spec-history plan#N [--since BRANCH_OR_DATE]
    python -m tools.quill.quill spec-revert plan#N --to EDIT_ID
    python -m tools.quill.quill export --to DIR

Exit status: 0 done (for ``watch``, its REPORT lines printed: they alert from ``sync``, ruling
plan#1907); 1 refused, with nothing written (or, for ``sync``, a
REPORT a person must act on); 2 a call failed -- GitHub, git, the network, or
a file the command reads or writes (the App's credentials, a ``--body-file``,
``export``'s file) -- or the command line itself is not one of the forms above
(argparse's usage error), before anything is read.  Every write is printed as it
lands, so after a failure the output says what was written; ``file`` run again finishes a filing
a failure cut short (R-BAL186): every card it creates is marked ``filing``
until its last write, and a marked card is never offered (R-BAL202).  Run
again, it writes nothing over a card the earlier run filed that is still open,
still marked (refused when a person's or `quill drop`'s close ended it, unless it
is work git says shipped), or -- for a ruling, which its own filing closes -- blocked by
its owner (R-BAL395); a card closed after its filing finished is not looked for, so the
command files another (unless a listing that lags its close still shows it open).
``edit`` run again writes only the parts that differ, so it finishes an edit a
failure cut short.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import requests

from tools.ci.arcs import ARCS, REPO
from tools.ci.gitcmd import DEV, MAIN, GitError, tracking
from tools.quill import (
    _comments,
    _filing,
    _findings,
    _git,
    _notes,
    _pr,
    _status,
    _sync,
    _tasks,
    _watch,
)
from tools.quill._command import (
    _label,
    _one,
    _read,
    _unfinished,
    card_number,
    with_closure,
)
from tools.quill._exits import Refused, exit_status
from tools.quill._export import cmd_export
from tools.quill._filing import cmd_file
from tools.quill._github import CredentialsError, GitHubError
from tools.quill._show import cmd_show
from tools.quill._state import (
    STALE_CLAIM,
    SyncPlan,
    Unsplit,
    above,
    drop_shows,
    drop_unlinks,
    filing_unfinished,
    holder,
    is_container,
    is_work,
    leaves_below,
    left_bare,
    never_offered,
    never_split,
    next_step,
    outside_reports,
    release_hint,
    resolved,
    stale_claims,
    sync_plan,
    sync_unsplits,
    unfinished_reports,
    unshipped_shown_done,
    unsplit,
)
from tools.quill._tracker import Card, Tracker, TrackerError
from tools.quill.check import (
    BODY,
    TITLE,
    Draft,
    in_ruling_shape,
    normalized,
    violations,
)
from tools.quill.setup_tracker import (
    DEPLOY_TOGETHER_LABEL,
    FILING,
    IN_ORDER,
    MOVES_MONEY_LABEL,
    PLAN,
    TASK,
)

#: How a command reaches the plan's tracker: quill's App, narrowed to its repository.  The
#: release tool's command line reaches it the same way (``tools/release/release.py``).
CONNECT = partial(Tracker.connect, PLAN)


# -- next, claim, release -----------------------------------------------------

def cmd_next(args, tracker: Tracker, root: Path) -> int:
    """The first open step in the board's order that is unclaimed, unshipped and unblocked;
    then how far ``sync`` is behind, what it cannot offer that a person must act on, and last,
    after every line no further read can cost, each claimed card, and each task under one,
    holding a comment of the developer's that no answer of quill's has followed (plan#1822,
    ruling plan#1924, :func:`_comments.unanswered_lines`).

    **Sync is behind** (ruling plan#1815) while git says work shipped or a task is done that
    the tracker still shows open: the cards ``sync`` would close over what this reads, by
    its one producer (:func:`_state.sync_plan`), so the count clears at the next ``sync``, a
    schedule that stopped is seen, and a card a PERSON reopened, which ``sync`` reports
    and never closes, is never counted.  Each card so counted is read again by its number
    and decided again, as ``sync`` decides (:func:`_sync_reads`): the open listing can
    still show a card ``sync`` has just closed."""
    reads = _git.read(root)
    shipped = reads.shipped.cards
    cards = with_closure(tracker, {**tracker.open_cards(), **_unfinished(tracker)})
    order = [number for number, _ in tracker.board.order()]
    answer = next_step(order, cards, shipped, reads.claims, args.arc)
    scope = f" in {args.arc}" if args.arc else ""
    print(f"next{scope}: {_label(answer.card)}" if answer.card else f"next{scope}: nothing")
    closes = partial(sync_plan, shipped=shipped, claims=reads.claims, tasks=reads.shipped.tasks)
    if behind := closes(cards).close:
        behind = closes({**cards, **tracker.cards(behind)}).close
    if behind:
        print(f"sync is behind: {len(behind)} shipped cards still open")
    for card in answer.unplaced:
        print(f"NOT ON THE BOARD, so in no order: {_label(card)} -- "
              + ("`quill sync` puts it back on the board, unlinking the steps closed while "
                 "still being filed under it (R-BAL205)"
                 if never_split(card, cards, shipped) else
                 f"place it with `quill move plan#{card.number} --after plan#M` (or the board "
                 "lags a fresh write)"))
    for claim in stale_claims(reads.claims, datetime.now(UTC)):
        print(f"STALE CLAIM: plan#{claim.card} on {holder(claim)}: no commit for more than "
              f"{STALE_CLAIM.days} days -- {release_hint(claim)} keeps its work and frees it")
    for line in outside_reports(cards):
        print(f"OUTSIDE LINK: {line}")
    for line in unfinished_reports(cards, shipped):
        print(f"UNFINISHED FILING: {line}")
    for line in _comments.unanswered_lines(tracker, reads.claims, cards):
        print(f"UNANSWERED COMMENT: {line}")
    return 0


def cmd_claim(args, tracker: Tracker, root: Path,
              clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Claim a card (R-BAL359): a commit adding its ship marker, made on ``origin/dev`` --
    or with ``--here`` on the commit checked out, for work already started -- and pushed
    create-only as the branch ``card/N`` (:func:`_git.claim`), then checked out here as
    that branch, with no upstream; then its two finishing tasks filed under it, last in its
    order, unless they are filed already (ruling plan#1875): decided before the push
    (:func:`_filing.finishing_filings`), written after it.  Refused, with nothing written,
    for what is no work to claim (work shown done that a revert unshipped says to sync
    first); while ``card/N`` holds a claim (:func:`_claimed_again`); while ``origin`` holds
    a ``card/N`` already merged, which a create-only push cannot replace; while this clone
    already has a branch of that name, which the check-out would collide with; and for what
    the filing of a finishing task refuses.  The claim line is printed once the push lands,
    so a failed check-out or filing after it says what to run."""
    card = _one(tracker, args.card)
    reads = _git.read(root)
    shipped = reads.shipped.cards
    cards = with_closure(tracker, {card.number: card})
    if is_work(card, cards, shipped) and unshipped_shown_done(card, shipped):
        raise Refused(f"{_label(card)} is shown done, but git no longer says it shipped (a "
                      f"revert deleted {_git.marker(card.number)}): `quill sync` reopens it, "
                      "then claim it")
    if not card.is_open or not is_work(card, cards, shipped):
        raise Refused(f"{_label(card)} is not work a branch ships (open step or finding, "
                      "not a container)")
    if why := never_offered(card, cards, shipped):
        raise Refused(f"{_label(card)} is never offered as work: {why}")
    if card.number in shipped:
        raise Refused(f"{_label(card)} shipped in git: no work is left on it to claim")
    if card.number in reads.claims:
        return _claimed_again(tracker, root, card, cards, reads)
    branch = _git.card_branch(card.number)
    if tip := reads.merged.get(card.number):
        raise Refused(f"origin holds {branch} at {tip[:12]}, whose work {DEV} or {MAIN} already "
                      "holds: a card branch that outlived its merge, which a claim cannot "
                      "replace.  Delete it, with a lease on that tip, then claim again: "
                      f"`git push --force-with-lease=refs/heads/{branch}:{tip} origin "
                      f":refs/heads/{branch}`")
    if _git.has_branch(root, branch):
        raise Refused(f"this clone already has a branch {branch!r}, which a claim of "
                      f"plan#{card.number} checks out: delete or rename it first")
    finishing = _filing.finishing_filings(tracker, root, card, cards, reads)
    base = "HEAD" if args.here else tracking(DEV)
    try:
        sha = _git.claim(root, card.number, base, clock())
    except _git.ClaimTaken:
        raise Refused(f"origin holds {branch!r} already, though this command's read saw no "
                      f"branch of that name: another session claimed plan#{card.number} since "
                      f"(`quill show plan#{card.number}` names its claim)") from None
    print(f"claimed {_label(card)}: {branch} at {sha[:12]} on origin, adding "
          f"{_git.marker(card.number)} on {'the commit checked out' if args.here else DEV}")
    if failed := _git.check_out(root, card.number, sha):
        print(f"  not switched to {branch} ({failed}): the claim stands; run "
              f"`git switch {branch}`")
    else:
        print(f"  switched to {branch}")
    _filing.file_finishing(tracker, card, finishing)
    return 0


def _claimed_again(tracker: Tracker, root: Path, card: Card, cards: dict[int, Card],
                   reads: _git.Reads) -> int:
    """``claim`` of ``card``, which one of ``reads``' claims holds: refused, with nothing
    written, but where that claim's branch is checked out here and some of the card's
    finishing tasks are not filed (a failure cut the claim short, or the claim came before
    ruling plan#1875), the claim is finished: they are filed
    (:func:`_filing.file_finishing`, R-BAL186)."""
    held = reads.claims[card.number]
    finishing = (_filing.finishing_filings(tracker, root, card, cards, reads)
                 if _git.checked_out(root) == held.branch else [])
    if not finishing:
        raise Refused(f"plan#{card.number} is already claimed: {holder(held)}")
    print(f"plan#{card.number} is claimed already, {holder(held)}, and checked out here; its "
          "finishing tasks are not all filed, so this finishes its claim")
    _filing.file_finishing(tracker, card, finishing)
    return 0


def cmd_release(args, _tracker: Tracker, root: Path,
                clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Release a card's claim, whoever made it: its branch's work pushed to
    ``released/N/<instant>``, then ``card/N`` deleted from origin with a lease on the tip
    this command read (:func:`_git.release`), so neither that work nor a push made since
    is lost.  This clone's own ``card/N`` is left as it is, and named."""
    claim = _git.read(root).claims.get(args.card)
    if claim is None:
        raise Refused(f"plan#{args.card} is not claimed: origin holds no "
                      f"{_git.card_branch(args.card)} whose work neither {DEV} nor {MAIN} holds")
    kept = _git.release(root, args.card, claim.sha, clock())
    print(f"released plan#{args.card}, {holder(claim)}: its work is kept on {kept}")
    if _git.has_branch(root, claim.branch):
        print(f"  this clone's branch {claim.branch} is left as it is")
    return 0


# -- edit, comment --------------------------------------------------------------

def _card_refusal(card: Card) -> str | None:
    """Why ``card`` is never rewritten, whatever its new title or text; None when it may be.

    A ruling is the developer's record (R-BAL220).  A card carrying the filing
    mark is found again by its ``quill file`` command through its kind, title
    and text (R-BAL186, R-BAL202): rewritten, an unfinished filing would be
    filed a second time, and so would one closed while still marked, which that
    command now files nothing over (a drop refused, a shipped card filed
    already).  No way out by hand is offered for that one: removing its stale
    mark re-arms the command to file it anew, and, for a leaf still linked under
    its split step until ``sync`` unlinks it, makes it a dropped leaf, so
    R-BAL187 could drop a step nobody built (``_state.sync_unsplits``, review
    M4).  A closed card with no mark is edited: no command looks for it by its
    text (``_filing._same_filing``), and R-BAL174's review reads any change to
    it in ``spec-history``.
    """
    if card.kind == "ruling":
        return (f"{_label(card)} is a ruling, the developer's question and answer word for "
                "word: edit never rewrites one, its title or its text; a correction is a new "
                "ruling that names the one it amends (R-BAL220)")
    if FILING in card.labels:
        return (f"{_label(card)} carries the {FILING!r} mark (R-BAL202): its `quill file` "
                "command finds it by its kind, title and text, so a new title or text would "
                "make that command file a second card (R-BAL186); "
                + (f"finish its filing first (`quill show plan#{card.number}` says how)"
                   if filing_unfinished(card) else
                   "closed while still marked, it keeps them so that command files nothing "
                   "over it, and so it is not edited"))
    return None


def _text_refusal(card: Card, path: str, body: str) -> str | None:
    """Why ``body``, read from ``path``, is not ``card``'s new text; None when it may be.

    A text that is only space would blank the card's spec or question: edit
    never blanks a text (a finding's, the check refuses too).  A question's
    text in a ruling's shape is what only its conversion writes: ``_filing``
    reads such an edit of quill's as an earlier conversion's
    (``_Asked.converted_before``).
    """
    if not body.strip():
        return f"{path} holds no text: edit never blanks a card's text"
    if card.kind == "question" and in_ruling_shape(body):
        return (f"{_label(card)} is a question, and this text has a ruling's shape (\"Question: "
                "...\" then \"Answer: ...\"), which only its conversion writes: `quill file "
                f"ruling --from-question plan#{card.number}` turns an answered question into "
                "its ruling")
    return None


def cmd_edit(args, tracker: Tracker, _root: Path) -> int:
    """Rewrite a card's title, its text, or both, through the tracker's own writes
    (:meth:`_tracker.Tracker.retitle`, :meth:`_tracker.Tracker.set_body`), after the check
    ``file`` runs on what it writes: :func:`check.violations`, grading the parts written
    -- a title (R-BAL180), a finding's one sentence (R-BAL136) -- and the card's type and
    arc label, which every written card keeps.  Refused, with nothing written: with
    nothing to write, before any card is read; for the card itself
    (:func:`_card_refusal`), before ``--body-file`` is read; and for its new text
    (:func:`_text_refusal`).  Each part is written only when it differs from the card's
    as the rules read it (:func:`check.normalized`), each write printed as it lands, so
    the same command run again finishes an edit a failure cut short; R-BAL174's review
    reads a text's change in ``spec-history``."""
    if args.title is None and args.body_file is None:
        raise Refused("nothing to edit: pass --title, --body-file or both")
    card = _one(tracker, args.card)
    if why := _card_refusal(card):
        raise Refused(why)
    body = None
    if args.body_file is not None:
        body = _read(args.body_file)
        if why := _text_refusal(card, args.body_file, body):
            raise Refused(why)
    written = {part for part, new in ((TITLE, args.title), (BODY, body)) if new is not None}
    draft = Draft(card.kind, card.title if args.title is None else args.title, body, card.labels)
    if problems := violations(draft, written):
        raise Refused("not edited:\n  " + "\n  ".join(problems))
    number = card.number
    if args.title is not None:
        if args.title.strip() == card.title.strip():
            print(f"plan#{number}'s title is already {card.title!r}: not written")
        else:
            tracker.retitle(number, args.title)
            print(f"retitled plan#{number}: {card.title!r} -> {args.title!r}")
    if body is not None:
        if normalized(tracker.body(number)) == normalized(body):
            print(f"plan#{number}'s text is already this text: not written")
        else:
            tracker.set_body(number, body)
            print(f"plan#{number}'s text replaced (`quill spec-history plan#{number}` shows "
                  "the change)")
    return 0


def cmd_comment(args, tracker: Tracker, _root: Path) -> int:
    """Add a comment to a card through :meth:`_tracker.Tracker.comment`, its text read
    from ``--body-file``: a note beside the card's own text, which it never changes, so a
    ruling takes one too.  A note is no answer: ``quill answer`` answers a person's comments
    (plan#1822).  A text that is only space is refused before any card is read."""
    text = _read(args.body_file)
    if not text.strip():
        raise Refused(f"{args.body_file} holds no text: a comment says something")
    card = _one(tracker, args.card)
    tracker.comment(card.number, text)
    print(f"commented on {_label(card)}")
    return 0


# -- block, move, drop ----------------------------------------------------------

def cmd_block(args, tracker: Tracker, root: Path) -> int:
    """Record (or with ``--remove``, delete) that one card is blocked by another; for an open
    finding, only as its one owner allows (:func:`_findings.refuse_block`, ruling plan#1784)."""
    if args.card == args.by:
        raise Refused("a card cannot block itself")
    blocker = _one(tracker, args.by)
    card = _one(tracker, args.card)
    if card.kind == "finding":
        _findings.refuse_block(tracker, root, card, blocker, args.remove)
    if args.remove:
        if blocker.number not in card.blocked_by:
            raise Refused(f"plan#{card.number} is not blocked by plan#{blocker.number}")
        tracker.unblock(card.number, blocker)
        print(f"plan#{card.number} is no longer blocked by plan#{blocker.number}")
    else:
        tracker.block(card.number, blocker)
        print(f"plan#{card.number} is blocked by plan#{blocker.number}")
    return 0


def _not_in_order(card: Card) -> str:
    """Why nothing is moved after ``card``, which the board's order read does not list: a step
    or question off the board, or on it but not yet listed by a read that lags a write
    (``_tracker``'s doctrine); any other kind, a task among them, is in no place of it."""
    if card.kind not in IN_ORDER:
        return (f"plan#{card.number} is not in the board's order, its steps and questions "
                "(R-BAL177)")
    if card.board_item is None:
        return (f"{_label(card)} is not on the board, so nothing is placed after it: put it "
                f"on first (`quill move plan#{card.number} --top`, `--bottom` or `--after`)")
    return (f"{_label(card)} is on the board, but the board's order read does not list it yet: "
            "the board lags a write; run this again")


def cmd_move(args, tracker: Tracker, root: Path) -> int:
    """Put a step or question at a place in the board's order."""
    card = _one(tracker, args.card)
    shipped = _git.read(root).shipped.cards
    cards = with_closure(tracker, {card.number: card})
    if card.kind not in IN_ORDER or not card.is_open or is_container(card, cards, shipped):
        raise Refused(f"the board's order holds open steps and questions only (a task nests "
                      f"under its step in no order, ruling plan#1814), and no step split into "
                      f"leaves (R-BAL177, R-BAL179): {_label(card)}")
    if filing_unfinished(card) and card.kind == "step":
        raise Refused(f"{_label(card)}'s filing has not finished (R-BAL202): finish it first.  "
                      "If it is a leaf -- one whose link has not landed reads as a top-level "
                      "step -- its `quill file` command moves it again after this move")
    order = tracker.board.order()
    items = dict(order)
    if args.after is not None:
        if args.after not in items:
            raise Refused(_not_in_order(_one(tracker, args.after)))
        after = items[args.after]
    elif args.bottom:
        rest = [item for number, item in order if number != card.number]
        after = rest[-1] if rest else None
    else:
        after = None
    item = items.get(card.number)
    if item is None:
        item = tracker.board.add(card)
        print(f"  board: added {_label(card)} at the bottom")
    shown = tracker.board.place(item, after)
    print(f"moved {_label(card)}" + ("" if shown else
                                     " -- the board has not shown it yet; re-read it later"))
    return 0


def _drop(tracker: Tracker, card: Card, why: str, reads: _git.Reads) -> None:
    """Drop one piece of work: first each task under it its drop takes
    (:func:`_tasks.taken_by_drop`, R-BAL386), each dropped as this drops it; then the reason
    as a comment, then closed as not planned; each write printed as it lands, and a claim on
    it (one of ``reads``' claims) named.  Run again after a failure, it finds the tasks still
    to drop: one closed already is not taken again."""
    for task in _tasks.taken_by_drop(tracker, card, reads.shipped.tasks):
        _drop(tracker, task, why, reads)
    tracker.comment(card.number, f"Dropped: {why}")
    print(f"  commented on plan#{card.number}: Dropped: {why}")
    _close_dropped(tracker, card, reads.claims)


def _close_dropped(tracker: Tracker, card: Card, claims: dict[int, _git.Claim]) -> None:
    """Close a card whose reason is already on it as not planned, printed as it lands,
    and name a claim on it (one of ``claims``), which a drop leaves: its branch holds a
    person's work."""
    tracker.close(card.number, "not_planned")
    print(f"dropped {_label(card)}")
    if (claim := claims.get(card.number)) is not None:
        print(f"  its claim stays, {holder(claim)}: {release_hint(claim)} keeps its work "
              "and frees it")


def _unsplit(tracker: Tracker, undo: Unsplit, cards: dict[int, Card],
             dry_run: bool = False) -> None:
    """Take leaves whose filing never finished out of the split they began (R-BAL205,
    :func:`_state.unsplit` decides), each write printed as it lands, or under ``dry_run``
    printed as what would be written and not made: the split step put back on the board,
    and moved into a leaf's place, when it is left a plain step; its milestone given back
    (:func:`_give_back_milestone`); then each leaf unlinked.
    ``cards`` holds the split step and every card linked under it
    (:func:`with_closure`); ``quill drop`` and ``quill sync`` both take a split apart
    here."""
    split, would = cards[undo.step], "would be " if dry_run else ""
    item = split.board_item
    if undo.add:
        item = None if dry_run else tracker.board.add(split)
        print(f"  board: plan#{split.number} {would}added back at the bottom, a plain step again")
    if undo.after is not None:
        leaf, after = undo.after
        shown = dry_run or tracker.board.place(item, after)
        print(f"  board: plan#{split.number} {would}moved into plan#{leaf}'s place"
              + ("" if shown else " (the board has not shown it yet)"))
    _give_back_milestone(tracker, undo, dry_run)
    for number in undo.unlink:
        if not dry_run:
            tracker.remove_child(split.number, cards[number])
        print(f"  plan#{number} {would}unlinked from plan#{split.number}: its filing never "
              "finished, so it was never part of that split (R-BAL205)")


def _give_back_milestone(tracker: Tracker, undo: Unsplit, dry_run: bool) -> None:
    """Undo the milestone move of each leaf ``undo`` takes out of its split (X-cx S3a's
    review, MEDIUM 2): a leaf's create carried its split step's milestone, and its filing
    then cleared the split step's (``_filing._leaf_milestone``), so the split step, while
    it holds none, gets the leaf's back, and the leaf, never part of the split, leaves it;
    each write printed as it lands, or under ``dry_run`` printed as what would be written
    and not made.  Run again after a failure, it finds the split step holding one
    and takes the leaf's off.  A split step left with other leaves gets it back too, as it
    held it before this filing, and the next leaf filed under it moves it again."""
    held, would = tracker.milestones_of([undo.step, *undo.unlink]), "would be " if dry_run else ""
    for number in undo.unlink:
        if (moved := held.get(number)) is None:
            continue
        if held.get(undo.step) is None:
            if not dry_run:
                tracker.set_milestone(undo.step, moved)
            print(f"  plan#{undo.step} {would}put back in milestone #{moved}, from "
                  f"plan#{number}")
        if not dry_run:
            tracker.set_milestone(number, None)
        print(f"  plan#{number} {would}taken out of milestone #{moved}")


def _leave_split(tracker: Tracker, leaf: Card, cards: dict[int, Card],
                 shipped: frozenset[int]) -> None:
    """Take ``leaf``, dropped while its filing has not finished, out of the split it began,
    which it was never part of (R-BAL205), before its close: a split step the TOOL closed
    first shown as ``sync`` would show it once the drop is done
    (:func:`_state.drop_shows`), each write printed as it lands; then :func:`_unsplit`,
    the step put back on the board only when it will be open."""
    split = cards[leaf.parent]
    write = drop_shows(split, leaf, cards, shipped)
    if write == "reopen":
        tracker.reopen(split.number)
        print(f"  reopened plan#{split.number}: quill had closed it, and without "
              f"plan#{leaf.number} it still has work to do (R-BAL190)")
    elif write is not None:
        tracker.close(split.number, write)
        print(f"  closed plan#{split.number} again as {write.replace('_', ' ')}: what its "
              f"smaller steps say once plan#{leaf.number} is out of them (R-BAL190)")
    opened = split.is_open or write == "reopen"
    _unsplit(tracker, unsplit(split, (leaf.number,), cards, shipped,
                              tracker.board.order() if opened else None), cards)


def _refuse_drop(card: Card, cards: dict[int, Card], reads: _git.Reads) -> None:
    """Refuse to drop ``card``, no step split into smaller steps, when it is closed already, or
    is work git says shipped, or a task whose file is on ``dev`` or ``main``: each is undone
    by a revert.  A task closed on the web while its claim has begun it is still in progress
    in git, so the refusal says how its drop is recorded there (ruling plan#1900)."""
    if not card.is_open:
        claim = reads.claims.get(card.parent) if card.kind == TASK else None
        if claim is not None and card.number in claim.in_progress:
            raise Refused(f"{_label(card)} is already closed, and git still says it is in "
                          f"progress on {claim.branch}: reopen it on the web, then drop it "
                          f"here with {claim.branch} checked out, which records its drop in git "
                          "(ruling plan#1900)")
        raise Refused(f"{_label(card)} is already closed")
    if is_work(card, cards, reads.shipped.cards) and card.number in reads.shipped.cards:
        raise Refused(f"{_label(card)} shipped in git: undo it by reverting the change that "
                      f"shipped it, which deletes {_git.marker(card.number)}, not by a drop")
    if card.kind == TASK and card.number in reads.shipped.tasks:
        raise Refused(f"{_label(card)} is done in git ({_git.task_file(card.number)} is on "
                      f"{DEV} or {MAIN}): undo it by reverting the commit that wrote that "
                      "file, not by a drop")


def cmd_drop(args, tracker: Tracker, root: Path) -> int:
    """Retire work with no code: each card's reason as a comment, then closed as not planned.

    Work git says shipped is never dropped: undoing it is a revert of the change that
    shipped it, which deletes its marker; nor is a task whose file is on ``dev`` or ``main``,
    undone the same way; nor, until each is handed on, a step that owns an open finding, a
    split step any leaf of which, or which itself, owns one, or a step below one that owns
    one, which a drop of its last leaf would leave dropped (ruling plan#1784,
    :func:`_findings.refuse_orphaning`): a task's drop, or a leaf's still being filed, leaves
    the steps above as they were.  A card's drop drops each task under it still to do first
    (:func:`_drop`, R-BAL190's shape: the tool records the decision where the work is).
    A task its card's claim has begun is dropped in git first, on its card's branch checked
    out, so it is in progress no longer (:func:`_tasks.record_drop`, ruling plan#1900).
    A leaf dropped while its filing has not finished is first taken out of the
    split it began, which it was never part of (R-BAL205, :func:`_leave_split`):
    unlinked, and its split step, if it was the only leaf and will be open, a
    plain step again, on the board (in the leaf's place when the leaf is on
    it).  A split step the TOOL closed is first shown as ``sync`` would show it
    once the drop is done (:func:`_state.drop_shows`, review C2 LOW 6): reopened
    while it still has work to do, closed again as its other leaves say, or, a
    plain step a drop closed, left dropped.  One a PERSON closed keeps their
    close, and stays off the board.
    No drop leaves a link that is no leaf behind it (:func:`_state.drop_unlinks`):
    a step closed while still being filed and linked under the card dropped is
    unlinked before the card is closed, and so is every leaf still being filed
    that a split step's drop closes, after its close and before any step left
    bare is closed.  Left linked until the next ``sync`` (which reads every
    marked card), such a leaf would act on the plan as it stands meanwhile: a
    person reopening it would make it a leaf again and revive the step that was
    dropped (review C2 M1), and a person removing its stale mark would make it a
    dropped leaf, so R-BAL187 could drop a step nobody built (review C3 M1).
    A split step holds no decision of the tool's (R-BAL190):
    dropping one notes it on the split step first, then drops every leaf below
    it that is still work by git's answer -- not shipped and not dropped,
    whatever its card shows -- each with the reason, and names the shipped ones
    it leaves; its own state shows its leaves at the next ``sync``.  A step
    every leaf of which was still being filed is closed too, after them: closed
    while marked, they were never its leaves (:func:`_state.left_bare`).  Run again
    after a failure, it notes the split step again and drops the leaves still
    work (a leaf whose comment landed but not its close gets the reason twice).
    """
    card = _one(tracker, args.card)
    reads = _git.read(root)
    shipped = reads.shipped.cards
    cards = with_closure(tracker, {card.number: card})
    if not is_container(card, cards, shipped):
        _refuse_drop(card, cards, reads)
        leaf = card.kind == "step" and not filing_unfinished(card)
        _findings.refuse_orphaning(tracker, [card, *(above(card, cards) if leaf else ())],
                                   shipped)
        if card.kind == "step" and card.parent is not None and filing_unfinished(card):
            _leave_split(tracker, card, cards, shipped)
        for undo in drop_unlinks(card, cards, shipped, set()):
            _unsplit(tracker, undo, cards)
        if card.kind == TASK:
            _tasks.record_drop(tracker, card, root, args.why, reads)
        _drop(tracker, card, args.why, reads)
        return 0
    below = leaves_below(card, cards, shipped)
    done = [leaf for leaf in below if leaf.number in shipped]
    below = [leaf for leaf in below if not resolved(leaf.number, cards, shipped)]
    for leaf in done:
        print(f"  plan#{leaf.number} shipped in git, so it is not dropped")
    if not below:
        raise Refused(f"{_label(card)} has no leaf below it that is still work; its own state "
                      "shows its leaves, which `quill sync` writes")
    _findings.refuse_orphaning(tracker, [card, *below, *above(card, cards)], shipped)
    names = ", ".join(f"plan#{leaf.number}" for leaf in below)
    ending = {leaf.number for leaf in below if filing_unfinished(leaf)}
    bare = left_bare(card, cards, shipped, ending)
    tracker.comment(card.number, f"Dropped: {args.why} (its leaves still work: {names})")
    print(f"  commented on plan#{card.number}: dropping its leaves still work, {names}"
          + ("" if card in bare else "; it shows them at the next `quill sync`"))
    for leaf in below:
        _drop(tracker, leaf, args.why, reads)
    for undo in drop_unlinks(card, cards, shipped, ending):
        _unsplit(tracker, undo, cards)
    for step in bare:
        print(f"  plan#{step.number}: every leaf of it was still being filed, so no leaf "
              "carries its drop (R-BAL190): it is closed itself")
        if step.number == card.number:
            _close_dropped(tracker, step, reads.claims)
        else:
            _drop(tracker, step, args.why, reads)
    return 0


# -- sync -------------------------------------------------------------------------

def _undo_splits(tracker: Tracker, cards: dict[int, Card], shipped: frozenset[int],
                 changes: SyncPlan, dry_run: bool) -> None:
    """``sync``'s undo of each split a leaf closed while still being filed is linked under
    (:func:`_state.sync_unsplits`, R-BAL205, which reads the board's order and ``changes``,
    the state writes ``sync`` makes): each named, then made by :func:`_unsplit` -- under
    ``dry_run``, printed as what would be written and not made."""
    for undo in sync_unsplits(cards, shipped, changes, tracker.board.order()):
        names = ", ".join(f"plan#{number}" for number in undo.unlink)
        print(f"{'would ' if dry_run else ''}undo the split of {_label(cards[undo.step])} by "
              f"{names}, closed while still being filed (R-BAL205)")
        _unsplit(tracker, undo, cards, dry_run)


def cmd_sync(args, tracker: Tracker, root: Path) -> int:
    """Write each step's, finding's and task's open or closed state from git (display only,
    :func:`_sync.write_states`), then undo each split a leaf closed while still being filed is
    linked under (R-BAL205, :func:`_undo_splits`): after every state write and every REPORT and
    HISTORY line, so a failed undo costs none of them; run again, it finishes the undo.  Then
    put on the board each task whose step is on it and that is not (ruling plan#1842,
    :func:`_tasks.put_on_board`), and last write the board's Status of every card it holds
    from git (rulings plan#1873, plan#1905, :func:`_status.sync_statuses`): display of the
    plan, so a failed write costs nothing above it; run again, it makes the writes still owed.

    Prints a REPORT for each thing a person must fix in the tracker, which makes
    it exit 1, and a HISTORY line for each thing ``dev`` and ``main`` already hold
    wrongly, which no tracker write can change and so never fails it (R-BAL184): those
    about the cards it decides, and each entry under ``plan/ships`` or ``plan/tasks`` there
    that is no card's (:attr:`_git.Shipped.history`).

    What it reads: :func:`_sync.every_card`.  ``quill watch`` runs the same producer over
    the cards a moved branch names (:mod:`_watch`).
    """
    reads = _git.read(root)
    shipped = reads.shipped.cards
    decided = _sync.every_card(tracker, reads, _git.ever_marked(root))
    changes = _sync.write_states(tracker, decided, shipped, args.dry_run)
    for line in reads.shipped.history:
        print(f"HISTORY: {line}")
    _undo_splits(tracker, decided.cards, shipped, changes, args.dry_run)
    _tasks.put_on_board(tracker, decided.cards, args.dry_run)
    for line in (shown := _status.sync_statuses(tracker, decided.cards, reads, args.dry_run)):
        print(f"REPORT: {line}")
    return 1 if changes.reports or shown else 0


# -- spec-history, spec-revert ------------------------------------------------------

def _since(root: Path, text: str) -> datetime:
    """``--since``: an ISO date or time, or a branch (when its work started,
    :func:`_git.started`)."""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.fromisoformat(_git.started(root, text))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _diff(before: str, after: str, label: str) -> str:
    """A unified diff of two bodies."""
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        "before", label, lineterm="\n",
    ))


def cmd_spec_history(args, tracker: Tracker, root: Path) -> int:
    """Every saved change to a card's body in a window, for the review R-BAL174 requires."""
    _one(tracker, args.card)
    _, versions = tracker.edits(args.card)
    since = _since(root, args.since) if args.since else None
    shown = 0
    for index, version in enumerate(versions):
        if since and datetime.fromisoformat(version.edited_at) < since:
            continue
        before = versions[index - 1].body if index else ""
        what = "filed" if index == 0 else f"edit {version.edit_id}"
        print(f"== {what}, {version.edited_at} by {version.editor}")
        print(_diff(before, version.body, version.edited_at) or "(no change to the text)")
        shown += 1
    if not shown:
        last = versions[-1]
        print(f"plan#{args.card}'s body has not changed since {args.since} (last saved "
              f"{last.edited_at} by {last.editor})")
    return 0


def cmd_spec_revert(args, tracker: Tracker, _root: Path) -> int:
    """Restore a card's body to an earlier saved version, by hand.

    The body is read again just before the write, and the write is refused if
    it changed since the history was read: the diff printed is then still the
    change made.  (GitHub offers no conditional issue write, so a change in the
    instant between that read and the write is not seen.)
    """
    _one(tracker, args.card)
    body, versions = tracker.edits(args.card)
    chosen = [version for version in versions if version.edit_id == args.to]
    if not chosen:
        raise Refused(f"plan#{args.card} has no saved version {args.to!r}; "
                      f"`quill spec-history plan#{args.card}` lists them")
    if chosen[0].body == body:
        raise Refused(f"plan#{args.card}'s body already is that version")
    print(_diff(body, chosen[0].body, f"version {args.to}"))
    if tracker.body(args.card) != body:
        raise Refused(f"plan#{args.card} changed while its history was read; nothing was "
                      "written: run it again")
    tracker.set_body(args.card, chosen[0].body)
    print(f"restored plan#{args.card}'s body to the version saved {chosen[0].edited_at} "
          f"by {chosen[0].editor}")
    return 0


# -- the command line -----------------------------------------------------------------

def _file_parser(commands) -> None:
    """``file step|finding|ruling|question|task`` and each kind's arguments."""
    kinds = commands.add_parser("file", help="file a step, finding, ruling, question or task")
    sub = kinds.add_subparsers(dest="kind", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--arc", required=True, choices=ARCS)
    common.add_argument("--title", required=True, help="a short name (R-BAL178)")
    step = sub.add_parser("step", parents=[common], help="a step, or a leaf of one")
    step.add_argument("--body-file", required=True, help="its spec")
    step.add_argument("--parent", type=card_number, help="the step this leaf splits")
    step.add_argument("--label", action="append",
                      choices=(MOVES_MONEY_LABEL, DEPLOY_TOGETHER_LABEL))
    finding = sub.add_parser("finding", parents=[common], help="a defect in the code")
    finding.add_argument("--owner", type=card_number, required=True, help="its owner step")
    finding.add_argument("--text", required=True, help="one sentence, at most 400 characters")
    ruling = sub.add_parser("ruling", parents=[common], help="the developer's decision")
    ruling.add_argument("--owner", type=card_number,
                        help="its owner step; from a question that names one, that one")
    asked = ruling.add_mutually_exclusive_group(required=True)
    asked.add_argument("--question-file", help="his question, word for word")
    asked.add_argument("--from-question", type=card_number,
                       help="the question card he answered; it becomes the ruling")
    ruling.add_argument("--answer-file", required=True, help="his answer, word for word")
    question = sub.add_parser("question", parents=[common], help="for the developer to answer")
    question.add_argument("--body-file", required=True, help="the question")
    question.add_argument("--owner", type=card_number,
                          help="the step it is asked for, if one, which its ruling keeps")
    task = sub.add_parser(TASK, help="a piece of one step or finding: a commit in its card's "
                                     "pull request (its arc is its card's)")
    task.add_argument("--parent", type=card_number, required=True,
                      help="the step or finding it is a piece of")
    task.add_argument("--title", required=True, help="a short name (R-BAL178)")
    task.add_argument("--body-file", required=True, help="what the piece builds")
    task.add_argument("--before", type=card_number,
                      help="the open task under the same card it is built before; else it goes "
                           "above the card's finishing tasks")


def _text_parser(commands) -> None:
    """``edit``, ``comment``, ``answer``, ``note`` and ``pr``, and their arguments: each names a
    card and a file holding the text it writes."""
    edit = commands.add_parser("edit", help="rewrite a card's title or text (never a ruling's)")
    edit.add_argument("card", type=card_number)
    edit.add_argument("--title", help="its new short name (R-BAL178, R-BAL180)")
    edit.add_argument("--body-file", help="a file holding its new text")
    comment = commands.add_parser("comment", help="add a comment to a card")
    comment.add_argument("card", type=card_number)
    comment.add_argument("--body-file", required=True, help="a file holding the comment")
    answer = commands.add_parser("answer", help="answer a card's comments no answer has "
                                                "followed, quoting and mentioning each author")
    answer.add_argument("card", type=card_number)
    answer.add_argument("--body-file", required=True, help="a file holding what was done")
    note = commands.add_parser("note", help="post a stop's note on the task a claimed card's "
                                            "next session starts, and print its prompt")
    note.add_argument("card", type=card_number)
    note.add_argument("--body-file", required=True, help="a file holding the note")
    pull = commands.add_parser("pr", help="open a card's pull request into dev from card/N, "
                                          "linked to its card")
    pull.add_argument("card", type=card_number)
    pull.add_argument("--title", required=True, help="the pull request's title")
    pull.add_argument("--body-file", required=True, help="a file holding what it changes")


def parser() -> argparse.ArgumentParser:
    """Every command and its arguments."""
    top = argparse.ArgumentParser(prog="quill", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    nxt = commands.add_parser("next", help="the next step to build")
    nxt.add_argument("--arc", choices=ARCS)
    claim = commands.add_parser("claim", help="claim a card as the branch card/N, checked out")
    claim.add_argument("card", type=card_number)
    claim.add_argument("--here", action="store_true",
                       help="on the commit checked out (work already started), not on origin/dev")
    release = commands.add_parser("release", help="free a card's claim, keeping its work")
    release.add_argument("card", type=card_number)
    commands.add_parser("show", help="a card, its comments and its git history").add_argument(
        "card")
    _file_parser(commands)
    _text_parser(commands)
    block = commands.add_parser("block", help="record a blocked-by edge")
    block.add_argument("card", type=card_number)
    block.add_argument("--by", type=card_number, required=True)
    block.add_argument("--remove", action="store_true")
    move = commands.add_parser("move", help="place a step or question on the board")
    move.add_argument("card", type=card_number)
    where = move.add_mutually_exclusive_group(required=True)
    where.add_argument("--top", action="store_true")
    where.add_argument("--bottom", action="store_true")
    where.add_argument("--after", type=card_number)
    drop = commands.add_parser("drop", help="retire a card with no code")
    drop.add_argument("card", type=card_number)
    drop.add_argument("--why", required=True)
    commands.add_parser("start", help="begin a task: a commit naming it on its card's "
                                      "branch, pushed").add_argument("task", type=card_number)
    commands.add_parser("task-file", help="write a task's file plan/tasks/T, to commit with "
                                          "its piece on its card's branch").add_argument(
        "task", type=card_number)
    commands.add_parser("finding-file", help="write a fixed finding's marker plan/ships/F, to "
                                             "commit with its fix on its owner's branch"
                        ).add_argument("finding", type=card_number)
    hand = commands.add_parser("hand", help="hand an open finding to another owner step")
    hand.add_argument("finding", type=card_number)
    hand.add_argument("--to", type=card_number, required=True, help="its new owner step")
    commands.add_parser("sync", help="write cards' state from git").add_argument(
        "--dry-run", action="store_true")
    commands.add_parser("watch", help="write the state of each card whose branch moved since "
                                      "the last pass, from git").add_argument(
        "--dry-run", action="store_true")
    history = commands.add_parser("spec-history", help="a card's body edits")
    history.add_argument("card", type=card_number)
    history.add_argument("--since", help="an ISO date or time, or a branch (from its start)")
    revert = commands.add_parser("spec-revert", help="restore an earlier body")
    revert.add_argument("card", type=card_number)
    revert.add_argument("--to", required=True, help="an edit id spec-history printed")
    commands.add_parser("export", help="back up every card whole, to one file").add_argument(
        "--to", type=Path, required=True, help="the directory the day's file is written in")
    return top


COMMANDS: dict[str, Callable[..., int]] = {
    "next": cmd_next, "claim": cmd_claim, "release": cmd_release, "show": cmd_show,
    "file": cmd_file, "edit": cmd_edit, "comment": cmd_comment,
    "answer": _comments.cmd_answer, "note": _notes.cmd_note, "block": cmd_block,
    "move": cmd_move, "drop": cmd_drop, "start": _tasks.cmd_start,
    "task-file": _tasks.cmd_task_file,
    "finding-file": _findings.cmd_finding_file, "hand": _findings.cmd_hand,
    "sync": cmd_sync, "spec-history": cmd_spec_history, "spec-revert": cmd_spec_revert,
    "export": cmd_export,
}


#: The commands handed how to reach the tracker, ``connect``, in place of a tracker: each
#: connects only once it has something to read or write there.  ``watch`` runs every minute,
#: and most minutes ask git alone (:mod:`_watch`); ``pr`` writes the code repository and reads
#: nothing of the tracker (:mod:`_pr`).
CONNECTS_LATE: dict[str, Callable[..., int]] = {"watch": _watch.cmd_watch, "pr": _pr.cmd_pr}

#: What a command's failed call raises: exit status 2 (:func:`exit_status`).
FAILURES = (GitHubError, TrackerError, GitError, requests.RequestException, OSError,
            CredentialsError, _pr.PullRequestError)


def main(argv: list[str] | None = None, connect: Callable[[], Tracker] = CONNECT,
         root: Path | None = None) -> int:
    """Run one command; its exit status."""
    args = parser().parse_args(argv)
    if args.command in CONNECTS_LATE:
        return exit_status(partial(CONNECTS_LATE[args.command], args, connect, root or REPO),
                           FAILURES)
    return exit_status(lambda: COMMANDS[args.command](args, connect(), root or REPO), FAILURES)


if __name__ == "__main__":
    sys.exit(main())
