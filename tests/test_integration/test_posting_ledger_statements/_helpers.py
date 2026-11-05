"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_posting_ledger_statements.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import NamedTuple

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.services import account_posting_service, ledger_report_service

from tests._ledger_reads import ledger_net
from tests._test_helpers import observed_day_of


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


def _checking_at_its_opening(user_id):
    """Return the full balance sheet, asserting Checking holds its $1,000.00 opening.

    The seeded Checking's opening, and nothing on top of it, with the sheet
    tied out: what a fixture whose only activity nets to zero, or never
    reaches the baseline, leaves.  One spelling for the three cases that ask
    it, which the split of this package exposed as pylint R0801.
    """
    sheet = _reader_bs(user_id)
    assert _find_line(
        sheet.assets.lines, "Checking",
    ).amount == Decimal("1000.00")
    assert sheet.tie_out.in_balance is True
    return sheet


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
