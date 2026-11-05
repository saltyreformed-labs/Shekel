"""One day of the bank comparison, opened: every line and every row behind it.

The DRILL-DOWN half of :mod:`app.services.bank_agreement`, split from the
agreement fold by plan step ``balance:X-bk-2`` (plan#1192, task plan#2363)
because the module stood at 988 lines and that step adds to the fold.  The two
halves share no code: the fold prices every drawn day from the cash walk and
the imported runs, and this lists what makes up ONE day -- the bank's lines
posted on it and the app's settled movements on it, each with its match state
-- for the agreement page's day view and for
:func:`~app.services.outstanding_difference.disagreement_list`.  Callers reach
both through the package, never this module (``shekel-private-module-import``).

Services-boundary discipline: no Flask import, no clock read -- the reader's
NOW arrives on the :class:`~app.services.balance_at.BalanceContext`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatchMember
from app.models.transaction_entry import TransactionEntry
from app.services import balance_at, cash_ledger
from app.services.statement_match import matched_subjects

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class DayLine:
    """One bank line on a day, as the drill-down shows it.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row, so a reader that
            must ACT on the line -- the one-time reconcile's act sheet (plan
            step ``balance:X-bk-2``, plan#1192) -- names it rather than
            re-finding it by day and figure, which two same-day lines of one
            amount would make ambiguous.
        amount: The signed figure the bank posted.
        description: The bank's own prose.
        merchant: The merchant the adapter read, or ``None``.
        matched: Whether a statement match already claims this line.
    """

    line_id: int
    amount: Decimal
    description: str
    merchant: "str | None"
    matched: bool


@dataclass(frozen=True)
class DayRow:
    """One of the app's own settled movements on a day.

    Attributes:
        entry_id: The MOVEMENT (``budget.transaction_entries``) the walk
            counted -- the fact's own
            :attr:`~app.services.cash_ledger.CashSourceFact.entry_id`, carried
            for the reason :attr:`DayLine.line_id` is: the reconcile's act
            sheet cancels or matches exactly this movement, and a second walk
            of the ledger to recover its id would be a second statement of
            which movements are on the day.
        amount: The signed cash the row moved -- the same
            :attr:`~app.services.cash_ledger.CashSourceFact.delta` the fold
            counted, so the rows shown here sum to the day's ``recorded``.
        description: What the row is called.
        matched: Whether a statement match already claims it.
    """

    entry_id: int
    amount: Decimal
    description: str
    matched: bool


@dataclass(frozen=True)
class DayDetail:
    """What makes up one day's difference, on both sides.

    Attributes:
        day: The day explained.
        lines: The bank's lines posted that day.
        rows: The app's own settled movements that day.

    **Every line and every row is listed, not only the unmatched ones**, and
    that is the correction of the obvious shortcut.  A row matched to a line
    the bank posted on a DIFFERENT day contributes to both days' residues
    while being unmatched on neither, so a list filtered to "unexplained"
    would omit exactly the timing case a reader is trying to understand.  The
    match flag is shown per item instead, which says the same thing without
    dropping anything.
    """

    day: date
    lines: "list[DayLine]"
    rows: "list[DayRow]"


def day_detail(
    account: Account, ctx: balance_at.BalanceContext, day: date,
) -> DayDetail:
    """Return what makes up one day's difference, on both sides.

    Args:
        account: The account to explain.  Must be session-attached; the caller
            owns the ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.
        day: The civil day to explain.

    Returns:
        The :class:`DayDetail`.  A day with nothing on either side gets empty
        lists rather than ``None``: "the bank showed nothing and so did you" is
        an answer, and one a reader following a link has asked for.

    Raises:
        BaselineMissingError: When *ctx* carries no scenario.

    **The app's rows come from the same walk the day's ``recorded`` total was
    taken from**, so the items shown sum to the figure they explain.  Listing
    the account's transactions by ``settled_on`` instead would be a second
    statement of which rows count as settled cash and on what day -- the shape
    this codebase has repeatedly paid for.
    """
    return DayDetail(
        day=day,
        lines=[
            DayLine(
                line_id=line.id,
                amount=line.amount,
                description=line.description,
                merchant=line.merchant_name,
                matched=matched,
            )
            for line, matched in _lines_on(account.id, day)
        ],
        rows=[
            DayRow(
                entry_id=entry_id, amount=amount,
                description=description, matched=matched,
            )
            for entry_id, amount, description, matched
            in _rows_on(account, ctx, day)
        ],
    )


def _lines_on(
    account_id: int, day: date,
) -> "list[tuple[BankStatementLine, bool]]":
    """Return the bank's lines posted on one day, each with its match state.

    Args:
        account_id: The account whose lines to read.
        day: The posted day.

    Returns:
        ``[(line, matched), ...]`` ordered by recording order (``id``).

    **NOT the bank's own within-day order, and nothing recorded preserves it.**
    An earlier docstring claimed it did.  ``sequence_in_group`` is keyed per
    ``(account, posted_on, amount)`` for line IDENTITY, not statement position,
    and ``id`` is the order lines were first recorded across imports -- so a
    re-import inserting a line the bank had placed ahead of a recorded one puts
    it last here.  Recording order is a defensible, stable choice; the claim
    was the defect.  Found by adversarial review 2026-08-24.
    """
    claimed = {
        row[0]
        for row in db.session.query(
            StatementMatchMember.bank_statement_line_id,
        ).filter(
            StatementMatchMember.account_id == account_id,
            StatementMatchMember.bank_statement_line_id.isnot(None),
        )
    }
    lines = (
        db.session.query(BankStatementLine)
        .filter(
            BankStatementLine.account_id == account_id,
            BankStatementLine.posted_on == day,
        )
        .order_by(BankStatementLine.id)
        .all()
    )
    return [(line, line.id in claimed) for line in lines]


def _rows_on(
    account: Account, ctx: balance_at.BalanceContext, day: date,
) -> "list[tuple[int, Decimal, str, bool]]":
    """Return the app's own settled movements on one day, with match state.

    Args:
        account: The account whose walk to read.
        ctx: The read pass's context, whose scenario scopes the walk.
        day: The settle day.

    Returns:
        ``[(entry_id, amount, description, matched), ...]`` largest movement
        first, so the item that explains most of a day's difference reads
        first.

    **Sourced from the WALK, so these sum to the day's ``recorded`` total.**
    Every fact there is a MOVEMENT
    (:attr:`~app.services.cash_ledger.CashSourceFact.entry_id`; the fold
    reads movements and no row, ruling **R-BAL80**) -- a purchase, or the
    covering movement a settled bill's or paycheck's money walks as -- and
    every app-side match member names a movement (rulings **R-CC43**,
    **R-CC45**), so the match state is the fact's own entry, asked of the
    ONE claims producer (:func:`~app.services.statement_match
    .matched_subjects`, its ``entries``) rather than a second query over the
    members that could drift from the review screen's.
    Through plan step ``credit_card:CC-5-4a-2`` an act recorded before
    ``CC-5-4a-1`` named the covering movement's ROW, and this asked both
    homes; migration ``2eabfa596ee0`` re-keyed those onto the movement.
    """
    facts = [
        fact
        for fact in cash_ledger.walk_cash_ledger(
            account.id, ctx.amounts().scenario_id,
        ).source_facts
        # A fact that moved NOTHING is not an unexplained row.  An envelope
        # whose whole amount is already booked by its posted purchases carries
        # a ``0.00`` parent leg, and listing it under "no bank line is tied to
        # this row" invites the owner to chase something that cannot be
        # matched and moved no money -- three of them on the developer's real
        # 2026-07-02.  They contribute nothing to the day's total either, so
        # dropping them leaves the sum identity intact.  Found by adversarial
        # review 2026-08-24.
        if fact.settled_on == day and fact.delta != _ZERO_MONEY
    ]
    if not facts:
        return []
    names = _row_names(facts)
    claimed = matched_subjects(account.id).entries
    return sorted(
        (
            (
                fact.entry_id,
                fact.delta,
                names.get((fact.transaction_id, fact.entry_id), "(unnamed)"),
                fact.entry_id in claimed,
            )
            for fact in facts
        ),
        key=lambda row: abs(row[1]),
        reverse=True,
    )


def _row_names(facts: list) -> "dict[tuple[int, int], str]":
    """Return a display name for each fact's movement.

    Args:
        facts: :class:`~app.services.cash_ledger.CashSourceFact` values, each
            a movement (the fold reads no row, ruling **R-BAL80**).

    Returns:
        ``{(transaction_id, entry_id): name}``, from the movement's own
        description, in one query -- none when nothing is on the day.
    """
    entry_ids = {fact.entry_id for fact in facts}
    return {
        (txn_id, entry_id): description
        for entry_id, txn_id, description in db.session.query(
            TransactionEntry.id,
            TransactionEntry.transaction_id,
            TransactionEntry.description,
        ).filter(TransactionEntry.id.in_(entry_ids))
    }
