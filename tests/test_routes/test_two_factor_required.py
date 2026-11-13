"""Every companion must use two-factor, and there is no switch (ruling plan#3434).

Plan#2407, task plan#3445.  A signed-in companion without two-factor reaches
only its setup, its confirmation and signing out (the login gate's hold,
``app/login_gate.py``): a page, an htmx request and a POST are each sent to
setup, and the POST is not performed.  Once she confirms setup she reaches her
pages.  She cannot turn it off: ``/mfa/disable`` refuses her and her Security
page has no Disable.  ``scripts/reset_mfa.py`` still clears it, and she meets
setup at her next sign-in.  An owner without two-factor is not held.

The hold's policy, its endpoint set and its sweep over every served route are
pinned beside the anonymous gate's, in ``tests/test_routes/test_auth_required.py``.
Who must have two-factor is ``User.must_have_two_factor``, its one home.
"""

from urllib.parse import urlsplit

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.user import User
from app.services import mfa_service
from scripts.reset_mfa import reset_mfa
from tests._test_helpers import generate_row_of, make_expense_template
from tests.test_routes.test_auth_required import spy_on_the_hold
from tests._two_factor_sign_in import (
    SEED_COMPANION_EMAIL,
    SEED_COMPANION_PASSWORD,
    code_now,
    sign_in_seed_companion,
    sign_in_seed_companion_by_password,
    two_factor_row_of,
    two_factor_secret_of,
)

_SETUP = "/mfa/setup"
_HER_PAGE = "/companion/"
_HER_ENDPOINT = "companion.index"
_MARK_DONE = "transactions.mark_done"

#: The setup page's sentence for a user held there (the "why she is here").
_WHY_HELD = b"Your account requires two-factor authentication. Set it up here to"

#: The Security page's sentence for a user who must have two-factor.
_REQUIRED = b"Your account requires two-factor authentication, and it is on."

_REFUSED = (
    b"Two-factor authentication is required for your account and cannot "
    b"be turned off."
)

_DISABLE_LINK = b'href="/mfa/disable"'


def _companion_visible_bill(seed_user, period):
    """A companion-visible Projected bill in *period*; return its id."""
    template = make_expense_template(
        db.session, seed_user, amount="120.00", name="Hotel",
        category_key="Rent", companion_visible=True,
    )
    row = generate_row_of(template, period)
    db.session.commit()
    return row.id


def _status_of(txn_id):
    """The row's status id, read fresh."""
    db.session.expire_all()
    return db.session.get(Transaction, txn_id).status_id


@pytest.fixture(name="holds")
def _holds(monkeypatch):
    """Record ``(method, path, endpoint)`` for each request the hold ends.

    The hold's decision, observed through the one spy on it
    (:func:`tests.test_routes.test_auth_required.spy_on_the_hold`), so a view
    that redirects to setup on its own (``/mfa/confirm`` does) is never read
    as the hold's.

    Yields:
        The spy's live list.
    """
    yield spy_on_the_hold(monkeypatch)


def _assert_sent_to_setup(response):
    """The hold's plain answer: a redirect to the setup page."""
    assert response.status_code == 302, response.status_code
    assert urlsplit(response.headers["Location"]).path == _SETUP, (
        response.headers["Location"]
    )


class TestACompanionWithoutTwoFactorIsHeldAtSetup:
    """She reaches setup and sign-out, and every other request is sent to setup."""

    @pytest.mark.usefixtures("seed_companion")
    def test_her_page_sends_her_to_setup_which_says_why(self, app, holds):
        """Signed in with her password, her page sends her to setup, which says why."""
        with app.app_context():
            browser = sign_in_seed_companion_by_password(app)

            response = browser.get(_HER_PAGE)

            _assert_sent_to_setup(response)
            assert holds == [("GET", _HER_PAGE, _HER_ENDPOINT)]
            setup = browser.get(_SETUP)
            assert setup.status_code == 200
            assert _WHY_HELD in setup.data
            assert b"Verify and Enable" in setup.data

    @pytest.mark.usefixtures("seed_companion")
    def test_an_htmx_post_is_sent_to_setup_and_not_performed(
        self, app, seed_user, seed_periods_today, holds,
    ):
        """An htmx save gets ``HX-Redirect`` to setup, and the row stays Projected."""
        with app.app_context():
            txn_id = _companion_visible_bill(seed_user, seed_periods_today[4])
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            assert _status_of(txn_id) == projected
            browser = sign_in_seed_companion_by_password(app)

            response = browser.post(
                f"/transactions/{txn_id}/mark-done", headers={"HX-Request": "true"},
            )

            assert response.status_code == 204, response.status_code
            assert response.headers["HX-Redirect"] == _SETUP
            assert response.data == b""
            assert holds == [("POST", f"/transactions/{txn_id}/mark-done", _MARK_DONE)]
            assert _status_of(txn_id) == projected

    @pytest.mark.usefixtures("seed_companion")
    def test_a_plain_post_is_sent_to_setup_and_not_performed(
        self, app, seed_user, seed_periods_today, holds,
    ):
        """A form POST is redirected to setup, and the row stays Projected."""
        with app.app_context():
            txn_id = _companion_visible_bill(seed_user, seed_periods_today[4])
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            browser = sign_in_seed_companion_by_password(app)

            response = browser.post(f"/transactions/{txn_id}/mark-done")

            _assert_sent_to_setup(response)
            assert holds == [("POST", f"/transactions/{txn_id}/mark-done", _MARK_DONE)]
            assert _status_of(txn_id) == projected

    @pytest.mark.usefixtures("seed_companion")
    def test_a_session_signed_in_before_the_rule_is_held_at_its_next_request(
        self, app, monkeypatch, holds,
    ):
        """A session that reached her page before the rule is held once it holds.

        The rule is switched off for the sign-in and her first page, the world
        before this card, and back on for the next request of the same session.
        """
        with app.app_context():
            with monkeypatch.context() as before_the_rule:
                before_the_rule.setattr(
                    User, "must_have_two_factor", property(lambda user: False),
                )
                browser = sign_in_seed_companion_by_password(app)
                assert browser.get(_HER_PAGE).status_code == 200

            response = browser.get(_HER_PAGE)

            _assert_sent_to_setup(response)
            assert holds == [("GET", _HER_PAGE, _HER_ENDPOINT)]

    @pytest.mark.usefixtures("seed_companion")
    def test_a_url_no_route_matches_is_sent_to_setup(self, app, holds):
        """The hold has no exception the route table does not name."""
        with app.app_context():
            browser = sign_in_seed_companion_by_password(app)

            response = browser.get("/this-page-does-not-exist")

            _assert_sent_to_setup(response)
            assert holds == [("GET", "/this-page-does-not-exist", None)]

    @pytest.mark.usefixtures("seed_companion")
    def test_she_can_sign_out(self, app, holds):
        """Sign-out is reached, and signs her out."""
        with app.app_context():
            browser = sign_in_seed_companion_by_password(app)

            response = browser.post("/logout")

            assert response.status_code == 302
            assert urlsplit(response.headers["Location"]).path == "/login"
            assert holds == []
            after = browser.get(_HER_PAGE)
            assert urlsplit(after.headers["Location"]).path == "/login"

    def test_confirming_setup_lets_her_reach_her_pages(
        self, app, seed_user, seed_companion, seed_periods_today, holds,
    ):
        """Setup, then her code: her page opens and her save is performed.

        The same save the two cases above saw refused is the control here, so
        the refusal there is the hold's and not the row's.
        """
        with app.app_context():
            companion_id = seed_companion["user"].id
            txn_id = _companion_visible_bill(seed_user, seed_periods_today[4])
            browser = sign_in_seed_companion_by_password(app)
            assert browser.get(_SETUP).status_code == 200
            secret = mfa_service.decrypt_secret(
                two_factor_row_of(companion_id).pending_secret_encrypted,
            )

            codes = browser.post("/mfa/confirm", data={"totp_code": code_now(secret)})

            assert codes.status_code == 200
            assert b"Save Your Backup Codes" in codes.data
            page = browser.get(_HER_PAGE)
            assert page.status_code == 200
            assert b"Hotel" in page.data
            saved = browser.post(
                f"/transactions/{txn_id}/mark-done", headers={"HX-Request": "true"},
            )
            assert saved.status_code == 200, saved.status_code
            assert _status_of(txn_id) == ref_cache.status_id(StatusEnum.DONE)
            assert holds == []


class TestAnOwnerWithoutTwoFactorIsNotHeld:
    """The rule is a companion's; an owner without two-factor reaches every page."""

    def test_the_dashboard_and_settings_open(self, app, no_two_factor_client, holds):
        """An owner without two-factor opens the dashboard and Settings."""
        with app.app_context():
            assert no_two_factor_client.get("/dashboard").status_code == 200
            assert no_two_factor_client.get("/settings").status_code == 200
            assert holds == []

    def test_the_setup_page_does_not_say_it_is_required(self, app, no_two_factor_client):
        """The owner's setup page carries no "required" sentence: it is optional."""
        with app.app_context():
            response = no_two_factor_client.get(_SETUP)

            assert response.status_code == 200
            assert _WHY_HELD not in response.data


class TestSheCannotTurnItOff:
    """``/mfa/disable`` refuses her, and her Security page has no Disable."""

    @staticmethod
    def _enrolled(app, companion_id):
        """Her browser, signed in through her shared sign-in, and her secret.

        The sign-in types no code, so the one code a case types is the first
        her secret ever accepts.
        """
        return sign_in_seed_companion(app), two_factor_secret_of(companion_id)

    def test_the_disable_page_refuses_her(self, app, seed_companion):
        """GET ``/mfa/disable`` sends her to her Security page, saying why."""
        with app.app_context():
            browser, _ = self._enrolled(app, seed_companion["user"].id)

            response = browser.get("/mfa/disable")

            assert response.status_code == 302
            assert response.headers["Location"] == "/security"
            assert _REFUSED in browser.get("/security").data

    def test_the_right_password_and_code_turn_nothing_off(self, app, seed_companion):
        """POST with both right is refused before either is read.

        Her two-factor stays on with its secret, and the code was never
        checked: the step it would have recorded is still unset.
        """
        with app.app_context():
            companion_id = seed_companion["user"].id
            browser, secret = self._enrolled(app, companion_id)

            response = browser.post("/mfa/disable", data={
                "current_password": SEED_COMPANION_PASSWORD,
                "totp_code": code_now(secret),
            })

            assert response.status_code == 302
            assert response.headers["Location"] == "/security"
            assert _REFUSED in browser.get("/security").data
            config = two_factor_row_of(companion_id)
            assert config.is_enabled is True
            assert config.totp_secret_encrypted is not None
            assert config.last_totp_timestep is None

    def test_her_security_page_says_it_is_required_and_has_no_disable(
        self, app, seed_companion,
    ):
        """Her two-factor card says it is required and links no Disable."""
        with app.app_context():
            browser, _ = self._enrolled(app, seed_companion["user"].id)

            response = browser.get("/security")

            assert response.status_code == 200
            assert _REQUIRED in response.data
            assert _DISABLE_LINK not in response.data
            assert b"Regenerate Backup Codes" in response.data

    def test_an_owners_security_page_keeps_disable(self, app, auth_client):
        """The control: an owner with two-factor still has Disable, and no "required"."""
        with app.app_context():
            response = auth_client.get("/security")

            assert response.status_code == 200
            assert _DISABLE_LINK in response.data
            assert _REQUIRED not in response.data


class TestTheResetScriptStillClearsIt:
    """``scripts/reset_mfa.py`` clears hers, and she meets setup at her next sign-in."""

    @pytest.mark.usefixtures("seed_companion")
    def test_she_meets_setup_after_a_reset(self, app, holds):
        """Signed in with her code, reset, then signed in again: held at setup."""
        with app.app_context():
            browser = sign_in_seed_companion(app)
            assert browser.get(_HER_PAGE).status_code == 200

            reset_mfa(SEED_COMPANION_EMAIL)

            ended = browser.get(_HER_PAGE)
            assert urlsplit(ended.headers["Location"]).path == "/login"
            again = sign_in_seed_companion_by_password(app)
            _assert_sent_to_setup(again.get(_HER_PAGE))
            assert holds == [("GET", _HER_PAGE, _HER_ENDPOINT)]


class TestCompanionsSaysSo:
    """The owner's Companions screen says each companion sets it up."""

    def test_the_intro_says_each_companion_sets_it_up(self, app, auth_client):
        """Companions' intro names two-factor at a companion's first sign-in."""
        with app.app_context():
            response = auth_client.get("/settings?section=companions")

            assert response.status_code == 200
            # Compared with its whitespace collapsed: the template wraps it.
            assert (
                b"Each companion sets up two-factor authentication at their "
                b"first sign-in." in b" ".join(response.data.split())
            )
