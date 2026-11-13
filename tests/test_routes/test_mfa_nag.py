"""Tests for the owner-role MFA enrolment nag banner (F-095 / C-12).

Covers the ``inject_mfa_nag_visible`` context processor in
``app/context_processors.py`` and the ``dashboard/_mfa_nag.html`` partial that
``base.html`` includes when the flag is truthy.

The banner must:

* Render for owner-role users who have no MFA enabled.
* Stay hidden for owners who DO have MFA enabled.
* Stay hidden for companion-role users (the audit scopes the nag to
  the de facto administrator role).
* Stay hidden for anonymous visitors (they cannot enrol from /login).
* Stay suppressed on the ``auth.mfa_*`` endpoints themselves so the
  banner does not stack on top of the page that fulfils the nag.
* Appear consistently across owner-role pages (the nag is global, not
  dashboard-specific) -- this is C-12's "more robust than the plan"
  expansion of "every owner-role landing page".

The marker the assertions check for is ``id="mfa-nag-banner"`` from the
partial.  Asserting on the DOM id rather than the copy text means the
copy can be tweaked without breaking the test, while still proving the
banner element rendered.

Every shared account is made with two-factor (plan#3600), so an owner who
sees the banner is ``no_two_factor_client``'s, the seed owner after the reset
act took it away.
"""

from flask_login import login_user

from app.extensions import db
from app.services import mfa_service
from tests._two_factor_sign_in import reset_two_factor_of, two_factor_row_of


_NAG_MARKER = b'id="mfa-nag-banner"'
_NAG_LINK = b'href="/mfa/setup"'
_NAG_COPY = b"Enable two-factor authentication to protect your financial data."


def _add_pending_mfa_for(user):
    """Give *user*'s reset two-factor row a pending setup (is_enabled=False).

    Models the state where a user visited ``/mfa/setup`` (writing a
    pending secret) but never POSTed ``/mfa/confirm``.  The nag must
    keep appearing in this state -- the user is still unprotected.
    """
    config = two_factor_row_of(user.id)
    config.pending_secret_encrypted = mfa_service.encrypt_secret("TESTBASE32SECRET")
    db.session.commit()


class TestMfaNagOwnerVisibility:
    """Banner appears for owner-role users without enabled MFA."""

    def test_banner_shows_for_owner_with_no_mfa_row(self, no_two_factor_client):
        """An owner without two-factor sees the nag on every page.

        ``seed_user`` is made with two-factor (plan#3600), and the fixture's
        reset takes it away, so the ``has_enabled_mfa`` query returns False
        and the banner renders.
        """
        resp = no_two_factor_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data
        assert _NAG_LINK in resp.data
        assert _NAG_COPY in resp.data

    def test_banner_shows_for_owner_with_pending_only_mfa(
        self, no_two_factor_client, seed_user,
    ):
        """A pending-but-unconfirmed setup keeps the banner visible.

        ``MfaConfig.is_enabled=False`` means the user started enrolment
        but did not confirm -- they are still unprotected, so the nag
        must continue to appear until ``is_enabled=True``.
        """
        _add_pending_mfa_for(seed_user["user"])

        resp = no_two_factor_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_hidden_when_mfa_enabled(self, auth_client):
        """An owner with confirmed MFA, as the seed owner is made, does NOT see the banner."""
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER not in resp.data
        assert _NAG_COPY not in resp.data


class TestMfaNagOtherRolesAndAnonymous:
    """Banner is scoped to authenticated owner-role users only."""

    def test_banner_hidden_for_companion(self, app, seed_user, seed_companion):
        """Companion-role users do not see the owner-targeted nag.

        The audit scopes F-095 to the owner role (de facto admin).
        Companions should never see the banner regardless of whether
        they have personally enrolled in MFA.

        **Asked of the processor, not a page** (ruling plan#3434).  A companion
        without two-factor is now held at its setup, whose endpoint hides the
        nag on its own, and ``companion_client`` signs in one who has it, whom
        the two-factor clause hides it from.  Neither page can tell the owner
        clause from the others, so this asks the processor for a request to
        her page, as her and as the owner, both without two-factor: the two
        answers differ in the role alone.
        """
        nag = next(
            processor for processor in app.template_context_processors[None]
            if processor.__name__ == "inject_mfa_nag_visible"
        )
        reset_two_factor_of(seed_user["user"].id)
        for user, visible in (
            (seed_companion["user"], False), (seed_user["user"], True),
        ):
            with app.test_request_context("/companion/"):
                login_user(user)
                assert nag() == {"mfa_nag_visible": visible}, user

    def test_banner_hidden_for_anonymous_user_on_login(self, client):
        """The login page must not render the nag.

        Anonymous visitors cannot act on ``/mfa/setup`` (login_required)
        and the context processor short-circuits on
        ``current_user.is_authenticated`` so the partial never includes.
        """
        resp = client.get("/login")
        assert resp.status_code == 200
        assert _NAG_MARKER not in resp.data


class TestMfaNagEndpointSuppression:
    """Banner is suppressed on the MFA enrolment endpoints themselves."""

    def test_banner_suppressed_on_mfa_setup_page(self, no_two_factor_client):
        """``/mfa/setup`` must not render the nag above its own form.

        The banner exists to drive the user to ``/mfa/setup``; once they
        are there it is redundant and visually noisy.  The context
        processor filters any ``auth.mfa_*`` endpoint.
        """
        resp = no_two_factor_client.get("/mfa/setup")
        assert resp.status_code == 200
        assert _NAG_MARKER not in resp.data


class TestMfaNagAcrossOwnerPages:
    """Banner renders consistently on every owner-role page.

    The audit's "every owner-role landing page" expectation is
    implemented as a base-template include driven by a global context
    processor, so any owner page that ``extends "base.html"`` shows the
    banner.  Spot-check a representative slice -- one major page per
    blueprint type (dashboard / settings / accounts / savings) -- to
    catch a regression that accidentally scopes the include to a
    subset.
    """

    def test_banner_on_settings_page(self, no_two_factor_client):
        """Settings dashboard renders the banner."""
        resp = no_two_factor_client.get("/settings")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_on_savings_dashboard(self, no_two_factor_client):
        """Savings dashboard renders the banner."""
        resp = no_two_factor_client.get("/savings")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_on_grid_index(self, no_two_factor_client):
        """Budget grid renders the banner."""
        resp = no_two_factor_client.get("/grid")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data


class TestMfaNagDismissibility:
    """Banner is a Bootstrap dismissible alert (data-bs-dismiss)."""

    def test_banner_includes_dismiss_button(self, no_two_factor_client):
        """The partial must render a Bootstrap close button.

        The plan calls for "dismissible" -- Bootstrap's standard
        ``data-bs-dismiss="alert"`` close control.  Per-page-load
        dismissal only; the banner reappears on the next navigation
        until ``MfaConfig.is_enabled=True``.
        """
        resp = no_two_factor_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data
        assert b'data-bs-dismiss="alert"' in resp.data
        assert b"alert-dismissible" in resp.data
