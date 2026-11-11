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
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.ledger_lines import category_key
# The two families' kind tables are the chart's own, package-private since plan
# task plan#3155 deleted their public sets (the stored chart's writer filed the
# two under different columns); a family filter reads them by name so the fact
# keeps one spelling.
from app.services.ledger_lines._chart import _ACCOUNT_COUNTER_KINDS
from app.utils.balance_predicates import balance_excluded_status_ids, settled_status_ids
from app.services import cash_ledger
from tests._ledger_reads import account_net, scenario_lines, trial_balance


# ---------------------------------------------------------------------------
# Independent source queries (test-authored, NOT the service helpers)
# ---------------------------------------------------------------------------
#
# The ledger side of this oracle is the worked-out read (``tests/_ledger_reads.py``,
# plan step plan#2913).  The source side below re-derives each account's effect
# from ``transactions``, ``transfers`` and ``transaction_entries`` with
# independently-written SQL/Python, and both are also pinned to hand-computed
# literals.  (The source side necessarily restates the one correct definition of a
# family's cash effect -- its dated non-card movements, plan step
# ``balance:X-bi-4a`` -- so the hand-computed literals, not these queries, are what
# make the oracle non-tautological; this layer adds the whole-owner sweep the
# literals cannot.)


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
    are one kind of fact.  Asserting it equal to the worked-out read reconciles
    what the read books against the source of truth.

    **It replaced two helpers** that between them spelled the pre-4a rule --
    ``effective - Sigma(credit)`` over SETTLED rows, plus the purchases posted
    under UNSETTLED parents -- an oracle that would have stayed green had the
    ledger regressed to booking the row's own leg beside its movement.

    Restated in SQL rather than shared with ``ledger_lines.purchase_posts``
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
    it holds, as the worked-out ledger books nothing for it
    (``ledger_lines.purchase_posts``' first term, ruling **R-FM**).  The gate
    is restated here from the same primitive the SQL twin filters on, not
    imported from the ledger.  (It was ``settled_contribution - Sigma(credit)``
    for a settled row through ``X-bi-3e``.)  Independent of ``ledger_lines``
    and ``cash_ledger`` (it calls neither producer); the counter leg the
    ledger should hold for
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


# ---------------------------------------------------------------------------
# Sweep assertions (production-wide, run after each scenario's mutations)
# ---------------------------------------------------------------------------


def _assert_linked_accounts_reconcile(scenario_id: int) -> None:
    """Assert every non-loan account's own line reconciles ABSOLUTELY in *scenario_id*.

    For each of the scenario owner's non-loan real accounts, its worked-out net
    equals the account's opening anchor plus the independent combined (transfer +
    transaction) source effect (every settle in this suite is stamped at
    server-now, after the origination assertion).  Amortizing loans are excluded
    -- their absolute invariant couples on the amortization split and is the loan
    oracle's job.  Holds over every such account, not only the ones a given test
    hand-computes.
    """
    owner_id = _db.session.get(Scenario, scenario_id).user_id
    account_ids = [
        account_id for (account_id,) in (
            _db.session.query(Account.id)
            .join(AccountType, Account.account_type_id == AccountType.id)
            .filter(
                AccountType.has_amortization.is_(False),
                Account.user_id == owner_id,
            )
        )
    ]
    # Every owner here holds at least the seeded Checking, so an empty result
    # means the query silently found nothing and the loop below would pass
    # vacuously -- assert non-empty so the sweep cannot be a no-op.
    assert account_ids, (
        "no non-loan accounts to reconcile -- the linked sweep would be vacuous "
        "(expected at least the Checking account)"
    )
    for account_id in account_ids:
        ledger = account_net(_db.session, account_id, scenario_id)
        effect = _independent_combined_source_effect(account_id, scenario_id)
        opening = _opening_anchor(account_id)
        assert ledger == opening + effect, (
            f"account {account_id}: ledger {ledger} != "
            f"opening {opening} + combined source effect {effect} in "
            f"scenario {scenario_id}"
        )


def _assert_counter_accounts_reconcile(scenario_id: int) -> None:
    """Assert every COUNTER line reconciles by its movement's parent.

    A counter line is one on a key with no real account's own money behind it: a
    category, the Uncategorized bucket, Transfers in transit.  The scenario's
    counter lines are netted per (key, parent), each parent being the row its
    movement sits under or, for a transfer side, the side's record: the record
    hangs off the transfer and carries no parent row.

    A row's net on its key is the negation of its independently-computed signed
    cash effect, and the key is the one its CURRENT category and type give
    (:func:`_assert_row_counter_net`), so a same-class miscategorization (a $50
    Groceries expense whose counter line landed on Rent: same class, same
    magnitude) is caught and not just a wrong amount.  A side's net is the
    negation of its record's effect, on transit
    (:func:`_assert_side_counter_net`).  Across a settled pair the two cancel,
    which the trial balance reads as transit at zero.

    A line on one of these keys comes from a movement, and the sweep refuses one
    that does not: a correction books on an account's own counters, which it
    skips, or on a loan's components, and no case here holds a loan with terms.
    """
    nets: dict[tuple, Decimal] = {}
    for line in scenario_lines(_db.session, scenario_id):
        if (
            line.key.kind is LedgerAccountKindEnum.LINKED
            or line.key.kind in _ACCOUNT_COUNTER_KINDS
        ):
            continue
        assert line.movement_id is not None, (
            f"counter {line.key}: a line of {line.amount} on "
            f"{line.entry_date} comes from no movement"
        )
        movement = _db.session.get(TransactionEntry, line.movement_id)
        cell = (
            line.key, movement.transaction_id,
            None if movement.transaction_id is not None else movement.id,
        )
        nets[cell] = nets.get(cell, Decimal("0")) + line.amount
    for (counter, transaction_id, record_id), net in nets.items():
        if record_id is None:
            _assert_row_counter_net(counter, transaction_id, net)
        else:
            _assert_side_counter_net(counter, record_id, net)


def _assert_row_counter_net(counter, transaction_id: int, net: Decimal) -> None:
    """Check one plan row's net on a counter key.

    A non-zero net comes from an active row with a DATED movement under it -- its
    covering movement or a purchase -- whatever its status (plan step
    ``balance:X-bi-4a``).  Routing: the key must be the one the row's CURRENT
    category gives, the Uncategorized bucket for a row with none.

    Args:
        counter: The :class:`~app.services.ledger_lines.ChartKey` the lines sit on.
        transaction_id: The parent row's id.
        net: The row's lines' net on *counter* in the swept scenario.
    """
    txn = _db.session.get(Transaction, transaction_id)
    assert txn.is_deleted is False
    expected_counter = -_signed_cash_effect(txn)
    assert net == expected_counter, (
        f"counter {counter}: transaction {transaction_id} net {net} "
        f"!= expected counter leg {expected_counter}"
    )
    ledger_class = (
        LedgerAccountClassEnum.INCOME if txn.is_income
        else LedgerAccountClassEnum.EXPENSE
    )
    assert category_key(txn.category_id, ledger_class) == counter, (
        f"counter {counter}: transaction {transaction_id} routed its counter "
        f"leg to the wrong category account"
    )


def _assert_side_counter_net(counter, record_id: int, net: Decimal) -> None:
    """Check one transfer side's record's net on a counter key.

    A side's net on the counter is the negation of its record's signed cash
    effect -- the transfer's settled figure ``+figure`` on transit for a from-side
    record (money left the account), ``-figure`` for a to-side record.  Routing: a
    side's line lands on the transit key and nowhere else -- a transfer between
    the owner's own accounts is neither income nor expense, so a category counter
    here is the very mis-post ruling R-BAL45 rejected.  Read off the record's own
    columns, the side links, and the figure off the TRANSFER's ``settled_figure``
    column (its one home since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half), not
    through ``transfer_legs`` (ruling R-BAL167 class 4).

    Args:
        counter: The :class:`~app.services.ledger_lines.ChartKey` the lines sit on.
        record_id: The side's ``budget.transaction_entries`` id.
        net: The record's lines' net on *counter* in the swept scenario.
    """
    record = _db.session.get(TransactionEntry, record_id)
    assert record.settled_on is not None, (
        f"counter {counter}: transfer side record {record_id} is un-dated "
        f"but holds a net {net} -- an un-dated record books no line"
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
        f"counter {counter}: transfer side record {record_id} net {net} "
        f"!= expected counter leg {expected_counter}"
    )
    assert counter.kind is LedgerAccountKindEnum.TRANSIT, (
        f"counter {counter}: transfer side record {record_id} routed its "
        f"counter leg to a non-transit account"
    )


def _assert_full_reconciliation(scenario_id: int) -> None:
    """Assert the whole ledger reconciles: linked, counter and trial balance.

    The production-wide sweep a case runs after its mutations; not every
    case runs it (measured 2026-11-02: 13 of the 16 cash cases do).  Linked and
    counter reconciliation are scenario-scoped over the worked-out read; the trial
    balance is its whole-ledger self-check.
    """
    _assert_linked_accounts_reconcile(scenario_id)
    _assert_counter_accounts_reconcile(scenario_id)
    assert trial_balance(_db.session) == Decimal("0")
