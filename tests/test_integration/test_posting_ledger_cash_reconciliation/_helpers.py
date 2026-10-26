"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_posting_ledger_cash_reconciliation.py``, moved verbatim by
``tools/split``.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services import ledger_account_service
from app.utils.balance_predicates import balance_excluded_status_ids, settled_status_ids
from app.services import cash_ledger


# ---------------------------------------------------------------------------
# Independent reconciliation queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# These deliberately re-derive each side from scratch so the oracle is a genuine
# second opinion: a bug shared by the two service readers cannot hide, because
# the ledger side here reads ``account_postings`` and the source side reads
# ``transactions``, ``transfers`` and ``transaction_entries`` with
# independently-written SQL/Python, and both are also
# pinned to hand-computed literals.  (The source side necessarily restates the
# one correct definition of a family's cash effect -- its dated non-card
# movements, plan step ``balance:X-bi-4a`` -- so it mirrors
# ``posted_purchase_effect``'s semantics; the hand-computed literals, not these
# queries, are what make the oracle non-tautological -- this layer adds the
# cross-table, whole-DB sweep the literals cannot.)
#
# Some of these (``_independent_ledger_sum``, ``_trial_balance``,
# ``_entries_violating_balance``, ``_independent_transfer_shadow_effect``) mirror
# the Step-2 oracle (``test_posting_ledger_reconciliation.py``).  The duplication
# is DELIBERATE, not an oversight: each oracle keeps its OWN independent
# reconciliation queries so it remains a self-contained second opinion (the
# Step-2 module docstring states the same "re-derive from scratch" intent).
# Genuinely shared *utilities* with no independence role (the migration loader,
# the raw-SQL posting clear, the account-pairing lookup) live in
# ``tests/_test_helpers.py`` instead.


def _independent_ledger_sum(account_id: int, scenario_id: int) -> Decimal:
    """Sum a REAL account's posting legs in a scenario (independent query).

    Joins ``account_postings`` -> ``journal_entries`` (for the scenario) ->
    ``ledger_accounts`` (for the real ``account_id``), summing the signed
    ``amount`` over BOTH transfer and transaction legs.  Keyed off the REAL
    account via ``ledger_accounts.account_id``, a different join shape than
    ``posting_service.account_posting_total`` (which resolves the ledger account
    first), so the two cannot share a lookup bug.

    Filtered to the LINKED kind (Step 5): an anchor correction lands
    ``+delta`` on the linked row and ``-delta`` on the ``anchor_equity``
    twin, which shares the ``account_id`` column -- a bare-``account_id``
    sum would cancel the correction pairwise and silently reproduce the
    pre-Step-5 changes-only figure, making the absolute assertions vacuous.
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


def _opening_anchor(account_id: int) -> Decimal:
    """Return an account's anchor balance -- its posted opening's target.

    The Step-5 opening correction drives the linked ledger to exactly the
    account's asserted anchor (every account in this suite carries only its
    origination assertion, and every settle is stamped at server-now, after
    it), so the absolute reconciliation is ``linked ledger == anchor +
    settled source effect``.
    """
    # The account's asserted balance, read from the assertion itself: ruling
    # R-EH deleted the ``accounts.current_anchor_balance`` column this queried,
    # which was a copy of the same row.
    return cash_ledger.resolve_anchor(
        _db.session.get(Account, account_id),
    ).balance


def _ledger_account_sum(ledger_account_id: int, scenario_id: int) -> Decimal:
    """Sum one SPECIFIC ledger account's posting legs in a scenario.

    The counter-account (category / fallback / orphan) analog of
    :func:`_independent_ledger_sum`: keyed on the ledger account's own ``id``
    rather than a real ``account_id``, because a non-linked Income/Expense
    account has no ``account_id``.  Scenario-scoped via the journal entry's
    denorm, so the same owner-scoped category account reconciles independently in
    each scenario that booked into it.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .select_from(Posting)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _independent_transfer_shadow_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum an account's settled transfer-side effect (independent query).

    The transfer half of the balance-side truth: over the RECORDS on the
    account filed under a side of a settled (``status.is_settled``),
    non-deleted transfer in *scenario_id*, add the TRANSFER's settled figure
    (``budget.transfers.settled_figure``) as ``+figure`` for a to-side record
    (``income_transfer_id``, money in) and ``-figure`` for a from-side record
    (``expense_transfer_id``, money out).  The figure is stored once on the
    transfer since plan step ``balance:X-bi-6-4d-2`` (its 4d-4 half); each
    side's record carried it until then and states no figure now.  A settled
    transfer holding no record is a ``$0.00`` close and adds nothing
    (R-BAL82).  The same shape as the Step-2 oracle's transfer
    reconciliation, read from the row tables.  It summed the settled transfer
    SHADOWS' figures by each shadow's status and type until plan step
    ``balance:X-bi-6-4d-2`` stopped keeping a shadow's status and moved the
    record onto the transfer; it reads the transfer's status, figure and the
    side links by its own SQL now, never through ``transfer_legs``, the
    producer ``settled_transfer_effect`` joins (ruling R-BAL167 class 4, plan
    step balance:X-bi-6-4d-2).  The name is kept.
    """
    signed = case(
        (
            TransactionEntry.income_transfer_id.isnot(None),
            Transfer.settled_figure,
        ),
        else_=-Transfer.settled_figure,
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(signed), Decimal("0"))
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
        .scalar()
    )


def _independent_movement_effect(account_id: int, scenario_id: int) -> Decimal:
    """Sum an account's DATED movements' effect (independent query).

    The non-transfer half of the balance-side truth since plan step
    ``balance:X-bi-4a`` (ruling **R-BAL80**): a plan row posts nothing of its
    own, so every cash leg the ledger holds for an ordinary transaction's
    family is a movement's.  Over every non-card ``transaction_entries`` row
    carrying a ``settled_on`` ON THIS ACCOUNT (``TransactionEntry.account_id``,
    the movement's own -- ruling **R-BAL75**) whose parent is a non-deleted,
    balance-contributing, NON-transfer transaction in *scenario_id*, sum the
    amount signed by the PARENT's type (``+`` income / ``-`` expense; plan
    step X-bi-3b, ruling **R-BAL35**), whatever the parent's STATUS: a dated
    purchase under an open envelope and a settled bill's covering movement
    are one kind of fact.  Reads ``transaction_entries`` and ``transactions``,
    different tables than :func:`_independent_ledger_sum`, so asserting the
    two equal reconciles what the producers wrote against the source of
    truth.

    **It replaced two helpers** that between them spelled the pre-4a rule --
    ``effective - Sigma(credit)`` over SETTLED rows, plus the purchases posted
    under UNSETTLED parents -- an oracle that would have stayed green had the
    writer regressed to posting the row's own leg beside its movement.

    Restated in SQL rather than shared with ``_posting_purchases.purchase_posts``
    or ``cash_ledger.movement_cash_leg``, for the reason every helper in this
    file is: an oracle that imported the rule it grades could not grade it.
    """
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    signed = case(
        (
            Transaction.transaction_type_id == income_type_id,
            TransactionEntry.amount,
        ),
        else_=-TransactionEntry.amount,
    )
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(signed), Decimal("0"))
        )
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            TransactionEntry.account_id == account_id,
            Transaction.scenario_id == scenario_id,
            Transaction.transfer_id.is_(None),
            Transaction.is_deleted.is_(False),
            Transaction.status_id.notin_(balance_excluded_status_ids()),
            TransactionEntry.settled_on.isnot(None),
            TransactionEntry.is_credit.is_(False),
        )
        .scalar()
    )


def _independent_combined_source_effect(
    account_id: int, scenario_id: int
) -> Decimal:
    """Sum an account's combined settled transfer + transaction source effect.

    The full balance-side truth a linked account's ledger must equal in Step 3:
    transfer sides' records AND every dated movement of an ordinary
    transaction, both
    signed debit-positive (plan step ``balance:X-bi-4a``, ruling **R-BAL80**).
    The independent restatement of ``settled_transfer_effect +
    posted_purchase_effect``.
    """
    return (
        _independent_transfer_shadow_effect(account_id, scenario_id)
        + _independent_movement_effect(account_id, scenario_id)
    )


def _signed_cash_effect(txn: Transaction) -> Decimal:
    """Return a transaction family's signed, debit-positive cash effect.

    The per-row independent computation used by the per-counter sweep: the
    sum of the row's DATED non-card movements, signed ``+`` for income / ``-``
    for an expense, whatever the row's status WITHIN the contributing gate --
    a plan row posts nothing of its own since plan step ``balance:X-bi-4a``
    (ruling **R-BAL80**), so its family's whole effect is its movements'; and
    a soft-deleted or balance-excluded parent's family is worth ``0`` whatever
    it holds, as the writer posts nothing for it (``purchase_posts``'s first
    term, ruling **R-FM**).  The gate is restated here from the same
    primitive the SQL twin filters on, not imported from the writer.  (It was
    ``settled_contribution - Sigma(credit)`` for a settled row through
    ``X-bi-3e``.)  Independent of ``posting_service`` and ``cash_ledger`` (it
    imports neither producer); the counter leg the ledger should hold for
    *txn* is the negation of this.
    """
    if txn.is_deleted or txn.status_id in balance_excluded_status_ids():
        return Decimal("0")
    effect = sum(
        (
            entry.amount for entry in txn.entries
            if not entry.is_credit and entry.settled_on is not None
        ),
        Decimal("0"),
    )
    return effect if txn.is_income else -effect


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


def _assert_linked_accounts_reconcile(scenario_id: int) -> None:
    """Assert every non-loan LINKED ledger reconciles ABSOLUTELY in *scenario_id*.

    For each of the scenario owner's non-loan real accounts (its LINKED
    ledger row), the independent ledger sum equals the account's opening
    anchor plus the independent combined (transfer + transaction) source
    effect -- the Step-5 absolute form (every settle in this suite is
    stamped at server-now, after the origination assertion; every swept
    scenario carries its owner's openings, posted in the baseline at create
    time or into a what-if by the effect-time self-heal alongside that
    scenario's first settle -- the latter ONLY because these fixtures
    settle on the same UTC day the accounts were created; not a general
    guarantee, and R8 owns the residual multi-scenario policy).  Amortizing loans are excluded -- their
    absolute invariant couples on the amortization split and is the loan
    oracle's job.  Holds over every such account, not only the ones a given
    test hand-computes.
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
    # Every caller settles at least one cash movement, which mints the Checking
    # linked ledger account, so an empty result means the query silently found
    # nothing (a minting or filter regression) and the loop below would pass
    # vacuously -- assert non-empty so the sweep cannot be a no-op.
    assert linked, (
        "no linked ledger accounts to reconcile -- the linked sweep would be "
        "vacuous (expected at least the Checking account's linked ledger)"
    )
    for ledger_account in linked:
        ledger = _independent_ledger_sum(ledger_account.account_id, scenario_id)
        effect = _independent_combined_source_effect(
            ledger_account.account_id, scenario_id
        )
        opening = _opening_anchor(ledger_account.account_id)
        assert ledger == opening + effect, (
            f"account {ledger_account.account_id}: ledger {ledger} != "
            f"opening {opening} + combined source effect {effect} in "
            f"scenario {scenario_id}"
        )


def _assert_counter_accounts_reconcile(scenario_id: int) -> None:
    """Assert every COUNTER ledger account reconciles by transaction_id linkage.

    For each non-linked (category / fallback / orphan) ledger account CA, sum its
    posting legs in *scenario_id* (the LHS), then -- via the
    ``journal_entries.transaction_id`` linkage, NOT a ``category_id`` match --
    group those legs by transaction and check each transaction's net on CA equals
    the negation of its independently-computed signed cash effect.  A transaction
    whose net on CA is zero (reversed, or recategorized away) is excluded by the
    non-zero-net guard, so a recategorize (which leaves a net-zero reversal pair
    on the OLD account) reconciles; an orphan, whose transactions now read
    ``category_id IS NULL`` and could not be found by ``category_id`` matching,
    reconciles because the linkage still points its legs at it.

    Beyond the magnitude, a still-categorized transaction's leg is also checked
    for correct ROUTING -- its current ``category_id`` must resolve to CA -- so a
    same-class miscategorization (a $50 Groceries expense whose counter leg landed
    on the Rent-Expense account: same class, same magnitude) is caught, not just a
    wrong amount.  A NULL-category transaction skips the routing check: it resolves
    to the fallback, but its leg may legitimately sit on an ORPHAN (its category
    was deleted and the row never re-synced) -- precisely why the orphan is
    reconciled by the linkage, not by re-resolving ``category_id``.

    **A MOVEMENT's leg is resolved to its PARENT before grouping** (plan step
    X-f3b, ruling **R-FM**): it links by ``transaction_entry_id`` and carries no
    ``transaction_id``, so grouping on that column alone would drop every one
    into the hard-deleted bucket and fail with the wrong cause.  Resolved, the
    per-transaction expectation stays whole -- a row's counter net is its dated
    movements' (the covering movement's and its purchases'), whatever its
    status, since plan step ``balance:X-bi-4a`` (ruling **R-BAL80**).

    A ``transaction_id IS NULL`` group is a hard-deleted transaction's
    SET-NULL'd legs: the reverse-before-delete pair MUST net to zero, asserted
    here so a stranded (un-reversed) leg would be caught.

    **The owner's Transfers-in-transit account is a counter account too**
    (plan step ``balance:X-bi-6-3``, rulings **R-BAL45** and **R-BAL101**):
    a transfer SIDE's record books its cash leg against it, so a side's net
    on transit is the negation of its signed cash effect by the very rule
    every category row obeys -- the from-side record lands ``+figure`` on
    transit, the to-side record's ``-figure`` -- and the routing check for a
    side is that the counter IS the transit row, never a category.  Across a
    settled pair the two nets cancel, which the trial-balance and per-linked
    sweeps read as transit at zero.  **Each side's legs are grouped by its
    RECORD** since plan step ``balance:X-bi-6-4d-2``: the record hangs off
    the transfer and carries no ``transaction_id``, so resolving it to a
    parent row would drop both sides into the hard-deleted bucket, where the
    pair nets to zero and neither side's figure nor its routing is read.
    They resolved to the side's SHADOW, the record's parent, until then
    (ruling R-BAL167 class 4).
    """
    counters = (
        _db.session.query(LedgerAccount)
        .filter(LedgerAccount.account_id.is_(None))
        .all()
    )
    for counter in counters:
        lhs = _ledger_account_sum(counter.id, scenario_id)
        # A MOVEMENT's counter leg links by ``transaction_entry_id`` and carries
        # NO ``transaction_id`` (plan step X-f3b, ruling **R-FM**), so grouping
        # on that column alone would drop every one of them into the
        # hard-deleted bucket below and fail with the wrong cause.  Resolving it
        # to the movement's PARENT is what keeps the per-transaction expectation
        # whole: a row's counter net is its dated movements' (plan step
        # ``balance:X-bi-4a``).
        parent_of_purchase = (
            _db.session.query(TransactionEntry.transaction_id)
            .filter(TransactionEntry.id == JournalEntry.transaction_entry_id)
            .correlate(JournalEntry)
            .scalar_subquery()
        )
        source_transaction_id = _db.func.coalesce(
            JournalEntry.transaction_id, parent_of_purchase,
        )
        # A transfer side's record carries no parent row at all (it hangs off
        # the TRANSFER since plan step balance:X-bi-6-4d-2), so its legs group
        # by the record itself (ruling R-BAL167 class 4).
        side_record = (
            _db.session.query(TransactionEntry.id)
            .filter(
                TransactionEntry.id == JournalEntry.transaction_entry_id,
                _db.or_(
                    TransactionEntry.expense_transfer_id.isnot(None),
                    TransactionEntry.income_transfer_id.isnot(None),
                ),
            )
            .correlate(JournalEntry)
            .scalar_subquery()
        )
        rows = (
            _db.session.query(
                source_transaction_id,
                side_record,
                _db.func.sum(Posting.amount),
            )
            .select_from(Posting)
            .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
            .filter(
                Posting.ledger_account_id == counter.id,
                JournalEntry.scenario_id == scenario_id,
            )
            .group_by(source_transaction_id, side_record)
            .all()
        )
        rhs = Decimal("0")
        for transaction_id, side_record_id, net in rows:
            if side_record_id is not None:
                rhs += _checked_side_counter_net(counter, side_record_id, net)
                continue
            if transaction_id is None:
                # Legs whose source transaction was hard-deleted (transaction_id
                # SET NULL): the reverse-before-delete pair must net to zero.
                assert net == 0, (
                    f"counter {counter.id}: orphaned (transaction_id NULL) "
                    f"legs net {net}, not zero -- a delete failed to reverse"
                )
                continue
            if net == 0:
                # Reversed or recategorized-away on this account: contributes
                # nothing, and the source row is correctly accounted elsewhere.
                continue
            txn = _db.session.get(Transaction, transaction_id)
            assert txn is not None, (
                f"counter {counter.id}: transaction {transaction_id} linked to "
                f"a non-zero leg no longer exists (link should have SET NULL)"
            )
            # A non-zero net on a counter account comes from an active row
            # with a DATED movement under it -- its covering movement or a
            # purchase -- whatever its status (plan step ``balance:X-bi-4a``);
            # everything else nets to zero.  A transfer side's record counts
            # against the owner's transit row by the same rule (plan step
            # ``balance:X-bi-6-3``), checked by ``_checked_side_counter_net``.
            assert txn.is_deleted is False
            expected_counter = -_signed_cash_effect(txn)
            assert net == expected_counter, (
                f"counter {counter.id}: transaction {transaction_id} net {net} "
                f"!= expected counter leg {expected_counter}"
            )
            # Routing: a still-categorized transaction's leg must land on the
            # account its CURRENT category resolves to (catches a same-class
            # wrong-category post the magnitude check alone would miss).  A
            # NULL-category transaction is skipped -- it resolves to the fallback,
            # but its leg may legitimately sit on an orphan (deleted category).
            if txn.category_id is not None:
                ledger_class = (
                    LedgerAccountClassEnum.INCOME if txn.is_income
                    else LedgerAccountClassEnum.EXPENSE
                )
                assert _counter_ledger_id(
                    txn.pay_period.user_id, ledger_class, txn.category_id,
                ) == counter.id, (
                    f"counter {counter.id}: transaction {transaction_id} routed "
                    f"its counter leg to the wrong category account"
                )
            rhs += net
        assert lhs == rhs, (
            f"counter {counter.id}: ledger {lhs} != linkage-summed source "
            f"effect {rhs} in scenario {scenario_id}"
        )


def _checked_side_counter_net(counter, record_id: int, net: Decimal) -> Decimal:
    """Check one transfer side's record's net on a counter account; return it.

    The transfer-side arm of :func:`_assert_counter_accounts_reconcile`: a
    side's net on the counter is the negation of its record's signed cash
    effect -- the transfer's settled figure ``+figure`` on transit for a
    from-side record (money left the account), ``-figure`` for a to-side
    record -- or zero for a record the ledger holds no money for (an
    un-dated record, kept across a revert whose settle and reversal cancel).
    Routing: a side's leg lands on the transit row and nowhere else -- a
    transfer between the owner's own accounts is neither income nor expense,
    so a category counter here is the very mis-post ruling R-BAL45 rejected.
    Read off the record's own columns, the side links, and the figure off the
    TRANSFER's ``settled_figure`` column (its one home since plan step
    ``balance:X-bi-6-4d-2``'s 4d-4 half; the record carried it until then),
    not through ``transfer_legs``; it checked the side's SHADOW here until
    plan step ``balance:X-bi-6-4d-2`` moved the record onto the transfer
    (ruling R-BAL167 class 4).

    Args:
        counter: The counter :class:`LedgerAccount` the legs sit on.
        record_id: The side's ``budget.transaction_entries`` id.
        net: The record's legs' net on *counter* in the swept scenario.

    Returns:
        *net*, for the caller's linkage sum.
    """
    if net == 0:
        # A settle and its reversal: contributes nothing.
        return net
    record = _db.session.get(TransactionEntry, record_id)
    assert record.settled_on is not None, (
        f"counter {counter.id}: transfer side record {record_id} is un-dated "
        f"but holds a non-zero net {net} -- a revert failed to reverse"
    )
    figure = _db.session.get(
        Transfer,
        record.income_transfer_id if record.income_transfer_id is not None
        else record.expense_transfer_id,
    ).settled_figure
    expected_counter = (
        -figure if record.income_transfer_id is not None
        else figure
    )
    assert net == expected_counter, (
        f"counter {counter.id}: transfer side record {record_id} net {net} "
        f"!= expected counter leg {expected_counter}"
    )
    assert counter.kind_id == ref_cache.ledger_account_kind_id(
        LedgerAccountKindEnum.TRANSIT,
    ), (
        f"counter {counter.id}: transfer side record {record_id} routed its "
        f"counter leg to a non-transit account"
    )
    return net


def _assert_full_reconciliation(scenario_id: int) -> None:
    """Assert the whole ledger reconciles: linked, counter, per-entry, trial.

    The production-wide sweep run after each test's mutations.  Linked and
    counter reconciliation are scenario-scoped; the per-entry balance and trial
    balance are global self-checks (always true for a balanced ledger, asserted
    cheaply on every sweep).
    """
    _assert_linked_accounts_reconcile(scenario_id)
    _assert_counter_accounts_reconcile(scenario_id)
    assert _entries_violating_balance() == []
    assert _trial_balance() == Decimal("0")


def _counter_ledger_id(
    user_id: int, ledger_class, category_id: int | None
) -> int:
    """Resolve the category / fallback ledger account id, mirroring the resolver.

    Returns the row the go-forward reconcile created, so a leg can be hand-checked
    against it; ``category_id=None`` resolves the per-(owner, class) Uncategorized
    fallback.
    """
    return ledger_account_service.get_or_create_category_ledger_account(
        user_id, category_id, ledger_class,
    ).id
