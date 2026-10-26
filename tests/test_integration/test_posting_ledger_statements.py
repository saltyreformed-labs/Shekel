"""The confirmed-ledger statements oracle (Build-Order Step 5, Commit 13).

The correctness gate for the READ side of Step 5: the two confirmed-ledger
statements (:mod:`app.services.ledger_report_service`) -- an income statement
(pay-period AND calendar windows) and a balance sheet (as-of a date, with a
trial-balance tie-out) -- read against an independent second opinion re-derived
from the posting ledger and the source tables, never against the readers that
produced the numbers.  This is the FOURTH reconciliation oracle, the read-side
sibling of the account-anchor write oracle
(the ``test_posting_ledger_account_anchor_reconciliation`` package); together they pin the
Step-5 promise that the trial balance closes app-wide and ``A = L + E`` is
checkable end to end.

**Non-tautological by construction**, in the same three independent ways the
other posting-ledger oracles are:

  * **hand-computed literals** -- every expected statement line, section total,
    net income, and tie-out figure is the test author's arithmetic over the
    seeded anchors and settled amounts, owing nothing to the reader under test
    (e.g. a $1000 opening + $3000 salary - $400 groceries - $50 uncategorized -
    $150 transfer out => Checking asset 3400.00);
  * **an independent balance-sheet re-derivation** (:func:`_independent_bs`) --
    a genuine second opinion that groups ``account_postings`` by ledger-account
    CLASS directly (a different path than the reader's source-bucket
    attribution), signs each class from a test-local debit-normal set (NOT the
    ref-cache flag the reader signs by), and derives retained earnings from the
    Income + Expense debit nets, so a classification / signing / retained-earnings
    bug in the reader cannot hide;
  * **structural invariants** the readers must satisfy regardless of any single
    figure -- the accounting identity ``A = L + E`` at multiple as-of dates,
    ARTICULATION (income net over a window + that window's equity corrections
    equals the equity delta between the bounding balance sheets), and
    period-vs-calendar agreement on an aligned fixture.

Every account and settle is produced through the REAL go-forward primitives --
``create_account`` (the C6 opening sync), ``create_settled_cash_transaction`` /
``create_settled_transfer`` (the status seam + posting builder),
``create_loan_with_trueup`` + a settled payment (the Step-4 loan split),
``_true_up_at`` (the anchor-true-up chokepoint's two steps) -- so every
reconciled statement was posted exactly as production posts it.  The one
non-production affordance is pinning an anchor row's ``created_at`` (via
:func:`_true_up_at`, the C8 technique) so a true-up's civil-day partition is
deterministic regardless of the test clock.  Sources are pinned to noon-UTC
paid dates so their storage (UTC) civil date and their display-timezone
attribution date coincide -- except the dedicated display-timezone case, which
pins an 8:05pm-Eastern Dec-31 settle precisely to prove the two diverge as L9
requires.

An adversarial case proves the oracle is not vacuous: injecting one unbalanced
leg pushes ``tie_out.ledger_net`` off zero and flips ``in_balance`` to False.
Every other (balanced) fixture asserts the whole-DB trial balance is zero and no
entry is malformed (:func:`_assert_ledger_self_consistent`), so a lone-leg bug
cannot slip past.  All money is ``Decimal`` from strings, with the arithmetic
shown per the testing standard.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import NamedTuple

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.category import Category
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.services import (
    account_posting_service,
    ledger_report_service,
    posting_service,
    status_seam,
)
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
import pytest
import sqlalchemy as sa

from tests._test_helpers import (
    family_journal_filter,
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_cash_transaction,
    create_settled_transfer,
    freeze_today,
    linked_ledger_account,
    observed_day_of,
)

# A far-future as-of that folds every posted source into the balance sheet,
# clock- and timezone-independent (nothing is ever attributed after it).  Reused
# from the C9 service suite's convention so a "full position" sheet is stable
# regardless of when CI runs (account openings carry the server-now civil date).
_ALL_ACTIVITY = date.max

# The clean year every hand-computed calendar fixture pins its settles into:
# far from any real activity so a "year" window sees only what the fixture put
# there, and after the accounts' origination so a pinned settle rides ON TOP of
# the opening rather than being absorbed into it.
#
# **The module FREEZES today into it** (:func:`_today_inside_the_fixture_year`
# below), and that pairing is load-bearing since ruling **R-EJ**.  The year was
# chosen as "far FUTURE" precisely so it would sit after an origination stamped
# with the server clock -- but a settle dated after today is exactly what R-EJ
# refuses, because a settled row asserts that money HAS moved.  The fixture's
# premise was only ever expressible while that guard was missing: an account
# opened TODAY closes its opening balance on today, so nothing settled today
# can ride on top of it, and reaching for tomorrow was the workaround.
#
# Freezing today INTO the year fixes it at the root: the accounts originate on
# 2099-01-01, the settles land in March and April of the same year, and the
# ordering the fixtures actually depend on holds without any date being in its
# own future.  Every hand-computed figure and every ``"2099"`` window label is
# unchanged.
_Y = 2099

#: The day every account these fixtures create is OPENED on -- the first of
#: :data:`_Y`, so an opening precedes every settle the fixtures pin and a
#: settle therefore rides ON TOP of it rather than being absorbed.  Passed to
#: the account factory explicitly, never re-stamped afterwards: the factory
#: posts the opening's anchor correction keyed on this day.
_FIXTURE_OPENING = date(_Y, 1, 1)

#: The day this module's clock is frozen to -- the LAST of :data:`_Y`, so every
#: settle the fixtures pin is in the past, which is what ruling R-EJ requires
#: of a settled row and what production always looks like.
_FIXTURE_TODAY = date(_Y, 12, 31)


@pytest.fixture(autouse=True)
def _today_after_the_fixture_year(monkeypatch):
    """Freeze today to :data:`_FIXTURE_TODAY` for every test in this module.

    A fixture's calendar must contain its own today, and this one has three
    instants that must stay in order: the opening, the settles, and now.  These
    suites pin settles into :data:`_Y` and open accounts explicitly at
    :data:`_FIXTURE_OPENING`, so freezing now at the end of the same year puts
    all three in the production order -- an account existed, then money moved,
    and today is after both.

    Without it the clock sits in the real present while the calendar sits in
    2099, which is the fixture-clock defect class findings N-131, N-132 and R8
    were all instances of, and which ruling R-EJ's write-door guard turns from
    silent into loud.
    """
    freeze_today(monkeypatch, _FIXTURE_TODAY)


def _noon(year: int, month: int, day: int) -> datetime:
    """Return the noon-UTC instant of a civil day (aware).

    Noon UTC is 7-8am Eastern, so a settle pinned here has the SAME civil date
    in UTC storage and in the display timezone -- the attribution date equals
    the stored ``entry_date``, which lets the independent as-of re-derivation
    (:func:`_independent_bs`, folding by ``entry_date``) stand in for the
    reader's display-timezone fold.  The one place this must NOT hold -- the
    Dec-31 evening-Eastern boundary -- pins its own instant explicitly.
    """
    return datetime(year, month, day, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Independent re-derivations (test-authored, NOT the reader under test)
# ---------------------------------------------------------------------------
#
# These re-derive each statement figure from scratch so the oracle is a genuine
# second opinion: the balance-sheet totals are re-summed by ledger-account CLASS
# straight from ``account_postings`` (a different path than the reader's
# per-source attribution), signed by a test-local debit-normal set, with
# retained earnings derived here -- so a bug shared by the reader's attribution
# and presentation cannot reproduce these numbers.  The trial-balance and
# per-entry-balance checks mirror the sibling oracles; the duplication is
# DELIBERATE -- each oracle keeps its OWN independent queries so it stays a
# self-contained second opinion.

# The accounting sign convention, restated INDEPENDENTLY of
# ``ref_cache.ledger_class_is_debit_normal`` (which the reader signs by): a
# debit-normal class presents its debit-positive net as-is, a credit-normal
# class presents the negated net.  Asset and Expense are debit-normal; Liability,
# Income, and Equity are credit-normal.
_DEBIT_NORMAL = frozenset({
    LedgerAccountClassEnum.ASSET,
    LedgerAccountClassEnum.EXPENSE,
})


def _class_debit_nets(
    user_id: int, scenario_id: int, through: date | None = None,
) -> dict[int, Decimal]:
    """Return ``{class_id: debit_net}`` over a user's postings in a scenario.

    Joins ``account_postings`` -> ``journal_entries`` (owner + scenario) ->
    ``ledger_accounts`` (for the class) and sums the signed ``amount`` grouped by
    ledger-account class.  Optionally bounded to entries dated at or before
    *through* (an entry-date fold, valid as an as-of reader only for the
    noon-UTC fixtures where ``entry_date`` equals the attribution date).  Keyed
    off the class directly, so it never touches the reader's source-bucket
    attribution.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        through: An inclusive ``entry_date`` bound, or ``None`` for full
            position.

    Returns:
        ``{ledger_account_class_id: summed debit net}`` over the nonempty
        classes.
    """
    query = (
        _db.session.query(
            LedgerAccount.class_id,
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0")),
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
        )
    )
    if through is not None:
        query = query.filter(JournalEntry.entry_date <= through)
    return dict(query.group_by(LedgerAccount.class_id).all())


class _IndependentSheet(NamedTuple):
    """A balance sheet re-derived from raw postings, signed independently.

    The second opinion the reader's ``BalanceSheetReport`` must match (built by
    :func:`_independent_bs`): each class total re-summed from ``account_postings``
    by class and signed from :data:`_DEBIT_NORMAL`, with retained earnings
    derived from the Income + Expense debit nets and the mechanical ledger net
    summed raw.  A plain value holder, compared field by field against the
    reader.

    Attributes:
        assets: The Asset-class natural total.
        liabilities: The Liability-class natural total.
        equity_ledger: The Equity-class natural total (posted equity accounts
            only, excluding the derived retained earnings).
        retained_earnings: The derived retained earnings (negated Income +
            Expense debit net).
        equity: ``equity_ledger + retained_earnings`` -- the reader's Equity
            section total.
        ledger_net: The raw debit-positive net of every posting (zero for a
            balanced ledger).
    """

    assets: Decimal
    liabilities: Decimal
    equity_ledger: Decimal
    retained_earnings: Decimal
    equity: Decimal
    ledger_net: Decimal


def _independent_bs(
    user_id: int, scenario_id: int, through: date | None = None,
) -> _IndependentSheet:
    """Return the balance sheet re-derived from raw postings (see :class:`_IndependentSheet`).

    Groups the user's postings in the scenario by ledger-account class
    (:func:`_class_debit_nets`), signs each class from the test-local
    :data:`_DEBIT_NORMAL` set (NOT the ref-cache flag the reader signs by), and
    derives retained earnings from the Income + Expense debit nets -- a genuine
    second opinion on classification, signing, and the retained-earnings close.
    """
    nets = _class_debit_nets(user_id, scenario_id, through)

    def _debit(class_enum: LedgerAccountClassEnum) -> Decimal:
        return nets.get(
            ref_cache.ledger_account_class_id(class_enum), Decimal("0"),
        )

    def _natural(class_enum: LedgerAccountClassEnum) -> Decimal:
        debit = _debit(class_enum)
        return debit if class_enum in _DEBIT_NORMAL else -debit

    retained_earnings = -(
        _debit(LedgerAccountClassEnum.INCOME)
        + _debit(LedgerAccountClassEnum.EXPENSE)
    )
    equity_ledger = _natural(LedgerAccountClassEnum.EQUITY)
    return _IndependentSheet(
        assets=_natural(LedgerAccountClassEnum.ASSET),
        liabilities=_natural(LedgerAccountClassEnum.LIABILITY),
        equity_ledger=equity_ledger,
        retained_earnings=retained_earnings,
        equity=equity_ledger + retained_earnings,
        ledger_net=sum(nets.values(), Decimal("0")),
    )


def _windowed_equity_corrections(
    user_id: int, scenario_id: int, first_day: date, last_day: date,
) -> Decimal:
    """Return the natural Equity-class net attributed inside a calendar window.

    The equity corrections (anchor openings / true-ups) whose ``entry_date``
    falls in ``[first_day, last_day]``, summed as their debit nets and NEGATED
    into natural (credit-normal) terms -- the articulation identity's
    "windowed equity corrections" term, computed here from raw postings so the
    articulation test relates three independently-obtained quantities.  Equity
    accounts receive only sourceless corrections (dated by ``entry_date``), so an
    entry-date fold is the correct window.

    Args:
        user_id: The owner whose ledger to read.
        scenario_id: The budget scenario to scope to.
        first_day: The inclusive first day of the window.
        last_day: The inclusive last day of the window.

    Returns:
        The window's Equity-class natural total as a ``Decimal``.
    """
    equity_debit = (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0")),
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.scenario_id == scenario_id,
            LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EQUITY,
            ),
            JournalEntry.entry_date >= first_day,
            JournalEntry.entry_date <= last_day,
        )
        .scalar()
    )
    return -equity_debit


def _trial_balance() -> Decimal:
    """Return ``SUM(account_postings.amount)`` over the whole ledger (zero when balanced)."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0")),
        )
        .scalar()
    )


def _entries_violating_balance() -> list[tuple[int, Decimal, int]]:
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``; any
    row returned violates the deferred balanced trigger's invariant, re-checked
    from the ORM side.
    """
    rows = (
        _db.session.query(
            Posting.journal_entry_id,
            _db.func.sum(Posting.amount),
            _db.func.count(Posting.id),
        )
        .group_by(Posting.journal_entry_id)
        .all()
    )
    return [
        (entry_id, leg_sum, leg_count)
        for entry_id, leg_sum, leg_count in rows
        if leg_sum != 0 or leg_count < 2
    ]


def _assert_ledger_self_consistent() -> None:
    """Assert every entry balances and the whole-DB trial balance is zero."""
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


# ---------------------------------------------------------------------------
# Fixture helpers (a controlled true-up at a pinned instant)
# ---------------------------------------------------------------------------


def _origin_instant(account) -> datetime:
    """Return the factory origination row's stored assertion instant (aware UTC).

    The one instant a test cannot choose (the origination ``created_at`` is the
    INSERT's server ``now()``); a fixture's pinned true-up instants are built
    relative to it, in whole DAYS, so the civil-day partition is deterministic.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.created_at, AccountAnchorHistory.id)
        .first()
    )
    created_at = row.created_at
    if created_at.tzinfo is None:
        return created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def _true_up_at(account, balance, created_at) -> None:
    """Assert a controlled true-up and reconcile it (the chokepoint's two steps).

    The deterministic stand-in for ``anchor_service.apply_anchor_true_up``: it
    appends the ``AccountAnchorHistory`` row + ``current_anchor_balance`` cache at
    a PINNED ``created_at`` and drives the SAME all-scenarios reconcile the
    true-up chokepoint calls.  Pinning the instant is what makes the moment
    partition exact -- ``apply_anchor_true_up`` stamps ``created_at = now()``,
    which cannot be placed between two synthetic settles; the C5/C8 suites use
    the same affordance for the same reason.  The chokepoint itself is covered
    end to end by ``test_account_posting_service.py``; this oracle validates the
    resulting statements against an independent second opinion.

    The account is re-fetched into the CURRENT session first: the ``seed_user``
    fixture object is attached to the fixture-setup session, so mutating its
    ``current_anchor_balance`` cache on the fixture reference alone would not
    persist (the test body runs under a fresh app-context session).  Re-fetching
    keeps the cache write faithful to how production stages a true-up.
    """
    account = _db.session.get(Account, account.id)
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created_at,
        # The civil day this assertion is the closing balance FOR, kept in step
        # with the pinned instant by the shared rule (ruling R-DH, plan step 2).
        observed_on=observed_day_of(created_at),
        # The ENTERED day, in step with the pinned instant (**N-299**).
        # The column's default is the wall clock, which a row built to sit in
        # the PAST must not inherit: it would claim to have been typed today.
        recorded_on=observed_day_of(created_at),
    )
    _db.session.add(row)
    _db.session.flush()
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )


def _find_line(lines, label):
    """Return the single :class:`StatementLine` in *lines* with *label*."""
    matches = [line for line in lines if line.label == label]
    assert len(matches) == 1, (
        f"expected exactly one {label!r} line, got {[m.label for m in lines]}"
    )
    return matches[0]


def _labels(lines):
    """Return the ordered labels of *lines* (they are label-sorted)."""
    return [line.label for line in lines]


def _reader_bs(user_id, as_of=_ALL_ACTIVITY):
    """Return the reader's balance sheet (default full position)."""
    return ledger_report_service.compute_balance_sheet(user_id, as_of)


def _assert_reader_matches_independent(user_id, scenario_id, sheet, through=None):
    """Assert the reader's sheet equals the independent re-derivation.

    Ties the reader's section totals, retained earnings, and two-part tie-out to
    :func:`_independent_bs` -- the genuine second opinion -- so a reader-side
    classification / signing / retained-earnings bug is caught even where a
    hand-computed literal is not spelled out for every line.
    """
    independent = _independent_bs(user_id, scenario_id, through)
    assert sheet.assets.total == independent.assets
    assert sheet.liabilities.total == independent.liabilities
    assert _find_line(
        sheet.equity.lines, "Retained Earnings",
    ).amount == independent.retained_earnings
    assert sheet.equity.total == independent.equity
    assert sheet.tie_out.ledger_net == independent.ledger_net
    assert sheet.tie_out.assets == independent.assets
    assert sheet.tie_out.liabilities_plus_equity == (
        independent.liabilities + independent.equity
    )
    assert sheet.tie_out.in_balance is True


# ---------------------------------------------------------------------------
# 1. A rich hand-computed income statement + balance sheet
# ---------------------------------------------------------------------------


class TestRichFixtureStatements:
    """A multi-account fixture: every line hand-computed and independently tied."""

    def test_income_statement_and_balance_sheet_hand_computed(
        self, app, db, seed_user,
    ):
        """Categories, a fallback, a liability charge, and a transfer, tied out.

        On the seeded Checking ($1000.00 opening) plus a Rewards Card anchored
        -$500.00 (owed-as-negative Liability) and a Rainy Day Savings anchored
        $200.00, all settles pinned into year 2099 (noon UTC):

          - income  "Income: Salary"     $3000.00 -> Checking   (2099-03-10)
          - expense "Family: Groceries"  $ 400.00 -> Checking   (2099-03-15)
          - expense Uncategorized (none) $  50.00 -> Checking   (2099-03-20)
          - expense "Family: Groceries"  $ 120.00 -> Rewards Card (2099-03-16)
          - transfer                     $ 150.00  Checking -> Rainy Day (2099-04-01)

        INCOME STATEMENT (year 2099):
          income   "Income: Salary"                       3000.00
          expense  "Family: Groceries"  400 + 120 =        520.00
          expense  "Uncategorized Expense"                  50.00
          net income = 3000 - (520 + 50) =                2430.00
        Transfers never appear (both legs land on linked Asset accounts).

        BALANCE SHEET (full position):
          Assets      Checking 1000 + 3000 - 400 - 50 - 150 =   3400.00
                      Rainy Day 200 + 150 =                       350.00   -> 3750.00
          Liabilities Rewards Card -(-500 - 120) =                620.00   ->  620.00
          Equity      Checking -- Opening                        1000.00
                      Rainy Day -- Opening                         200.00
                      Rewards Card -- Opening                     -500.00
                      Retained Earnings (= net income)            2430.00   -> 3130.00
          A = L + E: 3750 == 620 + 3130.  Tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
                observed_on=_FIXTURE_OPENING,
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Rainy Day",
                anchor_balance=Decimal("200.00"),
                observed_on=_FIXTURE_OPENING,
            )
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("3000.00"), account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 3, 10),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("400.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("50.00"), account=checking, category=None,
                settled_on=date(_Y, 3, 20),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("120.00"), account=card,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 16),
            )
            create_settled_transfer(
                seed_user, db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                settled_on=date(_Y, 4, 1),
            )
            db.session.commit()

            # --- Income statement (year 2099), hand-computed line by line.
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert income.window_label == "2099"
            assert _labels(income.income.lines) == ["Income: Salary"]
            assert _find_line(
                income.income.lines, "Income: Salary",
            ).amount == Decimal("3000.00")
            assert income.income.total == Decimal("3000.00")
            assert _labels(income.expense.lines) == [
                "Family: Groceries", "Uncategorized Expense",
            ]
            assert _find_line(
                income.expense.lines, "Family: Groceries",
            ).amount == Decimal("520.00")
            assert _find_line(
                income.expense.lines, "Uncategorized Expense",
            ).amount == Decimal("50.00")
            assert income.expense.total == Decimal("570.00")
            assert income.net_income == Decimal("2430.00")

            # --- Balance sheet (full position), hand-computed line by line.
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("3400.00")
            assert _find_line(
                sheet.assets.lines, "Rainy Day",
            ).amount == Decimal("350.00")
            assert sheet.assets.total == Decimal("3750.00")
            assert _find_line(
                sheet.liabilities.lines, "Rewards Card",
            ).amount == Decimal("620.00")
            assert sheet.liabilities.total == Decimal("620.00")
            assert _find_line(
                sheet.equity.lines, "Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert _find_line(
                sheet.equity.lines, "Rainy Day -- Opening",
            ).amount == Decimal("200.00")
            assert _find_line(
                sheet.equity.lines, "Rewards Card -- Opening",
            ).amount == Decimal("-500.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("2430.00")
            assert sheet.equity.total == Decimal("3130.00")
            assert sheet.tie_out.in_balance is True

            # --- Independent second opinion + whole-ledger self-consistency.
            _assert_reader_matches_independent(user_id, scenario_id, sheet)
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 2. A loan: interest + escrow reach the income statement; cash does not
# ---------------------------------------------------------------------------


class TestLoanInterestEscrowInStatements:
    """A loan payment's interest/escrow are Expense lines; its cash never is."""

    def test_loan_split_reaches_income_statement_and_balance_sheet(
        self, app, db, seed_user,
    ):
        """A $1000 Mortgage payment splits to Interest 500 / Escrow 100 / principal 400.

        A Mortgage originated at $250,000 @ 6% (2099-01-01), trued up to
        $100,000 (2099-01-10), carrying a $1,200/yr escrow component.  A single
        $1,000.00 Checking -> Mortgage payment settled in Feb 2099 splits (the
        running balance is the $100,000 anchor):

          interest = round(100000 * 0.06/12) = round(100000 * 0.005) =  500.00
          escrow   = round(1200 / 12)                                =  100.00
          principal = 1000 - 500 - 100                               =  400.00   (refund 0)

        A $2000.00 salary lands on Checking first (2099-02-05) so it stays
        nonzero after the payment.

        INCOME STATEMENT (year 2099):
          income   "Income: Salary"                 2000.00
          expense  "Mortgage -- Interest"            500.00
          expense  "Mortgage -- Escrow"              100.00
          net income = 2000 - (500 + 100) =         1400.00
        The transfer's two cash legs (Checking -1000, Mortgage linked +1000)
        land on Asset/Liability accounts, so they never touch the statement.

        BALANCE SHEET (full position):
          Assets      Checking 1000 + 2000 - 1000 =            2000.00  -> 2000.00
          Liabilities Mortgage -(cash 1000 - open 250000
                        + trueup 150000 - split 600) = -(-99600) = 99600.00 -> 99600.00
          Equity      Checking -- Opening                      1000.00
                      Mortgage -- Opening (250000 - 150000)  -100000.00
                      Retained Earnings (= net income)         1400.00  -> -97600.00
          A = L + E: 2000 == 99600 + (-97600).  Tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=Decimal("250000.00"),
                anchor_balance=Decimal("100000.00"),
                # In the payment's own year, the month before its 2099-02-01
                # installment: every contractual installment from origination
                # is charged since plan step recurrence:R16-c-2, so the
                # 2025-01-01 origination and 2026-01-10 true-up it carried
                # until then left seventy-three years unpaid ahead of the
                # payment (ruling R-R103).
                anchor_date=date(_Y, 1, 10),
                rate=Decimal("0.06000"),
                origination_date=date(_Y, 1, 1),
                name="Mortgage", term=360,
                escrow_annual=Decimal("1200.00"),
            )
            pay_period = PayPeriod(user_id=user_id, start_date=date(_Y, 2, 1))
            db.session.add(pay_period)
            db.session.flush()

            create_settled_cash_transaction(
                seed_user, db.session, pay_period, Decimal("2000.00"),
                account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 2, 5),
            )
            create_settled_transfer(
                seed_user, db.session, checking, loan, pay_period,
                amount=Decimal("1000.00"), settled_on=date(_Y, 2, 10),
            )
            db.session.commit()

            # --- Income statement: interest + escrow present, cash absent.
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert _labels(income.income.lines) == ["Income: Salary"]
            assert income.income.total == Decimal("2000.00")
            assert _labels(income.expense.lines) == [
                "Mortgage -- Escrow", "Mortgage -- Interest",
            ]
            assert _find_line(
                income.expense.lines, "Mortgage -- Interest",
            ).amount == Decimal("500.00")
            assert _find_line(
                income.expense.lines, "Mortgage -- Escrow",
            ).amount == Decimal("100.00")
            assert income.expense.total == Decimal("600.00")
            assert income.net_income == Decimal("1400.00")
            # No transfer / linked cash line ever leaks into the statement.
            for label in ("Checking", "Mortgage"):
                assert label not in _labels(income.income.lines)
                assert label not in _labels(income.expense.lines)

            # --- Balance sheet: the loan liability signs positive.
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("2000.00")
            assert _find_line(
                sheet.liabilities.lines, "Mortgage",
            ).amount == Decimal("99600.00")
            assert _find_line(
                sheet.equity.lines, "Mortgage -- Opening",
            ).amount == Decimal("-100000.00")
            assert _find_line(
                sheet.equity.lines, "Retained Earnings",
            ).amount == Decimal("1400.00")
            assert sheet.tie_out.in_balance is True

            # The production loan helper agrees on the linked liability net.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-99600.00")

            _assert_reader_matches_independent(user_id, scenario_id, sheet)
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 3. A = L + E at multiple as-of dates (incl. inside an anchor period)
# ---------------------------------------------------------------------------


class TestAccountingIdentityAtMultipleAsOf:
    """The identity and tie-out hold at every as-of, including mid-anchor-period."""

    def test_identity_holds_before_and_after_a_true_up(
        self, app, db, seed_user,
    ):
        """A Savings true-up between two settles ties out at five as-of dates.

        Savings anchored $500.00, every event on its own noon-UTC civil day so
        an entry-date fold reconstructs each as-of ledger:

          - $200.00 Groceries expense paid 2099-06-05
          - true-up asserting $350.00 at 2099-06-10  (ledger_before = 500 - 200
            = 300, so the true-up delta is +50.00)
          - $100.00 Groceries expense paid 2099-06-15

        Savings walks 500 -> 300 -> 350 -> 250 across the as-of ladder, with
        THREE as-of dates placed EXACTLY on an event day so the balance sheet's
        inclusive ``<= as_of`` fold is pinned (a ``< as_of`` mutant mis-states
        Savings by that day's whole entry).  One as-of falls inside the anchor
        period BEFORE the assertion (2099-06-05, the first spend) and one AFTER
        it (2099-06-12) -- the balance-sheet case of the civil-day partition.  With
        the seeded Checking ($1000.00):

          2099-06-01  A = 1000 + 500 = 1500  (only openings folded)
          2099-06-05  A = 1000 + 300 = 1300  (EXACT first-spend day)
          2099-06-10  A = 1000 + 350 = 1350  (EXACT true-up day, re-based)
          2099-06-12  A = 1000 + 350 = 1350  (after the assertion)
          2099-06-15  A = 1000 + 250 = 1250  (EXACT second-spend day)

        At each, ``A == L + E`` and both tie-out halves hold, cross-checked
        against the independent entry-date re-derivation.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Ladder Savings",
                anchor_balance=Decimal("500.00"),
                observed_on=_FIXTURE_OPENING,
            )
            db.session.commit()

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("200.00"), account=savings,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 6, 5),
            )
            _true_up_at(savings, "350.00", _noon(_Y, 6, 10))
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), account=savings,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 6, 15),
            )
            db.session.commit()

            expected_savings = {
                date(_Y, 6, 1): Decimal("500.00"),
                date(_Y, 6, 5): Decimal("300.00"),   # exact first-spend day
                date(_Y, 6, 10): Decimal("350.00"),  # exact true-up day
                date(_Y, 6, 12): Decimal("350.00"),  # after the assertion
                date(_Y, 6, 15): Decimal("250.00"),  # exact second-spend day
            }
            for as_of, savings_balance in expected_savings.items():
                sheet = _reader_bs(user_id, as_of)
                assert _find_line(
                    sheet.assets.lines, "Ladder Savings",
                ).amount == savings_balance, as_of
                assert _find_line(
                    sheet.assets.lines, "Checking",
                ).amount == Decimal("1000.00"), as_of
                # The identity and both tie-out halves hold at this as-of.
                assert sheet.tie_out.assets == (
                    sheet.tie_out.liabilities_plus_equity
                ), as_of
                assert sheet.tie_out.ledger_net == Decimal("0.00"), as_of
                assert sheet.tie_out.in_balance is True, as_of
                # Independent entry-date fold agrees (valid for noon-UTC dates).
                _assert_reader_matches_independent(
                    user_id, scenario_id, sheet, through=as_of,
                )

            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 4. Articulation: income + windowed equity corrections == equity delta
# ---------------------------------------------------------------------------


class TestArticulation:
    """The statements articulate through the shared attribution core."""

    def test_income_plus_equity_corrections_equals_equity_delta(
        self, app, db, seed_user,
    ):
        """A year's income net plus its equity corrections is the equity delta.

        On the seeded Checking ($1000.00 opening, dated in a prior year), all in
        year 2099:

          - income  "Income: Salary"    $800.00  paid 2099-05-01
          - expense "Family: Groceries" $300.00  paid 2099-05-02
          - true-up asserting $1600.00 at 2099-05-03  (ledger_before =
            1000 + 800 - 300 = 1500, so the equity correction delta is +100.00)

        net income (2099)                 = 800 - 300              =  500.00
        windowed equity corrections(2099) = -(true-up equity -100) =  100.00

        Bounding sheets:
          equity total as of 2098-12-31 (only the prior-year opening) = 1000.00
          equity total as of 2099-12-31 (opening 1000 + true-up 100
              + retained earnings 500)                               = 1600.00
          equity delta = 1600 - 1000 = 600.00 = 500 (income) + 100 (corrections)

        The identity relates three INDEPENDENTLY-obtained quantities (two reader
        equity totals, one reader net income, one raw-posting equity-correction
        sum), so it fails if the income statement and balance sheet attribute
        inconsistently -- the whole point of the shared read core.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]

            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("800.00"), account=checking, is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 5, 1),
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("300.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 5, 2),
            )
            _true_up_at(checking, "1600.00", _noon(_Y, 5, 3))
            db.session.commit()

            start_sheet = _reader_bs(user_id, date(_Y - 1, 12, 31))
            end_sheet = _reader_bs(user_id, date(_Y, 12, 31))
            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )

            equity_delta = end_sheet.equity.total - start_sheet.equity.total
            corrections = _windowed_equity_corrections(
                user_id, scenario_id, date(_Y, 1, 1), date(_Y, 12, 31),
            )
            # Hand-computed anchors for the three quantities.
            assert income.net_income == Decimal("500.00")
            assert corrections == Decimal("100.00")
            assert equity_delta == Decimal("600.00")
            # The articulation identity.
            assert equity_delta == income.net_income + corrections

            # Both bounding sheets tie out.
            assert start_sheet.tie_out.in_balance is True
            assert end_sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 5. Period-vs-calendar agreement on an aligned fixture
# ---------------------------------------------------------------------------


class TestPeriodVsCalendarAgreement:
    """When a period's settles all fall in its calendar month, the two agree."""

    def test_aligned_period_and_month_windows_match(self, app, db, seed_user):
        """A pay period wholly inside March 2099 gives the same income statement.

        A period spanning 2099-03-02..2099-03-15, with both its settles paid
        inside it (noon UTC, so their display-timezone paid date is that same
        March day):

          - income  "Income: Salary"    $500.00  paid 2099-03-05
          - expense "Family: Groceries" $200.00  paid 2099-03-10

        The pay-period window (filtering ``pay_period_id``) and the March-2099
        calendar window (filtering the attribution date) see exactly this set,
        so their income statements are identical line for line: income 500.00,
        expense 200.00, net 300.00.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = PayPeriod(user_id=user_id, start_date=date(_Y, 3, 2))
            db.session.add(period)
            db.session.flush()

            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("500.00"),
                account=seed_user["account"], is_income=True,
                category=seed_user["categories"]["Salary"],
                settled_on=date(_Y, 3, 5),
            )
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("200.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 10),
            )
            db.session.commit()

            by_period = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("pay_period", period_id=period.id),
            )
            by_month = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )

            assert _labels(by_period.income.lines) == _labels(
                by_month.income.lines,
            ) == ["Income: Salary"]
            assert _labels(by_period.expense.lines) == _labels(
                by_month.expense.lines,
            ) == ["Family: Groceries"]
            assert by_period.income.total == by_month.income.total == Decimal(
                "500.00",
            )
            assert by_period.expense.total == by_month.expense.total == Decimal(
                "200.00",
            )
            assert by_period.net_income == by_month.net_income == Decimal(
                "300.00",
            )
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 6. Revert and cross-year hard-delete residue drop cleanly
# ---------------------------------------------------------------------------


class TestRevertAndResidueDropped:
    """A reverted source and cross-year residue net to zero and disappear."""

    def test_reverted_expense_leaves_no_statement_footprint(
        self, app, db, seed_user,
    ):
        """Settling then reverting a $400 expense removes it from both statements.

        A $400.00 Groceries expense settled in 2099 posts, then is reverted via
        the real ``sync_transaction_postings(settled=False)`` path -- the source
        nets to zero, so ``_grouped_source_nets`` drops it whole.  The income
        statement shows no Groceries line and the balance sheet's Checking is
        back on its $1000.00 opening, tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            txn = create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("400.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 7, 1),
            )
            db.session.commit()
            # Sanity: it was present before the revert.
            before = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert before.expense.total == Decimal("400.00")

            # Through the seam first (plan step X-bi-3a; see
            # ``_expense_ledger_for_category``).
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transaction_postings(txn)
            db.session.commit()

            after = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert not after.expense.lines
            assert after.net_income == Decimal("0.00")
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_reader_matches_independent(user_id, scenario_id, sheet)
            _assert_ledger_self_consistent()

    def test_cross_year_residue_dropped_from_every_window(
        self, app, db, seed_user,
    ):
        """Residue split across two years nets to zero and is dropped from both.

        A hard delete SET-NULLs ``journal_entries.transaction_id`` after
        reversing the postings, leaving residue: transaction-source entries with
        both concrete FKs NULL.  Here the ORIGINAL residue lands in 2098 and its
        REVERSAL in 2099 -- different calendar years, netting to zero per account
        (the reverse-before-delete discipline).  Because the reader keeps only
        correction sources in the sourceless bucket, BOTH years' income
        statements and the balance sheet exclude the residue whole, so neither
        year sees a half-entry and the tie-out stays closed -- the exact
        cross-year rationale the residue drop protects.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            linked = linked_ledger_account(db.session, checking.id)
            groceries_ledger_id = self._expense_ledger_for_category(
                db, seed_user, checking, "Groceries",
            )

            # Residue original in 2098, reversal in 2099 -- both transaction-
            # source, transaction_id NULL, netting to zero on each account.
            self._residue_entry(
                seed_user, linked.id, groceries_ledger_id,
                amount=Decimal("100.00"), entry_date=date(2098, 6, 15),
            )
            self._residue_entry(
                seed_user, linked.id, groceries_ledger_id,
                amount=Decimal("-100.00"), entry_date=date(2099, 6, 15),
            )
            db.session.commit()

            for year in (2098, 2099):
                statement = ledger_report_service.compute_income_statement(
                    user_id, calendar_for(user_id), StatementWindow("year", year=year),
                )
                assert not statement.expense.lines, year
                assert statement.net_income == Decimal("0.00"), year

            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_reader_matches_independent(user_id, scenario_id, sheet)
            # The residue is real postings that net to zero: the raw trial
            # balance is still exactly zero (whole-entry, self-cancelling).
            _assert_ledger_self_consistent()

    @staticmethod
    def _expense_ledger_for_category(db, seed_user, account, category_key):
        """Mint the category's Expense ledger via a real settle, return its id.

        Settling a $0.01 expense on the category through the go-forward builder
        creates the per-category Expense ledger account exactly as production
        does, so the residue entries below post onto a REAL ledger row (not a
        hand-minted one).  The tiny settle is reverted immediately so it leaves
        no footprint of its own.
        """
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_user["bootstrap_period"],
            Decimal("0.01"), account=account,
            category=seed_user["categories"][category_key],
            settled_on=date(_Y, 1, 1),
        )
        db.session.flush()
        ledger_id = (
            db.session.query(Posting.ledger_account_id)
            .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
            .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
            .filter(
                family_journal_filter(txn),
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.EXPENSE,
                ),
            )
            .scalar()
        )
        # Revert the seeding settle so only the residue remains -- through the
        # ONE status door first (plan step X-bi-3a): a settle now writes a
        # covering movement that carries the money, and only the seam's
        # revert releases it; the primitive alone reconciles what is left.
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        posting_service.sync_transaction_postings(txn)
        db.session.flush()
        assert ledger_id is not None
        return ledger_id

    @staticmethod
    def _residue_entry(seed_user, linked_id, expense_id, *, amount, entry_date):
        """Post one residue entry (transaction source, NULL FKs) at a civil date.

        Two balanced legs (linked ``amount`` cash, expense ``-amount`` counter)
        under a ``transaction`` source with ``transaction_id`` NULL -- the shape a
        hard delete leaves after SET-NULLing the FK.  ``entry_date`` is set
        explicitly so the original and reversal can straddle a year boundary.
        """
        entry = JournalEntry(
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            pay_period_id=seed_user["bootstrap_period"].id,
            entry_date=entry_date,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.TRANSACTION,
            ),
            transaction_id=None,
            transfer_id=None,
            description="Residue entry",
        )
        _db.session.add(entry)
        _db.session.flush()
        expense_kind = ref_cache.posting_kind_id(PostingKindEnum.EXPENSE)
        _db.session.add(Posting(
            journal_entry_id=entry.id, ledger_account_id=linked_id,
            amount=amount, posting_kind_id=expense_kind,
        ))
        _db.session.add(Posting(
            journal_entry_id=entry.id, ledger_account_id=expense_id,
            amount=-amount, posting_kind_id=expense_kind,
        ))
        _db.session.flush()


# ---------------------------------------------------------------------------
# 7. Display-timezone attribution and the NULL / future-period edges
# ---------------------------------------------------------------------------


class TestAttributionEdgeCases:
    """L9's display-timezone rule and the NULL / early-settled attributions."""

    def test_dec31_evening_eastern_attributes_to_prior_year(
        self, app, db, seed_user,
    ):
        """An 8:05pm-ET Dec-31 settle lands in that year on both statements.

        A $500.00 Groceries expense paid at 2099-01-01 01:05 UTC -- 2098-12-31
        8:05pm Eastern (EST, UTC-5).  L9 attributes it to Dec 31 2098 (the user's
        wall clock), so the 2098 income statement includes it and 2099 does not,
        even though the STORED ``entry_date`` is the Jan-1 2099 the instant
        becomes in UTC.  The balance sheet as of 2098-12-31 already reflects it,
        confirming the readers attribute on the SAME display-timezone basis.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("500.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2098, 12, 31),
            )
            db.session.commit()

            in_2098 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2098),
            )
            assert in_2098.expense.total == Decimal("500.00")
            in_2099 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2099),
            )
            assert not in_2099.expense.lines

            # The balance sheet dated on that civil day already reflects the
            # spend (Checking 1000 - 500 = 500), the same wall-clock basis.
            sheet = _reader_bs(user_id, date(2098, 12, 31))
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("500.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_a_settle_on_the_period_start_attributes_to_that_day(
        self, app, db, seed_user,
    ):
        """A settle dated on its pay period's start attributes to that day.

        A $150.00 Groceries expense settled 2099-08-03, in a period starting
        2099-08-03.  So the August 2099 month window includes it and July does
        not, and it folds onto an as-of-2099-08-03 balance sheet.

        **It reached that day through a FALLBACK until plan step X-f1** -- the
        row carried no ``paid_at`` and the reader substituted the period's
        ``start_date`` -- and this case was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant; every figure below is unchanged, because the
        day is.  What the case still grades is real and unrelated to the
        fallback: a settle day sitting exactly ON a window boundary belongs to
        the LATER window, and to the balance sheet as of that day.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = PayPeriod(user_id=user_id, start_date=date(_Y, 8, 3))
            db.session.add(period)
            db.session.flush()
            create_settled_cash_transaction(
                seed_user, db.session, period, Decimal("150.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=period.start_date,
            )
            db.session.commit()

            august = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=8, year=_Y),
            )
            assert august.expense.total == Decimal("150.00")
            july = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=7, year=_Y),
            )
            assert not july.expense.lines

            before = _reader_bs(user_id, date(_Y, 8, 2))
            assert _find_line(
                before.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            on_day = _reader_bs(user_id, date(_Y, 8, 3))
            assert _find_line(
                on_day.assets.lines, "Checking",
            ).amount == Decimal("850.00")
            _assert_ledger_self_consistent()

    def test_early_settled_source_appears_in_its_paid_year(
        self, app, db, seed_user,
    ):
        """An early-settled future-period source lands in its paid calendar year.

        A $250.00 Groceries expense settled with a paid date in 2099-11 but
        placed in a pay period whose ``start_date`` is 2100-01-05 (an
        early-settled future-period source): the calendar year 2099 window
        attributes it to its PAID date (present, 250.00) while the 2099
        pay-period-less calendar view and the 2100 window differ -- both honest
        answers to different questions (C-2 vs C-3), the documented divergence.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            future_period = PayPeriod(
                user_id=user_id,
                start_date=date(2100, 1, 5),
            )
            db.session.add(future_period)
            db.session.flush()
            create_settled_cash_transaction(
                seed_user, db.session, future_period, Decimal("250.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2099, 11, 20),
            )
            db.session.commit()

            # Calendar 2099 (the paid year) sees it; the pay period lives in 2100.
            paid_year = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2099),
            )
            assert paid_year.expense.total == Decimal("250.00")
            # The pay-period window (its period start is 2100) still sees it,
            # because a pay-period window keys on the entry's period, not a date.
            by_period = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("pay_period", period_id=future_period.id),
            )
            assert by_period.expense.total == Decimal("250.00")
            # The 2100 calendar window does NOT (the paid date is 2099).
            future_year = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2100),
            )
            assert not future_year.expense.lines
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 8. Display labels branch on kind (live rename, orphan snapshot, equity twin)
# ---------------------------------------------------------------------------


class TestDisplayLabels:
    """A category line reflects a rename; the equity twin keeps its snapshot."""

    def test_rename_reflected_and_equity_twin_uses_snapshot(
        self, app, db, seed_user,
    ):
        """A live category rename reflects; the equity twin label stays snapshot.

        A $100.00 Groceries expense posts to the "Family: Groceries" category
        ledger; renaming the category's item to "Snacks" makes the income line
        read the LIVE ``category.display_name`` ("Family: Snacks").  Renaming the
        Checking ACCOUNT does NOT change its equity twin's balance-sheet label,
        which is the frozen ``"Checking -- Opening"`` snapshot (the twin is not a
        linked row, so the live-account-name rule does not apply to it).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"), account=checking,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            db.session.commit()

            groceries = db.session.get(
                Category, seed_user["categories"]["Groceries"].id,
            )
            groceries.item_name = "Snacks"
            # Rename the linked account too; the equity twin must NOT follow it.
            # Re-fetch into the current session -- the fixture reference is
            # attached to the fixture-setup session and would not persist.
            db.session.get(Account, checking.id).name = "Primary Checking"
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )
            assert _labels(income.expense.lines) == ["Family: Snacks"]

            sheet = _reader_bs(user_id)
            # The linked Asset line follows the live account name...
            assert _find_line(
                sheet.assets.lines, "Primary Checking",
            ).amount == Decimal("900.00")
            # ...but the equity twin keeps its frozen origination snapshot.
            assert _find_line(
                sheet.equity.lines, "Checking -- Opening",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_orphaned_category_uses_snapshot_label(self, app, db, seed_user):
        """A category ledger account whose category is gone keeps its snapshot label.

        A $100.00 Groceries expense posts to the category ledger; when the
        budget category's key SET-NULLs the account's ``category_id`` (its
        ``kind_id`` stays ``category``), the line falls back to the account's
        own "Family: Groceries" snapshot and the amount is untouched.

        **The orphan state is STAGED, not reached through a category delete.**
        Every plan row names a definition since the family's cutover (plan
        step ``balance:X-bi-7d-2``) and ``transaction_templates.category_id``
        is RESTRICT, so a category with a settled one-off under it cannot be
        deleted at all (the category door archives it; the database refuses
        the delete).  A LEGACY link-less row reached the state through the
        delete until then.  Production can still hold the state -- accounts
        orphaned before the cutover, or a category deleted once every row
        under it was hard-deleted -- so the reader's fallback is graded on
        the state itself: the SET NULL the key performs, written here.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            groceries_id = seed_user["categories"]["Groceries"].id
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("100.00"),
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 3, 15),
            )
            db.session.commit()
            orphaned = db.session.execute(sa.text(
                "UPDATE budget.ledger_accounts SET category_id = NULL "
                "WHERE category_id = :cid"
            ), {"cid": groceries_id}).rowcount
            db.session.commit()
            assert orphaned == 1

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=3, year=_Y),
            )
            assert _labels(income.expense.lines) == ["Family: Groceries"]
            assert income.expense.total == Decimal("100.00")
            # The orphaned category leaves the ledger balanced (SET NULL touches
            # only the display FK, never the postings).
            assert _reader_bs(user_id).tie_out.in_balance is True
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 9. Scenario and owner isolation
# ---------------------------------------------------------------------------


class TestScenarioAndOwnerIsolation:
    """Statements read the baseline only and never bleed across owners."""

    def test_non_baseline_scenario_never_appears(self, app, db, seed_user):
        """A what-if expense is invisible to the baseline statements.

        A $70.00 Groceries expense settled in a NON-baseline what-if scenario
        posts to that scenario only.  The baseline income statement shows no
        Groceries line and the baseline balance sheet's Checking stays on its
        $1000.00 opening -- the readers scope to ``get_baseline_scenario``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            whatif = Scenario(
                user_id=user_id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("70.00"), account=seed_user["account"], scenario=whatif,
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 9, 1),
            )
            db.session.commit()

            income = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert not income.expense.lines
            sheet = _reader_bs(user_id)
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_owners_do_not_see_each_other(
        self, app, db, seed_user, seed_second_user,
    ):
        """Neither owner's statements show the other's accounts or sources.

        Owner 1 (seeded Checking $1000.00) settles a $60.00 expense; owner 2
        (seeded Checking $2000.00) settles an $80.00 expense.  Each owner's
        balance sheet lists only their own Checking (1000 - 60 = 940.00 and
        2000 - 80 = 1920.00) and neither income statement sees the other's
        expense.
        """
        with app.app_context():
            user1 = seed_user["user"].id
            user2 = seed_second_user["user"].id
            create_settled_cash_transaction(
                seed_user, db.session, seed_user["bootstrap_period"],
                Decimal("60.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 1),
            )
            create_settled_cash_transaction(
                seed_second_user, db.session,
                seed_second_user["bootstrap_period"], Decimal("80.00"),
                account=seed_second_user["account"],
                category=seed_second_user["categories"]["Groceries"],
                settled_on=date(_Y, 10, 2),
            )
            db.session.commit()

            sheet1 = _reader_bs(user1)
            sheet2 = _reader_bs(user2)
            assert _labels(sheet1.assets.lines) == ["Checking"]
            assert _find_line(
                sheet1.assets.lines, "Checking",
            ).amount == Decimal("940.00")
            assert _labels(sheet2.assets.lines) == ["Checking"]
            assert _find_line(
                sheet2.assets.lines, "Checking",
            ).amount == Decimal("1920.00")

            income1 = ledger_report_service.compute_income_statement(
                user1, calendar_for(user1), StatementWindow("year", year=_Y),
            )
            income2 = ledger_report_service.compute_income_statement(
                user2, calendar_for(user2), StatementWindow("year", year=_Y),
            )
            assert income1.expense.total == Decimal("60.00")
            assert income2.expense.total == Decimal("80.00")
            _assert_reader_matches_independent(
                user1, seed_user["scenario"].id, sheet1,
            )
            _assert_reader_matches_independent(
                user2, seed_second_user["scenario"].id, sheet2,
            )
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 10. Adversarial: the tie-out is not vacuous
# ---------------------------------------------------------------------------


class TestTieOutIsNotVacuous:
    """An injected unbalanced leg turns the tie-out red."""

    def test_injected_leg_flips_in_balance_false(self, app, db, seed_user):
        """One raw +50 leg on the opening pushes ledger_net off zero, tie-out red.

        A balanced book has a green tie-out (ledger_net 0.00, in_balance True).
        Inserting one unmatched +50.00 leg onto the seeded Checking opening entry
        (raw SQL, flushed but never committed, so the DEFERRED per-entry trigger
        never fires) makes the whole-ledger net 50.00: the reader's
        ``tie_out.ledger_net`` reads 50.00, its Assets no longer equal
        Liabilities + Equity, and ``in_balance`` flips to False -- so the tie-out
        is a real check, not one the balanced trigger makes vacuously true.
        Rolled back so the leg never lands.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            # Green before the injection.
            assert _reader_bs(user_id).tie_out.in_balance is True

            linked = linked_ledger_account(db.session, checking.id)
            opening_source = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )
            # The LATEST opening entry, and named as a choice rather than
            # taken as the only one.  Since plan step X-f3c-2b the seeded
            # account's books are restated, and a restatement REVERSES the
            # opening entry and re-posts it -- three opening-sourced entries
            # where there used to be one, which is production's own shape
            # after the same act.  Any of them carries a leg on this ledger,
            # so the injection below is equally unbalanced whichever is
            # picked; ``.scalar()`` over the set would simply raise.
            entry_id = (
                db.session.query(JournalEntry.id)
                .join(Posting, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    Posting.ledger_account_id == linked.id,
                    JournalEntry.scenario_id == scenario_id,
                    JournalEntry.source_kind_id == opening_source,
                )
                .order_by(JournalEntry.id.desc())
                .limit(1)
                .scalar()
            )
            assert entry_id is not None, (
                "no opening entry to inject into -- this class's whole name is "
                "a promise that it is not vacuous, and a None here would "
                "inject nothing and still pass"
            )
            db.session.execute(_db.text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id,
                "l": linked.id,
                "a": Decimal("50.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.OPENING),
            })
            db.session.flush()

            tampered = _reader_bs(user_id)
            assert tampered.tie_out.ledger_net == Decimal("50.00")
            assert tampered.tie_out.assets != (
                tampered.tie_out.liabilities_plus_equity
            )
            assert tampered.tie_out.in_balance is False

            db.session.rollback()
