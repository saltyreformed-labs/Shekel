"""X-cx's migration, its PLAN: the writes still to make, from what the tracker holds, to file
the cards the registries make (the L7 design's draft 4 s.4).

**GitHub is the only state.**  Every run reads the whole tracker -- every card with its body
and comments, its milestones, its board -- and this module says, from that and the items
(:mod:`_migrate_source`), what is still to write and what it refuses.  So a run cut short
anywhere is resumed by running it again, and a run with nothing left writes nothing.  It is
PURE: it reads only what it is handed (:class:`Tracked`), and writes nothing; X-cx's B2b
executes what it says.

**A card's KEY is its arc label and its alias** (:func:`card_key`): the old id its title
carries in brackets (:func:`check.title_alias`), under its one arc label -- except a card
filed already (:data:`_migrate_source.MAPPED`), keyed by its number.  An item's card is the
card of its key; a card whose key is no item's is the tracker's own, and moves nowhere.

**The writes, in order** (draft 4 s.4, s.11 V3-8, ruling ``balance:R-BAL242``):

- HISTORY, first, inside the cutover's freeze: each ruling filed born marked, then closed
  with its mark removed in ONE write (:data:`CLOSE_UNMARKED`);
- the FREEZE: each outcome's milestone, found by its TITLE (R-BAL243); then the steps in
  an order that files a card's parent and its blockers before it; then the findings, each
  blocked by its owner step (R-BAL395); then the questions.  A card's writes are its
  create (born marked, its labels and milestone in it), its parent link, a link per open
  blocker, a question's As filed comment, its board place (a ranked step's or a
  question's), and last its mark's removal -- a container's only after every step's
  writes, so after its last leaf is linked (draft 4 s.4, R9).  A card filed already takes
  its links alone.

**The board** is put in its desired order apart, once every card is on it
(:func:`desired_board`, :func:`moves`): the ranked steps by rank, the card filed already
at its rank holding every board card below it in its present order, then the questions in
the ledger's order (R-BAL238); only the cards outside the longest common subsequence of the
present and desired orders move, so a second run moves nothing.  What the board's shape
refuses is refused by :func:`plan` itself, before any write (:func:`board_refusals`).

**What it refuses, never chooses** (each a line of :attr:`Plan.refusals`): a key on two
cards (a create re-sent after a rate-limit refusal it was performed for; a card given the
key by hand, retitled or relabelled, one of the tracker's own or another item's, and the
key a card filed already's among them; or a re-sent create's card edited by hand), each
card named with what it holds; only in the state a re-sent create leaves, its last card
refused nothing as the item's card on the read once its refusals are settled as they say,
assuming nothing a person decides (:func:`_shared_refusals`: each key on two cards holding
the card that keeps it, a card filed already's own or a re-sent create's last, any other
none, and no card or milestone excused), are the others offered for deletion, and that
last card, the one the run went on with, named to keep by its number (GitHub's numbers
rise), and a card filed already is never offered (:func:`_shared`); a card filed already
that the tracker does not hold, or that still carries the filing mark (its own filing is
unfinished); a card no item claims but the tracker's own (:func:`_unclaimed`); a step the
registries put below a card filed already (X-cx's leaves are its sub-issues, never
``steps.md`` rows); a milestone the input file does not name (one renamed on the web would
be made again); a board card the desired order has no place for (:func:`board_refusals`);
an item's card of another kind, labels or milestone, or one whose outcome's milestone the
tracker no longer holds (deleted or renamed on the web, it would be made again holding none
of its cards), closed when it moves open, or open when it is a ruling; one whose filing ENDED
(:func:`_state.filing_ended`: dropped, or a ruling withdrawn, R-BAL205's hazard); one under
another parent, blocked by a card its item is not, or linked outside the tracker (a
person's call); one whose title, body or As filed comment is not what the registries make
it at this commit (:func:`_migrate_bodies.differences`; a stale card is never finished, and
``quill edit`` cannot repair a ruling, R-BAL220); one whose filing finished with a write
still missing; a title whose alias does not read back as its item's; and steps waiting in a
cycle.  Each is stated once: a card or item already refused is not refused again for what
follows from it.  **A plan that refuses anything writes nothing** (:func:`plan`): one
refusal leaves other items' writes leaning on it -- a container unmarked over a leaf that
was refused, a finding blocked by a step with no card -- so every refusal is settled
before any write.
The registries' and the input file's own refusals (:func:`_migrate_source.read_source`,
:func:`_migrate_input.grade`, :func:`_migrate_bodies.build` and its census,
:func:`_migrate_numbers.link`) are the executor's to gate on: it runs no plan while any of
them refuses.

**A card filed already** (:data:`_migrate_source.MAPPED`, card #1) is graded on its kind,
labels and links -- parent, blockers and links outside the tracker -- and on its state
(open, its filing not ended); its title, text and milestone are its own, never the
registries'.  That is draft 4 s.5's "key, kind, labels and links", and the state besides:
no link is written onto a card in a state the migration does not expect.

**What the tracker may hold besides the items' cards** (:func:`_unclaimed`): the cards
filed already, and the TRACKER'S OWN cards below them -- X-cx's own, of any kind (#2-#10,
#26 and #27 under #1, all steps, measured read-only 2026-10-09 and again 2026-10-24; a
finding or a ruling ``quill file`` files with #1 or a card below it as its owner, blocked
by it and below nothing, R-BAL395, is its own too, and a task under one, :func:`_own`) --
and nothing else, so the cutover starts on a tracker holding X-cx's cards and the
migration's only.  A card no
item claims is told the tracker's own by its NUMBER, which no hand edit changes, never by
what its title, labels, body or comments still say: the migration makes every write
inside the freeze (R-BAL242), and no card is filed natively inside it (draft 4 s.4), so
every card numbered above the migration's first card is the migration's.  Its first card
is the lowest-numbered card an
item's key names (a card filed already apart), which rests on two PREMISES: no card the
tracker held before the migration carries an item's key (an X-cx leaf's ``[X-cx Ln]``
alias is no registry id), and the freeze holds from the migration's first create to its
last run, gaps between runs included.  Broken, each fails closed: a card of the tracker's
own retitled to an item's key is refused as that item's card, and the tracker's own cards
numbered above it are refused as the migration's too, each refusal naming that card as
the one to settle FIRST (it leads, because it cannot tell the two cases apart); a card filed
natively between runs is refused as the migration's.  Refused: a card no item claims
numbered above the first card, wherever it sits and whatever it holds -- the migration's
card with its title or arc label changed by hand, or one whose item left the registries
(or a card filed natively inside the freeze); and one numbered below it outside every card
filed already (filed outside X-cx before the migration, an X-cx leaf quill unlinked when it
was dropped while marked, R-BAL205, or that a person unlinked by hand, or one of the
migration's first cards with its key changed by hand or its item gone from the registries,
which numbers the first card above it).
**Where the freeze ends in code** (the second premise's bound): at L8's deleting pull
request, which removes the registries, the read-only door :mod:`_migrate_source` imports
(``tools/plan_gate/registries.py``, R-BAL244) and the migration's own modules, so nothing
is left to call :func:`plan` once natively filed cards are allowed again.  A run between
``migrate verify`` and that merge is inside the freeze still, and refuses every card filed
natively (fail closed).
What this cannot see: a card of the migration's numbered lower than every card that still
carries an item's key (a card filed already apart), sitting below a card filed already (a
sub-issue, or a finding owned by one or by a card below one, or a card below such a
finding: :func:`_own`), where it reads as the
tracker's own.  That takes every card the migration filed before it
to have lost its key, been deleted by hand or lost its item from the registries; the first
cards it files are closed rulings (HISTORY runs first, R-BAL242, once the executor runs it
first), so it is a person rekeying or deleting the first rulings filed, or their rows
leaving the registries, and one of them moved below card #1, inside the freeze that forbids
each of those edits.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from tools.ci.arcs import ARCS
from tools.quill._migrate_bodies import AS_FILED, Built, differences, filed_commit
from tools.quill._migrate_source import MAPPED, Item
from tools.quill._state import filing_ended, owner
from tools.quill._tracker import Card, Comment, Milestone, WholeCard
from tools.quill.check import card_title, title_alias
from tools.quill.setup_tracker import FILING

#: The writes, by what :attr:`Write.what` says.  Every one but :data:`MILESTONE` names its
#: card by key.
MILESTONE = "milestone"
CREATE = "create"
CLOSE_UNMARKED = "close-unmarked"
LINK = "link"
BLOCK = "block"
COMMENT = "comment"
BOARD_ADD = "board-add"
UNMARK = "unmark"


@dataclass(frozen=True)
class Write:
    """One write, addressed by KEY (a card filed in this run has no number until its create
    answers): what it is, the card's key (for :data:`MILESTONE`, the outcome's name), and
    the other card it names -- a :data:`LINK`'s parent, a :data:`BLOCK`'s blocker."""

    what: str
    key: str
    other: str | None = None

    def __str__(self) -> str:
        """The write as a run prints it, and ``migrate verify`` names one still to make: what
        it is, its key, and the other card it names."""
        return f"{self.what} {self.key}" + (f" -> {self.other}" if self.other else "")


@dataclass(frozen=True)
class Tracked:
    """The tracker as one run read it: every card whole, by number
    (:meth:`_tracker.Tracker.all_cards`); its milestones, by title; its board's cards in
    order; and the App's login, whose comments are the tool's."""

    cards: Mapping[int, WholeCard]
    milestones: Mapping[str, Milestone]
    board: tuple[int, ...]
    app_login: str


@dataclass(frozen=True)
class Plan:
    """What a run writes and refuses: each item's card number where one exists, the
    HISTORY phase's writes, the FREEZE phase's, and the refusals."""

    numbers: Mapping[str, int]
    history: tuple[Write, ...]
    freeze: tuple[Write, ...]
    refusals: tuple[str, ...]


def card_key(number: int, whole: WholeCard) -> str | None:
    """The key a card is an item's card by: :data:`_migrate_source.MAPPED`'s for a card filed
    already, else its one arc label and its title's alias (:func:`check.title_alias`); None
    for a card with no alias or not exactly one arc label."""
    for key, mapped in MAPPED.items():
        if mapped == number:
            return key
    alias = title_alias(whole.card.title)
    arcs = [label for label in whole.card.labels if label in ARCS]
    if alias is None or len(arcs) != 1:
        return None
    return f"{arcs[0]}:{alias}"


def plan(items: Sequence[Item], built: Mapping[str, Built],
         milestones: Mapping[str, tuple[str, str]], tracked: Tracked) -> Plan:
    """The writes still to make, and what is refused; none at all when anything is (the
    registries' and the input file's own refusals are the caller's to gate on).

    Args:
        items: Every item, as :func:`_migrate_source.read_source` orders them.
        built: Each item's card text as filed, by key (``migrate.prepare``: built, then its
            bare ``#N`` written; a card filed already has none).
        milestones: Each outcome's milestone, ``(title, description)``, as the input file
            gives it (:attr:`_migrate_input.Decisions.milestones`).
        tracked: The tracker as this run read it.

    Returns:
        The :class:`Plan`.
    """
    known = _numbers(items, tracked)
    unnamed = _unnamed_milestones(milestones, tracked)
    graded = _Grading(known.numbers, tracked, milestones, built, frozenset(known.unaccounted),
                      frozenset(held.number for held in unnamed))
    refusals = _shared_refusals(items, known, graded)
    refusals += [*known.refusals, *(f"milestone #{held.number} {held.title!r} is none the input "
                                    "file names (renamed on the web?): settle it before the run"
                                    for held in unnamed)]
    writes, refused = {}, set(known.unwritable)
    for item in items:
        count = len(refusals)
        writes[item.key] = [] if item.key in known.unwritable else graded.writes(item, refusals)
        if len(refusals) > count:
            refused.add(item.key)
    refusals += board_refusals(items, known.numbers, tracked, refused, known.unaccounted)
    freeze, cycle = _freeze(items, writes, milestones, tracked)
    refusals += cycle
    if refusals:
        return Plan(known.numbers, (), (), tuple(refusals))
    history = [write for item in items if item.kind == "ruling" for write in writes[item.key]]
    return Plan(known.numbers, tuple(history), tuple(freeze), ())


def _unnamed_milestones(milestones: Mapping[str, tuple[str, str]],
                        tracked: Tracked) -> list[Milestone]:
    """Each milestone of the tracker the input file does not name, by title: one renamed on
    the web, beside which the one the input names would be made again, so :func:`plan`
    refuses it, and no card in it whose own outcome's milestone is missing by title
    (:meth:`_Grading._unlike_milestone`)."""
    titles = {title for title, _ in milestones.values()}
    return [held for title, held in sorted(tracked.milestones.items()) if title not in titles]


@dataclass(frozen=True)
class _Known:
    """What a run knows of each item's card before grading it: its number, where exactly one
    card holds its key; the items no write may touch; the refusals that say why, but a key's
    on two cards or more; the cards refused, which nothing else is refused for; and each key
    on two cards or more with its cards, lowest first, which :func:`plan` refuses once it can
    grade the last of them (:func:`_shared`)."""

    numbers: dict[str, int]
    unwritable: set[str]
    refusals: list[str]
    unaccounted: set[int]
    shared: dict[str, list[int]]


def _numbers(items: Sequence[Item], tracked: Tracked) -> _Known:
    """Each item's card number, and the items and cards refused before any is graded: a card
    filed already that the tracker does not hold, an alias no title gives back, a step the
    registries put below a card filed already, and each card no item claims but the
    tracker's own (:func:`_unclaimed`); and each key on two cards or more, which :func:`plan`
    refuses once it can grade the last of them (:func:`_shared`)."""
    by_key = defaultdict(list)
    for number, whole in sorted(tracked.cards.items()):
        if (key := card_key(number, whole)) is not None:
            by_key[key].append(number)
    keys = {item.key for item in items}
    parents = {item.key: item.links.parent for item in items}
    shared = {key: found for key, found in by_key.items() if key in keys and len(found) > 1}
    unheld = {key for key, number in MAPPED.items() if key in keys and number not in tracked.cards}
    misread = {item.key for item in items
               if title_alias(card_title(item.alias, item.alias)) != item.alias}
    under = {item.key for item in items
             if item.kind == "step" and _under_mapped(item.key, parents)}
    refusals = [f"{key} is plan#{MAPPED[key]}, which the tracker does not hold: nothing is "
                "filed in its place" for key in sorted(unheld)]
    refusals += [f"{key}: its alias does not read back from the title it would carry, so no "
                 "run could find its card" for key in sorted(misread)]
    refusals += [f"{key} is a step below a card filed already in the registries: that card's "
                 "leaves are its own sub-issues, never steps.md rows, so no step is filed below "
                 "it; re-point the row before the migration" for key in sorted(under)]
    unclaimed = _unclaimed(keys, tracked, _first_card(by_key, keys))
    refusals += [refusal for _, refusal in unclaimed]
    numbers = {key: found[0] for key, found in by_key.items() if key in keys and len(found) == 1}
    return _Known(numbers, set(shared) | unheld | misread | under, refusals,
                  {number for found in shared.values() for number in found}
                  | {number for number, _ in unclaimed}, shared)


def _shared_refusals(items: Sequence[Item], known: _Known, graded: _Grading) -> list[str]:
    """Each key on two cards or more refused (:func:`_shared`), in key order, on ONE grading
    of the read ``graded`` grades: the read once its refusals are settled as they say,
    assuming nothing a person decides -- each key on two cards holding the card that keeps
    it, a card filed already's own or a re-sent create's last, any other none, and no card
    or milestone excused.

    A re-sent create's last is the last made of cards in the state a re-sent create leaves
    (:func:`_re_sent`), whatever that key's own refusal: a card linked to one of them is
    linked to the last, the others as their create left them, so that last holds a later
    write, and no refusal lets a card holding one be deleted.  Any other key a person
    settles, keeping any card (retitling one given the key by hand away from it), so here it
    holds none, and a card under or blocked by one of its cards is refused (one holding or
    blocking it is graded on its own links).  No card or milestone is
    excused for being refused already, since settling a refusal can leave what it excused
    standing wrong (a card retitled away from its key, then back): a link to a card refused
    already, and a card in a milestone refused already, are graded as they are.

    A last card refused nothing here as its item's card (:meth:`_Grading.writes`; the
    board's shape is graded apart, :func:`board_refusals`) is refused nothing so by the very
    next run either, once its copies are deleted as offered and nothing else is settled:
    that run grades it as this grading does, but excuses more -- its numbers lack the keys
    still on two cards, whose cards it excuses, as it excuses the cards no item claims and a
    card in a milestone it refuses."""
    re_sent = {key for key, found in known.shared.items() if _re_sent(found, graded.tracked)}
    kept = {key: MAPPED.get(key, found[-1]) for key, found in known.shared.items()
            if key in MAPPED or key in re_sent}
    after = replace(graded, numbers={**graded.numbers, **kept}, unaccounted=frozenset(),
                    unnamed=frozenset())
    return [_shared(item, known.shared[item.key], after, item.key in re_sent)
            for item in sorted(items, key=lambda item: item.key) if item.key in known.shared]


def _re_sent(numbers: Sequence[int], tracked: Tracked) -> bool:
    """Whether cards ``numbers`` (lowest first) are in the state a re-sent create leaves:
    copies of one create (:func:`_create_of`), each but the last made as its create left it
    (no later write, :func:`_later_writes`)."""
    last = _create_of(tracked.cards[numbers[-1]])
    return all(not _later_writes(number, tracked) and _create_of(tracked.cards[number]) == last
               for number in numbers[:-1])


def _shared(item: Item, numbers: Sequence[int], graded: _Grading, re_sent: bool) -> str:
    """How :func:`plan` refuses ``item``'s key on the cards ``numbers`` (lowest first), on
    the grading ``graded`` (:func:`_shared_refusals`', which numbers the key by its last card
    where ``re_sent`` says the cards are in the state a re-sent create leaves): each card
    named with its state (:func:`_card_state`).

    The one machine cause is a create GitHub refused for a rate limit though it had performed
    it, which :mod:`_github` sends again: the run goes on with the card the last send made,
    so every card before it is a copy of it, still as its create left it (:func:`_re_sent`).
    Only in that state, and only when that last card is refused nothing as the item's card
    on the read once its refusals are settled as they say, assuming nothing a person decides
    (:func:`_shared_refusals`: each key on two cards holding the card that keeps it, a card
    filed already's own or a re-sent create's last, any other none, and no card or
    milestone excused; :meth:`_Grading.refuses_nothing`), are the others offered for
    deletion.  Only there is the card to keep named by its CREATION ORDER, the last made:
    GitHub numbers issues in one rising sequence, so the highest of the copies is the last
    send's, the card the run went on with -- whatever a read that lags the run's writes
    shows of it, where a keeper named by its state could be any copy, and the card deleted
    the one holding the writes.  A key a card filed already holds is never the machine's
    (the migration files no card for it).  In any other state a person gave a card the key
    by hand, edited one of them, or changed what the last is graded against, or the read
    lags the migration's own writes, so a later run is to tell, and one refused still is
    settled by hand: neither offers a card for deletion nor names one to keep, and every card
    is named by its state alone."""
    tracked = graded.tracked
    states = "; ".join(f"plan#{number} {_card_state(number, tracked)}" for number in numbers)
    head = f"{item.key} is the key of {len(numbers)} cards ({states})"
    if item.key in MAPPED:
        return (f"{head}: the migration files no card for a card filed already (plan#"
                f"{MAPPED[item.key]}), so every other card holding its key was given it by "
                "hand (retitled, or its arc label changed); retitle or relabel each to its own "
                "title and arc label, delete none, and run again")
    if re_sent and graded.refuses_nothing(item):
        return (f"{head}: the state a re-sent create leaves (GitHub refused a create for a "
                "rate limit though it had performed it, and the tool sent it again), copies of "
                "one create, each but the last made as its create left it.  Keep the last made "
                f"(plan#{numbers[-1]}), the one the run went on with; delete the others on the "
                "web, and run again: the next run grades it as the item's card")
    return (f"{head}, not the state a re-sent create leaves (copies of one create, each but "
            "the last made as its create left it, and the last refused nothing as the item's "
            "card): a person gave a card this key by hand (retitled it, or changed its arc "
            "label), edited one of them, or changed what the last is graded against (such as "
            "a card it is linked to, that card's key given to another card, or its milestone), "
            "or this read lags the migration's own writes.  Run again later, and if it is "
            "refused still, settle them by hand and run again: retitle or relabel a card given "
            "this key to its own title and arc label, never deleting it (above all one of the "
            "tracker's own, filed before the migration), and delete no card holding a later "
            "write")


def _card_state(number: int, tracked: Tracked) -> str:
    """Card ``number``'s state as a refusal names it: marked or not, and each later write it
    holds (:func:`_later_writes`)."""
    later = _later_writes(number, tracked)
    if not later:
        return ("still marked and open, linked to nothing the tool reads, off the board, with "
                "no comment")
    marked = FILING in tracked.cards[number].card.labels
    return ", ".join(["marked", *later] if marked else later)


def _later_writes(number: int, tracked: Tracked) -> list[str]:
    """What card ``number`` holds that its create did not make, each as a refusal names it:
    its mark removed, a close, a parent, sub-issues, a blocker either way, a link outside the
    tracker, a board place, comments.  None: it is still marked and open, linked to nothing
    the tool reads, off the board, with no comment.

    The tool reads no issue OUTSIDE the tracker that the card blocks: a card's read holds
    its blockers, never what it blocks (:data:`_queries.CARD_FIELDS`), so "blocking" is
    told from the tracker's own cards' blockers alone, and a card blocking an issue outside
    the tracker reads as blocking nothing."""
    whole = tracked.cards[number]
    card = whole.card
    children = [held for held, other in tracked.cards.items() if other.card.parent == number]
    blocking = [held for held, other in sorted(tracked.cards.items())
                if number in other.card.blocked_by]
    found = [] if FILING in card.labels else ["unmarked"]
    found += [] if card.is_open else ["closed"]
    found += [] if card.parent is None else [f"under plan#{card.parent}"]
    found += [f"holding {len(children)} sub-issue(s)"] if children else []
    found += [f"blocked by {_named(card.blocked_by)}"] if card.blocked_by else []
    found += [f"blocking {_named(blocking)}"] if blocking else []
    found += ["linked outside the tracker"] if card.outside else []
    found += [] if card.board_item is None else ["on the board"]
    found += [f"{len(whole.comments)} comment(s)"] if whole.comments else []
    return found


def _named(numbers: Iterable[int]) -> str:
    """Cards ``numbers`` as a refusal names them."""
    return ", ".join(f"plan#{number}" for number in numbers)


def _create_of(whole: WholeCard) -> tuple:
    """What a card's create set and no later write of the run's changes: its kind, title,
    body, milestone, and labels but the filing mark."""
    card = whole.card
    return card.kind, card.title, whole.body, whole.milestone, tuple(_labels(card))


def _labels(card: Card) -> list[str]:
    """A card's labels but the filing mark, sorted (:func:`_create_of`,
    :meth:`_Grading._unlike`)."""
    return sorted(label for label in card.labels if label != FILING)


def _first_card(by_key: Mapping[str, list[int]], keys: set[str]) -> int | None:
    """The migration's first card: the lowest-numbered card an item's key names, a card filed
    already apart (None: none yet).  Every card numbered above it is the migration's
    (:func:`_unclaimed`), on the premises the module docstring states: no card the tracker
    held before the migration carries an item's key, and the freeze holds from the
    migration's first create to its last run."""
    mapped = set(MAPPED.values())
    return min((number for key, found in by_key.items() if key in keys for number in found
                if number not in mapped), default=None)


def milestone_title(item: Item, milestones: Mapping[str, tuple[str, str]]) -> str | None:
    """The title of the milestone ``item``'s card is filed in, by its outcome's name in
    ``milestones`` (the input file's), None for an item with no outcome (``Item.outcome``:
    a split step's is carried to its leaves): what the plan grades a card's milestone by
    and the run files a create in.

    Raises:
        KeyError: When ``milestones`` lacks the item's outcome, which
            :func:`_migrate_input.grade` refuses for every outcome an item is in, so no plan
            runs on such a file.
    """
    if item.outcome is None:
        return None
    return milestones[item.outcome][0]


def as_filed_comments(comments: Iterable[Comment], app_login: str) -> list[str]:
    """A card's As filed comments among its ``comments``: the tool's that open
    :data:`_migrate_bodies.AS_FILED`, found by reading them, never by remembering (draft 4
    s.4) -- by the plan from every card's listing, and by the run (:mod:`_migrate_run`)
    from one card's re-read before it writes one."""
    return [comment.body for comment in comments
            if comment.author == app_login and comment.body.startswith(AS_FILED)]


def _unclaimed(keys: set[str], tracked: Tracked,
               first: int | None) -> list[tuple[int, str]]:
    """Each card no item claims that is not the tracker's own, with its refusal: one
    numbered above ``first``, the migration's first card (None: it has filed none), wherever
    it sits; one numbered below it outside every card filed already, its filing ended
    (dropped while marked, or a ruling withdrawn) or not.  Below a card filed already
    (:func:`_own`), a card numbered below it is the tracker's own, of any kind (the module
    docstring says why, and what this cannot see)."""
    mapped = set(MAPPED.values())
    found = []
    for number, whole in sorted(tracked.cards.items()):
        if number in mapped or card_key(number, whole) in keys:
            continue
        label = f"plan#{number} ({whole.card.title[:60]!r})"
        if first is not None and number > first:
            found.append((number, f"{label} is no item's card, and it is numbered above plan#"
                          f"{first}, the lowest card an item's key names (a card filed already "
                          "apart), so it reads as the migration's.  FIRST, if plan#"
                          f"{first} is a card of the tracker's own given an item's key by hand "
                          "(refused besides), settle that one and run again: this card is not "
                          "to be deleted for it.  Else it is the migration's card with its "
                          "title or arc label "
                          "changed by hand, or its item left the registries, or it was filed "
                          "natively inside the freeze; settle it before the run"))
        elif _own(number, mapped, tracked.cards):
            continue
        elif filing_ended(whole.card):
            found.append((number, f"{label}'s filing ended (dropped while marked, or a ruling "
                          "withdrawn), no item claims it, and it is numbered lower than every "
                          "card an item's key names (a card filed already apart) and under no "
                          "card filed already: a card filed outside X-cx before the migration, "
                          "an X-cx leaf quill unlinked when it was dropped (R-BAL205), an X-cx "
                          "card a person unlinked from a card filed already, or one of the "
                          "migration's first cards with its title or arc label changed by hand "
                          "or its item gone from the registries; re-link one a person unlinked "
                          "under its card filed already (a finding or a ruling: block it by its "
                          "owner again, R-BAL395), and delete any other on the web, before the "
                          "run"))
        else:
            found.append((number, f"{label} is no item's card, numbered lower than every card "
                          "an item's key names (a card filed already apart) and under no card "
                          "filed already: a card filed outside X-cx before the migration, an "
                          "X-cx leaf unlinked from a card filed already by hand (a finding or a "
                          "ruling: its owner link removed; block it by its owner again, "
                          "R-BAL395), or one of the "
                          "migration's first cards with its title or arc label changed by hand "
                          "or its item gone from the registries; settle it before the run"))
    return found


def _freeze(items: Sequence[Item], writes: Mapping[str, list[Write]],
            milestones: Mapping[str, tuple[str, str]],
            tracked: Tracked) -> tuple[list[Write], list[str]]:
    """The FREEZE phase's writes in order: the milestones not found by title; the steps,
    each after its parent and blockers, a container's mark removed only after every step's
    writes; the findings; the questions -- and a refusal when steps wait on each other."""
    order, cycle = _filing_order([item for item in items if item.kind == "step"])
    containers = {item.links.parent for item in items
                  if item.kind == "step" and item.links.parent is not None}
    freeze = [Write(MILESTONE, name) for name, (title, _) in milestones.items()
              if title not in tracked.milestones]
    held = []
    for item in order:
        held += [write for write in writes[item.key]
                 if item.key in containers and write.what == UNMARK]
        freeze += [write for write in writes[item.key]
                   if not (item.key in containers and write.what == UNMARK)]
    freeze += held
    for kind in ("finding", "question"):
        freeze += [write for item in items if item.kind == kind for write in writes[item.key]]
    return freeze, cycle


@dataclass(frozen=True)
class _Grading:
    """What one run grades an item's card against: each item's card number, the tracker as
    read, each outcome's milestone, each item's card text, the cards refused already, a link
    to which is not refused again, and the milestones refused already
    (:func:`_unnamed_milestones`), a card in which, its own outcome's milestone missing, is
    not refused again."""

    numbers: Mapping[str, int]
    tracked: Tracked
    milestones: Mapping[str, tuple[str, str]]
    built: Mapping[str, Built]
    unaccounted: frozenset[int]
    unnamed: frozenset[int]

    def writes(self, item: Item, refusals: list[str]) -> list[Write]:
        """``item``'s writes still to make: all of them when it has no card; else what its
        card lacks, each refusal added instead where its card differs from it."""
        number = self.numbers.get(item.key)
        if number is None:
            return self._new(item)
        found = self._refusal(item, self.tracked.cards[number])
        if found:
            refusals.append(f"{item.key} (plan#{number}): {found}")
            return []
        missing = self._missing(item, self.tracked.cards[number])
        whole = self.tracked.cards[number]
        unfinished = FILING in whole.card.labels
        if item.key not in MAPPED and not unfinished and [
                write for write in missing if not self._follows(write, whole.card)]:
            refusals.append(f"{item.key} (plan#{number}): its filing finished, yet it lacks "
                            f"{', '.join(sorted({write.what for write in missing}))}: a "
                            "person changed it, or this read lags the migration's own writes; "
                            "run again later, and if it is refused still, settle it by hand")
            return []
        if item.key in MAPPED:
            return [write for write in missing if write.what in (LINK, BLOCK)]
        return missing + ([Write(UNMARK, item.key)] if unfinished and item.kind != "ruling"
                          else [])

    def refuses_nothing(self, item: Item) -> bool:
        """Whether ``item``'s card on this grading is refused nothing (:meth:`writes`): on
        :func:`_shared_refusals`' grading, what a re-sent create's last card must be before
        its others are offered for deletion (:func:`_shared`)."""
        refusals: list[str] = []
        self.writes(item, refusals)
        return not refusals

    def _follows(self, write: Write, card: Card) -> bool:
        """Whether a missing ``write`` follows from a card refused already: a link to an item
        with no card of its key, where the card is linked so to a card refused already (the
        item's card with its key changed), so it is no refusal of its own."""
        if write.what not in (LINK, BLOCK) or write.other in self.numbers:
            return False
        held = [card.parent] if write.what == LINK else list(card.blocked_by)
        return any(number in self.unaccounted for number in held)

    def _new(self, item: Item) -> list[Write]:
        """Every write that files ``item`` as a new card."""
        if item.kind == "ruling":
            return [Write(CREATE, item.key), Write(CLOSE_UNMARKED, item.key)]
        writes = [Write(CREATE, item.key)]
        if item.links.parent is not None:
            writes.append(Write(LINK, item.key, item.links.parent))
        writes += [Write(BLOCK, item.key, blocker) for blocker in item.links.blockers]
        if item.kind == "question":
            writes.append(Write(COMMENT, item.key))
        if item.links.place is not None:
            writes.append(Write(BOARD_ADD, item.key))
        return [*writes, Write(UNMARK, item.key)]

    def _milestone(self, item: Item) -> int | None:
        """The number of the milestone ``item``'s card is filed in, None for none (an item
        with no outcome, :func:`milestone_title`, or one whose milestone does not exist
        yet)."""
        title = milestone_title(item, self.milestones)
        found = None if title is None else self.tracked.milestones.get(title)
        return None if found is None else found.number

    def _refusal(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card is not one the migration may write on, or None: it is not
        the item's card (:meth:`_unlike`), it is placed otherwise (:meth:`_misplaced`), or
        it says otherwise (:meth:`_stale`)."""
        return (self._unlike(item, whole) or self._misplaced(item, whole)
                or self._stale(item, whole))

    def _stale(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card does not say what the registries make it, or None: its title,
        body or As filed comment differs (:func:`_migrate_bodies.differences`), or it holds
        that comment more than once.  A card filed already says what it says."""
        if item.key in MAPPED:
            return None
        comments = self._as_filed(whole)
        if len(comments) > 1:
            return f"it holds {len(comments)} As filed comments; the tool comments one"
        built = self.built[item.key]
        parts = differences(built, whole.card.title, whole.body,
                            comments[0] if comments else None)
        if parts:
            commit = filed_commit(built.comment if item.kind == "question" else built.body)
            return (f"it differs in its {', '.join(parts)} from what the registries make it at "
                    "this commit: a stale card is never finished; settle it by hand, giving it "
                    f"that text, its As filed block naming this commit ({commit})")
        return None

    def _unlike(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card is not what the migration files for it: its kind, labels or
        milestone (:meth:`_unlike_milestone`); a filing that ended; a link outside the
        tracker."""
        card = whole.card
        labels = _labels(card)
        if card.kind != item.kind:
            return f"it is a {card.kind}, not a {item.kind}"
        if labels != sorted(item.labels):
            return f"its labels are {labels}, not {sorted(item.labels)}"
        if filing_ended(card):
            return ("its filing ended (dropped while marked, or a ruling withdrawn): nothing is "
                    "filed over it; settle it by hand")
        if card.outside:
            outside = ", ".join(link.issue for link in card.outside)
            return f"it is linked outside the tracker ({outside}): a person's call"
        return self._unlike_milestone(item, whole)

    def _unlike_milestone(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card is not in the milestone the migration files it in: its
        outcome's milestone gone from the tracker by its title (deleted or renamed on the web),
        or another milestone, or none.  A card filed already is not graded on its milestone,
        nor a card whose outcome's milestone is gone that sits in a milestone the plan refuses
        already (renamed on the web, :func:`_unnamed_milestones`): most often its refusal
        follows from that one (its own renamed); where it does not (its own deleted and the
        card moved into a milestone made by hand), the run after that milestone is settled
        names it."""
        if item.key in MAPPED:
            return None
        title = milestone_title(item, self.milestones)
        if title is not None and self._milestone(item) is None:
            if whole.milestone in self.unnamed:
                return None
            return (f"its milestone {title!r} is not on the tracker, "
                    "though the migration makes a milestone before any card in it: deleted or "
                    "renamed on the web, or this read lags its create? One made again would "
                    "hold none of its cards (a card's milestone is set only by its create); run "
                    "again later, and if it is refused still, settle it by hand")
        if whole.milestone != self._milestone(item):
            return (f"it is in milestone {whole.milestone}, not {self._milestone(item)}; a "
                    "card's milestone is set only by its create")
        return None

    def _misplaced(self, item: Item, whole: WholeCard) -> str | None:
        """Why ``item``'s card stands where the migration would not put it: a card filed
        already still marked, an open ruling whose filing finished, a closed card of work,
        another parent, a blocker the registries do not record (a step's in ``steps.md``, a
        finding's owner in ``ledger.md``, R-BAL395)."""
        card = whole.card
        if item.key in MAPPED and FILING in card.labels:
            return ("a card filed already still carrying the filing mark: its own filing is "
                    "unfinished; finish it with `quill file` before the run")
        if item.kind == "ruling" and card.is_open and FILING not in card.labels:
            return "an open ruling whose filing finished: a ruling is a closed record"
        if item.kind != "ruling" and not card.is_open:
            return f"it is closed, and a {item.kind} moves open"
        parent = None if item.links.parent is None else self.numbers.get(item.links.parent)
        if card.parent is not None and card.parent != parent \
                and card.parent not in self.unaccounted:
            return (f"it is a sub-issue of plan#{card.parent}, not "
                    f"{'plan#' + str(parent) if parent else 'none'}: re-homing is a person's call")
        expected = {self.numbers.get(key) for key in item.links.blockers}
        extra = sorted(set(card.blocked_by) - expected - self.unaccounted)
        if extra:
            return (f"it is blocked by {_named(extra)}, which the registries do not record: "
                    "a person's call")
        return None

    def _missing(self, item: Item, whole: WholeCard) -> list[Write]:
        """The writes ``item``'s card does not show done, but its mark's removal."""
        card = whole.card
        if item.kind == "ruling":
            return [Write(CLOSE_UNMARKED, item.key)] if FILING in card.labels else []
        missing = []
        if item.links.parent is not None and card.parent is None:
            missing.append(Write(LINK, item.key, item.links.parent))
        missing += [Write(BLOCK, item.key, blocker) for blocker in item.links.blockers
                    if self.numbers.get(blocker) not in card.blocked_by]
        if item.kind == "question" and not self._as_filed(whole):
            missing.append(Write(COMMENT, item.key))
        if item.links.place is not None and card.board_item is None:
            missing.append(Write(BOARD_ADD, item.key))
        return missing

    def _as_filed(self, whole: WholeCard) -> list[str]:
        """The card's As filed comments (:func:`as_filed_comments`)."""
        return as_filed_comments(whole.comments, self.tracked.app_login)


def _filing_order(steps: Sequence[Item]) -> tuple[list[Item], list[str]]:
    """``steps`` in an order that files each one after its parent and its open blockers, in
    any arc -- the order given wherever that allows, which it often does not: ``steps.md``
    lists its containers after their leaves, and a blocker may be a container listed later
    -- and a refusal naming the steps left unordered when some wait in a cycle."""
    position = {item.key: index for index, item in enumerate(steps)}
    waits = {item.key: {key for key in (item.links.parent, *item.links.blockers)
                        if key in position} for item in steps}
    order, done = [], set()
    while len(order) < len(steps):
        ready = [item for item in steps if item.key not in done and waits[item.key] <= done]
        if not ready:
            left = sorted(key for key in position if key not in done)
            return order, ["steps wait in a cycle, so these can be filed in no order (the cycle "
                           f"and every step waiting on it): {left}"]
        order.append(ready[0])
        done.add(ready[0].key)
    return order, []


def board_refusals(items: Sequence[Item], numbers: Mapping[str, int], tracked: Tracked,
                   refused: Iterable[str] = (), unaccounted: Iterable[int] = ()) -> list[str]:
    """Each board card the desired order has no place for: neither the card of an item with a
    place (a card filed already is a container, never on the board) nor the tracker's own
    below a card filed already whose item has a place, where it stands at that card's rank
    (draft 4 s.4: the coordinator settles it before the run).  An item's card ``refused``
    already, and a card ``unaccounted`` for already, is not refused again here."""
    skip = set(unaccounted) | {numbers[key] for key in refused if key in numbers}
    placed = {numbers[item.key] for item in items
              if item.links.place is not None and item.key in numbers and item.key not in MAPPED}
    ranked = {MAPPED[item.key] for item in items
              if item.links.place is not None and item.key in MAPPED}
    claimed = set(numbers.values())
    mapped = set(MAPPED.values())
    return [f"plan#{number} is on the board, and the desired order has no place for it: settle "
            "it before the run" for number in tracked.board
            if number not in skip and number not in placed
            and (number in claimed or _ancestor(number, mapped, tracked.cards) not in ranked)]


def _under_mapped(key: str, parents: Mapping[str, str | None]) -> bool:
    """Whether the item ``key`` sits below a card filed already in the registries' own links
    (its parent, its parent's, ...)."""
    seen = set()
    parent = parents.get(key)
    while parent is not None and parent not in seen:
        if parent in MAPPED:
            return True
        seen.add(parent)
        parent = parents.get(parent)
    return False


def desired_board(items: Sequence[Item], numbers: Mapping[str, int],
                  tracked: Tracked) -> tuple[list[int], list[str]]:
    """The board's desired order, card by card, and what refuses it: a board card with no
    place (:func:`board_refusals`, which :func:`plan` refuses already) and each item with a
    place that has no card yet, which a run orders the board only after filing (R-BAL238,
    draft 4 s.4).  Called on a tracker :func:`plan` refused nothing on; a card the order would
    hold twice there is refused by :func:`moves`.

    The items with a place, by tier and position: the ranked steps by rank, then the
    questions in the ledger's order.  A card filed already (:data:`_migrate_source.MAPPED`)
    is a container on the tracker, never on the board itself: at its rank stands every board
    card below it, in the order the board holds them now.
    """
    mapped = set(MAPPED.values())
    below = {number: _ancestor(number, mapped, tracked.cards) for number in tracked.board}
    placed = sorted((item for item in items if item.links.place is not None),
                    key=lambda item: item.links.place)
    desired, refusals = [], board_refusals(items, numbers, tracked)
    for item in placed:
        if item.key in MAPPED:
            desired += [number for number in tracked.board if below[number] == MAPPED[item.key]]
        elif item.key in numbers:
            desired.append(numbers[item.key])
        else:
            refusals.append(f"{item.key} has a place on the board and no card yet: file it "
                            "before the board is ordered")
    return desired, refusals


def _own(number: int, mapped: set[int], cards: Mapping[int, WholeCard]) -> bool:
    """Whether card ``number`` sits below one of the cards filed already, ``mapped``, at any
    depth, read up each card's links: its parent and, for a finding or a ruling, its owner
    -- the one card it is blocked by (:func:`_state.owner`, R-BAL395), which it sits below
    in place of a parent -- so a task under a finding owned below one is below it too."""
    seen, wanted = {number}, [number]
    while wanted:
        card = cards[wanted.pop()].card
        for up in (card.parent, owner(card)):
            if up in mapped:
                return True
            if up is not None and up not in seen and up in cards:
                seen.add(up)
                wanted.append(up)
    return False


def _ancestor(number: int, among: set[int], cards: Mapping[int, WholeCard]) -> int | None:
    """The first card of ``among`` that card ``number`` sits below (a sub-issue, at any
    depth), None for none."""
    seen = set()
    parent = cards[number].card.parent if number in cards else None
    while parent is not None and parent not in seen:
        if parent in among:
            return parent
        seen.add(parent)
        parent = cards[parent].card.parent if parent in cards else None
    return None


def moves(current: Sequence[int], desired: Sequence[int]) -> list[tuple[int, int | None]]:
    """The moves that put the board's cards ``current`` in the order ``desired`` (the same
    cards): each desired card outside a longest common subsequence of the two, top-down,
    moved to just after its desired predecessor (None: the top).  A card in the subsequence
    is never moved, a move never undoes one made before it, and equal orders take none
    (draft 4 s.4, V3-6).

    Raises:
        ValueError: When the two do not hold the same cards, each once.
    """
    if sorted(current) != sorted(desired) or len(set(desired)) != len(desired):
        raise ValueError("the board's present and desired orders hold different cards")
    common = set(_longest_common(current, desired))
    return [(number, desired[index - 1] if index else None)
            for index, number in enumerate(desired) if number not in common]


def _longest_common(first: Sequence[int], second: Sequence[int]) -> list[int]:
    """A longest common subsequence of two sequences of distinct cards."""
    lengths = [[0] * (len(second) + 1) for _ in range(len(first) + 1)]
    for i in range(len(first) - 1, -1, -1):
        for j in range(len(second) - 1, -1, -1):
            lengths[i][j] = (lengths[i + 1][j + 1] + 1 if first[i] == second[j]
                             else max(lengths[i + 1][j], lengths[i][j + 1]))
    common, i, j = [], 0, 0
    while i < len(first) and j < len(second):
        if first[i] == second[j]:
            common.append(first[i])
            i, j = i + 1, j + 1
        elif lengths[i + 1][j] >= lengths[i][j + 1]:
            i += 1
        else:
            j += 1
    return common
