"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_posting_ledger_account_anchor_reconciliation.py``, moved verbatim
by ``tools/split``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import account_posting_service
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import create_settled_cash_transaction, observed_day_of
from app.services.row_valuation import settled_contribution


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a genuine
# second opinion: a bug shared by the walk and the reconcile cannot hide,
# because the ledger side reads ``account_postings`` and the source side reads
# ``transactions`` / the transfers' side records / ``account_anchor_history``
# with independently-written
# code, and both are also pinned to hand-computed literals.  Some mirror the
# Step-2 / Step-3 oracles (``_trial_balance``, ``_entries_violating_balance``);
# the duplication is DELIBERATE -- each oracle keeps its OWN independent queries
# so it remains a self-contained second opinion.


def _as_utc(instant: datetime) -> datetime:
    """Return *instant* as an aware-UTC datetime (independent of the walk).

    The oracle's own copy of the walk's instant convention (an aware value
    converts to UTC, a naive value is assumed UTC -- every ``timestamptz`` in
    this app is stored UTC), written here rather than imported from the SUT so
    the ``<=`` / ``>`` partition comparisons are an independent restatement,
    not a reuse of the code under test.
    """
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def _independent_linked_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a non-loan account's LINKED ledger legs in a scenario (independent).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount`` over the LINKED kind only.  Keyed off the REAL account via
    ``ledger_accounts.account_id`` -- a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger row
    first), so the two cannot share a lookup bug.

    Filtered to the LINKED kind because an anchor correction lands ``+delta``
    on the linked row and ``-delta`` on the ``anchor_equity`` twin, which shares
    the ``account_id`` column: a bare-``account_id`` sum would cancel the
    correction pairwise and silently reproduce the pre-Step-5 changes-only
    figure, making the absolute assertions vacuous.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .join(LedgerAccount, Posting.ledger_account_id == LedgerAccount.id)
        .filter(
            LedgerAccount.account_id == account_id,
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _latest_assertion(account_id: int) -> tuple[date, Decimal]:
    """Return an account's latest ``(asserted business day, anchor balance)``.

    Reads ``account_anchor_history`` directly -- the row with the max
    ``(observed_on, created_at, id)``, exactly the row
    ``cash_ledger.resolve_anchor`` picks -- so the "latest anchor" the invariant
    references is taken from the source of truth, not the
    ``current_anchor_balance`` cache the writer also maintains (which a bug
    could desync).

    **All THREE keys are load-bearing and this oracle ordered on only two until
    plan step X-f1.**  ``observed_on`` is the BUSINESS day the balance was true
    for and has been a user-supplied column since plan step 2, so the recording
    order and the business order can differ outright: a balance asserted for an
    earlier day but typed later is not the current one, and ``created_at`` first
    names it.  ``created_at`` then separates two assertions about one day, and
    ``id`` breaks a tie between two stamped at one instant.  The oracle stating
    a DIFFERENT "latest" rule than the engine is the shape that lets both be
    wrong together while the sweep reports clean.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(
            AccountAnchorHistory.observed_on.desc(),
            AccountAnchorHistory.created_at.desc(),
            AccountAnchorHistory.id.desc(),
        )
        .first()
    )
    assert row is not None, (
        f"account {account_id} has no anchor history -- every real account "
        f"carries its origination row, so this is a broken fixture"
    )
    return row.observed_on, Decimal(str(row.anchor_balance))


def _source_settled_day(txn) -> date:
    """Return a settled source's civil settle day, independently, or REFUSE.

    The source's stored ``settled_on`` -- the day its cash moved.  It derived
    the day from ``paid_at``, falling back to the pay period's ``start_date``,
    until plan step X-f1 replaced the instant with the stored day and the
    fallback with a refusal; this restates that refusal rather than importing
    it, because an oracle that shares the engine's accessor cannot catch the
    engine dating a row it should have refused.

    An ordinary cash transaction's only.  A transfer side's day is its
    RECORD's, read by :func:`_independent_transfer_side_effects`: it was the
    side's shadow's own ``settled_on`` until plan step ``balance:X-bi-6-4d-2``
    stopped keeping a shadow's day (ruling R-BAL167 class 4).
    """
    assert txn.settled_on is not None, (
        f"transaction {txn.id} is settled but carries no settled_on -- the "
        "engine refuses this row, so a fixture that produced it is broken"
    )
    return txn.settled_on


def _independent_source_effect(txn) -> Decimal:
    """Return a settled cash transaction's signed, debit-positive effect.

    The per-source truth the linked ledger must reflect: an ordinary cash
    transaction contributes ``effective - Sigma(credit entries)`` signed ``+``
    for income / ``-`` for an expense.  ``effective`` is the model property
    (``actual`` over ``estimated``).  Independent of the posting builder (it
    never imports ``_signed_cash_leg``); the linked leg for *txn* equals this.
    A transfer side's effect is its transfer's settled figure on its
    record's day, read by :func:`_independent_transfer_side_effects` (it was
    its shadow's settled figure here until plan step
    ``balance:X-bi-6-4d-2``, and its record's until that step's 4d-4 half;
    ruling R-BAL167 class 4).
    """
    credit_sum = sum(
        (entry.amount for entry in txn.entries if entry.is_credit),
        Decimal("0"),
    )
    effect = settled_contribution(txn) - credit_sum
    return effect if txn.is_income else -effect


def _independent_transfer_side_effects(
    account_id: int, scenario_id: int,
) -> list[tuple[date, Decimal]]:
    """Return ``(settle day, signed effect)`` per settled transfer side on an account.

    Over the RECORDS on *account_id* filed under a side of a settled
    (``status.is_settled``), non-deleted transfer in *scenario_id*: the
    record's stored ``settled_on`` and the TRANSFER's settled figure
    (``budget.transfers.settled_figure``) as ``+figure`` for a to-side record
    (``income_transfer_id``, money in) or ``-figure`` for a from-side record
    (``expense_transfer_id``, money out).  The figure is stored once on the
    transfer since plan step ``balance:X-bi-6-4d-2`` (its 4d-4 half); each
    side's record carried it until then and states no figure now.  A settled
    transfer holding no record is a ``$0.00`` close and moves nothing
    (R-BAL82).  A settled side's
    record carries its day, and an undated one under a settled transfer is a
    state the database refuses, so a missing day is REFUSED here as
    :func:`_source_settled_day` refuses one.  The transfer's status and the
    side links are read by this oracle's own SQL, never through
    ``transfer_legs``, the producer the posting writer joins: the record hangs
    off the transfer since plan step ``balance:X-bi-6-4d-2``, and this read
    the side's shadow's status, day and figure until then (ruling R-BAL167
    class 4).
    """
    rows = (
        _db.session.query(
            TransactionEntry.id,
            TransactionEntry.settled_on,
            Transfer.settled_figure,
            TransactionEntry.income_transfer_id,
        )
        .select_from(Transfer)
        .join(
            TransactionEntry,
            _db.or_(
                TransactionEntry.income_transfer_id == Transfer.id,
                TransactionEntry.expense_transfer_id == Transfer.id,
            ),
        )
        .filter(
            TransactionEntry.account_id == account_id,
            Transfer.scenario_id == scenario_id,
            Transfer.is_deleted.is_(False),
            Transfer.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    effects = []
    for record_id, settled_on, figure, income_transfer_id in rows:
        assert settled_on is not None, (
            f"record {record_id} is a settled transfer side's but carries no "
            "settled_on -- the database refuses this state, so a fixture "
            "that produced it is broken"
        )
        effects.append(
            (settled_on, figure if income_transfer_id is not None else -figure)
        )
    return effects


def _independent_post_assertion_source_effect(
    account_id: int, scenario_id: int, latest_asserted_day: date,
) -> Decimal:
    """Sum an account's settled source effect dated AFTER the latest anchor's day.

    Over the account's settled (``status.is_settled``), non-deleted ordinary
    transactions in *scenario_id* (:func:`_independent_source_effect`) AND
    the sides of its settled transfers there
    (:func:`_independent_transfer_side_effects`), add each source's signed
    effect iff its settle day is STRICTLY
    AFTER *latest_asserted_day* -- the sources that ride on top of the asserted
    balance.  An assertion is the CLOSING balance for its own civil day (ruling
    R-DH (a)), so a source dated ON that day is already inside it and is
    absorbed by the opening / true-up delta; only a strictly later day rides.
    Read from ``transactions``, ``transfers`` and ``transaction_entries`` (not
    the ledger side's tables), so asserting the equality reconciles what the
    producers wrote against the transaction source of truth.  It read the
    transfer shadows as sources beside the ordinary rows until plan step
    ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 4).
    """
    txns = (
        _db.session.query(Transaction)
        .filter(
            Transaction.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.in_(settled_status_ids()),
        )
        .all()
    )
    cash = sum(
        (
            _independent_source_effect(txn)
            for txn in txns
            if _source_settled_day(txn) > latest_asserted_day
        ),
        Decimal("0"),
    )
    return cash + sum(
        (
            effect
            for day, effect in _independent_transfer_side_effects(
                account_id, scenario_id,
            )
            if day > latest_asserted_day
        ),
        Decimal("0"),
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
# Sweep assertions (production-wide, run after each scenario's mutations)
# ---------------------------------------------------------------------------


def _assert_account_anchors_reconcile(scenario_id: int) -> None:
    """Assert every non-loan LINKED ledger reconciles ABSOLUTELY in *scenario_id*.

    For each of the scenario owner's non-loan real accounts, the independent
    linked-ledger sum equals the latest anchor balance plus the independent
    post-assertion source effect -- the Step-5 absolute invariant, holding over
    EVERY such account, not only the ones a given test hand-computes.  Amortizing
    loans are excluded: their absolute invariant couples on the amortization
    split and is the loan oracle's job.  The per-entry balance and trial balance
    are global self-checks (always true for a balanced ledger, asserted cheaply
    on every sweep).
    """
    scenario_owner_id = (
        _db.session.query(Scenario.user_id)
        .filter(Scenario.id == scenario_id)
        .scalar()
    )
    linked = (
        _db.session.query(LedgerAccount)
        .join(Account, LedgerAccount.account_id == Account.id)
        .join(AccountType, Account.account_type_id == AccountType.id)
        .filter(
            LedgerAccount.kind_id == ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
            AccountType.has_amortization.is_(False),
            Account.user_id == scenario_owner_id,
        )
        .all()
    )
    # Every owner carries at least the seeded Checking's linked ledger, so an
    # empty result means the query silently found nothing (a minting or filter
    # regression) and the loop below would pass vacuously -- assert non-empty so
    # the sweep cannot be a no-op.
    assert linked, (
        "no non-loan linked ledger accounts to reconcile -- the sweep would "
        "be vacuous (expected at least the Checking account's linked ledger)"
    )
    for ledger_account in linked:
        account_id = ledger_account.account_id
        latest_asserted_day, latest_anchor = _latest_assertion(account_id)
        ledger = _independent_linked_ledger_sum(account_id, scenario_id)
        effect = _independent_post_assertion_source_effect(
            account_id, scenario_id, latest_asserted_day,
        )
        assert ledger == latest_anchor + effect, (
            f"account {account_id}: linked ledger {ledger} != latest anchor "
            f"{latest_anchor} + post-assertion source effect {effect} in "
            f"scenario {scenario_id}"
        )
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


# ---------------------------------------------------------------------------
# Local fixture helpers (controlled assertion instants)
# ---------------------------------------------------------------------------


def _origin_instant(account) -> datetime:
    """Return the factory origination row's stored assertion instant (UTC).

    The one instant a test cannot choose (the origination row's ``created_at``
    is the INSERT transaction's server ``now()``); every other instant in a
    fixture is built relative to it so the pre / post / tie partitions are
    deterministic regardless of clock or timezone.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.created_at, AccountAnchorHistory.id)
        .first()
    )
    return _as_utc(row.created_at)


def _assert_balance_at(account, balance, created_at) -> AccountAnchorHistory:
    """Append a true-up ``AccountAnchorHistory`` row at a controlled instant.

    Mirrors ``anchor_service.stage_anchor_true_up`` (the history row plus the
    ``current_anchor_balance`` cache write) but pins ``created_at`` explicitly
    so the civil-day partition under test is exact -- the one non-production
    affordance, the same the C5 unit suite uses.  Anchors against the account's
    current anchor period; flushes.  The caller drives the reconcile
    (``sync_account_anchor_postings_all_scenarios``) afterward, exactly as the
    true-up chokepoint does.
    """
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
    return row


def _true_up_at(account, balance, created_at) -> None:
    """Assert a controlled true-up and reconcile it (the chokepoint's two steps).

    The deterministic stand-in for ``anchor_service.apply_anchor_true_up``: it
    stages the history row + cache (:func:`_assert_balance_at`) at a PINNED
    ``created_at`` and then drives the SAME all-scenarios reconcile the true-up
    chokepoint calls.  Pinning the instant is what makes the civil-day partition
    exact -- ``apply_anchor_true_up`` stamps ``created_at = now()``, which cannot
    be placed between two synthetic settles; the C5 unit suite uses the same
    affordance for the same reason.  The chokepoint itself is covered end to end
    by ``test_account_posting_service.py``; this oracle validates the resulting
    ledger against an independent second opinion.
    """
    _assert_balance_at(account, balance, created_at)
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )


def _settle_expense(seed_user, account, amount, settled_on):
    """Settle an expense on *account* on a pinned civil DAY; return it.

    ``settled_on`` is the day the cash moved, pinned so the posted entry and the
    walk's attribution agree, as in production.  Placed in the seed bootstrap
    period; the walk attributes by the settle DAY, so the period placement is
    immaterial.  It took an instant (or ``None``, for the period-start fallback)
    until plan step X-f1 removed both the derivation and the fallback.
    """
    return create_settled_cash_transaction(
        seed_user, _db.session, seed_user["bootstrap_period"],
        Decimal(str(amount)), account=account, settled_on=settled_on,
    )
