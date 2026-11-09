"""Salary route tests: listing, creating, updating and reactivating a salary profile.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.services import pay_list_service
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.account import Account
from app.models.scenario import Scenario
from app.models.ref import AccountType, FilingStatus
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis

from app.services import account_service

from tests._stub_period import stub_period_on
from tests._test_helpers import create_account_of_type, create_loan_account, NO_TAX_LAW

from ._helpers import _create_profile, _create_other_user_profile, _respace_paydays
from app.services.balance_at import BalanceContext
from app.services.obligations_aggregator import todays_price


# ── Profile CRUD ───────────────────────────────────────────────────


class TestProfileList:
    """Tests for GET /salary (the cockpit) and GET /salary/new."""

    def test_cockpit_renders(self, app, auth_client, seed_user, seed_periods):
        """GET /salary renders the salary cockpit for the primary profile."""
        with app.app_context():
            _create_profile(seed_user)

            response = auth_client.get("/salary")

            assert response.status_code == 200
            assert b"Day Job" in response.data
            assert b"Net per paycheck" in response.data
            # The card's own element, not its title: the pay stubs strip
            # names the card by title too (ruling R-SAL126), so the title
            # alone would pass with the card gone.
            assert b'id="anatomy-composition"' in response.data

    def test_new_profile_form(self, app, auth_client, seed_user):
        """GET /salary/new renders the salary profile creation form."""
        with app.app_context():
            response = auth_client.get("/salary/new")

            assert response.status_code == 200
            # The first pay entry, typed per paycheck (plan step
            # salary:X-av-3a, ruling R-SAL61).
            assert b'name="pay_amount"' in response.data
            assert b'name="pay_payday"' in response.data
            assert b'name="filing_status_id"' in response.data
            assert b"New Salary Profile" in response.data


class TestProfileCreate:
    """Tests for POST /salary."""

    def test_create_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary creates a profile with linked template and recurrence."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"created" in response.data

            # Verify profile and template were created.
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id, name="Day Job")
                .one()
            )
            # The salary is the profile's pay list: ONE entry, what was typed,
            # from the payday it was typed for (plan step salary:X-av-3a).
            assert [(e.payday, e.amount) for e in profile.pay_entries] == [
                (date(2026, 1, 2), Decimal("2884.62")),
            ]
            assert profile.template is not None
            assert profile.template.is_active is True

    def test_create_profile_yearly_pay_follows_the_cadence(
        self, app, auth_client, seed_user, seed_periods, tax_law,
    ):
        """A weekly owner's created pay is 52 paychecks a year, not 26.

        Input: a create POST of $1,000.00 a paycheck from 2026-01-02, from an
        owner whose paydays are 7 days apart.
        Expected: the template is born at $1,000.00 (no tax law, so net ==
        gross), and the pay list states the entry at 52 paychecks a year,
        $1,000.00 x 52 = $52,000.00 (ruling R-SAL59).
        Why: pay is typed per paycheck since plan step salary:X-av-3a, so the
        yearly division this case graded until then (the salary over the
        derived count) is gone from the create door.  The derived count
        survives in the yearly figure, and a count hardcoded to 26 -- the
        regression plan step R-F16 removed, where a weekly owner accepting
        the dropdown's default was modelled at double their pay -- reads
        $26,000.00 here.  Every other case in this module is biweekly, where
        the derived count and the old constant agree.
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()
            # A COHERENT weekly owner: the paydays are respaced with the
            # cadence, not just relabelled.  A 7-day cadence beside 14-day
            # paydays is a state no door in the application can produce, and
            # asserting a figure for one pins nothing about a real owner.
            _respace_paydays(db, seed_user["user"].id, cadence_days=7)

            auth_client.post("/salary", data={
                "name": "Weekly Check",
                "pay_amount": "1000.00",  # $52,000.00 a year / 52
                "pay_payday": "2026-01-02",  # the first weekly payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id, name="Weekly Check")
                .one()
            )
            # Typed 1,000.00; gross == net here (no tax law).  Today's paycheck
            # is the definition's figure (the template states no price since
            # plan step X-bp, where this read its ``default_amount`` copy).
            assert todays_price(
                profile.template, BalanceContext.build(seed_user["user"].id),
            ).amount == Decimal("1000.00")
            rows = pay_list_service.pay_rows(
                PayrollBasis(profile, calendar_for(seed_user["user"].id)),
            )
            # 1,000.00 x 52 = 52,000.00.
            assert [(row.paychecks_a_year, row.yearly) for row in rows] == [
                (52, Decimal("52000.00")),
            ]

    def test_the_salary_form_states_the_derived_paycheck_count(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The form SHOWS the count and offers no control to change it.

        Input: ``GET /salary/new`` for an owner at a 7-day cadence and again
        at 14.
        Expected: "52" then "26" under a "Paychecks / Year" label, no
        ``pay_periods_per_year`` input of any kind, and a link to the
        pay-period settings that own the cadence.
        Why: the count is what the page's own gross preview divides by, so
        hiding it makes that figure unexplainable -- but rendering a SECOND
        control for it is the finding.  **Both cadences are asked** because a
        readout hardcoded to 26 renders correctly for the biweekly fixture and
        is the exact regression this replaces; only the weekly read
        distinguishes them.  Asserting the absence of the input matters just
        as much: ``BaseSchema``'s ``unknown = EXCLUDE`` means a stale control
        would submit silently and reach nothing, so nothing else in the suite
        would fail if one came back.
        """
        with app.app_context():
            for cadence_days, shown in ((7, ">52<"), (14, ">26<")):
                _respace_paydays(db, seed_user["user"].id, cadence_days)

                response = auth_client.get("/salary/new")

                assert response.status_code == 200
                body = response.data.decode()
                assert b'name="pay_periods_per_year"' not in response.data
                assert b'id="pay_periods_per_year"' not in response.data
                assert "Paychecks / Year" in body
                assert "from your pay schedule" in body
                marker = body.index("Paychecks / Year")
                assert shown in body[marker:marker + 400], (
                    f"a {cadence_days}-day cadence rendered "
                    f"{body[marker:marker + 400]!r}"
                )

    def test_create_profile_validation_error(self, app, auth_client, seed_user):
        """POST /salary with missing fields shows a validation error."""
        with app.app_context():
            response = auth_client.post("/salary", data={
                "name": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_profile_no_baseline_scenario(self, app, auth_client, seed_user):
        """POST /salary with no baseline scenario flashes danger."""
        with app.app_context():
            # Remove the baseline scenario.
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2024-01-05",  # the seed owner's bootstrap payday
                **stub_period_on("2024-01-05"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"No baseline scenario found" in response.data
            assert b"register" in response.data

    def test_create_profile_no_active_account(self, app, auth_client, seed_user):
        """POST /salary with no active account flashes danger."""
        with app.app_context():
            # Deactivate the account.
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2024-01-05",  # the seed owner's bootstrap payday
                **stub_period_on("2024-01-05"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"active account" in response.data
            assert b"/accounts/new" in response.data

    def test_create_profile_refuses_a_loan_only_user(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With only a loan active, /salary refuses and posts nothing to it.

        A salary profile deposits recurring income; depositing it onto a loan
        would have the recurrence engine generate raw income transactions the
        loan fold cannot see (finding N-11 / ruling D4).  The picker excludes
        amortizing accounts, so a user whose only active account is a loan is
        told to create a real account, and no profile, template, or transaction
        lands on the loan.
        """
        with app.app_context():
            for acct in (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, is_active=True).all()
            ):
                acct.is_active = False
            loan = create_loan_account(
                seed_user, db.session, name="Only Loan",
                principal=Decimal("200000.00"), rate=Decimal("0.06"),
                origination_date=date(2026, 1, 1),
            )
            db.session.commit()
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()
            resp = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"not a loan" in resp.data
            assert (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id).count() == 0
            )
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=loan.id).count() == 0
            )

    def test_create_profile_picks_a_non_loan_over_a_loan(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The picker skips a loan EVEN WHEN the loan would win query order.

        A real negative control: every seeded account is deactivated, then the
        loan is created FIRST (so it is the lowest active id an unordered
        ``.first()`` returns), then a non-loan account AFTER it.  Without the
        ``has_amortization`` exclusion the picker would return the loan and
        generate income onto it; the exclusion sends the deposit to the later
        non-loan account instead.  Reverting the filter makes this test pick the
        loan and fail -- which the vacuous version (checking already lower-id)
        could not do.
        """
        with app.app_context():
            for acct in (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, is_active=True).all()
            ):
                acct.is_active = False
            db.session.flush()
            loan = create_loan_account(
                seed_user, db.session, name="First Loan",
                principal=Decimal("200000.00"), rate=Decimal("0.06"),
                origination_date=date(2026, 1, 1),
            )
            savings_type = db.session.query(AccountType).filter_by(
                name="Savings",
            ).one()
            deposit_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Late Checking",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(deposit_account)
            db.session.commit()
            # The loan is the lower-id active account, so an unordered .first()
            # would return it if the exclusion were removed.
            assert loan.id < deposit_account.id
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()
            resp = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"created" in resp.data
            profile = db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id, name="Day Job",
            ).one()
            assert profile.template.account_id == deposit_account.id
            assert profile.template.account_id != loan.id
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=loan.id).count() == 0
            )

    def test_create_profile_refuses_a_card_only_user(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """With only a credit card active, /salary refuses and posts nothing to it.

        The loan twin above, for plan step credit_card:CC-10 (design 3.8): a
        card's balance IS the cash fold, so a paycheck deposited onto it would
        be counted as income paying the card down.  The picker asks
        ``active_accounts_query``'s orthogonal ``revolving=False``, so a user
        whose only active account is a card is told to create a real account,
        and no profile, template or transaction lands on the card.
        """
        with app.app_context():
            for acct in (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, is_active=True).all()
            ):
                acct.is_active = False
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Only Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()
            resp = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"not a loan or a credit card" in resp.data
            assert (
                db.session.query(SalaryProfile)
                .filter_by(user_id=seed_user["user"].id).count() == 0
            )
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=card.id).count() == 0
            )

    def test_create_profile_picks_a_non_card_over_a_card(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The picker skips a card EVEN WHEN the card would win query order.

        The loan twin's exact shape: every seeded account deactivated, the
        card created FIRST (the lowest active id an unordered ``.first()``
        returns), a Savings account AFTER it.  Without ``revolving=False`` the
        picker would return the card and generate income onto it; with it
        the deposit lands on the later Savings.  Reverting the kwarg makes
        this test pick the card and fail.
        """
        with app.app_context():
            for acct in (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, is_active=True).all()
            ):
                acct.is_active = False
            db.session.flush()
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "First Card",
                anchor_balance=Decimal("-500.00"),
            )
            deposit_account = create_account_of_type(
                seed_user, db.session, "Savings", "Late Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            # The card is the lower-id active account, so an unordered .first()
            # would return it if the exclusion were removed.
            assert card.id < deposit_account.id
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single",
            ).one()
            resp = auth_client.post("/salary", data={
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)
            assert resp.status_code == 200
            assert b"created" in resp.data
            profile = db.session.query(SalaryProfile).filter_by(
                user_id=seed_user["user"].id, name="Day Job",
            ).one()
            assert profile.template.account_id == deposit_account.id
            assert profile.template.account_id != card.id
            assert (
                db.session.query(Transaction)
                .filter_by(account_id=card.id).count() == 0
            )

    def test_create_profile_double_submit(self, app, auth_client, seed_user, seed_periods):
        """POST /salary twice results in an error on the second attempt."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
            data = {
                "name": "Day Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                **stub_period_on("2026-01-02"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }

            # First submit succeeds.
            response1 = auth_client.post("/salary", data=data, follow_redirects=True)
            assert b"created" in response1.data

            # Second submit with same name triggers unique constraint error.
            response2 = auth_client.post("/salary", data=data, follow_redirects=True)
            assert b"Failed to create salary profile" in response2.data

    def test_create_profile_no_account_flash_has_link(
        self, app, auth_client, seed_user
    ):
        """POST /salary with no active account flashes a message with a create-account link."""
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            account.is_active = False
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
            response = auth_client.post("/salary", data={
                "name": "Link Test",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2024-01-05",  # the seed owner's bootstrap payday
                **stub_period_on("2024-01-05"),
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"/accounts/new" in response.data


class TestProfileUpdate:
    """Tests for GET/POST /salary/<id>/edit and POST /salary/<id>/delete."""

    def test_edit_profile_form(self, app, auth_client, seed_user, seed_periods):
        """GET /salary/<id>/edit renders the edit form."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.get(f"/salary/{profile.id}/edit")

            assert response.status_code == 200
            assert b"Day Job" in response.data

    def test_update_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id> updates the profile and redirects."""
        with app.app_context():
            profile = _create_profile(seed_user)
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            response = auth_client.post(f"/salary/{profile.id}", data={
                "name": "Updated Job",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"updated" in response.data

            # The update takes no salary since plan step salary:X-av-3a (a
            # salary change is the pay list's Fix); the name is the edit.
            db.session.refresh(profile)
            assert profile.name == "Updated Job"

    def test_delete_profile(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/delete deactivates the profile and its template."""
        with app.app_context():
            profile = _create_profile(seed_user)
            template_id = profile.template_id

            response = auth_client.post(
                f"/salary/{profile.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"deactivated" in response.data

            db.session.refresh(profile)
            assert profile.is_active is False

            template = db.session.get(TransactionTemplate, template_id)
            assert template.is_active is False

    def test_edit_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """GET /salary/<id>/edit for another user's profile returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.get(
                f"/salary/{other['profile'].id}/edit",
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_update_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/<id> for another user's profile returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.post(
                f"/salary/{other['profile'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_delete_other_users_profile_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/<id>/delete for another user's profile returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()

            response = auth_client.post(
                f"/salary/{other['profile'].id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 404


class TestReactivate:
    """POST /salary/<id>/reactivate: the inverse of delete_profile."""

    def test_reactivate_full_cycle(self, app, auth_client, seed_user, seed_periods):
        """Deactivate then reactivate restores the profile, template, and txns."""
        with app.app_context():
            profile = _create_profile(seed_user)
            template_id = profile.template_id

            # Deactivate first.
            auth_client.post(f"/salary/{profile.id}/delete", follow_redirects=True)
            db.session.expire_all()
            assert db.session.get(SalaryProfile, profile.id).is_active is False

            # Reactivate.
            resp = auth_client.post(
                f"/salary/{profile.id}/reactivate", follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"reactivated" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(SalaryProfile, profile.id)
            assert refreshed.is_active is True
            template = db.session.get(TransactionTemplate, template_id)
            assert template.is_active is True

            # Transactions were regenerated for the reactivated template.
            txn_count = (
                db.session.query(Transaction)
                .filter_by(
                    template_id=template_id,
                    scenario_id=seed_user["scenario"].id,
                )
                .count()
            )
            assert txn_count > 0

    def test_reactivate_already_active(self, app, auth_client, seed_user, seed_periods):
        """Reactivating an already-active profile is a no-op info flash."""
        with app.app_context():
            profile = _create_profile(seed_user)

            resp = auth_client.post(
                f"/salary/{profile.id}/reactivate", follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already active" in resp.data

    def test_reactivate_other_user_404(self, app, auth_client, seed_user):
        """Reactivating another user's profile 404s and leaves it unchanged."""
        with app.app_context():
            other = _create_other_user_profile()
            # Deactivate the victim's profile directly.
            other["profile"].is_active = False
            db.session.commit()

            resp = auth_client.post(f"/salary/{other['profile'].id}/reactivate")
            assert resp.status_code == 404

            db.session.expire_all()
            assert db.session.get(
                SalaryProfile, other["profile"].id
            ).is_active is False

    def test_reactivate_version_conflict(
        self, app, auth_client, seed_user, seed_periods, monkeypatch
    ):
        """A StaleDataError during reactivation flashes, redirects, rolls back."""
        with app.app_context():
            profile = _create_profile(seed_user)
            profile.is_active = False
            if profile.template:
                profile.template.is_active = False
            db.session.commit()

            # Force the regeneration flush to raise the concurrent-edit error
            # the optimistic-lock guard converts into a flash + redirect.
            # Pylint: import-outside-toplevel -- file-wide test convention.
            from app.routes.salary import profiles as profiles_mod  # pylint: disable=import-outside-toplevel
            from sqlalchemy.orm.exc import StaleDataError  # pylint: disable=import-outside-toplevel

            def _raise_stale(_profile):
                raise StaleDataError("simulated concurrent edit")

            monkeypatch.setattr(
                profiles_mod, "_regenerate_salary_transactions", _raise_stale,
            )

            resp = auth_client.post(
                f"/salary/{profile.id}/reactivate", follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"changed by another action" in resp.data

            db.session.expire_all()
            # Rolled back: the profile stays inactive.
            assert db.session.get(SalaryProfile, profile.id).is_active is False
