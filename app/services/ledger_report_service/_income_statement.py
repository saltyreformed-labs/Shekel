"""Confirmed-ledger income statement (Build-Order Step 5).

Revenue and costs drawn from the owner's worked-out ledger for one window:
the Income-class and Expense-class chart accounts' presented natural balances
over the window, plus the derived net income.  Reads the baseline scenario only
(the deferred multi-scenario policy is R8's); a user with no baseline is
refused.

**Below that line sits the Unrealized section** (ruling **R-FO**, plan step
X-f3d): a modelled account's balance-assertion true-up books its counter leg
to a per-account Change in Value chart account, so investment return is
reported instead of vanishing into equity.  It is a class of its own precisely
so ``net_income`` cannot count it, and ``comprehensive_income`` is the sum the
statement states rather than leaves to the reader.

**Two window paths, one honesty rule** (:mod:`._attribution` states the
reader contract over lines).  A ``"pay_period"`` window takes the lines filed
in that paycheck (reader-contract C-2): a movement's in its parent's, a loan
payment split's in its payment's, an opening's or true-up's by its day on the
caller's calendar
(:meth:`~app.services.ledger_lines.LedgerLine.filed_period_id`), the one
reader that asks a calendar of the ledger (finding plan#2875).  A
``"month"`` / ``"year"`` window takes the lines by their civil day (C-3), so
an early-settled movement appears in its actual paid month even when its
parent's paycheck sits elsewhere -- both are honest answers to different
questions.

Transfers never appear on either path: both a transfer's legs land on linked
Asset/Liability accounts and the owner's Transfers in transit, outside the
Income/Expense filter.  Flask-isolated, read-only.
"""

from collections.abc import Callable
from decimal import Decimal

from app.enums import LedgerAccountClassEnum
from app.services import spending_analysis
from app.services.ledger_lines import ChartKey, LedgerLine
from app.services.pay_calendar import PayCalendar

from ._attribution import build_section, nets_by_key, read_statement_ledger
from ._types import IncomeStatementReport, StatementWindow

# The accounting classes the income statement reports on, in presentation
# order: Income and Expense (the operating result) plus Unrealized (ruling
# **R-FO**'s other comprehensive income, reported below the net-income line).
# Stated once so the two window paths select the SAME set: they answer
# different questions about which lines fall in the window, never different
# questions about which accounts belong on the statement.
_REPORTED_CLASSES = frozenset({
    LedgerAccountClassEnum.INCOME,
    LedgerAccountClassEnum.EXPENSE,
    LedgerAccountClassEnum.UNREALIZED,
})


def compute_income_statement(
    user_id: int, calendar: PayCalendar, window: StatementWindow,
) -> IncomeStatementReport:
    """Return the confirmed-ledger income statement for a user over a window.

    Income (revenue) and Expense (cost) lines from the baseline scenario's
    worked-out ledger, each natural-signed (positive = revenue / cost) and
    sorted by label, plus ``net_income = total_income - total_expense``; then,
    BELOW that line, the Unrealized section and ``comprehensive_income =
    net_income + unrealized.total`` (ruling **R-FO**).

    **The window's PERIOD comes from the caller's calendar** (plan step
    C2-f3a), which is a REQUIRED parameter for the reason the contribution
    tier's is (ruling **R-PC19**, "How the CONTRIBUTION tier learns its
    periods"): the route that calls this already derives one for its own
    ``<select>``, so resolving a second here made the heading and the option
    the reader picked it from two reads of one table on one render.  **A
    ``"pay_period"`` window files its corrections on that same calendar**
    (:func:`_window_test`; the read filed them on it, as plan task plan#2771
    built, until finding plan#2875 moved the filing here), so the render
    derives the owner's schedule once
    (``tests/test_arch/test_one_read_pass_per_render/``).  **Another owner's
    calendar is refused**, whatever the window: an opening filed in another
    owner's paycheck would be in no paycheck of its own owner's, and the read
    refused the pairing while it filed (plan task plan#2771).

    **And it closed a hole the route had to guard.**  This was
    ``db.session.get(PayPeriod, window.period_id)`` -- UNSCOPED, so a foreign
    ``period_id`` resolved another owner's row and its dates were rendered into
    the heading.  Nothing here refused it; the only thing standing in the way
    was ``analytics._validate_owned_or_abort`` at the route boundary, whose
    comment says so in as many words.  A calendar carries ONE owner's periods,
    so a foreign id now answers ``None`` by construction and the label is
    empty.  The route guard STAYS -- it is what emits the
    ``access_denied_cross_user`` audit event, and a silent empty label is not
    a refusal -- but the leak no longer depends on it being there.

    Args:
        user_id: The owner whose statement to compute.
        calendar: That owner's
            :class:`~app.services.pay_calendar.PayCalendar`, derived once by
            the caller: it labels a ``"pay_period"`` window, and that window
            files each correction on it.
        window: The :class:`StatementWindow` selector (a ``"pay_period"`` window
            takes the lines filed in its paycheck; a ``"month"`` / ``"year"``
            window takes them by their civil day).

    Returns:
        The :class:`IncomeStatementReport`.

    Raises:
        ValueError: If the window is an invalid type or omits a field its type
            requires (via
            :func:`app.services.spending_analysis.validate_window`); or if
            *calendar* is another owner's.
        BaselineMissingError: The owner has no baseline scenario.  It used to
            report an all-zero income statement, which reads as "you earned
            and spent nothing this period" rather than "this ledger cannot be
            read".
        PayCalendarError: A ``"pay_period"`` window, and a correction with no
            paycheck on *calendar* to file in (from
            :meth:`~app.services.ledger_lines.LedgerLine.filed_period_id`).
    """
    if calendar.user_id != user_id:
        raise ValueError(
            f"the income statement of user {user_id} was handed user "
            f"{calendar.user_id}'s pay calendar: a correction is filed in "
            f"its owner's own paychecks"
        )
    spending_analysis.validate_window(
        window.window_type, window.period_id, window.month, window.year,
    )
    period = (
        calendar.period_by_id(window.period_id)
        if window.window_type == "pay_period" else None
    )
    window_label = spending_analysis.window_label(
        window.window_type, window.month, window.year, period,
    )
    ledger = read_statement_ledger(user_id)
    in_window = _window_test(window, calendar)
    nets = nets_by_key(
        line for line in ledger.lines
        if in_window(line) and line.key.ledger_class in _REPORTED_CLASSES
    )
    return _income_statement_from_nets(nets, ledger.labels, window_label)


def _window_test(
    window: StatementWindow, calendar: PayCalendar,
) -> Callable[[LedgerLine], bool]:
    """Return whether a line falls in *window*, by the reader contract.

    A ``"pay_period"`` window takes the lines filed in its paycheck (C-2,
    :meth:`~app.services.ledger_lines.LedgerLine.filed_period_id` on
    *calendar*); a ``"month"`` / ``"year"`` window the lines whose civil day
    falls in its inclusive bounds (C-3), asking no calendar.  Either takes a
    booking whole, its legs sharing their paycheck and their day.

    Args:
        window: The validated :class:`StatementWindow`.
        calendar: The owner's pay calendar, which files each opening and
            true-up of a ``"pay_period"`` window by its day.

    Returns:
        A predicate over :class:`~app.services.ledger_lines.LedgerLine`.
    """
    if window.window_type == "pay_period":
        return lambda line: line.filed_period_id(calendar) == window.period_id
    first_day, last_day = spending_analysis.calendar_window_bounds(
        window.window_type, window.year, window.month,
    )
    return lambda line: first_day <= line.entry_date <= last_day


def _income_statement_from_nets(
    nets: dict[ChartKey, Decimal],
    labels: dict[ChartKey, str],
    window_label: str,
) -> IncomeStatementReport:
    """Assemble the report from per-key debit nets.

    Builds the Income, Expense and Unrealized sections (label-sorted,
    natural-signed: revenue / cost / gain positive), derives net income from
    the first two, and comprehensive income from net income plus the third.

    **Net income is the operating result and the Unrealized total is NOT in
    it** (ruling **R-FO**): a change in what a holding is worth has not been
    sold into cash, so folding it in would let a house revaluation read as
    earnings.

    Args:
        nets: ``{chart key: debit_net}`` over the reported accounts.
        labels: The chart's labels.
        window_label: The human window label.

    Returns:
        The assembled :class:`IncomeStatementReport`.
    """
    income = build_section(nets, labels, LedgerAccountClassEnum.INCOME)
    expense = build_section(nets, labels, LedgerAccountClassEnum.EXPENSE)
    unrealized = build_section(nets, labels, LedgerAccountClassEnum.UNREALIZED)
    net_income = income.total - expense.total
    return IncomeStatementReport(
        window_label=window_label,
        income=income,
        expense=expense,
        net_income=net_income,
        unrealized=unrealized,
        comprehensive_income=net_income + unrealized.total,
    )
