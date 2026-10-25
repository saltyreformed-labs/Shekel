"""X-cx's migration, its VERIFY: whether the tracker holds what the registries make, both ways
(the L7 design's draft 4 s.5).  It reads, and writes nothing.

**ONE grading.**  The tracker is graded by the plan every run converges on
(:func:`_migrate_plan.plan`): it must refuse nothing and leave nothing to write.  So each
item has exactly one card, by key, of its kind, labels and milestone; open, or for a ruling
closed; its mark removed; under its parent, blocked by its blockers and no others; on the
board where it has a place; its title equal, and its body and As filed comment equal under
:func:`_migrate_bodies.masked`, which leaves out the As filed commit alone, to the text a
run files (``migrate.prepare``: built, then each bare ``#N`` written as the input file
decides, R-BAL277), so a number left bare where a marker or a link was decided is a
difference; a question holding exactly one such comment, and no other card any.  A card
filed already is graded on its kind, labels, links and state, and every card no item
claims must be the tracker's own.  Each difference is named in the words the plan's
refusals use; a second grading here would be a second spelling of what a finished
migration is (rule 14).

**What no plan grades** is graded here, once the plan refuses nothing and leaves nothing to
write:

- the board's ORDER, which a run sets after the plan's writes: it must be
  :func:`_migrate_plan.desired_board`'s;
- each card's As filed COMMIT, which :func:`_migrate_bodies.masked` leaves out: it must
  ``resolve`` (the caller's predicate: the full id of a commit this checkout's HEAD
  descends from), and its item's row there must write what this checkout's row writes
  (``rows_at``, the caller's: :func:`_migrate_bodies.row_texts`, over the one producer of
  what a row writes on its card, :func:`_migrate_bodies.row_text`, run on that commit's
  rows with no input file).  A card's As filed block claims the text its item's ROW
  makes, as that commit's registries hold the row: the lead (a step's sentence, a
  finding's cell, a ruling's rule, a question's question where the input file writes
  none) and the block's cells -- so a reader finds that text there once the registries
  are gone.  It claims nothing else: the input file's work (the card's name, a step's
  spec and the paragraphs given to it, a written question) is not versioned, so it is no
  commit's to quote, and the plan grades it against this checkout's (review 8, MEDIUM-1:
  a whole card rebuilt at its commit under today's input file only approximates its run,
  and refused a true quote whenever a note had changed since); nor anything the ranks
  derive -- a step's rank, which places its card, and its ``starts`` cell's head, which
  its block does not quote -- so a tick that only renumbers ranks moves no card's claim
  (the coordinator's ruling on review 10, LOW-1).  The plan, graded first, holds the card
  to this checkout's text but the commit, so the card says what its commit's row writes
  exactly where that row and this checkout's write the same, which is what is compared:
  no part of the card is read back for it (review 9, MEDIUM-1: the block, compared alone,
  left each lead unclaimed);

and on any read, each milestone's DESCRIPTION, which the plan finds by its title alone: it
must be the input file's.

**What it grades is every fact a registry ROW an item is made from, the OUTCOMES table, or
the input file states about a card or a milestone** (the coordinator's rulings on review
10, MEDIUM-1, and review 11, MEDIUM-2).  What it does not grade, each for its reason, is
first a field a write leaves at GitHub's default, or sets only as a by-product, which is no
such fact:

- who last closed or reopened a card: ``close_unmarked`` closes each ruling as the App,
  which GitHub records as its closer (``Card.closed_by_tool``, ``_tracker.py:401``), but a
  ruling closed as completed is a record whoever closed it (R-BAL206);
- a card's author, the App by ``create``, which no registry names and the read does not
  fetch;
- the ORDER of a card's sub-issues (a container's steps), the order ``add_child`` filed
  them in, and of the cards a step blocks (the findings it owns, each blocked by it,
  R-BAL395), which the read does not fetch: a by-product (the same ruling).  The order the
  registries set is the board's (R-BAL238: the steps by rank, then the questions by the
  ledger's order), graded above; which cards are a card's sub-issues is graded as each
  one's parent, and which a step owns as each finding's blockers;
- a milestone's state, which ``create_milestone`` leaves open, and its due date: the input
  file gives a milestone its title and description alone;
- a comment's hidden (minimized) state, which the read does not fetch: a hidden As filed
  comment is graded as a shown one;
- a person's comment, or any comment of the App's but a question's As filed one: no
  registry writes one;
- a card's link BLOCKING an issue outside the tracker (the read takes a card's blockers,
  not what it blocks), its assignees, lock, pin and reactions: no write of the
  migration's sets one, and the read does not fetch them;
- its items on other projects, which the read fetches and discards
  (``_tracker.py:102``, ``:372-375``): only its place on this board is graded;

and then what a registry or the input file states that no card of the migration's holds:

- a card filed already's text and milestone (:data:`_migrate_source.MAPPED`; X-cx's own
  card today): its own, which the migration does not rewrite, so it is graded on its kind,
  labels, links and state alone (above);
- the steps-section paragraphs the input file gives a disposition of ``dropped``, ``rule``
  or ``reasoning``, and the ``home`` it names: no card carries them (L8 places them), and
  ``_migrate_input`` holds each entry to its one paragraph's text, by hash
  (``_migrate_input.py:361``), and grades its form; and the registries' prose outside
  their tables, on no card either;
- what an OUTCOMES row's scope cell holds but its keys -- each key's annotation, and any
  part naming no key: its milestone's membership, and the hash the input file holds it to
  (``_migrate_input.py:413``), read its keys alone.  Latent: no live scope cell holds any
  (0 of 6, 2026-10-12);
- the ORDER of the OUTCOMES rows, the outcomes' priority: no milestone holds it, as
  R-BAL243 keeps it in the board's order, which is the ranks', graded above; nothing here
  holds the ranks to it;
- a step's ``starts`` cell's HEAD, which the ranks derive (the plan gate recomputes it,
  and verify runs no plan gate): no card quotes it (above), and the board's order and the
  blocked-by links carry what it says.

**The tracker's own cards are LISTED**: once the plan refuses nothing, every card that is
no item's, so a reader sees which cards the migration let be.

**Not re-run here: the census of the fetched bodies** (:func:`_migrate_bodies.census`).
Once the plan refuses nothing, each fetched body equals its built one but for line ends,
the space around it and its As filed commit, and ``migrate`` runs nothing while the
census refuses a built body, so the census of the fetched bodies would be the census of
the built ones, and a re-run would measure nothing.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from tools.quill._migrate_bodies import Built, RowText, filed_commit, row_text
from tools.quill._migrate_plan import Tracked, as_filed_comments, desired_board, moves, plan
from tools.quill._migrate_run import Filing
from tools.quill._migrate_source import MAPPED, Item
from tools.quill._tracker import WholeCard
from tools.quill.check import normalized


class Unreadable(RuntimeError):
    """A commit whose registries cannot be read (``rows_at``'s): every card naming it is
    refused, naming why."""


@dataclass(frozen=True)
class Verified:
    """What ``migrate verify`` found: the tracker's own cards, every card no item claims
    (None while the plan refuses anything or leaves a write, when which cards they are is not
    settled), and the refusals."""

    own: tuple[int, ...] | None
    refusals: tuple[str, ...]


def verify(filing: Filing, tracked: Tracked, resolves: Callable[[str], bool],
           rows_at: Callable[[str], Mapping[str, frozenset[RowText]]]) -> Verified:
    """Grade the tracker as read, ``tracked``, against ``filing`` (the module docstring).

    Args:
        filing: Every item, its card text and each outcome's milestone, from a
            :func:`migrate.prepare` that refused nothing.
        tracked: The tracker as :func:`_migrate_run.read_tracked` reads it.
        resolves: Whether an As filed commit is the full id of a commit this checkout's
            HEAD descends from.
        rows_at: What each item's row writes on its card, by key, as a commit's registries
            hold the rows (:func:`_migrate_bodies.row_texts`), asked of a commit that
            resolves; it raises :class:`Unreadable` for one whose registries cannot be read.

    Returns:
        The :class:`Verified`.
    """
    found = plan(filing.items, filing.built, filing.milestones, tracked)
    refusals = [*found.refusals, *_descriptions(filing.milestones, tracked)]
    left = [("history", write) for write in found.history]
    left += [("open", write) for write in found.freeze]
    refusals += [f"{write}: still to make (`migrate {phase}` makes it), or this read lags the "
                 "run's writes" for phase, write in left]
    if found.refusals or left:
        return Verified(None, tuple(refusals))
    refusals += _board(filing.items, found.numbers, tracked)
    refusals += _commits(filing, found.numbers, tracked, resolves, rows_at)
    claimed = set(found.numbers.values())
    return Verified(tuple(number for number in sorted(tracked.cards) if number not in claimed),
                    tuple(refusals))


def _descriptions(milestones: Mapping[str, tuple[str, str]], tracked: Tracked) -> list[str]:
    """Each milestone the tracker holds by an input file's title whose description is not
    that file's (:func:`check.normalized`, as a card's text is compared)."""
    found = []
    for title, description in sorted(milestones.values()):
        held = tracked.milestones.get(title)
        if held is not None and normalized(held.description) != normalized(description):
            found.append(f"milestone #{held.number} {title!r}: its description is not the input "
                         "file's (edited on the web?): set one of the two to the other")
    return found


def _board(items: Sequence[Item], numbers: Mapping[str, int], tracked: Tracked) -> list[str]:
    """The board's order, refused unless it is the desired one.  On a plan that refuses
    nothing and leaves nothing to write, :func:`_migrate_plan.desired_board` refuses
    nothing either (a board card with no place is the plan's refusal, an item's card its
    own read shows off the board its write).  But an item's card is put on the board by its
    OWN read, and the board's order is the board's read, made apart and page by page: a
    card the one shows on the board and the other lacks (a read that lags, or an edit made
    during the read), and a card the board's read shows twice (moved between its pages),
    are refused, and once neither is, the two orders hold the same cards, each once, as
    :func:`_migrate_plan.moves` takes them (review 8, LOW-2)."""
    desired = desired_board(items, numbers, tracked)[0]
    shown = Counter(tracked.board)
    unshown = [number for number in desired if number not in shown]
    doubled = sorted(number for number, count in shown.items() if count > 1)
    refusals = []
    if unshown:
        refusals.append("the board's read does not show "
                        + ", ".join(f"plan#{number}" for number in unshown)
                        + ", which its card's read puts on it: a read that lags, or an edit "
                        "made during the read; run `migrate verify` again")
    if doubled:
        refusals.append("the board's read shows "
                        + ", ".join(f"plan#{number}" for number in doubled)
                        + " more than once: an edit made during the read, a card moved "
                        "between its pages; run `migrate verify` again")
    if refusals:
        return refusals
    out = [number for number, _ in moves(tracked.board, desired)]
    if not out:
        return []
    return [f"the board is not in its desired order, {len(out)} card(s) out of place ("
            + ", ".join(f"plan#{number}" for number in out) + "): `migrate open` orders it"]


def _commits(filing: Filing, numbers: Mapping[str, int], tracked: Tracked,
             resolves: Callable[[str], bool],
             rows_at: Callable[[str], Mapping[str, frozenset[RowText]]]) -> list[str]:
    """Each card whose As filed commit does not ``resolve`` or cannot be read, the cards
    naming it refused together; then each card naming a commit that resolves where its
    item's row writes (``rows_at``) other than this checkout's row writes
    (:func:`_migrate_bodies.row_text`), or where it is no item: the commit is read from a
    step's, a finding's or a ruling's block in its body, a question's in its As filed
    comment.  A card filed already holds none of the migration's.  Graded on a plan that
    refuses nothing, so every card holds this checkout's text, its block's commit apart:
    a card refused here says what this checkout's row writes, which its commit's does not,
    and naming this checkout's commit settles it."""
    naming = defaultdict(list)
    for item in filing.items:
        if item.key not in MAPPED:
            text = _quoting(item, tracked.cards[numbers[item.key]], tracked.app_login)
            naming[filed_commit(text) if text is not None else None].append(item)
    refusals = []
    for commit, quoting in sorted(naming.items(), key=lambda pair: str(pair[0])):
        cards = ", ".join(f"plan#{numbers[item.key]}" for item in quoting)
        if commit is None or not resolves(commit):
            refusals.append(f"{cards}: the As filed block names {commit or 'no commit'}, not "
                            "the full id of a commit this checkout's HEAD descends from, so a "
                            "reader cannot find the text it quotes")
            continue
        try:
            made = rows_at(commit)
        except Unreadable as error:
            refusals.append(f"{cards}: the As filed block names {commit}, whose registries "
                            f"cannot be read ({error}), so whether it quotes them is unknown")
            continue
        refusals += [_misquoted(item, numbers[item.key], commit, item.key in made,
                                filing.built[item.key])
                     for item in quoting if row_text(item) not in made.get(item.key, ())]
    return refusals


def _quoting(item: Item, whole: WholeCard, app_login: str) -> str | None:
    """The text of ``item``'s card holding its As filed block: a question's As filed comment
    (None while it has none), any other card's body."""
    if item.kind != "question":
        return whole.body
    comments = as_filed_comments(whole.comments, app_login)
    return comments[0] if comments else None


def _misquoted(item: Item, number: int, commit: str, there: bool, here: Built) -> str:
    """The refusal of ``item``'s card, plan#``number``, whose block names ``commit``, at
    which its row writes otherwise (``there``: an item of its key there) or it is no item:
    the card holds this checkout's text, as the plan found (``here``), so naming this
    checkout's commit settles it."""
    head = filed_commit(here.comment if item.kind == "question" else here.body)
    return (f"{item.key} (plan#{number}): its As filed block names {commit}, at which "
            + ("its row reads otherwise" if there else "it is no item")
            + "; the card holds this checkout's text, so set the commit its As filed block "
            f"names to this checkout's ({head})")
