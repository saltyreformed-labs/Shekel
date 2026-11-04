"""Frozen report shapes for the confirmed-ledger statements (Build-Order Step 5).

The plain-data results the reporting readers (:mod:`._income_statement`,
:mod:`._balance_sheet`) return, plus the :class:`StatementWindow` selector the
income statement is computed over.  Decimal-only, frozen, Flask-free: a route
builds a window, a reader returns a report, a template renders it -- no logic
lives here.

Every monetary field is presented in NATURAL-BALANCE terms (the reader-contract
C-4 rule): a debit-normal class (Asset, Expense) carries its debit-positive
net as-is; a credit-normal class (Liability, Income, Equity,
Unrealized) carries the NEGATED sum, so a revenue line, a liability line, an
equity line and an unrealized-change line all read positive when the account
holds its natural balance.  The signing is done once, in the readers; consumers
of these dataclasses read the already-natural value.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.ledger_lines import ChartKey


@dataclass(frozen=True)
class StatementWindow:
    """The time window an income statement is computed over.

    A discriminated selector mirroring
    :class:`app.services.spending_report_service.SpendingWindow`:
    ``window_type`` decides which of the other fields are meaningful --
    ``period_id`` for ``"pay_period"``, ``month`` + ``year`` for ``"month"``,
    ``year`` for ``"year"``.  A ``"pay_period"`` window takes the lines
    filed in that paycheck (reader-contract C-2); a ``"month"`` / ``"year"``
    window takes them by their civil day (C-3).  The two are deliberately separate value
    objects -- the statements attribute by the confirmed ledger's paid-date
    basis while a spending window scopes settled-expense attribution, so
    coupling them would couple two different attribution rules; the route
    layer shares only the parameter PARSING.  This class is the un-disabled
    anchor of that ``duplicate-code`` pair (``SpendingWindow`` carries the
    one-sided disable), mirroring the journal_entry/transfer FK-block
    precedent.

    Attributes:
        window_type: One of ``"pay_period"`` / ``"month"`` / ``"year"``.
        period_id: The pay period id (``"pay_period"`` windows only).
        month: The calendar month 1-12 (``"month"`` windows only).
        year: The calendar year (``"month"`` and ``"year"`` windows).
    """

    window_type: str
    period_id: int | None = None
    month: int | None = None
    year: int | None = None


@dataclass(frozen=True)
class StatementLine:
    """One presented line of a statement: a chart account and its natural amount.

    A single Income / Expense / Unrealized / Asset / Liability / Equity chart
    account's contribution to a statement, its ``amount`` already signed into
    natural-balance terms (see the module docstring).  ``key`` is the chart
    account the line nets (ruling plan#2518), or ``None`` for the two DERIVED
    closing lines on the balance sheet, retained earnings and accumulated
    change in value, which no line is booked on (reader-contract C-5).

    Attributes:
        label: The display label, from the key's live record
            (:func:`app.services.ledger_lines.chart_label`): an account's
            name, a category's ``"Group: Item"``, a bucket's canonical label,
            ``"<account> -- <component>"`` for a counter or loan line; or the
            closing line's own label.
        amount: The line's natural-balance value as a cent-quantized
            ``Decimal``.
        key: The :class:`~app.services.ledger_lines.ChartKey` the line nets,
            or ``None`` for a derived closing line.
    """

    label: str
    amount: Decimal
    key: ChartKey | None = None


@dataclass(frozen=True)
class StatementSection:
    """One labeled group of statement lines with its total.

    A cohesive section of a statement -- Income / Expense on the income
    statement, Assets / Liabilities / Equity on the balance sheet -- pairing the
    (label-sorted, natural-signed, zero-net-dropped) lines with their summed
    ``total``, so a consumer reads a section as one unit and the total is
    derived once beside the lines it sums.

    Attributes:
        lines: The section's :class:`StatementLine` list, sorted by label.
        total: The sum of the section's line amounts (natural-balance terms).
    """

    lines: list[StatementLine]
    total: Decimal


@dataclass(frozen=True)
class TrialBalanceTieOut:
    """The balance sheet's two-part tie-out check.

    The confirmed ledger closes when BOTH: the presented Assets total equals
    the presented Liabilities-plus-Equity total (the accounting identity, with
    retained earnings closing Income + Expense into equity), AND the mechanical
    debit-positive net of every included line is exactly zero (the
    double-entry self-check -- each booking's legs sum to zero, and whole
    bookings are included, so the total must be zero unless a rule booked an
    unbalanced leg).  ``in_balance`` requires both, so a
    classification/presentation bug is caught by the first and an unbalanced
    booking by the second.

    Attributes:
        assets: The presented total of the Asset section.
        liabilities_plus_equity: The presented Liabilities total plus the
            presented Equity total (Equity includes the derived retained
            earnings line).
        ledger_net: The mechanical sum of every included debit-positive line
            through the as-of date; ``Decimal("0")`` for a balanced ledger.
        in_balance: ``True`` iff ``assets == liabilities_plus_equity`` AND
            ``ledger_net == 0``.
    """

    assets: Decimal
    liabilities_plus_equity: Decimal
    ledger_net: Decimal
    in_balance: bool


@dataclass(frozen=True)
class IncomeStatementReport:
    """A confirmed-ledger income statement over one window.

    Revenue (Income-class accounts) and costs (Expense-class accounts) drawn
    from the worked-out ledger for one window, each as a
    :class:`StatementSection` (label-sorted lines + total).  ``net_income`` is
    ``income.total - expense.total``.  Transfers never appear (both their legs
    land on linked Asset/Liability accounts, which are not Income or Expense),
    so this is a pure operating statement.

    **``unrealized`` sits BELOW that line and is deliberately not in it**
    (ruling **R-FO**): a price movement nobody sold into cash is other
    comprehensive income, so counting it as revenue would let a ``$40,000``
    house revaluation read as ``$40,000`` earned.  ``comprehensive_income`` is
    the two figures added -- what the owner's position actually did over the
    window -- and it is stated rather than left to the reader because the
    alternative is a statement that shows two numbers and no relationship
    between them.

    Attributes:
        window_label: The human label of the window (e.g. ``"January 2026"``).
        income: The Income section, natural (positive = revenue).
        expense: The Expense section, natural (positive = cost).
        net_income: ``income.total - expense.total``.
        unrealized: The Unrealized section, natural (positive = gain).
        comprehensive_income: ``net_income + unrealized.total``.
    """

    window_label: str
    income: StatementSection
    expense: StatementSection
    net_income: Decimal
    unrealized: StatementSection
    comprehensive_income: Decimal


@dataclass(frozen=True)
class BalanceSheetReport:
    """A confirmed-ledger balance sheet as of one date.

    The posted ledger's position as of ``as_of``: the Assets, Liabilities, and
    Equity :class:`StatementSection` s (each label-sorted, zero-net accounts
    dropped), the Equity section carrying the derived retained-earnings line
    (reader-contract C-5 -- never posted, computed from the Income + Expense
    accounts).  ``tie_out`` reports whether the ledger closes.  This is the
    POSTED ledger's statement: it excludes modeled growth / appreciation /
    interest between anchor true-ups (that is Net Worth's job, via the
    ``balance_at`` seam), so a footnote records the honesty boundary.

    Attributes:
        as_of: The evaluation date; nets attributed on or before it are folded.
        assets: The Asset section, natural (positive = held).
        liabilities: The Liability section, natural.
        equity: The Equity section, natural.  It carries TWO derived closing
            lines, both computed and never posted: retained earnings (Income +
            Expense) and accumulated change in value (the Unrealized
            class, ruling **R-FO**).  The second is what keeps the tie-out
            closed once a class exists outside the original five -- an
            change in value is still equity on the balance sheet, however it is
            reported on the income statement.
        tie_out: The two-part :class:`TrialBalanceTieOut`.
    """

    as_of: date
    assets: StatementSection
    liabilities: StatementSection
    equity: StatementSection
    tie_out: TrialBalanceTieOut
