"""Confirmed-ledger balance sheet (Build-Order Step 5).

The owner's ledger position as of a date: Assets, Liabilities, and Equity
sections folded from the worked-out ledger's lines through the date, plus the
two derived lines that close the income-statement classes into equity
(reader-contract C-5 -- never booked): retained earnings for Income + Expense,
and accumulated change in value for the Unrealized class (ruling **R-FO**).
A two-part tie-out reports whether the ledger closes: the presented
accounting identity (Assets == Liabilities + Equity) AND the mechanical
double-entry self-check (every included debit-positive line nets to zero).

This is the POSTED ledger's statement.  It reflects only asserted anchor facts
and settled activity; it excludes modeled growth / appreciation / interest
between anchor true-ups -- that projection is Net Worth's job, via the
``balance_at`` seam.  A future-dated opening (the rolling edge) is simply
excluded from an as-of-today sheet, and because a booking's lines share one
day, excluding one keeps the tie-out closed.  Reads the baseline scenario
only (R8 owns the multi-scenario policy); a user with no baseline is refused.
Flask-isolated, read-only.
"""

from datetime import date
from decimal import Decimal
from functools import partial

from app.enums import LedgerAccountClassEnum
from app.services.ledger_lines import ChartKey
from app.services.pay_calendar import calendar_for

from ._attribution import (
    build_section,
    nets_by_key,
    read_statement_ledger,
    section_lines,
)
from ._types import (
    BalanceSheetReport,
    StatementLine,
    StatementSection,
    TrialBalanceTieOut,
)

_ZERO_MONEY = Decimal("0.00")
_RETAINED_EARNINGS_LABEL = "Retained Earnings"
_ACCUMULATED_VALUE_CHANGE_LABEL = "Accumulated Change in Value"


def compute_balance_sheet(user_id: int, as_of: date) -> BalanceSheetReport:
    """Return the confirmed-ledger balance sheet for a user as of a date.

    Folds every line of the owner's worked-out baseline ledger dated on or
    before *as_of* into per-key cumulative positions, sections them by
    accounting class, derives the two closing equity lines -- retained
    earnings, and the accumulated change in value where there is one (ruling
    **R-FO**) -- and reports the two-part tie-out.

    **It holds no pay calendar, so the read derives the owner's** when a
    correction is to be filed (:func:`~app.services.pay_calendar.calendar_for`
    bound to the owner, plan task plan#2771): its route renders no paycheck,
    and an owner with no correction to file is asked no calendar.

    Args:
        user_id: The owner whose balance sheet to compute.
        as_of: The evaluation date; lines dated on or before it are folded
            (a future-dated booking is excluded, whole).

    Returns:
        The :class:`BalanceSheetReport`.

    Raises:
        BaselineMissingError: When the user has no baseline scenario, so this
            ledger cannot be read at all.  It used to return an EMPTY sheet
            instead: assets ``$0.00``, liabilities ``$0.00``, equity ``$0.00``
            and ``tie_out.in_balance = True`` -- the app ASSERTING that a
            user's books balance over a ledger it could not read (plan step
            X-v2's adversarial review; the same fabrication ruling R-CA deleted
            from the net-worth hero, one screen over).  A statement that cannot
            be produced is not a statement of zeros.
        PayCalendarError: A correction is to be filed and the owner holds no
            pay schedule, or no paycheck to file it in (from the read).
    """
    ledger = read_statement_ledger(user_id, partial(calendar_for, user_id))
    cumulative = nets_by_key(
        line for line in ledger.lines if line.entry_date <= as_of
    )
    return _balance_sheet_from_cumulative(as_of, cumulative, ledger.labels)


def _balance_sheet_from_cumulative(
    as_of: date,
    cumulative: dict[ChartKey, Decimal],
    labels: dict[ChartKey, str],
) -> BalanceSheetReport:
    """Assemble the report from per-key cumulative nets.

    Sections the nets (Asset / Liability / Equity), appends the derived
    closing lines to Equity, and computes the totals and two-part tie-out.

    Args:
        as_of: The evaluation date recorded on the report.
        cumulative: ``{chart key: cumulative_debit_net}`` through *as_of*.
        labels: The chart's labels.

    Returns:
        The assembled :class:`BalanceSheetReport`.
    """
    assets = build_section(cumulative, labels, LedgerAccountClassEnum.ASSET)
    liabilities = build_section(
        cumulative, labels, LedgerAccountClassEnum.LIABILITY,
    )
    equity = _equity_section(cumulative, labels)

    liabilities_plus_equity = liabilities.total + equity.total
    # The mechanical double-entry self-check: the raw debit-positive net of every
    # included line, independent of the class-based presentation above.  Zero
    # for a balanced ledger (each booking's legs sum to zero); nonzero exposes
    # an unbalanced booking the section identity alone could miss.
    ledger_net = sum(cumulative.values(), _ZERO_MONEY)

    return BalanceSheetReport(
        as_of=as_of,
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        tie_out=TrialBalanceTieOut(
            assets=assets.total,
            liabilities_plus_equity=liabilities_plus_equity,
            ledger_net=ledger_net,
            in_balance=(
                assets.total == liabilities_plus_equity and ledger_net == 0
            ),
        ),
    )


def _equity_section(
    cumulative: dict[ChartKey, Decimal],
    labels: dict[ChartKey, str],
) -> StatementSection:
    """Return the Equity section, with its two derived closing lines last.

    The Equity accounts (:func:`._attribution.section_lines`) plus the derived
    closing lines (reader-contract C-5) appended AFTER them, with the total
    summing all of them.  Built directly rather than via
    :func:`._attribution.build_section` because those closing lines are
    computed, not booked accounts.

    Two lines close, one per class set the income statement reports and the
    balance sheet does not: **Retained Earnings** closes Income + Expense, and
    **Accumulated Change in Value** closes the Unrealized class (ruling
    **R-FO**).  The second is not decoration -- the presented tie-out is
    ``assets == liabilities + equity``, which holds only because every class
    outside Assets and Liabilities is folded into equity exactly once.  Adding
    a reporting class without its closing line would put the sheet out of
    balance by that class's whole net (``$10,623.66`` on a production clone
    2026-08-13).

    Args:
        cumulative: ``{chart key: cumulative_debit_net}`` through the as-of
            date.
        labels: The chart's labels.

    Returns:
        The Equity :class:`StatementSection`.
    """
    lines = section_lines(cumulative, labels, LedgerAccountClassEnum.EQUITY)
    lines.append(StatementLine(
        label=_RETAINED_EARNINGS_LABEL,
        amount=_closed_class_total(cumulative, frozenset({
            LedgerAccountClassEnum.INCOME, LedgerAccountClassEnum.EXPENSE,
        })),
        key=None,
    ))
    accumulated_unrealized = _closed_class_total(
        cumulative, frozenset({LedgerAccountClassEnum.UNREALIZED}),
    )
    # Dropped when it is zero, which is the rule :func:`section_lines` already
    # applies to a booked account with no position -- an owner holding no
    # investment or property has no unrealized position to report, and a
    # ``$0.00`` line would say they do.  Retained earnings is unconditional by
    # contrast: it is this statement's own closing line, and zero cumulative
    # net income is a real answer.  Dropping a zero cannot move the section
    # total, so the tie-out is untouched either way.
    if accumulated_unrealized != 0:
        lines.append(StatementLine(
            label=_ACCUMULATED_VALUE_CHANGE_LABEL,
            amount=accumulated_unrealized,
            key=None,
        ))
    return StatementSection(
        lines=lines,
        total=sum((line.amount for line in lines), _ZERO_MONEY),
    )


def _closed_class_total(
    cumulative: dict[ChartKey, Decimal],
    closed_classes: frozenset[LedgerAccountClassEnum],
) -> Decimal:
    """Return the natural equity value of a class set closed into equity (C-5).

    The accumulated position of *closed_classes* through the as-of date, as
    an equity line: the NEGATED cumulative debit net of every account in those
    classes.  Income credits and Expense debits net to income, an unrealized
    gain is a credit, and equity presents credit-normal -- so in both cases the
    natural line is the negated debit net.  Computed, never booked; there are
    no closing entries in this ledger.

    ONE function for both closing lines rather than one each: they differ only
    in which classes they gather, and two copies of "negate the cumulative
    debit net of these classes" is exactly the kind of duplicate the next
    reporting class would triple.

    Args:
        cumulative: ``{chart key: cumulative_debit_net}`` through the as-of
            date; each key carries its class.
        closed_classes: The accounting classes this line closes.

    Returns:
        The line's natural-balance amount as a ``Decimal`` (positive when the
        closed classes are cumulatively in credit -- profitable, or in gain).
    """
    closed_debit = sum(
        (
            debit_net
            for key, debit_net in cumulative.items()
            if key.ledger_class in closed_classes
        ),
        _ZERO_MONEY,
    )
    return _ZERO_MONEY - closed_debit
