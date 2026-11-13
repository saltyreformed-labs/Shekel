"""Auth route tests: the /register endpoint, GET and POST."""

import logging
import re
from datetime import timedelta

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.services import pay_schedule_service
from app.config import BaseConfig
from app.models.pay_era import CADENCE_DAYS_MAX, CADENCE_DAYS_MIN
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN
from app.schemas.validation.pay_periods import PERIOD_BATCH_MAX
from app.utils.dates import display_today
from tests._seed_owner_fixtures import OWNERS_PAGE
from tests._test_helpers import enable_mfa, register_form_data
from tests._two_factor_sign_in import (
    redirect_path,
    sign_in_by_password,
    sign_in_from_a_trusted_browser,
    two_factor_row_of,
)
from tests.test_routes.test_auth_required import spy_on_the_hold

_SETUP = "/mfa/setup"


class TestRegistration:
    """Tests for the /register endpoint (GET and POST)."""

    def test_get_register_renders_form(self, app, client):
        """GET /register returns the registration form with all expected fields."""
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"Create Account" in response.data
            assert b'name="email"' in response.data
            assert b'name="display_name"' in response.data
            assert b'name="password"' in response.data
            assert b'name="confirm_password"' in response.data
            assert b"csrf_token" in response.data

    def test_get_register_renders_the_pay_schedule_inputs(self, app, client):
        """The form asks for the owner's real pay schedule (plan step X-ad-a).

        Three inputs the form did not carry before, because registration used
        to invent a pay period covering ``[today, today + 13]`` -- the
        fabrication finding **N-123** traces, which then refused thirteen of
        the fourteen paydays the owner might actually have.

        The rendered bounds are asserted, not just the field names.  They come
        from the same constants the validators read, and an input hint that
        drifts from the rule behind it teaches the user a bound that is not
        real -- so a template literal creeping back in has to fail here.
        """
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            body = response.data

            assert b'name="last_payday"' in body
            # The SCHEDULED day, not the day the money arrived (plan step
            # pay_calendar:C17-c-2a, ledger row PC-504), and the sentence
            # every payday door shares says which.
            assert b"What was your most recent scheduled payday?" in body
            assert b"not the day the money arrived" in body
            # No ``max`` on the USER's clock: under "pay the business day
            # before" the scheduled day of a paycheck already paid today can
            # be tomorrow, and the service holds the real bound at both ends.
            payday_input = re.search(
                rb'<input[^>]*name="last_payday"[^>]*>', body,
            ).group(0)
            assert b"max=" not in payday_input, payday_input

            assert b'name="cadence_days"' in body
            assert f'min="{CADENCE_DAYS_MIN}"'.encode() in body
            assert f'max="{CADENCE_DAYS_MAX}"'.encode() in body
            # Prefilled with the app's biweekly premise rather than blank.
            assert (
                f'value="{BaseConfig.DEFAULT_PAY_CADENCE_DAYS}"'.encode()
                in body
            )

            assert b'name="num_periods"' in body
            assert f'max="{PERIOD_BATCH_MAX}"'.encode() in body
            assert (
                f'value="{BaseConfig.DEFAULT_PAY_PERIOD_HORIZON}"'.encode()
                in body
            )

            # The fourth input (plan step balance:X-bh-2): how far back the
            # owner's paychecks reach.  OPTIONAL -- no ``required`` -- because
            # blank is a real answer, and bounded by the app's own calendar
            # window rather than by a template literal.
            assert b'name="history_opens_on"' in body
            assert b"When did these paychecks start?" in body
            assert f'min="{CALENDAR_DATE_MIN.isoformat()}"'.encode() in body
            assert f'max="{CALENDAR_DATE_MAX.isoformat()}"'.encode() in body

    def test_register_persists_a_stated_pay_history_opening(
        self, app, db, client,
    ):
        """The form's fourth control reaches ``budget.pay_schedule``.

        Plan step **balance:X-bh-2**.  End to end over HTTP rather than at the
        service, because the path this grades is the browser's: the control is
        rendered, submitted with every other one, normalized from a string,
        validated against the app's calendar window and written after the
        paydays that create the row it lands on.  A break anywhere in that
        chain reads as "the owner answered and the app ignored them".
        """
        payday = display_today()
        opening = (payday - timedelta(days=400)).isoformat()
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="history-route@example.com",
                display_name="History Route",
                last_payday=payday.isoformat(),
                history_opens_on=opening,
            ))

            assert response.status_code == 302
            user = db.session.query(User).filter_by(
                email="history-route@example.com",
            ).one()
            assert pay_schedule_service.get_schedule(
                user.id,
            ).history_opens_on.isoformat() == opening

    def test_register_with_the_history_box_untouched_stores_NULL(
        self, app, db, client,
    ):
        """THE CONTROL, and the commonest sign-up.

        ``register_form_data`` posts ``history_opens_on=""`` because that is
        what a browser sends for a control nobody touched.  Without this case
        the one above would pass against a form that wrote the same day for
        everybody, and the empty string would never be exercised at all --
        ``fields.Date`` refuses it as "Not a valid date." without the schema's
        pre-load, so this is the arm that keeps sign-up working.
        """
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="history-blank-route@example.com",
                display_name="History Blank Route",
                last_payday=display_today().isoformat(),
            ))

            assert response.status_code == 302
            user = db.session.query(User).filter_by(
                email="history-blank-route@example.com",
            ).one()
            assert pay_schedule_service.get_schedule(
                user.id,
            ).history_opens_on is None

    def test_register_preserves_the_pay_schedule_inputs_on_error(
        self, app, client, seed_user,
    ):
        """A rejected submission re-renders with the stated schedule intact.

        The credential fields already survived a round trip; the three new
        ones must too, or a duplicate-email retry silently drops the payday
        the owner just looked up -- and the field has no default to fall back
        on.
        """
        payday = (display_today() - timedelta(days=5)).isoformat()
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="test@shekel.local",
                display_name="Dup",
                last_payday=payday,
                cadence_days="7",
                num_periods="20",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"already exists" in response.data
            assert f'value="{payday}"'.encode() in response.data
            assert b'value="7"' in response.data
            assert b'value="20"' in response.data

    def test_get_register_has_login_link(self, app, client):
        """GET /register includes a link back to the login page."""
        with app.app_context():
            response = client.get("/register")
            assert response.status_code == 200
            assert b"Already have an account?" in response.data
            assert b"/login" in response.data

    def test_get_login_has_register_link(self, app, client):
        """GET /login includes a link to the registration page."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" in response.data
            assert b"/register" in response.data

    def test_register_success_creates_all_records(self, app, client):
        """POST /register with valid data creates User, UserSettings, and Scenario.

        Verifies the redirect to /login, the success flash message, and
        that all three database records exist with correct values.
        """
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="NewUser@Example.com",
                display_name="New User",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=False)

            assert response.status_code == 302
            assert "/login" in response.headers.get("Location", "")

            # Follow the redirect and check for flash message.
            follow = client.get(response.headers["Location"])
            assert b"Account created" in follow.data

            # Verify database records (email should be lowercased).
            user = db.session.query(User).filter_by(
                email="newuser@example.com"
            ).first()
            assert user is not None

            settings = db.session.query(UserSettings).filter_by(
                user_id=user.id
            ).first()
            assert settings is not None

            scenario = db.session.query(Scenario).filter_by(
                user_id=user.id, is_baseline=True
            ).first()
            assert scenario is not None

    def test_register_success_emits_user_registered_event(
        self, app, client, caplog,
    ):
        """POST /register emits ``user_registered`` (F-085 / C-14).

        Replaces the pre-C-14 ``logger.info("action=user_registered ...")``
        with a structured event including user_id and email so the
        Python tier of the audit story is queryable by event-name.
        """
        from app.utils.log_events import AUTH, EVT_USER_REGISTERED

        with app.app_context():
            with caplog.at_level(logging.INFO, logger="app.routes.auth"):
                response = client.post("/register", data=register_form_data(
                    email="audit-event@example.com",
                    display_name="Audit Event",
                    password="securepass123",
                    confirm_password="securepass123",
                ), follow_redirects=False)
            assert response.status_code == 302

            user = db.session.query(User).filter_by(
                email="audit-event@example.com"
            ).first()
            assert user is not None

            matching = [
                r for r in caplog.records
                if getattr(r, "event", None) == EVT_USER_REGISTERED
            ]
            assert len(matching) == 1, (
                f"Expected exactly one ``user_registered`` event; "
                f"observed: "
                f"{[(r.levelname, getattr(r, 'event', None)) for r in caplog.records]}"
            )
            record = matching[0]
            assert record.levelno == logging.INFO
            assert record.category == AUTH
            assert record.user_id == user.id
            assert record.email == "audit-event@example.com"

    def test_register_success_user_can_login(self, app, client):
        """A newly registered user can log in with their credentials.

        Verifies the full registration-to-login flow works end to end.
        """
        with app.app_context():
            # Register.
            client.post("/register", data=register_form_data(
                email="logintest@example.com",
                display_name="Login Test",
                password="securepass123",
                confirm_password="securepass123",
            ))

            # Log in with the same credentials.
            login_resp = client.post("/login", data={
                "email": "logintest@example.com",
                "password": "securepass123",
            }, follow_redirects=False)

            assert login_resp.status_code == 302
            # Should redirect to grid, not back to login.
            location = login_resp.headers.get("Location", "")
            assert "login" not in location

    def test_a_registered_owners_first_sign_in_is_held_at_setup(
        self, app, client, monkeypatch,
    ):
        """A new owner's password signs them in to two-factor setup, and their pages send them back.

        Registration writes no two-factor row at all, which makes it the
        commonest way an owner without two-factor reaches production
        (plan#3452).  So this is the hold's no-row branch for an owner, where
        ``no_two_factor_client`` is an owner a reset left with a row that is
        off.  The password lands them on the dashboard, which the hold sends
        to setup; setup opens, and Settings sends them back to it.
        """
        holds = spy_on_the_hold(monkeypatch)
        with app.app_context():
            client.post("/register", data=register_form_data(
                email="held-owner@example.com",
                display_name="Held Owner",
                password="securepass123",
                confirm_password="securepass123",
            ))
            owner = db.session.query(User).filter_by(
                email="held-owner@example.com",
            ).one()
            assert two_factor_row_of(owner.id) is None, (
                "registration wrote a two-factor row, so this case no longer "
                "starts from an owner with none"
            )
            sign_in_by_password(
                client, "held-owner@example.com", "securepass123", OWNERS_PAGE,
            )

            first_page = client.get(OWNERS_PAGE)
            setup = client.get(_SETUP)
            settings = client.get("/settings")

            assert first_page.status_code == 302, first_page.status_code
            assert redirect_path(first_page) == _SETUP
            assert setup.status_code == 200, setup.status_code
            assert b"Verify and Enable" in setup.data
            assert settings.status_code == 302, settings.status_code
            assert redirect_path(settings) == _SETUP
            assert holds == [
                ("GET", OWNERS_PAGE, "dashboard.page"),
                ("GET", "/settings", "settings.show"),
            ]

    def test_register_success_new_user_sees_empty_grid(
        self, app, client, seed_user, seed_periods_today
    ):
        """A newly registered user sees an empty grid with no seed user data.

        Verifies complete data isolation: the new user's grid does not
        contain transactions from the seed user.  A new account meets
        two-factor setup at its first sign-in (plan#3452), so the account is
        given two-factor first, as its setup would, and signs in through the
        shared sign-in.
        """
        with app.app_context():
            # Register a new user.
            client.post("/register", data=register_form_data(
                email="isolated@example.com",
                display_name="Isolated User",
                password="securepass123",
                confirm_password="securepass123",
            ))
            new_user = db.session.query(User).filter_by(
                email="isolated@example.com",
            ).one()
            enable_mfa(db.session, new_user.id)

            # Log in as the new user.
            sign_in_from_a_trusted_browser(
                client, "isolated@example.com", "securepass123", OWNERS_PAGE,
            )

            # Access the grid.
            grid_resp = client.get("/")
            assert grid_resp.status_code == 200
            # Should NOT contain seed user data.
            assert b"Rent Payment" not in grid_resp.data

    def test_register_duplicate_email_shows_error(
        self, app, client, seed_user
    ):
        """POST /register with an existing email shows the conflict error.

        Uses the seed_user fixture (test@shekel.local) to trigger a
        duplicate email conflict.
        """
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="test@shekel.local",
                display_name="Dup Test",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"already exists" in response.data

    def test_register_duplicate_email_preserves_form_input(
        self, app, client, seed_user
    ):
        """POST /register with a duplicate email preserves the submitted values.

        After a conflict error, the form should be re-rendered with the
        email and display_name fields pre-filled so the user does not
        have to re-type them.
        """
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="test@shekel.local",
                display_name="Keep This Name",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"test@shekel.local" in response.data
            assert b"Keep This Name" in response.data

    def test_register_short_password_shows_error(self, app, client):
        """POST /register with a short password shows a validation error."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="short@example.com",
                display_name="Short Test",
                password="12345678901",
                confirm_password="12345678901",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"at least 12 characters" in response.data

    def test_register_password_mismatch_shows_error(self, app, client):
        """POST /register with mismatched passwords shows an error."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="mismatch@example.com",
                display_name="Mismatch Test",
                password="validpassword1",
                confirm_password="validpassword2",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"do not match" in response.data

    def test_register_invalid_email_shows_error(self, app, client):
        """POST /register with an invalid email shows a validation error."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="notvalid",
                display_name="Invalid Email Test",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"Invalid email format" in response.data

    def test_register_empty_display_name_shows_error(self, app, client):
        """POST /register with an empty display name shows a validation error."""
        with app.app_context():
            response = client.post("/register", data=register_form_data(
                email="noname@example.com",
                display_name="",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=True)

            assert response.status_code == 200
            assert b"Display name is required" in response.data

    def test_register_get_redirects_when_authenticated(
        self, app, auth_client
    ):
        """GET /register redirects to the grid when already logged in."""
        with app.app_context():
            response = auth_client.get("/register", follow_redirects=False)
            assert response.status_code == 302
            # Should redirect to grid, not stay on register.
            location = response.headers.get("Location", "")
            assert "register" not in location

    def test_register_post_redirects_when_authenticated(
        self, app, auth_client, seed_user
    ):
        """POST /register redirects when already logged in, creating no new user.

        Verifies that authenticated users cannot create additional
        accounts via POST.
        """
        with app.app_context():
            user_count_before = db.session.query(User).count()

            response = auth_client.post("/register", data=register_form_data(
                email="sneaky@example.com",
                display_name="Sneaky",
                password="securepass123",
                confirm_password="securepass123",
            ), follow_redirects=False)

            assert response.status_code == 302

            # No new user should have been created.
            user_count_after = db.session.query(User).count()
            assert user_count_after == user_count_before

    def test_register_success_has_baseline_scenario(self, app, client):
        """POST /register creates exactly one baseline Scenario for the new user.

        Verifies the scenario has the correct name, is_baseline flag,
        and user_id.
        """
        with app.app_context():
            client.post("/register", data=register_form_data(
                email="scenario@example.com",
                display_name="Scenario Test",
                password="securepass123",
                confirm_password="securepass123",
            ))

            user = db.session.query(User).filter_by(
                email="scenario@example.com"
            ).first()
            assert user is not None

            scenarios = db.session.query(Scenario).filter_by(
                user_id=user.id
            ).all()
            assert len(scenarios) == 1
            assert scenarios[0].is_baseline is True
            assert scenarios[0].name == "Baseline"
            assert scenarios[0].user_id == user.id

    def test_register_success_creates_default_categories(self, app, client):
        """POST /register creates 22 default categories for the new user."""
        with app.app_context():
            client.post("/register", data=register_form_data(
                email="categories@example.com",
                display_name="Category Test",
                password="securepass123",
                confirm_password="securepass123",
            ))

            user = db.session.query(User).filter_by(
                email="categories@example.com"
            ).first()
            assert user is not None

            categories = db.session.query(Category).filter_by(
                user_id=user.id
            ).all()
            assert len(categories) == 24

    def test_register_success_creates_checking_account(self, app, client):
        """POST /register creates a default checking account for the new user."""
        with app.app_context():
            client.post("/register", data=register_form_data(
                email="acct@example.com",
                display_name="Account Test",
                password="securepass123",
                confirm_password="securepass123",
            ))

            user = db.session.query(User).filter_by(
                email="acct@example.com"
            ).first()
            assert user is not None

            accounts = db.session.query(Account).filter_by(
                user_id=user.id
            ).all()
            assert len(accounts) == 1
            assert accounts[0].name == "Checking"

    def test_register_disabled_get_returns_404(self, app, client):
        """GET /register returns 404 when REGISTRATION_ENABLED is False."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.get("/register")
            assert response.status_code == 404
            # The form must not leak in a custom 404 page.
            assert b"Create Account" not in response.data
            app.config["REGISTRATION_ENABLED"] = True

    def test_register_disabled_post_returns_404(self, app, client):
        """POST /register returns 404 and creates no user when disabled."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.post("/register", data=register_form_data(
                email="blocked@example.com",
                display_name="Blocked",
                password="securepass123",
                confirm_password="securepass123",
            ))
            assert response.status_code == 404

            # Confirm no user was created.
            user = db.session.query(User).filter_by(
                email="blocked@example.com"
            ).first()
            assert user is None
            app.config["REGISTRATION_ENABLED"] = True

    def test_login_hides_register_link_when_disabled(self, app, client):
        """GET /login omits the register link when registration is disabled."""
        with app.app_context():
            app.config["REGISTRATION_ENABLED"] = False
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" not in response.data
            app.config["REGISTRATION_ENABLED"] = True

    def test_login_shows_register_link_when_enabled(self, app, client):
        """GET /login shows the register link when registration is enabled."""
        with app.app_context():
            response = client.get("/login")
            assert response.status_code == 200
            assert b"Create an account" in response.data

    def test_register_post_rate_limited(self, app):
        """POST /register is rate-limited to 3 per hour (H5).

        Uses the same pattern as test_rate_limiting_after_5_attempts:
        create a fresh app with RATELIMIT_ENABLED=True, then verify
        the 4th POST triggers a 429.

        ``try``/``finally`` cleanup: the limiter is reset and disabled
        even when the assertion below fails.  Without this guard a
        failed assertion would leave ``limiter.enabled = True`` and a
        populated rate-limit bucket on the Limiter singleton, which
        every subsequent test on the same xdist worker would
        inherit -- see ``phase1-flake-investigation.md`` failure
        shape #1 for the full failure mode.
        """
        with app.app_context():
            rate_app = create_app("testing")
            rate_app.config["RATELIMIT_ENABLED"] = True

            from app.rate_limits import limiter
            limiter.enabled = True
            limiter.init_app(rate_app)

            try:
                rate_client = rate_app.test_client()

                with rate_app.app_context():
                    # Make 3 registration attempts (within the limit).
                    for i in range(3):
                        rate_client.post("/register", data=register_form_data(
                            email=f"bot{i}@example.com",
                            display_name=f"Bot {i}",
                            password="securepass123",
                            confirm_password="securepass123",
                        ))

                    # 4th attempt should be rate-limited.
                    response = rate_client.post("/register", data=register_form_data(
                        email="bot3@example.com",
                        display_name="Bot 3",
                        password="securepass123",
                        confirm_password="securepass123",
                    ))
                    assert response.status_code == 429
            finally:
                # Clean up: dispose the secondary app's engine to
                # release connections, clear the rate-limit storage's
                # counters (so a future test that flips
                # ``limiter.enabled = True`` without calling
                # ``init_app`` cannot inherit non-zero counts from
                # this test -- see Issue 2a of
                # shekel-audits/security-2026-04-15/c-38-followups.md),
                # and reset limiter for other tests.  The ``finally``
                # guarantees this runs even if an assertion above
                # raised.
                with rate_app.app_context():
                    from app.extensions import db as _db
                    _db.engine.dispose()
                if limiter._storage is not None:  # pylint: disable=protected-access
                    limiter.reset()
                limiter.enabled = False
