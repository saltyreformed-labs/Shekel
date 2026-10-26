"""
Shekel Budget App -- The Rows a Definition's Unarchive Brings Back

ONE statement of which rows unarchiving a recurring definition restores (plan
step ``pay_calendar:C18-a``, rulings **R-PC93** and **R-PC95**, developer
2026-09-23), read by the two doors that restore them --
``routes/templates/crud.unarchive_template`` and
``routes/transfers/lifecycle.unarchive_transfer_template`` -- and by the
doors that refuse to strand a planned row below an account's books
(``app.services.planned_rows_books``).

**Why the refusal reads it.**  Archiving a definition hides its still-Projected
rows, and unarchiving brings them back.  A books move made while the
definition was archived saw no live row to strand, so it went through; the
unarchive then restored rows inside the opening balance, and a recurring
transfer's unarchive -- whose maintain pass retires a row the rule no longer
names -- deleted the current paycheck's row without a word, measured by the
C18-a round-2 review.  R-PC93 treats the rows an unarchive would bring back as
planned rows at every door that moves a definition's books.  That holds only
while the refusal counts EXACTLY the rows the unarchive restores, which is why
both read :class:`UnarchiveScope`: two spellings of one scope are two homes
that have to agree (``CLAUDE.md`` rule 14).

**A row its definition's books already drop STAYS DELETED** (ruling
**R-PC95**, the C18-a round-3 review's H-C).  The archive's hide and the
owner's own delete of one occurrence write the same flag -- a hand delete of
a recurring row is a SOFT delete, whose tombstone keeps the maintain pass from
re-creating the occurrence -- and nothing on the row tells the two apart.  So
an unarchive that restored every soft-deleted row also restored one its owner
deleted while the definition was active, below the books when those had
since moved past it (allowed: a deleted row is not planned), inside the
opening balance: measured through the real routes, on dev and on this step
alike, the row's $10.00 counted a second time.  Now the rows answering an
occurrence the books drop, as they stand when the definition is unarchived,
are left deleted, and the unarchive says so (:func:`stays_deleted_notice`),
naming every one.  **The scope judges each hidden row as it stands and never
asks how the row got there.**  A hand delete made before a books move is one
way; the C18-a round-4 review measured another -- an unpaid copy revived by a
status revert (refused since ruling **R-PC97**) and then hidden by the
archive -- and it refuted the sentence this paragraph used to end on, that
such a row "can only be one its owner deleted by hand".  So no argument about
which writers can leave one is made here.

**A row a walk names is judged by the WALK for what its schedule drops**
(the round-1 review's finding H1): :func:`books_reading` asks
:func:`~app.services.recurrence.occurrence_walk` which occurrences the books
drop, and a row is matched to the occurrence it answers by
``occurs_on``, the key the maintain pass matches by -- the same question, of
the same producer, the refusals ask.  A stored day would be wrong for THAT
question, because the regeneration re-dates every row its walk still names.

**Every row is ALSO judged by its OWN day, against the books of the account
it SITS ON** (:func:`own_day_held`; rulings **R-PC96**, **R-PC98** and
**R-PC99**).  R-PC96 (the round-4 review's H1) began it for the rows NO walk
names: a rule-less definition's rows -- an item that no longer repeats, a
one-time transfer -- and a recurring definition's rows answering no
occurrence (``occurs_on`` ``NULL``, a carried-forward leftover) have no
occurrence to look up, and the scope used to restore every one of them:
measured, an archived $50.00 transfer whose cadence was cleared while it was
archived came back due ON its books after a restatement onto that day,
$50.00 counted a second time.  R-PC98 (the round-5 review's H2) added the
ORPHAN, a recurring definition's row whose ``occurs_on`` its CURRENT walk
names nowhere, left behind by a rule edit -- measured, a start moved later
while archived, the books restated onto the second old row's day, and the
unarchive restoring both old rows inside the opening, ``-$20.00`` counted a
second time.  No regeneration re-dates either, so the stored day IS the day:
the one picker (:func:`~app.utils.books_boundary.row_books_day`: the due
day, or the paycheck's last day for an envelope) gives the day
:func:`~app.utils.books_boundary.books_hold` compares.  **R-PC99** (the
round-6 review's H1) fixed WHICH books: the ones of the account the row
sits on (:func:`~app.services.balance_at.row_books_opened_on`), which the
balance counts it in, never its definition's.  A definition's account move
leaves the rows of paychecks that had already ended on the account it left;
measured, that account restated past two such rows committed with the
forecast at ``-$100.00`` where the books rule gives ``-$80.00``, orphans and
named rows alike.  So the own-day judgment asks every hidden row, named or
not, and the walk's judgment above asks the named ones as well.  Every door
that bounds a planned row by the books asks the same two questions, not only
here (``app.services.planned_rows_books``).

**The conflict chooser's "use the template" restores by this scope too**
(ruling **R-BAL253**, developer 2026-10-09, "Leave it deleted, both kinds"):
both engines' ``resolve_conflicts`` ask :func:`restored_by_use`, which
narrows a definition's scope to the deleted rows the owner picked
(:attr:`UnarchiveScope.among`), so a row its books hold stays deleted and is
named in this module's sentence.  Measured before it, through the edit route
and the chooser's Apply: a deleted ex-Paid transfer due on Savings' opening
day, its definition re-priced to $60.00, came back as a live plan inside the
books, Savings $600.00 where the books give $540.00.

**The root is not fixed here.**  An unarchive still restores a row its owner
deleted by hand ABOVE the books, and so does the conflict chooser, which never
asks whether the rule still names the row's occurrence (ledger row
**REC-535**): both follow from the shared flag, which ledger row
**REC-536** holds.  Plan step ``recurrence:R22`` designs the from-scratch
model -- a recurring item's unpaid occurrences computed from its schedule and
never stored, only the owner's acts kept -- under which no hidden copy exists
for an unarchive to bring back.

Services-boundary discipline (``CLAUDE.md`` Architecture): ORM reads in, SQL
criteria and a sentence out; no Flask symbol, no write, no clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import NamedTuple

from sqlalchemy import false, func, or_, select

from app.extensions import db
from app.models.account import Account
from app.models.account_opening import AccountOpening
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import (
    BalanceContext,
    definition_books,
    definition_money_accounts,
    row_books_opened_on,
)
from app.services.cash_ledger import governing_account_opening
from app.services.pay_calendar import FiledRow, PayCalendar
from app.services.recurrence import ResolvedRecurrence, occurrence_walk
from app.services.recurring_definition import resolved_rule_of
from app.utils.balance_predicates import is_projected_clause
from app.utils.books_boundary import books_hold, row_books_day


@dataclass(frozen=True)
class UnarchiveScope:
    """What unarchiving one recurring definition does with its hidden rows.

    Read off the definition AS IT STANDS on the reader's pass: before any
    save the caller is grading at the unarchive, the edit doors and the
    opening restatement, because that is what an unarchive would act on;
    and AFTER the edit's staged write and its regeneration at the conflict
    chooser (:func:`restored_by_use`, ruling **R-BAL253**), because the rows
    "use" hands back go to the definition the save leaves.

    Attributes:
        definition: The
            :class:`~app.models.transaction_template.TransactionTemplate` or
            :class:`~app.models.transfer_template.TransferTemplate`.
        inside_the_books: Every occurrence its books drop, mapped to the day
            the books are compared with for its placement
            (:attr:`BooksReading.inside`): the hidden rows answering one stay
            deleted.
        own_day_inside: Every hidden row whose OWN compared day the books
            of the account it sits on hold (:func:`own_day_held`, rulings
            **R-PC96**, **R-PC98** and **R-PC99**), by row id, mapped to
            that day: these stay deleted too.
        held_by: The same rows' ids mapped to the opening day of the books
            holding each -- the books the notice names for it, which after
            its definition's account move are not the definition's.
        books_opened_on: The definition's books floor, which the notice
            quotes for a row only its WALK drops; ``None`` when it has none,
            and then the walk drops nothing.
        is_envelope: Whether the compared day is a paycheck's last day
            (ruling **R-PC89**) rather than a due day, which the notice has
            to say to be true.
        among: The ids of the hidden rows the scope speaks for, or ``None``
            for every one.  The conflict chooser asks about the rows its
            owner picked alone (:func:`restored_by_use`, ruling
            **R-BAL253**), and narrowing HERE narrows :meth:`restores`,
            :meth:`stays_deleted` and :func:`stays_deleted_notice` at once,
            so the sentence counts only those rows.
    """

    definition: TransactionTemplate | TransferTemplate
    inside_the_books: dict
    own_day_inside: dict
    held_by: dict
    books_opened_on: date | None
    is_envelope: bool
    among: frozenset | None = None

    def _hidden(self) -> tuple:
        """Return the criteria for the definition's soft-deleted, still-Projected rows."""
        hidden = _hidden_rows(self.definition)
        if self.among is None:
            return hidden
        _table_order, model, _template_fk = rows_of(self.definition)
        return (*hidden, model.id.in_(sorted(self.among)))

    def restores(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive restores.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows, less those answering an
            occurrence its books drop and less those whose own day the books
            of the account they sit on hold (:attr:`own_day_inside`).  A row
            answering NO occurrence (``occurs_on`` ``NULL``) passes the first
            test, since no walk names it with or without the books; the
            second is the one that judges it.
        """
        _table_order, model, _template_fk = rows_of(self.definition)
        criteria = list(self._hidden())
        if self.inside_the_books:
            criteria.append(or_(
                model.occurs_on.is_(None),
                model.occurs_on.notin_(list(self.inside_the_books)),
            ))
        if self.own_day_inside:
            criteria.append(model.id.notin_(list(self.own_day_inside)))
        return tuple(criteria)

    def stays_deleted(self) -> tuple:
        """Return the SQL criteria selecting the rows the unarchive leaves deleted.

        Returns:
            The criteria, for ``query.filter(*criteria)``: the definition's
            soft-deleted still-Projected rows answering an occurrence its
            books drop, or held on their own day by the books of the account
            they sit on -- the complement of :meth:`restores` among its
            hidden rows.
        """
        _table_order, model, _template_fk = rows_of(self.definition)
        held = []
        if self.inside_the_books:
            held.append(model.occurs_on.in_(list(self.inside_the_books)))
        if self.own_day_inside:
            held.append(model.id.in_(list(self.own_day_inside)))
        if not held:
            return (*self._hidden(), false())
        return (*self._hidden(), or_(*held))


def rows_of(definition) -> tuple:
    """Return ``(table_order, model, template_fk)`` for *definition*'s rows.

    Args:
        definition: A transaction or transfer template, or either class --
            the restatement asks by class which definitions own rows sitting
            on an account (``planned_rows_books``, ruling **R-PC99**).

    Returns:
        ``(0, Transaction, Transaction.template_id)`` or ``(1, Transfer,
        Transfer.transfer_template_id)`` -- the order the books refusals break
        ties in, the row model, and the column naming the row's definition.
    """
    kind = definition if isinstance(definition, type) else type(definition)
    if issubclass(kind, TransferTemplate):
        return 1, Transfer, Transfer.transfer_template_id
    return 0, Transaction, Transaction.template_id


def _hidden_rows(definition) -> tuple:
    """Return the criteria for *definition*'s soft-deleted, still-Projected rows.

    Args:
        definition: A transaction or transfer template.

    Returns:
        The criteria, for ``query.filter(*criteria)``: the rows its archive
        hid, and any its owner deleted by hand (ruling **R-PC95**).
    """
    _table_order, model, template_fk = rows_of(definition)
    return (
        template_fk == definition.id,
        is_projected_clause(model),
        model.is_deleted.is_(True),
    )


@dataclass(frozen=True)
class BooksReading:
    """What one walk of a recurring definition says about its books.

    Attributes:
        inside: ``{occurrence: compared day}`` for every occurrence the
            definition's books drop (the rows answering one are judged by
            the WALK's day for its placement).
    """

    inside: dict


def books_reading(
    resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> BooksReading:
    """Return what *resolved*'s books drop, from ONE walk.

    **The one reading of what a definition's books drop**, taken by the
    unarchive here and by the refusals in ``app.services.planned_rows_books``
    (the opening and edit doors'
    :func:`~app.services.planned_rows_books.first_row_below_the_books` and
    the revert's), so a row the unarchive leaves deleted and a row a refusal
    names come from one walk.  **A definition with no books floor is not
    walked**: its walk drops nothing, so none is needed (the C18-a round-6
    review's L3 -- walking it anyway refused a revert over a rule no walk
    could read, where no answer depended on the walk).

    Args:
        resolved: The definition's recurrence with its books floor attached,
            or ``None`` -- a definition with no rule, or an owner with no pay
            periods -- which drops nothing.
        calendar: The owner's pay calendar.

    Returns:
        The :class:`BooksReading`: every dropped occurrence mapped to
        :meth:`~app.services.recurrence.ResolvedRecurrence.books_day` for its
        placement (the due day for a bill, ruling **R-PC86**; the paycheck's
        last day for an envelope, ruling **R-PC89**).

    Raises:
        RecurrenceGenerationError: See
            :func:`~app.services.recurrence.occurrence_walk`.
    """
    if resolved is None or resolved.books_opened_on is None:
        return BooksReading(inside={})
    walk = occurrence_walk(resolved, calendar)
    return BooksReading(inside={
        placement.occurrence: resolved.books_day(
            placement.occurrence, placement.period,
        )
        for placement in walk.below_the_books
    })


def own_books_day(
    row, calendar: PayCalendar, *, is_envelope: bool, rewrite=None,
) -> date:
    """Return the day the books are compared with for a row judged by its own day.

    Rulings **R-PC96**, **R-PC98** and **R-PC99**: a row is judged where it
    SITS on its STORED day -- no regeneration re-dates a row no walk names,
    and the balance counts every row on its own day -- and the one picker
    (:func:`~app.utils.books_boundary.row_books_day`) chooses between its
    due day and its paycheck's last day (an envelope, ruling **R-PC89**).
    A row the save being graded REWRITES is judged on the due day the
    rewrite gives it, in the paycheck it keeps: the pass writes no paycheck.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        calendar: The owner's pay calendar, which gives the paycheck's end.
        is_envelope: Whether its definition is an envelope.
        rewrite: See :func:`own_day_held`.

    Returns:
        The day :func:`~app.utils.books_boundary.books_hold` is asked of.
    """
    placed = row if rewrite is None else rewrite
    return row_books_day(
        placed.due_date,
        calendar.require_period(FiledRow.for_row(row)).end_date,
        is_envelope=is_envelope,
    )


class OwnDayHeld(NamedTuple):
    """A row whose own day the books of the account it sits on hold (ruling R-PC99).

    Attributes:
        day: Its own compared day (:func:`own_books_day`).
        opened_on: The opening of the books holding it: the latest among the
            accounts it sits on
            (:func:`~app.services.balance_at.row_books_opened_on`).
    """

    day: date
    opened_on: date


def own_day_held(
    row, calendar: PayCalendar, memo: dict, *, is_envelope: bool,
    rewrite=None,
) -> OwnDayHeld | None:
    """Return *row*'s own day when the books of the account it sits on hold it.

    **Ruling R-PC99's one predicate** (developer 2026-09-23, the C18-a
    round-6 review's H1), asked by every door that bounds a planned row by
    the books: the opening restatement and both edit doors
    (``planned_rows_books.first_row_below_the_books``), the revert
    (``planned_rows_books.reject_revert_below_the_books``) and the unarchive
    (:func:`unarchive_scope`).  A still-Projected row on or before the books
    of the account the balance counts it in sits inside that account's
    opening and is counted a second time, whether or not its definition's
    walk still names it and whichever account its definition names now.

    **Asked of the row as the save being graded LEAVES it** (ruling
    **R-PC91**, the C18-a round-7 review's M1): an edit's own regeneration
    rewrites the due day and accounts of every row it brings into line, so
    such a row is judged on those (*rewrite*), in the paycheck it keeps.
    Judged as stored instead, one save that unticked an envelope and moved
    its due day was refused over a day the save itself moves the row off,
    where the same two edits as two saves passed.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        calendar: The owner's pay calendar.
        memo: The ``account_id -> opened_on`` memo
            :func:`~app.services.balance_at.row_books_opened_on` reads
            through; a restatement's holds the candidate day for the account
            it restates.
        is_envelope: Whether the row's definition is an envelope (ruling
            **R-PC89**), as the save being graded would leave it.
        rewrite: The fields the save's own regeneration writes onto *row*
            (a value of
            :attr:`~app.services.recurrence_engine.RegenerationPreview.rewrites`:
            its due day and the accounts it moves money in), or ``None`` for
            a row the save leaves as stored.

    Returns:
        The :class:`OwnDayHeld`, or ``None`` when no account it sits on has
        an opening or the books open before its day.
    """
    opened_on = row_books_opened_on(row if rewrite is None else rewrite, memo)
    if opened_on is None:
        return None
    day = own_books_day(row, calendar, is_envelope=is_envelope, rewrite=rewrite)
    if books_hold(opened_on, day):
        return None
    return OwnDayHeld(day=day, opened_on=opened_on)


def rows_held_where_they_sit(
    definition, criteria: tuple, calendar: PayCalendar, memo: dict, *,
    rewrites: dict | None = None,
) -> dict:
    """Return *definition*'s rows matching *criteria* that :func:`own_day_held` holds.

    **The one loader of ruling R-PC99's question**, read by the unarchive
    (over the hidden rows) and by every refusal in
    ``app.services.planned_rows_books`` (over the planned ones), with the
    envelope flag its books composition reads
    (:func:`~app.services.balance_at.definition_books`) -- the edited
    object's own at an edit door.  The query loads only rows the books COULD
    hold (:func:`_could_be_held`), so the books-opening card, asking at every
    render, never loads a schedule's future.

    Args:
        definition: The transaction or transfer template.
        criteria: Which of its rows to ask, for ``query.filter(*criteria)``.
        calendar: The owner's pay calendar.
        memo: The ``account_id -> opened_on`` memo :func:`own_day_held`
            reads through; a restatement's holds its candidate day.
        rewrites: At an edit door, ``{row id: the fields the save's own
            regeneration writes onto it}``
            (:attr:`~app.services.recurrence_engine.RegenerationPreview.rewrites`),
            each such row judged as rewritten (:func:`own_day_held`); ``None``
            elsewhere, where nothing rewrites a row.

    Returns:
        ``{row id: (its OwnDayHeld, the row)}``.
    """
    rewrites = {} if rewrites is None else rewrites
    _table_order, model, _template_fk = rows_of(definition)
    is_envelope = definition_books(definition, memo).is_envelope
    held = {}
    for row in (
        db.session.query(model)
        .filter(*criteria, _could_be_held(model, definition.user_id, memo))
        .all()
    ):
        own = own_day_held(
            row, calendar, memo, is_envelope=is_envelope,
            rewrite=rewrites.get(row.id),
        )
        if own is not None:
            held[row.id] = (own, row)
    return held


def _could_be_held(model, user_id: int, memo: dict):
    """Return a SQL criterion no row :func:`own_day_held` holds can fail.

    A row's own day is its due day or its paycheck's LAST day, which falls on
    or after the paycheck's start; and no books a row of the owner's can sit
    on open after the latest of the owner's account openings and any day
    *memo* holds (a restatement's candidate).  So a row due after that day
    in a paycheck starting after it is never held -- a bound the query
    filters by, never a books floor.  **It reads the STORED columns, and a
    row an edit's regeneration rewrites is still never wrongly dropped**:
    rewritten, it sits on its definition's accounts on the day its walk
    names for an occurrence the books did not drop, so as a bill it is
    never held, and as an envelope its own day is still the last day of the
    paycheck it keeps, which starts no later.

    Args:
        model: ``Transaction`` or ``Transfer``.
        user_id: The owner.
        memo: The books memo, whose days count toward the bound.

    Returns:
        The criterion, for ``query.filter``.
    """
    latest = (
        select(func.max(AccountOpening.opened_on))
        .join(Account, Account.id == AccountOpening.account_id)
        .where(Account.user_id == user_id)
        .scalar_subquery()
    )
    days = [day for day in memo.values() if day is not None]
    bound = func.greatest(latest, max(days)) if days else latest
    return or_(
        model.due_date <= bound,
        model.pay_period_id.in_(
            select(PayPeriod.id).where(
                PayPeriod.user_id == user_id, PayPeriod.start_date <= bound,
            )
        ),
    )


def unarchive_scope(
    definition, resolved: ResolvedRecurrence | None, calendar: PayCalendar,
) -> UnarchiveScope:
    """Return what unarchiving *definition* would restore and leave deleted.

    A recurring definition's floor and envelope flag are the ones its
    resolution carries -- the composition its walk took.  A rule-less
    definition has no resolution, so they are read off it through the SAME
    producer that composition reads
    (:func:`~app.services.balance_at.definition_books`).  Every hidden row is
    judged on its own day too, against the books of the account it sits on
    (:func:`own_day_held`, rulings **R-PC96** and **R-PC99**), the governing
    ones: an unarchive restores rows as they stand.

    Args:
        definition: The transaction or transfer template.
        resolved: Its recurrence as it stands, books attached -- the pass's
            (:func:`~app.services.recurring_definition.resolved_rule_of`) at
            an unarchive or an edit, the opening door's composition over the
            governing books at a restatement -- or ``None`` when it has no
            rule or the owner no pay periods (who then has no rows).
        calendar: The owner's pay calendar.

    Returns:
        The :class:`UnarchiveScope`.

    Raises:
        RecurrenceGenerationError: See :func:`books_reading`.
    """
    if resolved is None:
        books = definition_books(definition, {})
        books_opened_on, is_envelope = books.opened_on, books.is_envelope
    else:
        books_opened_on = resolved.books_opened_on
        is_envelope = resolved.is_envelope
    own_day_inside, held_by = _own_day_inside(definition, calendar)
    return UnarchiveScope(
        definition=definition,
        inside_the_books=books_reading(resolved, calendar).inside,
        own_day_inside=own_day_inside,
        held_by=held_by,
        books_opened_on=books_opened_on,
        is_envelope=is_envelope,
    )


def scope_holding_nothing_back(definition) -> UnarchiveScope:
    """Return the scope of an unarchive that would restore every hidden row.

    What an edit door counts for an ARCHIVED definition whose stored rule the
    recurrence package cannot walk
    (``planned_rows_books.restorable_before_the_edit``): with no walk there is
    no telling which rows the books hold, so every hidden row is counted --
    the careful answer at the form that repairs such a rule.

    Args:
        definition: The transaction or transfer template.

    Returns:
        An :class:`UnarchiveScope` holding nothing back.
    """
    return UnarchiveScope(
        definition=definition,
        inside_the_books={},
        own_day_inside={},
        held_by={},
        books_opened_on=None,
        is_envelope=False,
    )


def _own_day_inside(definition, calendar: PayCalendar) -> tuple[dict, dict]:
    """Return the hidden rows the books of the account they sit on hold, by their own day.

    Every hidden row of *definition* -- a rule-less definition's (ruling
    **R-PC96**), a recurring one's undated rows (R-PC96) and orphans (ruling
    **R-PC98**), and its rows a walk names as well (ruling **R-PC99**) -- is
    compared on the day :func:`own_books_day` picks off the row itself with
    the governing books of the account it sits on
    (:func:`rows_held_where_they_sit`).

    Args:
        definition: The transaction or transfer template.
        calendar: The owner's pay calendar, which gives a paycheck's last day.

    Returns:
        ``({row id: compared day}, {row id: opening day of the books holding
        it})`` over each such row on or before those books.
    """
    held = rows_held_where_they_sit(
        definition, _hidden_rows(definition), calendar, {},
    )
    return (
        {row_id: own.day for row_id, (own, _row) in held.items()},
        {row_id: own.opened_on for row_id, (own, _row) in held.items()},
    )


def unarchive_scope_on(definition, ctx: BalanceContext) -> UnarchiveScope:
    """Return :func:`unarchive_scope` for *definition* as it stands on a read pass.

    The unarchive routes', the edit doors' and the conflict chooser's
    reading: the pass's memoised resolution of the stored rule, books attached
    (:func:`~app.services.recurring_definition.resolved_rule_of`), walked on
    the pass's calendar.

    Args:
        definition: The owner-checked transaction or transfer template.
        ctx: A PRE-WRITE read pass for its owner at the unarchive and the
            edit doors; at the conflict chooser, the edit's own pass, after
            its staged write and its regeneration (:func:`restored_by_use`).

    Returns:
        The :class:`UnarchiveScope`.

    Raises:
        RecurrenceResolutionError: The stored rule cannot be resolved
            (:func:`~app.services.recurring_definition.resolved_rule_of`).
        RecurrenceGenerationError: See :func:`books_reading`.
    """
    return unarchive_scope(
        definition, resolved_rule_of(definition, ctx), ctx.calendar(),
    )


def stays_deleted_notice(scope: UnarchiveScope) -> str | None:
    """Return the sentence naming the rows an unarchive leaves deleted, or ``None``.

    Ruling **R-PC95**'s words: ``1 item due 2026-03-01 stays deleted: it
    falls inside Checking's books, which open 2026-03-08.``  An envelope's row
    is named by its paycheck's last day, the day its books compare.  A row
    held on its own day takes that day and the books of the account it sits
    on (rulings **R-PC96** and **R-PC99**); a row only its WALK drops takes
    its occurrence's compared day and says its schedule no longer produces
    it, since it does not sit inside the books that drop it -- its definition
    moved to another account after its paycheck ended.  One sentence per
    books, so every row the scope leaves deleted is named, and none as inside
    books it does not sit in.

    Args:
        scope: The unarchive's :class:`UnarchiveScope`, read before its
            restore.

    Returns:
        The sentences, or ``None`` when every hidden row is restored.
    """
    _table_order, model, _template_fk = rows_of(scope.definition)
    groups: dict = {}
    for row in db.session.query(model).filter(*scope.stays_deleted()).all():
        if row.id in scope.own_day_inside:
            key = (True, definition_money_accounts(row), scope.held_by[row.id])
            day = scope.own_day_inside[row.id]
        else:
            key = (
                False, definition_money_accounts(scope.definition),
                scope.books_opened_on,
            )
            day = scope.inside_the_books[row.occurs_on]
        groups.setdefault(key, []).append(day)
    if not groups:
        return None
    return " ".join(
        _stays_deleted_sentence(
            sorted(days), sits_inside, _possessive(accounts, opened_on),
            opened_on, is_envelope=scope.is_envelope,
        )
        for (sits_inside, accounts, opened_on), days in sorted(
            groups.items(), key=lambda group: min(group[1]),
        )
    )


def _stays_deleted_sentence(
    days: list, sits_inside: bool, books: str, opened_on: date,
    *, is_envelope: bool,
) -> str:
    """Return one sentence of :func:`stays_deleted_notice`, over one books.

    Args:
        days: The compared days of the rows it names, ascending.
        sits_inside: Whether they sit inside *books* (held on their own day)
            rather than only answering occurrences their walk drops.
        books: The accounts, in the possessive (:func:`books_named`).
        opened_on: Those books' opening day.
        is_envelope: Whether the days are paychecks' last days.

    Returns:
        The sentence.
    """
    first, last = days[0].isoformat(), days[-1].isoformat()
    span = first if first == last else f"{first} to {last}"
    one = len(days) == 1
    if is_envelope:
        where = f"in the paycheck{'' if one else 's'} ending {span}"
    else:
        where = f"due {span}"
    count = "1 item" if one else f"{len(days)} items"
    stays = "stays" if one else "stay"
    if sits_inside:
        falls = "it falls" if one else "they fall"
        return (
            f"{count} {where} {stays} deleted: {falls} inside {books} books, "
            f"which open {opened_on.isoformat()}."
        )
    produces = "its schedule no longer produces it" if one else (
        "their schedule no longer produces them"
    )
    return (
        f"{count} {where} {stays} deleted: {produces}, since {books} books "
        f"open {opened_on.isoformat()}."
    )


class UseRestore(NamedTuple):
    """What the conflict chooser's "use the template" does with the rows it is handed.

    **The partition is stated HERE, once**, so neither engine's resolver
    spells "which picked rows go back to the definition" for itself.

    Attributes:
        handed_back: The picked rows "use" hands back to their definition,
            in the order given: every live one that names a definition, and
            every deleted one the definition's unarchive would restore.
        left_deleted: The ids of the picked deleted rows it leaves deleted
            and untouched because the unarchive would not restore them --
            through the chooser, which offers only Projected rows, because
            their books hold them.
        orphaned: The ids of the picked rows that name NO definition, live
            or deleted, left untouched (ledger row **N-440**): there is no
            definition to hand them back to.
        notice: :func:`stays_deleted_notice`'s sentence naming the rows left
            deleted, or ``None`` when there are none.
    """

    handed_back: tuple
    left_deleted: tuple
    orphaned: tuple
    notice: str | None


def restored_by_use(
    rows: Sequence[Transaction | Transfer], ctx: BalanceContext,
) -> UseRestore:
    """Return which of *rows* the conflict chooser's "use the template" may un-delete.

    **Ruling R-BAL253** (developer 2026-10-09, "Leave it deleted, both
    kinds": *"'Use the template' brings back only what un-archiving would: an
    item whose due day sits inside its books stays deleted, and the page
    names it the way un-archiving does"*).  The one rule both engines'
    ``resolve_conflicts`` ask, so a transfer and a bill cannot come to
    disagree: each picked deleted row is judged by its definition's
    :class:`UnarchiveScope` as it stands on the edit's read pass, narrowed to
    the picked rows (:attr:`UnarchiveScope.among`) so the sentence names only
    rows the owner asked back.  A live row naming a definition is not asked
    about: "use" hands it back to its definition as before.

    **A row that names NO definition is never handed back** (ledger row
    **N-440**; the guard the transaction resolver spelled for itself until it
    moved here, and the transfer resolver lacked): a transfer can outlive its
    definition (``transfer_template_id`` is ON DELETE SET NULL) or never have
    had one (an ad-hoc transfer), and a card payback names none.  Declaring
    such a row derived would write the one state no rule can price -- a
    transaction's amount rule refuses it in a money path, a transfer's
    ``ck_transfers_adhoc_owns_amount`` refuses it outright -- so it keeps
    what it owns, live or deleted, and is reported as
    :attr:`UseRestore.orphaned`; no unarchive restores one either.
    UNREACHABLE from the route -- the conflict set is built by selecting on
    the definition, and its hard delete 404s the Apply -- so this is defence
    in depth for a published service entry.

    Args:
        rows: The owner-checked rows the owner picked "use" for --
            :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`, each read through its
            ``template``.
        ctx: The edit's read pass, the one its regeneration resolved
            against: the books, the rule and the calendar the save leaves.
            Built after the edit is staged
            (``routes._recurrence_conflict_chooser.regenerate_or_conflict_chooser``
            builds it on entry, which both edit routes reach only after
            writing the edit's fields), so nothing it memoises predates the
            edit.

    Returns:
        The :class:`UseRestore`.

    Raises:
        RecurrenceResolutionError: See :func:`unarchive_scope_on`.
        RecurrenceGenerationError: See :func:`unarchive_scope_on`.
    """
    owned = [row for row in rows if row.template is not None]
    picked: dict = {}
    for row in owned:
        if row.is_deleted:
            picked.setdefault(
                (type(row.template), row.template.id), (row.template, set()),
            )[1].add(row.id)
    restores: set = set()
    notices = []
    for definition, row_ids in picked.values():
        scope = replace(
            unarchive_scope_on(definition, ctx), among=frozenset(row_ids),
        )
        _table_order, model, _template_fk = rows_of(definition)
        restores.update(
            db.session.scalars(select(model.id).where(*scope.restores())),
        )
        notice = stays_deleted_notice(scope)
        if notice is not None:
            notices.append(notice)
    return UseRestore(
        handed_back=tuple(
            row for row in owned if not row.is_deleted or row.id in restores
        ),
        left_deleted=tuple(
            row.id for row in owned if row.is_deleted and row.id not in restores
        ),
        orphaned=tuple(row.id for row in rows if row.template is None),
        notice=" ".join(notices) or None,
    )


def books_named(holder, books_opened_on: date) -> str:
    """Return the accounts whose books open on *books_opened_on*, as a sentence names them.

    ``Checking's``, or ``Src's and Dst's`` for a transfer whose two accounts
    open the same day.  **The one statement of which account a books sentence
    names**, read by the unarchive's notice and by the books refusals
    (``planned_rows_books``, rulings **R-PC97** and **R-PC99**).  *holder* is
    a definition -- whose floor is the latest opening among the accounts it
    moves money in -- or one of its rows, held by the books of the accounts
    it SITS ON (:func:`own_day_held`), which after its definition's account
    move are not the definition's -- or, at an edit door, the fields the
    save's regeneration rewrites onto a row, naming where it will sit.  A
    floor carries the latest opening day, not which account set it, and
    naming that account is what makes the sentence one the owner can check.
    The openings are read here and only when a sentence is being written, so
    a door that refuses nothing reads nothing more.

    Args:
        holder: The transaction or transfer template, a
            :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer` row, or a rewrite (a value
            of ``RegenerationPreview.rewrites``, :func:`own_day_held`).
        books_opened_on: The governing opening day of at least one of the
            accounts *holder* names.

    Returns:
        Each such account's name in the possessive, joined by ``and``.
    """
    return _possessive(definition_money_accounts(holder), books_opened_on)


def _possessive(account_ids: tuple, books_opened_on: date) -> str:
    """Return :func:`books_named`'s sentence over *account_ids*."""
    return " and ".join(
        f"{db.session.get(Account, account_id).name}'s"
        for account_id in account_ids
        if _opened_on(account_id) == books_opened_on
    )


def _opened_on(account_id: int) -> date | None:
    """Return *account_id*'s governing opening day, or ``None`` for no record."""
    opening = governing_account_opening(account_id)
    return None if opening is None else opening.opened_on


__all__ = [
    "BooksReading",
    "OwnDayHeld",
    "UnarchiveScope",
    "UseRestore",
    "books_named",
    "books_reading",
    "own_books_day",
    "own_day_held",
    "restored_by_use",
    "rows_held_where_they_sit",
    "rows_of",
    "scope_holding_nothing_back",
    "stays_deleted_notice",
    "unarchive_scope",
    "unarchive_scope_on",
]
