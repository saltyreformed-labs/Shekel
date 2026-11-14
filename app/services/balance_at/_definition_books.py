"""WHERE A RECURRING DEFINITION'S BOOKS OPEN -- the floor its occurrences start above.

Plan step ``pay_calendar:C18-a``, rulings **R-PC85** and **R-PC86**
(developer, 2026-09-22).  A recurring definition's occurrences run from the
LATER of its own first occurrence and the day after the books open on every
account it moves money in -- the opening end's mirror of how its closing
already composes the owner's end date with a loan's payoff.  The rule says
when it fires; the ACCOUNT says where the app keeps books, and money on or
before an account's opening day is already inside its opening equity (ruling
**balance:R-HG**).  This module answers the account half:
:func:`definition_books_opened_on`, read by
:meth:`~app.services.balance_at.BalanceContext.resolved_for` and attached to
the resolved value the recurrence walk then bounds by
(``recurrence._placement._lands_inside_the_books``) -- beside the one fact
about the DEFINITION that walk also asks, whether it is an envelope, whose
row the books compare on its paycheck's last day rather than its due day
(ruling **R-PC89**).  :func:`definition_books` reads both as ONE value, so
the pass's memo keys on the pair and cannot serve an envelope's walk to a
bill stating the same cadence over the same accounts.

**Why this is a fact about ACCOUNTS and never about the rule.**  A rule's
``starts_on`` is its first occurrence (ruling **R-R16**) -- the rhythm's
anchor and, for a rule that begins in the future, its start -- and it is
authored.  "Not before my account's books" is where the account's books
START (``cash_ledger.books_start``; card plan#2436, ruling **plan#3954**): a
configured loan's origination, from its terms, and every other account's
governing ``budget.account_openings`` row, which is RESTATED.  A copy of
either on the rule would be a second home needing a reconciler
(``CLAUDE.md`` rule 14); reading it here, on every pass, is what makes the
bound follow a restatement with nothing to keep in step.  Until plan task
plan#4087 this read the opening record for a loan too, so a recurring
transfer into a loan whose record a migration dated years after its
origination dropped every occurrence between the two days, though the
loan's balance and every movement into it start at the origination.

**Why it is a module of its own**: ``_context.py`` stood within a hundred
lines of pylint's 1,000-line ceiling at this step (the next merge crossed it,
and ruling R-BAL146 split it), and the two questions below -- which
accounts a definition moves money in, and where the latest of their books
opens -- are this step's, not the pass's.  The pass keeps the MEMO (one read
per account per pass) and the one composition; this keeps the reading.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in,
plain values out; no Flask symbol, no writes, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from app.services.cash_ledger import books_start
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import (
    RecurrenceSpec,
    ResolvedRecurrence,
    resolved_spec,
)

#: The attributes naming an account a recurring definition moves money in,
#: read off the definition by name.  A ``TransactionTemplate`` carries
#: ``account_id``; a ``TransferTemplate`` carries ``from_account_id`` and
#: ``to_account_id`` -- money leaves one and reaches the other, so BOTH books
#: bound it; the form preview's
#: :class:`~app.services.recurring_definition.UnsavedDefinition` carries the
#: same three names for the same two shapes.  Read by name rather than by type
#: because the unsaved definition is not a template and the recurrence
#: package's own owner contract is duck-typed
#: (:data:`~app.services.recurrence.RecurrenceOwner`).
_MONEY_ACCOUNT_ATTRIBUTES = ("account_id", "from_account_id", "to_account_id")


@dataclass(frozen=True)
class DefinitionBooks:
    """Where a definition's books bound its rows, and which row day they compare.

    The two facts :meth:`~app.services.balance_at.BalanceContext.resolved_for`
    attaches to a resolved recurrence (plan step ``pay_calendar:C18-a``), held
    as one hashable value because the pass memoises by it: two definitions
    stating one cadence share a resolution only when BOTH agree.

    Attributes:
        opened_on: The latest books start among the accounts the
            definition moves money in (:func:`definition_books_opened_on`),
            or ``None`` for no floor.
        is_envelope: The definition's ``is_envelope`` (ruling **R-PC89**):
            ``True`` compares a row's paycheck's LAST day with the books.
            ``False`` for anything without the attribute -- a transfer
            template, which is never an envelope, and a payroll line's rule.
    """

    opened_on: date | None
    is_envelope: bool


def resolved_with_books(
    spec: RecurrenceSpec, calendar: PayCalendar, books: DefinitionBooks,
) -> ResolvedRecurrence | None:
    """Return what *spec* MEANS on *calendar*, with *books* attached.

    **The ONE composition of a recurrence with where its definition's books
    open** (plan step ``pay_calendar:C18-a``): the pure resolution
    (:func:`~app.services.recurrence.resolved_spec`) with the floor and the
    envelope flag laid on it.  Two callers, one body:
    :meth:`~app.services.balance_at.BalanceContext.resolved_for` memoises it
    for every reader of a definition's occurrences, keyed by exactly these
    inputs; and the opening restatement (ruling **R-PC88**,
    ``planned_rows_books.first_row_an_opening_strands``) asks it of the books
    the account WOULD have -- a :class:`DefinitionBooks` read with the
    candidate day standing in for the account's own opening -- which no pass
    memo holds, because no account opens there yet.

    Args:
        spec: The authored recurrence.
        calendar: The owner's pay calendar.
        books: The definition's :class:`DefinitionBooks`.

    Returns:
        The resolved value with ``books_opened_on`` and ``is_envelope`` set,
        or ``None`` when the owner has no pay periods.

    Raises:
        RecurrenceResolutionError: See
            :func:`~app.services.recurrence.resolved_spec`.
    """
    resolved = resolved_spec(spec, calendar)
    if resolved is None:
        return None
    return replace(
        resolved, books_opened_on=books.opened_on,
        is_envelope=books.is_envelope,
    )


def definition_books(
    definition: object | None, memo: "dict[int, date | None]",
) -> DefinitionBooks:
    """Return *definition*'s :class:`DefinitionBooks`, reading each opening once.

    Args:
        definition: What moves the money, as :func:`definition_money_accounts`
            takes it.
        memo: The pass's ``account_id -> opened_on`` memo.

    Returns:
        The floor and the envelope flag.  ``is_envelope`` is read by name,
        as the accounts are, and through ``bool``: a template built and not
        yet flushed holds ``None`` until the column default applies, which
        means ``False``.
    """
    return DefinitionBooks(
        opened_on=definition_books_opened_on(
            definition_money_accounts(definition), memo,
        ),
        is_envelope=bool(getattr(definition, "is_envelope", False)),
    )


def row_books_opened_on(row: object, memo: "dict[int, date | None]") -> date | None:
    """Return the LATEST books start among the accounts *row* sits on.

    Ruling **R-PC99** (developer 2026-09-23, the C18-a round-6 review's H1):
    a planned row is judged against the books of the account it SITS ON as
    well as by its definition's walk, because the balance counts it there --
    and after a definition's account move the rows of paychecks that had
    already ended stay on the account it left.  The same reading
    :func:`definition_books` takes of a definition, taken of one of its rows:
    a ``Transaction`` names ``account_id`` and a ``Transfer`` names
    ``from_account_id`` and ``to_account_id``, the attribute names
    :data:`_MONEY_ACCOUNT_ATTRIBUTES` reads off a definition, so a transfer's
    row is held by the later of its two openings exactly as its definition
    is.

    Args:
        row: The :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        memo: An ``account_id -> opened_on`` memo, filled here.  A
            restatement seeds it with the candidate day for the account it
            restates, which then stands in for that account's own opening.

    Returns:
        The latest books start, or ``None`` when the books of no account
        the row sits on start anywhere.
    """
    return definition_books_opened_on(definition_money_accounts(row), memo)


def definition_money_accounts(definition: object | None) -> tuple[int, ...]:
    """Return the ids of every account *definition* moves money in.

    Args:
        definition: A ``TransactionTemplate``, a ``TransferTemplate``, an
            :class:`~app.services.recurring_definition.UnsavedDefinition`, or
            ``None`` -- a rule whose owner is a payroll LINE, whose cadence
            names which paychecks carry a deduction and creates no row of its
            own (the paycheck row it rides on is bounded by that row's own
            definition).  A ``Transaction`` or ``Transfer`` ROW carries the
            same names, and :func:`row_books_opened_on` reads the accounts it
            sits on through here (ruling **R-PC99**).

    Returns:
        The account ids *definition* names, in attribute order, ``None``
        values skipped; empty for ``None`` and for a form that has not stated
        an account yet.
    """
    if definition is None:
        return ()
    return tuple(
        account_id
        for account_id in (
            getattr(definition, name, None)
            for name in _MONEY_ACCOUNT_ATTRIBUTES
        )
        if account_id is not None
    )


def money_account_columns(model) -> tuple:
    """Return *model*'s columns naming an account it moves money in.

    The SQL face of :data:`_MONEY_ACCOUNT_ATTRIBUTES`, for a query that finds
    every definition moving money in an account
    (``planned_rows_books``): it and :func:`definition_money_accounts`, which
    reads the same names off one definition, cannot come to disagree about
    which columns those are (the C18-a round-2 review's L-g).

    Args:
        model: ``TransactionTemplate`` or ``TransferTemplate`` -- or a row
            model, ``Transaction`` or ``Transfer``, which carries the same
            names: the restatement asks after the rows SITTING ON an account
            with it (ruling **R-PC99**).

    Returns:
        The columns among the three names *model* carries, in attribute
        order.
    """
    return tuple(
        getattr(model, name)
        for name in _MONEY_ACCOUNT_ATTRIBUTES
        if hasattr(model, name)
    )


def definition_books_opened_on(
    account_ids: tuple[int, ...], memo: "dict[int, date | None]",
) -> date | None:
    """Return the LATEST day the books start among *account_ids*.

    The floor a definition's occurrences start above: a transfer moves money
    in both of its accounts, so neither's books may be written below, and the
    later start is the binding one.  Each account's day is where its books
    start (:func:`~app.services.cash_ledger.books_start`, ruling
    **plan#3954**): a configured loan's origination, every other account's
    governing opening record -- the day every movement into it is judged by,
    in Python and in the database alike.  It is read once per pass through
    *memo* -- the caller's -- so a render resolving many definitions over one
    account pays one read.

    **An account whose books start nowhere contributes no floor** -- one that
    is no configured loan and carries no opening record -- the disposition
    ``budget.assert_movement_after_books_open`` takes for the same state:
    every account gets a record at creation and a migration backfilled the
    rest, so it is a broken invariant, and the READ side already refuses it
    loudly (``cash_ledger.account_opening_fact`` raises), so it is not
    re-raised here on an unrelated path.

    Args:
        account_ids: The accounts the definition moves money in
            (:func:`definition_money_accounts`).  Assumed the pass owner's --
            a stored template's by its own ownership, an unsaved definition's
            by the route's ownership gate.
        memo: The pass's ``account_id -> opened_on`` memo, filled here.

    Returns:
        The latest books start, or ``None`` when no named account's books
        start anywhere (or none is named).
    """
    days = []
    for account_id in account_ids:
        if account_id not in memo:
            start = books_start(account_id)
            memo[account_id] = None if start is None else start.opened_on
        if memo[account_id] is not None:
            days.append(memo[account_id])
    return max(days, default=None)
