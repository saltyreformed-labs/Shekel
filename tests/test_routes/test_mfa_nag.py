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
"""

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from app.utils.field_encryption import encrypt_secret


_NAG_MARKER = b'id="mfa-nag-banner"'
_NAG_LINK = b'href="/mfa/setup"'
_NAG_COPY = b"Enable two-factor authentication to protect your financial data."


def _enable_mfa_for(user):
    """Persist a fully-enabled MfaConfig row for *user*.

    Mirrors the production ``/mfa/confirm`` end-state: a real encrypted
    secret, a hashed backup-code list, and ``is_enabled=True``.  Using
    ``mfa_service`` rather than hand-rolled fixtures keeps the test
    coupled to the production encrypt/hash paths -- if those change the
    fixture follows automatically.
    """
    config = MfaConfig(
        user_id=user.id,
        is_enabled=True,
        totp_secret_encrypted=encrypt_secret("TESTBASE32SECRET"),
        backup_codes=mfa_service.hash_backup_codes(
            ["aaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        ),
    )
    db.session.add(config)
    db.session.commit()


def _add_pending_mfa_for(user):
    """Persist an MfaConfig row that is mid-setup (is_enabled=False).

    Models the state where a user visited ``/mfa/setup`` (writing a
    pending secret) but never POSTed ``/mfa/confirm``.  The nag must
    keep appearing in this state -- the user is still unprotected.
    """
    config = MfaConfig(
        user_id=user.id,
        is_enabled=False,
        pending_secret_encrypted=encrypt_secret("TESTBASE32SECRET"),
    )
    db.session.add(config)
    db.session.commit()


class TestMfaNagOwnerVisibility:
    """Banner appears for owner-role users without enabled MFA."""

    def test_banner_shows_for_owner_with_no_mfa_row(self, auth_client):
        """A fresh owner with no MfaConfig row sees the nag on every page.

        ``seed_user`` does not create an MfaConfig row, so the
        ``has_enabled_mfa`` query returns False and the banner renders.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data
        assert _NAG_LINK in resp.data
        assert _NAG_COPY in resp.data

    def test_banner_shows_for_owner_with_pending_only_mfa(
        self, auth_client, seed_user,
    ):
        """A pending-but-unconfirmed setup keeps the banner visible.

        ``MfaConfig.is_enabled=False`` means the user started enrolment
        but did not confirm -- they are still unprotected, so the nag
        must continue to appear until ``is_enabled=True``.
        """
        _add_pending_mfa_for(seed_user["user"])

        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_hidden_when_mfa_enabled(self, auth_client, seed_user):
        """An owner with confirmed MFA does NOT see the banner."""
        _enable_mfa_for(seed_user["user"])

        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER not in resp.data
        assert _NAG_COPY not in resp.data


class TestMfaNagOtherRolesAndAnonymous:
    """Banner is scoped to authenticated owner-role users only."""

    def test_banner_hidden_for_companion(self, companion_client):
        """Companion-role users do not see the owner-targeted nag.

        The audit scopes F-095 to the owner role (de facto admin).
        Companions should never see the banner regardless of whether
        they have personally enrolled in MFA.
        """
        resp = companion_client.get("/companion/")
        assert resp.status_code == 200
        assert _NAG_MARKER not in resp.data

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

    def test_banner_suppressed_on_mfa_setup_page(self, auth_client):
        """``/mfa/setup`` must not render the nag above its own form.

        The banner exists to drive the user to ``/mfa/setup``; once they
        are there it is redundant and visually noisy.  The context
        processor filters any ``auth.mfa_*`` endpoint.
        """
        resp = auth_client.get("/mfa/setup")
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

    def test_banner_on_settings_page(self, auth_client):
        """Settings dashboard renders the banner."""
        resp = auth_client.get("/settings")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_on_savings_dashboard(self, auth_client):
        """Savings dashboard renders the banner."""
        resp = auth_client.get("/savings")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data

    def test_banner_on_grid_index(self, auth_client):
        """Budget grid renders the banner."""
        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data


class TestMfaNagDismissibility:
    """Banner is a Bootstrap dismissible alert (data-bs-dismiss)."""

    def test_banner_includes_dismiss_button(self, auth_client):
        """The partial must render a Bootstrap close button.

        The plan calls for "dismissible" -- Bootstrap's standard
        ``data-bs-dismiss="alert"`` close control.  Per-page-load
        dismissal only; the banner reappears on the next navigation
        until ``MfaConfig.is_enabled=True``.
        """
        resp = auth_client.get("/")
        assert resp.status_code == 200
        assert _NAG_MARKER in resp.data
        assert b'data-bs-dismiss="alert"' in resp.data
        assert b"alert-dismissible" in resp.data
