"""The shared read core both confirmed-ledger statements consume (Step 5).

Two statements read the owner's ledger: an income statement
(:mod:`._income_statement`) and a balance sheet (:mod:`._balance_sheet`).  They
share this module so the ledger they read, and how a line is sectioned, signed
and labelled, are defined ONCE, which is what makes the statements articulate
automatically (the income net over a window equals the equity delta between the
bounding balance sheets, with no reconciliation code).

**The ledger is the one worked out from the records** (balance:X-ew's second
leaf, plan task plan#2771): every :class:`~app.services.ledger_lines.LedgerLine`
of the owner's baseline (:func:`app.services.ledger_lines.worked_out_lines`),
each an amount on a chart account, dated by a civil day and carrying the
paycheck its record names.  The stored journal (``budget.journal_entries``
and its postings) is written no more since plan task plan#3133 deleted its
writers, nothing here reads it, and X-ew's third leaf deletes its tables.

**The reader contract (M3), restated over lines.**

* **Whole entries in** (C-1): every leg of one booking shares its line's
  paycheck and its day -- a movement's two legs its parent's paycheck and its
  own ``settled_on``, a correction's legs the ``(kind, paycheck, day)`` its
  target is keyed by, an opening's or true-up's paycheck ``None`` and filed
  by that shared day -- so a window by paycheck or by day takes a booking
  whole or not at all, and the trial balance closes over every window.
* **A pay-period window takes the lines filed in that paycheck** (C-2,
  :meth:`~app.services.ledger_lines.LedgerLine.filed_period_id`): a
  movement's its parent's paycheck, a loan payment split's its payment's, an
  opening's or true-up's by its day on the owner's calendar as the window
  asks (ruling plan#2519).  Only that window asks a calendar (finding
  plan#2875), so the balance sheet reads an owner whose calendar could file
  nothing.
* **A calendar window and an as-of date take the lines by their day** (C-3):
  a movement's ``settled_on``, which IS the owner's civil day (the decided L9
  rule -- an 8:05pm-Eastern Dec-31 settle attributes to Dec 31, not the Jan 1
  it becomes in UTC); an assertion's ``observed_on``; an opening's
  ``opened_on``; a loan payment split's visible day.
* **No residue to drop.**  A hard delete's residue, and the refusals for a
  stored net with no movement or no day, were the store's: a worked-out
  ledger holds a line only for a record that books one, on the day it holds.
* **Natural-balance presentation** (C-4, :func:`present_natural`) and the
  closing lines derived, never booked (C-5, :mod:`._balance_sheet`).

**Sections and labels are the worked-out chart's** (ruling plan#2518): a line's
class is its key's (:attr:`~app.services.ledger_lines.ChartKey.ledger_class`),
and its label :func:`app.services.ledger_lines.chart_label` on the live record
-- so a renamed account relabels its counter and loan lines, and a deleted
category's lines, whose rows read no category, are the Uncategorized bucket's.

**Flask-isolated** and read-only: plain ids in, plain data out; no writes.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.services.ledger_lines import (
    ChartKey,
    LedgerLine,
    chart_label,
    chart_record_type,
    worked_out_lines,
)
from app.services.scenario_resolver import require_baseline_scenario

from ._types import StatementLine, StatementSection

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class StatementLedger:
    """The owner's worked-out baseline ledger, as a statement reads it.

    Attributes:
        lines: Every :class:`~app.services.ledger_lines.LedgerLine` of the
            owner's baseline scenario.
        labels: Each chart key a line is booked on, mapped to the label a
            statement shows for it, from its live record.
    """

    lines: list[LedgerLine]
    labels: dict[ChartKey, str]


def read_statement_ledger(user_id: int) -> StatementLedger:
    """Return *user_id*'s worked-out baseline ledger and its chart's labels.

    The one read both statements make: the baseline scenario's lines
    (:func:`app.services.ledger_lines.worked_out_lines`), then one load of the
    accounts and one of the categories their keys name, each scoped to the
    owner, to label them (:func:`_chart_labels`).

    Args:
        user_id: The owner whose ledger to read.

    Returns:
        The :class:`StatementLedger`.

    Raises:
        BaselineMissingError: The owner has no baseline scenario, so there is
            no ledger to read (from
            :func:`app.services.scenario_resolver.require_baseline_scenario`).
    """
    scenario = require_baseline_scenario(user_id)
    lines = worked_out_lines(user_id, scenario.id)
    return StatementLedger(
        lines=lines,
        labels=_chart_labels(user_id, {line.key for line in lines}),
    )


def _chart_labels(user_id: int, keys: set[ChartKey]) -> dict[ChartKey, str]:
    """Return each key's label, from the live record behind it.

    The records are loaded in one statement per table the keys name
    (:func:`app.services.ledger_lines.chart_record_type`), each scoped to the
    owner, so the count does not grow with the number of lines or keys.  A
    key whose record the owner does not hold is handed no record, which
    :func:`app.services.ledger_lines.chart_label` refuses: its line would
    otherwise be labelled by another owner's name, or by none.

    Args:
        user_id: The owner whose records name the keys.
        keys: The chart keys to label.

    Returns:
        ``{key: label}`` over *keys*.

    Raises:
        ValueError: A key names a record the owner does not hold (from
            :func:`app.services.ledger_lines.chart_label`).
    """
    record_types = {key: chart_record_type(key) for key in keys}
    records = {
        model: _owned_by_id(model, user_id, {
            key.record_id for key, record_type in record_types.items()
            if record_type is model
        })
        for model in (Account, Category)
    }
    return {
        key: chart_label(
            key,
            None if record_type is None
            else records[record_type].get(key.record_id),
        )
        for key, record_type in record_types.items()
    }


def _owned_by_id(
    model: type[Account] | type[Category], user_id: int, ids: set[int],
) -> dict[int, Account | Category]:
    """Return the owner's rows of *model* among *ids*, keyed by id.

    Args:
        model: :class:`Account` or :class:`Category`, each carrying
            ``user_id``.
        user_id: The owner the rows must belong to.
        ids: The ids to load.

    Returns:
        ``{id: row}``; empty, with no statement issued, for no ids.
    """
    if not ids:
        return {}
    rows = (
        db.session.query(model)
        .filter(model.user_id == user_id, model.id.in_(ids))
        .all()
    )
    return {row.id: row for row in rows}


def nets_by_key(lines: Iterable[LedgerLine]) -> dict[ChartKey, Decimal]:
    """Return the debit-positive net of *lines* on each chart key.

    A caller hands the lines its window takes (whole bookings, so the nets
    sum to zero over every window, which is the tie-out's double-entry
    self-check).  A key whose lines cancel is kept at ``$0.00``; the section
    builders drop it.

    Args:
        lines: The lines to net.

    Returns:
        ``{chart key: net}``.
    """
    nets: dict[ChartKey, Decimal] = defaultdict(lambda: _ZERO_MONEY)
    for line in lines:
        nets[line.key] += line.amount
    return dict(nets)


def present_natural(
    ledger_class: LedgerAccountClassEnum, debit_net: Decimal,
) -> Decimal:
    """Return a debit-positive net as its natural-balance value (C-4).

    The reader-contract C-4 presentation rule: a debit-normal class (Asset,
    Expense) presents its debit-positive net as-is; a credit-normal class
    (Liability, Income, Equity, Unrealized) presents the NEGATED net, so a
    revenue, liability, equity or unrealized-change line reads positive when the
    account holds its natural balance -- and an unrealized LOSS therefore reads
    negative, which is the honest rendering of that account's contra position.
    The ledger is presented FAITHFULLY -- there is no ``-abs``
    normalization, so an owed-as-negative non-loan liability renders as a
    positive Liabilities line while a positively-anchored one would render
    negative (the stated non-loan liability sign rule).

    ``_ZERO_MONEY - debit_net`` (not ``-debit_net``) so a zero net presents as
    ``0.00``, never ``-0.00``.

    Args:
        ledger_class: The chart account's class.
        debit_net: The account's debit-positive net.

    Returns:
        The natural-balance value as a ``Decimal``.

    Raises:
        RuntimeError: If the ref cache is not initialized.
    """
    if ref_cache.ledger_class_is_debit_normal(
        ref_cache.ledger_account_class_id(ledger_class),
    ):
        return debit_net
    return _ZERO_MONEY - debit_net


def section_lines(
    nets: dict[ChartKey, Decimal],
    labels: dict[ChartKey, str],
    ledger_class: LedgerAccountClassEnum,
) -> list[StatementLine]:
    """Return the natural-signed, label-sorted lines for one accounting class.

    The presentation step both statements share (extracted so the income
    statement's sections and the balance sheet's sections cannot drift): for
    every key of *ledger_class* with a nonzero net, one :class:`StatementLine`
    labelled from the chart and signed by :func:`present_natural`, sorted by
    label.  Zero-net keys are dropped (an account with no position in the
    window), so no zero line is emitted.

    Args:
        nets: ``{chart key: debit_net}`` (any classes; this filters to
            *ledger_class*).
        labels: The chart's labels (:attr:`StatementLedger.labels`).
        ledger_class: The accounting class to build lines for.

    Returns:
        The class's :class:`StatementLine` list, sorted by label.
    """
    lines = [
        StatementLine(
            label=labels[key],
            amount=present_natural(ledger_class, debit_net),
            key=key,
        )
        for key, debit_net in nets.items()
        if key.ledger_class is ledger_class and debit_net != 0
    ]
    lines.sort(key=lambda line: line.label)
    return lines


def build_section(
    nets: dict[ChartKey, Decimal],
    labels: dict[ChartKey, str],
    ledger_class: LedgerAccountClassEnum,
) -> StatementSection:
    """Return one accounting class's lines and total as a :class:`StatementSection`.

    The section builder both statements share: the class's :func:`section_lines`
    paired with their summed total, so a section's lines and total are derived
    together, from the same set, and cannot drift.  The balance sheet's Equity
    section is instead built directly (it appends the derived retained-earnings
    line before totaling), so this covers Assets / Liabilities / Income /
    Expense / Unrealized.

    Args:
        nets: ``{chart key: debit_net}`` (any classes; filtered to
            *ledger_class*).
        labels: The chart's labels (:attr:`StatementLedger.labels`).
        ledger_class: The accounting class to build the section for.

    Returns:
        The :class:`StatementSection` (label-sorted lines + total).
    """
    lines = section_lines(nets, labels, ledger_class)
    return StatementSection(
        lines=lines,
        total=sum((line.amount for line in lines), _ZERO_MONEY),
    )
