"""Score the app's rendered cash balance against the BANK's own daily closing.

The instrument behind ruling **balance:R-GW** (its ruling card in the tracker),
and it exists as a committed script rather than as a number in a
planning document because the planning conventions' rule 6 said a measurement is named
by its COMMAND: a figure copied into prose goes stale invisibly, and the four
places that ruling's evidence appears would each have gone stale separately.
**Cited with its ARC** because `bank_import:R-GW` was minted the same day on
another branch: a bare id here names two rulings.

**What it answers.**  Three arms, over the days an account's records and a
bank export both cover:

* **the cash fold** -- :func:`app.services.balance_at.cash_balance_at`, in
  which every balance ASSERTION resets the running total (ruling R-S);
* **the RENDERED figure** -- :func:`app.services.balance_at.balance_at`, the
  seam's own scalar, which is the cash fold PLUS the modelled CONTRIBUTION and
  ACCRUAL tiers an interest, investment or appreciating account carries
  (:mod:`app.services.balance_at._asset_fold`).  It is the number a screen
  shows, and on an account with no modelled tier it is the cash fold exactly;
* **the cutover** -- ``opening equity + SUM(settled sources <= day)`` with no
  reset at all, which is what plan step ``balance:X-f3c-5`` makes
  ``balance(T)``.

The first and the last fold the SAME walk, so the only difference between those
two is the reset.  The planned tier is out of all three by construction: ruling
R-G clamps a still-Projected row to ``as_of + 1``, and the reader's as-of here
is the LAST day compared.

**It reports SPLIT by whether the day carries an assertion, and that split is
the point.**  Pooled, the comparison flatters whichever arm resets: an assertion
recorded on a day forces that day's balance onto the number the owner typed, so
the gap closes to the cent whether or not the records underneath are right.
This project had already measured that trap one screen over --
:mod:`app.services.bank_agreement` scores the per-day RESIDUE rather than the
balance gap precisely because "a gap of zero over a non-zero residue is the
exact shape this report exists to stop reading as agreement", and 11 of 35 real
disagreements on this same account read as exact agreement in the gap (finding
**N-337**).  A pooled score here would repeat that, in favour of the arm that
resets.  So the days are partitioned and both halves are printed.

**Why it does not simply score `bank_agreement`'s residue instead, which would
be the obvious way to dodge the confound.**  The residue is
``the app's own rows - the bank's lines`` for a day: a MOVEMENT comparison, and
a reset changes no movement.  So it is blind to the difference between the reset
arms by construction, and scoring both would return the same number for reasons
that have nothing to do with which is right -- a tautology wearing the shape of
evidence.  The confound is answered by PARTITIONING the level comparison, not by
swapping it for a metric that cannot see the subject.  (That producer also
returns nothing for an account with no imported lines, which every account was
until statement imports began -- finding **N-368** -- and any account no
statement has been imported for still is.)  This script reads the bank's
exported daily-balance file directly, so it answers for an account whether or
not its statements were ever imported.

**Usage** (from the repository root, against a production-shaped clone -- never
the dev runtime database, which other sessions move)::

    DATABASE_URL=postgresql://.../shekel_xf3c \\
        .venv/bin/python tests/manual/measure_cutover_against_bank.py \\
        --account 1 --bank ~/Downloads/checking/2026_ytd_daily_balances.csv

    DATABASE_URL=postgresql://.../shekel_xf3c \\
        .venv/bin/python tests/manual/measure_cutover_against_bank.py \\
        --account 10 --format fidelity \\
        --bank ~/Downloads/History_for_Account_Z29868989.csv

**Two export shapes, one comparison** (``--format``, plan step
**balance:X-f3c-2b-2c**).  SECU's "daily balances" export is a two-column
``date,balance`` CSV under a short preamble, listing only days that carried
activity.  Fidelity's is a transaction HISTORY whose running
``Cash Balance ($)`` column states the same fact per line.  Both reduce to
``{day: closing}`` and nothing downstream of :meth:`_Bank.read` learns which
bank it is reading.  A quiet day's closing balance is the last named day's,
carried forward, and this script does that carry rather than skipping the day
-- skipping would drop exactly the days where the reset arms differ most.

**The span begins where the account's BOOKS do**, and the bank days below that
are named and excluded rather than scored (:func:`_report_absorbed`): the fold
answers every pre-opening day with the one stored equity, so a comparison there
measures how much history the opening ABSORBED (ruling **R-HG**) rather than
whether the records are right.

Reads only.  No writes, no commit, no fixture.
"""

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app import create_app, ref_cache
from app.enums import AccountOpeningSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.scenario import Scenario
from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import walk_cash_ledger

_ZERO_MONEY = Decimal("0.00")
_CENT = Decimal("0.01")
_ONE_DAY = timedelta(days=1)

#: The Fidelity history columns this reader needs, by their own header text.
#: Named rather than indexed so a column added upstream cannot silently shift
#: the figure that is read.
_FIDELITY_DAY = "Run Date"
_FIDELITY_BALANCE = "Cash Balance ($)"

#: The arms scored, and what each row's label calls them.  A dict rather than
#: hand-written label strings: the report named every arm twice in the split
#: table and once more in the pooled one, so an arm added anywhere had five
#: sites to reach and a partition left unprinted would have read as an arm
#: with no days rather than as a missing row.
_ARMS = {
    "today": "cash fold, reset at each assertion",
    "rendered": "RENDERED = cash fold + the modelled tier",
    "cutover": "cutover, no reset at all",
}

#: The label column's width, shared by the header and every scored row so a
#: long arm name cannot wrap the figures off their own heading.
_LABEL = 46


def _money(raw: str, path: str, day: "date | None") -> Decimal:
    """Return *raw* as cents, refusing anything that is not a bare number.

    **A bare ``Decimal(...)`` raises ``InvalidOperation``, which is a traceback
    rather than the ``SystemExit`` every other refusal here gives.**  A comma
    thousands separator and a leading ``$`` are both ordinary in an exported
    balance column and both hit it (measured 2026-09-01), so the failure a
    reader would most plausibly meet was the one failure mode this file did not
    state.

    Args:
        raw: The cell.
        path: The file, named in the refusal.
        day: The day it belongs to, or ``None`` before one is parsed.

    Returns:
        The figure quantized to cents.

    Raises:
        SystemExit: When the cell is not a plain decimal number.
    """
    try:
        return Decimal(raw).quantize(_CENT)
    except InvalidOperation:
        where = f" for {day}" if day is not None else ""
        raise SystemExit(
            f"{path} states a balance{where} this reader cannot parse: "
            f"{raw!r}.  It expects a bare number -- no currency symbol, no "
            "thousands separator."
        ) from None


@dataclass(frozen=True)
class _Bank:
    """One parsed daily-balance export: the days it names and their closings.

    ONE value rather than a ``{day: balance}`` beside a sorted key list,
    because every reader needs both and two loose arguments are the shape this
    project rules a defect rather than a contract (plan Section 8).

    Attributes:
        closings: ``{day: closing balance}`` -- one entry per day the file
            NAMES, which is every day that carried activity.
        named: The same days, ascending.
    """

    closings: "dict[date, Decimal]"
    named: "list[date]"

    @classmethod
    def _readers(cls) -> dict:
        """Return the export shapes this file knows, by their ``--format`` name.

        **ONE statement of the shape list**, read by :meth:`read` and by
        ``main``'s ``--format`` choices.  Spelling it in both places is the
        drift :data:`_ARMS` exists to remove, one option over, and it also made
        :meth:`read`'s own unknown-shape refusal unreachable from the command
        line (adversarial review, 2026-09-01).

        Returns:
            ``{shape name: reader}``.
        """
        return {"secu": cls._read_secu, "fidelity": cls._read_fidelity}

    @classmethod
    def shapes(cls) -> tuple:
        """Return the ``--format`` values, for argparse.

        Returns:
            The shape names, sorted.
        """
        return tuple(sorted(cls._readers()))

    @classmethod
    def read(cls, path: str, shape: str) -> "_Bank":
        """Parse a daily-balance export in whichever *shape* the bank writes.

        **Two shapes rather than two scripts** (plan step
        **balance:X-f3c-2b-2c**).  What this file needs of an export is one
        answer -- ``{day: closing balance}`` -- and every bank states it, so a
        second copy of the comparison, the split table and the cutover arm
        would have been duplicated for a CSV dialect.

        Args:
            path: The CSV file.
            shape: ``"secu"`` for the two-column ``date,balance`` daily-balance
                export, or ``"fidelity"`` for the transaction-history export
                whose running ``Cash Balance ($)`` column states the same fact.

        Returns:
            The :class:`_Bank`.

        Raises:
            SystemExit: When *shape* is not a shape this reader knows (reachable
                only from a caller that is not ``main``, whose ``--format``
                choices come from :meth:`shapes`), or when the file names no day
                at all -- an empty parse is the shape a WRONG ``--format``
                produces, and it would otherwise read as "the bank has no days"
                and score zero comparisons green.
        """
        readers = cls._readers()
        if shape not in readers:
            raise SystemExit(f"unknown --format {shape!r}: "
                             f"expected one of {sorted(readers)}")
        closings = readers[shape](path)
        if not closings:
            raise SystemExit(
                f"{path} states no daily balance in the {shape!r} shape; "
                "check --format"
            )
        return cls(closings=closings, named=sorted(closings))

    @staticmethod
    def _read_secu(path: str) -> "dict[date, Decimal]":
        """Parse SECU's two-column daily-balance export.

        Args:
            path: The CSV file.  A row that is not exactly ``date,balance``
                with a parseable ``MM/DD/YYYY`` date is preamble and is
                skipped.

        Returns:
            ``{day: closing balance}``.
        """
        closings: "dict[date, Decimal]" = {}
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if len(row) != 2:
                    continue
                day = _parse_day(row[0])
                if day is None:
                    continue
                closings[day] = _money(row[1], path, day)
        return closings

    @staticmethod
    def _read_fidelity(path: str) -> "dict[date, Decimal]":
        """Parse Fidelity's transaction-history export.

        The file is a BOM'd CSV with blank preamble lines above its header and
        a disclaimer below its rows, and it states a running
        ``Cash Balance ($)`` per LINE rather than per day.  The header is found
        by its own column names rather than by position, and the columns are
        read by name -- a positional read would silently take the wrong figure
        if Fidelity ever adds a column.

        **A day's CLOSING is its chronologically LAST line, and the file's own
        ordering decides which that is.**  An earlier version required every
        line on a day to state the same balance, which is true of the
        developer's export only because its multi-line days are
        dividend/reinvestment pairs that net to zero on the reported column.
        An ordinary day with two transfers states one closing and one INTRA-DAY
        balance, and that version aborted on it with a message about a
        data-integrity fault in a perfectly well-formed file (measured
        2026-09-01: a two-EFT day refused with "states two closing balances").
        The direction is MEASURED from the date sequence rather than assumed,
        and a file sorted neither way is refused -- because then no rule picks
        the closing out of the intra-day lines, and guessing would be worse
        than stopping.

        Args:
            path: The CSV file.

        Returns:
            ``{day: closing balance}``.

        Raises:
            SystemExit: When the rows are in date order in neither direction.
        """
        rows: "list[tuple[date, Decimal]]" = []
        day_col = balance_col = None
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.reader(handle):
                cells = [cell.strip() for cell in row]
                if day_col is None:
                    if _FIDELITY_DAY in cells and _FIDELITY_BALANCE in cells:
                        day_col = cells.index(_FIDELITY_DAY)
                        balance_col = cells.index(_FIDELITY_BALANCE)
                    continue
                if len(cells) <= max(day_col, balance_col):
                    continue
                day = _parse_day(cells[day_col])
                if day is None or not cells[balance_col]:
                    continue
                rows.append((day, _money(cells[balance_col], path, day)))
        return _chronological_closings(path, rows)

    def closing_on(self, day: date) -> "Decimal | None":
        """Return the bank's closing balance FOR *day*.

        A quiet day is absent from the file, so its closing is the last named
        day's, carried forward.

        Args:
            day: The civil day to answer.

        Returns:
            The closing balance, or ``None`` for a day before the file begins.
        """
        prior = [named for named in self.named if named <= day]
        return self.closings[prior[-1]] if prior else None


def _chronological_closings(
    path: str, rows: "list[tuple[date, Decimal]]",
) -> "dict[date, Decimal]":
    """Fold per-LINE running balances into one closing per day.

    Args:
        path: The file, named in any refusal.
        rows: ``(day, running balance)`` in FILE order.

    Returns:
        ``{day: closing balance}``, empty when *rows* is.

    Raises:
        SystemExit: When the days are in order in neither direction, so no rule
            picks a day's last line.
    """
    if not rows:
        return {}
    days = [day for day, _ in rows]
    ascending = all(a <= b for a, b in zip(days, days[1:]))
    descending = all(a >= b for a, b in zip(days, days[1:]))
    if not ascending and not descending:
        raise SystemExit(
            f"{path} is not in date order in either direction, so no rule "
            "picks a day's CLOSING line out of its intra-day ones"
        )
    closings: "dict[date, Decimal]" = {}
    # Last write wins, and after the reversal the last write for a day is that
    # day's final line -- its closing balance.
    for day, balance in (rows if ascending else reversed(rows)):
        closings[day] = balance
    return closings


def _parse_day(raw: str) -> "date | None":
    """Return *raw* as a civil day, or ``None`` when it is not one.

    Args:
        raw: A cell from an export's date column.

    Returns:
        The date, or ``None`` for preamble and disclaimer rows.
    """
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _score(label: str, diffs: "list[Decimal]") -> None:
    """Print one arm's day count, exact agreements, worst and mean error.

    Args:
        label: The row label.
        diffs: One signed ``rendered - bank`` per compared day.
    """
    if not diffs:
        print(f"{label:<{_LABEL}}{'--':>5}{'--':>7}{'--':>12}{'--':>11}")
        return
    count = len(diffs)
    exact = sum(1 for diff in diffs if diff == _ZERO_MONEY)
    worst = max(abs(diff) for diff in diffs)
    mean = (sum(abs(diff) for diff in diffs) / count).quantize(_CENT)
    print(f"{label:<{_LABEL}}{count:>5}{exact:>7}{worst:>12}{mean:>11}")


def _reject_configured_loan(account: Account) -> None:
    """Refuse an account whose balance is an amortization replay, not a fold.

    **The predicate is the loan being CONFIGURED, not the account kind**, and
    the two differ on a state both ``balance_at._kind_correct.balance_at`` and
    ``_resolution.configured_loan`` name in terms: an AMORTIZING account with
    no :class:`~app.models.loan_params.LoanParams` -- "a Mortgage typed but
    never filled in" -- has no schedule to fold and IS its transaction rows, so
    it is exactly what this harness scores.  Refusing on the kind alone turned
    such an account away with the sentence "its balance is the amortization
    replay", which is false for it (adversarial review, 2026-09-01; latent on
    the developer's data, where both amortizing accounts carry params).

    Args:
        account: The account to value.

    Raises:
        SystemExit: When the account is an amortizing loan with its terms
            filled in, whose balance is the schedule replay rather than a cash
            fold.  Scoring one would print a loan projection under a caption
            saying "cash fold", which is the figure-disagreeing-with-its-caption
            shape this arc has closed twice.
    """
    if classify_account(account) is not AccountProjectionKind.AMORTIZING:
        return
    if account.loan_params is None:
        return
    raise SystemExit(
        f"account {account.id} is a CONFIGURED amortizing loan; its balance is "
        "the amortization replay, not a cash fold.  This harness scores CASH "
        "accounts against a bank export."
    )


def _opening_provenance(source_id: int) -> str:
    """Return the opening's provenance as its own name.

    The row is a ``ref.account_opening_sources`` FK and printing the integer
    made a reader look it up -- while "the app computed this figure" is exactly
    what a reader of a books-versus-bank line needs to know (ruling **R-GX**;
    finding **N-275** measures a derived one wrong).

    Args:
        source_id: The stored ``source_id``.

    Returns:
        The enum's own value, or the raw id when it matches neither member.
    """
    for member in AccountOpeningSourceEnum:
        if ref_cache.account_opening_source_id(member) == source_id:
            return member.value
    return f"source id {source_id}"


def _compare(account_id: int, bank_path: str, shape: str) -> None:
    """Score the three arms against the bank file and print the split table.

    Args:
        account_id: The cash account to value.
        bank_path: The bank's daily-balance or history export.
        shape: Which export shape *bank_path* is (:meth:`_Bank.read`).

    Raises:
        SystemExit: When the account is a configured loan
            (:func:`_reject_configured_loan`, asked BEFORE the export is read
            so a refused account does not first demand a valid file), or when
            it carries no balance assertion -- the cash fold then has no reset
            to apply and reads the cutover exactly, so two of the three arms
            would be one measurement printed twice.
    """
    account = db.session.get(Account, account_id)
    _reject_configured_loan(account)
    scenario = db.session.execute(
        db.select(Scenario).filter_by(user_id=account.user_id, is_baseline=True)
    ).scalar_one()
    walk = walk_cash_ledger(account_id, scenario.id)
    if not walk.anchor_facts:
        raise SystemExit(f"account {account_id} has no balance assertion")

    bank = _Bank.read(bank_path, shape)
    print(f"bank file: {len(bank.named)} days, "
          f"{bank.named[0]} .. {bank.named[-1]}")

    # **The STORED opening, not a derivation, and the difference is measurable
    # rather than tidy** (plan step **balance:X-f3c-2b-2c**).  This read used to
    # be ``anchor_facts[0].anchor_balance`` minus the sources on or before it,
    # under a comment saying that equalled the stored figure by construction.
    # It does not survive the two acts ``budget.account_openings`` exists for.
    # A RESTATEMENT moves the stored figure and leaves every assertion saying
    # what it said, which is the whole point of the door
    # (``opening_service.apply_opening_restatement`` documents it); and a
    # SUPERSEDING assertion on a day that already carries one leaves
    # ``anchor_facts[0]`` naming the row that no longer governs.  Measured on
    # this step's own rehearsal: the derivation answered ``$5,363.56`` for an
    # account whose books had just been restated to ``$0.00``.
    opening_day = walk.opening.opened_on
    opening_equity = walk.opening.opening_equity
    context = balance_at.BalanceContext.build(
        user_id=account.user_id, as_of=max(bank.named),
    )

    # ``anchor_facts`` is business-date ascending, so ``[0]`` is the EARLIEST
    # RECORDED assertion -- which is not always the one that governs its day,
    # because a later row for the same day supersedes it.  Labelled for what it
    # is: on the developer's archived Fidelity account it is a ``$5,363.56``
    # that a ``$0.00`` on the same day has replaced.
    print(f"books open {opening_day} holding {opening_equity} "
          f"({_opening_provenance(walk.opening.source_id)}); "
          f"{len(walk.anchor_facts)} assertion(s), earliest recorded "
          f"{walk.anchor_facts[0].observed_on} at "
          f"{walk.anchor_facts[0].anchor_balance}")
    assertion_days = {fact.observed_on for fact in walk.anchor_facts}
    span_opens_on = _span_opens_on(opening_day, assertion_days)
    _report_opening_day(bank, opening_day, opening_equity, assertion_days)
    _report_absorbed(bank, walk, opening_day)
    scored, unanswerable = _arms(
        account, context, walk, opening_equity, bank, span_opens_on,
    )
    _report(scored, unanswerable, bank, span_opens_on)


def _span_opens_on(opening_day: date, assertion_days: "set[date]") -> date:
    """Return the first day the arms are scored on.

    **The opening day is scored EXACTLY when it carries an assertion, and the
    reason is mechanical rather than a preference** (adversarial review,
    2026-09-01, correcting this file's first answer).  Ruling **R-HG** makes
    the day movement-free -- ``cash_ledger._books`` states the boundary as
    ``day > opened_on`` -- so with no assertion there the cutover sums no
    source, the cash fold has no step, and both answer the stored equity: the
    day cannot separate the arms, and scoring it moves a partition's worst case
    by a figure no arm chose.

    **An assertion on that day changes it, and that is the ORDINARY shape.**
    ``account_service.create_account`` writes the origination opening and the
    origination assertion for one day, so ``_cash_fold._actual_steps`` books a
    correction there and the cash fold answers ``anchor_balance`` while the
    cutover still answers the equity.  Measured 2026-09-01 on a
    production-shaped clone: **four of the developer's nine accounts** assert on
    their own opening day (the two IRAs, the 401(k) and the Property).  A first
    draft excluded the day unconditionally, on the claim that all three arms
    were algebraically forced to one value there -- true only for an account
    with no assertion on it, and false for the four where the day is most
    informative.

    Args:
        opening_day: The day the account's books open.
        assertion_days: Every day the account asserts a balance on.

    Returns:
        ``opening_day`` when it carries an assertion, else the day after it.
    """
    return opening_day if opening_day in assertion_days else opening_day + _ONE_DAY


def _report_opening_day(
    bank: _Bank, opening_day: date, opening_equity: Decimal,
    assertion_days: "set[date]",
) -> None:
    """Print the stored opening against the bank's own close for its own day.

    Ruling **R-HG** makes the opening equity the CLOSING balance for
    ``opened_on``, so the bank states a figure for exactly the same quantity
    and the two are directly comparable -- and disagreeing is a real defect,
    not an artefact.  It is printed whether or not the day is scored, and the
    line says which, because :func:`_span_opens_on` decides that on a property
    of the account rather than of the export.

    **What the figure IS, stated exactly.**  On the developer's Checking it
    reads ``-$2,493.47``: the stored opening ``$689.16`` against the bank's
    ``$3,182.63`` close for 2026-03-26.  That is **not** finding **N-275**'s
    own number -- that row measures ``$436.05``, the 2026-03-27 assertion
    against the same carried-forward close -- and the ``$2,057.42`` between
    them is Checking's own 03-27 movements, which sit above the books and
    belong to neither figure.  Both are true of the same disagreement measured
    at different points, and saying so is what stops a reader diffing this line
    against the ledger and finding a 5.7x discrepancy with no explanation.

    Args:
        bank: The parsed export.
        opening_day: The day the account's books open.
        opening_equity: What they opened holding.
        assertion_days: Every day the account asserts a balance on.
    """
    scored = "SCORED below (it carries an assertion)" \
        if opening_day in assertion_days \
        else "not scored (no assertion on it, so no arm can differ there)"
    closing = bank.closing_on(opening_day)
    if closing is None:
        print(f"the opening day {opening_day} is below the export's first "
              f"line, so the bank states no figure for it; {scored}")
        return
    stated = "states" if opening_day in bank.closings else "carries forward"
    print(f"the opening day {opening_day}: books {opening_equity} against a "
          f"bank that {stated} {closing} -- {opening_equity - closing}; "
          f"{scored}")


def _report_absorbed(bank: _Bank, walk, opening_day: date) -> None:
    """Name the days the account's books cannot cover, and why.

    **They are excluded from the score and PRINTED rather than skipped.**  A
    day before ``opened_on`` is inside the opening equity by ruling **R-HG**:
    the fold is total and answers it with that one level, so comparing there
    scores the app against history it has no column to record -- and the gap it
    reports is the SIZE of what the opening absorbed, never a defect the
    records could fix.  Dropping them silently would make a comparison that
    stops at the books look like one that covers the file, which is the shape
    plan step **balance:X-f3c-2b-2c** measured wrong in its own acceptance
    sentence.

    **ASSERTIONS below the opening are named too, and that state is reachable**
    (finding **N-400**): ``anchor_service.resolve_observation_day`` bounds an
    assertion at ``earliest_recordable_day`` and at today, never at
    ``opened_on``, and no trigger holds the pair.  Such a day leaves the span
    the same way a bank day does, and it is the one case where the exclusion's
    own justification would be false -- an assertion RESETS the fold, so below
    the opening the answer would stop being one constant.  Measured
    2026-09-01: zero such rows on either production-shaped clone, so the arm is
    reported-not-instantiated.  The neighbouring case -- an assertion ON the
    opening day, which four of nine accounts carry -- is not excluded at all;
    :func:`_span_opens_on` scores that day instead.

    Args:
        bank: The parsed export.
        walk: The account's cash-ledger walk, for its assertions.
        opening_day: The day the account's books open.
    """
    absorbed = [day for day in bank.named if day < opening_day]
    below = sorted(
        fact.observed_on for fact in walk.anchor_facts
        if fact.observed_on < opening_day
    )
    if absorbed:
        print(f"{len(absorbed)} bank day(s) fall below these books and are NOT "
              f"compared -- {absorbed[0]} .. {absorbed[-1]}, closing "
              f"{bank.closings[absorbed[0]]} .. {bank.closings[absorbed[-1]]}. "
              f"Ruling R-HG puts them inside the opening equity.")
    if below:
        print(f"{len(below)} ASSERTION(s) also fall below these books "
              f"({below[0]} .. {below[-1]}) and leave the span with them -- "
              f"finding N-400, a state nothing refuses.")


def _arms(
    account: Account, context, walk, opening_equity: Decimal, bank: _Bank,
    span_opens_on: date,
) -> "tuple[dict[tuple[str, bool], list[Decimal]], list[date]]":
    """Return each arm's signed ``rendered - bank`` per compared day.

    A day is compared when the bank names it OR the account carries an
    assertion on it; a bank-quiet day inside the span takes the prior closing
    (:meth:`_Bank.closing_on`).  The SPAN is derived here rather than passed --
    it runs from :func:`_span_opens_on` through the bank file's last day, and
    both ends are already in the two arguments that carry them.

    **It used to start at the first ASSERTION, and that hid days the arms
    answer** (plan step **balance:X-f3c-2b-2c**).  An account's records begin at
    its opening, not at the first balance its owner happened to type: on the
    developer's Money Market those are 2026-03-26 and 2026-05-01, so FIVE bank
    days the repaired records reproduce to the cent -- 03-31, 04-09, 04-23,
    04-29 and 04-30 -- were outside the score.

    **The figures ruling balance:R-GW quotes are UNCHANGED by this, and that
    was checked rather than assumed.**  Re-run 2026-09-01 on a
    production-shaped clone, account 1 against the SECU export answers exactly
    what that ruling cites: ``17-of-75`` pooled for the cash fold against
    ``0-of-75`` for the cutover, and ``$529.48`` against ``$1,956.64`` on the
    29 days no assertion touches.  Checking's books open 2026-03-26, its first
    assertion is 2026-03-27, and it asserts nothing on the opening day -- so
    :func:`_span_opens_on` returns the same first day the old rule did.

    Args:
        account: The account being valued.
        context: The read pass, built at the bank file's last day.
        walk: Its :class:`~app.services.cash_ledger.CashLedgerWalk`.
        opening_equity: The cutover's constant.
        bank: The parsed export.
        span_opens_on: The first day to score (:func:`_span_opens_on`).

    Returns:
        ``({(arm, day carries an assertion): [diff, ...]}, [unanswerable day])``
        -- every arm over both partitions, and the days inside the span the
        export begins too late to state a figure for.
    """
    arms: "dict[tuple[str, bool], list[Decimal]]" = {
        (arm, on_assertion): []
        for arm in _ARMS for on_assertion in (True, False)
    }
    assertion_days = {fact.observed_on for fact in walk.anchor_facts}
    unanswerable: "list[date]" = []
    day, last_day = span_opens_on, max(bank.named)
    while day <= last_day:
        if day in bank.closings or day in assertion_days:
            closing = bank.closing_on(day)
            if closing is None:
                # A day the file cannot answer -- an assertion below the
                # export's own first line.  Counted and named by
                # :func:`_report`, not skipped in silence: an earlier version
                # dropped two asserted days out of a report whose own header
                # claimed to score "every day the owner asserted on".
                unanswerable.append(day)
                day += _ONE_DAY
                continue
            on_assertion = day in assertion_days
            arms[("today", on_assertion)].append(
                balance_at.cash_balance_at(account, context, day) - closing,
            )
            # **The third arm is what the SCREEN says, and it is not the first
            # one** (plan step **balance:X-f3c-2b-2c**).  ``cash_balance_at``
            # folds the cash ledger; ``balance_at`` is the seam's own scalar
            # and adds the MODELLED tier an interest, investment or
            # appreciating account carries.  They are the same number on a
            # Checking account -- which is why this file read only the first
            # for as long as it measured one -- and they are not on a Money
            # Market: measured 2026-08-31 on this step's repaired clone, the
            # cash fold answers the bank's own ``$3,673.90`` on 2026-07-31 and
            # the rendered figure answers ``$3,674.22``, one day of accrual on
            # top of a close the bank has already stated (ruling **R-L**, named
            # in **R-HM**).  Scoring only the cash fold would report an account
            # agreeing with its bank on a day no screen shows that figure.
            arms[("rendered", on_assertion)].append(
                balance_at.balance_at(account, context, day) - closing,
            )
            settled = sum(
                (fact.delta for fact in walk.source_facts
                 if fact.settled_on <= day),
                _ZERO_MONEY,
            )
            arms[("cutover", on_assertion)].append(
                opening_equity + settled - closing,
            )
        day += _ONE_DAY
    return arms, unanswerable


def _report(
    arms: "dict[tuple[str, bool], list[Decimal]]",
    unanswerable: "list[date]", bank: _Bank, span_opens_on: date,
) -> None:
    """Print the split table, then the pooled figures it warns about.

    The pooled rows are printed LAST and labelled, rather than omitted: the
    number that reversed this step's order was the pooled one, and a reader
    checking that history has to be able to see it beside what de-confounds it.

    **The header reconciles its own arithmetic.**  It printed a scored count
    beside a bank-day count and an absorbed count that did not add up, because
    the fourth term -- days the owner asserted on that the bank never names --
    was in no line (adversarial review, 2026-09-01: account 1 printed
    ``112 / 46 / 75`` where ``46 + 1 + 75 = 122``).

    Args:
        arms: :func:`_arms`' scored figures.
        unanswerable: :func:`_arms`' days the export begins too late to state.
        bank: The parsed export, for the bank-day count the header reconciles
            against.
        span_opens_on: The first day scored, which bounds that count.
    """
    counts = {
        arm: len(arms[(arm, True)]) + len(arms[(arm, False)]) for arm in _ARMS
    }
    assert len(set(counts.values())) == 1, (
        f"the arms scored different day counts, which cannot happen: {counts}"
    )
    scored = next(iter(counts.values()))
    in_span = [day for day in bank.named if day >= span_opens_on]
    answerable = [day for day in in_span if day not in unanswerable]
    print(f"\n{scored} day(s) scored = {len(answerable)} bank day(s) from "
          f"{span_opens_on} + {scored - len(answerable)} the owner asserted on "
          f"that the bank never names")
    if unanswerable:
        print(f"{len(unanswerable)} day(s) in the span are BELOW the export's "
              f"first line and could not be scored -- {unanswerable[0]} .. "
              f"{unanswerable[-1]}")
    print(f"{'':<{_LABEL}}{'days':>5}{'exact':>7}{'worst':>12}{'mean':>11}")
    # **The PARTITION is the heading and the ARM is the row**, so a label names
    # one thing rather than two.  Printing the cross product as flat labels put
    # a 58-character sentence in a 46-character column and wrapped the figures
    # off their own header -- which is a report that cannot be read beside the
    # one it is being diffed against.
    for on_assertion, said in ((True, "an ASSERTION"), (False, "NO assertion")):
        print(f"days carrying {said}")
        for arm, gloss in _ARMS.items():
            _score(f"  {gloss}", arms[(arm, on_assertion)])
    print("POOLED (confounded -- see the docstring)")
    for arm, gloss in _ARMS.items():
        _score(f"  {gloss}", arms[(arm, True)] + arms[(arm, False)])


def main() -> None:
    """Parse the arguments, open an app context and print the comparison."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--account", type=int, required=True,
        help="the cash account id to value",
    )
    parser.add_argument(
        "--bank", required=True,
        help="path to the bank's CSV export",
    )
    parser.add_argument(
        "--format", dest="shape", default="secu", choices=_Bank.shapes(),
        help="which export shape --bank is (default: secu)",
    )
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        _compare(args.account, args.bank, args.shape)


if __name__ == "__main__":
    main()
