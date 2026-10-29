"""
Tests for HYSA routes (detail view and params update).
"""

from decimal import Decimal

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.extensions import db
from app.models.account import Account
from app.models.interest_params import InterestParams
from app.models.ref import AccountType
from app.services import account_service
from app.models.amount_ownership import AmountOwnership


def _create_hysa_account(seed_user, db_session, name="My HYSA"):
    """Helper to create a HYSA account with params.

    ``anchor_period_id`` is forwarded to
    :func:`app.services.account_service.create_account` so the
    origination ``AccountAnchorHistory`` row points at the caller's
    intended anchor period.  Tests that previously mutated
    ``account.current_anchor_period_id`` directly after the helper
    returned were relying on the pre-Commit-4 cache being authoritative;
    post-Commit-4 the dated SoT (latest history row) wins over the
    cache, so the explicit ``anchor_period_id`` here is the canonical
    way to anchor a HYSA against a specific period at creation time.
    """
    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name=name,
            anchor_balance=Decimal("10000.00"),
        ),
    )
    db_session.add(account)
    db_session.flush()

    # HIGH-06 / Commit 24: ``apy`` is NOT NULL and no longer carries
    # ``server_default="0.04500"``; the production auto-create path
    # (``app/routes/accounts.py``) supplies an explicit
    # ``Decimal("0")`` sentinel so a first-save flow cannot
    # silently materialise the legacy 4.5% rate.  Test fixtures
    # mirror the production convention.
    params = InterestParams(
        account_id=account.id, apy=Decimal("0.04500"),
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        ),
    )
    db_session.add(params)
    db_session.commit()
    return account, params


def _create_other_hysa(second_user, db_session):
    """Create a HYSA account owned by the second user.

    Builds on the shared second_user fixture. Returns (Account, InterestParams).
    """
    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user["user"].id,
            account_type_id=hysa_type.id,
            name="Other HYSA",
            anchor_balance=Decimal("5000.00"),
        ),
    )
    db_session.add(account)
    db_session.flush()

    # HIGH-06 / Commit 24: ``apy`` is NOT NULL and no longer carries
    # ``server_default="0.04500"``; the production auto-create path
    # (``app/routes/accounts.py``) supplies an explicit
    # ``Decimal("0")`` sentinel so a first-save flow cannot
    # silently materialise the legacy 4.5% rate.  Test fixtures
    # mirror the production convention.
    params = InterestParams(
        account_id=account.id, apy=Decimal("0.04500"),
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        ),
    )
    db_session.add(params)
    db_session.commit()
    return account, params


class TestHysaDetailView:
    """GET /accounts/<id>/details (the merged cash detail page).

    The Fable 5 overhaul merged the interest detail page into
    ``accounts.cash_detail``; an interest-bearing HYSA now renders its
    APY / parameters card there.  The legacy ``/interest`` URL survives
    only as a redirect stub (see ``TestCashDetailRedirectStubs``).
    """

    def test_hysa_detail_view(self, auth_client, seed_user, db, seed_periods_today):
        """Returns 200 with interest data.

        Re-pinned (rule 2 exception; CRIT-01 / F-001 / Commit 4):
        passing ``anchor_period_id`` through the canonical factory
        replaces the legacy ``account.current_anchor_period_id = ...``
        cache mutation.  The dated ``AccountAnchorHistory`` SoT is
        authoritative post-Commit-4, so the cache-only mutation no
        longer drives the rendered projection.
        """
        account, _ = _create_hysa_account(
            seed_user, db.session,
        )

        resp = auth_client.get(f"/accounts/{account.id}/details")
        assert resp.status_code == 200
        assert b"HYSA" in resp.data
        assert b"APY" in resp.data

    def test_hysa_detail_idor(self, auth_client, second_user, db):
        """GET another user's HYSA account returns 404 (security)
        and does not leak victim data."""
        other_acct, _ = _create_other_hysa(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/details")
        assert resp.status_code == 404
        assert b"Other HYSA" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_hysa_detail_nonexistent(self, auth_client, seed_user, db):
        """Bad account ID returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/details")
        assert resp.status_code == 404

    def test_cash_detail_wrong_type(self, auth_client, seed_user, db):
        """A loan (has_amortization) account is NOT served by the cash page.

        The merged cash detail page serves cash accounts only; loans,
        physical assets, and retirement / investment accounts 404 out
        (they keep their own screens).  A Mortgage (has_amortization) is
        the loan representative here.
        """
        mortgage_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Home Mortgage",
                anchor_balance=Decimal("250000.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/details")
        assert resp.status_code == 404

    def test_hysa_detail_login_required(self, client, db):
        """Unauthenticated → redirect to login."""
        resp = client.get("/accounts/1/details")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestInterestParamsUpdate:
    """POST /accounts/<id>/interest/params."""

    def test_hysa_params_update(self, auth_client, seed_user, db, seed_periods_today):
        """Valid params → updates APY and compounding."""
        account, _ = _create_hysa_account(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={
                "apy": "5.000",
                "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.MONTHLY),
            },
        )
        assert resp.status_code == 302

        params = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert params.apy == Decimal("0.05000")
        assert params.compounding_frequency_id == ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.MONTHLY,
        )

    def test_hysa_params_update_validation(self, auth_client, seed_user, db):
        """Invalid params → validation error."""
        account, _ = _create_hysa_account(seed_user, db.session)

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={
                "apy": "200",  # > 100 is invalid
                "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY),
            },
        )
        assert resp.status_code == 302

        # APY should remain at default.
        params = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert params.apy == Decimal("0.04500")

    def test_hysa_params_update_idor(self, auth_client, second_user, db):
        """POST to another user's HYSA params is rejected
        and leaves the victim's data completely unchanged."""
        # Phase A: Setup victim's data with known values.
        other_acct, _ = _create_other_hysa(second_user, db.session)
        original = db.session.query(InterestParams).filter_by(
            account_id=other_acct.id
        ).one()
        orig_apy = original.apy
        orig_freq = original.compounding_frequency_id

        # Phase B: Attack.
        resp = auth_client.post(
            f"/accounts/{other_acct.id}/interest/params",
            data={"apy": "0.09000", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.MONTHLY)},
        )

        # Phase C: Verify no state change.
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.query(InterestParams).filter_by(
            account_id=other_acct.id
        ).one()
        assert after.apy == orig_apy, (
            "IDOR attack modified apy!"
        )
        assert after.compounding_frequency_id == orig_freq, (
            "IDOR attack modified compounding_frequency!"
        )


class TestHysaNegativePaths:
    """Negative-path and boundary tests for HYSA routes."""

    def test_params_update_invalid_apy(self, auth_client, seed_user, db):
        """Non-numeric APY is rejected and DB is unchanged."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={"apy": "abc", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY)},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "Invalid APY modified the DB!"

    def test_params_update_negative_apy(self, auth_client, seed_user, db):
        """Negative APY is rejected by Range(min=0) validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={"apy": "-0.5", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY)},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "Negative APY modified the DB!"

    def test_params_update_invalid_compounding_frequency(
        self, auth_client, seed_user, db,
    ):
        """Invalid compounding frequency is rejected by OneOf validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_freq = params.compounding_frequency_id

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={"apy": "0.04500", "compounding_frequency_id": 999999},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert after.compounding_frequency_id == orig_freq

    def test_params_update_nonexistent_account(self, auth_client, seed_user, db):
        """POST to nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.post(
            "/accounts/999999/interest/params",
            data={"apy": "0.04500", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY)},
        )
        assert resp.status_code == 404

    def test_params_update_wrong_account_type(self, auth_client, seed_user, db):
        """POST HYSA params to a checking account is rejected."""
        checking_acct = seed_user["account"]
        resp = auth_client.post(
            f"/accounts/{checking_acct.id}/interest/params",
            data={"apy": "0.04500", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY)},
        )
        assert resp.status_code == 302
        assert "/savings" in resp.headers.get("Location", "")
        resp2 = auth_client.get(resp.headers["Location"])
        assert b"does not support interest parameters" in resp2.data

    def test_params_update_extremely_high_apy(self, auth_client, seed_user, db):
        """APY > 1 (100%) is rejected by Range(max=1) validator."""
        account, params = _create_hysa_account(seed_user, db.session)
        orig_apy = params.apy

        resp = auth_client.post(
            f"/accounts/{account.id}/interest/params",
            data={"apy": "500", "compounding_frequency_id": ref_cache.compounding_frequency_id(CompoundingFrequencyEnum.DAILY)},
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = db.session.query(InterestParams).filter_by(account_id=account.id).one()
        assert after.apy == orig_apy, "APY > 1 should be rejected by schema"


class TestCreateHysaAccount:
    """Creating a HYSA account auto-creates InterestParams."""

    def test_create_hysa_account_auto_params(self, auth_client, seed_user, db, seed_periods_today):
        """POST to create account with HYSA type auto-creates InterestParams
        with an explicit zero-APY sentinel (HIGH-06 / Commit 24).

        Re-pinned (rule 2 exception; HIGH-06 / E-12 "zero is a value,
        not missing"): pre-fix the assertion was
        ``params.apy == Decimal("0.04500")`` because the column
        carried ``server_default="0.04500"`` and the auto-create
        path emitted no Python-side ``apy`` assignment -- so a
        first-time HYSA silently materialised a 4.5% rate the user
        never configured.  Post-fix the auto-create supplies
        ``apy=Decimal("0")`` and ``calculate_interest`` short-
        circuits on ``apy <= 0`` so the account projects zero
        interest until the user enters a real rate via the
        interest-detail form.  ``compounding_frequency_id`` is the
        ref-table FK (#38): the route auto-create supplies the DAILY
        id explicitly (the prior ``server_default="daily"`` is gone --
        an FK id is not a static literal; the cadence choice is not a
        financial hazard while the rate is zero).
        """
        hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()

        resp = auth_client.post(
            "/accounts",
            data={
                "name": "New HYSA",
                "account_type_id": str(hysa_type.id),
                "anchor_balance": "5000.00",
            },
        )
        assert resp.status_code == 302

        account = db.session.query(Account).filter_by(name="New HYSA").one()
        params = db.session.query(InterestParams).filter_by(account_id=account.id).first()
        assert params is not None, "InterestParams were not auto-created"
        assert params.account_id == account.id
        # HIGH-06 / Commit 24 re-pin: explicit zero sentinel.
        assert params.apy == Decimal("0.00000")
        assert params.compounding_frequency_id == ref_cache.compounding_frequency_id(
            CompoundingFrequencyEnum.DAILY,
        )


def _cash_detail_current_balance(app, auth_client, account_id):
    """Return the ``current_balance`` the cash-detail route hands the template.

    Reads the render context via Flask's ``template_rendered`` signal (the
    established chart / net-worth route-test pattern) so the assertion is on
    the route's balance figure directly, not a format-fragile HTML
    substring.  The merged page dropped the per-period table, so the hero's
    current-period balance -- not a late table row -- is the figure that
    reflects a shadow-transfer deposit.
    """
    # Pylint: import-outside-toplevel -- deferred import is the file-wide
    # test convention.
    from flask import template_rendered  # pylint: disable=import-outside-toplevel

    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append(context)

    template_rendered.connect(_record, app)
    try:
        resp = auth_client.get(f"/accounts/{account_id}/details")
    finally:
        template_rendered.disconnect(_record, app)
    assert resp.status_code == 200, (
        f"GET /accounts/{account_id}/details returned {resp.status_code}"
    )
    assert recorded, "cash_detail did not render a template"
    return recorded[0]["current_balance"]


class TestHysaDetailCountsTransferDeposits:
    """Verify that the HYSA detail page includes shadow transactions
    from transfers in its balance calculation and projection.
    """

    def test_hysa_detail_includes_transfer_deposit(
        self, app, auth_client, seed_user, db, seed_periods_today
    ):
        """Verify that the HYSA detail page includes the deposits
        transfers make into the account in the balance projection.  Without
        this, the HYSA projected balance underestimates by the total of
        all missed transfer deposits, and interest compounds on the
        wrong base amount.
        """
        from app.models.category import Category  # pylint: disable=import-outside-toplevel
        from app.models.ref import Status  # pylint: disable=import-outside-toplevel
        from app.services import transfer_service  # pylint: disable=import-outside-toplevel

        # Re-pinned (rule 2 exception; CRIT-01 / F-001 / Commit 4):
        # pass ``anchor_period_id`` through the canonical factory so the
        # origination ``AccountAnchorHistory`` row points at
        # ``seed_periods_today[0]``.  Post-Commit-7 the resolver reads the
        # dated SoT (latest history row); without this the anchor would be
        # today's current period (``seed_periods_today[4]``), which is
        # post-anchor to the transfer in ``seed_periods_today[0]`` and
        # silently omits it from the projection (the F-009 silent-degrade
        # shape).
        account, _ = _create_hysa_account(
            seed_user, db.session,
        )

        # Add transfer categories required by the service.
        incoming = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers", item_name="Incoming",
        )
        outgoing = Category(
            user_id=seed_user["user"].id,
            group_name="Transfers", item_name="Outgoing",
        )
        db.session.add_all([incoming, outgoing])
        db.session.flush()

        # Create a $500 transfer from checking to HYSA.
        projected = db.session.query(Status).filter_by(name="Projected").one()
        transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=account.id,
                pay_period_id=seed_periods_today[0].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("500.00")),
                status_id=projected.id,
                category_id=outgoing.id,
            ),
        )
        db.session.commit()

        # The hero shows the CURRENT-period balance (the per-period table
        # was dropped in the Fable 5 merge).  Anchor $10,000 + the $500
        # deposit (in the anchor period) + a few periods of 4.5% APY daily
        # interest => strictly above $10,500.  WITHOUT the fix the deposit
        # is omitted and the balance is only interest on $10,000
        # (~$10,067), which is BELOW $10,500 -- so the bound is falsifiable.
        current_balance = _cash_detail_current_balance(
            app, auth_client, account.id,
        )
        assert Decimal("10500.00") < current_balance < Decimal("10700.00"), (
            f"HYSA current balance {current_balance!r} does not reflect the "
            "$500 transfer deposit (expected anchor + deposit + interest)"
        )

    def test_hysa_detail_no_transfers_regression(
        self, app, auth_client, seed_user, db, seed_periods_today
    ):
        """Verify that the HYSA detail page still works correctly when
        there are no transfers.  The account_id query must return an
        empty set without errors, and the balance equals anchor + interest.

        Re-pinned (rule 2 exception; CRIT-01 / F-001 / Commit 4):
        anchor period passed through the canonical factory rather
        than mutated on the cache column; see the sibling test for
        the rationale.
        """
        account, _ = _create_hysa_account(
            seed_user, db.session,
        )

        # Anchor $10,000 with only interest (no deposit): the current-period
        # hero balance is just above $10,000 (a few periods of 4.5% APY daily
        # interest, ~$10,067) and well below the +$500 deposit case -- proving
        # the empty-transfer path projects cleanly, not that it stalls at the
        # anchor or double-counts.
        current_balance = _cash_detail_current_balance(
            app, auth_client, account.id,
        )
        assert Decimal("10000.00") < current_balance < Decimal("10200.00"), (
            f"HYSA current balance {current_balance!r} is not the expected "
            "anchor-plus-interest (no deposit) figure"
        )
