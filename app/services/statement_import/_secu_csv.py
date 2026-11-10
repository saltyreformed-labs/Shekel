"""The SECU checking CSV adapter -- one source, read into the shared shape.

Ruling **R-FP**'s file path.  It turns State Employees' Credit Union's own
transaction export into :class:`~._line.StatementLine` values and does nothing
else: no database, no clock, no request, no matching.

**Why this format and not the OFX**, measured on the developer's own exports
2026-08-16 and recorded here because the answer is not obvious:

* SECU publishes the same statement as OFX, QFX, QBO and CSV.  The first three
  are the SAME file -- 342 identical ``STMTTRN`` blocks, differing only by two
  Intuit routing tags -- so the choice is really CSV or OFX.
* The OFX truncates every description to the OFX ``NAME`` limit: 326 of 361
  lines land at exactly 32 characters, which renders a card purchase as
  ``POINT OF SALE DEBIT L340 DATE 12`` with no merchant in it at all.  The CSV
  carries up to 96 characters including the merchant, and the CSV description
  STARTS WITH the OFX name on 306 of 306 shared lines -- the same statement,
  one of them cut short.
* The CSV carries the bank's own category and, where the export offers it, a
  per-line RUNNING BALANCE.  The OFX carries neither.  **SECU stopped offering
  that column** between the developer's 2026-07-19 and 2026-08-16 pulls, and
  the boundary is exactly there: the 07-19 pull on disk carries ``Running
  Balance`` (and a ``Daily Balance`` twin of the same statement), while every
  pull from 2026-08-16 onward carries no balance column at all -- their header
  is ``Date, Account, Account Number, Account Type, Description, Check #,
  Category, Memo, Credit, Debit``.  Both shapes are therefore live on this
  developer's disk, which is why the column is READ where present and
  PRESCRIBED nowhere (plan step ``bank_import:X-gc``).  **A first census for
  that step looked only in the top level of ``~/Downloads`` and reported that
  no export carried the column at all**; the four chained files are one
  directory down.
* The OFX's one advantage is ``FITID``.  It buys nothing measurable: the
  positional identity key reproduced the ``FITID`` key exactly across two
  exports twelve days apart (:func:`~._line.group_key`).

**Columns are bound by HEADER NAME, never by position, and that is a measured
requirement rather than defensive style.**  SECU offers two balance options and
they occupy the SAME column index with DIFFERENT meanings: index 10 reads
``Running Balance`` in one export on disk and ``Daily Balance`` in another,
where the latter is populated only on each day's last line.  A positional read
silently reinterprets one as the other -- and on a span whose days each hold a
single transaction it would pass every check while meaning something else.
:func:`_bind_columns` refuses ``Daily Balance`` by name and names an export
this adapter CAN read.

**The file states itself twice more, and BOTH are required here.**  A CSV export
ends with a TOTALS row -- ``Category`` reading ``Totals:``, a ``Memo`` reading
``N items``, and the credit and debit sums.  It is both a trap and a gift.  The
trap: it parses as a transaction and would import as a fabricated
``+$43,597.96`` / ``-$43,213.56`` line dated nowhere.  The gift: it grades the
parse.  **This adapter REFUSES a file without it**, because it is the only
cross-check that survives on a file carrying no running balance -- which is
every export SECU has published since 2026-08-16 -- and
it is the last row of the file, which is exactly what a truncated or interrupted
download loses.  All six of the developer's real exports carry it.

What is NOT checked, and why: the file's own ``Balance as of`` header.  It was
measured to lag the line list -- on the 2026-08-16 export SECU reported
``$4,747.63``, which is 2026-08-13's closing balance, while the same statement
listed two 2026-08-14 lines worth ``-$1,006.72``.  A gate on a figure the bank
computes at a different instant from the lines would refuse honest files.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.utils.money import MoneyTextError, money_from_text

from ._errors import StatementParseError
from ._line import ParsedStatement, StatementLine

#: The header names this adapter binds, and whether each is required.  Bound by
#: NAME (see the module docstring); a header missing a required name is a file
#: this adapter cannot read, and saying which name is missing is what makes that
#: actionable.
_REQUIRED_COLUMNS = (
    "Date", "Account", "Account Number", "Description", "Category", "Memo",
    "Credit", "Debit",
)

#: The small set that IDENTIFIES the header row among the file's preamble.
#: Deliberately narrower than :data:`_REQUIRED_COLUMNS`: a file missing one of
#: the columns this adapter needs should be refused BY NAME
#: (:func:`_bind_columns`), not reported as having no header at all.
_HEADER_ANCHOR = frozenset({"Date", "Description", "Credit", "Debit"})

#: The per-line balance column, present only when the export option is ticked.
_RUNNING_BALANCE = "Running Balance"

#: SECU's OTHER balance option, which sits at the same index and means
#: something else: a day's closing balance, written only on that day's last
#: line.  Refused by name.
_DAILY_BALANCE = "Daily Balance"

#: What the trailing summary row puts in its category cell.
_TOTALS_MARKER = "Totals:"

#: SECU writes dates as US month/day/year.
_DATE_FORMAT = "%m/%d/%Y"

#: The PREAMBLE line stating the bank's own balance, above the transaction
#: header: ``Balance as of 08/20/2026,2501.310000``.  Both of the developer's
#: exports carry it, one quoted and one not, which ``csv.reader`` levels out
#: before this pattern sees it.
#:
#: **Read, recorded, and never gated on.**  The module docstring above records
#: the measurement that forbids a gate -- the figure can LAG its own file -- and
#: nothing here changes that.  What it buys is the only cross-check available
#: to a file carrying no running-balance column: the bank's claim about the
#: account, set beside the owner's own asserted anchor.
#:
#: **The day is captured LOOSELY and refused by the date parser**, not matched
#: strictly here.  A strict ``\d{2}/\d{2}/\d{4}`` made the two failure modes
#: indistinguishable: ``Balance as of 8/2/2026`` -- which ``strptime`` accepts
#: everywhere else in this adapter -- simply did not match, so a header the file
#: plainly states was reported as no header at all.  That is the silent drop
#: this function's own docstring refuses.
_STATED_BALANCE = re.compile(r"^Balance as of\s+(\S.*?)\s*$")

#: The transaction day SECU states INSIDE a card line's description, as
#: ``DATE MM-DD``.  It is a delimited token the bank concatenates into a text
#: column, not prose to be interpreted: measured over the developer's own
#: 2026-08-16 export, 182 of 361 lines carry exactly one, every one of them a
#: ``POINT OF SALE`` line, and NO line carries two.  The year is absent, which
#: is what :func:`_stated_transaction_day` exists to resolve.
_STATED_DAY = re.compile(r"\bDATE (\d{2})-(\d{2})\b")

#: The merchant SECU appends in PARENTHESES at the end of a description cell,
#: as a delimited trailing token exactly like the ``DATE MM-DD`` field above
#: and better covered: it is present on **361 of 361** of the developer's lines
#: where the stated day is on 182.  Anchored at the end of the cell, so an
#: earlier ``(...)`` inside the bank's own wording is not a candidate and there
#: is never more than one.  The 100 matches
#: ``budget.merchants.name``'s own width; the longest the developer's
#: export carries is 28 (``Department of motor vehicles``).
_MERCHANT = re.compile(r"\(([^()]{1,100})\)\s*$")

#: The largest file this adapter will read, in lines.  The developer's own
#: full year-to-date export is 361; a decade of weekly activity is under 6,000.
#: It exists because one import writes one row per line AND one audit row per
#: line, so an unbounded file is an unbounded write amplified twice.
MAX_LINES = 20_000


@dataclass(frozen=True)
class SecuTotals:
    """The file's own trailing summary, used to grade the parse.

    Attributes:
        item_count: How many transactions the file says it holds, read from
            the summary's ``N items`` memo.
        credit_total: The file's stated sum of money in.
        debit_total: The file's stated sum of money out, already negative.
    """

    item_count: int
    credit_total: Decimal
    debit_total: Decimal


def _money(raw: str, label: str) -> Decimal:
    """Return *raw* as cents, refusing anything this app cannot record.

    The CSV's own cleaning -- SECU writes ``$`` and thousands separators --
    over :func:`~app.utils.money.money_from_text`, the ONE walk from a
    source's text to a recorded figure (plan step ``bank_import:X-f6b-2``).
    That walk keeps the arms this function grew here: the figure built from
    the string and never a float; a quiet ``NaN`` refused, because it raises
    nowhere on its own and, committed, made every later render of the page
    500 (adversarial robustness review 2026-08-22); and the rounding through
    the app's one rule INSIDE the refusal, because ``1E+30`` passes the
    finiteness check and raised from ``quantize`` (same review).  SECU's own
    OFX writes six decimal places (``-165.220000``), so the rounding case is
    real.  The walk also refuses a figure past the money columns' width,
    which reached the database here as ``NumericValueOutOfRange``.

    Args:
        raw: The cell's text.
        label: What to call it if it will not parse.

    Returns:
        The value, rounded to cents.

    Raises:
        StatementParseError: When the cell is not a finite number this app
            can record.
    """
    try:
        return money_from_text(raw.strip().replace("$", "").replace(",", ""))
    except MoneyTextError as exc:
        raise StatementParseError(
            f"This file has {label} that is {exc.reason}: {raw!r}.  Nothing "
            f"was imported."
        ) from exc


def _bind_columns(header: "list[str]") -> "dict[str, int]":
    """Return the column index of each header name this adapter reads.

    Binding by NAME is what makes the adapter safe against SECU's two balance
    options, which share an index and mean different things (module docstring).

    Args:
        header: The header row's cells.

    Returns:
        ``{header name: index}`` for every required column, plus
        ``Running Balance`` when the file carries it.

    Raises:
        StatementParseError: When a required column is absent, or when the file
            carries ``Daily Balance`` instead of ``Running Balance``.
    """
    index = {name.strip(): position for position, name in enumerate(header)}
    missing = [name for name in _REQUIRED_COLUMNS if name not in index]
    if missing:
        raise StatementParseError(
            f"This file is missing the column(s) {', '.join(missing)}, so it "
            f"is not a SECU transaction export.  Nothing was imported."
        )
    if _RUNNING_BALANCE not in index and _DAILY_BALANCE in index:
        raise StatementParseError(
            f"This export carries a '{_DAILY_BALANCE}' column, which states "
            f"only each DAY's closing balance and not each line's, so it "
            f"cannot be read as one.  Re-export with no balance column at "
            f"all: a file carrying none imports fine, and its own 'Totals:' "
            f"row still checks the parse.  Nothing was imported."
        )
    bound = {name: index[name] for name in _REQUIRED_COLUMNS}
    if _RUNNING_BALANCE in index:
        bound[_RUNNING_BALANCE] = index[_RUNNING_BALANCE]
    return bound


def _cell(row: "list[str]", columns: "dict[str, int]", name: str) -> str:
    """Return one named cell's text, or ``""`` when the row is short.

    Args:
        row: The row's cells.
        columns: The bound column indexes.
        name: The header name to read.

    Returns:
        The stripped cell text.
    """
    position = columns.get(name)
    if position is None or position >= len(row):
        return ""
    return row[position].strip()


def _row_amount(row: "list[str]", columns: "dict[str, int]") -> Decimal:
    """Return one data row's SIGNED amount.

    SECU fills exactly one of Credit and Debit and signs the debit itself, so
    the amount is whichever is present -- positive INTO the account either way,
    with no inversion to get backwards.

    Args:
        row: A data row's cells.
        columns: The bound column indexes.

    Returns:
        The signed amount.

    Raises:
        StatementParseError: When both columns are filled or neither is.  Both
            being filled is the shape of the TOTALS row, so a data row in that
            state means the summary was not recognised and the parse cannot be
            trusted; neither being filled is a line that moves no money, which
            SECU does not write and this adapter will not invent.
    """
    credit = _cell(row, columns, "Credit")
    debit = _cell(row, columns, "Debit")
    date_text = _cell(row, columns, "Date")
    if credit and debit:
        raise StatementParseError(
            f"A line on {date_text} fills BOTH the credit and the debit "
            f"column ({credit} / {debit}).  That is the shape of the file's "
            f"summary row, so this file was not read correctly.  Nothing was "
            f"imported."
        )
    if not credit and not debit:
        raise StatementParseError(
            f"A line on {date_text} fills NEITHER the credit nor the debit "
            f"column, so it states no amount.  Nothing was imported."
        )
    return _money(credit or debit, "an amount")


def _is_totals_row(row: "list[str]", columns: "dict[str, int]") -> bool:
    """Return whether *row* is the file's trailing summary rather than a line.

    Keyed on the MARKER alone, not on the empty date and not on position: a
    summary that gained a date would otherwise be read as a transaction, which
    is a fabricated ``$43,597.96`` line and the failure this predicate exists
    for.  (An adversarial review measured that keying on the empty date made
    this predicate untestable, because the blank-date skip already caught the
    only shape the test fed it.)

    Args:
        row: A row's cells.
        columns: The bound column indexes.

    Returns:
        True when this is the summary row.
    """
    return _cell(row, columns, "Category") == _TOTALS_MARKER


def _read_totals(row: "list[str]", columns: "dict[str, int]") -> SecuTotals:
    """Return the summary row's three stated figures.

    Args:
        row: The summary row's cells.
        columns: The bound column indexes.

    Returns:
        Its :class:`SecuTotals`.

    Raises:
        StatementParseError: When the item count is not the ``N items`` shape
            the summary is supposed to carry.
    """
    memo = _cell(row, columns, "Memo")
    # Commas stripped because a count crosses 1,000 on a multi-year export and
    # the natural growth path must not become a refusal.
    count_text = memo.split()[0].replace(",", "") if memo else ""
    if not count_text.isdigit():
        raise StatementParseError(
            f"The file's summary row states its size as {memo!r}, which is "
            f"not a count of items.  Nothing was imported."
        )
    return SecuTotals(
        item_count=int(count_text),
        # An EMPTY total is zero, not a parse failure: a span with no credits
        # (or no debits) is an ordinary export, and refusing it would blame the
        # file for the adapter's assumption.
        credit_total=(
            _money(_cell(row, columns, "Credit"), "a credit total")
            if _cell(row, columns, "Credit") else Decimal("0.00")
        ),
        debit_total=(
            _money(_cell(row, columns, "Debit"), "a debit total")
            if _cell(row, columns, "Debit") else Decimal("0.00")
        ),
    )


def _verify_against_totals(lines, totals: SecuTotals) -> None:
    """Refuse *lines* unless they reproduce the file's own summary.

    Three equalities, and each catches something the others do not: the COUNT
    catches a dropped or duplicated row, the CREDIT total catches a sign read
    backwards on money in, and the DEBIT total catches one on money out.

    Args:
        lines: The parsed :class:`~._line.StatementLine` values.
        totals: The file's summary.

    Raises:
        StatementParseError: On any disagreement.
    """
    credit = sum(
        (line.amount for line in lines if line.amount > 0), Decimal("0.00"),
    )
    debit = sum(
        (line.amount for line in lines if line.amount < 0), Decimal("0.00"),
    )
    mismatches = []
    if len(lines) != totals.item_count:
        mismatches.append(
            f"it says {totals.item_count} items and {len(lines)} were read"
        )
    if credit != totals.credit_total:
        mismatches.append(
            f"it says {totals.credit_total} came in and the lines total "
            f"{credit}"
        )
    if debit != totals.debit_total:
        mismatches.append(
            f"it says {totals.debit_total} went out and the lines total "
            f"{debit}"
        )
    if mismatches:
        raise StatementParseError(
            "This file disagrees with its own summary: "
            + "; ".join(mismatches)
            + ".  Nothing was imported."
        )


def _stated_transaction_day(
    description: str, posted_on: date,
) -> "date | None":
    """Return the day SECU states the transaction happened, or ``None``.

    **The bank states it, this does not infer it** -- and the distinction is
    what makes reading a token out of a text column legitimate here where
    "deriving a fact from prose" would not be.  SECU concatenates a fixed
    ``DATE MM-DD`` field into a card line's description, and it is the ONLY
    place either of this bank's formats carries the day a swipe happened: the
    OFX's structured ``DTUSER`` is a copy of ``DTPOSTED`` on 359 of its 361
    lines and is one day LATER on the other two.  So the choice is not between
    a parsed day and a stated one; it is between the stated day and nothing.

    **What it is FOR** (plan step ``bank_import:X-f6a-3a``, ruling **R-FW**): an
    accepted match writes this day onto a matched purchase's ``purchased_on``.
    Writing the POSTING day there instead would record every card purchase as
    having been made on the day it cleared, which is measurably wrong on this
    data -- the stated day is 1 to 4 days earlier on 157 of the 182 lines that
    carry one.

    **Three refusals, and each is a way the token could mean something else:**

    * NO token -- this source states no transaction day for this line, which
      is what ``None`` means and is the honest record for 179 of 361 lines;
    * MORE THAN ONE token -- which one is "the" transaction day would then be
      a guess.  0 of 361 lines carry two; refusing keeps the rule TOTAL rather
      than correct-because-the-data-is-tidy.  **A memo's own date cannot reach
      here at all**, because the caller passes the DESCRIPTION cell rather than
      the ``Description | Memo`` text the row stores;
    * a token whose day lands neither in the posted month nor in the one
      immediately before it.  The token states no YEAR, so its day is only
      unambiguous near the posting day -- this is a statement about the token's
      own ambiguity, not a tolerance on money.  It is what resolves the two
      real ROLLOVERS in the developer's export (posted 2026-01-02, stating
      ``DATE 12-31``, so 2025-12-31) while refusing to read a stale or garbled
      token as a purchase made ten months ago and dragging a matched purchase
      into a closed pay period.

    Args:
        description: The bank's DESCRIPTION cell, verbatim and untruncated --
            not the ``Description | Memo`` text the row stores, and not the
            200-character form.
        posted_on: The day the bank posted the line, which anchors the year.

    Returns:
        The stated civil day, or ``None`` when the bank states none this rule
        can read.  Never after *posted_on*.
    """
    found = _STATED_DAY.findall(description)
    if len(found) != 1:
        return None
    month, day = int(found[0][0]), int(found[0][1])
    # The most recent (month, day) at or before the posting day.  Both years
    # are tried because a January line routinely states a December day, and
    # ``ValueError`` covers 02-29 in a year that has no such day.
    for year in (posted_on.year, posted_on.year - 1):
        try:
            stated = date(year, month, day)
        except ValueError:
            continue
        if stated > posted_on:
            continue
        # In the posted month, or the one immediately before it.
        months_back = (
            (posted_on.year - stated.year) * 12 + posted_on.month - stated.month
        )
        return stated if months_back <= 1 else None
    return None


def _stated_merchant(description: str) -> "str | None":
    """Return the merchant SECU names for this line, or ``None``.

    **The bank names it, this does not infer it** -- the same distinction
    :func:`_stated_transaction_day` rests on, and better covered: SECU appends
    its own normalized merchant in PARENTHESES at the end of the description
    cell on **361 of 361** of the developer's lines, where the stated day is on
    182.  ``... BJS FUEL #9151 25GARNER     NC (BJ's Fuel)`` names ``BJ's
    Fuel``.

    **What it is FOR** (plan step ``bank_import:X-f6a-3d``): it is the KEY a
    merchant DESTINATION POLICY is stated against -- *lines from this merchant
    go in this budget line* -- so it is a fact the adapter records rather than
    a token a reader parses.  It was a reader
    (``statement_match._offers.merchant_of``), which was right while the only
    consumer was a form's name box: a wrong parse cost a badly-named row.  A
    rule that MATCHES on it is a stronger claim, and the reader could not make
    it -- being TOTAL, it fell back to the whole description, and SECU's own
    OFX truncates 326 of 361 descriptions to the same 32 characters, so a
    policy keyed that way would fire on every merchant at once.

    **Read from the DESCRIPTION cell, never the ``Description | Memo`` text the
    row stores**, which is the bound :func:`_stated_transaction_day` learned to
    make structural on 2026-08-18: a memo is the user's own free text and its
    parentheses are indistinguishable from the bank's, so a memo ending
    "(anything)" would become the merchant -- and, now, the key a policy fires
    on.  Reading the cell makes that unreachable rather than guarded against.

    Args:
        description: The bank's DESCRIPTION cell, verbatim and untruncated.

    Returns:
        The merchant, trimmed, or ``None`` when this line names none.  The
        pattern is anchored at the end of the cell, so there is exactly one
        candidate token or none at all -- an earlier ``(...)`` inside the
        bank's own wording is part of the description and is not a candidate.
    """
    found = _MERCHANT.search(description)
    if found is None:
        return None
    # An all-whitespace token is not a name, and here that matters more than it
    # did for a display default: this string is a policy's KEY, and a blank one
    # would be a rule the owner could neither read nor restate.
    # ``ck_merchants_name_not_blank`` says the same thing in the database --
    # on ``budget.merchants`` since plan step ``bank_import:X-gd-1``, where the
    # string now lives once -- so the two cannot drift.
    return found.group(1).strip() or None


def _account_identity(row: "list[str]", columns: "dict[str, int]") -> str:
    """Return what this file calls its account: the NAME and the number.

    **Both, because the number alone is not an identity.**  SECU masks it to
    its last four digits (``******3820``), so two of one owner's accounts whose
    numbers end alike mask identically -- and
    :class:`~app.models.statement_import.AccountExternalIdentity` exists
    precisely to refuse a file imported against the wrong account.  Comparing
    only the mask would ACCEPT that mistake, which is the failure direction
    that matters.  The file states the account's name in its own column, so the
    identity is the pair.

    Args:
        row: A data row's cells.
        columns: The bound column indexes.

    Returns:
        ``"<name> <masked number>"``.

    Raises:
        StatementParseError: When either half is blank.  A blank is not an
            identity, and recording one would claim the empty string for this
            account forever with no door to correct it.
    """
    name = _cell(row, columns, "Account")
    number = _cell(row, columns, "Account Number")
    if not name or not number:
        raise StatementParseError(
            "This file does not say which account it is for (its Account or "
            "Account Number column is blank).  Nothing was imported."
        )
    return f"{name} {number}"[:64]


def _stated_balance(
    rows: "list[list[str]]", header_index: int,
) -> "tuple[Decimal | None, date | None]":
    """Return the balance the file's PREAMBLE claims, and the day it names.

    Searched only ABOVE the transaction header, because that is where SECU
    writes it and because below it every row is a transaction -- a description
    reading ``Balance as of ...`` is then the user's text, not the bank's
    header, and must not be read as the account's balance.

    **An ABSENT header is not an error; an UNREADABLE one is.**  A source may
    state no balance at all and still import, so nothing here refuses a file
    for lacking the line.  A line that IS present and whose figure cannot be
    read is the other case entirely: that is a parse losing a fact the file
    states, and dropping it silently would retire this cross-check without
    anyone noticing -- the same distinction
    :func:`~._integrity.carries_running_balance` draws when it refuses a
    PARTIAL running-balance column rather than downgrading to "no self-check".

    Args:
        rows: Every parsed CSV row, in file order.
        header_index: Where the transaction header sits, bounding the search.

    Returns:
        ``(balance, day)``, or ``(None, None)`` when the file states none.
        Never one without the other: a figure with no day asserts nothing about
        an account, and a day with no figure asserts nothing at all.

    Raises:
        StatementParseError: When the line is present and its figure is not a
            number, its day is not a date, or the file states more than one.
    """
    found = [
        (row, stated)
        for row in rows[:header_index] if row
        for stated in [_STATED_BALANCE.match(row[0].strip())] if stated
    ]
    if not found:
        return (None, None)
    if len(found) > 1:
        # Refused rather than resolved, exactly as the neighbouring rule
        # refuses a file naming two accounts.  Taking the first silently would
        # pick whichever the export happened to write first, and the module
        # docstring's own measurement is that one of these can LAG the file --
        # so "the first" is not a safe default, it is a coin toss about money.
        raise StatementParseError(
            f"This file states its balance {len(found)} times "
            f"({', '.join(row[0].strip() for row, _ in found)}).  A statement "
            f"states one balance for one day.  Nothing was imported."
        )
    row, stated = found[0]
    if len(row) < 2 or not row[1].strip():
        raise StatementParseError(
            f"This file states {row[0].strip()!r} and then no figure, so "
            f"its own balance line cannot be read.  Nothing was imported."
        )
    try:
        day = datetime.strptime(stated.group(1), _DATE_FORMAT).date()
    except ValueError as exc:
        # **Refused, not dropped.**  ``strptime`` raises a bare ``ValueError``,
        # which ``run_statement_door`` does not catch -- so ``13/45/2026`` and
        # Unicode digits alike reached the 500 page instead of this message.
        # The transaction-date parser five lines up has always guarded its own
        # ``strptime``; this one did not.  Found by adversarial robustness
        # review 2026-08-22.
        raise StatementParseError(
            f"This file says {row[0].strip()!r}, and that is not a date.  "
            f"Nothing was imported."
        ) from exc
    return (_money(row[1], "the balance the file states"), day)


def _decode(payload: bytes) -> str:
    """Return *payload* as text, refusing what cannot be stored.

    Args:
        payload: The uploaded bytes.

    Returns:
        The decoded text.

    Raises:
        StatementParseError: When the bytes are not text, or carry a NUL.  A
            NUL survives ``decode`` and ``csv.reader`` intact and only fails
            deep inside the driver, at the write: psycopg2 raised a bare
            ``ValueError`` that escaped the route's ``SQLAlchemyError``
            handlers as a 500, and psycopg 3 (since plan step balance:X-dj)
            raises a ``DataError`` those handlers turn into a generic failure.
            Neither is the sentence this gives.
    """
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatementParseError(
            "This file is not text the importer can read.  Export it again as "
            "CSV.  Nothing was imported."
        ) from exc
    if "\x00" in text:
        raise StatementParseError(
            "This file contains binary data where text was expected, so it is "
            "not a CSV export.  Nothing was imported."
        )
    return text


def _read_line(row: "list[str]", columns: "dict[str, int]") -> StatementLine:
    """Return one transaction row as the normalized line it states.

    Extracted from :func:`parse` when the preamble read pushed that
    function past pylint's local-variable ceiling.  It is a lift and not a
    rewrite: every field, every comment and every guard below stood in the
    loop body unchanged, and the five locals it owns (the posted day, the
    memo, the description, the running balance and the joined text) belong
    to building ONE line rather than to walking a file.

    Args:
        row: The CSV row.
        columns: The bound header, by name.

    Returns:
        The :class:`~._line.StatementLine`.

    Raises:
        StatementParseError: When the row's Date cell is not a date, or a
            figure on it cannot be read.
    """
    try:
        posted_on = datetime.strptime(
            _cell(row, columns, "Date"), _DATE_FORMAT,
        ).date()
    except ValueError as exc:
        raise StatementParseError(
            f"This file has a line dated "
            f"{_cell(row, columns, 'Date')!r}, which is not a date.  "
            f"Nothing was imported."
        ) from exc
    memo = _cell(row, columns, "Memo")
    description = _cell(row, columns, "Description")
    running = _cell(row, columns, _RUNNING_BALANCE)
    joined = (f"{description} | {memo}" if memo else description)[:200]
    return StatementLine(
        posted_on=posted_on,
        # **The day the bank STATES the swipe happened, or None.**  SECU's
        # CSV carries one DATE column and states the transaction day inside
        # the description as ``DATE MM-DD`` (182 of 361 lines); its OFX
        # carries no second day at all -- ``DTUSER`` equals ``DTPOSTED`` on
        # 359 of 361.  This field held a COPY of ``posted_on`` until plan
        # step X-f6a-3a, which is what made it useless: a match writes this
        # day onto a matched purchase's ``purchased_on``, and a copy would
        # record every card purchase as made on the day it cleared.
        # **Read from the DESCRIPTION cell, never the joined text.**  A
        # first draft parsed ``joined`` so that "what is parsed is what is
        # stored" -- which is the wrong property: what matters is which
        # CELL the bank put the token in.  SECU states a transaction day
        # inside the description of a card line; a MEMO is the user's own
        # free text, so a ``DATE 08-13`` in a memo alone would have been
        # read as the bank's word.  The two-token refusal below was written
        # for exactly that hazard and only caught its two-token form.
        # Parsing the cell also removes the 200-character truncation from
        # the guard's reach, where a second token past the cut could turn a
        # refused line into an accepted one.  Found by adversarial design
        # and financial review 2026-08-18.
        transaction_on=_stated_transaction_day(description, posted_on),
        amount=_row_amount(row, columns),
        description=joined,
        # **The merchant the bank NAMES, read from the same cell and for
        # the same reason** (plan step X-f6a-3d): it is the key a
        # rule is stated against, so a memo's own
        # parentheses must not be able to reach it.
        merchant=_stated_merchant(description),
        source_category=_cell(row, columns, "Category")[:100] or None,
        external_id=None,
        running_balance=(
            _money(running, "a running balance") if running else None
        ),
    )


def parse(payload: bytes) -> ParsedStatement:
    """Return everything this file states: its account, its lines, its balance.

    Args:
        payload: The uploaded file's raw bytes.  Decoded as UTF-8 with a BOM
            tolerated.

    Returns:
        The :class:`~._line.ParsedStatement`.  Its lines are in CHRONOLOGICAL
        order, oldest first; the file itself is newest-first, and the reversal
        here is what lets :func:`~._integrity.verify_running_balance` and
        :func:`~._line.group_indexes` both take one stated order, with the
        result CHECKED rather than assumed.

    Raises:
        StatementParseError: When the file is not a SECU transaction export,
            carries no lines or too many, lacks or disagrees with its own
            summary, names more than one account, is not in date order, or
            states a balance line whose figure cannot be read.
    """
    text = _decode(payload)
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise StatementParseError(
            f"This file could not be read as CSV ({exc}).  Nothing was "
            f"imported."
        ) from exc

    # Found by the NAMES it carries, not by its first cell: this adapter binds
    # every column by name, so requiring one of them to be first would be the
    # positional assumption the binding exists to remove.
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if _HEADER_ANCHOR <= {cell.strip() for cell in row}
        ),
        None,
    )
    if header_index is None:
        raise StatementParseError(
            "This file has no transaction header row, so it is not a SECU "
            "transaction export.  Nothing was imported."
        )
    columns = _bind_columns(rows[header_index])
    # Read BEFORE the lines are walked, so a file whose balance line is present
    # but unreadable is refused before any of it is interpreted.
    stated_balance, stated_balance_on = _stated_balance(rows, header_index)

    lines, totals, accounts = [], None, set()
    for row in rows[header_index + 1:]:
        if not any(cell.strip() for cell in row):
            continue
        if _is_totals_row(row, columns):
            totals = _read_totals(row, columns)
            continue
        if not _cell(row, columns, "Date"):
            continue
        if len(lines) >= MAX_LINES:
            raise StatementParseError(
                f"This file holds more than {MAX_LINES:,} transactions, which "
                f"is far beyond any real statement.  Nothing was imported."
            )
        # **Read BEFORE the identity is taken, which is the order this loop
        # always had.**  Extracting the line build moved ``accounts.add``
        # ahead of the date parse, so a row with both a blank Account cell and
        # an unreadable Date reported the account fault where it used to report
        # the date one.  Both refuse the file and neither writes anything, so
        # the cost was one sentence -- but the extraction's docstring calls
        # itself a lift rather than a rewrite, and this is what makes that
        # true.  Found by adversarial robustness review 2026-08-22.
        line = _read_line(row, columns)
        accounts.add(_account_identity(row, columns))
        lines.append(line)

    if not lines:
        raise StatementParseError(
            "This file holds no transactions.  Nothing was imported."
        )
    if len(accounts) > 1:
        raise StatementParseError(
            f"This file mixes {len(accounts)} accounts "
            f"({', '.join(sorted(accounts))}).  A statement import records one "
            f"account at a time.  Nothing was imported."
        )
    if totals is None:
        raise StatementParseError(
            "This export has no 'Totals:' summary row, so nothing can check "
            "that it is complete -- and that row is the last line of the "
            "file, which is exactly what a truncated download loses.  "
            "Re-export the full statement.  Nothing was imported."
        )
    _verify_against_totals(lines, totals)

    lines.reverse()
    _refuse_unordered(lines)
    return ParsedStatement(
        external_account_id=accounts.pop(),
        lines=lines,
        # A CSV export states no range, so what it DECLARES is the days its
        # own lines reach (ruling **R-BAL71**).  MIN/MAX rather than first
        # and last: ``_refuse_unordered`` has already held the file to date
        # order, so the two agree, and the extremes stay right if that
        # refusal is ever relaxed.
        declared_start=min(line.posted_on for line in lines),
        declared_end=max(line.posted_on for line in lines),
        stated_balance=stated_balance,
        stated_balance_on=stated_balance_on,
    )


def _refuse_unordered(lines: "list[StatementLine]") -> None:
    """Refuse *lines* unless they are non-decreasing in ``posted_on``.

    **Chronological order is a precondition of three separate things** -- the
    running-balance chain (a prefix sum), the identity ordinal (positional),
    and the import's own recorded span -- and until this check existed it was
    stated in three docstrings and enforced nowhere.  The reversal above is the
    only thing that produced it, and the file being newest-first was an
    assumption no gate held: a user who opens the CSV in a spreadsheet, sorts
    it and saves would have had their span read backwards and their lines
    keyed in the wrong order, surfacing as a database-level error the route
    renders as "Something went wrong saving this statement".

    Args:
        lines: The lines, already reversed into intended chronological order.

    Raises:
        StatementParseError: On the first pair that goes backwards.
    """
    for previous, current in zip(lines, lines[1:]):
        if current.posted_on < previous.posted_on:
            raise StatementParseError(
                f"This file is not in date order: {current.posted_on} follows "
                f"{previous.posted_on}.  Export it again from your bank "
                f"rather than re-saving it from a spreadsheet.  Nothing was "
                f"imported."
            )
