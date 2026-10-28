"""Tasks in quill (ruling ``balance:R-BAL386``): a piece of one step or finding, a commit
inside its card's one pull request, done when its file ``plan/tasks/N`` is in git
(``_state.done_tasks``).

``quill file task`` files one (``_filing``), on the board under a step the board holds
(:func:`_command.on_board`, the one rule), and ``quill claim`` files a card's two finishing
tasks (:data:`FINISHING`, ruling plan#1875).  Here are ``quill start``, which begins a piece
with a commit naming it (:func:`cmd_start`, ruling plan#1872), and which tasks are in
progress (:func:`in_progress`); ``quill drop``'s record in git of a task begun
(:func:`record_drop`, ruling plan#1900); ``quill task-file``, which writes a
task's file into the working tree for the session to commit with the piece it finishes
(:func:`cmd_task_file`), the read a card's drop makes of the tasks under it
(:func:`taken_by_drop`), and ``quill sync``'s put-on of each task that rule says belongs on
the board and is not (:func:`put_on_board`, ruling plan#1842).  ``quill sync`` writes each
task's state from git, and ``quill show`` names each task under a card with it.  And CI's
landing check refuses a claim's pull request while a task under its card that was not dropped
lacks its done file (:func:`owed`, ``landing``'s rule 7).

Split out of the command module and the landing check, which these would take past the 1,000
lines ``too-many-lines`` allows.
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from tools.ci.gitcmd import DEV, MAIN, is_ancestor
from tools.quill import _git
from tools.quill._command import _label, _one, on_board, with_cards
from tools.quill._exits import Refused
from tools.quill._state import done_tasks, drop_takes, dropped
from tools.quill._tracker import BareCard, Card, CardReader, Tracker
from tools.quill.setup_tracker import TASK

#: What a task's file says: the instant it was written (:func:`_git.instant`), so each write
#: is a new line, as each claim's marker is.
DONE = "done "
#: The tasks that finish every card, last in its order, each title with what the piece is,
#: ``{card}`` standing for the card's number (ruling plan#1875, plan#1870): a card is ready to
#: land once both are done.  ``quill claim`` files them (:func:`finishing`), and a task filed
#: under a card goes above the first of them still to begin that comes after it here
#: (:func:`place`).
FINISHING = (
    ("Adversarial review",
     "A neutral fresh subagent reviews the design and the whole diff against the card's spec "
     "and its edits since the claim (`quill spec-history plan#{card} --since card/{card}`), "
     "grading the change's claims about itself hardest. Each finding to fix in this pull "
     "request is filed as a task above \"Full suite and pull request\"."),
    ("Full suite and pull request",
     "The full suite through `./scripts/test.sh`, `pylint app/` with CLAUDE.md's `--fail-on`, "
     "the tools packages' own tests and pylint floor, the developer's comments read and "
     "answered, then plan#{card}'s pull request into dev, queued."),
)
#: Each finishing task's title, with its place among them.
_FINISHING_RANKS = {title: rank for rank, (title, _) in enumerate(FINISHING)}


def finishing(card: int) -> tuple[tuple[str, str], ...]:
    """Card ``plan#<card>``'s finishing tasks (:data:`FINISHING`), each title with its body,
    in order: each body names the card, so no two cards' finishing tasks share a title and a
    text, a filing's identity (``_filing._same_filing``)."""
    return tuple((title, body.format(card=card)) for title, body in FINISHING)


def finishing_rank(title: str) -> int:
    """Where a task titled ``title`` stands among its card's finishing tasks: its place in
    :data:`FINISHING`, or -1 for a piece built before them."""
    return _FINISHING_RANKS.get(title.strip(), -1)


def _on_its_branch(tracker: Tracker, number: int, root: Path, what: str,
                   would: str) -> tuple[Card, str]:
    """Task ``plan#<number>`` and its card's branch ``card/<its card>``, checked out in
    ``root``: the one branch on which a task is begun and done, which ``start`` and
    ``task-file`` each write on.  ``what`` names what the command writes and ``would`` what
    it would do to a dropped piece, in its refusals.  Refused for a card that is no task, a
    task under no card of the tracker, a task dropped, and any other branch checked out, or
    none."""
    task = _one(tracker, number)
    if task.kind != TASK:
        raise Refused(f"{_label(task)} is no task: {what} is written for a task, which "
                      "`quill file task` files")
    if task.parent is None:
        raise Refused(f"{_label(task)} is under no card of the tracker: link it under the step "
                      "or finding it is a piece of first")
    if dropped(task.number, {task.number: task}, frozenset()):
        raise Refused(f"{_label(task)} was dropped (closed as {task.state_reason}): {what} "
                      f"would {would}; reopen it on the web first if it is work after all")
    branch = _git.card_branch(task.parent)
    here = _git.checked_out(root)
    if here != branch:
        raise Refused(f"{_label(task)} is a piece of plan#{task.parent}, so {what} is written "
                      f"on {branch}, and {here or 'no branch'} is checked out: switch to "
                      f"{branch} (`quill claim plan#{task.parent}` makes it)")
    return task, branch


def in_progress(claims: Mapping[int, _git.Claim]) -> frozenset[int]:
    """Each task in progress (rulings plan#1872, plan#2009): the one each live claim
    (``claims``) says is, by its card's file at its tip (:attr:`_git.Claim.in_progress`, the
    one reading), which only that card's ``quill start`` writes.  ``show``, ``sync``'s Status,
    ``note`` and :func:`place` read it."""
    return frozenset(claim.in_progress for claim in claims.values()
                     if claim.in_progress is not None)


def place(tracker: Tracker, task: Card, parent: Card, before: Card | None,
          reads: _git.Reads) -> None:
    """Put ``task``, a piece linked under ``parent``, where it is built in ``parent``'s order of
    its tasks (ruling plan#1868), each write printed as it lands: above ``before`` when the
    filing names one, else above the first of ``parent``'s finishing tasks still to begin
    that comes after it in :data:`FINISHING` (:func:`finishing_rank`), so a piece goes above
    the review, the review above the suite and pull request, and a fix the review finds, filed
    while it is in progress or once it is done, above the suite and pull request (ruling
    plan#1870).  Still to begin is open and neither done (:func:`_state.done_tasks`) nor in
    progress (:func:`in_progress`) by ``reads``, the filing's read of git, never the
    tracker's open state alone, which shows a task done in git open until ``quill sync``.  A
    task with none still to begin after it, the last finishing task always, stays where the
    link put it, last.  ``parent``'s order is its read before the link, with ``task`` last
    where that read does not list it, as GitHub lists a sub-issue linked last
    (``recorded/priority.json``).  A task already just above its place is not moved, so the
    filing run again sends nothing more."""
    rank = finishing_rank(task.title)
    order = [child.number for child in parent.children]
    if task.number not in order:
        order.append(task.number)
    if before is None:
        tasks = [child.number for child in parent.children
                 if child.kind == TASK and child.is_open and child.number != task.number]
        held = tracker.cards(tasks) if tasks else {}
        begun = done_tasks(held, reads.shipped.tasks, reads.claims) | in_progress(reads.claims)
        before = next((held[number] for number in tasks if number in held
                       and number not in begun and finishing_rank(held[number].title) > rank),
                      None)
    if before is None:
        print(f"  in plan#{parent.number}'s order: last, no finishing task "
              f"{'after it ' if rank >= 0 else ''}still to begin")
    elif order.index(task.number) + 1 == order.index(before.number):
        print(f"  in plan#{parent.number}'s order: already above plan#{before.number}")
    else:
        tracker.place_child(parent.number, task, before)
        print(f"  in plan#{parent.number}'s order: placed above plan#{before.number}")


def _holds_its_claim(root: Path, card: int, tip: str, then: str) -> None:
    """Refuse a write on card ``card``'s branch, checked out, while it lacks work ``origin``'s
    claim holds: ``tip``, the claim's tip, fetched, is not in ``HEAD``'s history, the branch
    behind the claim or diverged from it.  What such a branch says is done and in progress
    is not the claim's, and its push would be refused, so the write is refused before it
    acts on either; ``then`` says what to run once the branch holds that work.  ``quill
    start`` (:func:`cmd_start`) and ``quill drop`` (:func:`record_drop`) each ask it before
    their commit or push."""
    if not is_ancestor(root, tip, "HEAD"):
        branch = _git.card_branch(card)
        raise Refused(f"origin's {branch} holds work {branch} here does not (its tip "
                      f"{tip[:12]}), so what is in progress on it is not read here: bring that "
                      f"work in first (`git merge origin/{branch}`), then {then}")


def cmd_start(args, tracker: Tracker, root: Path) -> int:
    """Begin task ``plan#T`` (rulings plan#1872, plan#2009): a commit on its card's branch
    writing the card's file ``plan/doing/N`` to name it, pushed (:func:`_git.start`), so it is
    in progress (:func:`_git.in_progress`) and each commit until the one that writes its done
    file is a commit of its piece.  Begun on the branch checked out, ``card/<its card>``,
    while ``origin`` holds that branch, the card's claim, which the push moves forward and
    never makes, and the branch here holds all of the claim's work, fetched first
    (:func:`_holds_its_claim`), and while no other task of that card is in progress there.
    Refused, with nothing committed or pushed, for what ``task-file`` refuses
    (:func:`_on_its_branch`), a claim ``origin`` no longer holds, a branch behind the claim
    or diverged from it, a task whose file the branch holds (it is done), and another task of
    the card in progress.  Run again for a task begun already, it pushes the branch: a start
    whose push failed is finished so."""
    task, branch = _on_its_branch(tracker, args.task, root, "its start", "begin a dropped piece")
    if (tip := _git.on_origin(root).get(task.parent)) is None:
        raise Refused(f"origin holds no {branch}, so plan#{task.parent} is claimed by no one: "
                      f"`quill claim plan#{task.parent}` claims it")
    _git.fetch(root, [task.parent])
    _holds_its_claim(root, task.parent, tip, f"start plan#{task.number}")
    if _git.blob_at(root, "HEAD", _git.task_file(task.number)) is not None:
        raise Refused(f"{_label(task)} is done: {branch} holds {_git.task_file(task.number)}")
    begun = _git.in_progress(root, "HEAD", task.parent)
    if begun not in (None, task.number):
        raise Refused(f"plan#{begun} is in progress on {branch}: finish it first, its file "
                      f"(`quill task-file plan#{begun}`) in the commit that ends it, then "
                      f"start plan#{task.number}; or drop it (`quill drop plan#{begun} --why "
                      f"REASON`), which records its drop on {branch}")
    if begun == task.number:
        _git.push(root, task.parent)
        print(f"{_label(task)} is begun on {branch} already: pushed {branch}")
        return 0
    sha = _git.start(root, task.parent, task.number, task.title)
    print(f"started {_label(task)}: {sha[:12]} on {branch}, pushed; each commit until the one "
          f"writing its file (`quill task-file plan#{task.number}`) is a commit of this piece")
    return 0


def record_drop(tracker: Tracker, task: Card, root: Path, why: str, reads: _git.Reads) -> None:
    """Record in git the drop of ``task`` while its card's claim has begun it and not finished
    it (rulings plan#1900, plan#2009): ``quill drop``'s first write, before the tracker's, so
    the piece is in progress no longer for ``quill start``, ``show`` and the commit hook,
    which read its card's file in git alone (:func:`_git.in_progress`).  Begun is read where
    the drop is recorded: on ``origin``'s claim (``reads``) and, with its card's branch
    checked out, at ``HEAD``, which may hold a start not yet pushed.  Recorded on that branch
    checked out, and refused, with nothing written, anywhere else (:func:`_on_its_branch`),
    and while it lacks work the claim holds (:func:`_holds_its_claim`): a commit naming the
    task and deleting that file, saying ``why``, pushed (:func:`_git.drop`); or, its drop
    recorded at ``HEAD`` and not yet on ``origin`` (a push a failure cut short), the branch
    pushed.  A task never begun, or done, or under no card, writes nothing to git."""
    if task.parent is None:
        return
    claim = reads.claims.get(task.parent)
    branch = _git.card_branch(task.parent)
    at_head = (_git.checked_out(root) == branch
               and _git.in_progress(root, "HEAD", task.parent) == task.number)
    if not at_head and (claim is None or claim.in_progress != task.number):
        return
    _on_its_branch(tracker, task.number, root, "its drop", "drop a dropped piece")
    if claim is not None:
        _holds_its_claim(root, task.parent, claim.sha, f"drop plan#{task.number}")
    if at_head:
        sha = _git.drop(root, task.parent, task.number, task.title, why)
        print(f"  recorded its drop on {branch}: {sha[:12]}, pushed; it is in progress no "
              "longer (ruling plan#1900)")
    else:
        _git.push(root, task.parent)
        print(f"  its drop is recorded on {branch} already: pushed {branch}")


def cmd_task_file(args, tracker: Tracker, root: Path,
                  clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Write task ``plan#T``'s file ``plan/tasks/T`` (:func:`_git.task_file`), one line,
    ``done <instant>``, into the working tree, for the session to commit with the piece it
    finishes: written only while the branch checked out is ``card/<the task's card>``, the
    one branch on which the file marks it done.  It writes no tracker state: ``sync`` closes
    the task once ``origin``'s branch holds the file.  Refused, with nothing written, for a
    card that is no task, a task under no card of the tracker, a task dropped (its file would
    mark a dropped piece done), and any other branch checked out, or none
    (:func:`_on_its_branch`)."""
    task, branch = _on_its_branch(tracker, args.task, root, "its file",
                                  "mark a dropped piece done")
    path = root / _git.task_file(task.number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{DONE}{_git.instant(clock())}\n", encoding="utf-8")
    print(f"wrote {_git.task_file(task.number)}: commit it with the piece it finishes and push "
          f"{branch}; `quill sync` then closes {_label(task)}")
    return 0


def taken_by_drop(tracker: Tracker, card: Card, tasks: Iterable[int]) -> list[Card]:
    """The tasks under ``card`` that its drop drops (:func:`_state.drop_takes`), in card order,
    each read by its number: a parent's read lists a sub-issue's state, never who closed it
    or why.  Each task whose file is in the merge of ``dev`` and ``main`` (``tasks``) is
    named as left; no read is made for a card with no task under it."""
    numbers = card.task_children
    if not numbers:
        return []
    landed, taken = set(tasks), []
    for task in sorted(tracker.cards(numbers).values(), key=lambda task: task.number):
        if task.number in landed:
            print(f"  task plan#{task.number} is done in git ({_git.task_file(task.number)} is "
                  f"on {DEV} or {MAIN}), so it is not dropped")
        elif drop_takes(task, landed):
            taken.append(task)
    return taken


def off_the_board(cards: Mapping[int, Card]) -> list[Card]:
    """Each task among ``cards``, open or closed, that belongs on the board
    (:func:`_command.on_board`: its step is on it) and is not, in card order; ``cards`` holds
    each card's parent (``_command.with_closure``).  ``quill sync`` reads every open card and
    every task under each card it reads (``quill._sync_reads``), so every open task is decided
    over, and every closed one under a step it reads; not a closed task under a closed step it
    reads no other way, which a view of open cards does not show."""
    return [card for _, card in sorted(cards.items())
            if card.kind == TASK and card.board_item is None
            and on_board(TASK, cards.get(card.parent))]


def put_on_board(tracker: Tracker, cards: Mapping[int, Card], dry_run: bool) -> None:
    """``quill sync``'s put-on (ruling plan#1842): each task :func:`off_the_board` names, added
    to the board, where it nests under its step, each write printed as it lands, or under
    ``dry_run`` printed as what would be written and not made.  It catches a task a session's
    older code filed off the board, or one linked under its step by hand on the web, and one
    whose step went on the board after it was filed.  An add GitHub cut off is never re-sent
    (ruling plan#1816, ``_github``): the run fails, and the next one reads the task again, on
    the board or not.  Run again, it adds none once each add reads back as on the board; an
    add sent again for a task already on it keeps its one item (``recorded/twice.json``)."""
    for task in off_the_board(cards):
        if not dry_run:
            tracker.board.add(task)
        print(f"{'would ' if dry_run else ''}put on the board, nested under its step "
              f"plan#{task.parent} (ruling plan#1842): {_label(task)}")


def owed(reader: CardReader, card: BareCard, held: dict[int, BareCard],
         done: Collection[int]) -> list[str]:
    """CI's landing check's open-task arm (``R-BAL386``; ``tools/quill/landing.py``): one
    sentence for each task under ``card``, which the pull request of its claim ``card/N``
    records, that was not dropped (:func:`_state.dropped`: closed by a person, or by ``quill
    drop``; a task the TOOL closed as done counts, as that close only shows git) and whose
    done file the merge it lands does not hold (``done``, the tasks whose file it holds).  Each
    task is read by its number through ``reader``, as a parent's listing of it names no closer:
    those ``held`` lacks, which it then holds (:func:`_command.with_cards`).  A task the tool
    closed is dropped only once ``quill sync`` has reopened it (``quill drop`` refuses a closed
    card), so its sentence says so.

    Raises:
        _tracker.TrackerError: when a task ``card`` lists does not exist.
    """
    numbers, head = card.task_children, _git.card_branch(card.number)
    with_cards(reader, held, numbers)
    reasons = []
    for number in sorted(numbers):
        if number in done or dropped(number, held, set()):
            continue
        shown, drop = (("", f"drop the task (quill drop plan#{number})") if held[number].is_open
                       else (" (quill closed it as done while a branch held that file)",
                             f"drop the task once quill sync reopens it (quill sync, then quill "
                             f"drop plan#{number})"))
        reasons.append(f"{_git.task_file(number)}: {head} records card {card.number}, but holds "
                       f"no done file for task plan#{number} under it, which is neither done "
                       f"nor dropped{shown}: write it in the commit that finishes the task "
                       f"(quill task-file plan#{number}), or {drop}")
    return reasons
