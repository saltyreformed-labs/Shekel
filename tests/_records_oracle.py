"""
Shekel Budget App -- the records oracle: an account's settled money, by query

The suite's own independent statement of what an account's settled records
moved, which the worked-out ledger's tests pit the read against.  Two terms:

* :func:`settled_transfer_effect` -- an account's dated transfer legs, each at
  its transfer's settled figure, signed by side;
* :func:`posted_purchase_effect` -- an account's dated non-transfer movements,
  each signed by its parent's type.

Their sum is what the account's own line in the worked-out ledger holds once
its anchor corrections are set aside, which is the per-account invariant the
reconciliation suites grade.

**They read the RECORDS and restate the posting rule in SQL rather than share
it** -- the deliberate independence every oracle here keeps.  An oracle that
imported ``ledger_lines.purchase_posts`` or ``cash_ledger.movement_cash_leg``
could not grade them: a wrong rule would move both sides together.  What IS
shared is the base join and the transfer link,
:func:`app.services.transfer_legs.transfer_movement_rows`, so gate, scope and
direction are the transfer's own.

*They lived in ``app/services/posting_reads.py`` (re-exported by
``posting_service``) until plan task plan#3132 moved them here: only tests
called them, and the stored ledger's writers that module served went after
it, at plan task plan#3133.*
"""

from decimal import Decimal

from sqlalchemy import case

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from app.services.transfer_legs import transfer_movement_rows
from app.utils.balance_predicates import balance_excluded_status_ids


def settled_transfer_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its settled transfer legs.

    Over every DATED, debit covering movement ON the account (its own
    ``account_id``, ruling **R-BAL75**) that is a leg's record of a transfer
    in *scenario_id*, its transfer's settled figure (its one home since plan
    step ``balance:X-bi-6-4d-2``), ``+`` on the to-side (money in) and ``-``
    on the from-side (money out).  A settled leg's money IS its dated
    movement (rulings **R-BAL79** / **R-BAL80**), so "settled" here is the
    leg's, not the status's.  A transfer closed at ``$0.00`` holds no
    movement and adds nothing (ruling **R-BAL82**).

    **The narrowings are the posting rule, restated** (leaf ``X-bi-6-4a``):
    the worked-out ledger books a movement iff it is dated, a debit, and its
    parent contributes, whatever the parent's status (ruling **R-BAL101**),
    by its loader's clauses (``cash_ledger._movement_scope._dated_arms``)
    and ``ledger_lines.purchase_posts`` -- and, for a transfer leg, iff it
    is the leg's RECORD, which the join this reads returns alone.

    **The parent's live and contributing gate is not restated, because no
    dated record can fail it** (finding **BAL-534**): a record cannot arrive
    under a deleted transfer and a transfer holding one cannot be hidden
    (``app.deleted_row_infrastructure``'s transfer arm), and a side's record
    is dated exactly while its transfer is Paid or Received
    (``app.side_band_infrastructure``), neither of which is Credit or
    Cancelled.

    Args:
        account_id: The real account whose settled transfer legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's settled transfer legs.

    Raises:
        ValueError: If *scenario_id* is ``None``: the filter would then match
            no transfer and the oracle would answer a vacuous ``$0.00``.
    """
    if scenario_id is None:
        raise ValueError(
            "settled_transfer_effect requires a scenario_id (transfers are "
            "scenario-scoped); got None."
        )
    rows = transfer_movement_rows(
        TransactionEntry.account_id == account_id,
        Transfer.scenario_id == scenario_id,
        TransactionEntry.settled_on.isnot(None),
        TransactionEntry.is_credit.is_(False),
    ).all()
    return sum(
        (
            transfer.settled_figure if is_income else -transfer.settled_figure
            for _movement, transfer, is_income in rows
        ),
        Decimal("0"),
    )


def posted_purchase_effect(account_id: int, scenario_id: int) -> Decimal:
    """Return an account's net effect from its DATED non-transfer movements.

    Over every non-card entry carrying a ``settled_on`` ON THIS ACCOUNT
    (``TransactionEntry.account_id``, the movement's own -- ruling
    **R-BAL75**) whose parent is a non-deleted, balance-contributing
    transaction in *scenario_id* -- never a transfer's, by structure: a
    transfer side's record names its transfer and no transaction
    (``ck_transaction_entries_one_parent``) -- sum each amount signed by the
    PARENT's type -- ``+amount`` under an income row, ``-amount`` under an
    expense -- whatever the parent's status.  **A movement's direction is its
    parent's** (plan step ``balance:X-bi-3b``, ruling **R-BAL35**), and a plan
    row books nothing of its own (ruling **R-BAL80**), so this is the whole of
    an ordinary transaction family's cash.

    **SIGNED**: ruling **bank_import:R-II** made a merchant credit a NEGATIVE
    purchase, whose cash leg is POSITIVE because the money arrived.  The sign
    rule is arithmetic over the figure, total over both directions.

    Args:
        account_id: The real account whose dated movements to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net effect of the account's dated movements.

    Raises:
        ValueError: If *scenario_id* is ``None``, as
            :func:`settled_transfer_effect` says.
    """
    if scenario_id is None:
        raise ValueError(
            "posted_purchase_effect requires a scenario_id (transactions are "
            "scenario-scoped); got None."
        )
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    signed_effect = case(
        (
            Transaction.transaction_type_id == income_type_id,
            TransactionEntry.amount,
        ),
        else_=-TransactionEntry.amount,
    )
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(signed_effect), Decimal("0"))
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
