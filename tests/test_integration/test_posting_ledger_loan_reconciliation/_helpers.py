"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_posting_ledger_loan_reconciliation.py``, moved verbatim by
``tools/split``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db as _db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import loan_ledger, loan_loaders, loan_resolver, posting_service
from app.services import amortization_engine
from app.services.loan_resolver._periods import _replay_from_anchor
from app.utils.money import round_money
from app.utils.balance_predicates import settled_status_ids
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    SPLIT_LOAN,
    create_loan_with_trueup,
    create_settled_transfer,
    loan_correction_entries,
    posted_loan_balance_at,
    posted_loan_balance_map,
    seam_confirmed_view,
)


# The shared synthetic split-loan fixture ($250,000 @ 6%, trued up to $100,000 --
# distinct so a correct interest figure proves the walk's anchor reset); see
# SPLIT_LOAN in tests/_test_helpers.py.  _P1/_P2/_P3 are the seed_periods indices
# (payment_day=1) due 02-01 / 03-01 / 04-01 -- distinct months after the anchor,
# so the resolver's biweekly-collision redistribution never shifts a date and the
# parallel run is exact.
_ORIGINATION_PRINCIPAL = SPLIT_LOAN.origination_principal


_ORIGINATION_DATE = SPLIT_LOAN.origination_date


_RATE = SPLIT_LOAN.rate


_ANCHOR_BALANCE = SPLIT_LOAN.anchor_balance


_ANCHOR_DATE = SPLIT_LOAN.anchor_date


_P1 = SPLIT_LOAN.p1


_P2 = SPLIT_LOAN.p2


_P3 = SPLIT_LOAN.p3


# The origination a case takes when a payment is walked BEFORE its true-up: the
# month before P1's 02-01 installment (plan step recurrence:R16-c-2, ruling
# R-R101).  Every contractual installment from origination is charged now, so
# SPLIT_LOAN's 2025-01-01 would have that payment clear twelve unpaid months
# first; restated, each figure stays what these cases were written to pin.
_ORIGINATION_BEFORE_P1 = date(2026, 1, 1)


# The frozen as-of: after every payment period used, so each settled payment is
# historical (eligible) and the resolver / wiring see the same today.
_AS_OF = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_loan(
    user, *, anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
    rate=_RATE, name="Oracle Loan", escrow_annual=None, payment_day=1,
    origination_date=_ORIGINATION_DATE,
):
    """Create a resolvable amortizing loan with the suite's controlled anchor.

    ``anchor_date`` defaults to 2026-01-10 (before every payment period used, so
    every settled payment is post-anchor and eligible); a caller pins a LATER
    date to place a payment pre-anchor (the read-switch boundary case).
    ``payment_day`` defaults to the 1st; a caller pins another day to control
    where each installment's due date falls relative to the biweekly grid.  A
    case that places a payment pre-anchor passes
    :data:`_ORIGINATION_BEFORE_P1` as *origination_date* (ruling R-R101).
    """
    return create_loan_with_trueup(
        user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=origination_date, name=name,
        escrow_annual=escrow_annual, payment_day=payment_day,
    )


def _confirmed_rows_at(loan_account_id, scenario_id, as_of):
    """Return the seam's CONFIRMED schedule rows for a loan at *as_of*.

    The walk-built confirmed view's rows (:func:`seam_confirmed_view`), which
    since plan step E1d-b are the confirmed slice of the loan's resolved schedule
    -- and, before it, were the deleted ``confirmed_loan_history_rows`` read of
    the posted ledger.  Pinned at an explicit scenario and as-of for the same
    reason :func:`_resolved_at` is.

    Args:
        loan_account_id: The loan account whose confirmed rows to read.
        scenario_id: The scenario to scope to.
        as_of: The display boundary.

    Returns:
        The chronological confirmed ``AmortizationRow`` list, or ``None`` when
        the seam has no confirmed view for this loan (not configured, no
        baseline, or not yet originated).
    """
    view = seam_confirmed_view(loan_account_id, scenario_id, as_of)
    return None if view is None else view.history_rows


def _resolved_at(loan_account_id, scenario_id, as_of):
    """Resolve ONE loan through the seam, at an explicit scenario and as-of.

    The suite's whole-loan read.  It goes through the seam's public entry
    (:func:`app.services.balance_at._resolution.resolved_loan`, where the db-facing
    resolution has lived since plan step E1d-a) rather than reaching into the
    private module behind it, and it pins the scenario EXPLICITLY -- several
    tests here resolve a loan under a named scenario (a what-if) or at a frozen
    as-of, which ``BalanceContext.build`` would not reproduce (it looks the
    user's BASELINE up for itself).

    Args:
        loan_account_id: The loan account to resolve.
        scenario_id: The scenario to scope the payment history to.
        as_of: The date to resolve the loan AT.

    Returns:
        The :class:`~app.services.balance_at._resolution.ResolvedLoan`, or ``None`` when the
        account is not a configured loan.
    """
    loan = _db.session.get(Account, loan_account_id)
    return resolved_loan(loan, BalanceContext(
        user_id=loan.user_id,
        scenario=_db.session.get(Scenario, scenario_id),
        as_of=as_of,
    ))


def _settle(
    user, loan, period, amount=Decimal("1000.00"), scenario=None,
    settled_on=None,
):
    """Settle a Checking -> loan payment transfer through the service.

    Routes through ``create_settled_transfer`` (the sole transfer writer), which
    posts the Step-2 cash entry AND fires the Commit-5 wiring that posts the
    Step-4 correction -- so the returned payment is fully posted, exactly as
    marking it Paid produces it.

    Settles ON the period's start by default (``settled_on=None``), so the
    payment is visible from its period start under C2's settled-date clock -- the
    on-time basis the resolver's confirmed-payment eligibility uses, keeping the
    reader-vs-resolver parallel run exact.  A test exercising an early / late
    settle passes an explicit ``settled_on`` civil date.
    """
    return create_settled_transfer(
        user, _db.session, user["account"], loan, period,
        amount=amount, scenario=scenario,
        settled_on=period.start_date if settled_on is None else settled_on,
    )


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These re-derive each side from scratch so the oracle is a genuine second
# opinion: the ledger side reads ``account_postings`` with an independently
# written join shape (keyed off the REAL loan account / the per-loan discriminator
# rather than resolving the ledger account first, as ``account_posting_total``
# does), and the source side reads ``transfers`` (status and settled figure)
# and their side records in ``transaction_entries``.  ``_trial_balance`` /
# ``_entries_violating_balance`` mirror the Step-2 / Step-3 oracles; the
# duplication is DELIBERATE -- each oracle keeps its OWN independent queries so it
# stays a self-contained second opinion.


def _independent_loan_linked_net(loan_account_id: int, scenario_id: int) -> Decimal:
    """Sum a loan's LINKED-ledger posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` and keys on the REAL ``ledger_accounts.account_id ==
    loan_account_id`` -- a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger account
    first), so the two cannot share a lookup bug.  The linked ledger carries the
    Step-2 cash (+cash) AND the Step-4 correction's loan leg
    (-(interest+escrow+excess)), so its net is exactly the summed real principal.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _per_loan_correction_net(loan_account_id: int, scenario_id: int) -> Decimal:
    """Sum a loan's PER-LOAN (interest/escrow/refund) ledger legs (independent).

    Keys on ``ledger_accounts.loan_account_id == loan_account_id`` -- the per-loan
    Expense / Asset accounts the correction backs the non-principal onto, which
    carry ``account_id IS NULL``.  Their net is exactly ``sum(interest + escrow +
    excess)`` across the loan's corrections: the amount the correction moves OFF
    the loan.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.loan_account_id == loan_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _independent_settled_income_cash(
    loan_account_id: int, scenario_id: int
) -> Decimal:
    """Sum a loan's settled payments' loan-side cash (independent query).

    The independent restatement of ``settled_transfer_effect`` for a loan: over
    the settled, non-deleted transfers INTO the loan in *scenario_id* that
    hold a loan-side RECORD -- the movement filed under the transfer's to-side
    (``income_transfer_id``) on the loan -- sum each TRANSFER's settled
    figure (``budget.transfers.settled_figure``), the figure's one home since
    plan step ``balance:X-bi-6-4d-2`` (its 4d-4 half; the side's record
    carried it until then and states no figure now).  A settled transfer with
    no record is a ``$0.00`` close and adds nothing (R-BAL82).  Every term is
    ``+figure`` -- the cash that flowed in.  It summed the loan-side income
    SHADOWS' settled figures by the shadow's status until plan step
    ``balance:X-bi-6-4d-2`` stopped keeping a shadow's status and moved the
    record onto the transfer; it reads the transfer's status, figure and the
    side link by its own SQL now, never through ``transfer_legs``, the
    producer the reader under test joins (ruling R-BAL167 class 4, plan step
    balance:X-bi-6-4d-2).  Reads the row tables, different tables than the
    ledger queries above, so asserting the ledger reconciles against this ties
    the postings to the transaction source of truth.
    """
    return (
        _db.session.query(
            _db.func.coalesce(
                _db.func.sum(Transfer.settled_figure), Decimal("0"),
            )
        )
        .select_from(Transfer)
        .join(
            TransactionEntry,
            _db.and_(
                TransactionEntry.income_transfer_id == Transfer.id,
                TransactionEntry.account_id == loan_account_id,
            ),
        )
        .filter(
            Transfer.to_account_id == loan_account_id,
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_status_ids()),
        )
        .scalar()
    )


def _trial_balance() -> Decimal:
    """Return ``SUM(account_postings.amount)`` over the whole ledger."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .scalar()
    )


def _entries_violating_balance() -> list[tuple[int, Decimal, int]]:
    """Return ``(entry_id, leg_sum, leg_count)`` for every malformed entry.

    A well-formed double-entry has ``leg_sum == 0`` and ``leg_count >= 2``.  Any
    row returned here is a violation -- the per-entry invariant the deferred
    trigger also enforces, re-checked from the ORM side.
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


# ---------------------------------------------------------------------------
# Parallel-run and sweep helpers
# ---------------------------------------------------------------------------


def _resolver_balance(
    loan_account_id: int, scenario_id: int, as_of: date
) -> Decimal:
    """Return the un-seeded anchor-replay balance -- the parallel reference.

    The oracle's genuinely independent producer: it replays the SCHEDULED payment
    (``principal = period_pi - interest``) forward from the latest anchor and
    discards the cash, so it NEVER reads the posted ledger -- the load-bearing
    property the whole parallel run rests on (module docstring invariants 1 / 7).

    Since the read switch (plan Section 8) the PRODUCTION balance reads the
    ledger's facts (the seam's fold; the seeded resolver threaded the confirmed
    balance in until plan step D2a deleted its balance field entirely) -- so a
    production window here would make the "resolver" read the very ledger it is
    meant to cross-check, collapsing the parallel run to a tautology.  This
    helper therefore runs the production replay derivation directly, on the
    production feed, preserving the schedule-replay reference.  The
    production path is verified separately -- that it equals the
    ledger off-schedule is the read switch, pinned by
    ``TestReadSwitchProductionPath``.

    **It takes its OWN inputs, and that is plan step balance:X-bl-2b** (finding
    **N-432**).  The replay reads three dates per payment and NO amount, but
    this assembled its feed through ``loan_payment_service.load_loan_context``,
    which prices every row through the amount model -- so the reference's
    import closure was **101 modules** where its own arithmetic needs **45**,
    and an ``AmountUnresolvable`` could break a control that reads no figure.
    Every module in that closure is a module this reference has to be right
    about; the amount-free feed
    (:func:`app.services.loan_ledger.payment_installments`) removes 56 of them.
    """
    params = loan_loaders.load_loan_params(loan_account_id)
    assert params is not None, "loan is not resolvable (no LoanParams)"
    # The DATES alone -- no pricing tier behind them.  ``options=()`` and
    # ``leg_options=()`` because nothing here traverses a relationship on the
    # settled rows or on the projected legs' parents.
    installments = loan_ledger.payment_installments(
        loan_account_id, scenario_id, params,
        options=(), leg_options=(),
    )
    periods = loan_resolver.resolve_periods(
        params, loan_loaders.load_rate_changes(loan_account_id),
    )
    # The replay derivation directly (``LoanState.current_balance`` carried it
    # until plan step D2a deleted the field): unchanged as the oracle's
    # independent schedule-replay reference.  ``slotted_dates`` is the ONE
    # biweekly-collision assignment, shared with the priced feed's
    # ``prepare_payments_for_engine``, so the two cannot resolve a collision
    # differently.  It is called MODULE-QUALIFIED on purpose: the roots control
    # below reads this body's qualifiers, and a bare name would hide the
    # dependency from it.
    return round_money(
        _replay_from_anchor(
            anchor_events=loan_loaders.load_loan_anchor_facts(params),
            periods=periods,
            payments=amortization_engine.slotted_dates(
                [installment.dates for installment in installments],
                params.payment_day,
            ),
            payment_day=params.payment_day,
            as_of=as_of,
        ).balance_as_of
    )


def _ledger_balance(loan_account_id: int, scenario_id: int) -> Decimal:
    """Return the ledger's genesis view of the current balance.

    ``-(sum of the loan's linked-ledger postings)``: under genesis the linked
    ledger carries the opening (-original_principal), every true-up correction,
    and every payment's principal (the Step-2 cash plus the Step-4 correction), so
    the negated sum IS the confirmed balance the read switch (plan Section 10)
    displays -- no anchor read, no boundary rule.  Numerically identical to the
    Step-4 formula this replaces (``anchor - linked_net``) on a
    post-anchor-only loan, because the opening + true-up legs sum to -(anchor) on
    the linked ledger; but genesis also SUMS a pre-anchor payment correctly (its
    principal is subsumed by the true-up's owed_before), so -- unlike the Step-4
    formula -- there is no pre-anchor exclusion to assume away.
    """
    return -_independent_loan_linked_net(loan_account_id, scenario_id)


def _posted_balance(
    loan_account_id: int, scenario_id: int, as_of: date = _AS_OF,
) -> Decimal:
    """Return what the postings say the loan owed on a DATE (the dated window).

    :func:`tests._test_helpers.posted_loan_balance_at`: the same linked-ledger
    legs :func:`_ledger_balance` sums, but bounded by ``entry_date <= as_of``
    and guarded by the OPENING sentinel.  A distinct derivation, so the two
    windows agreeing is a real check rather than one query written twice, and
    the only one that can answer a historical date at all.  Asserts non-``None``
    because every caller here builds a CONFIGURED loan (an opening is posted), so
    a ``None`` would be a defect, not an unconfigured loan (the ``None`` route is
    proven directly by its own test).
    """
    result = posted_loan_balance_at(
        loan_account_id, scenario_id, as_of,
    )
    assert result is not None, (
        f"posting window returned None for configured loan {loan_account_id} in "
        f"scenario {scenario_id} -- no OPENING posting where one was expected"
    )
    return result


def _posted_period_map(
    loan_account_id: int, scenario_id: int, periods: list[PayPeriod],
) -> "dict[int, Decimal]":
    """Return the dated posting window at every period END (the per-period form).

    :func:`tests._test_helpers.posted_loan_balance_map` -- the scalar window
    applied at each period's END, which is what lets the early-settle parallel
    run below check period boundaries past the frozen today (no posted entry in
    these fixtures is dated later, so such a period carries the confirmed sum
    flat).  Because it IS the scalar per period, never assert the two agree --
    that identity cannot fail; the assertions here are against the independent
    resolver.  Asserts non-``None`` for the same reason as
    :func:`_posted_balance`.
    """
    result = posted_loan_balance_map(
        loan_account_id, scenario_id, periods,
    )
    assert result is not None, (
        f"posting window map returned None for configured loan {loan_account_id} "
        f"in scenario {scenario_id} -- no OPENING posting where one was expected"
    )
    return result


def _assert_completeness(
    loan_account_id: int, scenario_id: int, as_of: date
) -> None:
    """Assert every settled payment that owes a correction has one.

    Plan 8.3: for each settled payment (the split walk's output -- since the R1
    split-at-settlement fix that is EVERY settled payment, including one whose
    pay period has not yet begun), a non-zero non-principal part (interest +
    escrow + excess) means the loan's cash is partly non-principal, so a
    correction MUST exist; an all-principal payment (a zero-rate, no-escrow,
    no-overpay payment) legitimately carries none.  The pre-R1 "future-dated
    settled payment is flagged, not covered" sentinel is retired: settlement is
    now the confirming event, so an early-settled payment is inside this
    guarantee, not outside it.
    """
    splits = loan_ledger.compute_loan_payment_splits(
        loan_account_id, scenario_id,
    )
    # Every caller settles at least one payment before reconciling, so an empty
    # split walk means the loader silently found nothing (a scenario-scope or
    # settled-filter regression) and the completeness loop below would pass
    # vacuously -- assert the enumeration is non-empty so it cannot.
    assert splits, (
        f"loan {loan_account_id} scenario {scenario_id}: the split walk found no "
        f"settled payments to check completeness over -- the sweep would be vacuous"
    )
    # A split's ``source`` is the payment's loan-side TransferLeg since plan step
    # balance:X-bi-6-4d-2, not its income shadow; the shared reader keys the
    # corrections off the transfer and the loan side's record, and is passed
    # the transfer and the loan since plan step balance:X-bi-6-4d-2, where no
    # shadow exists to name the side (ruling R-BAL167 class 4).
    for split in splits:
        non_principal = split.interest + split.escrow + split.excess
        transfer_id = split.source.transfer.id
        entries = loan_correction_entries(
            _db.session, transfer_id, loan_account_id,
        )
        if non_principal != Decimal("0"):
            assert entries, (
                f"settled payment transfer {transfer_id} has non-"
                f"principal {non_principal} but no correction -- an uncorrected "
                f"Step-2 cash entry"
            )


def _assert_loan_reconciles(
    loan, scenario_id: int, as_of: date
) -> None:
    """Assert a loan's whole ledger reconciles: three-way + completeness + trial.

    The production-wide sweep run after each fixture's mutations (plan 8.3 / 8.4 /
    8.7).  It ties together, for one loan in one scenario:

    * (a) the production reader ``account_posting_total`` equals the independent
      linked-ledger query -- the two computed the same net two different ways;
    * (b) the loan-aware superseding invariant, entirely from independent queries:
      ``linked_net == settled_income_cash - per_loan_correction_net`` (the loan
      nets to cash minus the non-principal moved off it);
    * (c) the same invariant through the PRODUCTION readers
      (``account_posting_total == settled_transfer_effect - per_loan_net``), so
      the posted-ledger readers the balance sheet and statements consume satisfy
      it too;
    * (d) completeness -- every eligible payment owing a correction has one;
    * (e) per-entry balance and a zero whole-ledger trial balance.

    IMPORTANT -- this is a STRUCTURAL / reader-consistency sweep, NOT a split-VALUE
    check.  (a)/(b)/(c)/(e) are accounting IDENTITIES: given Step 2 is correct and
    every correction balances (both DB-enforced by ``ck_account_postings_balanced``),
    they hold no matter WHAT interest/principal the split posted -- if the split
    booked interest 700 instead of 500, ``per_loan`` and the loan leg both shift and
    the identities still pass (this is why, in the injected-``+$10`` experiment, only
    the two invariant-only tests survived).  So every caller MUST pair this with a
    value assertion -- a parallel-run ``ledger == resolver`` or a hand-computed
    literal -- which is what actually pins the split; the sweep's job is to catch a
    reader / scenario-scope / routing / balance defect the value checks do not.
    (b)/(c) also assume the loan has NO OUTBOUND transfer:
    ``_independent_settled_income_cash`` sums transfers INTO the loan only, which
    restates ``settled_transfer_effect`` faithfully only while every loan side is a
    to-side (income) -- true for a payment, false for a hypothetical disbursement.
    That assumption is now ENFORCED, not merely relied on: a transfer OUT of a
    loan is rejected at creation
    (``transfer_service`` / ``_reject_transfer_out_of_loan``, review R6), so every
    transfer touching a loan is a payment IN by construction.
    """
    loan_id = loan.id
    linked_reader = posting_service.account_posting_total(loan_id, scenario_id)
    linked_independent = _independent_loan_linked_net(loan_id, scenario_id)
    per_loan = _per_loan_correction_net(loan_id, scenario_id)
    income_independent = _independent_settled_income_cash(loan_id, scenario_id)
    income_reader = posting_service.settled_transfer_effect(loan_id, scenario_id)

    # (a) production reader == independent linked-ledger query.
    assert linked_reader == linked_independent, (
        f"loan {loan_id}: account_posting_total {linked_reader} != independent "
        f"linked net {linked_independent} in scenario {scenario_id}"
    )
    # (b) superseding invariant, fully independent.
    assert linked_independent == income_independent - per_loan, (
        f"loan {loan_id}: linked net {linked_independent} != income cash "
        f"{income_independent} - non-principal corrections {per_loan}"
    )
    # (c) superseding invariant through the production readers.
    assert income_reader == income_independent, (
        f"loan {loan_id}: settled_transfer_effect {income_reader} != independent "
        f"income cash {income_independent}"
    )
    assert linked_reader == income_reader - per_loan, (
        f"loan {loan_id}: account_posting_total {linked_reader} != "
        f"settled_transfer_effect {income_reader} - corrections {per_loan}"
    )
    # (d) completeness.
    _assert_completeness(loan_id, scenario_id, as_of)
    # (e) per-entry balance + trial balance (whole-ledger self-checks).
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")
