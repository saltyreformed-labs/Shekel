"""
Shekel Budget App -- C-29 Cross-User FK Re-Parenting Tests

Route-level coverage for commit C-29 of the 2026-04-15 security
remediation plan: ``transactions.update_transaction`` rejects a
PATCH that submits another user's ``pay_period_id`` or
``category_id``.  Without this probe an authenticated owner could
silently re-parent their own transaction into the victim's
namespace because the FK constraint passes (the row exists -- just
under another user) and the unfiltered ``setattr`` loop would
complete the IDOR write.

The plan-cited tests verify:

  * Owner submits their own ``pay_period_id`` -- 200.
  * Owner submits Owner-B's ``pay_period_id`` -- 404, no mutation.
  * Owner submits their own ``category_id`` -- 200.
  * Owner submits Owner-B's ``category_id`` -- 404, no mutation.
  * Owner submits a non-existent ``pay_period_id`` -- 404,
    no mutation.
  * Owner submits a non-existent ``category_id`` -- 404, no mutation.
  * Cross-user FK rejection is atomic with the rest of the patch
    payload: an ``estimated_amount`` bundled with a cross-user
    ``pay_period_id`` is NOT applied (proves the route short-
    circuits before the ``setattr`` loop, not after).
  * Cross-user FK is rejected even on a transfer-shadow request
    (the transfer-shadow branch silently drops ``pay_period_id``
    in normal flow, but the route-boundary probe must still 404
    so the security boundary is enforced regardless of whether
    the value would be applied).

The IDOR rejections all return 404 (security response rule:
"404 for both not found and not yours").
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.models.ref import AccountType
from app.services import transfer_service
from app.services import account_service
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import one_off_row_of


# ── Helpers ──────────────────────────────────────────────────────────


def _create_projected_expense(seed_user, period):
    """Create a baseline projected expense for the seeded user.

    Mirrors the inline helper ``_TestTransactionCRUDMembers._create_test_txn``
    in ``tests/test_routes/test_grid/_helpers.py`` but is reproduced locally so
    this module stays self-contained.

    Returns:
        The created Transaction (committed, refreshed).
    """
    txn = one_off_row_of(
        period,
        name="C-29 Bill",
        amount=Decimal("123.45"),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Rent"].id,
    )
    db.session.commit()
    return txn


def _create_savings_account(seed_user):
    """Add a Savings account on the seeded user for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="C-29 Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_transfer(seed_user, period, savings):
    """Create a Checking -> Savings transfer via the service.

    Named ``_create_transfer_with_shadows`` until plan step
    ``balance:X-bi-6-4d-2``, which births no shadow (ruling R-BAL167 class 4,
    plan step balance:X-bi-6-4d-2).

    Returns:
        The Transfer (committed).
    """
    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(Decimal("100.00")),
            status_id=projected_id,
            category_id=seed_user["categories"]["Rent"].id,
            name="C-29 Transfer",
        ),
    )
    db.session.commit()
    return xfer


# ── transactions.update_transaction -- F-029 ────────────────────────


class TestUpdateTransactionPayPeriodOwnership:
    """``transactions.update_transaction`` rejects a cross-user pay_period_id.

    The schema's ``pay_period_id`` field accepts any int; the
    route-boundary check must verify ownership before the
    ``setattr`` loop writes the cross-user FK.  All rejections
    return 404 per the project security response rule.
    """

    def test_own_pay_period_succeeds(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A PATCH that moves the transaction to the OWNER's other period succeeds.

        Establishes the success baseline: the route-boundary probe
        must NOT reject a legitimate same-user period switch.  The
        transaction is moved from period[0] to period[1] (both
        owned by the seeded user) and the persisted
        ``pay_period_id`` is the new value.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            target_period_id = seed_periods_today[1].id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"pay_period_id": str(target_period_id)},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.pay_period_id == target_period_id, (
                "owner PATCH with own pay_period_id must persist the move"
            )

    def test_cross_user_pay_period_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_periods,
    ):
        """A cross-user pay_period_id is rejected with 404 and no mutation.

        Owner A PATCHes with Owner B's ``pay_period_id``.  The
        response is 404 ("Pay period not found") and the
        transaction's ``pay_period_id`` is unchanged after a
        fresh DB read.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_period_id = txn.pay_period_id
            attacker_target = seed_second_periods[0].id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"pay_period_id": str(attacker_target)},
            )
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.pay_period_id == original_period_id, (
                "cross-user pay_period_id must not re-parent the transaction"
            )

    def test_nonexistent_pay_period_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A pay_period_id that points to no row is rejected with 404.

        The 404 path treats "not found" and "not yours" identically.
        ``9_999_999`` is a deliberately out-of-range integer that
        neither user has ever owned, exercising the ``period is None``
        branch of the probe.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_period_id = txn.pay_period_id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"pay_period_id": "9999999"},
            )
            assert resp.status_code == 404
            assert b"Pay period not found" in resp.data

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.pay_period_id == original_period_id


class TestUpdateTransactionCategoryOwnership:
    """``transactions.update_transaction`` rejects a cross-user category_id.

    Same threat model as ``pay_period_id``: the schema accepts any
    int; the route-boundary check verifies ownership before the
    ``setattr`` loop.
    """

    def test_own_category_succeeds(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A PATCH that re-categorizes to the OWNER's other category succeeds.

        Establishes the success baseline: a legitimate same-user
        re-categorization (Rent -> Groceries) must persist.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            target_category_id = seed_user["categories"]["Groceries"].id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"category_id": str(target_category_id)},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert txn.category_id == target_category_id, (
                "owner PATCH with own category_id must persist the re-categorization"
            )

    def test_cross_user_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """A cross-user category_id is rejected with 404 and no mutation.

        Owner A PATCHes with Owner B's ``category_id``.  The
        response is 404 ("Category not found") and the
        transaction's ``category_id`` is unchanged after a fresh
        DB read.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_category_id = txn.category_id
            attacker_target = seed_second_user["categories"]["Rent"].id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"category_id": str(attacker_target)},
            )
            assert resp.status_code == 404
            assert b"Category not found" in resp.data

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.category_id == original_category_id, (
                "cross-user category_id must not re-categorize the transaction"
            )

    def test_nonexistent_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A category_id that points to no row is rejected with 404."""
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_category_id = txn.category_id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={"category_id": "9999999"},
            )
            assert resp.status_code == 404
            assert b"Category not found" in resp.data

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.category_id == original_category_id


class TestUpdateTransactionAtomicityWithCrossUserFk:
    """A PATCH that bundles a cross-user FK with valid same-user fields
    is rejected as a whole.  The route must short-circuit BEFORE the
    ``setattr`` loop or any commit, leaving every targeted column at
    its pre-request value -- not just the FK.  This proves the probe
    runs early enough that no partial mutation leaks through.
    """

    def test_cross_user_pay_period_blocks_estimated_amount_update(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_periods,
    ):
        """estimated_amount in the same payload as a cross-user pay_period_id is NOT applied.

        Pre-C-29, the unfiltered ``setattr`` loop would happily
        write ``estimated_amount`` to its new value while also
        rewriting ``pay_period_id`` to the victim's period and
        committing both.  After C-29 the cross-user FK probe
        rejects the request before any setattr runs, so
        ``estimated_amount`` must also be unchanged on a fresh DB
        read.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_amount = txn.estimated_amount
            original_period_id = txn.pay_period_id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "pay_period_id": str(seed_second_periods[0].id),
                    "estimated_amount": "999.99",
                    "estimated_amount_as_rendered": "123.45",
                },
            )
            assert resp.status_code == 404

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.pay_period_id == original_period_id, (
                "cross-user pay_period_id must not re-parent the transaction"
            )
            assert txn.estimated_amount == original_amount, (
                "estimated_amount must not be applied when the same payload "
                "carries a cross-user pay_period_id -- the route must short-"
                "circuit before the setattr loop"
            )

    def test_cross_user_category_blocks_estimated_amount_update(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """estimated_amount in the same payload as a cross-user category_id is NOT applied.

        Companion test to ``test_cross_user_pay_period_blocks_...``;
        proves the same atomicity guarantee for the category_id
        probe.
        """
        with app.app_context():
            txn = _create_projected_expense(seed_user, seed_periods_today[0])
            original_amount = txn.estimated_amount
            original_category_id = txn.category_id

            resp = auth_client.patch(
                f"/transactions/{txn.id}",
                data={
                    "category_id": str(seed_second_user["categories"]["Rent"].id),
                    "estimated_amount": "888.88",
                    "estimated_amount_as_rendered": "123.45",
                },
            )
            assert resp.status_code == 404

            db.session.expire_all()
            txn = db.session.get(Transaction, txn.id)
            assert txn.category_id == original_category_id, (
                "cross-user category_id must not re-categorize the transaction"
            )
            assert txn.estimated_amount == original_amount, (
                "estimated_amount must not be applied when the same payload "
                "carries a cross-user category_id"
            )


class TestUpdateTransferFromALegFkOwnership:
    """The cross-user FK probe on the transfer PATCH a grid LEG uses.

    **Was ``TestUpdateTransactionTransferShadowFkOwnership`` until plan step
    balance:X-bi-6-1 (ruling R-BAL87)**: it probed ``PATCH /transactions/
    <shadow>``, whose transfer-shadow branch silently dropped
    ``pay_period_id`` and whose route-boundary probe (C-29) still had to
    404.  That branch is deleted -- a shadow row is "not found" at the
    transaction door outright -- and the grid's door for a transfer is the
    transfer PATCH carrying ``leg_account_id``, which owns its own probe of
    the same two references.  The boundary is graded THERE now: consistency
    matters more than micro-optimisation when the difference is "did the
    attacker's probe succeed."

    Each case's leg is the transfer's from-side, read off the transfer.  It
    was the account of the first live twin the case found until plan step
    ``balance:X-bi-6-4d-2``, which births none; that twin sat on the
    from-side (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
    """

    def test_cross_user_pay_period_from_a_leg_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_periods,
    ):
        """A cross-user pay_period_id on the transfer PATCH from a leg is rejected with 404.

        The transfer's status, period, and amount must all be unchanged
        after a fresh DB read -- nothing in the request was applied.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(
                seed_user, seed_periods_today[0], savings,
            )
            leg_account_id = xfer.from_account_id

            original_period_id = xfer.pay_period_id
            original_status_id = xfer.status_id
            original_amount = xfer.amount

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "pay_period_id": str(seed_second_periods[0].id),
                    "amount": "200.00",
                    "amount_as_rendered": "123.45",
                    "leg_account_id": str(leg_account_id),
                },
            )
            assert resp.status_code == 404
            assert b"Not found" in resp.data

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.pay_period_id == original_period_id
            assert xfer.status_id == original_status_id
            assert xfer.amount == original_amount, (
                "transfer amount must be unchanged when the cross-user FK "
                "probe rejected the request"
            )

    def test_cross_user_category_from_a_leg_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
        seed_second_user,
    ):
        """A cross-user category_id on the transfer PATCH from a leg is rejected with 404.

        Pre-C-29 the route forwarded ``category_id`` to
        ``transfer_service.update_transfer`` which raised
        ``NotFoundError`` -> 400.  After C-29 the route-boundary
        probe runs first and the rejection is consistently 404,
        matching the regular-path response and the security
        response rule.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(
                seed_user, seed_periods_today[0], savings,
            )
            leg_account_id = xfer.from_account_id

            original_category_id = xfer.category_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "category_id": str(
                        seed_second_user["categories"]["Rent"].id
                    ),
                    "leg_account_id": str(leg_account_id),
                },
            )
            assert resp.status_code == 404
            assert b"Not found" in resp.data

            db.session.expire_all()
            xfer = db.session.get(Transfer, xfer.id)
            assert xfer.category_id == original_category_id, (
                "cross-user category_id must not mutate the transfer"
            )
