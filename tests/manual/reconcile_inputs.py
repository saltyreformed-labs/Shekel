"""What the reconcile act sheet classes: the two exports, and the days left apart.

The INPUT half of :mod:`reconcile_act_sheet` (plan step **balance:X-bk-2**,
plan#1192, task plan#2372), split from it on its seam because the two stood at
1,132 lines together: this asks the import what SECU's export would add --
the import's own decision, read without performing it -- reads Capital One's
export by its header, and gathers every books day the import leaves apart with
its unanswered movements named -- :class:`Row` for the app's, :class:`Line`
for SECU's.  How those are CLASSED is :mod:`reconcile_act_sheet`'s.

Reads only, inside the read-only app :mod:`reconcile_act_sheet` builds.
"""

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import joinedload

from app.enums import StatementSourceEnum
from app.exceptions import StatementImportError
from app.extensions import db
from app.models.account import Account
from app.models.statement_import import BankStatementLine
from app.models.transaction_entry import TransactionEntry
from app.services import (
    balance_at,
    bank_agreement,
    outstanding_difference,
    statement_import,
)
from app.services.outstanding_difference import StretchKind
from app.utils.dates import days_in_range

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

#: The Capital One export's columns this reads, bound by NAME as the SECU
#: adapter binds its own (``statement_import._secu_csv``): a positional read
#: would reinterpret a column the bank moved.
_CARD_COLUMNS = ("Transaction Date", "Posted Date", "Description", "Debit",
                 "Credit")


def words_of(text: str) -> "frozenset[str]":
    """Return the words of *text* that can name a store, lower-cased.

    A word is a run of three or more letters, an apostrophe dropped so
    ``BJ's`` and ``BJS`` agree.  **A heuristic, and used only where a wrong
    answer costs nothing a cancel depends on alone**: whether a row names a
    charge's store ORDERS which row a contested charge goes to and bounds which
    charges are added up to one row; it never refuses a single charge.
    """
    return frozenset(
        re.findall(r"[a-z]{3,}", text.lower().replace("'", "").replace("’", ""))
    )


@dataclass(frozen=True)
class Row:  # pylint: disable=too-many-instance-attributes
    """One unanswered movement of the app's, as the sheet names it.

    Pylint: too-many-instance-attributes (10/7) -- ten because an act needs
    them: which movement and which row or transfer it belongs to, its two
    days, its figure, its two names, and which of the two acts cancels it.

    Attributes:
        entry_id: The movement (``budget.transaction_entries``).
        transaction_id: Its row, or ``None`` for a transfer's side.
        transfer_id: Its transfer, or ``None``.
        payback_for: The row a CC Payback pays back, or ``None``.
        day: The day the books hold it on -- its settle day.
        purchased_on: The day the owner recorded it as bought.
        amount: Signed, positive INTO the account.
        row: Its row's name, or ``None`` for a transfer's side.
        line: The movement's own description.
        envelope_line: Whether it is a purchase line of an envelope rather
            than a row's own record, which decides its NEVER MOVED act.
    """

    entry_id: int
    transaction_id: "int | None"
    transfer_id: "int | None"
    payback_for: "int | None"
    day: date
    purchased_on: date
    amount: Decimal
    row: "str | None"
    line: str
    envelope_line: bool

    @property
    def earliest(self) -> date:
        """Return the earlier of its two days, where its window opens."""
        return min(self.purchased_on, self.day)

    @property
    def words(self) -> "frozenset[str]":
        """Return the words its two names hold (:func:`words_of`)."""
        return words_of(f"{self.line} {self.row or ''}")

    def named(self) -> dict:
        """Return it as the sheet writes it."""
        return {
            "entry_id": self.entry_id,
            "transaction_id": self.transaction_id,
            "transfer_id": self.transfer_id,
            "day": self.day.isoformat(),
            "purchased_on": self.purchased_on.isoformat(),
            "amount": str(self.amount),
            "row": self.row,
            "line": self.line,
            "envelope_line": self.envelope_line,
        }


@dataclass(frozen=True)
class Line:
    """One unanswered SECU line, recorded or only in the export.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row, or ``None`` for a
            line only the export holds, which the import will add.
        posted_on: The day SECU posted it.
        transaction_on: The day SECU says it was made, or ``None``.
        amount: Signed, positive INTO the account.
        description: SECU's own words.
    """

    line_id: "int | None"
    posted_on: date
    transaction_on: "date | None"
    amount: Decimal
    description: str

    def named(self) -> dict:
        """Return it as the sheet writes it."""
        return {
            "line_id": self.line_id,
            "posted_on": self.posted_on.isoformat(),
            "transaction_on": (
                None if self.transaction_on is None
                else self.transaction_on.isoformat()
            ),
            "amount": str(self.amount),
            "description": self.description,
        }


@dataclass(frozen=True)
class CardLine:
    """One line of Capital One's export.

    Attributes:
        transaction_on: The day the card says it was made.
        posted_on: The day the card posted it.
        amount: Its MAGNITUDE.
        is_credit: Whether it is a credit to the card -- a payment, cash back
            or a refund -- rather than a charge.
        description: Capital One's own words.
    """

    transaction_on: date
    posted_on: date
    amount: Decimal
    is_credit: bool
    description: str

    @property
    def words(self) -> "frozenset[str]":
        """Return the words its description holds (:func:`words_of`)."""
        return words_of(self.description)

    def named(self) -> dict:
        """Return it as the sheet writes it."""
        return {
            "transaction_on": self.transaction_on.isoformat(),
            "posted_on": self.posted_on.isoformat(),
            "amount": str(self.amount),
            "credit": self.is_credit,
            "description": self.description,
        }


@dataclass(frozen=True)
class ClassedDay:
    """One day whose unanswered movements the sheet classes.

    Attributes:
        day: The civil day.
        residue: What the app's rows move that the bank's lines do not, once
            the import has added the export's fresh lines -- the
            comparison's own residue less those lines.
        rows: The day's unanswered movements of the app's.
        lines: The day's unanswered SECU lines, the fresh ones included.
    """

    day: date
    residue: Decimal
    rows: "tuple[Row, ...]"
    lines: "tuple[Line, ...]"

    @property
    def adds_up(self) -> bool:
        """Return whether its unanswered movements ARE its whole residue.

        False when a match pairs a movement here with one on another day, so
        the day's difference holds money no act on this day answers.
        """
        return (
            sum((row.amount for row in self.rows), ZERO)
            - sum((line.amount for line in self.lines), ZERO)
        ) == self.residue


def _money(raw: str, where: str) -> Decimal:
    """Return *raw* as a positive amount, refusing anything else.

    Args:
        raw: The cell's text.
        where: Which line, for the refusal.

    Raises:
        SystemExit: When it is not a positive figure to the cent.
    """
    try:
        amount = Decimal(raw)
    except InvalidOperation as error:
        raise SystemExit(f"{where}: {raw!r} is not an amount") from error
    if amount <= ZERO or amount != amount.quantize(CENT):
        raise SystemExit(f"{where}: {raw!r} is not a positive amount to the cent")
    return amount


def _day(raw: str, where: str) -> date:
    """Return *raw* (``YYYY-MM-DD``) as a day, refusing anything else."""
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as error:
        raise SystemExit(f"{where}: {raw!r} is not a day") from error


def read_card_export(payload: bytes) -> "list[CardLine]":
    """Return the lines of Capital One's CSV export.

    **Read here because the app has no Capital One adapter yet**: the card has
    no Shekel account to import into until the card arc gives it one
    (``statement_import._adapters``), and this reads the file only to class
    Checking's movements against it.

    Args:
        payload: The file's bytes.

    Returns:
        Every line, in the file's order.

    Raises:
        SystemExit: When a column this reads is missing, or a line states both
            or neither of a debit and a credit, or a figure or day it cannot
            read.
    """
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    missing = [
        name for name in _CARD_COLUMNS if name not in (reader.fieldnames or [])
    ]
    if missing:
        raise SystemExit(
            f"the Capital One export has no column {', '.join(missing)}: it "
            "is not the file this reads"
        )
    lines = []
    for number, record in enumerate(reader, start=2):
        where = f"Capital One export line {number}"
        debit = record["Debit"].strip()
        credit = record["Credit"].strip()
        if bool(debit) == bool(credit):
            raise SystemExit(
                f"{where} states {'both' if debit else 'neither'} a debit and "
                "a credit"
            )
        lines.append(CardLine(
            transaction_on=_day(record["Transaction Date"], where),
            posted_on=_day(record["Posted Date"], where),
            amount=_money(debit or credit, where),
            is_credit=bool(credit),
            description=record["Description"].strip(),
        ))
    return lines


def plan_import(
    account: Account, payload: bytes,
) -> "statement_import.StatementPlan":
    """Return what importing SECU's export into *account* would do.

    **The import's own decision** (:func:`~app.services.statement_import
    .plan_statement`), which reads and writes nothing: its parse by the app's
    adapter, its identity check -- the account the owner names held against
    the account the FILE names, as the upload form's choice is (ruling
    **R-FP**) -- and its pairing of every line against what SECU already
    sighted, so the lines it would add are the import's answer and not a
    second spelling of its pairing.

    Raises:
        SystemExit: When the import would refuse the file, with its reason --
            no import would then cover the days the sheet classes.
    """
    try:
        return statement_import.plan_statement(
            account.id, account.user_id,
            StatementSourceEnum.SECU_CHECKING_CSV, payload,
        )
    except StatementImportError as error:
        raise SystemExit(
            f"the import would refuse the SECU export: {error}"
        ) from error


def fresh_lines(plan: "statement_import.StatementPlan") -> "list[Line]":
    """Return the lines *plan* would ADD, as the sheet names a line.

    Each is only in the export, so it has no line id yet.
    """
    return [
        Line(
            line_id=None, posted_on=line.posted_on,
            transaction_on=line.transaction_on, amount=line.amount,
            description=line.description,
        )
        for line in plan.fresh_lines
    ]


def _uncovered_days(
    listing: "outstanding_difference.DisagreementList",
    parsed: "statement_import.ParsedStatement",
) -> "list[date]":
    """Return the books days no import covers yet, which the export must.

    Raises:
        SystemExit: When a books day is one the comparison cannot take, or one
            the export does not cover either.
    """
    days = []
    for stretch in listing.stretches:
        first, last = stretch.verdict.first_day, stretch.verdict.last_day
        if stretch.kind is StretchKind.NOT_COMPARED:
            raise SystemExit(
                f"the books days {first}..{last} lie outside what the "
                "comparison draws; the sheet cannot class them"
            )
        if stretch.kind is StretchKind.NO_STATEMENT:
            if first < parsed.declared_start or last > parsed.declared_end:
                raise SystemExit(
                    f"no import covers the books days {first}..{last} and "
                    f"the SECU export covers only {parsed.declared_start}.."
                    f"{parsed.declared_end}: import a later export first"
                )
            days.extend(days_in_range(first, last))
    return days


@dataclass(frozen=True)
class _Picked:
    """One day :func:`classed_days` keeps, before its movements are named.

    Attributes:
        day: The civil day.
        residue: Its residue once the import has added the export's lines.
        detail: The drill-down's day (:func:`bank_agreement.day_detail`).
        added: The export's fresh lines the import adds to it.
    """

    day: date
    residue: Decimal
    detail: "bank_agreement.DayDetail"
    added: "list[Line]"


def _picked_days(
    account: Account,
    ctx: "balance_at.BalanceContext",
    listing: "outstanding_difference.DisagreementList",
    parsed: "statement_import.ParsedStatement",
    fresh: "list[Line]",
) -> "list[_Picked]":
    """Return the books days the import leaves apart, each with its drill-down.

    The list's DISAGREES days, with the list's own detail; the books days no
    import covers yet, which the import will; and a covered day the export
    adds a line to.  A day not on the list is kept only when the import
    leaves it apart, since a day that agrees has nothing to answer.
    """
    first, last = listing.books.first_day, listing.books.last_day
    listed = {
        day.comparison.day: day
        for stretch in listing.stretches
        if stretch.kind is StretchKind.DISAGREES
        for day in stretch.days
    }
    uncovered = set(_uncovered_days(listing, parsed))
    fresh_on: "dict[date, list[Line]]" = defaultdict(list)
    for line in fresh:
        if first <= line.posted_on <= last:
            fresh_on[line.posted_on].append(line)
    picked = []
    for day in sorted(set(listed) | uncovered | set(fresh_on)):
        if day in listed:
            detail = listed[day].detail
            residue = listed[day].comparison.residue
        else:
            detail = bank_agreement.day_detail(account, ctx, day)
            # Before the import a day no statement covers holds the app's rows
            # alone; a covered day off the list agrees.
            residue = (
                sum((row.amount for row in detail.rows), ZERO)
                if day in uncovered else ZERO
            )
        residue -= sum((line.amount for line in fresh_on[day]), ZERO)
        if day in listed or residue != ZERO:
            picked.append(_Picked(day, residue, detail, fresh_on[day]))
    return picked


def classed_days(
    account: Account,
    ctx: "balance_at.BalanceContext",
    listing: "outstanding_difference.DisagreementList",
    parsed: "statement_import.ParsedStatement",
    fresh: "list[Line]",
) -> "list[ClassedDay]":
    """Return every books day the import leaves apart, its movements named.

    The days are :func:`_picked_days`'; a movement's name is its own record,
    read once for every day (:func:`_rows_by_id`, :func:`_lines_by_id`).

    Args:
        account: The account.
        ctx: The read pass's context.
        listing: Its disagreement list.
        parsed: SECU's export.
        fresh: The export's lines the import would add (:func:`fresh_lines`).

    Returns:
        The days, ascending.
    """
    picked = _picked_days(account, ctx, listing, parsed, fresh)
    rows = _rows_by_id([
        row for each in picked for row in each.detail.rows if not row.matched
    ])
    lines = _lines_by_id([
        line.line_id
        for each in picked for line in each.detail.lines if not line.matched
    ])
    return [
        ClassedDay(
            day=each.day, residue=each.residue,
            rows=tuple(
                rows[row.entry_id]
                for row in each.detail.rows if not row.matched
            ),
            lines=tuple(
                lines[line.line_id]
                for line in each.detail.lines if not line.matched
            ) + tuple(each.added),
        )
        for each in picked
    ]


def _rows_by_id(
    day_rows: "list[bank_agreement.DayRow]",
) -> "dict[int, Row]":
    """Return each movement the drill-down named, as the sheet names it.

    **The signed figure is the drill-down's** -- the walk's own ``delta``,
    which is the only signed figure the app states for a movement: an entry's
    ``amount`` is a magnitude, and a transfer's side states none.  Everything
    else is the movement's own record, read once for all of them.
    """
    signed = {row.entry_id: row.amount for row in day_rows}
    entries = (
        db.session.query(TransactionEntry)
        .options(joinedload(TransactionEntry.transaction))
        .filter(TransactionEntry.id.in_(signed))
        .all()
    )
    return {
        entry.id: Row(
            entry_id=entry.id,
            transaction_id=entry.transaction_id,
            transfer_id=entry.expense_transfer_id or entry.income_transfer_id,
            payback_for=(
                None if entry.transaction is None
                else entry.transaction.credit_payback_for_id
            ),
            day=entry.settled_on,
            purchased_on=entry.purchased_on,
            amount=signed[entry.id],
            row=None if entry.transaction is None else entry.transaction.name,
            line=entry.description,
            envelope_line=not entry.covers_settlement,
        )
        for entry in entries
    }


def _lines_by_id(line_ids: "list[int]") -> "dict[int, Line]":
    """Return each recorded line the drill-down named, as the sheet names it."""
    return {
        line.id: Line(
            line_id=line.id, posted_on=line.posted_on,
            transaction_on=line.transaction_on, amount=line.amount,
            description=line.description,
        )
        for line in db.session.query(BankStatementLine).filter(
            BankStatementLine.id.in_(line_ids),
        )
    }
