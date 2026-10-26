"""Account route tests: the list, the edit form's danger zone, and creating, updating and archiving
an account.
"""

from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.utils.dates import display_today
from app.models.ref import AccountType
from app.services import account_service, cash_ledger, pay_period_service

from ._helpers import _create_other_user_account


# ── Account CRUD ───────────────────────────────────────────────────


class TestAccountList:
    """Tests for GET /accounts (the retired table is now a redirect, P4)."""

    def test_accounts_url_redirects_to_cockpit(self, app, auth_client, seed_user):
        """GET /accounts 302-redirects to the Net Worth Cockpit.

        Loop B P4 retired the standalone /accounts management table; the
        endpoint is kept only as a permanent redirect to savings.dashboard
        so old bookmarks resolve and the unauthenticated-redirect contract
        stays green.  No list page is rendered here anymore.
        """
        with app.app_context():
            response = auth_client.get("/accounts")

            assert response.status_code == 302
            assert response.headers["Location"].endswith("/savings")

    def test_accounts_redirect_lands_on_cockpit_with_accounts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Following the /accounts redirect shows the user's accounts on the cockpit."""
        with app.app_context():
            response = auth_client.get("/accounts", follow_redirects=True)

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_new_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/new renders the account creation form."""
        with app.app_context():
            response = auth_client.get("/accounts/new")

            assert response.status_code == 200
            assert b'name="name"' in response.data
            assert b'name="anchor_balance"' in response.data
            assert b"New Account" in response.data


class TestEditFormDangerZone:
    """The shared edit form hosts hard-delete in a Danger Zone (Loop B P4).

    P4 retired the /accounts table that used to expose hard-delete; the
    developer ruling (audit decision 12) relocated it to the edit form so
    every account type can be deleted from one shared surface, including
    Savings and Credit Card, which have no per-type detail page.
    """

    def test_edit_form_has_delete_danger_zone(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit renders a hard-delete form in the Danger Zone."""
        with app.app_context():
            account_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{account_id}/edit")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Danger Zone" in html
            # The delete form posts to the hard-delete route for THIS account.
            assert f"/accounts/{account_id}/hard-delete" in html

    def test_create_form_has_no_danger_zone(self, app, auth_client, seed_user):
        """GET /accounts/new (create mode) shows no delete affordance.

        The Danger Zone is gated on ``{% if account %}`` so it never appears
        while creating a brand-new account.
        """
        with app.app_context():
            resp = auth_client.get("/accounts/new")

            assert resp.status_code == 200
            html = resp.data.decode()
            assert "Danger Zone" not in html
            assert "hard-delete" not in html


class TestAccountCreate:
    """Tests for POST /accounts."""

    def test_create_account(self, app, auth_client, seed_user):
        """POST /accounts creates a new account and redirects to the list."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            response = auth_client.post("/accounts", data={
                "name": "Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Savings&#39; created." in response.data

            # Verify in the database.
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("500.00")

    def test_create_account_stores_the_submitted_observed_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A back-dated "Balance as of" is stored, and sets the anchor period.

        The user is entering an account they already had: they type today's
        balance but say it was true on a day inside an EARLIER pay period.  Two
        things must follow (ruling R-DH, plan step 2):

        * the origination row's ``observed_on`` is the submitted day, not the
          day the form was submitted -- it is the day the whole anchor/settle
          partition compares settled rows against;
        * the anchor PERIOD is resolved FROM that day, not from today.  A row
          asserting an earlier day while filed against today's period would put
          its correction's journal entry in a period its own ``entry_date``
          falls outside.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            earlier = seed_periods_today[0]
            observed = earlier.start_date + timedelta(days=1)

            response = auth_client.post("/accounts", data={
                "name": "Old Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": observed.isoformat(),
            }, follow_redirects=True)
            assert response.status_code == 200

            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Old Savings")
                .one()
            )
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct.id)
                .one()
            )
            assert row.observed_on == observed
            # Non-vacuity: the submitted day is genuinely not today, so this
            # cannot pass by the default coinciding with the assertion.
            assert observed != display_today()
            # The ASSERTION carries no period since ruling R-EO -- a fact
            # about a bank is not filed under a budgeting artifact -- and the
            # account carries none either since ruling R-EH.  The rule this
            # case exists for is that the SUBMITTED day survives the round
            # trip, which the assertion above grades; the period half of it
            # went with the columns.
            assert not hasattr(row, "pay_period_id")

    def test_create_account_refuses_a_future_observed_on(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A balance cannot be observed on a day that has not happened.

        The route surfaces the service's own message and returns the user to
        the form -- not to /pay-periods/generate, which is the other shape that
        raises the same exception type.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            tomorrow = display_today() + timedelta(days=1)

            response = auth_client.post("/accounts", data={
                "name": "Future Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": tomorrow.isoformat(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"has not happened yet" in response.data
            # Back to the account form, NOT to /pay-periods/generate -- the
            # other shape that raises the same exception type.
            assert b"New Account -- Shekel" in response.data
            assert (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Future Savings")
                .first()
            ) is None

    def test_create_account_refuses_an_observed_on_before_the_schedule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A day before the recorded history has no period to be filed against.

        Unbounded back-dating is not a cosmetic problem: ``observed_on`` opens
        the modelled-return accrual window and the contribution model's first
        period, so a day in the distant past fabricates history and folds over
        every calendar day since (finding N-133, the review of the F1 revert).
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            too_early = seed_periods_today[0].start_date - timedelta(days=1)
            assert too_early < display_today()

            response = auth_client.post("/accounts", data={
                "name": "Ancient Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": too_early.isoformat(),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"recorded history starts on" in response.data
            assert (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Ancient Savings")
                .first()
            ) is None

    def test_create_account_defaults_observed_on_to_today(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An omitted "Balance as of" means today, applied by the factory.

        The schema drops the empty input, so the route passes ``None`` and the
        default is stated once in ``account_service.create_account`` rather
        than per caller.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            auth_client.post("/accounts", data={
                "name": "Today Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
                "observed_on": "",
            }, follow_redirects=True)

            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Today Savings")
                .one()
            )
            row = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=acct.id)
                .one()
            )
            assert row.observed_on == display_today()

    def test_new_account_form_bounds_the_observed_on_input(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date input carries the same two bounds the service enforces.

        The browser refuses what the service would refuse, rather than
        round-tripping a rejection.  The floor is read from
        ``pay_period_service.earliest_recordable_day`` -- the SAME function
        ``anchor_service.resolve_observation_day`` refuses below -- rather than
        from the fixture's own first period, so this pins the form to the
        implementation instead of to a value that merely happens to match.
        It named ``account_service.earliest_observable_day`` until ruling
        **R-ER** deleted that pass-through (plan step X-f1c4c).
        """
        with app.app_context():
            floor = pay_period_service.earliest_recordable_day(
                seed_user["user"].id,
            )
            # Non-vacuity: the fixture's first period IS that floor today, so
            # state the equality rather than letting the two silently diverge.
            assert floor == seed_periods_today[0].start_date

            html = auth_client.get("/accounts/new").data.decode()

            assert 'name="observed_on"' in html
            assert f'value="{display_today().isoformat()}"' in html
            assert f'max="{display_today().isoformat()}"' in html
            assert f'min="{floor.isoformat()}"' in html

    def test_creating_an_account_without_a_schedule_is_refused_not_500(
        self, app, auth_client, seed_user,
    ):
        """The schedule precondition, which no test could see before.

        Ruling **R-ER** split this arm out of the shared day rule into
        ``account_service._require_pay_period_schedule``, promoted it to run
        FIRST in ``create_account``, and rewrote its rationale.  A neutral
        review then deleted the guard outright and watched **6,218 tests stay
        green** -- so the split, the reorder and the message were all shipping
        ungraded.

        What it protects: ``create_account``'s tail posts the opening's anchor
        correction, and that reconcile derives each correction's pay period from
        the day it asserts (ruling R-EA).  With an empty calendar there is no
        such period and the reconcile raises (finding **N-192**), so without
        this guard the user gets an unhandled 500 on a money route instead of a
        flash pointing at the repair.
        """
        # Imported here, matching this module's established local-import idiom
        # for the model (see ``_create_other_user_account``).
        from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel

        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            assert db.session.query(PayPeriod).filter_by(
                user_id=user_id,
            ).count() == 0, "precondition: the owner has no schedule"
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            accounts_before = db.session.query(Account).filter_by(
                user_id=user_id,
            ).count()

            response = auth_client.post("/accounts", data={
                "name": "Scheduleless Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "500.00",
            }, follow_redirects=True)

            # A designed refusal, not a crash, and it points at the repair.
            assert response.status_code == 200
            assert b"Generate pay periods" in response.data
            # No account was created -- the guard runs before the factory.
            assert db.session.query(Account).filter_by(
                user_id=user_id,
            ).count() == accounts_before

    def test_create_account_zero_anchor_balance(self, app, auth_client, seed_user):
        """POST /accounts with anchor_balance "0" stores an exact zero.

        A submitted zero opening balance is a value, not a missing one:
        the route must persist ``Decimal("0")`` rather than treating the
        falsy zero as absent.  Omitting the field entirely also defaults
        to zero.
        """
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

            response = auth_client.post("/accounts", data={
                "name": "Zero Savings",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }, follow_redirects=True)
            assert response.status_code == 200
            acct = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="Zero Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct).balance == Decimal("0")

            # Omitting anchor_balance entirely also defaults to zero.
            response = auth_client.post("/accounts", data={
                "name": "No Balance Savings",
                "account_type_id": savings_type.id,
            }, follow_redirects=True)
            assert response.status_code == 200
            acct2 = (
                db.session.query(Account)
                .filter_by(user_id=seed_user["user"].id, name="No Balance Savings")
                .one()
            )
            assert cash_ledger.resolve_anchor(acct2).balance == Decimal("0")

    def test_create_account_validation_error(self, app, auth_client, seed_user):
        """POST /accounts with missing name shows a validation error."""
        with app.app_context():
            response = auth_client.post("/accounts", data={
                "name": "",
                "account_type_id": "",
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_create_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts with a duplicate name shows a warning flash."""
        with app.app_context():
            # "Checking" already exists from seed_user.
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post("/accounts", data={
                "name": "Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data


class TestAccountUpdate:
    """Tests for GET/POST /accounts/<id>/edit."""

    def test_edit_account_form_renders(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit renders the edit form."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.get(f"/accounts/{account_id}/edit")

            assert response.status_code == 200
            assert b"Checking" in response.data

    def test_edit_form_offers_no_balance_field(
        self, app, auth_client, seed_user,
    ):
        """The EDIT form has no way to assert a balance (X-f1e, N-195).

        The surface half of the deletion, and it is tested separately from the
        route half because the two fail independently: a route that ignores the
        field while the form still renders it shows the user a control that
        silently does nothing, and a form that drops it while the route still
        accepts it leaves the second door reachable by a forged POST.

        Paired with the CREATE form below, which must KEEP both inputs -- an
        account's opening balance is a different fact from a later reading, and
        a deletion that took the opening with it would be a regression wearing
        this step's clothes.
        """
        with app.app_context():
            account_id = seed_user["account"].id

            html = auth_client.get(f"/accounts/{account_id}/edit").data.decode()

            assert 'name="anchor_balance"' not in html
            assert 'name="observed_on"' not in html
            # The fields this door DOES own are still there.
            assert 'name="name"' in html
            assert 'name="account_type_id"' in html

    def test_create_form_keeps_the_opening_assertion(
        self, app, auth_client, seed_user,
    ):
        """The CREATE form keeps the balance AND the day it was true."""
        with app.app_context():
            html = auth_client.get("/accounts/new").data.decode()

            assert 'name="anchor_balance"' in html
            assert 'name="observed_on"' in html

    def test_update_account(self, app, auth_client, seed_user):
        """POST /accounts/<id> updates the account and redirects."""
        with app.app_context():
            account_id = seed_user["account"].id
            checking_type = db.session.query(AccountType).filter_by(name="Checking").one()

            response = auth_client.post(f"/accounts/{account_id}", data={
                "name": "Primary Checking",
                "account_type_id": checking_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"Account &#39;Primary Checking&#39; updated." in response.data

            # Verify in the database.
            acct = db.session.get(Account, account_id)
            assert acct.name == "Primary Checking"

    def test_update_account_duplicate_name(self, app, auth_client, seed_user):
        """POST /accounts/<id> with a duplicate name shows a warning."""
        with app.app_context():
            # Create a second account first.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            second = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(second)
            db.session.commit()

            # Try to rename it to "Checking" (already exists).
            response = auth_client.post(f"/accounts/{second.id}", data={
                "name": "Checking",
                "account_type_id": savings_type.id,
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"An account with that name already exists." in response.data

    def test_edit_other_users_account_redirects(self, app, auth_client, seed_user):
        """GET /accounts/<id>/edit for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.get(
                f"/accounts/{other['account'].id}/edit",
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_update_other_users_account_redirects(self, app, auth_client, seed_user):
        """POST /accounts/<id> for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}",
                data={"name": "Hacked"},
                follow_redirects=True,
            )

            assert response.status_code == 404

            # Verify name was not changed.
            acct = db.session.get(Account, other["account"].id)
            assert acct.name == "Other Checking"


class TestAccountArchive:
    """Tests for POST /accounts/<id>/archive and /unarchive."""

    def test_archive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/archive archives the account."""
        with app.app_context():
            account_id = seed_user["account"].id

            response = auth_client.post(
                f"/accounts/{account_id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"archived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is False

    def test_unarchive_account(self, app, auth_client, seed_user):
        """POST /accounts/<id>/unarchive restores an archived account."""
        with app.app_context():
            account_id = seed_user["account"].id

            # Archive first.
            seed_user["account"].is_active = False
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{account_id}/unarchive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"unarchived" in response.data

            acct = db.session.get(Account, account_id)
            assert acct.is_active is True

    def test_archive_account_with_active_transfers(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive is blocked when active transfer templates reference it."""
        with app.app_context():
            from app.models.transfer_template import TransferTemplate

            # Create a second account and an active transfer template.
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Savings",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            template = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                name="Monthly Savings",
                default_amount=Decimal("200.00"),
                is_active=True,
            )
            db.session.add(template)
            db.session.commit()

            response = auth_client.post(
                f"/accounts/{seed_user['account'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Cannot archive this account" in response.data

            # Account should still be active.
            acct = db.session.get(Account, seed_user["account"].id)
            assert acct.is_active is True

    def test_archive_other_users_account_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /accounts/<id>/archive for another user's account returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_account()

            response = auth_client.post(
                f"/accounts/{other['account'].id}/archive",
                follow_redirects=True,
            )

            assert response.status_code == 404

            # Other user's account should still be active.
            acct = db.session.get(Account, other["account"].id)
            assert acct.is_active is True

    def test_create_account_double_submit(self, app, auth_client, seed_user):
        """POST /accounts twice with the same name flashes duplicate on 2nd."""
        with app.app_context():
            savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
            data = {
                "name": "Emergency Fund",
                "account_type_id": savings_type.id,
                "anchor_balance": "0",
            }

            # First submit succeeds.
            response1 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"created" in response1.data

            # Second submit hits duplicate guard.
            response2 = auth_client.post("/accounts", data=data, follow_redirects=True)
            assert b"An account with that name already exists." in response2.data
