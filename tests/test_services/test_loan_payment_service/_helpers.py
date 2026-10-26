"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_loan_payment_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from tests._test_helpers import (
    an_entered_day,
    on_both_sides,
    open_books_before_the_first_assertion,
)
from app.services.cash_ledger import derived_amount_basis
from app.utils.balance_predicates import settled_status_ids
from app.services.transfer_service import (
    TransferSpec,
    create_transfer,
    update_transfer,
)
from app.services import account_service, status_seam
from app.models.amount_ownership import AmountOwnership


# ── Helpers ──────────────────────────────────────────────────────────


def _basis(seed_user):
    """Return the seeded owner's amount basis -- what ``get_payment_history`` prices with.

    Spelled once because every case in this file passes the same one, and
    because an :class:`~app.services.cash_ledger.AmountBasis` is pinned to an
    OWNER and a SCENARIO: writing it per case invites a pair that names one
    owner's rows and another's scenario, which is the mismatch
    ``resolve_transaction_amount`` refuses a row for.  Not a fixture, because
    the basis must be built INSIDE each case's ``app.app_context()`` -- both of
    its derivations are lazy and neither may outlive the session that will
    resolve them.
    """
    return derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)


#: The mortgage this file builds closes here.  Named because the books-boundary
#: bound below reads it as well as ``LoanParams``, and two literals that must
#: agree are one a caller can split.
_ORIGINATION = date(2024, 1, 1)


def _create_loan_account(seed_user):
    """Create a mortgage account with LoanParams for the test user.

    A LOCAL factory rather than ``tests._test_helpers.create_loan_account``.
    It existed so that ``original_principal`` and ``current_principal`` would
    DIFFER (250,000 against 200,000) and a producer reading the wrong one be
    visible; plan step R20 dropped that column, so no producer can read it and
    the account's cash anchor (200,000) is the one remaining decoy.  What still
    tells the two factories apart is that this one does NOT open the loan's
    genesis posting ledger -- the shared one always does -- and moving this
    suite onto the shared factory is a change to what its producers read, not
    a tidy-up, so it is left as it was.

    Returns:
        Account: the mortgage account.
    """
    loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Test Mortgage",
            anchor_balance=Decimal("200000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()

    # **Its books open before the loan does** (plan step X-f3c-2b, ruling
    # **R-HG**): ``create_account`` puts them on the assertion's own day, which
    # is the frozen today, and every payment this suite records is dated before
    # that.  The shared ``create_loan_account`` does the same thing for the same
    # reason; this factory exists only for the differing principals below.
    open_books_before_the_first_assertion(
        db.session, account, also_before=_ORIGINATION,
    )

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("250000.00"),
        term_months=360,
        origination_date=_ORIGINATION,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    # DH-#56: the loan's base rate lives in the origination RateHistory row
    # (the retired LoanParams.interest_rate column), and the resolver raises on
    # an empty rate-change feed, so seed it.  The origination balance anchor is
    # synthesized from LoanParams (step C1 / the read switch) rather than stored
    # as a LoanAnchorEvent, so no anchor-event insert is needed.
    from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
        insert_origination_rate,
    )
    insert_origination_rate(params, Decimal("0.06500"))
    return account


def _create_transfer_to_loan(seed_user, loan_account, period, amount,
                              status_enum=StatusEnum.PROJECTED,
                              settled_on=None):
    """Create a transfer from checking to loan account.

    Uses the transfer service, the one door that writes a transfer, so the
    transfer is the one production writes: one row since plan step
    balance:X-bi-6-4d-2 (ruling **R-BAL258**), where the door also wrote the
    two shadows whose amounts and statuses it kept equal.  **A SETTLED one is
    created Projected, then settled** through the settle verb
    (``update_transfer`` with *status_enum*), because the create makes no
    settled transfer (ruling **R-BAL379**); it was created in its settled
    status until then, and the settle stores what that create stored.  A day
    stated beside an unsettled status is refused before any row exists, by
    the seam's own refusal, as that create refused it.

    Args:
        seed_user: The seed_user fixture dict.
        loan_account: The destination loan account.
        period: The PayPeriod for the transfer.
        amount: Transfer amount as Decimal.
        status_enum: StatusEnum member the transfer ends in.  The settled one
            is Paid (``DONE``): the transfer workflow settles to Done alone,
            so the settle refuses ``RECEIVED``, which no transfer door writes.
        settled_on: The civil day the cash moved, stated ``entered`` on both
            sides by the settle of a transfer whose *status_enum* is settled.
            ``None`` leaves the settle to stamp the user's today (a Paid
            press); a day beside an unsettled status is refused.

    Returns:
        Transfer: the created transfer.
    """
    status_id = ref_cache.status_id(status_enum)
    settled = status_id in settled_status_ids()
    stated = None if settled_on is None else an_entered_day(settled_on)
    status_seam.reject_settle_day_without_settled_status(status_id, stated)
    transfer = create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=(
                ref_cache.status_id(StatusEnum.PROJECTED) if settled
                else status_id
            ),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )
    if settled:
        update_transfer(
            transfer.id, seed_user["user"].id, status_id=status_id,
            side_days=(
                () if stated is None
                else on_both_sides(
                    seed_user["account"].id, loan_account.id, stated,
                )
            ),
        )
    return transfer
