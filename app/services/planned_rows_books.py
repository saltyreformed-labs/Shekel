"""
Shekel Budget App -- No books move or definition edit strands an unpaid recurring row

Plan step ``pay_calendar:C18-a``.  A recurring definition's occurrences start
above the books of every account it moves money in (ruling **R-PC85**), and
the walk names no occurrence whose row would land on or before them
(``recurrence._placement._lands_inside_the_books``).  A row the rule has
already generated and the owner has not settled -- an unpaid, still-Projected
bill or envelope -- is therefore STRANDED by any save that makes the books
drop the occurrence it answers: the rule stops naming it, and the maintain
pass retires a row its rule no longer names (plan step R10-a) whenever a pass
reaches that row's paycheck -- the save's own regeneration for a paycheck
ending on or after the edit's effective date (today unless the owner types an
earlier one), a later pass for an older one.  A row holding the owner's
records is retained with a notice instead; a record-free one is deleted and
the forecast rises by its amount without a word.  The owner decides instead --
marked paid it becomes a movement, cancelled it holds nothing -- and the doors
refuse the save until they have:

* the opening restatement, moving an account's books LATER (ruling
  **R-PC88**; ``opening_service._reject_books_open_on_or_after_planned_rows``);
* a recurring definition's edit, whatever field it changes (rulings
  **R-PC90** -- an account move -- and **R-PC91**, which widened it to any
  save, an envelope box unticked or (until recurrence:R5-a) a due day cleared):
  :func:`definition_edit_refusal`, asked by the transaction- and
  transfer-template edit routes AFTER the edit is applied, so it reads the
  state the save would leave.

**A row the books already hold may not become unpaid again** (ruling
**R-PC97**, developer 2026-09-23, the round-4 review's H2): a paid, received,
credited or cancelled row set back to Projected, which the database's books
boundary cannot see (its movement trigger watches a row only while it
carries a settle day, and a revert clears it).  Measured: a cancelled row
reverted onto its books day took $10.00 off the forecast (-90.00 ->
-100.00).  Round 9's door census found this and the conflict chooser (below)
as the doors left for a recurring row -- a census, not an argument, so a new
writer is not covered by it.  :func:`reject_revert_below_the_books` asks the
two questions the doors above ask, and each row type's one status door
refuses a revert it answers (``status_seam.apply_status_change``,
``transfer_service.apply_status_to_all_three``).  **A stopgap by design**:
plan step ``recurrence:R22`` designs the model under which no unpaid copy is
stored, so a revert only deletes a record and there is nothing to refuse.

**An ARCHIVED definition's hidden rows count** (ruling **R-PC93**, developer
2026-09-23; the round-2 review's H-B: a books move made while it was
archived saw nothing to strand, and the unarchive then restored rows inside
the opening).  Both doors count the rows its unarchive would restore as it
STANDS, before the save being graded
(:class:`~app.services.definition_unarchive.UnarchiveScope`, the scope the
unarchive routes restore by), and name such a row as the archived
definition's, with the remedy that reaches it.  The unarchive leaves
deleted, and names, a row the books already drop or hold (ruling
**R-PC95**), so the refusal names none it would not restore -- save where
the rule cannot be walked, and every hidden row is counted.  **Not covered**: an
unarchive still restores a row deleted by hand ABOVE the books, and so does
the chooser's "use the template" (R-BAL253), whether or not the rule names it
(ledger rows **REC-536** and **REC-535**, closed by plan step
``recurrence:R22``); a rule-less row, hand-added or left by a cleared rule,
is bounded by the books at no door but the unarchive (ledger row **PC-519**).

**Two questions per planned row, and either one refuses** (ruling **R-PC99**,
developer 2026-09-23, the round-6 review's H1).

*Does its schedule drop it?*  The WALK decides, never a stored day (this
step's first review, H1): :func:`~app.services.definition_unarchive
.books_reading` names the occurrences the books drop from the walk the save
would leave, and a row is matched to its occurrence by ``occurs_on``, the
key the maintain pass matches by, so a door refuses exactly the rows the
pass would stop naming.
Reading a row's STORED due day instead let a save through that strands one:
the regeneration re-dates every still-named row by the NEW rule, so funding a
bill from the paycheck containing its date moves its cash day back onto its
scheduled day, inside the books, while the stored day still read as outside
them.  The day a refusal names is the walk's for the placement
(:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`: the due day
for a bill, ruling **R-PC86**; the paycheck's last day for an envelope,
ruling **R-PC89**) -- the row as the save would leave it, which is the
wording the developer ruled for R-PC91's message.  The books are its
DEFINITION's, since those are what its walk is bounded by.

*Do the books of the account it SITS ON hold its own day?*
(:func:`~app.services.definition_unarchive.own_day_held`.)  The balance
counts a row on the account it sits on, on its stored day, so a
still-Projected row on or before that account's opening is counted a second
time whether or not its walk still names it.  Ruling **R-PC98** (the round-5
review's H2) asked it first of an ORPHAN, a row the walk names nowhere --
left behind by a rule edit, its start moved later, say -- which no dropped
occurrence catches; R-PC99 asks it of every planned row, against the books
of the account it sits on rather than its definition's: a definition's
account move leaves the rows of paychecks that had already ended on the
account it left, and that account's restatement past them committed with
``-$100.00`` in the forecast where the books rule gives ``-$80.00``
(measured, orphans and named rows alike).  Every door asks it -- an edit
door of each row as the save LEAVES it, its regeneration applied (the
round-7 review's M1) -- and the refusal names the books it asked of: a row
its schedule would drop without sitting inside the books that drop it is
refused for the pass that would delete it, never as sitting inside them.

**An OVERRIDDEN row is refused over too.**  The pass keeps a row the owner
re-priced as a conflict rather than retiring it, but the conflict chooser's
"use" hands it back to its definition, and the next pass that reaches it then
retires it like any other.  A row that answers NO occurrence (``occurs_on``
``NULL``) is asked neither question here: no walk names it with or without
the books, and ruling **R-PC96** judges it at the unarchive alone, leaving
the books move allowed (finding REC-516 retains it either way).

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data and ORM
reads in, a row or a sentence out; no Flask symbol, no writes, no clock.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple

from sqlalchemy import and_, or_

from app.exceptions import ValidationError
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import (
    BalanceContext,
    definition_books,
    definition_money_accounts,
    money_account_columns,
    resolved_with_books,
)
from app.services.definition_unarchive import (
    UnarchiveScope,
    books_named,
    books_reading,
    own_day_held,
    rows_held_where_they_sit,
    rows_of,
    scope_holding_nothing_back,
    unarchive_scope,
    unarchive_scope_on,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import (
    RecurrenceGenerationError,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    recurrence_spec,
)
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause, reverts_to_projected

logger = logging.getLogger(__name__)


class BooksHolding(NamedTuple):
    """Which books a stranded row is compared with (ruling R-PC99).

    Attributes:
        opened_on: Those books' opening day.
        holder: What they are read off, for :meth:`StrandedRow.books`: the
            ROW when the books of the account it sits on hold its own day --
            as an edit's regeneration rewrites it, where it does -- else its
            definition, whose walk drops it.
        sits_inside: Whether it sits on those books: ``False`` for a row its
            schedule would drop although it sits on another account -- one
            its definition left behind by an account move -- which is
            refused for the pass that would delete it, never described as
            sitting inside them.
    """

    opened_on: date
    holder: object
    sits_inside: bool


@dataclass(frozen=True)
class StrandedRow:
    """A still-projected recurring row a save would leave below the books.

    Attributes:
        name: What the refusal calls it: the row's own name for a live row,
            its DEFINITION's name for a hidden one (*is_hidden*), which the
            owner finds on the archived list and nowhere else.
        books_day: The day the books are compared with, for the placement
            the walk gives the row
            (:meth:`~app.services.recurrence.ResolvedRecurrence.books_day`).
        is_envelope: Whether *books_day* is the row's paycheck's last day
            (an envelope) rather than its due day, which is what the refusal
            has to say to be true.
        is_hidden: Whether the row is hidden by its definition's archive --
            one its unarchive would bring back (ruling **R-PC93**) -- which
            the owner cannot see, let alone mark paid, until they unarchive.
        holding: The books it is compared with (:class:`BooksHolding`), or
            ``None`` where no sentence names them -- read as sitting inside.
        dropped: Whether its schedule's walk drops its occurrence, so a
            later maintain pass would retire it: the "stop being planned"
            half of the refusal.
    """

    name: str
    books_day: date
    is_envelope: bool
    is_hidden: bool = False
    holding: BooksHolding | None = None
    dropped: bool = True

    def described(self) -> str:
        """Return the row as the refusals name it: its name and the day compared.

        Returns:
            ``"Rent" is still projected and due 2026-01-01`` for a bill, or
            ``"Groceries" is still projected in the paycheck ending
            2026-09-23`` for an envelope -- the day the owner has to move
            the row past, in the words ruling **R-PC91** gave it.  A hidden
            row reads ``"Rent" is archived and still holds an unpaid item due
            2026-01-01 that unarchiving would bring back``, R-PC93's words.
        """
        where = (
            f"in the paycheck ending {self.books_day.isoformat()}"
            if self.is_envelope
            else f"due {self.books_day.isoformat()}"
        )
        if self.is_hidden:
            return (
                f'"{self.name}" is archived and still holds an unpaid item '
                f"{where} that unarchiving would bring back"
            )
        if not self.is_envelope:
            where = f"and {where}"
        return f'"{self.name}" is still projected {where}'

    def remedy(self) -> str:
        """Return what the owner does first, the clause every refusal ends on.

        Returns:
            ``Mark it paid or cancel it first`` for a live row; for a hidden
            one, ``Unarchive "Rent", mark that item paid or cancel it`` --
            the one door that reaches a row the owner cannot see (ruling
            **R-PC93**), naming the definition so the sentence cannot be read
            as unarchiving the opening or the row.  Both are true for every
            row these refusals name (developer, 2026-09-23, round 9's M1):
            R-PC88's "or move it later" could never work, since every such
            row was made by a recurring schedule -- its due day is the
            schedule's, which the form refuses to change, and a move to a
            later paycheck keeps it, so the refusal came back unchanged -- and
            R-PC93's "or delete "Rent" for good" is refused for any definition
            with payment history (round 8's M-1).
        """
        if self.is_hidden:
            return f'Unarchive "{self.name}", mark that item paid or cancel it'
        return "Mark it paid or cancel it first"

    def consequence(self) -> str:
        """Return what the save would do to the row, the refusals' middle sentence.

        Returns:
            ``An opening is the balance at the END of its day, so that unpaid
            item would sit inside it`` -- ending ``and stop being planned``
            when its schedule drops it too -- for a row inside the books it
            sits on; for a row its schedule drops while it sits on another
            account (ruling **R-PC99**), ``Its schedule would stop producing
            that unpaid item, so the next pass to reach it would delete it
            without a word`` -- the developer's reason such a row still
            blocks the move; that pass is the save's own regeneration for a
            row in its paycheck window, a later one for a row before it.  No
            pass reaches a HIDDEN row (the round-7 review's L1): its sentence
            stops at its schedule.
        """
        if self.holding is not None and not self.holding.sits_inside:
            then = "" if self.is_hidden else (
                ", so the next pass to reach it would delete it without a word"
            )
            return f"Its schedule would stop producing that unpaid item{then}."
        tail = " and stop being planned" if self.dropped else ""
        return (
            "An opening is the balance at the END of its day, so that unpaid "
            f"item would sit inside it{tail}."
        )

    def books(self) -> str:
        """Return the accounts whose books it is compared with, in the possessive.

        Read only when a sentence names them
        (:func:`~app.services.definition_unarchive.books_named`), off
        :attr:`holding`'s holder: the accounts the row sits on, or its
        definition's.

        Returns:
            ``Checking's``, or ``Src's and Dst's``.
        """
        return books_named(self.holding.holder, self.holding.opened_on)


@dataclass(frozen=True)
class DefinitionWalk:
    """One recurring definition as a save would leave it.

    Attributes:
        resolved: The definition's recurrence, carrying the books floor the
            save would leave -- or ``None`` for a definition a restatement
            reaches only through the rows it left ON the account (ruling
            **R-PC99**): its walk is bounded by books the restatement does
            not move, so only where its rows sit is asked (the round-7
            review's L2; an archived one's scope still walks its rule).
        definition: The
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate` itself --
            whose rows are asked after (:func:`_planned_rows`) and whose name
            a hidden row's refusal gives.
        restorable: The rows its unarchive would restore, read off the
            definition as it stands BEFORE the save
            (:class:`~app.services.definition_unarchive.UnarchiveScope`), or
            ``None`` for an active definition, which has none.  No
            default: a walk built without asking would count no hidden row,
            which is the round-2 review's H-B.
        save_pass: At an edit door, what the save's own regeneration does
            to the definition's rows
            (:class:`~app.services.recurrence_engine.RegenerationPreview`);
            ``None`` where it regenerates nothing, every row asked as stored.
    """

    resolved: ResolvedRecurrence | None
    definition: TransactionTemplate | TransferTemplate
    restorable: UnarchiveScope | None
    save_pass: object | None = None


class SaveRegeneration(NamedTuple):
    """How an edit's save regenerates its definition's rows.

    Handed in by the edit door: this module cannot import the transfer
    engine, which imports the two status doors that import it.

    Attributes:
        preview_fn: The engine's ``preview_regeneration_for_template``.
        effective_from: The date the save's regeneration maintains from.
    """

    preview_fn: object
    effective_from: date


def first_row_below_the_books(
    calendar: PayCalendar, walks: Iterable[DefinitionWalk], *,
    books_memo: dict | None = None, sitting_on: int | None = None,
) -> StrandedRow | None:
    """Return the earliest planned row a save would strand below the books.

    **The one question every door refusing to strand a row asks**, over one
    definition (an edit) or every definition with money or rows in an
    account (an opening restatement).  Planned means still Projected and
    either live or, for an archived definition, one its unarchive would
    bring back (:func:`_planned_rows`, rulings **R-PC93** and **R-PC95**),
    in any scenario (one definition's rule walks the same in every scenario,
    and an opening is scenario-free, ruling **R-GX**); a settled row is a
    movement, and the movement rules speak for it.  Each dated planned row
    is asked TWO things, and either strands it (ruling **R-PC99**):

    * **does its schedule drop it?** -- whether its ``occurs_on`` is an
      occurrence :func:`~app.services.definition_unarchive.books_reading`
      reports the walk's books floor dropping, the key the maintain pass
      matches by, so the rows found are the ones a pass would stop naming;
    * **do the books of the account it SITS ON hold its own day?**
      (:func:`~app.services.definition_unarchive.own_day_held`) -- the
      balance counts it there, so on or before that opening it is counted a
      second time.  Asked of an ORPHAN, a row the walk names nowhere, since
      ruling **R-PC98** (measured: a start moved later, the books restated
      onto the second old row's day, ``-$20.00`` counted inside the
      opening), and of every planned row, against the account it sits on
      rather than its definition's, since R-PC99 (measured: an account a
      definition moved off, restated past the rows it left there,
      ``-$100.00`` against the books rule's ``-$80.00``).

    An undated row (``occurs_on`` ``NULL``) is asked neither: ruling
    **R-PC96** judges it at the unarchive alone.

    Args:
        calendar: The owner's pay calendar, which every walk places on.
        walks: The definitions to ask about, each as the save would leave it.
        books_memo: The ``account_id -> opened_on`` memo the second question
            reads the books where a row sits through; a restatement's holds
            its candidate day.  ``None`` reads every account's governing
            opening.
        sitting_on: The account a restatement restates: only the rows
            sitting on it are asked the second question there, since its
            books are the only ones the restatement moves.  ``None``
            elsewhere.

    Returns:
        The stranded row with the EARLIEST compared day across every walk
        (ties to transactions before transfers, then the lower id), named by
        its own day when the books it sits on hold it, else by the day its
        walk compares for its placement -- or ``None`` when the save strands
        none.
    """
    memo = {} if books_memo is None else books_memo
    candidates = []
    for walk in walks:
        candidates.extend(_stranded_by(calendar, walk, memo, sitting_on))
    if not candidates:
        return None
    first = min(
        candidates,
        key=lambda candidate: (
            candidate.books_day, candidate.table_order, candidate.row_id,
        ),
    )
    return StrandedRow(
        name=first.walk.definition.name if first.is_hidden else first.name,
        books_day=first.books_day,
        is_envelope=definition_books(first.walk.definition, memo).is_envelope,
        is_hidden=first.is_hidden,
        holding=first.holding,
        dropped=first.dropped,
    )


class _Candidate(NamedTuple):
    """One planned row a save would strand, with both questions' answers.

    :func:`first_row_below_the_books` names the one with the earliest
    ``books_day``, ties to transactions before transfers
    (``table_order``), then the lower ``row_id``.
    """

    books_day: date
    table_order: int
    row_id: int
    name: str
    is_hidden: bool
    walk: DefinitionWalk
    holding: BooksHolding
    dropped: bool


def _stranded_by(
    calendar: PayCalendar, walk: DefinitionWalk, memo: dict,
    sitting_on: int | None,
) -> list:
    """Return the refusal candidates among *walk*'s planned rows (ruling R-PC99).

    Args:
        calendar: The owner's pay calendar.
        walk: The definition as the save would leave it.
        memo: The books memo (:func:`first_row_below_the_books`).
        sitting_on: The restated account, or ``None``.

    Returns:
        One :class:`_Candidate` per planned row either question strands: a
        row the books it sits on hold is named by its own day and those
        books; one only its schedule drops, by the walk's day and the
        definition's books, and as sitting inside them only when it sits on
        the definition's own accounts.  At an edit door the second question
        asks each row as the save LEAVES it (the round-7 review's M1): one
        its regeneration rewrites as rewritten, one it deletes not at all
        (the first question still asks that one: R-PC90's loss).
    """
    table_order, model, template_fk = rows_of(walk.definition)
    planned = _planned_rows(model, template_fk, walk)
    dropped = (
        {} if walk.resolved is None
        else _dropped_rows(calendar, walk, model, planned)
    )
    rewrites, retires = (
        ({}, frozenset()) if walk.save_pass is None
        else (walk.save_pass.rewrites, walk.save_pass.retires)
    )
    held = rows_held_where_they_sit(
        walk.definition,
        (
            *planned, model.occurs_on.isnot(None),
            *_sitting(model, sitting_on),
            *((model.id.notin_(list(retires)),) if retires else ()),
        ),
        calendar, memo, rewrites=rewrites,
    )
    its_accounts = set(definition_money_accounts(walk.definition))
    return [
        *(
            _Candidate(
                own.day, table_order, row_id, row.name, row.is_deleted, walk,
                BooksHolding(own.opened_on, rewrites.get(row_id, row), True),
                row_id in dropped,
            )
            for row_id, (own, row) in held.items()
        ),
        *(
            _Candidate(
                day, table_order, row_id, name, is_deleted, walk,
                BooksHolding(
                    walk.resolved.books_opened_on, walk.definition,
                    set(accounts) <= its_accounts,
                ),
                True,
            )
            for row_id, (day, name, is_deleted, accounts) in dropped.items()
            if row_id not in held
        ),
    ]


def _sitting(model, sitting_on: int | None) -> tuple:
    """Return the criteria for *model*'s rows sitting on *sitting_on*, if one is named."""
    if sitting_on is None:
        return ()
    return (or_(*(
        column == sitting_on for column in money_account_columns(model)
    )),)


def _dropped_rows(
    calendar: PayCalendar, walk: DefinitionWalk, model, planned: tuple,
) -> dict:
    """Return *walk*'s planned rows answering an occurrence its books drop.

    Args:
        calendar: The owner's pay calendar.
        walk: The definition as the save would leave it.
        model: ``Transaction`` or ``Transfer``.
        planned: The definition's planned-row criteria (:func:`_planned_rows`).

    Returns:
        ``{row id: (the walk's compared day for its placement, name,
        is_deleted, the accounts it sits on)}``.
    """
    inside = books_reading(walk.resolved, calendar).inside
    if not inside:
        return {}
    columns = money_account_columns(model)
    return {
        row_id: (inside[occurs_on], name, is_deleted, tuple(
            account_id for account_id in accounts if account_id is not None
        ))
        for row_id, name, occurs_on, is_deleted, *accounts in (
            db.session.query(
                model.id, model.name, model.occurs_on, model.is_deleted,
                *columns,
            )
            .filter(*planned, model.occurs_on.in_(list(inside)))
            .all()
        )
    }


def _planned_rows(model, template_fk, walk: DefinitionWalk) -> tuple:
    """Return the SQL criteria for a definition's rows a books move must not strand.

    Its live, still-Projected rows; and, while it is ARCHIVED, the rows its
    unarchive would bring back as well (ruling **R-PC93**), read through
    :meth:`~app.services.definition_unarchive.UnarchiveScope.restores` over
    the definition as it stands -- the scope both unarchive routes restore
    by, so this counts exactly what an unarchive could put back below the
    books, and never a row the unarchive would leave deleted (ruling
    **R-PC95**).  An ACTIVE definition's soft-deleted rows are its owner's
    own deletions, which no unarchive of it restores (the unarchive routes
    refuse an active definition) and only the conflict chooser revives,
    never one its books hold (ruling **R-BAL253**, ledger row **REC-535**).

    Args:
        model: ``Transaction`` or ``Transfer``.
        template_fk: The column naming the row's definition.
        walk: The definition's :class:`DefinitionWalk`, whose ``restorable``
            is ``None`` for an active definition.

    Returns:
        The criteria, for ``query.filter(*criteria)``.
    """
    live = and_(
        template_fk == walk.definition.id,
        is_projected_clause(model),
        model.is_deleted.is_(False),
    )
    if walk.restorable is None:
        return (live,)
    return (or_(live, and_(*walk.restorable.restores())),)


def restorable_before_the_edit(
    template, ctx: BalanceContext,
) -> UnarchiveScope | None:
    """Return the rows *template*'s unarchive would restore, asked BEFORE its edit.

    Only an ARCHIVED definition has rows an unarchive would bring back
    (ruling **R-PC93**).  Each edit door asks this before it applies a
    single field, and hands the answer to :func:`definition_edit_refusal`
    once the edit is whole: that refusal grades the state the save would
    LEAVE, and the rows an unarchive would restore are a fact about the
    state it would REPLACE.  Read after the edit, the scope would already
    leave out every hidden row the edit moves below the books, and the
    refusal could never fire over one (it would still fire over an archived
    definition's LIVE row, which no scope reads).

    **A stored rule the recurrence package cannot walk counts every hidden
    row** (the scope of an unarchive that holds nothing back), because the
    edit form is also where such a rule is repaired
    (``_recurrence_form_refusals.UNREPAIRED_CADENCE_CANNOT_BE_CLEARED``),
    and a refusal over rows it cannot place is the careful answer where a
    500 would lock the owner out of the repair.

    Args:
        template: The owner-checked transaction or transfer template, no
            field of the edit yet applied.
        ctx: The edit door's PRE-WRITE read pass.  Its resolution memo is
            keyed by the rule's spec and the definition's books, so the
            edited rule resolves afresh afterwards.

    Returns:
        The :class:`~app.services.definition_unarchive.UnarchiveScope`, or
        ``None`` for an active definition.
    """
    if template.is_active:
        return None
    return _scope_or_every_hidden_row(
        template, lambda: unarchive_scope_on(template, ctx),
    )


def _scope_or_every_hidden_row(definition, scope_of) -> UnarchiveScope:
    """Return ``scope_of()``, or every hidden row when the rule cannot be walked.

    Args:
        definition: The archived transaction or transfer template.
        scope_of: Reads its :class:`UnarchiveScope` through a walk.

    Returns:
        The scope, or :func:`scope_holding_nothing_back`'s.
    """
    try:
        return scope_of()
    except (RecurrenceResolutionError, RecurrenceGenerationError):
        logger.warning(
            "Counting every hidden row of archived %s %d: its stored "
            "recurrence rule cannot be walked.",
            type(definition).__name__, definition.id, exc_info=True,
        )
        return scope_holding_nothing_back(definition)


def first_row_an_opening_strands(
    account_id: int, opened_on: date, calendar: PayCalendar,
) -> StrandedRow | None:
    """Return the earliest row books opening on *opened_on* would strand, or ``None``.

    **Ruling R-PC88's one producer**, read by the restatement door's refusal
    (``opening_service._reject_books_open_on_or_after_planned_rows``) and by
    the books-opening card's date ceiling (``routes/accounts/opening``), so
    the day the card stops at and the row the door names cannot come from two
    answers.  Every RECURRING definition that moves money in the account --
    an archived one included, whose hidden rows its unarchive would bring
    back (ruling **R-PC93**) -- is walked with the books it would have if the
    account opened on *opened_on*: the later of that day and the
    definition's OTHER accounts' governing openings
    (:func:`~app.services.balance_at.definition_books`, the candidate
    standing in for this account's own), composed by the ONE composition
    (:func:`~app.services.balance_at.resolved_with_books`).  That walk is
    asked after the definition's OWN rows, wherever they sit.  And every row
    SITTING ON the account is asked whether the candidate books hold its own
    day (ruling **R-PC99**), a definition that moved off it included: its
    rows of paychecks that had already ended stayed here, and the balance
    counts them here.  Nothing such a definition's rule says bounds rows by
    this account's books, so it is walked only if archived, for its scope.
    An archived definition's restorable rows are read off the SAME
    composition over the books as they stand (ruling **R-PC95**), or every
    hidden row where its rule cannot be walked.  A rule-less definition is
    not asked (ruling **R-PC96** judges its rows at the unarchive alone).

    Args:
        account_id: The account whose books would open on *opened_on*.
            Assumed the owner's; the definitions referring to it are too.
        opened_on: The candidate opening day.
        calendar: The owner's pay calendar.

    Returns:
        The earliest stranded row, or ``None``.

    Raises:
        RecurrenceResolutionError: The stored rule of a definition moving
            money in the account cannot be resolved.
        RecurrenceGenerationError: Its resolved value names something the
            walk cannot place.
    """
    # ONE memo for every definition and every row, holding the candidate:
    # the other accounts are read once each, as they stand, and this one as
    # it would.  A second holds every account as it stands, for the rows an
    # archived definition's unarchive would restore.
    memo = {account_id: opened_on}
    standing_memo: dict = {}
    walks = []
    for definition, moves_money_in in _recurring_definitions_bounding(account_id):
        resolved = None
        if moves_money_in:
            resolved = resolved_with_books(
                recurrence_spec(definition.recurrence_rule), calendar,
                definition_books(definition, memo),
            )
            if resolved is None:
                continue
        restorable = None
        if not definition.is_active:
            restorable = _scope_or_every_hidden_row(
                definition,
                lambda held=definition: unarchive_scope(
                    held,
                    resolved_with_books(
                        recurrence_spec(held.recurrence_rule), calendar,
                        definition_books(held, standing_memo),
                    ),
                    calendar,
                ),
            )
        walks.append(DefinitionWalk(resolved, definition, restorable))
    return first_row_below_the_books(
        calendar, walks, books_memo=memo, sitting_on=account_id,
    )


def _recurring_definitions_bounding(account_id: int) -> list:
    """Return every recurring definition a restatement of *account_id* asks after.

    A transaction or transfer template carrying a recurrence rule, archived
    or not, that either names the account in one of its money-account
    columns (:func:`~app.services.balance_at.money_account_columns`, the
    names the books floor reads off a definition) or owns a still-Projected
    row SITTING ON it -- one it left behind when it moved to another account
    (ruling **R-PC99**), live or hidden.

    Args:
        account_id: The account.

    Returns:
        ``(definition, moves_money_in)`` pairs, transaction templates first,
        each table by id: *moves_money_in* is ``False`` for a definition
        reached only through its rows, whose schedule the restatement does
        not bound.
    """
    definitions = []
    for model, rule_fk in (
        (TransactionTemplate, RecurrenceRule.transaction_template_id),
        (TransferTemplate, RecurrenceRule.transfer_template_id),
    ):
        _table_order, row_model, template_fk = rows_of(model)
        moves_money_in = or_(*(
            column == account_id for column in money_account_columns(model)
        ))
        rows_sit_on_it = model.id.in_(
            db.session.query(template_fk).filter(
                is_projected_clause(row_model),
                *_sitting(row_model, account_id),
            )
        )
        definitions.extend(
            (definition, bool(moves))
            for definition, moves in (
                db.session.query(model, moves_money_in)
                .join(RecurrenceRule, rule_fk == model.id)
                .filter(or_(moves_money_in, rows_sit_on_it))
                .order_by(model.id)
                .all()
            )
        )
    return definitions


def reject_revert_below_the_books(row, new_status_id: int) -> None:
    """Refuse setting *row* back to Projected when the books drop or hold it (R-PC97, R-PC99).

    **Ruling R-PC97's one refusal** (developer 2026-09-23, the C18-a
    round-4 review's H2), asked by each row type's one status door ahead of
    any write -- ``status_seam.apply_status_change`` for a transaction,
    ``transfer_service.apply_status_to_all_three`` for a transfer, before
    either shadow is written -- and acting only on a REVERT of a LIVE row (a hidden one is no
    plan, ruling **balance:R-BAL248**): a paid, received, credited or cancelled row about to go
    back to Projected.  A row whose occurrence its definition's books drop may not: unpaid, it
    would sit inside the opening balance, and the maintain pass
    deletes a still-Projected row its rule no longer names without a word
    once a pass reaches its paycheck (plan step R10-a).  The questions are
    the two the books refusals above ask (ruling **R-PC99**): whether the
    books of the account the row SITS ON hold its own day
    (:func:`~app.services.definition_unarchive.own_day_held`, asked first,
    of an orphan a rule edit left behind too, ruling **R-PC98**), and which
    occurrences its definition's walk drops
    (:func:`~app.services.definition_unarchive.books_reading`), a row
    matched to its occurrence by ``occurs_on`` -- over the walk as it
    stands, composed by the ONE composition the opening door takes
    (:func:`~app.services.balance_at.resolved_with_books` over
    :func:`~app.services.balance_at.definition_books`).  The refusal names
    the books that hold it: the account it sits on, or its definition's.

    **Its known cost, stated rather than rounded off**: a settled row's
    LOCKED fields -- its amount, paycheck, category and due date
    (``state_machine.finalised_edit_rejection``) -- are editable only after
    a revert, so a row due on or before its books but settled after them
    can no longer have those corrected until the books are restated to open
    before its due day.  (What a settled row records is corrected in place
    and is untouched: a transaction's actual figure and the account it was
    paid from, ``transaction_service._door._correction_for_status``, and a
    transfer's settle day per side, an identity pass through
    ``transfer_service._status.apply_status_to_all_three``.)  Production
    held SIX such recurring rows on 2026-09-23, measured by this refusal
    itself over every settled templated row: four transactions (one of them
    CANCELLED, so it cannot be reactivated) and two transfers, every one
    held on its own due day by the books of an account it sits on (the
    rows are named in the lane's records, not here: ruling **R-BAL132**).
    **A stopgap by design**: plan step
    ``recurrence:R22`` designs the model in which no unpaid copy is stored,
    under which a revert deletes a record and this refuses nothing.

    **A schedule the recurrence package cannot walk refuses the revert**:
    with no walk there is no telling whether the books drop the row, and the
    edit form is the door that repairs such a rule.  A definition with NO
    books floor is not walked, since its walk drops nothing (the round-6
    review's L3).

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` whose status is about to
            change, not yet changed.  A transfer's shadow has no definition
            of its own and is never held here: its transfer is, at the
            transfer's own door.
        new_status_id: The ``ref.statuses.id`` it is moving to.

    Raises:
        ValidationError: When the move is a revert to Projected and the
            books of the account the row sits on hold its own day, or its
            definition's books drop its occurrence, or its schedule cannot be
            walked.  Nothing is refused for a row no recurring definition
            names (a rule-less item's row, a link-less row: ledger row
            **PC-519**'s, bounded at no door but the unarchive), for an
            undated row (``occurs_on`` ``NULL``, which ruling **R-PC96**
            judges at the unarchive alone), for a HIDDEN row (R-BAL248), for an owner
            with no pay periods, or for a row the books do not hold.
    """
    if not reverts_to_projected(row, new_status_id) or row.is_deleted:
        return
    refusal = _revert_refusal(row)
    if refusal is not None:
        raise ValidationError(refusal)


def _revert_refusal(row) -> str | None:
    """Return why reverting *row* to Projected is refused, or ``None``.

    Args:
        row: See :func:`reject_revert_below_the_books`.

    Returns:
        The refusal's sentence, or ``None`` when nothing refuses it.
    """
    definition = row.template
    if definition is None or not definition.recurs or row.occurs_on is None:
        # A rule-less item's row, a link-less row (PC-519's) and an undated
        # row (R-PC96's): bounded at no door but the unarchive, so not here.
        return None
    calendar = calendar_for(definition.user_id)
    books = definition_books(definition, {})
    held = own_day_held(row, calendar, {}, is_envelope=books.is_envelope)
    if held is not None:
        return _revert_sentence(
            row, held.day, books_named(row, held.opened_on), held.opened_on,
            is_envelope=books.is_envelope,
        )
    if books.opened_on is None:
        return None
    try:
        resolved = resolved_with_books(
            recurrence_spec(definition.recurrence_rule), calendar, books,
        )
        reading = books_reading(resolved, calendar)
    except (RecurrenceResolutionError, RecurrenceGenerationError):
        logger.warning(
            "Refusing to revert %s %d to Projected: its definition's stored "
            "recurrence rule cannot be walked.",
            type(row).__name__, row.id, exc_info=True,
        )
        return (
            f'"{row.name}" cannot be set back to projected while the '
            f'schedule of "{definition.name}" cannot be read: repair its '
            "schedule first."
        )
    if row.occurs_on not in reading.inside:
        return None
    return _revert_sentence(
        row, reading.inside[row.occurs_on],
        books_named(definition, books.opened_on), books.opened_on,
        is_envelope=books.is_envelope,
    )


def _revert_sentence(
    row, day: date, books: str, opened_on: date, *, is_envelope: bool,
) -> str:
    """Return the revert refusal's sentence for *row*, compared on *day*.

    Args:
        row: The row being reverted.
        day: The day compared: its own, or its walk's for its placement.
        books: The accounts whose books hold it, in the possessive.
        opened_on: Those books' opening day.
        is_envelope: Whether *day* is a paycheck's last day.

    Returns:
        ``"Rent" is due 2026-01-02, on or before Checking's books, which
        open 2026-01-16, so it cannot be planned as unpaid.``
    """
    where = (
        f"in the paycheck ending {day.isoformat()}" if is_envelope
        else f"due {day.isoformat()}"
    )
    return (
        f'"{row.name}" is {where}, on or before {books} books, which open '
        f"{opened_on.isoformat()}, so it cannot be planned as unpaid."
    )


def definition_edit_refusal(
    template, ctx, restorable: UnarchiveScope | None,
    regeneration: SaveRegeneration,
) -> str | None:
    """Return why saving *template*'s edit would strand a row, or ``None``.

    Rulings **R-PC90** and **R-PC91** (developer, 2026-09-22): a recurring
    definition's edit is refused when the state it would SAVE leaves a
    still-projected row of that definition answering an occurrence the books
    drop, or inside the books of the account it sits on (ruling **R-PC99**)
    -- whatever field changed.  An account moved onto one whose books open
    later, an envelope box unticked (its rows then compare on their due day,
    not their paycheck's last day), a funding switch moving the row's cash
    day back onto its scheduled day, or a row that already sat below its
    books: one check, of the state the save LEAVES, so no field has to be
    remembered.  An ARCHIVED definition's edit counts the rows its
    unarchive would bring back too (ruling **R-PC93**) -- as it stood BEFORE
    the edit (*restorable*, :func:`restorable_before_the_edit`), since a row
    its books already dropped stays deleted when it is unarchived (ruling
    **R-PC95**).

    **The state the save leaves includes its own regeneration** (the round-7
    review's M1, :func:`_stranded_by`), read off that pass's OWN decision
    (*regeneration*'s preview), never a second spelling of its window: the
    rows asked as stored are exactly those it leaves alone -- paychecks
    ending before the effective date, other scenarios, its conflicts.

    **Asked after the edit is applied and before regeneration**, so the
    rule, the floor and the envelope flag are the save's own, all through
    the SAME composition the walk takes (the pass's memoised resolution,
    :func:`~app.services.recurring_definition.resolved_rule_of`, reading the
    rule's columns and the definition's accounts and ``is_envelope`` off the
    edited objects).

    Args:
        template: The edited
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate`, its new
            field values and rule applied, not yet committed.
        ctx: A read pass for the owner built after the door's writes, as
            the regeneration builds its own (for a definition with a rule,
            nothing is written between them): the state the save leaves.
        restorable: :func:`restorable_before_the_edit`'s answer, asked
            before the edit was applied; ``None`` for an active definition.
        regeneration: How the save regenerates (:class:`SaveRegeneration`),
            previewed only once the definition has a rule to walk.

    Returns:
        The refusal's sentence, or ``None`` when the save strands nothing --
        including a definition with no rule, whose rows no walk names, and an
        owner with no pay periods, who has no rows.
    """
    resolved = resolved_rule_of(template, ctx)
    if resolved is None:
        return None
    save_pass = None
    if ctx.scenario is not None:
        save_pass = regeneration.preview_fn(
            template, GenerationSchedule.for_pass(ctx), ctx.scenario_id,
            effective_from=regeneration.effective_from,
        )
    row = first_row_below_the_books(
        ctx.calendar(),
        (DefinitionWalk(resolved, template, restorable, save_pass=save_pass),),
    )
    if row is None:
        return None
    return (
        f"This change cannot be saved: {row.described()}, and {row.books()} "
        f"books open {row.holding.opened_on.isoformat()}.  "
        f"{row.consequence()}  "
        f"{row.remedy()}, then make the change."
    )


__all__ = [
    "DefinitionWalk",
    "SaveRegeneration",
    "StrandedRow",
    "definition_edit_refusal",
    "first_row_an_opening_strands",
    "first_row_below_the_books",
    "reject_revert_below_the_books",
    "restorable_before_the_edit",
]
