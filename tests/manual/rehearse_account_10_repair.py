"""Rehearse the account-10 repair through the app's own HTTP doors.

The instrument behind plan step **balance:X-f3c-2b-2c**, whose product is a
REHEARSED runbook rather than a code change: ruling **R-HJ** forbids a
migration writing these money rows, so an owner performs the repair by clicking
through the app, and what a runbook owes before anybody does that on real data
is evidence that the doors it names accept the acts it asks for, in the order
it asks for them.
``docs/audits/balance_architecture/account_10_repair_runbook.md`` is the
runbook; this is what proves it.

**The acts are ruling R-BAL3's** (2026-09-05), which replaced the design this
file performed until plan step X-f3c-2b-2c rewrote it: Checking and account 10
BOTH open their books 2026-03-25 at their banks' own closes, the four bank
lines of 2026-03-26 are RECORDED on 2026-03-26 -- transfer 102 among them, KEPT
and re-dated -- and transfer 1, which points at the archived twin, is the row
dropped.  Two rulings of 2026-10-09 amend how: transfer 1 is SET BACK and
CANCELLED rather than deleted (**R-BAL255**: no page renders a single-transfer
delete), and every Fidelity side is typed its own day from Fidelity's record
(**R-BAL256**).  :func:`_perform` states the order and the two points where the
books boundary forces it.

**No production amount and no payee is written in this file** (ruling
**R-BAL249**, applying **R-BAL132**).  Every figure an act types is DERIVED at
run time: Checking's close from the app's own fold of its imported statement
(:func:`app.services.statement_import.fold_bank_balances`), account 10's from
the Fidelity export, the unrecorded line's amount from its own
``budget.bank_statement_lines`` row.  No act types a transfer's figure; each
is RECONCILED, read off the cash fold's own fact for that side.  The run writes
the typed figures, act by act, to a PERFORMANCE SHEET outside every checkout
(``--sheet``, refused inside one), and that sheet is the page the repair is
performed from.  What this file STATES is
structure: row ids, days, and which row answers which bank line.

**IT WRITES.**  Every other harness in this directory reads.  This one performs
the repair across three accounts, so it refuses any database it was not
explicitly pointed at, refuses the name ``shekel`` outright (what BOTH the
deployed database and the shared dev runtime are called), and refuses any clone
whose pre-repair MARKERS have moved (:func:`_require_unrepaired`).

**It is not the repair and must never become it** (ruling **R-HJ**).  That
ruling rejected "a one-off ``scripts/`` routine driving the services" for the
repair itself: the acts have doors, an owner performs them, and a script that
did it instead would be dead code the moment it ran and would put a second
writer beside every door it drove.

**What is DERIVED and what is STATED, and both are RECONCILED.**  Which app row
answers which bank line is the owner's judgement (rulings **R-HJ** to **R-HM**
and **R-BAL3**), so that map is stated -- as ids and days, nothing else -- and
then checked in BOTH directions before a single write (:func:`_reconcile`):
every stated pairing must hold on the figures, and every bank day and every
settled movement in scope must be answered by exactly one act.  *The first
draft of this file's reconciliation was one-directional, and an adversarial
review broke it with two transfers exchanged that ran to completion with every
arm green; the two-directional census is what closed that, and it is kept.*

**Usage** (from the repository root, against a throwaway clone of production
taken the SAME DAY and at the current alembic head; ``LC_ALL`` is the app's
own requirement)::

    LC_ALL=C.UTF-8 PYTHONPATH=. DATABASE_URL=postgresql://.../shekel_rehearsal \\
        python tests/manual/rehearse_account_10_repair.py \\
        --clone shekel_rehearsal \\
        --bank <the Fidelity history CSV> \\
        --residue <the payroll residue ledger row BAL-467 states> \\
        --sheet <handoff folder>/performance_sheet.md \\
        --snapshot <handoff folder>/snapshot.json

Then score both accounts against their banks::

    ... python tests/manual/measure_cutover_against_bank.py \\
        --account 10 --format fidelity \\
        --bank <the Fidelity history CSV>
    ... python tests/manual/measure_cutover_against_bank.py \\
        --account 1 --bank ~/Downloads/checking/2026_ytd_daily_balances.csv

**``--check`` grades a copy the repair was PERFORMED on, and writes nothing**
(:func:`_check`).  It runs the rehearsal's own post-state verification
(:func:`_verify_post_state`, the one set of arms both modes call) on a copy
opened READ-ONLY, refuses a copy no act has touched, and prints what only a
comparison can grade -- the posted ledger's class totals and the Default Grid
Account -- so the runbook diffs its output on production's performed copy
against its output on the rehearsed one.  It reads the snapshot the rehearsal
wrote of the same dump before its first act (:class:`_Snapshot`), which is
what every row the acts created is told apart by::

    ... python tests/manual/rehearse_account_10_repair.py --check \\
        --clone shekel_performed \\
        --bank <the Fidelity history CSV> \\
        --residue <the payroll residue ledger row BAL-467 states> \\
        --snapshot <handoff folder>/snapshot.json
"""

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

from app import create_app, ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db, login_manager
from app.models.account import Account, AccountAnchorHistory
from app.models.account_opening import AccountOpening
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.models.user import UserSettings
from app.services import cash_ledger, statement_import
from app.services.balance_at import BalanceContext, balance_at, cash_balance_at
from app.services.row_valuation import settled_figure
from app.services.settle_day import SettleDay, is_evidence, recorded_settle_day
from app.utils.archive_helpers import category_has_usage
from app.utils.balance_predicates import is_cancelled
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from app.utils.session_helpers import login_session_stamps

_ZERO_MONEY = Decimal("0.00")
_CENT = Decimal("0.01")

#: The day BOTH accounts' books open (ruling **R-BAL3**).  A DECISION -- no
#: bank record says which day the books should start on -- so it is stated
#: here, and each account's opening EQUITY is derived from it: its bank's own
#: close for this day.
BOOKS_OPEN = date(2026, 3, 25)

#: The first day the restated books can hold a movement (ruling **R-HG**: an
#: opening is the CLOSE of its own day), and the day both banks posted the four
#: lines this repair records on Checking.
BOUNDARY_DAY = date(2026, 3, 26)

#: The three accounts the repair touches.  The twin is the archived
#: predecessor of account 10 (ruling **R-HK**: one real Fidelity account).
SUBJECT_ACCOUNT = 10
CHECKING_ACCOUNT = 1
TWIN_ACCOUNT = 2

#: The day the twin's own balance is superseded to zero (ruling **R-HK**).
TWIN_ASSERTED_ON = date(2026, 4, 6)

#: The day the twin's books open, which act 2 KEEPS while it zeroes the
#: equity.  STATED rather than read at act 2, because once act 2 has written
#: its row a performed copy no longer says which day that row superseded, and
#: a twin restated onto another day moves no balance any comparison sees
#: (measured by adversarial review 2026-10-10).  Reconciled against the
#: governing opening before any write (:func:`_reconcile`).
TWIN_OPENED_ON = date(2026, 3, 26)

#: Ruling **R-BAL3**: ONE real ACH left Checking and reached Fidelity on
#: BOUNDARY_DAY, and the app records it twice.  Transfer 102 (Checking ->
#: account 10) is KEPT and re-dated onto that day, inverting finding **N-382**;
#: transfer 1 (Checking -> the archived twin) is DROPPED -- set back to
#: Projected and CANCELLED (ruling **R-BAL255**): no page renders the
#: single-transfer delete, and Cancelled says what is true, that the planned
#: transfer to the old account did not happen.  Not being a soft delete, it
#: does not incur finding **N-386**'s restore exposure.
KEPT_TRANSFER = 102
DROPPED_TRANSFER = 1


@dataclass(frozen=True)
class _BoundaryLine:
    """One of Checking's bank lines on BOUNDARY_DAY, and what answers it.

    Attributes:
        line_id: The ``budget.bank_statement_lines`` row.
        rows: Checking transactions re-dated onto the line's day to answer it.
        transfer_id: A transfer whose CHECKING side answers it instead.
        residue: Whether the answer is KNOWN not to sum to the line.  Only
            finding **BAL-467**'s payroll line: two rows answer one line and
            fall short of it, and which row is short is the owner's
            knowledge, so the residue is measured and stated rather than
            split.
    """

    line_id: int
    rows: "tuple[int, ...]" = ()
    transfer_id: "int | None" = None
    residue: bool = False


#: Checking's four bank lines on BOUNDARY_DAY and what answers each (ruling
#: **R-BAL3**).  The line answered by NOTHING is :data:`UNRECORDED_LINE`.
BOUNDARY_LINES = (
    _BoundaryLine(131, rows=(781, 865), residue=True),
    _BoundaryLine(132, rows=(1069,)),
    _BoundaryLine(134, transfer_id=KEPT_TRANSFER),
)

#: Every Checking row :data:`BOUNDARY_LINES` re-dates: the one set the
#: reconciliation's census, act 7's search and Checking's boundary arm read.
MAPPED_ROWS = frozenset(row for entry in BOUNDARY_LINES for row in entry.rows)

#: The one line whose answer is KNOWN to fall short of it (BAL-467).
_RESIDUE_LINE = next(entry for entry in BOUNDARY_LINES if entry.residue)

#: The line the app records nowhere (finding **BAL-468**), recorded by act 7
#: as a new Checking expense in the category the developer ruled (2026-09-06,
#: ``Family: Birthday``, id 26), under the name the developer gave it.  Its AMOUNT is the
#: line's own, read from the row at run time.
UNRECORDED_LINE = 133
UNRECORDED_CATEGORY = 26
UNRECORDED_NAME = "Josh's Birthday"

#: Which transfer answers which day of the Fidelity export, on account 10's
#: side -- the day typed into that side's own box (ruling **R-BAL256**).  Days
#: only (ruling **R-BAL249**): each side's figure is the cash fold's own fact,
#: reconciled against what the export moved that day.
FIDELITY_DAYS = {
    KEPT_TRANSFER: BOUNDARY_DAY,
    155: date(2026, 4, 9),
    156: date(2026, 4, 23),
    154: date(2026, 4, 29),
    157: date(2026, 5, 7),
    346: date(2026, 5, 14),
    409: date(2026, 7, 23),
}

#: The category the recorded dividends book to (ruling **R-HL**), and the
#: category that BECOMES it: ruling **R-BAL250** has act 9 rename the owner's
#: unused ``Financial: Dividend`` (id 33) rather than create a second one.
DIVIDEND_CATEGORY = ("Income", "Interest & Dividends")
DIVIDEND_CATEGORY_ID = 33
DIVIDEND_CATEGORY_WAS = ("Financial", "Dividend")

#: What each recorded dividend is called.  The one door the owner can reach to
#: create a row in an EMPTY category -- the grid's Add Transaction modal, since
#: the grid draws rows only from existing transactions -- requires a name, and
#: one name for all five keeps them on one grid row.
DIVIDEND_NAME = "Dividend"

#: The SETTLED status each transaction type takes.  Income settles as
#: Received and an expense as Paid; submitting the other one is refused by the
#: status seam, which is how the first draft found out.  Members rather than
#: ids: the ids are the reference cache's (:func:`_settled_status_id`).
_SETTLED_STATUS = {
    TxnTypeEnum.INCOME: StatusEnum.RECEIVED,
    TxnTypeEnum.EXPENSE: StatusEnum.DONE,
}

#: The Fidelity history columns this file reads, by their own header text.
_BANK_DAY = "Run Date"
_BANK_BALANCE = "Cash Balance ($)"
_BANK_ACTION = "Action"
_BANK_AMOUNT = "Amount ($)"

#: What a received dividend's ``Action`` says.  The REINVESTMENT line beside it
#: is the same money buying the core position back and is not a second event.
_DIVIDEND_RX = re.compile(r"\bDIVIDEND RECEIVED\b", re.IGNORECASE)

#: The flash categories that mean a door REFUSED.  Read after every submission,
#: because two of this repair's doors answer a refusal with ``302`` and a flash
#: rather than with a designed fragment and a 4xx -- ``accounts.restate_opening``
#: is one, and it is the door this whole step exists for.
_REFUSAL_FLASHES = frozenset({"danger", "error", "warning"})


@dataclass(frozen=True)
class _Export:
    """The bank's own record: what it closed at, and what it says moved.

    Attributes:
        closings: ``{day: closing balance}``, one per day the file names.
        dividends: ``{day: amount}`` -- the SUM of that day's
            ``DIVIDEND RECEIVED`` lines, because a day may carry more than one
            and overwriting would drop money.
        named: The days, ascending.  Materialised once: :meth:`moved_on` is
            called per movement and per bank day, and re-sorting the dict on
            every call is this project's DRY violation rather than a cost.
    """

    closings: "dict[date, Decimal]"
    dividends: "dict[date, Decimal]"
    named: "list[date]" = field(default_factory=list)

    @classmethod
    def read(cls, path: str) -> "_Export":
        """Parse a Fidelity transaction-history export.

        The file is BOM'd, carries blank preamble above its header and a
        disclaimer below its rows, and states a running ``Cash Balance ($)``
        per LINE rather than per day.  The header is found by its own column
        names and the columns read by name, so a column added upstream cannot
        silently shift the figures.

        Args:
            path: The CSV file.

        Returns:
            The :class:`_Export`.

        Raises:
            SystemExit: When the file names no day, or its rows are not in date
                order in either direction.
        """
        rows: "list[tuple[date, Decimal, str, str]]" = []
        columns: "dict[str, int] | None" = None
        wanted = (_BANK_DAY, _BANK_BALANCE, _BANK_ACTION, _BANK_AMOUNT)
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.reader(handle):
                cells = [cell.strip() for cell in row]
                if columns is None:
                    if all(name in cells for name in wanted):
                        columns = {name: cells.index(name) for name in wanted}
                    continue
                if len(cells) <= max(columns.values()):
                    continue
                day = _parse_day(cells[columns[_BANK_DAY]])
                if day is None or not cells[columns[_BANK_BALANCE]]:
                    continue
                rows.append((
                    day,
                    Decimal(cells[columns[_BANK_BALANCE]]).quantize(_CENT),
                    cells[columns[_BANK_ACTION]],
                    cells[columns[_BANK_AMOUNT]],
                ))
        if not rows:
            raise SystemExit(f"{path} states no daily balance")
        return cls._assemble(path, rows)

    @classmethod
    def _assemble(cls, path: str, rows) -> "_Export":
        """Fold parsed rows into closings and dividends.

        **A day's CLOSING is its chronologically LAST line, and the file's own
        ordering decides which that is.**  An earlier draft required every line
        on a day to state the same balance, which is true of this export only
        because its multi-line days are dividend/reinvestment pairs that net to
        zero on the reported column; an ordinary day with two transfers states
        one closing and one intra-day balance, and that draft aborted on it.
        The direction is MEASURED from the date sequence rather than assumed,
        and a file sorted neither way is refused -- because then no rule picks
        the closing and guessing would be worse than stopping.

        Args:
            path: The file, named in any refusal.
            rows: ``(day, closing, action, amount)`` in FILE order.

        Returns:
            The :class:`_Export`.

        Raises:
            SystemExit: When the rows are not in date order either way.
        """
        days = [row[0] for row in rows]
        ascending = all(a <= b for a, b in zip(days, days[1:]))
        descending = all(a >= b for a, b in zip(days, days[1:]))
        if not ascending and not descending:
            raise SystemExit(
                f"{path} is not in date order in either direction, so no rule "
                "picks a day's CLOSING line out of its intra-day ones"
            )
        chronological = rows if ascending else list(reversed(rows))
        closings: "dict[date, Decimal]" = {}
        dividends: "dict[date, Decimal]" = {}
        for day, closing, action, amount in chronological:
            # Last write wins, and after the reversal above the last write for
            # a day is that day's final line -- its closing balance.
            closings[day] = closing
            if _DIVIDEND_RX.search(action) and amount:
                dividends[day] = dividends.get(day, _ZERO_MONEY) + Decimal(
                    amount
                ).quantize(_CENT)
        return cls(
            closings=closings, dividends=dividends, named=sorted(closings),
        )

    def moved_on(self, day: date) -> Decimal:
        """Return what the bank says moved on *day*, dividends excluded.

        Args:
            day: A day the file names.

        Returns:
            The day's closing less the previous named day's, less any dividend
            the same day credited -- so what is left is the transfer the app
            has a row for.
        """
        prior = [named for named in self.named if named < day]
        opening = self.closings[prior[-1]] if prior else _ZERO_MONEY
        return (self.closings[day] - opening
                - self.dividends.get(day, _ZERO_MONEY))

    def closing_on(self, day: date) -> Decimal:
        """Return the bank's closing balance FOR *day*, carried forward.

        A quiet day is absent from the file, so its closing is the last named
        day's.  BOOKS_OPEN is such a day on this export: nothing moved between
        the line before it and the next one, so the opening equity is that
        earlier line's balance carried forward (ruling **R-BAL3**).

        Args:
            day: The civil day to answer.

        Returns:
            The closing balance.

        Raises:
            SystemExit: When *day* precedes every day the file names.
        """
        prior = [named for named in self.named if named <= day]
        if not prior:
            raise SystemExit(f"the export names no day on or before {day}")
        return self.closings[prior[-1]]

    def recorded_dividends(self) -> "list[tuple[date, Decimal]]":
        """Return the dividends this repair records, ascending.

        Returns:
            One ``(day, amount)`` per ``DIVIDEND RECEIVED`` day dated strictly
            after the books open.  The ones on or before it are inside the
            opening equity (ruling **R-HG**) and are not recorded.
        """
        return [
            (day, self.dividends[day])
            for day in sorted(self.dividends) if day > BOOKS_OPEN
        ]


def _parse_day(raw: str) -> "date | None":
    """Return *raw* as a civil day, or ``None`` when it is not one.

    Args:
        raw: A cell from the export's date column.

    Returns:
        The date, or ``None`` for preamble and disclaimer rows.
    """
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


class _Forms(HTMLParser):
    """Collect every form a response renders: method, url and its own controls.

    **The payload comes from the page, not from this file.**  An HTML form
    submits every control it renders, so a rehearsal that posts a chosen subset
    is not rehearsing the owner's click -- a hand-picked payload once shipped a
    route arm that was dead in a browser.  Each act that submits a FORM fetches
    the form the owner opens, changes only what they type, and submits the rest
    exactly as rendered, including the version pin, the hidden ids and the
    selects' own current values.  The three presses that are buttons rather
    than forms -- Cancel transfer, Unarchive and Archive -- post what their
    buttons carry (Cancel the leg's id, the other two nothing but the CSRF
    token this harness disables), and Cancel only after its button is found
    on the reopened card.

    **Seven divergences from a browser have been measured and fixed** over
    two review rounds, each of which had this parser posting something no
    browser would.  **Three of the seven cannot be reached on the forms this
    file drives** -- the ``<textarea>`` and the two ``<select multiple>``
    cases: none of the eight forms it drives renders either (re-censused
    2026-10-09, when the creates moved to the grid's Add Transaction modal and
    the grid's account to Settings > General).  They are fixed anyway because
    the parser is the thing that makes "the payload comes from the page" true,
    and a parser correct only on today's pages is a claim about the pages
    rather than about the parser.

    * a CHECKED checkbox followed by a hidden partner of the same name -- the
      shape ``grid/_transaction_full_edit.html`` uses for its flags -- came out
      ``false``, because the controls were folded into a dict and the LAST
      value won.  Werkzeug's ``MultiDict`` gives ``request.form[key]`` the
      FIRST, so :func:`_payload` keeps the first and the flag survives;
    * a ``<textarea>`` posted empty whatever it held, because its content was
      never read;
    * a ``<select multiple>`` posted only its last selected option;
    * a ``<select>`` with no ``selected`` fell back to its first option even
      when that option was ``disabled``, which the reset algorithm skips;
    * a ``<select multiple>`` with NOTHING selected posted its first option,
      where a browser posts no value at all;
    * a ``<option selected disabled>`` -- the placeholder idiom
      ``analytics/_income_statement.html`` uses -- was dropped and the next
      option posted in its place, though the markup's own selectedness makes it
      the submitted one;
    * a checked checkbox with no ``value`` posted the empty string rather than
      ``on``.

    Disabled controls are dropped because a browser drops them, which is what
    makes a finalised transfer's locked amount absent from the submission
    rather than re-posted.
    """

    _NOT_A_VALUE = {"submit", "button", "image", "reset"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: "list[dict]" = []
        self._form: "dict | None" = None
        self._select: "dict | None" = None
        self._textarea: "str | None" = None
        self._text: "list[str]" = []

    def handle_starttag(self, tag, attrs):
        """Open a form, or record one control's submitted value."""
        attr = dict(attrs)
        if tag == "form":
            self._open_form(attr)
        elif self._form is None:
            return
        elif tag == "input":
            self._input(attr)
        elif tag == "select":
            self._select = (
                None if "disabled" in attr or not attr.get("name")
                else {
                    "name": attr["name"], "multiple": "multiple" in attr,
                    "selected": [], "first": None,
                }
            )
        elif tag == "option" and self._select is not None:
            self._option(attr)
        elif tag == "textarea" and attr.get("name") and "disabled" not in attr:
            self._textarea = attr["name"]
            self._text = []

    def _open_form(self, attr):
        """Start a form, taking its method and action from HTMX or plain HTML.

        Args:
            attr: The tag's attributes.
        """
        self._form = {
            "method": "post", "url": attr.get("action", ""),
            "controls": [], "multiple": set(),
        }
        for verb in ("patch", "post", "put", "delete", "get"):
            if f"hx-{verb}" in attr:
                self._form["method"] = verb
                self._form["url"] = attr[f"hx-{verb}"]
                break
        self.forms.append(self._form)

    def _input(self, attr):
        """Record one ``<input>``'s submitted value, or drop it.

        Args:
            attr: The tag's attributes.
        """
        if "disabled" in attr or attr.get("type") in self._NOT_A_VALUE:
            return
        if not attr.get("name"):
            return
        ticked = attr.get("type") in ("checkbox", "radio")
        if ticked and "checked" not in attr:
            return
        # A ticked control with no ``value`` submits the string ``on``; every
        # other control with no value submits the empty string.
        default = "on" if ticked else ""
        self._form["controls"].append((attr["name"], attr.get("value", default)))

    def _option(self, attr):
        """Record one ``<option>``'s value against the open select.

        Args:
            attr: The tag's attributes.
        """
        value = attr.get("value", "")
        # **A DISABLED option is still submitted when the markup marks it
        # selected**, and only the no-selection FALLBACK skips it: the reset
        # algorithm gives selectedness to the first option that is not
        # disabled, but an option that already carries ``selected`` keeps it.
        # ``analytics/_income_statement.html`` uses exactly that placeholder
        # idiom, and an earlier version dropped such an option and posted the
        # next one instead (adversarial review, 2026-09-01).
        if "disabled" not in attr and self._select["first"] is None:
            self._select["first"] = value
        if "selected" in attr:
            self._select["selected"].append(value)

    def handle_data(self, data):
        """Accumulate an open textarea's content."""
        if self._textarea is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        """Close a form, a select or a textarea, committing its value."""
        if tag == "form":
            self._form = None
        elif tag == "select" and self._select is not None:
            self._commit_select()
        elif tag == "textarea" and self._textarea is not None:
            if self._form is not None:
                self._form["controls"].append(
                    (self._textarea, "".join(self._text)),
                )
            self._textarea = None

    def _commit_select(self):
        """Append the open select's submitted value or values."""
        if self._form is not None:
            chosen = self._select["selected"]
            if self._select["multiple"]:
                # A MULTI-select with nothing selected submits NOTHING.  The
                # first-option fallback belongs to a single select only, and
                # applying it here posted a value no browser would.
                self._form["multiple"].add(self._select["name"])
            else:
                if not chosen and self._select["first"] is not None:
                    chosen = [self._select["first"]]
                chosen = chosen[:1]
            for value in chosen:
                self._form["controls"].append((self._select["name"], value))
        self._select = None


def _payload(form: dict) -> dict:
    """Return *form*'s controls as the payload a browser would submit.

    **The FIRST value of a repeated name wins**, which is what werkzeug's
    ``MultiDict`` gives ``request.form[key]`` and therefore what every schema
    in this app reads.  A genuinely multi-valued control -- a
    ``<select multiple>`` -- keeps every value as a list, which the test client
    posts as repeated fields.

    Args:
        form: One parsed form.

    Returns:
        ``{name: value}``, with a list for each multi-select.
    """
    payload: dict = {}
    for name, value in form["controls"]:
        if name in form["multiple"]:
            payload.setdefault(name, []).append(value)
        elif name not in payload:
            payload[name] = value
    return payload


def _forms_in(html: str) -> "list[dict]":
    """Return every form in *html*.

    Args:
        html: A rendered page or HTMX fragment.

    Returns:
        One form dict per form, in document order.
    """
    parser = _Forms()
    parser.feed(html)
    return parser.forms


class RefusedError(AssertionError):
    """A door refused a submission this rehearsal expected it to accept."""


class _Operator:
    """One owner's browser session against one clone."""

    def __init__(self, app, sheet: "_Sheet"):
        """Forge the owner's session and open a client.

        Args:
            app: The Flask application, already configured for a rehearsal.
            sheet: The performance sheet every act records into.
        """
        self.sheet = sheet
        self.client = app.test_client()
        self.user_id = db.session.get(Account, SUBJECT_ACCOUNT).user_id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
            session["_id"] = None
            # The stamps a sign-in writes, the user's among them: ``load_user``
            # refuses a session without them (plan#2173).
            session.update(login_session_stamps(
                datetime.now(timezone.utc), self.user_id,
            ))
        self.acts = 0

    def _drain_flashes(self) -> "list[tuple[str, str]]":
        """Return and clear whatever the last request flashed.

        Returns:
            ``[(category, message), ...]``, empty when nothing flashed.
        """
        with self.client.session_transaction() as session:
            return list(session.pop("_flashes", []))

    def form(self, url: str) -> dict:
        """Open *url* and return its first form.

        Args:
            url: The page or fragment the owner opens.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or renders no form.
        """
        response = self.client.get(url, headers={"HX-Request": "true"})
        assert response.status_code == 200, \
            f"GET {url} -> {response.status_code}"
        forms = _forms_in(response.get_data(as_text=True))
        assert forms, f"GET {url} rendered no form"
        return forms[0]

    def form_posting_to(self, page: str, action: str) -> dict:
        """Return the form on *page* whose action ends with *action*.

        Args:
            page: The full page the owner opens.
            action: The tail of the door's URL.

        Returns:
            The form dict.

        Raises:
            AssertionError: When the page does not answer, or carries no such
                form -- which is the card being unreachable, not a payload
                problem.
        """
        response = self.client.get(page)
        assert response.status_code == 200, \
            f"GET {page} -> {response.status_code}"
        for form in _forms_in(response.get_data(as_text=True)):
            if form["url"].endswith(action):
                return form
        raise AssertionError(f"{page} renders no form posting to {action}")

    def submit(self, form: dict, typed: "dict | None" = None):
        """Submit *form* with the owner's edits applied.

        Args:
            form: A form from :meth:`form` or :meth:`form_posting_to`.
            typed: What the owner changed, or supplied where a control is
                driven by script rather than rendered carrying its value.

        Returns:
            The response.
        """
        payload = _payload(form)
        payload.update(typed or {})
        return self.send(form["method"], form["url"], payload)

    def send(self, method: str, url: str, payload: dict):
        """Submit *payload* and assert the door accepted it.

        **A 302 with a danger flash is a REFUSAL, and reading only the status
        missed it.**  ``accounts.restate_opening`` -- the door this whole step
        exists for -- answers an illegal day by flashing and redirecting, so a
        status check alone reported success on a write that never happened
        (adversarial review, 2026-09-01).  The flash is drained after every
        submission and any refusal category raises, which also surfaces the
        refusal SENTENCE -- which is what a rehearsal is for.

        Args:
            method: The form's own method.
            url: The form's own action.
            payload: What the browser would submit.

        Returns:
            The response.

        Raises:
            RefusedError: On a refusing status, or on a refusal flash.
        """
        self.acts += 1
        response = getattr(self.client, method)(
            url, data=payload, headers={"HX-Request": "true"},
        )
        refused = [
            message for category, message in self._drain_flashes()
            if category in _REFUSAL_FLASHES
        ]
        # **A designed error fragment can carry a 2xx, and the status list
        # below would accept it.**  Every refusal this repair's doors produce
        # today answers 4xx or flashes, audited 2026-09-01 -- but that is a
        # convention, and the app stamps a header saying so precisely because
        # the client cannot tell a handled error from a crash page otherwise.
        # Asking the header is one line and does not rest on the audit staying
        # true.
        if DESIGNED_FRAGMENT_HEADER in response.headers:
            refused.append(
                f"a designed error fragment ({DESIGNED_FRAGMENT_HEADER})"
            )
        if response.status_code not in (200, 201, 302):
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code}\n"
                f"payload={payload}\n"
                f"{response.get_data(as_text=True)[:800]}"
            )
        if refused:
            raise RefusedError(
                f"{method.upper()} {url} -> {response.status_code} but the "
                f"app refused it: {' | '.join(refused)}"
            )
        return response


class _Sheet:
    """The PERFORMANCE SHEET: every act in order, what it types, what follows.

    **The page the repair is performed from, and the only place its figures
    are written** (ruling **R-BAL249**): the committed runbook names each
    amount by its role, and this run derives the amount and writes it here,
    beside the state the app reaches after the act.  Every line is also
    printed, so the console and the sheet cannot disagree.  It is saved even
    when the run stops early, so a refusal is recorded beside the acts that
    landed before it.
    """

    def __init__(self, path: "Path | None"):
        """Hold the sheet's destination.

        Args:
            path: Where to write it; outside every checkout (:func:`_new_file`).
                ``None`` for ``--check``, which prints and saves nothing.
        """
        self.path = path
        self.lines: "list[str]" = []

    def say(self, line: str = "") -> None:
        """Print *line* and keep it for the sheet.

        Args:
            line: One line of the record.
        """
        print(line)
        self.lines.append(line)

    def act(self, number: int, title: str, door: str) -> None:
        """Open one act's section.

        Args:
            number: The act's place in the order.
            title: What it does, in the runbook's words.
            door: Where the operator performs it.
        """
        self.say("")
        self.say(f"## Act {number}: {title}")
        self.say(f"Where: {door}")

    def typed(self, what: str, value: object) -> None:
        """Record one value the operator types.

        Args:
            what: The box, as the screen labels it.
            value: What goes in it.
        """
        self.say(f"- TYPE {what}: **{value}**")

    def save(self) -> None:
        """Write the sheet."""
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _new_file(raw: str, flag: str) -> Path:
    """Return where a file the rehearsal writes goes, refusing any git checkout.

    **Any checkout, not only this one**: the harness is run from worktrees, and
    a sheet written into the main checkout or a sibling worktree is as much one
    ``git add`` from a commit as one written beside this file.  The folder is
    also required to EXIST now, because the sheet is written last and a bad
    path found then would lose the record of every act already performed.
    **And the file must NOT exist**: the sheet is saved even when a run stops,
    so a re-run naming an earlier run's sheet would replace the page a repair
    is performed from with a STOPPED one (adversarial review, 2026-10-09).
    The snapshot (:class:`_Snapshot`) is held to the same three rules: it is
    a record of a production copy, and a run never overwrites an earlier
    run's.  Which snapshot a check is handed is the operator's typing; what
    the census does with one that is not of the copy's dump is
    :func:`_performance_span`'s.

    Args:
        raw: The argument's value.
        flag: The argument, ``--sheet`` or ``--snapshot``, for the refusals.

    Returns:
        The resolved path.

    Raises:
        SystemExit: When the path is inside a git checkout, where what a run
            records of a production copy does not belong (ruling
            **R-BAL249**), its folder does not exist, or a file is already
            there.
    """
    path = Path(raw).expanduser().resolve()
    checkouts = [
        folder for folder in (path, *path.parents) if (folder / ".git").exists()
    ]
    if checkouts:
        raise SystemExit(
            f"{flag} {path} is inside the git checkout {checkouts[0]}; what a "
            "run records of a production copy belongs in the handoff folder "
            "(ruling R-BAL249)"
        )
    if path.is_dir() or not path.parent.is_dir():
        raise SystemExit(
            f"{flag} {path} is a folder, or its folder does not exist; it "
            "names a FILE"
        )
    if path.exists():
        raise SystemExit(
            f"{flag} {path} already exists; every run writes its own, and an "
            "earlier one is never overwritten"
        )
    return path


def _repair_markers() -> "list[str]":
    """Return every pre-repair MARKER that has moved on this copy.

    **What these catch is a PARTIALLY repaired copy**, because nothing in the
    acts is idempotent and refusing at act zero is the only safe answer: act 1
    trips the transfer marker (and every act after it runs after act 1), acts
    3 and 4 trip the opening markers, and act 9 the category markers.  These
    are MARKERS, not a proof of the whole pre-repair state -- act 0 trips none
    of them and is not idempotent.  One list read both ways:
    :func:`_require_unrepaired` refuses a copy on which ANY has moved, and
    :func:`_require_repaired` one on which NONE has.

    Returns:
        One sentence per moved marker, empty on an unrepaired copy.
    """
    problems = []
    for account_id in (CHECKING_ACCOUNT, SUBJECT_ACCOUNT):
        opening = cash_ledger.governing_account_opening(account_id)
        if opening.opened_on == BOOKS_OPEN:
            problems.append(
                f"account {account_id}'s books already open {BOOKS_OPEN}"
            )
    for transfer_id in (DROPPED_TRANSFER, KEPT_TRANSFER):
        transfer = db.session.get(Transfer, transfer_id)
        if transfer is None or transfer.is_deleted:
            problems.append(f"transfer {transfer_id} is missing or deleted")
        elif not transfer.status.is_settled:
            problems.append(
                f"transfer {transfer_id} is not settled (already set back or "
                "cancelled?)"
            )
    user_id = db.session.get(Account, SUBJECT_ACCOUNT).user_id
    existing = db.session.execute(
        db.select(Category.id).filter_by(
            user_id=user_id, group_name=DIVIDEND_CATEGORY[0],
            item_name=DIVIDEND_CATEGORY[1],
        )
    ).scalar_one_or_none()
    if existing is not None:
        problems.append(
            f"category {DIVIDEND_CATEGORY[0]}: {DIVIDEND_CATEGORY[1]} "
            f"already exists (id {existing})"
        )
    # The category act 9 renames must be the owner's, still be the one ruling
    # R-BAL250 was asked about, and hold NOTHING -- asked of the app's own
    # in-use rule, because a rename relabels every template, transfer,
    # transaction and merchant rule filed under it.
    renamed = db.session.get(Category, DIVIDEND_CATEGORY_ID)
    if (
        renamed is None or renamed.user_id != user_id
        or (renamed.group_name, renamed.item_name) != DIVIDEND_CATEGORY_WAS
        or category_has_usage(DIVIDEND_CATEGORY_ID, user_id)
    ):
        problems.append(
            f"category {DIVIDEND_CATEGORY_ID} is not the owner's empty "
            f"{DIVIDEND_CATEGORY_WAS[0]}: {DIVIDEND_CATEGORY_WAS[1]}"
        )
    return problems


def _require_unrepaired() -> None:
    """Refuse a clone the repair has already been performed on.

    It doubles as the safety rail a name check cannot be: a database this
    repair has touched is refused whatever it is called.

    Raises:
        SystemExit: When any marker of :func:`_repair_markers` has moved,
            naming every one rather than the first.
    """
    problems = _repair_markers()
    if problems:
        raise SystemExit(
            "this clone is not in the pre-repair state:\n  "
            + "\n  ".join(problems)
            + "\nRestore it from an unrepaired snapshot and run again."
        )
    print("precondition: the clone is unrepaired")


def _require_repaired() -> None:
    """Refuse a copy no act of the repair has touched (``--check``).

    **An unrepaired copy is refused rather than graded**, because grading it
    fails every arm and names none of the cause: a copy of production taken
    BEFORE the acts, or the rehearsal's untouched copy, is the likeliest
    wrong target, and this says so.  A PARTIALLY repaired copy passes here
    and fails the arms its missing acts own, which is the check's job.

    Raises:
        SystemExit: When no marker of :func:`_repair_markers` has moved.
    """
    moved = _repair_markers()
    if not moved:
        raise SystemExit(
            "this copy is UNREPAIRED: no marker of the repair has moved. "
            "--check grades a copy taken AFTER the acts; was the dump taken "
            "before them, or is this the copy no act touches?"
        )
    print("precondition: the repair's markers have moved on this copy:")
    for marker in moved:
        print(f"  {marker}")


class _Snapshot(NamedTuple):
    """Every row and paycheck id a copy held before the first act.

    **What the acts CREATED is the difference against this, and nothing an
    operator types decides it** (:func:`_grade_created_rows`).  The rehearsal
    takes it of its own copy before act 1 -- a copy restored from the dump and
    touched by nothing yet, so it IS the dump's -- and writes it beside the
    sheet (``--snapshot``), and ``--check`` reads that file, so a performed
    copy of the same dump is measured against the same ids.  **A file, not
    the untouched copy ``$B``**: the runbook renders every page against
    ``$B``, and on a dump taken before the owner's first grid or dashboard
    load after a payday that render tops up the pay-period window and
    creates rows there (measured 2026-10-10: a paycheck and its rows),
    so ``$B`` is the dump's state only on days it happens to be.

    **Nothing in the file says which dump it is of, so the census does not
    trust it to be this copy's**: it carries the DATABASE's clock at capture,
    and :func:`_grade_created_rows` names a row with a remedy only when the
    row was created inside the performance that clock and the acts' own
    appended rows bound (:func:`_performance_span`).  A snapshot of an
    earlier dump leaves the owner's newer rows outside it, and the census
    then stops rather than prescribing a Delete for one of them (adversarial
    review 2026-10-10: it told the operator to delete the owner's newest row).

    Attributes:
        transactions: Every ``budget.transactions`` id, deleted rows too.
        pay_periods: Every ``budget.pay_periods`` id.
        deleted: The ``transactions`` already soft-deleted, so the rest are
            the rows the copy held LIVE, which no act deletes
            (:func:`_grade_deleted_rows`).
        captured_at: The database's ``now()`` when it was taken.
    """

    transactions: frozenset
    pay_periods: frozenset
    deleted: frozenset
    captured_at: datetime

    @classmethod
    def capture(cls) -> "_Snapshot":
        """Read this copy's ids now.

        Returns:
            The snapshot.
        """
        return cls(
            frozenset(db.session.execute(db.select(Transaction.id)).scalars()),
            frozenset(db.session.execute(db.select(PayPeriod.id)).scalars()),
            frozenset(db.session.execute(
                db.select(Transaction.id).filter(Transaction.is_deleted.is_(True))
            ).scalars()),
            db.session.execute(db.select(db.func.now())).scalar_one(),
        )

    @classmethod
    def read(cls, path: str) -> "_Snapshot":
        """Read the snapshot a rehearsal wrote.

        Args:
            path: The ``--snapshot`` file.

        Returns:
            The snapshot.

        Raises:
            SystemExit: When the file lacks a field this file writes: it was
                written by an older version of this file, whose snapshot
                cannot bound the acts (:func:`_performance_span`).
        """
        held = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        absent = sorted(
            {"transactions", "pay_periods", "deleted", "captured_at"} - set(held)
        )
        if absent:
            raise SystemExit(
                f"--snapshot {path} carries no {', '.join(absent)}: it was "
                "written by an older version of this file; rehearse again from "
                "this checkout and check against the snapshot that writes"
            )
        return cls(
            frozenset(held["transactions"]), frozenset(held["pay_periods"]),
            frozenset(held["deleted"]),
            datetime.fromisoformat(held["captured_at"]),
        )

    def write(self, path: Path) -> None:
        """Write it for ``--check`` to read.

        Args:
            path: Where, from :func:`_new_file`.
        """
        path.write_text(json.dumps({
            "transactions": sorted(self.transactions),
            "pay_periods": sorted(self.pay_periods),
            "deleted": sorted(self.deleted),
            "captured_at": self.captured_at.isoformat(),
        }) + "\n", encoding="utf-8")


def _facts(account_id: int) -> list:
    """Return the cash fold's own settled movements for one account.

    **The figures this repair reconciles and verifies are the FOLD'S**, read
    off :func:`app.services.cash_ledger.walk_cash_ledger` -- the walk every
    balance is folded from (ruling **R-BAL80**: the fold reads movements and no
    row) -- rather than off a transfer's stored amount or a row's columns,
    either of which is a second statement of what moved.

    Args:
        account_id: The account.

    Returns:
        Its :class:`~app.services.cash_ledger.CashSourceFact` values.
    """
    db.session.expire_all()
    owner = db.session.get(Account, account_id).user_id
    scenario = db.session.execute(
        db.select(Scenario.id).filter_by(user_id=owner, is_baseline=True)
    ).scalar_one()
    return list(
        cash_ledger.walk_cash_ledger(account_id, scenario).source_facts
    )


class _Moved(NamedTuple):
    """What one row or one transfer side moved, summed from its facts.

    Attributes:
        delta: The signed total.
        day: The day of its LAST movement -- meaningful only when
            :attr:`movements` is one, which every row in this repair's scope is
            asserted to be (:func:`_checking_problems`).
        movements: How many movements it holds.
    """

    delta: Decimal
    day: date
    movements: int


def _moved_by(facts: list) -> "tuple[dict, dict]":
    """Return what each transaction and each transfer moved, from *facts*.

    Args:
        facts: One account's :func:`_facts`.

    Returns:
        ``({transaction id: _Moved}, {transfer id: _Moved})``.
    """
    rows: dict = {}
    transfers: dict = {}
    for fact in facts:
        for key, into in ((fact.transaction_id, rows), (fact.transfer_id, transfers)):
            if key is None:
                continue
            was = into.get(key, _Moved(_ZERO_MONEY, fact.settled_on, 0))
            into[key] = _Moved(
                was.delta + fact.delta, fact.settled_on, was.movements + 1,
            )
    return rows, transfers


def _side_day(transfer_id: int, account_id: int) -> SettleDay | None:
    """Return the day one transfer side records, and how it is known.

    Read off the side's own movement (:func:`app.services.settle_day
    .recorded_settle_day`), which is what the popover's box for that side
    prefills from and what decides whether it reads as "a guess".

    Args:
        transfer_id: The transfer.
        account_id: Which side.

    Returns:
        The side's :class:`~app.services.settle_day.SettleDay`, or ``None``
        when the fold counts no movement for it.
    """
    entries = [
        fact.entry_id for fact in _facts(account_id)
        if fact.transfer_id == transfer_id
    ]
    if len(entries) != 1:
        return None
    return recorded_settle_day(db.session.get(TransactionEntry, entries[0]))


def _transfer_named(transfer_id: int, account_id: int) -> str:
    """Return how the owner tells one transfer from its namesakes.

    Five of the repair's seven transfers share one name, so the sheet adds
    what one side moved -- the cash fold's own figure for it, the way every
    figure in this file is read -- and the payday of the column it is filed
    in.

    Args:
        transfer_id: The transfer.
        account_id: The side whose movement to quote.

    Returns:
        ``"<name>, <moved> on account <id>, column <payday>"``.
    """
    transfer = db.session.get(Transfer, transfer_id)
    _, moved = _moved_by(_facts(account_id))
    column = _period_holding(account_id, transfer.pay_period.start_date)
    return (f"{transfer.name}, {moved[transfer_id].delta} on account "
            f"{account_id}, column {column.label}")


def _movement_key(fact) -> "tuple[str, int]":
    """Return what one movement IS, for a census: a transfer side or a row.

    A transfer side is named by its TRANSFER, not by the row that carries it
    today: a leg's ``transaction_id`` is documented to go NULL once transfer
    legs lose their shadow rows (plan step ``balance:X-bi-6-4d``), and a NULL
    would collapse every leg into one member of the set.

    Args:
        fact: A :class:`~app.services.cash_ledger.CashSourceFact`.

    Returns:
        ``("transfer", transfer id)`` or ``("row", transaction id)``.
    """
    if fact.transfer_id is not None:
        return ("transfer", fact.transfer_id)
    return ("row", fact.transaction_id)


def _next_bank_day() -> date:
    """Return the first day after BOUNDARY_DAY on which SECU posted a line.

    Checking's rows the repair moves sit between BOUNDARY_DAY and this day
    today (the app dated them a day late), and nothing else may, so it bounds
    the census in :func:`_checking_problems` and the verification in
    :func:`_grade_checking_boundary`.

    Returns:
        The day.

    Raises:
        SystemExit: When no line follows BOUNDARY_DAY.
    """
    day = db.session.execute(
        db.select(db.func.min(BankStatementLine.posted_on)).filter(
            BankStatementLine.account_id == CHECKING_ACCOUNT,
            BankStatementLine.posted_on > BOUNDARY_DAY,
        )
    ).scalar_one()
    if day is None:
        raise SystemExit(f"Checking has no bank line after {BOUNDARY_DAY}")
    return day


def _checking_sides_astray() -> "dict[int, SettleDay | None]":
    """Return each mapped transfer whose Checking side is not SECU's observed day.

    **The one statement of the rule, read before the acts and after them.**
    Every mapped transfer but the kept one carries its Checking side on the
    day SECU's import OBSERVED, and that is the day the map gives Fidelity's:
    these ACHs post at both banks the same day (ruling R-BAL3 for the two in
    March; the six this reads measured so on 2026-10-09).  The rehearsal
    refuses to start unless it holds (:func:`_subject_problems`), and the
    post-state requires that it STILL holds (:func:`_grade_checking_sides`):
    act 8 opens each of these cards and types account 10's box only, and
    Checking's box typed another day there passed every other arm (measured
    by adversarial review 2026-10-10).  The kept transfer's Checking side is
    act 5's to type and the boundary census's to grade.

    Returns:
        ``{transfer id: the side's SettleDay, or None for no movement}``, one
        entry per transfer whose side is not its mapped day as observed;
        empty when every side holds.
    """
    astray = {}
    for transfer_id, day in FIDELITY_DAYS.items():
        if transfer_id == KEPT_TRANSFER:
            continue
        side = _side_day(transfer_id, CHECKING_ACCOUNT)
        if (
            side is None or side.day != day
            or side.basis is not SettledDayBasisEnum.OBSERVED
        ):
            astray[transfer_id] = side
    return astray


def _subject_problems(export: _Export) -> "list[str]":
    """Return every disagreement between the Fidelity map and the records.

    Four arms.  **Each day**: what the export moved on every day it names
    after the books open equals what the mapped transfers' account-10 sides
    move on it -- zero on a day only a dividend answers, so a transfer line on
    a dividend day is caught.  **Each transfer**: it has a settled movement on
    account 10, is mapped to a day the export names, and no two claim one day.
    **Each Checking side**: on SECU's observed day, which is the map's day for
    Fidelity's (:func:`_checking_sides_astray`).  It is the arm that
    separates transfers of EQUAL amounts.
    **Each movement**: every one accounts 2 and 10 hold, on or before the
    export's last day, is a mapped transfer's or the dropped one's.

    Args:
        export: The parsed export.

    Returns:
        One sentence per disagreement, empty when the map reconciles.
    """
    problems: "list[str]" = []
    _, moved = _moved_by(_facts(SUBJECT_ACCOUNT))
    claimed: "dict[date, int]" = {}
    for transfer_id, day in FIDELITY_DAYS.items():
        if transfer_id not in moved:
            problems.append(
                f"transfer {transfer_id} has no settled movement on account "
                f"{SUBJECT_ACCOUNT}"
            )
        if day not in export.closings:
            problems.append(
                f"transfer {transfer_id} is mapped to {day}, a day the export "
                "does not name"
            )
        if day in claimed:
            problems.append(
                f"transfers {claimed[day]} and {transfer_id} both claim {day}"
            )
        claimed[day] = transfer_id
    problems.extend(
        f"transfer {transfer_id} is mapped to Fidelity's "
        f"{FIDELITY_DAYS[transfer_id]}, and its Checking side records {side}"
        for transfer_id, side in _checking_sides_astray().items()
    )

    dividends = {day for day, _ in export.recorded_dividends()}
    for day in export.named:
        if day <= BOOKS_OPEN:
            continue
        mapped = sum(
            (moved[t].delta for t, d in FIDELITY_DAYS.items()
             if d == day and t in moved),
            _ZERO_MONEY,
        )
        if export.moved_on(day) != mapped:
            problems.append(
                f"the export moved {export.moved_on(day)} on {day} and the "
                f"transfers mapped there move {mapped}"
            )
        if day not in claimed and day not in dividends:
            problems.append(f"the export names {day} and no act answers it")

    answered = set(FIDELITY_DAYS) | {DROPPED_TRANSFER}
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        problems.extend(
            f"account {account_id} carries a settled movement (transaction "
            f"{fact.transaction_id}, {fact.delta} on {fact.settled_on}) that "
            "no act answers"
            for fact in _facts(account_id)
            if fact.settled_on <= export.named[-1]
            and fact.transfer_id not in answered
        )
    return problems


def _line_problems(
    posted: dict, rows: dict, transfers: dict,
) -> "tuple[list[str], Decimal]":
    """Grade each boundary line against what answers it.

    Args:
        posted: ``{line id: BankStatementLine}`` on BOUNDARY_DAY.
        rows: Checking's ``{transaction id: _Moved}``.
        transfers: Checking's ``{transfer id: _Moved}``.

    Returns:
        ``(problems, residue)``: one sentence per disagreement, and what the
        residue line's rows move less what the bank posted, books minus bank.
    """
    problems: "list[str]" = []
    residue = _ZERO_MONEY
    for entry in BOUNDARY_LINES:
        line = posted.get(entry.line_id)
        if line is None:
            continue
        if entry.transfer_id is not None:
            found = transfers.get(entry.transfer_id)
            answered = None if found is None else found.delta
            dropped = transfers.get(DROPPED_TRANSFER)
            if dropped is None or dropped.delta != line.amount:
                problems.append(
                    f"transfer {DROPPED_TRANSFER}, the second record of the "
                    f"ACH bank line {entry.line_id} posted, moves "
                    f"{None if dropped is None else dropped.delta} on Checking"
                )
        elif all(row in rows for row in entry.rows):
            answered = sum((rows[row].delta for row in entry.rows), _ZERO_MONEY)
        else:
            answered = None
        if answered is None:
            problems.append(
                f"bank line {entry.line_id} is answered by a row or side with "
                "no settled movement on Checking"
            )
        elif entry.residue:
            residue = answered - line.amount
        elif answered != line.amount:
            problems.append(
                f"bank line {entry.line_id} moved {line.amount} and what "
                f"answers it moves {answered}"
            )
    return problems, residue


def _checking_problems() -> "tuple[list[str], Decimal]":
    """Return every disagreement on Checking's boundary, and BAL-467's residue.

    Graded against the app's OWN record of SECU's lines,
    ``budget.bank_statement_lines``:

    * the lines SECU posted on BOUNDARY_DAY are exactly the ones the map
      answers, and none is matched already (this repair answers them by hand);
    * **the census**: Checking's movements dated before the next day SECU
      posted anything are exactly the mapped rows and the dropped transfer's
      side, each row holding one movement.  This is what makes the residue
      BAL-467's and nothing else: with the rows fixed, and every other line
      answered to the cent, the residue line's shortfall is the day's whole
      gap, and a row left off the map or borrowed from another day is refused
      here rather than absorbed there;
    * each line other than the residue line equals what answers it;
    * the dropped transfer runs Checking -> the twin and moved what the kept
      one moved, which is what the line both answer says.

    Returns:
        ``(problems, residue)``, where *residue* is what the residue line's
        rows move less what the bank posted -- books minus bank.
    """
    problems: "list[str]" = []
    posted = {
        line.id: line
        for line in db.session.query(BankStatementLine).filter_by(
            account_id=CHECKING_ACCOUNT, posted_on=BOUNDARY_DAY,
        )
    }
    stated = {entry.line_id for entry in BOUNDARY_LINES} | {UNRECORDED_LINE}
    if set(posted) != stated:
        problems.append(
            f"Checking's bank lines on {BOUNDARY_DAY} are {sorted(posted)} "
            f"and the map answers {sorted(stated)}"
        )
    problems.extend(
        f"bank line {row[0]} is already matched; this repair answers it by "
        "hand"
        for row in db.session.query(
            StatementMatchMember.bank_statement_line_id,
        ).filter(StatementMatchMember.bank_statement_line_id.in_(stated))
    )

    facts = _facts(CHECKING_ACCOUNT)
    rows, transfers = _moved_by(facts)
    next_day = _next_bank_day()
    in_window = {_movement_key(f) for f in facts if f.settled_on < next_day}
    expected = {("row", row) for row in MAPPED_ROWS} | {
        ("transfer", DROPPED_TRANSFER),
    }
    if in_window != expected:
        problems.append(
            f"Checking's movements before {next_day} are {sorted(in_window)}; "
            f"the map answers {sorted(expected)}"
        )
    problems.extend(
        f"transaction {row} holds {rows[row].movements} movements, not one"
        for row in MAPPED_ROWS if row in rows and rows[row].movements != 1
    )

    line_problems, residue = _line_problems(posted, rows, transfers)
    problems.extend(line_problems)
    dropped = db.session.get(Transfer, DROPPED_TRANSFER)
    if dropped is None or (dropped.from_account_id, dropped.to_account_id) != (
        CHECKING_ACCOUNT, TWIN_ACCOUNT,
    ):
        problems.append(
            f"transfer {DROPPED_TRANSFER} is to be DROPPED as Checking -> "
            f"account {TWIN_ACCOUNT}, and it is not that"
        )
    return problems, residue


def _reconcile(export: _Export, sheet: _Sheet, stated: Decimal) -> Decimal:
    """Refuse to start unless the stated map matches BOTH banks' records.

    :func:`_subject_problems` grades account 10 against Fidelity's export and
    :func:`_checking_problems` grades Checking's boundary against SECU's lines.
    **The census arms run in opposite directions on purpose**: one that only
    walks the bank claims the app rows nobody looked at, and one that only
    walks the app claims the bank lines nobody looked at.

    **BAL-467's residue is checked against the figure that ledger row STATES**
    (*stated*, typed at run time so no committed file holds it, ruling
    **R-BAL249**).  That is what closes the hole the census cannot: the census
    sees only Checking's movements dated before SECU's next posted day, so a
    row the residue line should name, dated LATER, would otherwise be absorbed
    into the residue -- measured by adversarial review 2026-10-09 with a whole
    paycheck row moved out of the window.

    **The limits, stated because a control whose edges are unstated reads as
    stronger than it is**: two transfers whose Checking sides fall on the same
    day AND move the same amount could still be exchanged (the census refuses
    a day two transfers claim, and none do today); and the residue arm is only
    as good as the figure typed for it.

    Args:
        export: The parsed export.
        sheet: The performance sheet.
        stated: The residue ledger row BAL-467 states, books less bank.

    Returns:
        BAL-467's residue, for :func:`_grade_checking_boundary`.

    Raises:
        SystemExit: On any disagreement, naming the day and both figures.
    """
    problems = _subject_problems(export)
    checking, residue = _checking_problems()
    problems.extend(checking)
    if residue != stated:
        problems.append(
            f"the payroll residue on bank line {_RESIDUE_LINE.line_id} measures "
            f"{residue}, and ledger row BAL-467 states {stated}"
        )
    twin_opens = cash_ledger.governing_account_opening(TWIN_ACCOUNT).opened_on
    if twin_opens != TWIN_OPENED_ON:
        problems.append(
            f"account {TWIN_ACCOUNT}'s books open {twin_opens}, and act 2 is "
            f"stated to keep {TWIN_OPENED_ON}"
        )
    if problems:
        raise SystemExit(
            "the stated map does not reconcile:\n  " + "\n  ".join(problems)
        )
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        beyond = [
            fact for fact in _facts(account_id)
            if fact.settled_on > export.named[-1]
        ]
        sheet.say(
            f"account {account_id} holds {len(beyond)} settled movement(s) "
            f"after {export.named[-1]}, the export's last day; the export "
            "cannot speak for them and no act touches them"
        )
    bank_days = [day for day in export.named if day > BOOKS_OPEN]
    sheet.say(
        f"map reconciles both ways: {len(FIDELITY_DAYS)} transfers and "
        f"{len(export.recorded_dividends())} dividends answer all "
        f"{len(bank_days)} Fidelity days after {BOOKS_OPEN}; Checking's "
        f"{len(BOUNDARY_LINES) + 1} lines on {BOUNDARY_DAY} are each answered"
    )
    sheet.say(
        f"BAL-467 residue on bank line {_RESIDUE_LINE.line_id} (books less "
        f"bank): **{residue}**"
    )
    return residue


def _class_totals() -> "dict[str, Decimal]":
    """Return the posted ledger's total by account class.

    **This is what replaced a trial-balance assertion that could not fail.**
    ``budget.account_postings`` carries a deferred trigger refusing any journal
    entry whose legs do not sum to zero, so ``sum(amount)`` is ``0.00`` in
    every state the database can hold and asserting it measures the TRIGGER
    rather than the repair (adversarial review, 2026-09-01).  What the acts
    actually move is the balance BETWEEN classes, so that is what is captured
    before and diffed after -- and :func:`_expected_class_moves` turns three of
    those classes into an assertion, because a printed diff nothing compares
    against is not a replacement for a check, only for a claim.

    Returns:
        ``{class name: total}``.
    """
    return {
        row.name: row.total
        for row in db.session.execute(db.text("""
            select cl.name, coalesce(sum(ap.amount), 0) as total
            from budget.account_postings ap
            join budget.ledger_accounts la on la.id = ap.ledger_account_id
            join ref.ledger_account_classes cl on cl.id = la.class_id
            group by cl.name
        """)).all()
    }


def _account_trueup_total(account_id: int) -> Decimal:
    """Return what the posted ledger books as corrections for one account.

    The APP's own answer, read off ``budget.account_postings``, rather than a
    replay written here: an earlier version hand-rolled the correction fold and
    disagreed with the seam on three of four assertions, because it expressed
    neither the RESET (ruling **R-S**) nor which assertion clears which source
    (ruling **R-FL**).  A verification that re-implements the rule it checks is
    an equality whose two sides come from one head.

    Args:
        account_id: The account.

    Returns:
        The sum of its ASSET-side ``trueup`` postings.
    """
    return db.session.execute(db.text("""
        select coalesce(sum(ap.amount), 0)
        from budget.account_postings ap
        join budget.ledger_accounts la on la.id = ap.ledger_account_id
        join ref.posting_kinds k on k.id = ap.posting_kind_id
        join ref.ledger_account_classes cl on cl.id = la.class_id
        where la.account_id = :account and k.name = 'trueup'
          and cl.name = 'Asset'
    """), {"account": account_id}).scalar_one()


def _postings_fingerprint() -> str:
    """Return a digest of every posted-ledger row, ordered.

    **Written because the runbook made a claim nothing graded.**  It said the
    twin's archive round trip "moves no money (measured: a byte-identical
    postings fingerprint either side)" and no fingerprint existed anywhere in
    the instruments -- a one-off measurement quoted as a standing property,
    which is this project's "a fix describes itself ungraded" (adversarial
    review, 2026-09-01).  Now the round trip asserts it.

    Every column that could carry money or attribution is in the digest --
    the posting's, and its journal entry's date, period, source, scenario,
    owner, description and the movement it books (an entry's one source link
    since card plan#1845 dropped its row and transfer links) -- and the
    ORDER is fixed
    by ``ap.id`` so two equal ledgers cannot hash differently for a row-order
    reason.

    Returns:
        A hex SHA-256 over the posted ledger.
    """
    rows = db.session.execute(db.text("""
        select ap.id, ap.journal_entry_id, ap.ledger_account_id,
               ap.posting_kind_id, ap.amount, je.entry_date, je.pay_period_id,
               je.source_kind_id, je.scenario_id, je.user_id,
               je.transaction_entry_id, je.description
        from budget.account_postings ap
        join budget.journal_entries je on je.id = ap.journal_entry_id
        order by ap.id
    """)).all()
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(str(cell) for cell in row).encode())
    return f"{digest.hexdigest()[:16]} over {len(rows)} postings"


def _interest_income_total(account_id: int) -> Decimal:
    """Return the account's modelled interest-income chart row.

    Args:
        account_id: The account.

    Returns:
        Its total, or ``0`` when the row does not exist.
    """
    return db.session.execute(db.text("""
        select coalesce(sum(ap.amount), 0)
        from budget.account_postings ap
        join budget.ledger_accounts la on la.id = ap.ledger_account_id
        join ref.ledger_account_kinds k on k.id = la.kind_id
        where la.account_id = :account and k.name = 'interest_income'
    """), {"account": account_id}).scalar_one()


def _expected_class_moves(
    export: _Export, modelled_before: Decimal, recorded_expense: Decimal,
) -> "dict[str, Decimal]":
    """Return the class movements this repair's own inputs DERIVE.

    Four of the six classes are asserted rather than printed:

    * **Expense** rises by the one bank line act 7 records (finding
      **BAL-468**).  The figure is the line's own, which act 7 also typed, so
      this does not grade the typing; what it grades is that NOTHING ELSE
      reached an expense row -- act 6 re-dates one without changing its
      figure, and a transfer leg posts as a transfer;
    * **Income** falls by every dividend recorded, and RISES by the modelled
      interest those dividends replace -- the corrections that were standing in
      for them, which go to zero (ruling **R-HL**);
    * **Liability** and **Unrealized** do not move at all: no act touches a
      loan or a market value.

    Asset and Equity are NOT derived here.  Both move by the three
    restatements, whose figures are the repair's own subject rather than an
    input to it, and an expectation computed the way the acts compute it would
    be an equality with one head.

    Args:
        export: The parsed export, for the dividends recorded.
        modelled_before: The twin's and account 10's ``interest_income``
            totals before the repair, summed, LEDGER-NATIVE (a credit, so
            negative); both rows end at zero.
        recorded_expense: What act 7 records, positive.

    Returns:
        ``{class name: expected change}`` for the four classes this derives.
    """
    dividends = sum(
        (amount for _, amount in export.recorded_dividends()), _ZERO_MONEY,
    )
    return {
        "Expense": recorded_expense,
        "Income": -dividends - modelled_before,
        "Liability": _ZERO_MONEY,
        "Unrealized": _ZERO_MONEY,
    }


def _snapshot(export: _Export, sheet: _Sheet, label: str = "AFTER") -> None:
    """Record what the three touched accounts show, before or after an act.

    **After every act, because that is what tells a half-done repair from a
    finished one**, and what an operator compares the screen against before
    taking the next act.  It is also the honest place to see that most acts
    move a CORRECTION rather than a displayed balance.  Ruling **R-FO** sends
    an interest-bearing account's true-up to its ``interest_income`` row, and
    two such rows move here: the TWIN's books interest that never happened
    from act 1 until act 2 clears it, and account 10's -- nonzero before act
    1, standing in for dividends the app never recorded -- grows at act 3 and
    empties at act 9, when the dividends are recorded.  Both accounts'
    corrections and modelled interest are printed, act by act.

    **Valued at the export's LAST DAY rather than at today**, which is what
    makes two rehearsals comparable: an interest-bearing account accrues, so
    "the balance now" is a different number every day it is run.

    Args:
        export: The parsed export, for the day to value at.
        sheet: The performance sheet.
        label: ``BEFORE`` for the state no act has touched yet.
    """
    db.session.expire_all()
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    last = export.named[-1]
    context = BalanceContext.build(user_id=subject.user_id, as_of=last)
    figures = ", ".join(
        f"account {account_id} {balance_at(db.session.get(Account, account_id), context, last)}"
        for account_id in (CHECKING_ACCOUNT, TWIN_ACCOUNT, SUBJECT_ACCOUNT)
    )
    windows = "; ".join(
        f"account {account_id} corrections "
        f"{_account_trueup_total(account_id)}, modelled interest "
        f"{_interest_income_total(account_id)}"
        for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT)
    )
    sheet.say(f"- {label} (valued {last}): {figures}; {windows}")


def _restate_opening(
    op: _Operator, account_id: int, opened_on: date, equity: Decimal,
) -> None:
    """Restate one account's books through the edit page's own card.

    Args:
        op: The owner's session, which carries the performance sheet.
        account_id: The account to restate.
        opened_on: The day its books open.
        equity: What its books opened holding.

    Raises:
        AssertionError: When the door did not move the governing opening.
    """
    form = op.form_posting_to(
        f"/accounts/{account_id}/edit", f"/accounts/{account_id}/opening",
    )
    op.sheet.typed("When the books opened: Books opened on",
                   opened_on.isoformat())
    op.sheet.typed("When the books opened: Opening equity", equity)
    op.sheet.say("- PRESS 'Restate opening' (NOT the page's 'Update'); the green "
                 "flash must begin 'Books restated'")
    op.submit(form, {
        "opened_on": opened_on.isoformat(), "opening_equity": str(equity),
    })
    db.session.expire_all()
    opening = cash_ledger.governing_account_opening(account_id)
    assert (opening.opened_on, opening.opening_equity) == (opened_on, equity), \
        (f"account {account_id} books open {opening.opened_on} at "
         f"{opening.opening_equity}, not {opened_on} at {equity}")


def _assert_balance(
    op: _Operator, account_id: int, balance: Decimal, observed_on: date,
) -> None:
    """Assert a balance through the account's own true-up editor.

    It writes the two values the owner types onto the sheet itself, so no
    caller states them a second time.

    Args:
        op: The owner's session, which carries the performance sheet.
        account_id: The account.
        balance: What the bank says it held.
        observed_on: The day it held it.

    Raises:
        AssertionError: When the editor renders no date or balance box, or the
            appended assertion does not govern afterwards.
    """
    form = op.form(f"/accounts/{account_id}/anchor-form")
    fields = _payload(form)
    boxes = {key for key in fields if "balance" in key or "anchor" in key}
    days = {key for key in fields if "observed" in key or key.endswith("_on")}
    assert boxes and days, f"anchor form fields: {sorted(fields)}"
    op.sheet.typed("Balance", balance)
    op.sheet.typed("As of", observed_on.isoformat())
    op.submit(form, {
        **{key: str(balance) for key in boxes},
        **{key: observed_on.isoformat() for key in days},
    })
    db.session.expire_all()
    governing = cash_ledger.governing_anchor_on(account_id, observed_on)
    assert (governing.balance, governing.observed_on) == (balance, observed_on), \
        (f"account {account_id} governs at {governing.balance} on "
         f"{governing.observed_on}, not {balance} on {observed_on}")
    print(f"  account {account_id} asserts {balance} on {observed_on}")


def _category_id(user_id: int, group: str, item: str) -> int:
    """Return one category's id.

    Args:
        user_id: The owner.
        group: Its group name.
        item: Its item name.

    Returns:
        The ``budget.categories`` id.
    """
    return db.session.execute(
        db.select(Category.id).filter_by(
            user_id=user_id, group_name=group, item_name=item,
        )
    ).scalar_one()


def _period_holding(account_id: int, day: date):
    """Return the pay period whose span contains *day*, for that account's owner.

    **It asks the owner's CALENDAR since plan step ``pay_calendar:C4-c``.**  It
    was ``select id from budget.pay_periods where :day between start_date and
    end_date`` -- a raw span read off the row -- and that step dropped
    ``end_date``, because a period's last covered day is the day before the
    NEXT payday and is a property of the whole payday set rather than of one
    row.  ``period_containing`` is the application's own answer to this
    question, which is what the door this id is posted to will use.

    It also gained the account, and that is a repair rather than plumbing: the
    query it replaced was scoped by nothing at all, so on a database holding a
    second owner it would have raised ``MultipleResultsFound`` -- or, worse,
    have been made to work by dropping ``scalar_one`` and filed the row under
    someone else's paycheck.

    Args:
        account_id: The account the row will belong to; its owner is whose
            calendar answers.
        day: A civil day.

    Returns:
        The :class:`~app.services.pay_calendar.DerivedPeriod`: its
        ``period_id`` is what the create door is posted, and its
        ``start_date`` is the payday the owner picks it by.

    Raises:
        AssertionError: No paycheck of that owner covers *day*, which would
            otherwise post a ``period_id`` of ``None`` to the create door.
    """
    # Pylint: ``import-outside-toplevel`` -- this module is a runbook script
    # rather than an importable unit, and its own imports are grouped at the
    # top for the reader; the calendar is needed by this one helper.
    from app.services.pay_calendar import calendar_for  # pylint: disable=import-outside-toplevel

    owner_id = db.session.get(Account, account_id).user_id
    period = calendar_for(owner_id).period_containing(day)
    assert period is not None and period.period_id is not None, (
        f"no saved paycheck of owner {owner_id} covers {day}, so this row "
        f"has no period to be filed in"
    )
    return period


def _row_ids(account_id: int, category_id: int) -> "set[int]":
    """Return every transaction id one account holds in one category.

    The census :func:`_record` takes either side of a create, so the row the
    create made is the one id that APPEARS -- not the highest id, which
    answers whichever row is newest whether or not this request made it.

    Args:
        account_id: The account.
        category_id: The category.

    Returns:
        The ids, deleted rows included.
    """
    db.session.expire_all()
    return set(db.session.execute(
        db.select(Transaction.id).filter_by(
            account_id=account_id, category_id=category_id,
        )
    ).scalars())


def _set_grid_account(op: _Operator, account_id: "int | None") -> None:
    """Point the budget grid at *account_id* through Settings > General.

    **The grid's Add Transaction is the act's door, and nothing links to the
    grid on account 10** (the statement-reconcile pages create rows only from
    imported bank lines, which this repair does not use): every grid link, its
    own earlier/later arrows included, renders without ``account_id``, so it
    opens on the owner's Default Grid Account.  Setting that is the owner's click path to account
    10's grid, and setting it back is the owner's way home.  It moves no
    money, which every act's AFTER line shows.

    Args:
        op: The owner's session, which carries the performance sheet.
        account_id: The account, or ``None`` for the page's "Auto" choice.

    Raises:
        AssertionError: When the setting does not then hold *account_id*.
    """
    form = op.form_posting_to("/settings?section=general", "/settings")
    op.sheet.typed("Settings > General > Default Grid Account",
                   _grid_choice(account_id))
    op.submit(form, {
        "default_grid_account_id": "" if account_id is None else str(account_id),
    })
    assert _grid_account(op.user_id) == account_id, \
        f"the Default Grid Account is not {account_id}"


def _grid_choice(account_id: "int | None") -> str:
    """Return how Settings > General names one Default Grid Account choice.

    Args:
        account_id: The account, or ``None`` for the page's "Auto" choice.

    Returns:
        The choice as the operator reads it.
    """
    if account_id is None:
        return "Auto (first checking account)"
    return db.session.get(Account, account_id).name


def _grid_account(user_id: int) -> "int | None":
    """Return the owner's Default Grid Account setting.

    Args:
        user_id: The owner.

    Returns:
        The account id, or ``None`` for "Auto".
    """
    db.session.expire_all()
    return db.session.execute(
        db.select(UserSettings.default_grid_account_id).filter_by(
            user_id=user_id,
        )
    ).scalar_one()


def _settled_status_id(txn_id: int) -> str:
    """Return the status a row of *txn_id*'s type settles as, as the form posts it.

    Args:
        txn_id: The transaction.

    Returns:
        The ``ref.statuses`` id, from the reference cache.
    """
    type_id = db.session.get(Transaction, txn_id).transaction_type_id
    kind = next(
        member for member in _SETTLED_STATUS
        if ref_cache.txn_type_id(member) == type_id
    )
    return str(ref_cache.status_id(_SETTLED_STATUS[kind]))


@dataclass(frozen=True)
class _NewRow:
    """One row this repair records that the app holds nowhere.

    Act 7's bank line and each of act 9's dividends: what the owner types into
    the grid's Add Transaction modal, and the day the money moved.

    Attributes:
        account_id: The account the grid is on, which the row belongs to.
        category_id: Its category.
        kind: Income or expense.
        day: The day the money moved, whose paycheck the row is filed under.
        amount: What moved.
        name: What to call it.
    """

    account_id: int
    category_id: int
    kind: TxnTypeEnum
    day: date
    amount: Decimal
    name: str


def _new_rows(export: _Export) -> "list[_NewRow]":
    """Return the six rows the repair records: act 7's, then act 9's dividends.

    **One list, read by the acts and by the check**: :func:`_perform` types
    exactly these, and :func:`_grade_created_rows` expects exactly these on
    the copy, so what a rehearsal records and what a performance is graded
    against cannot come apart.

    Args:
        export: The parsed export, which states each dividend.

    Returns:
        Act 7's row first, then each dividend in day order.
    """
    unrecorded = db.session.get(BankStatementLine, UNRECORDED_LINE)
    return [_NewRow(
        CHECKING_ACCOUNT, UNRECORDED_CATEGORY, TxnTypeEnum.EXPENSE,
        unrecorded.posted_on, -unrecorded.amount, UNRECORDED_NAME,
    )] + [
        _NewRow(
            SUBJECT_ACCOUNT, DIVIDEND_CATEGORY_ID, TxnTypeEnum.INCOME, day,
            amount, DIVIDEND_NAME,
        )
        for day, amount in export.recorded_dividends()
    ]


def _record(op: _Operator, row: _NewRow) -> int:
    """Create *row*, settle it, then correct the day -- the owner's own path.

    **Created through the grid's Add Transaction modal, because a grid CELL
    exists only on a row that already holds a transaction** (``grid_view_service
    .build_row_keys`` draws rows from transactions alone; adversarial review
    2026-10-09).  Category 33 is empty until act 9's first dividend, so it has
    no row and no cell, and an earlier draft reached the cell's create card by
    typing its URL -- a door no click opens.  The modal is on every grid page,
    takes the name, amount, type, category and paycheck, and posts the account
    the grid is on; it shares its producer with the cell's create door
    (``routes/transactions/create.py``).  The grid must already be on the
    row's account, which this asserts rather than types: the owner leaves that
    control as rendered.  :func:`_settle_new_row` is the rest of the path.

    Args:
        op: The owner's session, which carries the performance sheet.
        row: What to record.

    Returns:
        The new transaction's id.

    Raises:
        AssertionError: When the grid is on another account, the create makes
            other than exactly one row, or :func:`_settle_new_row` does.
    """
    form = op.form_posting_to("/grid", "/transactions")
    posted = _payload(form).get("account_id")
    assert posted == str(row.account_id), \
        f"the grid's Add Transaction posts account {posted}, not {row.account_id}"
    period = _period_holding(row.account_id, row.day)
    category = db.session.get(Category, row.category_id)
    for label, value in (
        ("Name", row.name), ("Amount", row.amount), ("Type", row.kind.value),
        ("Category", f"{category.group_name}: {category.item_name}"),
        ("Pay Period", period.label),
    ):
        op.sheet.typed(f"Add Transaction: {label}", value)
    before = _row_ids(row.account_id, row.category_id)
    op.submit(form, {
        "name": row.name, "estimated_amount": str(row.amount),
        "transaction_type_id": str(ref_cache.txn_type_id(row.kind)),
        "category_id": str(row.category_id),
        "pay_period_id": str(period.period_id),
    })
    made = _row_ids(row.account_id, row.category_id) - before
    assert len(made) == 1, f"the create made rows {sorted(made)}, not one"
    txn_id = made.pop()
    _settle_new_row(op, txn_id, row)
    return txn_id


def _settle_new_row(op: _Operator, txn_id: int, row: _NewRow) -> None:
    """Settle a just-created row, then correct its day: saves two and three.

    **Three submissions in all, and the third is the one an earlier draft
    skipped.**  The full-edit card renders "Money moved on" ONLY for a row that
    is already settled -- ``grid/_transaction_full_edit.html`` gates it on
    ``txn.status.is_settled``, and gates the "Actual" box beside it on
    ``is_settled and correctable``.  So a browser cannot state the day while
    the row is Projected; the operator settles first -- **which stamps
    TODAY** -- and then reopens the now-settled card and corrects the day.

    **Between the second and third submissions the row is live in the fold at
    TODAY'S date**, for its full figure.  It is transient and inside one act,
    which is why the runbook tells an operator to finish a row rather than
    leave one half-recorded.

    Args:
        op: The owner's session, which carries the performance sheet.
        txn_id: The row :func:`_record` created.
        row: What it records.

    Raises:
        AssertionError: When a Projected card already offers a settle-day box
            (which would mean this path is describing a page that no longer
            exists), or the row does not end holding the day, the figure and
            the name.
    """
    settle = op.form(f"/transactions/{txn_id}/full-edit")
    assert "settled_on" not in _payload(settle), (
        f"transaction {txn_id} is Projected and its card already renders a "
        "settle-day box; this rehearsal's three-step path assumes it does not"
    )
    op.sheet.typed("the new row's card: More options > Status, then Save "
                   "(stamps TODAY)", _SETTLED_STATUS[row.kind].value)
    op.submit(settle, {"status_id": _settled_status_id(txn_id)})

    correct = op.form(f"/transactions/{txn_id}/full-edit")
    fields = _payload(correct)
    assert "settled_on" in fields, \
        f"transaction {txn_id} settled but its card renders no settle-day box"
    # Only the DAY makes this third save necessary.  The settle above already
    # stamped the figure from the estimate, so re-posting it changes nothing
    # -- it is submitted because the card renders it and an untouched Save
    # posts what it renders.
    typed = {"settled_on": row.day.isoformat()}
    op.sheet.typed("reopened card: Money moved on, then Save",
                   row.day.isoformat())
    if "settled_amount" in fields:
        typed["settled_amount"] = str(row.amount)
    op.submit(correct, typed)

    db.session.expire_all()
    txn = db.session.get(Transaction, txn_id)
    held = (txn.settled_on, settled_figure(txn), txn.name)
    assert held == (row.day, row.amount, row.name), \
        f"transaction {txn_id} holds {held}, not {(row.day, row.amount, row.name)}"


def _redate_row(op: _Operator, txn_id: int, day: date) -> None:
    """Move one settled row's day through its own card.

    Args:
        op: The owner's session, which carries the performance sheet.
        txn_id: The transaction.
        day: The day the bank posted it.

    Raises:
        AssertionError: When the card renders no settle-day box, or the fold
            does not then count the row on *day*.
    """
    form = op.form(f"/transactions/{txn_id}/full-edit")
    assert "settled_on" in _payload(form), \
        f"transaction {txn_id}'s card renders no settle-day box"
    named = db.session.get(Transaction, txn_id).name
    op.sheet.typed(f"transaction {txn_id} ({named}): Money moved on",
                   day.isoformat())
    op.submit(form, {"settled_on": day.isoformat()})
    rows, _ = _moved_by(_facts(db.session.get(Transaction, txn_id).account_id))
    assert rows[txn_id].day == day, \
        f"transaction {txn_id} is counted on {rows[txn_id].day}, not {day}"


def _redate_sides(
    op: _Operator, transfer_id: int, days: "dict[int, date]",
) -> None:
    """Type each named side's day into its own box, in ONE save.

    **Each side keeps its own day** (plan step ``balance:X-bi-6-4c-3``, ruling
    **R-BAL142**), so the popover renders one "Money moved on" box per side,
    and a side whose day is only BORROWED renders empty and reads "a guess".
    Typing a side's box makes the day that side's own, ENTERED; leaving a box
    as rendered leaves its side alone.  Ruling **R-BAL256** has every
    Fidelity side typed from Fidelity's own record, so after this the side
    must carry the day as evidence, not merely land on it.

    Args:
        op: The owner's session, which carries the performance sheet.
        transfer_id: The transfer.
        days: ``{account id: the day that account's bank posted it}``.

    Raises:
        AssertionError: When the popover renders no box for a side, or a side
            does not then record its day as its own.
    """
    transfer = db.session.get(Transfer, transfer_id)
    boxes = {
        transfer.from_account_id: "settled_on_from",
        transfer.to_account_id: "settled_on_to",
    }
    # Opened from the grid, as the owner reaches it: the only page that draws
    # a transfer cell is an account's grid, whose card carries the leg's id.
    leg = next(iter(days))
    form = op.form(f"/transfers/{transfer_id}/full-edit?leg_account_id={leg}")
    fields = _payload(form)
    typed = {}
    named = _transfer_named(transfer_id, leg)
    for account_id, day in days.items():
        assert boxes[account_id] in fields, (
            f"transfer {transfer_id}'s popover renders no box for account "
            f"{account_id}"
        )
        typed[boxes[account_id]] = day.isoformat()
        op.sheet.typed(
            f"transfer {transfer_id} ({named}): Money moved on, account "
            f"{account_id}",
            day.isoformat(),
        )
    op.submit(form, typed)
    for account_id, day in days.items():
        side = _side_day(transfer_id, account_id)
        assert side is not None and side.day == day and is_evidence(side), (
            f"transfer {transfer_id}'s side on account {account_id} records "
            f"{side}, not {day} as its own"
        )


def _drop_transfer(op: _Operator, transfer_id: int) -> None:
    """Set a settled transfer back to Projected, then Cancel it.

    **Ruling R-BAL255, and why it is not a delete**: no page renders the
    single-transfer delete door (``routes/transfers/mutations.py``
    ``delete_transfer`` says so itself), so a delete could never be clicked;
    and Cancelled is the true statement about this row -- the planned transfer
    to the old account did not happen, because the one real ACH is the kept
    transfer's.  The card offers Cancel only for a Projected transfer, so the
    owner sets Status to Projected and saves -- which CLOSES the card -- then
    reopens it from Checking's grid and presses the quick action whose
    accessible name is "Cancel transfer".  **On screen the reopened card has
    TWO buttons reading "Cancel"** (``transfers/_transfer_full_edit.html``):
    the grey one with an x beside Save only closes the card, and the
    red-outlined one with an octagon, beside the green Paid, is the cancel.
    **And the set-back cell grows a one-click check mark that marks the
    transfer Paid again, dated today.**  The grey button leaves transfer 1 a
    Projected plan, which rule R-G counts on the day after the valuation day,
    so every FORWARD projection of Checking and the twin is off by transfer
    1's amount while a valuation at any past day reads unchanged (measured by
    adversarial review 2026-10-09); the check mark moves it again, on today's
    date.  That is why :func:`_grade_dropped_transfer` asserts Cancelled.  What
    the owner sees on success is the cell DISAPPEARING from Checking's grid,
    since a cancelled item has no grid row (``grid_view_service``; the same
    review counted two cells before and none after).  Between the save and
    the press the row is a PLAN again, so the two are one act.

    Args:
        op: The owner's session, which carries the performance sheet.
        transfer_id: The transfer to drop.

    Raises:
        AssertionError: When the set-back does not render Cancel, the transfer
            does not end Cancelled, or either account's fold still counts it.
    """
    projected = ref_cache.status_id(StatusEnum.PROJECTED)
    card = f"/transfers/{transfer_id}/full-edit?leg_account_id={CHECKING_ACCOUNT}"
    form = op.form(card)
    op.sheet.typed("Status", "Projected, then Save (the card closes)")
    op.submit(form, {"status_id": str(projected)})
    cancel = f"/transfers/instance/{transfer_id}/cancel"
    page = op.client.get(card, headers={"HX-Request": "true"}).get_data(
        as_text=True,
    )
    assert cancel in page, \
        f"transfer {transfer_id}'s reopened card renders no Cancel transfer"
    op.sheet.say(
        "- REOPEN the card by clicking the transfer's cell on Checking's grid "
        "(NOT the check mark the cell now shows: that marks it Paid again, "
        "dated today). PRESS the red-outlined 'Cancel' with the octagon, in "
        "the bottom row beside the green 'Paid' -- NOT the grey 'Cancel' with "
        "the x beside Save, which only closes the card. The transfer's cell "
        "then DISAPPEARS from Checking's grid: a cancelled transfer has none."
    )
    op.send("post", cancel, {"leg_account_id": str(CHECKING_ACCOUNT)})
    db.session.expire_all()
    transfer = db.session.get(Transfer, transfer_id)
    assert transfer.status_id == ref_cache.status_id(StatusEnum.CANCELLED), \
        f"transfer {transfer_id} is status {transfer.status_id}, not Cancelled"
    for account_id in (transfer.from_account_id, transfer.to_account_id):
        _, moved = _moved_by(_facts(account_id))
        assert transfer_id not in moved, \
            f"account {account_id}'s fold still counts transfer {transfer_id}"


def _set_archived(op: _Operator, account_id: int, archived: bool) -> None:
    """Archive or unarchive an account and assert the flag moved.

    **The posted ledger is fingerprinted either side, so "the round trip moves
    no money" is GRADED rather than claimed.**  The runbook asserted that
    property and nothing measured it (adversarial review, 2026-09-01).
    Archiving is a visibility flag: the balance sheet reads the posted ledger
    over the whole chart and filters no account (**N-384**), so a flip that
    moved a posting would move net worth silently.

    Args:
        op: The owner's session.
        account_id: The account.
        archived: The state to reach.

    Raises:
        AssertionError: When the flag did not move, or the flip moved money.
    """
    door = "archive" if archived else "unarchive"
    before = _postings_fingerprint()
    op.send("post", f"/accounts/{account_id}/{door}", {})
    db.session.expire_all()
    assert db.session.get(Account, account_id).is_active is not archived, \
        f"account {account_id} did not {door}"
    after = _postings_fingerprint()
    assert after == before, (
        f"{door} of account {account_id} MOVED THE POSTED LEDGER: "
        f"{before} -> {after}"
    )
    print(f"  account {account_id} {door}d; postings unchanged ({after})")


def _add_earlier_paychecks(op: _Operator, count: int) -> None:
    """Record paychecks before the first one ("Add earlier paychecks").

    Plan step ``pay_calendar:C18-b``.  NOT one of ruling R-BAL3's acts: it is
    rehearsed so the runbook can say whether performing it first changes the
    repair (the step waits on ``pay_calendar:C18`` because the rehearsal must
    run against the calendar the repair is performed on).

    Args:
        op: The owner's session, which carries the performance sheet.
        count: How many paychecks to add.

    Raises:
        AssertionError: When the owner's paycheck count did not grow by *count*.
    """
    def periods() -> int:
        return db.session.execute(
            db.text("select count(*) from budget.pay_periods where user_id = :u"),
            {"u": op.user_id},
        ).scalar_one()

    was = periods()
    form = op.form_posting_to(
        "/settings?section=pay-periods", "/pay-periods/earlier",
    )
    op.sheet.typed("Add earlier paychecks: Paychecks to add", count)
    op.submit(form, {"num_periods": str(count)})
    db.session.expire_all()
    assert periods() == was + count, \
        f"paychecks went {was} -> {periods()}, not up by {count}"


def _type_fidelity_days(op: _Operator) -> None:
    """Type Fidelity's day into account 10's box on every other mapped transfer.

    Ruling **R-BAL256**: each Fidelity side carries its own day from
    Fidelity's record rather than borrowing Checking's.  **Which side needs it
    is MEASURED on both terms, the day and how it is known**: a side already
    on its day as its OWN is skipped, and one merely borrowing the right day is
    typed, since "a guess" that happens to agree would follow any later
    change to its Checking side.  Checking's sides are not touched here: each
    is SECU's to state, and the reconcile that grades them is plan step X-bk-2.

    Args:
        op: The owner's session, which carries the performance sheet.
    """
    for transfer_id, day in FIDELITY_DAYS.items():
        if transfer_id == KEPT_TRANSFER:
            continue
        side = _side_day(transfer_id, SUBJECT_ACCOUNT)
        if side is not None and side.day == day and is_evidence(side):
            op.sheet.say(
                f"- transfer {transfer_id} "
                f"({_transfer_named(transfer_id, SUBJECT_ACCOUNT)}): account "
                f"{SUBJECT_ACCOUNT}'s side already records {day} as its own; "
                "nothing to type"
            )
            continue
        _redate_sides(op, transfer_id, {SUBJECT_ACCOUNT: day})


def _consolidate_twin(op: _Operator) -> None:
    """Zero the archived twin in ONE sitting: unarchive, restate, assert, archive.

    **It follows act 1 directly, and that placement is the reason it is act
    2.**  Dropping transfer 1 takes its arrival off the twin while the twin's
    2026-04-06 assertion still stands, so the gap books a correction that
    ruling **R-FO** reports as INTEREST on this interest-bearing account -- a
    gain that never happened, measured by adversarial review 2026-10-09 -- and
    zeroing the twin is what clears it.  Nothing else forces this act's place.

    **The twin is UNARCHIVED for its own two acts and archived again after.**
    Its restatement door is reachable while archived (plan step
    ``balance:X-f3c-2b-2d`` closed finding **N-430**), but its BALANCE editor
    is not reachable by clicking (finding **N-453**, ruled to
    ``balance:X-f4``), and the runbook is a click procedure, so the round trip
    stays.  **Do not stop while it is unarchived, nor between the restatement
    and the assertion**: zeroing the opening while the 2026-04-06 assertion
    stands books that whole figure as the same false interest until the
    assertion that follows supersedes it.  The round trip moves no money, which
    :func:`_set_archived` grades with a postings fingerprint either side.

    Args:
        op: The owner's session, which carries the performance sheet.
    """
    _set_archived(op, TWIN_ACCOUNT, archived=False)
    _restate_opening(op, TWIN_ACCOUNT, TWIN_OPENED_ON, _ZERO_MONEY)
    _assert_balance(op, TWIN_ACCOUNT, _ZERO_MONEY, TWIN_ASSERTED_ON)
    _set_archived(op, TWIN_ACCOUNT, archived=True)


def _record_dividends(op: _Operator, dividends: "list[_NewRow]") -> None:
    """Rename the empty category into R-HL's, then record every dividend.

    Args:
        op: The owner's session, which carries the performance sheet.
        dividends: :func:`_new_rows`' dividends, in day order.
    """
    # The group control is a hidden input that SCRIPT keeps in step with a
    # select carrying no name of its own; the page renders it holding the
    # category's CURRENT group, so the owner's new choice reaches the payload
    # only through that script.  Supplying the group here is exactly what the
    # owner's pick does -- and said out loud, because a payload that overrides
    # what a page rendered is the thing this file's parser exists to avoid
    # doing silently.
    form = op.form_posting_to(
        "/settings?section=categories",
        f"/categories/{DIVIDEND_CATEGORY_ID}/edit",
    )
    op.sheet.typed(f"category {DIVIDEND_CATEGORY_ID}: Group",
                   DIVIDEND_CATEGORY[0])
    op.sheet.typed(f"category {DIVIDEND_CATEGORY_ID}: Item Name",
                   DIVIDEND_CATEGORY[1])
    op.submit(form, {
        "group_name": DIVIDEND_CATEGORY[0], "item_name": DIVIDEND_CATEGORY[1],
    })
    category_id = _category_id(op.user_id, *DIVIDEND_CATEGORY)
    assert category_id == DIVIDEND_CATEGORY_ID, \
        f"the dividends' category is {category_id}, not the renamed one"
    for row in dividends:
        op.sheet.say(f"- dividend of {row.day}, account {row.account_id}:")
        _record(op, row)


#: What the operator must hold to, on the sheet as well as here.
_STOP_RULES = (
    "STOP RULES. Perform acts 1 to 10 in ONE sitting. Act 1 opens a window in "
    "which the twin books interest that never happened, and act 2 closes it. "
    "Account 10's corrections already stand in, as modelled interest, for "
    "dividends the app never recorded; act 3 enlarges them and act 9 records "
    "the dividends and empties them (both accounts are on every AFTER line). "
    "Act 9's last dividend is counted twice until act 10 asserts Fidelity's "
    "close for that day (R-HM). "
    "Finish each row's saves before the next: between a row's settle and its "
    "day correction it counts on TODAY. Do not stop while account 2 is "
    "unarchived (act 2)."
)


def _equities(export: _Export) -> "dict[int, Decimal]":
    """Return what each restated account's books open holding: its bank's close.

    Args:
        export: The parsed Fidelity export.

    Returns:
        ``{account id: equity}`` for account 10 and Checking.

    Raises:
        SystemExit: When the app's own record of SECU's statement does not
            price Checking's close on BOOKS_OPEN.
    """
    checking_close = statement_import.fold_bank_balances(
        CHECKING_ACCOUNT, [BOOKS_OPEN],
    ).balances.get(BOOKS_OPEN)
    if checking_close is None:
        raise SystemExit(
            f"no imported statement prices Checking's close on {BOOKS_OPEN}"
        )
    return {
        SUBJECT_ACCOUNT: export.closing_on(BOOKS_OPEN),
        CHECKING_ACCOUNT: checking_close,
    }


def _perform(op: _Operator, export: _Export, earlier: int) -> int:
    """Perform the repair in ruling R-BAL3's order, as amended.

    **The order is FORCED at two points, not chosen.**  The books boundary
    refuses a movement dated on or before its account's ``opened_on`` (ruling
    **R-HG**), so nothing can be dated onto BOUNDARY_DAY until the books it
    belongs to open on BOOKS_OPEN -- which is why both restatements (acts 3
    and 4) precede every re-date and the new row (acts 5 to 7).  And transfer
    102's re-date touches BOTH endpoints, so BOTH openings must already be
    restated before that one save; a per-account "restate, then re-date" walk
    is one click from a refusal mid-act.

    **The dividends follow the restatements** (ruling **R-HL**): recorded
    first, the account's later assertions book corrections against its
    ``interest_income`` row, a negative interest income nobody earned, until
    the rest lands.  **The twin is zeroed directly after act 1**, which opens
    the window it closes (:func:`_consolidate_twin`).  The windows that remain
    are on :data:`_STOP_RULES` and measured act by act by :func:`_snapshot`.

    Args:
        op: The owner's session, which carries the performance sheet.
        export: The parsed export, which supplies every Fidelity figure.
        earlier: How many paychecks act 0 adds before the first; ``0`` skips
            it.

    Returns:
        The id act 7 created.  The equities the acts typed are not returned:
        :func:`_verify_post_state` derives them again from the same records,
        which is what lets a performed copy be graded on them too.

    Raises:
        SystemExit: From :func:`_equities`.
    """
    equities = _equities(export)
    unrecorded, *dividends = _new_rows(export)
    lines = {
        entry.line_id: db.session.get(BankStatementLine, entry.line_id)
        for entry in BOUNDARY_LINES
    }

    op.sheet.say("")
    op.sheet.say(_STOP_RULES)
    op.sheet.say("")
    op.sheet.say(f"## Before act 1 (balances valued {export.named[-1]}, the "
                 "last day the export states)")
    _snapshot(export, op.sheet, "BEFORE")

    if earlier:
        op.sheet.act(0, "add earlier paychecks (NOT an R-BAL3 act; measured)",
                     "Settings > Pay periods > Add earlier paychecks")
        _add_earlier_paychecks(op, earlier)
        _snapshot(export, op.sheet)

    op.sheet.act(1, f"drop transfer {DROPPED_TRANSFER}: set it back, then "
                 "cancel it (R-BAL3, R-BAL255)",
                 f"transfer {DROPPED_TRANSFER}'s popover: Status, Save; Cancel")
    _drop_transfer(op, DROPPED_TRANSFER)
    _snapshot(export, op.sheet)

    op.sheet.act(2, f"zero the archived twin (account {TWIN_ACCOUNT}) in ONE "
                 "sitting", f"Accounts > archived > account {TWIN_ACCOUNT}: "
                 "Unarchive; Edit > When the books opened; its balance editor; "
                 "Archive")
    _consolidate_twin(op)
    _snapshot(export, op.sheet)

    op.sheet.act(3, f"restate account {SUBJECT_ACCOUNT}'s books to "
                 f"{BOOKS_OPEN} at Fidelity's close for that day",
                 f"Accounts > account {SUBJECT_ACCOUNT} > Edit > When the books "
                 "opened")
    _restate_opening(op, SUBJECT_ACCOUNT, BOOKS_OPEN, equities[SUBJECT_ACCOUNT])
    _snapshot(export, op.sheet)

    op.sheet.act(4, f"restate Checking's books to {BOOKS_OPEN} at SECU's "
                 "close for that day", f"Accounts > account {CHECKING_ACCOUNT}"
                 " > Edit > When the books opened")
    _restate_opening(op, CHECKING_ACCOUNT, BOOKS_OPEN,
                     equities[CHECKING_ACCOUNT])
    _snapshot(export, op.sheet)

    op.sheet.act(5, f"type transfer {KEPT_TRANSFER}'s day into BOTH sides' "
                 "boxes, each from its own bank (R-BAL3, R-BAL256)",
                 f"transfer {KEPT_TRANSFER}'s popover, one box per side, ONE save")
    kept_line = next(e for e in BOUNDARY_LINES if e.transfer_id == KEPT_TRANSFER)
    _redate_sides(op, KEPT_TRANSFER, {
        CHECKING_ACCOUNT: lines[kept_line.line_id].posted_on,
        SUBJECT_ACCOUNT: FIDELITY_DAYS[KEPT_TRANSFER],
    })
    _snapshot(export, op.sheet)

    op.sheet.act(6, f"re-date Checking's rows onto {BOUNDARY_DAY}, the day "
                 "SECU posted them", "each row's card, Money moved on")
    for entry in BOUNDARY_LINES:
        for row in entry.rows:
            _redate_row(op, row, lines[entry.line_id].posted_on)
    _snapshot(export, op.sheet)

    op.sheet.act(7, f"record bank line {UNRECORDED_LINE}, which no row answers "
                 "(BAL-468)", "Checking's grid: Add Transaction; then the new "
                 "row's card: settle; reopen it: correct the day")
    created = _record(op, unrecorded)
    op.sheet.say(f"- created transaction {created}")
    _snapshot(export, op.sheet)

    # Acts 8 to 10 are worked from account 10's own grid, which only the
    # Default Grid Account setting opens (:func:`_set_grid_account`); the
    # owner's own setting is put back after act 10.
    owners_grid = _grid_account(op.user_id)
    op.sheet.act(8, f"type Fidelity's day into account {SUBJECT_ACCOUNT}'s box "
                 f"on the {len(FIDELITY_DAYS) - 1} other transfers (R-BAL256)",
                 f"first Settings > General: put the grid on account "
                 f"{SUBJECT_ACCOUNT}; then each transfer's popover from that "
                 f"grid, account {SUBJECT_ACCOUNT}'s box only")
    _set_grid_account(op, SUBJECT_ACCOUNT)
    _type_fidelity_days(op)
    _snapshot(export, op.sheet)

    op.sheet.act(9, "rename the empty category, then record the dividends the "
                 "app has never held (R-HL, R-BAL250)",
                 f"Settings > Categories > category {DIVIDEND_CATEGORY_ID}'s "
                 f"pencil; then account {SUBJECT_ACCOUNT}'s grid: Add "
                 "Transaction per dividend, settle, correct the day")
    _record_dividends(op, dividends)
    _snapshot(export, op.sheet)

    last = export.named[-1]
    op.sheet.act(10, f"assert Fidelity's last stated close on {last} (R-HM)",
                 f"account {SUBJECT_ACCOUNT}'s balance editor; then Settings > "
                 "General: the grid back on the account it was on")
    _assert_balance(op, SUBJECT_ACCOUNT, export.closings[last], last)
    _set_grid_account(op, owners_grid)
    _snapshot(export, op.sheet)
    return created


def _verify_class_moves(
    sheet: _Sheet, before: "dict[str, Decimal]",
    expected: "dict[str, Decimal]",
) -> None:
    """Record every class's move and assert the ones the inputs derive.

    Args:
        sheet: The performance sheet.
        before: :func:`_class_totals` captured before the first act.
        expected: :func:`_expected_class_moves`.

    Raises:
        AssertionError: When a derived class moved by anything else.
    """
    after = _class_totals()
    sheet.say("- posted ledger, by class (before -> after, moved):")
    for name in sorted(set(before) | set(after)):
        was = before.get(name, _ZERO_MONEY)
        now = after.get(name, _ZERO_MONEY)
        want = expected.get(name)
        said = "" if want is None else (
            f", expected {want:+}" if now - was == want
            else f", EXPECTED {want:+}"
        )
        sheet.say(f"  - {name}: {was} -> {now}, {now - was:+}{said}")
    for name, want in expected.items():
        moved = after.get(name, _ZERO_MONEY) - before.get(name, _ZERO_MONEY)
        assert moved == want, (
            f"the {name} class moved {moved}, not the {want} this repair's "
            "own inputs derive"
        )


def _verify_ledger(
    export: _Export, sheet: _Sheet, before: "dict[str, Decimal]",
    modelled_before: Decimal,
) -> None:
    """Assert how the acts moved the posted ledger's classes: the PRE-state arm.

    **The one arm that needs the state BEFORE the acts, and so the
    rehearsal's alone.**  A copy graded after a human performed the repair has
    no before; ``--check`` PRINTS the class totals instead, and the runbook
    compares them with the rehearsed copy's, whose before was the same dump.
    Every other arm is :func:`_verify_post_state`'s, which both modes run.

    Neither this nor :func:`_verify_post_state` is the day-by-day comparison
    with the banks -- that is ``measure_cutover_against_bank.py``'s question,
    asked of both accounts by the commands this file's docstring names.  What
    they ask is whether the ACTS landed.

    Args:
        export: The parsed export.
        sheet: The performance sheet.
        before: :func:`_class_totals` captured before the first act.
        modelled_before: The twin's and account 10's ``interest_income``
            totals before the first act, summed.

    Raises:
        AssertionError: When a class the inputs derive moved by anything else.
    """
    db.session.expire_all()
    recorded_expense = -db.session.get(BankStatementLine, UNRECORDED_LINE).amount
    sheet.say("")
    sheet.say("## After the last act: what to check")
    _verify_class_moves(
        sheet, before,
        _expected_class_moves(export, modelled_before, recorded_expense),
    )


class _Arms:
    """The post-state's arms, graded one by one and ALL reported.

    **Every arm runs and every failure is named**, rather than the first one
    raising.  A copy a human performed the repair on is graded once, after
    the fact, and an operator told one failure per run would re-run the check
    once per mistake.  It is also what makes each negative control measure
    ONE arm: a variant with act 8 skipped must fail the Fidelity-sides arm
    and pass every other.
    """

    def __init__(self, sheet: _Sheet):
        """Start with nothing failed.

        Args:
            sheet: Where each arm's line goes.
        """
        self.sheet = sheet
        self.failed: "list[str]" = []

    def fail(self, failure: str) -> None:
        """Record one failed arm.

        Args:
            failure: The sentence naming what failed.
        """
        self.failed.append(failure)
        self.sheet.say(f"- FAILS: {failure}")

    def grade(self, holds: bool, said: str, failure: str) -> None:
        """Record one arm: its line when it holds, its failure when not.

        Args:
            holds: Whether the arm holds.
            said: The sheet's line for a holding arm.
            failure: The sentence naming what failed.
        """
        if holds:
            self.sheet.say(f"- {said}")
        else:
            self.fail(failure)


def _verify_post_state(
    export: _Export, sheet: _Sheet, residue: Decimal, snapshot: _Snapshot,
) -> "int | None":
    """Grade every arm the post-state answers alone: what BOTH modes run.

    The rehearsal runs it after its acts and ``--check`` on a copy a human
    performed the repair on, so the two cannot grade different things: ONE set
    of arms, called by both (rule 14).  None needs the state before the acts,
    which is why a performed copy can answer them; the one that does is
    :func:`_verify_ledger`'s.

    **Four of the arms were asserted only INSIDE the acts until plan step
    X-f3c-2b-2c's check mode** -- the twin archived again, category 33
    renamed, the dividends filed there, act 7's row -- and so graded a
    rehearsal and never a performance.  A twin left unarchived and dividends
    filed under another income category each passed every money comparison
    the runbook made (measured 2026-10-09).  **Act 10's assertion is graded
    here for the same reason**: after act 9 every correction is already
    zero, so no other arm sees act 10 skipped.  **So are the twin's opening
    day and the DAY of its 0.00 assertion** (:func:`_grade_twin`,
    :func:`_grade_assertion`), each found passing every other arm and both
    scores by the reviews of 2026-10-10.  **And every row the acts CREATED is
    one census** (:func:`_grade_created_rows`): those reviews found, three
    rounds running, a duplicate or leftover row passing every arm, each time
    because an arm found the Adds' rows by a field the operator types.  **So
    are the six Checking sides act 8's cards render and the rows the copy
    already held** (:func:`_grade_checking_sides`,
    :func:`_grade_deleted_rows`): Checking's box typed another day, and a
    Delete on a row no act made, each moved money and was named by no line
    until the round that answered review 4 (2026-10-11).

    Args:
        export: The parsed export.
        sheet: Where each arm's line goes.
        residue: BAL-467's residue, books less bank: the figure
            :func:`_reconcile` measured in the rehearsal, the figure the ledger
            row states in ``--check``.
        snapshot: The copy's ids before the first act.

    Returns:
        Act 7's row as :func:`_grade_created_rows` FOUND it, which the
        rehearsal compares with the row it created; ``None`` when no created
        row matches it.

    Raises:
        AssertionError: Naming EVERY arm that failed.
    """
    arms = _Arms(sheet)
    db.session.expire_all()
    subject = db.session.get(Account, SUBJECT_ACCOUNT)
    last = export.named[-1]
    context = BalanceContext.build(user_id=subject.user_id, as_of=last)
    _grade_twin(arms, context, last)
    _grade_dropped_transfer(arms)
    _grade_category(arms, snapshot, subject.user_id)
    _grade_assertion(
        arms, SUBJECT_ACCOUNT, export.closings[last], last,
        "act 10, Fidelity's close",
    )
    _grade_openings(arms, export)
    _grade_fidelity_sides(arms)
    _grade_checking_sides(arms)
    subject_books = cash_balance_at(subject, context, BOUNDARY_DAY)
    arms.grade(
        subject_books == export.closing_on(BOUNDARY_DAY),
        f"account {SUBJECT_ACCOUNT} on {BOUNDARY_DAY}: books {subject_books}, "
        f"Fidelity {export.closing_on(BOUNDARY_DAY)}",
        f"account {SUBJECT_ACCOUNT}'s books read {subject_books} on "
        f"{BOUNDARY_DAY}, Fidelity {export.closing_on(BOUNDARY_DAY)}",
    )
    created = _grade_created_rows(arms, export, snapshot, context)
    _grade_deleted_rows(arms, snapshot)
    _grade_checking_boundary(arms, context, residue, created)
    if arms.failed:
        raise AssertionError(
            f"{len(arms.failed)} arm(s) of the post-state fail:\n  "
            + "\n  ".join(arms.failed)
        )
    return created


def _grade_twin(arms: _Arms, context: BalanceContext, last: date) -> None:
    """Grade act 2: the twin zeroed on its own days, archived, windows closed.

    Args:
        arms: The arms so far.
        context: The verification's read pass.
        last: The export's last day, the valuation day.
    """
    twin_account = db.session.get(Account, TWIN_ACCOUNT)
    twin = balance_at(twin_account, context, last)
    arms.grade(twin == _ZERO_MONEY, f"account {TWIN_ACCOUNT} holds {twin}",
               f"the twin still holds {twin}")
    arms.grade(
        not twin_account.is_active, f"account {TWIN_ACCOUNT} is archived",
        f"account {TWIN_ACCOUNT} is not archived: act 2 ends by archiving it",
    )
    opening = cash_ledger.governing_account_opening(TWIN_ACCOUNT)
    arms.grade(
        (opening.opened_on, opening.opening_equity)
        == (TWIN_OPENED_ON, _ZERO_MONEY),
        f"account {TWIN_ACCOUNT}'s books open {opening.opened_on} holding "
        f"{opening.opening_equity}",
        f"account {TWIN_ACCOUNT} opens {opening.opened_on} at "
        f"{opening.opening_equity}, where act 2 restates it to "
        f"{_ZERO_MONEY} on its own day, {TWIN_OPENED_ON}",
    )
    _grade_assertion(
        arms, TWIN_ACCOUNT, _ZERO_MONEY, TWIN_ASSERTED_ON, "act 2, step 3",
    )
    for account_id in (TWIN_ACCOUNT, SUBJECT_ACCOUNT):
        corrections = _account_trueup_total(account_id)
        modelled = _interest_income_total(account_id)
        arms.grade(
            (corrections, modelled) == (_ZERO_MONEY, _ZERO_MONEY),
            f"account {account_id}'s corrections total {corrections}, its "
            f"modelled interest row {modelled}",
            f"account {account_id}'s posted corrections total {corrections} "
            f"and its modelled interest row {modelled}: an assertion is not "
            "explained by the records",
        )


def _grade_dropped_transfer(arms: _Arms) -> None:
    """Grade act 1: the dropped transfer Cancelled.

    A transfer is one row: plan step ``balance:X-bi-6-4d-2`` deleted its twin
    rows and ``balance:X-bi-6-5`` dropped the transaction's transfer column,
    so the transfer's own status is the whole state.

    Args:
        arms: The arms so far.
    """
    cancelled = ref_cache.status_id(StatusEnum.CANCELLED)
    status_id = db.session.get(Transfer, DROPPED_TRANSFER).status_id
    arms.grade(
        status_id == cancelled,
        f"transfer {DROPPED_TRANSFER} is Cancelled",
        f"transfer {DROPPED_TRANSFER} holds status {status_id}, not "
        "Cancelled: was the card's grey Cancel pressed?",
    )


def _grade_category(arms: _Arms, snapshot: _Snapshot, user_id: int) -> None:
    """Grade act 9's rename: category 33 renamed, and no older row moved in.

    **The rows act 9 CREATED are :func:`_grade_created_rows`', graded there
    once.**  What is left for the category is its name and the rows the copy
    already held: category 33 held nothing before act 9 (the rehearsal of the
    same dump refuses to start otherwise, :func:`_repair_markers`), so a row
    the snapshot holds that is filed there now was moved in by an edit, which
    no act makes -- a category picked by mistake on a card an act opens.

    Args:
        arms: The arms so far.
        snapshot: The copy's ids before the first act.
        user_id: The owner.
    """
    category = db.session.get(Category, DIVIDEND_CATEGORY_ID)
    named = None if category is None or category.user_id != user_id else (
        category.group_name, category.item_name,
    )
    arms.grade(
        named == DIVIDEND_CATEGORY,
        f"category {DIVIDEND_CATEGORY_ID} is named {DIVIDEND_CATEGORY[0]}: "
        f"{DIVIDEND_CATEGORY[1]}",
        f"category {DIVIDEND_CATEGORY_ID} is {named}, not the owner's "
        f"{DIVIDEND_CATEGORY[0]}: {DIVIDEND_CATEGORY[1]}: act 9 renames it",
    )
    moved_in = sorted(db.session.execute(
        db.select(Transaction.id).filter(
            Transaction.category_id == DIVIDEND_CATEGORY_ID,
            Transaction.is_deleted.is_(False),
            Transaction.id.in_(snapshot.transactions),
        )
    ).scalars())
    arms.grade(
        not moved_in,
        f"category {DIVIDEND_CATEGORY_ID} holds no row the copy held before "
        "act 1",
        f"category {DIVIDEND_CATEGORY_ID} holds transactions {moved_in}, which "
        "the copy held before act 1, when it held nothing: an edit moved them "
        "in, and no act does. STOP and report it",
    )


def _counted(txns: list) -> "list[tuple[Transaction, _Moved | None]]":
    """Pair each row with what its own account's cash fold counts for it.

    Args:
        txns: Transactions, on any accounts.

    Returns:
        ``(transaction, _Moved)`` per row, ``None`` where the fold counts no
        movement for it; each account's fold is walked once.
    """
    folds: "dict[int, dict]" = {}
    paired = []
    for txn in txns:
        if txn.account_id not in folds:
            folds[txn.account_id], _ = _moved_by(_facts(txn.account_id))
        paired.append((txn, folds[txn.account_id].get(txn.id)))
    return paired


class _Slot(NamedTuple):
    """One row :func:`_new_rows` names, as the census expects to find it.

    Attributes:
        name: How a failure names it: act 7's row, or a dividend by its day.
        row: The row.
        period: The paycheck holding its day, which it is filed in.
        counted: What a settled row of it holds -- account, category, name,
            paycheck, and its account's fold's day, figure and movement count.
        typed: What an unsettled row of it holds -- account, category, name,
            paycheck, type and amount.
    """

    name: str
    row: _NewRow
    period: object
    counted: tuple
    typed: tuple


def _slots(export: _Export) -> "list[_Slot]":
    """Return :func:`_new_rows` as the census expects each, act 7's first.

    Args:
        export: The parsed export.

    Returns:
        One slot per row.
    """
    slots = []
    for index, row in enumerate(_new_rows(export)):
        period = _period_holding(row.account_id, row.day)
        same = (row.account_id, row.category_id, row.name, period.period_id)
        slots.append(_Slot(
            "act 7's row" if index == 0 else f"act 9's dividend of {row.day}",
            row, period,
            same + (
                row.day,
                row.amount if row.kind is TxnTypeEnum.INCOME else -row.amount,
                1,
            ),
            same + (ref_cache.txn_type_id(row.kind), row.amount),
        ))
    return slots


def _created_rows(snapshot: _Snapshot) -> "tuple[list[Transaction], int]":
    """Return the live rows the snapshot does not hold, less the window's.

    **The pay-period window's rows are told apart by what MADE them**: a row
    filed in a paycheck the snapshot does not hold, from a RECURRING
    definition.  The rolling window tops up on the owner's first grid or
    dashboard load after a payday and fills only the paychecks it appends
    (``routes/_period_population.py``), so these appear when the dump was
    taken before that load: measured 2026-10-10 on a copy one paycheck short,
    rows from the owner's recurring items, one transfer's two sides among
    them.  The Add modal gives each row a one-off definition of its own, so
    no typed row is set aside here, whatever paycheck it is filed in.

    Args:
        snapshot: The copy's ids before the first act.

    Returns:
        Every other created row, by id; and how many the window made.
    """
    created = db.session.query(Transaction).filter(
        Transaction.is_deleted.is_(False),
        Transaction.id.not_in(snapshot.transactions),
    ).order_by(Transaction.id).all()
    typed = [
        txn for txn in created
        if txn.pay_period_id in snapshot.pay_periods or not txn.recurs
    ]
    return typed, len(created) - len(typed)


def _match(
    slots: "list[_Slot]", typed: "list[Transaction]",
) -> "tuple[dict[tuple, int], list[tuple[Transaction, _Moved | None]]]":
    """Match each created row to the slot its fold and fields fill, once.

    Args:
        slots: :func:`_slots`.
        typed: :func:`_created_rows`' rows, by id.

    Returns:
        ``{slot's counted key: the first row that fills it}``, and every row
        that fills none, with what its fold counts for it.
    """
    owed = Counter(slot.counted for slot in slots)
    matched: "dict[tuple, int]" = {}
    stray = []
    for txn, moved in _counted(typed):
        held = None if moved is None else (
            txn.account_id, txn.category_id, txn.name, txn.pay_period_id,
            moved.day, moved.delta, moved.movements,
        )
        if held is not None and owed[held]:
            owed[held] -= 1
            matched.setdefault(held, txn.id)
        else:
            stray.append((txn, moved))
    return matched, stray


def _described(txn: Transaction) -> str:
    """Return how a failing line names one row: what the operator can find.

    Args:
        txn: The row.

    Returns:
        Its name, account, category and the paycheck it is filed in.
    """
    column = _period_holding(txn.account_id, txn.pay_period.start_date)
    return (
        f"{txn.name!r} on account {txn.account_id} in category "
        f"{txn.category_id}, in the paycheck {column.label}"
    )


def _stray_line(
    txn: Transaction, moved: "_Moved | None", unfinished: "dict[tuple, _Slot]",
    context: BalanceContext,
) -> "tuple[str, bool]":
    """Name one created row no slot matched, and its remedy.

    **Only a PROJECTED row is given a remedy to perform**, because the two
    that are prescribed -- **Delete this row**, and a row's two remaining
    saves -- were each measured from Projected (2026-10-10).  A row the fold
    does not count is not therefore unsettled: marked Credit, which act 7's
    Projected card offers, it holds no movement on its own account, and the
    unsettled remedy told the operator to finish saves the card no longer
    offers (adversarial review 2026-10-10).  Every status but Projected and
    Cancelled is named and stops.

    Args:
        txn: The row.
        moved: What its account's fold counts for it, ``None`` for nothing.
        unfinished: The missing slots by :attr:`_Slot.typed`; a row that
            fills one is POPPED from it, so one slot excuses one row.
        context: The verification's read pass, which prices an unsettled
            row: an Add's row stores no amount of its own, its one-off
            definition does (``estimated_amount`` reads ``None`` on it,
            measured 2026-10-10), so its amount is what the app resolves.

    Returns:
        The failure's sentence for it, and whether that sentence says stop
        rather than giving a remedy.
    """
    stops = True
    if moved is not None:
        state = f"counted for {moved.delta} on {moved.day}" + (
            "" if moved.movements == 1 else f" in {moved.movements} movements"
        )
        remedy = (
            "it counts and the sheet does not name it (a duplicate marked Paid "
            "or Received, or a field typed otherwise): STOP and report it; "
            "removing it is not rehearsed"
        )
    elif is_cancelled(txn):
        state = "Cancelled"
        remedy = (
            "a Cancelled row moves no money but no page reaches it: STOP and "
            "report it; the repair is not finished (ruling R-BAL262)"
        )
    elif txn.status_id != ref_cache.status_id(StatusEnum.PROJECTED):
        state = (f"marked {txn.status.name} and counted on no movement of "
                 f"account {txn.account_id}")
        remedy = (
            "no act leaves a row so (marked Credit, for one), and no remedy is "
            "rehearsed: STOP and report it"
        )
    else:
        stops = False
        state = "not settled"
        slot = unfinished.pop((
            txn.account_id, txn.category_id, txn.name, txn.pay_period_id,
            txn.transaction_type_id,
            cash_ledger.resolve_transaction_amount(txn, context.amounts()),
        ), None)
        remedy = (
            "not one of the six rows the sheet names: if it is a second Add "
            "you made in act 7 or act 9, press 'Delete this row' on its card "
            "and never settle it, which counts its money twice; if you did "
            "not make it, STOP and report it"
            if slot is None else
            f"{slot.name} left unfinished: finish its two saves on its card "
            "(Status, then Money moved on)"
        )
    return f"{_described(txn)}, {state}: {remedy}", stops


def _performance_span(
    snapshot: _Snapshot, last: date,
) -> "tuple[datetime, datetime]":
    """Return when this copy's acts 4 to 10 ran, by the database's own clock.

    **An Add is made between the restatements and act 10, and both ends are
    rows the acts APPEND.**  Acts 2 to 4 each append a restatement
    (``budget.account_openings``) and act 10 an assertion
    (``budget.account_anchor_history``); both tables are append-only, and
    the database stamps each row's ``created_at``.  So the span opens at the
    newest restatement of the three accounts -- act 4's, which act 7's Add
    follows -- and closes at the assertion governing the export's last day,
    act 10's.  **The snapshot's capture is a floor**, so a copy on which no
    restatement was made is still bounded by the rehearsal of its dump.

    **A row created outside the span is not an Add's, whatever it holds**:
    it was there before the acts and the snapshot does not hold it (a
    snapshot of an earlier dump, or a row typed between the dump and act 4),
    or it was made after act 10.  :func:`_grade_created_rows` gives such a
    row no remedy, because the remedies it gives are an Add's.  A
    restatement made again later moves the opening later, which only puts
    more rows outside; act 10 asserted again later moves the close later,
    which lets in a row made between the two assertions: it is then named
    as an Add's row would be, and the remedy for a Projected row asks the
    operator whether they made it as a second Add.

    Args:
        snapshot: The copy's ids before the first act, and when.
        last: The export's last day, which act 10 asserts.

    Returns:
        ``(opened, closed)``.  When no assertion governs *last*, ``closed``
        is ``opened`` and no row is inside.
    """
    opened = max(
        snapshot.captured_at,
        db.session.execute(
            db.select(db.func.max(AccountOpening.created_at)).filter(
                AccountOpening.account_id.in_(
                    (CHECKING_ACCOUNT, TWIN_ACCOUNT, SUBJECT_ACCOUNT),
                ),
            )
        ).scalar_one(),
    )
    governing = cash_ledger.governing_anchor_on(SUBJECT_ACCOUNT, last)
    if governing is None:
        return opened, opened
    return opened, db.session.get(
        AccountAnchorHistory, governing.anchor_id,
    ).created_at


def _grade_created_rows(
    arms: _Arms, export: _Export, snapshot: _Snapshot, context: BalanceContext,
) -> "int | None":
    """Grade acts 7 and 9's Adds: EVERY row the snapshot does not hold.

    **An exact census of what the acts created, keyed on the copy before act
    1 and on nothing typed** (:class:`_Snapshot`).  Three rounds of review on
    2026-10-10 each found a duplicate or leftover row passing every arm,
    because the arms found the Adds' rows by a field the operator TYPES -- a
    name, a category, a status -- and a copy with that one field typed
    otherwise was found by none of them (act 7's name with a curly
    apostrophe, a dividend under another income category, each left unpaid
    or settled).  Every live row the snapshot does not hold is the
    pay-period window's (:func:`_created_rows`), one of the six the sheet
    names, matched on everything :attr:`_Slot.counted` holds and each at most
    once, or NAMED (:func:`_stray_line`).

    **A remedy is printed only for a row an Add can have made**: one created
    inside :func:`_performance_span`.  If ANY row to be named was created
    outside it, the line is ONE stop naming those rows and no remedy at all,
    because then the snapshot is not of this copy's dump or the app was
    changed outside the acts, and a remedy read off it would be aimed at the
    owner's own row (adversarial review 2026-10-10: a snapshot one row short
    named the owner's newest row a duplicate Add to delete).  Inside the
    span, a Projected row holding a missing slot's every typed field is that
    row left unfinished, whose two remaining saves finish it; any other
    Projected row is not one of the six, and is removed by **Delete this
    row** only when the operator made it as a second Add, which the line
    asks them, since an owner's row typed mid-repair is the same shape
    (adversarial review 2026-10-11) -- both remedies measured.  A counted
    one, a Cancelled one (ruling
    **R-BAL262**) and one in any other status have no rehearsed remedy, and
    say stop.

    Args:
        arms: The arms so far.
        export: The parsed export.
        snapshot: The copy's ids before the first act.
        context: The verification's read pass.

    Returns:
        Act 7's row as matched, for the boundary census and the rehearsal's
        own comparison; ``None`` when no created row matches it.
    """
    typed, generated = _created_rows(snapshot)
    slots = _slots(export)
    matched, stray = _match(slots, typed)
    opened, closed = _performance_span(snapshot, export.named[-1])
    outside = [txn.id for txn, _ in stray if not opened < txn.created_at < closed]
    if outside:
        problems = [
            f"transactions {outside} were created outside the acts' span "
            "(before the newest restatement of acts 2 to 4, or after act "
            "10's assertion), where no Add of the acts as ruled is made: the "
            "snapshot is not of the dump this copy was taken from, the app "
            "was changed outside the acts, act 10 was skipped, an opening was "
            "restated again after an Add, or an Add was made out of order. "
            "STOP and report it; delete or settle NOTHING on the strength of "
            "this line"
        ]
    else:
        problems = _stray_problems(slots, matched, stray, context)
    arms.grade(
        not problems,
        "every row the snapshot before act 1 does not hold is accounted for: "
        f"the {len(slots)} the sheet names (act 7's and {len(slots) - 1} "
        "dividends), each in its account, category, name and paycheck, "
        f"counted once on its day for its figure; and {generated} the "
        "pay-period window filled into paychecks it added",
        "rows the snapshot before act 1 does not hold: " + "; ".join(problems),
    )
    return matched.get(slots[0].counted)


def _stray_problems(
    slots: "list[_Slot]", matched: "dict[tuple, int]",
    stray: "list[tuple[Transaction, _Moved | None]]", context: BalanceContext,
) -> "list[str]":
    """Name every created row no slot matched, and every slot none filled.

    A slot whose row is named unfinished is not named missing as well: one
    line, and its remedy, per row.  **A remedy is not to be performed beside
    a stop**: with a dividend settled onto the wrong day (stop) and a
    Projected copy of it left (finish its saves), the finish would count that
    dividend twice.  So when any row says stop and another gives a remedy,
    the line ends by withholding every remedy on it.

    Args:
        slots: :func:`_slots`.
        matched: :func:`_match`'s filled slots.
        stray: :func:`_match`'s unmatched rows, each created inside the
            performance (:func:`_performance_span`).
        context: The verification's read pass.

    Returns:
        One sentence per row or slot, empty when every slot is filled once
        and no row is left.
    """
    missing = [slot for slot in slots if slot.counted not in matched]
    unfinished = {slot.typed: slot for slot in missing}
    excusable = {slot.name for slot in unfinished.values()}
    named = [
        _stray_line(txn, moved, unfinished, context) for txn, moved in stray
    ]
    problems = [sentence for sentence, _ in named]
    if len({stops for _, stops in named}) == 2:
        problems.append(
            "a row on this line says STOP, so perform NONE of the remedies on "
            "it: stop and report it"
        )
    excused = excusable - {slot.name for slot in unfinished.values()}
    problems.extend(
        f"{slot.name} is missing: {slot.row.name!r} on account "
        f"{slot.row.account_id} in category {slot.row.category_id}, in the "
        f"paycheck {slot.period.label}, counted once for {slot.counted[5]} on "
        f"{slot.row.day}"
        for slot in missing if slot.name not in excused
    )
    return problems


def _grade_deleted_rows(arms: _Arms, snapshot: _Snapshot) -> None:
    """Grade that every row the copy held live before act 1 is live still.

    **No act deletes a row the copy already held** (measured 2026-10-11 on a
    whole rehearsal: none of the dump's live rows was deleted), so one that
    is gone was deleted by a press no act makes -- a **Delete this row**
    aimed at the wrong card.  That moves money, and before this arm the only
    sign of it was an unnamed ``BALANCES DIFFER``.  A one-off's Delete
    removes the row and a recurring occurrence's marks it deleted, so both
    are read.

    Args:
        arms: The arms so far.
        snapshot: The copy's ids before the first act.
    """
    held = snapshot.transactions - snapshot.deleted
    live = set(db.session.execute(
        db.select(Transaction.id).filter(
            Transaction.id.in_(held), Transaction.is_deleted.is_(False),
        )
    ).scalars())
    lost = sorted(held - live)
    still = {
        txn.id: txn for txn in db.session.query(Transaction).filter(
            Transaction.id.in_(lost),
        )
    }
    arms.grade(
        not lost,
        "every row the copy held before act 1 is still there: no act deletes "
        "one",
        "rows the copy held before act 1 are deleted: " + "; ".join(
            f"transaction {txn_id}, " + (
                _described(still[txn_id]) if txn_id in still
                else "no longer in the copy"
            )
            for txn_id in lost
        ) + ". No act deletes a row the copy already held, so a Delete was "
        "pressed on one, which moves money: STOP and report it",
    )


def _grade_assertion(
    arms: _Arms, account_id: int, balance: Decimal, observed_on: date,
    act: str,
) -> None:
    """Grade one assertion an act makes: the one GOVERNING its day holds it.

    Acts 2 and 10 each assert a balance on a stated day.  **Asked of the
    assertion governing THAT day**, so the same figure typed under another
    day fails: the older assertion still governs the stated one.  For the
    twin's zero typed as of a LATER day no other arm sees it, because the
    zero governs every day after either way (a one-digit slip measured
    passing every line and both scores by adversarial review 2026-10-10);
    for act 10, after act 9 every correction is already zero, so no other
    arm sees the act at all.

    Args:
        arms: The arms so far.
        account_id: The account asserted on.
        balance: What the act asserts.
        observed_on: The day it asserts it for.
        act: The act that asserts it, and what the figure is; it leads the
            line.
    """
    governing = cash_ledger.governing_anchor_on(account_id, observed_on)
    held = None if governing is None else (
        governing.balance, governing.observed_on,
    )
    arms.grade(
        held == (balance, observed_on),
        f"{act}: account {account_id} asserts {balance} on {observed_on}",
        f"{act}: account {account_id}'s assertion governing {observed_on} is "
        f"{'none' if held is None else f'{held[0]} on {held[1]}'}, not "
        f"{balance} on that day",
    )


def _grade_openings(arms: _Arms, export: _Export) -> None:
    """Grade acts 3 and 4: both books open on BOOKS_OPEN at their banks' closes.

    **On a performed copy this grades the TYPING.**  In the rehearsal it
    cannot fail -- the act typed each figure from the same records
    :func:`_equities` derives it from again here -- but an owner typing from
    the sheet can mistype, and this is the arm that says so.

    Args:
        arms: The arms so far.
        export: The parsed export.
    """
    for account_id, equity in _equities(export).items():
        opening = cash_ledger.governing_account_opening(account_id)
        arms.grade(
            (opening.opened_on, opening.opening_equity) == (BOOKS_OPEN, equity),
            f"account {account_id}'s books open {opening.opened_on} holding "
            f"{opening.opening_equity}",
            f"account {account_id} opens {opening.opened_on} at "
            f"{opening.opening_equity}, where its bank closed {BOOKS_OPEN} at "
            f"{equity}",
        )


def _grade_fidelity_sides(arms: _Arms) -> None:
    """Grade acts 5 and 8: every Fidelity side records Fidelity's day as its own.

    Args:
        arms: The arms so far.
    """
    astray = {}
    for transfer_id, day in FIDELITY_DAYS.items():
        side = _side_day(transfer_id, SUBJECT_ACCOUNT)
        if not (side is not None and side.day == day and is_evidence(side)):
            astray[transfer_id] = side
    arms.grade(
        not astray,
        f"all {len(FIDELITY_DAYS)} Fidelity sides record Fidelity's day as "
        "their own",
        "; ".join(
            f"transfer {transfer_id}'s Fidelity side records "
            + ("no movement" if side is None
               else f"{side.day} as {side.basis.value!r}")
            + f", not {FIDELITY_DAYS[transfer_id]} as its own"
            for transfer_id, side in astray.items()
        ),
    )


def _grade_checking_sides(arms: _Arms) -> None:
    """Grade act 8's other box: every Checking side still on SECU's day.

    Args:
        arms: The arms so far.
    """
    astray = _checking_sides_astray()
    arms.grade(
        not astray,
        f"the {len(FIDELITY_DAYS) - 1} other transfers' Checking sides still "
        "record the day SECU's import observed",
        "; ".join(
            f"transfer {transfer_id}'s Checking side records "
            + ("no movement" if side is None
               else f"{side.day} as {side.basis.value!r}")
            + f", not {FIDELITY_DAYS[transfer_id]} as SECU's import observed "
            "it: act 8 types account 10's box only and leaves Checking's as "
            "rendered"
            for transfer_id, side in astray.items()
        ),
    )


def _grade_checking_boundary(
    arms: _Arms, context: BalanceContext, residue: Decimal,
    created: "int | None",
) -> None:
    """Grade acts 4 to 7: Checking's boundary holds exactly what the map names.

    Every Checking movement dated before the next day SECU posted anything is
    a mapped row, act 7's row, or the kept transfer's side -- all on
    BOUNDARY_DAY -- and on BOUNDARY_DAY the books stand off SECU's close by
    BAL-467's residue and nothing else.  The census half is what makes the
    residue mean something: a row left behind, or one moved in from another
    day, changes the set before it can change the gap.  **The level is
    compared on BOUNDARY_DAY alone, deliberately**: the owner asserted a
    balance on the day after, and the cash fold RESETS at an assertion (ruling
    R-S), so a later day's gap would grade the typed figure, not the rows.
    Between BOUNDARY_DAY and SECU's next posted day neither record moves --
    the census says so for the app, :func:`_next_bank_day` for the bank.
    **Keyed on :func:`_movement_key`, as :func:`_checking_problems`' census
    is**: keyed on ``transaction_id``, every transfer side would collapse into
    one ``None`` once transfer legs lose their rows (plan step
    ``balance:X-bi-6-4d``), and a re-rehearsal on that code would read another
    transfer's side as the kept one's (adversarial review, 2026-10-09).

    Args:
        arms: The arms so far.
        context: The verification's read pass.
        residue: BAL-467's residue, books less bank.
        created: Act 7's row, or ``None`` when :func:`_grade_created_rows`
            matched none -- the census then names whatever stands there.
    """
    next_day = _next_bank_day()
    in_window = {
        (_movement_key(fact), fact.settled_on)
        for fact in _facts(CHECKING_ACCOUNT) if fact.settled_on < next_day
    }
    rows = set(MAPPED_ROWS)
    if created is not None:
        rows.add(created)
    wanted = {(("row", txn), BOUNDARY_DAY) for txn in rows} | {
        (("transfer", KEPT_TRANSFER), BOUNDARY_DAY),
    }
    books = cash_balance_at(
        db.session.get(Account, CHECKING_ACCOUNT), context, BOUNDARY_DAY,
    )
    bank = statement_import.fold_bank_balances(
        CHECKING_ACCOUNT, [BOUNDARY_DAY],
    ).balances[BOUNDARY_DAY]
    problems = []
    if in_window != wanted:
        problems.append(
            f"Checking's movements before {next_day} are {sorted(in_window)}; "
            f"the map names {sorted(wanted)}"
        )
    if books - bank != residue:
        problems.append(
            f"Checking's books read {books} on {BOUNDARY_DAY} against SECU's "
            f"{bank}: {books - bank}, where BAL-467's residue is {residue}"
        )
    arms.grade(
        not problems,
        f"Checking on {BOUNDARY_DAY}: books {books}, SECU {bank}, apart by "
        f"BAL-467's residue {residue} and nothing else; every Checking "
        f"movement before {next_day} is one of the {len(wanted)} the map "
        "names",
        "; ".join(problems),
    )


def _connect(clone: str, config_name: "str | None" = None):
    """Build an app pointed at *clone*, refusing anything it is not.

    **A money-moving harness names its own target.**  ``DATABASE_URL`` is an
    environment variable and an environment variable is inherited: a shell that
    last exported the dev runtime's URL would silently rehearse against a
    database other sessions are working in, and ``shekel`` is what BOTH the
    deployed database and that runtime are called.  So the target is a required
    ARGUMENT, checked against the connection the app actually opened, and the
    one name that is never a rehearsal clone is refused outright.

    **The name check is the weaker half and :func:`_require_unrepaired` is the
    stronger**: a name can only refuse the databases somebody thought of, while
    the pre-state refuses every database this repair has already run against,
    whatever it is called.

    Args:
        clone: The database this rehearsal is for.
        config_name: The app configuration, ``None`` for the environment's
            default (:func:`app.create_app`).

    Returns:
        The configured Flask application.

    Raises:
        SystemExit: When *clone* is the shared name, or the connection is not
            to it.
    """
    if clone == "shekel":
        raise SystemExit(
            "'shekel' is the deployed database AND the shared dev runtime; "
            "clone one and rehearse against the copy"
        )
    app = create_app(config_name)
    # CSRF and strong session protection are both disabled for the rehearsal
    # and neither is under test: this forges a session because it has no
    # password for the database it is pointed at, exactly as
    # ``verify_render_surfaces`` does, and strong protection refuses a session
    # whose identifier was not minted inside a request.
    app.config["WTF_CSRF_ENABLED"] = False
    login_manager.session_protection = None
    with app.app_context():
        live = db.session.execute(
            db.text("select current_database()")
        ).scalar_one()
    if live != clone:
        raise SystemExit(
            f"--clone says {clone!r} and DATABASE_URL opened {live!r}"
        )
    return app


#: What every connection of a ``--check`` run is opened with.
_READ_ONLY = "-c default_transaction_read_only=on"


def _connect_read_only(clone: str):
    """Build an app whose every connection is READ-ONLY, for ``--check``.

    **The SERVER enforces it, not this file**: libpq hands ``PGOPTIONS`` to
    the server at connect, and the server then refuses any write in any
    transaction of the session that does not itself ask for ``READ WRITE`` --
    which no app code does (``app/db_transaction.py`` only ever asks for
    ``READ ONLY``; grepped 2026-10-10) -- so a check that wrote would RAISE
    rather than write.  It is set in this process's environment before the
    app opens its engine, appended after anything the shell already set so it
    wins, and
    then READ BACK on the app's own session: a setting nobody reads back is a
    claim, and one a later ``-c`` overrode would be a false one.

    **The app is built with production's CONFIGURATION, because the
    development build WRITES at boot** -- the configuration only: it connects
    as whatever role ``DATABASE_URL`` names, and runs no entrypoint.
    ``create_app`` creates the schemas and seeds the
    reference tables for the development and testing configurations, and the
    first read-only run of this check was refused there, at
    ``CREATE SCHEMA IF NOT EXISTS`` (2026-10-10).  Those writes are idempotent
    on a seeded copy, but "idempotent" is a claim about the copy and
    read-only is a property of the run; production's build leaves both to its
    deploy and writes nothing.

    Args:
        clone: The copy to grade.

    Returns:
        The configured Flask application, every connection read-only.

    Raises:
        SystemExit: From :func:`_connect`, or when the session it opened is
            not read-only after all.
    """
    os.environ["PGOPTIONS"] = " ".join(
        part for part in (os.environ.get("PGOPTIONS"), _READ_ONLY) if part
    )
    app = _connect(clone, "production")
    with app.app_context():
        mode = db.session.execute(
            db.text("show transaction_read_only")
        ).scalar_one()
    if mode != "on":
        raise SystemExit(
            f"--check opened {clone} with transaction_read_only {mode!r}; it "
            "grades a copy only read-only"
        )
    return app


def _export_summary(export: _Export) -> str:
    """Return the line naming the export, which both modes print second.

    Args:
        export: The parsed export.

    Returns:
        Its span and how many dividends the repair records.
    """
    return (f"Fidelity export: {len(export.named)} days, {export.named[0]} "
            f"to {export.named[-1]}; {len(export.recorded_dividends())} "
            "dividends recorded by this repair")


def _rehearse(args: argparse.Namespace) -> None:
    """Reconcile the map, perform the repair, verify, and write the sheet.

    Args:
        args: The parsed command line.
    """
    sheet = _Sheet(_new_file(args.sheet, "--sheet"))
    snapshot_path = _new_file(args.snapshot, "--snapshot")
    export = _Export.read(args.bank)
    app = _connect(args.clone)
    sheet.say(f"# Account-10 repair: performance sheet ({args.clone}, "
              f"written {datetime.now():%Y-%m-%d %H:%M})")
    sheet.say(_export_summary(export))
    # **A run that ends early still records why.**  A refusal raised as one of
    # the two kinds this file raises is written with its sentence; anything
    # else leaves the STOPPED line below, and its traceback is on the console.
    completed = False
    try:
        with app.app_context():
            _require_unrepaired()
            snapshot = _Snapshot.capture()
            snapshot.write(snapshot_path)
            residue = _reconcile(export, sheet, args.residue)
            before = _class_totals()
            modelled_before = sum(
                (_interest_income_total(a) for a in (TWIN_ACCOUNT, SUBJECT_ACCOUNT)),
                _ZERO_MONEY,
            )
            operator = _Operator(app, sheet)
            created = _perform(operator, export, args.add_earlier_paychecks)
            sheet.say("")
            sheet.say(f"{operator.acts} door submissions performed; verifying")
            _verify_ledger(export, sheet, before, modelled_before)
            found = _verify_post_state(export, sheet, residue, snapshot)
            assert found == created, (
                f"the verification found act 7's row as transaction {found}, "
                f"and act 7 created transaction {created}"
            )
        sheet.say("")
        sheet.say("rehearsal complete")
        completed = True
    except (AssertionError, SystemExit) as error:
        sheet.say("")
        sheet.say(f"STOPPED: {error}")
        raise
    finally:
        if not completed and not sheet.lines[-1].startswith("STOPPED"):
            sheet.say("")
            sheet.say("STOPPED before the end: the console shows why")
        sheet.save()


def _check(args: argparse.Namespace) -> None:
    """Grade a copy the repair was PERFORMED on, writing nothing: ``--check``.

    **One instrument for the performance, not a list read off the screen.**
    The runbook's post-performance block compared only MONEY -- the scores and
    the balance dumps -- and a twin left unarchived or dividends filed under
    another income category each passed every one of those comparisons
    (measured 2026-10-09).  A skipped act 8, or act 5's account-10 box left
    alone, passes them too by REASONING, not measurement: each leaves a
    Fidelity side on the day it borrows from Checking, which the map already
    requires to be Fidelity's own, so no figure moves.  This runs the
    rehearsal's own post-state arms (:func:`_verify_post_state`) on the copy
    instead.

    **What only the state BEFORE the acts can grade is PRINTED, not
    asserted**: the posted ledger's class totals (the rehearsal asserts their
    moves) and the Default Grid Account (act 10 puts back the owner's own,
    which no post-state knows).  The runbook diffs this output on the
    performed copy against the same mode's output on the rehearsed copy, so
    nothing a PASSING check prints may vary between two copies on which the
    same acts were performed: no database name, no clock, and no id of a row
    the acts created.  A failing line may name such an id -- the boundary
    census lists the movements it found -- and is a difference anyway.
    **What the acts created is told apart by the SNAPSHOT the rehearsal wrote
    of the same dump before its first act** (``--snapshot``,
    :class:`_Snapshot`), which is why the rehearsed copy and the performed
    one are graded against one set of ids.

    **It writes nothing, and that is measured twice**: every connection is
    read-only at the server (:func:`_connect_read_only`), and the posted
    ledger's fingerprint is equal before and after.  It refuses the name
    ``shekel`` (:func:`_connect`) and a copy no act has touched
    (:func:`_require_repaired`).

    Args:
        args: The parsed command line.

    Raises:
        SystemExit: On a refusal (with its sentence), or ``1`` when an arm
            fails -- every failed arm is then on the output, above the
            ``CHECK FAILED`` line.
    """
    sheet = _Sheet(None)
    export = _Export.read(args.bank)
    snapshot = _Snapshot.read(args.snapshot)
    app = _connect_read_only(args.clone)
    sheet.say("# Account-10 repair: check of a repaired copy (read-only)")
    sheet.say(_export_summary(export))
    failure = None
    with app.app_context():
        _require_repaired()
        fingerprint = _postings_fingerprint()
        owner = db.session.get(Account, SUBJECT_ACCOUNT).user_id
        sheet.say("")
        sheet.say("## What the copy holds")
        sheet.say("- posted ledger, by class (printed, not asserted: the "
                  "rehearsed copy's check is what grades them):")
        for name, total in sorted(_class_totals().items()):
            sheet.say(f"  - {name}: {total}")
        sheet.say("- Settings > General > Default Grid Account: "
                  f"{_grid_choice(_grid_account(owner))}")
        try:
            _verify_post_state(export, sheet, args.residue, snapshot)
        except AssertionError as error:
            failure = error
        if _postings_fingerprint() != fingerprint:
            raise SystemExit("the check MOVED the posted ledger")
    sheet.say("- the check wrote nothing: its session was read-only and the "
              "posted ledger is unchanged")
    sheet.say("")
    if failure is not None:
        sheet.say(f"CHECK FAILED: {failure}")
        raise SystemExit(1)
    sheet.say("check complete")


def main() -> None:
    """Rehearse the repair on a clone, or ``--check`` a repaired copy."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--clone", required=True,
        help="the copy this run is pointed at: a throwaway clone the "
        "rehearsal writes to, or the repaired copy --check reads",
    )
    parser.add_argument(
        "--bank", required=True,
        help="path to the Fidelity history CSV export",
    )
    parser.add_argument(
        "--sheet",
        help="where to write the performance sheet (outside every checkout); "
        "the rehearsal's, never --check's",
    )
    parser.add_argument(
        "--residue", required=True, type=Decimal,
        help="the payroll residue ledger row BAL-467 states, books less bank",
    )
    parser.add_argument(
        "--snapshot", required=True,
        help="the copy's row and paycheck ids before act 1: the rehearsal "
        "WRITES it (a new file outside every checkout), --check READS it. "
        "Hand --check the one the rehearsal of the copy's own dump wrote: "
        "given another dump's, a row the copy holds and it does not is named "
        "as made outside the acts, with no remedy, and a row it holds that "
        "the copy lacks is named as deleted",
    )
    parser.add_argument(
        "--add-earlier-paychecks", type=int, default=0, metavar="N",
        help="perform 'Add earlier paychecks' for N first (act 0; measured)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="grade a copy the repair was PERFORMED on, read-only, instead of "
        "rehearsing it",
    )
    args = parser.parse_args()
    if args.check:
        if args.sheet is not None or args.add_earlier_paychecks:
            parser.error("--check writes nothing: no --sheet, no "
                         "--add-earlier-paychecks")
        _check(args)
    else:
        if args.sheet is None:
            parser.error("the rehearsal needs --sheet")
        _rehearse(args)


if __name__ == "__main__":
    main()
