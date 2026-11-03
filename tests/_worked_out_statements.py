"""The two confirmed-ledger statements, read from the store and worked out from lines.

The statement half of X-ew's first leaf's production run (plan step plan#2513, plan task
plan#2528), which ``tests/manual/verify_worked_out_ledger.py`` reports: the income statement
for every paycheck, month and year and the balance sheet at every paycheck end, each read from
the store (``app.services.ledger_report_service``) and worked out from the worked-out ledger's
lines, field by field.

**Each statement attributes a line as the store's reader does**
(``ledger_report_service._attribution``): a paycheck's window by the entry's paycheck; a
month's, a year's and a balance sheet's by the movement's bank day or the correction's own day,
the hard-delete residue dropped.  A contribution carries that day as ``attributed_on``.

**What it grades.**  Every stored figure must equal the one worked out from the lines PLUS the
expected source differences that fall in its window: a field that does not is UNEXPLAINED.  So
a statement is graded by the differences the harness has already named, never excused by a
difference it has not.

The worked-out side is built here from per-key nets in the store's own presentation: its sign
rule (``present_natural``) and its closing lines' labels are the store's, imported rather than
restated, so the comparison grades the LINES and nothing a copy of the presentation could hide.

**What "every statement field agreed" does NOT say** (review plan#2521, finding C-L1).  The
worked-out statements are this module's re-implementation of the store's report over the lines,
not the app's report reading them: every field agreeing grades the lines against the app's
report over the STORE, and nothing here can see a defect in the report a reader of the lines
would render.  The proof "every report byte-identical" (``docs/plans/verification.md``; design
report s.12.2, stage 1) is the app's own report, reading the lines, against itself reading the
store: leaf 2's (plan step plan#2515), whose spec carries it as
``tests/manual/verify_statement_baseline.py`` byte-identical before that leaf against after.

Underscore-prefixed module name keeps pytest from collecting it.
"""

from collections import defaultdict, namedtuple
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.exceptions import BaselineMissingError
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.posting_reads import PostingError
from app.services.spending_analysis import calendar_window_bounds

# Pylint: ``shekel-private-module-import`` -- the store's sign rule and its two
# closing lines' labels, named rather than restated (the module docstring says why).
# pylint: disable=shekel-private-module-import
from app.services.ledger_report_service._attribution import present_natural
from app.services.ledger_report_service._balance_sheet import (
    _ACCUMULATED_VALUE_CHANGE_LABEL,
    _RETAINED_EARNINGS_LABEL,
)

_ZERO = Decimal("0.00")


def money(value):
    """Return *value* as a two-place string, never a rounded one."""
    return f"{Decimal(value):.2f}"


def key_text(key):
    """Return a chart key as ``kind/class/record``."""
    return f"{key.kind.value}/{key.ledger_class.value}/{key.record_id}"


def mark(unexplained, parts):
    """Return the mark a report line opens with, for any grain.

    ``!`` an UNEXPLAINED difference (which may hide inside agreeing figures,
    an excused difference and an unexplained one cancelling), ``~`` one that
    parts by an expected difference alone, blank one that agrees.

    Args:
        unexplained: Whether the line's difference is UNEXPLAINED.
        parts: Whether its two figures part.
    """
    if unexplained:
        return "! "
    return "~ " if parts else "  "


def nets_by_key(contributions):
    """Return ``{chart key: net}`` over *contributions*."""
    nets = defaultdict(lambda: _ZERO)
    for contribution in contributions:
        nets[contribution.key] += contribution.amount
    return nets


def report_statements(ledger, explained):
    """Compare every statement the leaf's spec names; the lines and the tally.

    Args:
        ledger: The owner's ledger in its baseline scenario: ``owner_id``,
            ``calendar``, ``key_of`` (each stored chart row's key), and the
            ``stored`` and ``worked`` contributions, each carrying ``key``,
            ``pay_period_id``, ``amount``, ``attributed_on`` and
            ``entry_id`` (``None`` on a worked-out line).
        explained: The contributions, both sides', of every source cell whose
            difference is an expected one.

    Returns:
        ``(the report's lines, how many statement fields are UNEXPLAINED)``.
    """
    lines = []
    statements = _statements(ledger)
    lines.append(f"-- statements: {len(statements)}")
    unexplained = sum(
        _report_statement(ledger, explained, statement, lines.append)
        for statement in statements
    )
    return lines, unexplained


def _windows(ledger):
    """Return every statement window the leaf's spec names.

    Every paycheck the owner has or a line is filed in; every month and year
    either side attributes a line into; and every paycheck's end.

    Returns:
        ``(paycheck ids, (year, month) pairs, years, paycheck ends)``.
    """
    pay_period_ids = {
        period.period_id for period in ledger.calendar.periods
        if period.period_id is not None
    } | {c.pay_period_id for c in ledger.stored + ledger.worked}
    days = {
        c.attributed_on for c in ledger.stored + ledger.worked
        if c.attributed_on is not None
    }
    months = sorted({(day.year, day.month) for day in days})
    years = sorted({day.year for day in days})
    ends = sorted({
        period.end_date for period in ledger.calendar.periods
        if period.period_id is not None
    })
    return sorted(pay_period_ids), months, years, ends


def _income_view(nets):
    """Return the income statement *nets* make, as ``{field: amount}``.

    The store's presentation (``ledger_report_service._income_statement``):
    the Income, Expense and Unrealized sections natural-signed, their
    totals, net income and comprehensive income.

    Args:
        nets: ``{chart key: debit net}`` over the window.
    """
    view = {}
    for section, member in (
        ("income", LedgerAccountClassEnum.INCOME),
        ("expense", LedgerAccountClassEnum.EXPENSE),
        ("unrealized", LedgerAccountClassEnum.UNREALIZED),
    ):
        view[(section, "total")] = _section_into(view, section, member, nets)
    view[("result", "net income")] = (
        view[("income", "total")] - view[("expense", "total")]
    )
    view[("result", "comprehensive income")] = (
        view[("result", "net income")] + view[("unrealized", "total")]
    )
    return view


def _balance_view(nets):
    """Return the balance sheet *nets* make, as ``{field: amount}``.

    The store's presentation (``ledger_report_service._balance_sheet``): the
    Asset, Liability and Equity sections natural-signed, Equity's two closing
    lines, the totals and the tie-out.

    Args:
        nets: ``{chart key: cumulative debit net}`` through the as-of day.
    """
    view = {}
    assets = _section_into(view, "assets", LedgerAccountClassEnum.ASSET, nets)
    liabilities = _section_into(
        view, "liabilities", LedgerAccountClassEnum.LIABILITY, nets,
    )
    equity = _section_into(view, "equity", LedgerAccountClassEnum.EQUITY, nets)
    for label, members in (
        (_RETAINED_EARNINGS_LABEL,
         (LedgerAccountClassEnum.INCOME, LedgerAccountClassEnum.EXPENSE)),
        (_ACCUMULATED_VALUE_CHANGE_LABEL, (LedgerAccountClassEnum.UNREALIZED,)),
    ):
        closed = _ZERO - sum(
            (net for key, net in nets.items() if key.ledger_class in members),
            _ZERO,
        )
        view[("equity", label)] = closed
        equity += closed
    ledger_net = sum(nets.values(), _ZERO)
    view.update({
        ("assets", "total"): assets,
        ("liabilities", "total"): liabilities,
        ("equity", "total"): equity,
        ("tie-out", "liabilities plus equity"): liabilities + equity,
        ("tie-out", "ledger net"): ledger_net,
        ("tie-out", "in balance"): (
            assets == liabilities + equity and ledger_net == 0
        ),
    })
    return view


def _section_into(view, section, member, nets):
    """Write one class's natural-signed lines into *view*; their total."""
    class_id = ref_cache.ledger_account_class_id(member)
    total = _ZERO
    for key, net in nets.items():
        if key.ledger_class is member and net != 0:
            view[(section, key)] = present_natural(class_id, net)
            total += view[(section, key)]
    return total


def _stored_income_view(report, key_of):
    """Return a stored income statement as ``{field: amount}``."""
    view = {}
    for section in ("income", "expense", "unrealized"):
        part = getattr(report, section)
        _stored_lines_into(view, section, part.lines, key_of)
        view[(section, "total")] = part.total
    view[("result", "net income")] = report.net_income
    view[("result", "comprehensive income")] = report.comprehensive_income
    return view


def _stored_balance_view(report, key_of):
    """Return a stored balance sheet as ``{field: amount}``."""
    view = {}
    for section in ("assets", "liabilities", "equity"):
        part = getattr(report, section)
        _stored_lines_into(view, section, part.lines, key_of)
        view[(section, "total")] = part.total
    view.update({
        ("tie-out", "liabilities plus equity"):
            report.tie_out.liabilities_plus_equity,
        ("tie-out", "ledger net"): report.tie_out.ledger_net,
        ("tie-out", "in balance"): report.tie_out.in_balance,
    })
    return view


def _stored_lines_into(view, section, lines, key_of):
    """Write a stored section's lines into *view*, keyed as the read keys them.

    A posted line by its row's chart key (two rows of one key summed); a
    closing line, which no row posts, by its label.
    """
    for line in lines:
        if line.ledger_account_id is None:
            view[(section, line.label)] = line.amount
            continue
        cell = (section, key_of[line.ledger_account_id])
        view[cell] = view.get(cell, _ZERO) + line.amount


def _differing_fields(stored, worked):
    """Return the fields where two views part, a missing line read as zero."""
    return sorted(
        (
            name for name in stored.keys() | worked.keys()
            if stored.get(name, _ZERO) != worked.get(name, _ZERO)
        ),
        key=str,
    )


# One statement to compare: its heading; whether a contribution falls in its
# window, as the store's reader attributes it; a call reading it from the
# store; and its two view builders, for nets and for the stored report.
Statement = namedtuple(
    "Statement", "title in_window read worked_view stored_view",
)


def _statements(ledger):
    """Return every :data:`Statement` the leaf's spec names, in report order.

    Every paycheck's income statement (a paycheck's window is the entry's
    paycheck), then every month's and year's (a contribution's
    ``attributed_on``), then the balance sheet at every paycheck end (every
    ``attributed_on`` through it).
    """
    pay_period_ids, months, years, ends = _windows(ledger)
    owner, calendar = ledger.owner_id, ledger.calendar
    statements = [
        Statement(
            f"income statement, paycheck {pay_period_id}",
            lambda c, p=pay_period_id: c.pay_period_id == p,
            lambda p=pay_period_id: ledger_report_service.compute_income_statement(
                owner, calendar, StatementWindow("pay_period", period_id=p),
            ),
            _income_view, _stored_income_view,
        )
        for pay_period_id in pay_period_ids
    ]
    for window_type, year, month in (
        [("month", year, month) for year, month in months]
        + [("year", year, None) for year in years]
    ):
        first, last = calendar_window_bounds(window_type, year, month)
        statements.append(Statement(
            f"income statement, {window_type} {first.isoformat()} - "
            f"{last.isoformat()}",
            lambda c, a=first, b=last: (
                c.attributed_on is not None and a <= c.attributed_on <= b
            ),
            lambda t=window_type, y=year, m=month: (
                ledger_report_service.compute_income_statement(
                    owner, calendar, StatementWindow(t, month=m, year=y),
                )
            ),
            _income_view, _stored_income_view,
        ))
    statements.extend(
        Statement(
            f"balance sheet as of {end.isoformat()}",
            lambda c, e=end: (
                c.attributed_on is not None and c.attributed_on <= e
            ),
            lambda e=end: ledger_report_service.compute_balance_sheet(owner, e),
            _balance_view, _stored_balance_view,
        )
        for end in ends
    )
    return statements


def _report_statement(ledger, explained, statement, out):
    """Print one statement on both sides, and tally its unexplained fields.

    Args:
        ledger: The owner's ledger, as :func:`report_statements` takes it.
        explained: The contributions of every expected source difference.
        statement: The :data:`Statement`.
        out: Where each line of the report goes.

    Returns:
        How many of its fields are UNEXPLAINED; one when the store refused
        to produce it.
    """
    try:
        stored = statement.stored_view(statement.read(), ledger.key_of)
    except (PostingError, BaselineMissingError) as exc:
        out(f"!  {statement.title}: the store REFUSED it: {exc}")
        return 1
    worked_nets = nets_by_key(
        c for c in ledger.worked if statement.in_window(c)
    )
    # The worked-out nets, moved by every expected difference in the window:
    # a stored part added, a worked-out part taken away.
    adjusted_nets = defaultdict(lambda: _ZERO, worked_nets)
    for c in explained:
        if statement.in_window(c):
            sign = 1 if c.entry_id is not None else -1
            adjusted_nets[c.key] += sign * c.amount
    worked = statement.worked_view(worked_nets)
    unexplained = _differing_fields(stored, statement.worked_view(adjusted_nets))
    out(f"   {statement.title}: {len(_differing_fields(stored, worked))} "
        f"field(s) part, {len(unexplained)} UNEXPLAINED; each line: stored | "
        "worked-out")
    _print_fields(stored, worked, unexplained, out)
    return len(unexplained)


def _print_fields(stored, worked, unexplained, out):
    """Print every field of one statement on both sides, marked.

    ``!`` an UNEXPLAINED field, ``~`` one that parts by an expected
    difference, blank one that agrees.
    """
    for name in sorted(stored.keys() | worked.keys(), key=_field_order):
        held, made = stored.get(name, _ZERO), worked.get(name, _ZERO)
        section, part = name
        shown = part if isinstance(part, str) else key_text(part)
        out(f"{mark(name in unexplained, held != made)}   {section} {shown}: "
            f"{_shown(held)} | {_shown(made)}")


# The order a statement's sections print in, as the store presents them.
_SECTIONS = (
    "income", "expense", "unrealized", "result",
    "assets", "liabilities", "equity", "tie-out",
)


def _field_order(name):
    """Return the sort key a statement field prints by: section, lines, total."""
    section, part = name
    return (_SECTIONS.index(section), part == "total", str(part))


def _shown(value):
    """Return a statement field's value as the report prints it."""
    return str(value) if isinstance(value, bool) else money(value)
