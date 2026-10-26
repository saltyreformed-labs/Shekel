"""
Shekel Budget App -- C-27 Route Input Validation Tests

Route-level coverage for commit C-27 of the 2026-04-15 security
remediation plan: every endpoint that previously hand-parsed form
input or trusted FK IDs without route-boundary ownership checks
now goes through a Marshmallow schema and/or a per-FK
``current_user.id`` probe.  The plan-cited tests live here:

  * ``transactions.mark_done`` -- parses the figure via
    :class:`MarkDoneSchema`; a malformed or negative value is
    rejected at the schema tier instead of the route's old
    ``InvalidOperation`` catch.  Its transfer-shadow branch is gone
    (plan step ``balance:X-bi-6-1``), and the transfer PATCH a grid
    leg uses refuses the same figure.

  * ``transfers.create_ad_hoc`` -- route-boundary FK ownership for
    ``from_account_id``, ``to_account_id``, ``scenario_id``,
    ``category_id`` collapsed into a single loop that returns 404 on
    the first failure (``pay_period_id``'s is the service's since plan
    step ``pay_calendar:C13-b``).

  * ``transfers.update_transfer`` -- route-boundary ownership probe
    on ``category_id``, the one user-scoped FK of the two the schema
    accepts whose ownership the route checks (``pay_period_id``'s is
    the service's since plan step ``pay_calendar:C13-b``).

  * ``transfers.create_transfer_template`` /
    ``transfers.update_transfer_template`` -- ownership probes on
    every user-scoped FK accepted by the form.

The ad-hoc and update IDOR rejections return 404 (security response
rule: "404 for both not found and not yours"); the two template
routes are form posts and redirect (302) instead.  Each proves the
request wrote nothing: a create made no row, and the owner's row was not
re-pointed at the other user's.  On the
ad-hoc and update doors the service refuses the same references with
the same 404, so those cases grade the refusal whichever tier gives
it.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.models.ref import AccountType
from app.services import pay_period_write, transfer_service
from app.services import account_service
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER
from app.models.amount_ownership import AmountOwnership
from app.services.grid_view_service import leg_dom_id
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    one_off_row_of, open_books_before_the_first_assertion, rhythm_of,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _add_txn(db_session, seed_user, period, name, amount):
    """Place a Projected one-off expense for the seeded user, flushed.

    Through the producer (:func:`~tests._test_helpers.one_off_row_of`): a
    rule-less definition plus its placed row, the shape every one-off holds
    since the one-definition cutover (plan step ``balance:X-bi-7d-2``; a
    bare ``Transaction(...)`` is unstorable under
    ``ck_transactions_one_pricing_link`` at ``= 1``).  It hand-built a
    link-less row until then, carrying a ``transfer_id`` parameter no caller
    passed, which is why 7c's constructor census read it as a LINKED site.
    """
    txn = one_off_row_of(
        period, name=name, amount=Decimal(str(amount)),
        user_id=period.user_id, account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        category_id=seed_user["categories"]["Rent"].id,
    )
    db_session.flush()
    return txn


def _create_savings_account(seed_user):
    """Add a Savings account on the seeded user for transfer tests."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="C-27 Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _create_transfer(seed_user, period, savings):
    """Create a Projected $100.00 transfer, checking to *savings*, via the service."""
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
            name="C-27 Transfer",
        ),
    )
    db.session.commit()
    return xfer


def _seed_second_user_transfer_assets():
    """Seed a second user, their period, scenario, and a category.

    Returns a dict with keys: user, account, savings, period,
    scenario, category.  Used to source cross-user FKs the
    authenticated user must NOT be allowed to reference.
    """
    from app.models.user import User, UserSettings  # pylint: disable=import-outside-toplevel
    from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
    from app.services.auth_service import hash_password  # pylint: disable=import-outside-toplevel

    other = User(
        email="cross-c27@shekel.local",
        password_hash=hash_password("crossc27pass"),
        display_name="Cross C27",
    )
    db.session.add(other)
    db.session.flush()
    db.session.add(UserSettings(user_id=other.id))

    # Pay periods first (E-19, Commit 3): the account_service factory
    # requires the user to have at least one pay period to anchor
    # against before any account row can be created.
    other_periods = pay_period_write.record_paydays(
        user_id=other.id,
        first_payday=date(2026, 1, 2),
        num_periods=2,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    other_checking = account_service.create_account(
        account_service.AccountSpec(
            user_id=other.id,
            account_type_id=checking_type.id,
            name="Other Cross Checking",
            anchor_balance=Decimal("100.00"),
        ),
    )
    other_savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=other.id,
            account_type_id=savings_type.id,
            name="Other Cross Savings",
            anchor_balance=Decimal("0"),
        ),
    )

    other_scenario = Scenario(
        user_id=other.id, name="Baseline", is_baseline=True,
    )
    db.session.add(other_scenario)

    other_category = Category(
        user_id=other.id, group_name="Home", item_name="Rent",
    )
    db.session.add(other_category)
    db.session.commit()

    return {
        "user": other,
        "account": other_checking,
        "savings": other_savings,
        "period": other_periods[0],
        "scenario": other_scenario,
        "category": other_category,
    }


# ── transactions.mark_done -- F-042 / F-162 ──────────────────────────


class TestTransactionsMarkDoneActualAmount:
    """``transactions.mark_done`` parses ``settled_amount`` via MarkDoneSchema.

    F-042 / F-162 -- C-27 replaced two raw ``Decimal(...)`` calls (one in
    the regular branch, one in the transfer-shadow branch) with a
    single schema parse run before the branches diverged, and changed
    nothing else.  The transfer-shadow branch is gone (plan step
    ``balance:X-bi-6-1``); the case that graded it grades the transfer
    PATCH a grid leg uses.
    """

    def test_negative_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative ``settled_amount`` fails at the schema tier."""
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "-50.00"},
            )
            assert resp.status_code == 422

            db.session.refresh(txn)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED), (
                "a negative settled_amount must not transition the row"
            )

    def test_non_numeric_actual_amount_rejected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Non-numeric ``settled_amount`` fails Marshmallow's Decimal coercion.

        Refit 2026-07-11 for the marker-header convention (closeout plan
        session 4, developer-ruled): the 422 body changed from a JSON
        errors dict to a DESIGNED fragment -- the requesting surface
        re-rendered with the flattened field message -- carrying the
        marker header so htmx swaps it instead of dropping it.
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "abc"},
            )
            assert resp.status_code == 422
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
            body = resp.data.decode()
            assert "settled_amount" in body
            assert "txn-chip" in body, (
                "the 422 body must be the desktop cell fragment"
            )

    def test_negative_actual_amount_rejected_on_a_transfer_from_its_leg(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative settled_amount is rejected on the transfer door a grid leg uses.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87)**: it
        graded the transaction mark-done's transfer-shadow branch, whose own
        raw ``Decimal(...)`` parse C-27 replaced with the schema; that branch
        is deleted, and the grid's figure door for a transfer is the transfer
        PATCH carrying ``leg_account_id``, whose schema refuses the same
        figure with the same 422 and no transition.  The leg is the
        checking side's, the transfer's own ``from_account_id``: it was
        read off whichever of the transfer's two shadow rows came first
        until plan step balance:X-bi-6-4d-2 made a transfer one row.  The
        422 re-renders that leg's own cell, which is what grades the
        ``leg_account_id`` the request carries.  The savings books open
        before the period, so a valid figure WOULD settle here and the
        refusal is the figure's alone: on books opened today, every settle
        was refused by the books first and the no-transition read could
        not fail (found by an adversarial review of 4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            open_books_before_the_first_assertion(db.session, savings)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_amount": "-25.00",
                    "status_id": str(ref_cache.status_id(StatusEnum.DONE)),
                    "leg_account_id": str(xfer.from_account_id),
                },
            )
            assert resp.status_code == 422, resp.get_data(as_text=True)[:200]
            assert leg_dom_id(xfer.id, xfer.from_account_id) in (
                resp.get_data(as_text=True)
            ), "the 422 must re-render the leg the request came from"

            db.session.expire_all()
            db.session.refresh(xfer)
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED), (
                "a negative settled_amount must not transition the transfer"
            )

    def test_valid_actual_amount_persists(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Happy-path: a positive settled_amount transitions the row.

        Anchors the schema-based parse to the existing behaviour --
        the regression we are guarding against is "the schema
        rejected a value the route used to accept."
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "85.50"},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert settled_figure(txn) == Decimal("85.50")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_missing_actual_amount_leaves_the_record_untouched(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark-done without ``settled_amount`` does not nullify what was recorded.

        Pre-C-27 the route's ``if actual:`` truthy check left the
        column untouched on missing input; the schema's
        ``strip_empty_strings`` plus ``data.get(...)`` preserves
        that contract -- a button-click with no body must not
        clear the figure the row already recorded.

        **The prior value now has to BE a record** (plan step X-au-c3): a figure
        records a settle, so the row is settled with a human's ``$90.00``
        correction first and the empty re-post is an idempotent re-submit, which
        the seam reads as "nothing new about what moved" and leaves alone.
        """
        with app.app_context():
            txn = _add_txn(
                db.session, seed_user, seed_periods_today[0],
                "Bill", "100.00",
            )
            db.session.commit()

            assert auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={"settled_amount": "90.00"},
            ).status_code == 200
            db.session.refresh(txn)
            assert settled_figure(txn) == Decimal("90.00")

            resp = auth_client.post(
                f"/transactions/{txn.id}/mark-done",
                data={},
            )
            assert resp.status_code == 200

            db.session.refresh(txn)
            assert settled_figure(txn) == Decimal("90.00"), (
                "mark-done with no body must not clear what the row recorded"
            )


# ── transfers.create_ad_hoc -- F-043 ──────────────────────────────────


class TestTransfersAdHocFkOwnership:
    """``transfers.create_ad_hoc`` rejects cross-user FKs with a 404.

    Each test asserts (1) the response is 404 (security response
    rule), and (2) no transfer row was created in either user's
    namespace.  The route probes the two accounts, the scenario and
    the category before it calls ``transfer_service.create_transfer``;
    the period's 404 is the service's (``_get_owned_period``, plan
    step ``pay_calendar:C13-b``), and the service refuses the other
    four with the same 404 too, so a case cannot tell which tier
    answered.
    """

    def _baseline_payload(self, seed_user, period, savings):
        """Construct a valid ad-hoc payload for the seed user."""
        return {
            "pay_period_id": period.id,
            "from_account_id": seed_user["account"].id,
            "to_account_id": savings.id,
            "amount": "75.00",
            "scenario_id": seed_user["scenario"].id,
            "name": "C-27 cross-user attempt",
            "category_id": str(seed_user["categories"]["Rent"].id),
        }

    def test_cross_user_from_account_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``from_account_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["from_account_id"] = other["account"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before, (
                "cross-user from_account_id must not create a transfer"
            )

    def test_cross_user_to_account_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``to_account_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["to_account_id"] = other["savings"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_pay_period_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``pay_period_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["pay_period_id"] = other["period"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_scenario_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``scenario_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["scenario_id"] = other["scenario"].id

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before

    def test_cross_user_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """``category_id`` belonging to another user is rejected with 404."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(Transfer).count()
            payload = self._baseline_payload(
                seed_user, seed_periods_today[0], savings,
            )
            payload["category_id"] = str(other["category"].id)

            resp = auth_client.post("/transfers/ad-hoc", data=payload)
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.query(Transfer).count() == count_before


# ── transfers.update_transfer -- F-043 ──────────────────────────────


class TestUpdateTransferCategoryOwnership:
    """``transfers.update_transfer`` rejects a cross-user category_id.

    The schema accepts two user-scoped FKs on update, ``category_id`` and
    ``pay_period_id``.  The route verifies the category's ownership before
    delegating to ``transfer_service.update_transfer``, which checks the
    period's itself (plan step ``pay_calendar:C13-b``).
    """

    def test_cross_user_category_returns_404(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A cross-user category_id is rejected with 404 and the transfer keeps its category.

        The route probes the category before it delegates, and the
        service refuses it too (``_reject_unowned_references``), so the
        case grades the 404 and the unchanged row whichever tier
        answers.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)
            other = _seed_second_user_transfer_assets()
            original_category_id = xfer.category_id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "category_id": str(other["category"].id),
                },
            )
            assert resp.status_code == 404

            db.session.expire_all()
            db.session.refresh(xfer)
            assert xfer.category_id == original_category_id, (
                "cross-user category_id must not mutate the transfer"
            )

    def test_clearing_category_is_permitted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Setting category_id to empty (None) bypasses the ownership check.

        ``TransferUpdateSchema.category_id`` is ``allow_none=True``;
        sending the field empty must succeed because clearing the
        category is legitimate.  The new ownership probe correctly
        skips ``None`` values.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today[0], savings)
            xfer_id = xfer.id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "category_id": "",
                },
            )
            assert resp.status_code in (200, 201)

            # The schema's pre_load maps the empty input of a nullable
            # field to None (``_normalize_empty_inputs``), so the
            # ownership probe skips it and the service clears the
            # category.
            db.session.expire_all()
            db.session.refresh(xfer)
            assert xfer.category_id is None


# ── transfers.create_transfer_template -- F-043 ──────────────────────


class TestCreateTransferTemplateFkOwnership:
    """``transfers.create_transfer_template`` rejects cross-user FKs.

    The form-redirect UX makes the test pattern slightly different
    from the JSON-returning ad-hoc endpoint: a 302 redirect with
    no template row created counts as the rejection.  Each test
    counts the rows before and after to prove no side effects
    leaked.
    """

    def _baseline_form(self, seed_user, savings):
        """Valid template-create payload for the seeded user."""
        return {
            "name": "C-27 Template",
            "default_amount": "100.00",
            "from_account_id": seed_user["account"].id,
            "to_account_id": savings.id,
            "category_id": str(seed_user["categories"]["Rent"].id),
        }

    def test_cross_user_from_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """``from_account_id`` from another user blocks template creation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["from_account_id"] = other["account"].id

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before

    def test_cross_user_to_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """``to_account_id`` from another user blocks template creation."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["to_account_id"] = other["savings"].id

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before

    def test_cross_user_category_blocked(
        self, app, auth_client, seed_user,
    ):
        """``category_id`` from another user blocks template creation.

        Pre-C-27 this gap existed: the template-create route checked
        account ownership but not category ownership, so a
        cross-user ``category_id`` would persist on the template
        and only fail later when the service tried to use it.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            other = _seed_second_user_transfer_assets()

            count_before = db.session.query(TransferTemplate).count()
            form = self._baseline_form(seed_user, savings)
            form["category_id"] = str(other["category"].id)

            resp = auth_client.post(
                "/transfers", data=form, follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            assert db.session.query(TransferTemplate).count() == count_before


# ── transfers.update_transfer_template -- F-043 ──────────────────────


class TestUpdateTransferTemplateFkOwnership:
    """``transfers.update_transfer_template`` rejects cross-user FKs.

    The update path is partial: only fields actually submitted run
    through the ownership probe.  Each test verifies the per-FK
    rejection and asserts the template's row was not mutated.
    """

    def _create_template(self, seed_user, savings):
        """Make a saved template owned by the seeded user."""
        from app.models.transfer_template import TransferTemplate as TplModel  # pylint: disable=import-outside-toplevel
        tpl = TplModel(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            category_id=seed_user["categories"]["Rent"].id,
            name="C-27 Template To Update",
            default_amount=Decimal("80.00"),
        )
        db.session.add(tpl)
        db.session.commit()
        return tpl

    def test_cross_user_from_account_blocked(
        self, app, auth_client, seed_user,
    ):
        """Submitting a cross-user from_account_id leaves the template unchanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            tpl = self._create_template(seed_user, savings)
            other = _seed_second_user_transfer_assets()
            original_from = tpl.from_account_id

            resp = auth_client.post(
                f"/transfers/{tpl.id}",
                data={
                    "name": tpl.name,
                    "default_amount": str(tpl.default_amount),
                    "from_account_id": other["account"].id,
                    "to_account_id": tpl.to_account_id,
                    "category_id": str(tpl.category_id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            db.session.refresh(tpl)
            assert tpl.from_account_id == original_from

    def test_cross_user_category_blocked(
        self, app, auth_client, seed_user,
    ):
        """Submitting a cross-user category_id leaves the template unchanged."""
        with app.app_context():
            savings = _create_savings_account(seed_user)
            tpl = self._create_template(seed_user, savings)
            other = _seed_second_user_transfer_assets()
            original_category = tpl.category_id

            resp = auth_client.post(
                f"/transfers/{tpl.id}",
                data={
                    "name": tpl.name,
                    "default_amount": str(tpl.default_amount),
                    "from_account_id": tpl.from_account_id,
                    "to_account_id": tpl.to_account_id,
                    "category_id": str(other["category"].id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302

            db.session.expire_all()
            db.session.refresh(tpl)
            assert tpl.category_id == original_category
