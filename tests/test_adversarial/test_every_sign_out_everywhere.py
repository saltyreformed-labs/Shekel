"""Shekel Budget App -- Adversarial Tests: every sign-out everywhere deletes the sign-ins' records.

Plan#2171, rulings **plan#3927** and **plan#4010**: every act that signs a user
out everywhere works by deleting their sign-ins' records, through one act
(``app.services.sign_in_service.sign_out_everywhere``), so a session is live
exactly when its record is and nothing on the account or in the cookie decides
it.  These tests drive each door on the real app, with test clients holding
copies of browsers' session cookies, as someone who copied them would
(``tests._session_copies``):

* :class:`TestTheActsABrowserPresses` -- Log out all other sessions and Change
  password: the browser that pressed it stays signed in, under a new token with
  its thirty days unchanged, and every other browser and every copy, its own
  included, is refused; every trust ends.
* :class:`TestABackupCodeSignIn` -- every earlier browser and its copy are
  refused, the new sign-in is the user's one record, and every trust ends.
* :class:`TestTheOwnersActsOnACompanion` -- changing her password,
  deactivating her (then reactivating her, so ``is_active`` is not what
  refuses) and resetting her two-factor: her browser and its copy are refused
  and her trust ends, while the owner who pressed it stays signed in.
* :class:`TestTheScripts` -- ``scripts/reset_mfa.py`` and
  ``scripts/rotate_sessions.py``, which sign out every browser of a user, and
  of every user.

Each test's control is the same browsers signed in a request before the act.
"""

import pytest

from app.extensions import db
from app.models.sign_in import SignIn
from app.models.user import User
from app.services import auth_service
from app.utils.bearer_tokens import token_hash
from scripts.reset_mfa import reset_mfa
from scripts.rotate_sessions import execute_rotation
from tests._other_user_fixtures import SEED_SECOND_USER_EMAIL, SEED_SECOND_USER_PASSWORD
from tests._seed_owner_fixtures import (
    OWNERS_PAGE, SEED_USER_EMAIL, SEED_USER_PASSWORD, log_in_seed_user,
)
from tests._session_copies import (
    copy_of, is_signed_in, sign_ins_of, token_of, trusted_device_ids,
)
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import (
    redirect_path, sign_in_from_a_trusted_browser,
)

_NEW_PASSWORD = "a-brand-new-passphrase"

#: A companion's own page, which a signed-in companion reads.
_HER_PAGE = "/companion/"

_BACKUP_CODE = "aaaaaaaa"


def _log_out_all_other_sessions(browser):
    """Press "Log out all other sessions" on *browser*, asserting the Security page answers."""
    response = browser.post("/invalidate-sessions")
    assert redirect_path(response) == "/security"


def _change_the_password(browser):
    """Change the seed owner's password on *browser*, asserting it changed."""
    response = browser.post("/change-password", data={
        "current_password": SEED_USER_PASSWORD,
        "new_password": _NEW_PASSWORD,
        "confirm_password": _NEW_PASSWORD,
    })
    assert redirect_path(response) == "/security"
    owner = db.session.query(User).filter_by(email=SEED_USER_EMAIL).one()
    db.session.refresh(owner)
    assert auth_service.verify_password(_NEW_PASSWORD, owner.password_hash)


def _record_of(token):
    """Return the sign-in record *token* names, read fresh, or ``None``."""
    db.session.expire_all()
    return db.session.query(SignIn).filter_by(token_hash=token_hash(token)).one_or_none()


@pytest.fixture(
    name="press",
    params=[
        pytest.param(_log_out_all_other_sessions, id="log-out-all-other-sessions"),
        pytest.param(_change_the_password, id="change-password"),
    ],
)
def _press(request):
    """One act a signed-in browser presses, called with that browser."""
    return request.param


class TestTheActsABrowserPresses:
    """The pressing browser stays signed in; no other browser or copy does."""

    @pytest.mark.usefixtures("seed_user")
    def test_every_other_browser_and_every_copy_is_refused(self, app, press):
        """A copy of the pressing browser's own cookie too (ruling plan#4010)."""
        pressing = log_in_seed_user(app.test_client())
        other = log_in_seed_user(app.test_client())
        copy_of_pressing = copy_of(app, pressing)
        copy_of_other = copy_of(app, other)
        for browser in (pressing, other, copy_of_pressing, copy_of_other):
            assert is_signed_in(browser)

        press(pressing)

        assert is_signed_in(pressing)
        assert not is_signed_in(other)
        assert not is_signed_in(copy_of_other)
        assert not is_signed_in(copy_of_pressing)

    def test_the_pressing_browser_goes_on_under_a_new_token_from_its_sign_in(
        self, app, seed_user, press,
    ):
        """Its record is the user's one, its thirty days unchanged (ruling plan#3069)."""
        user_id = seed_user["user"].id
        pressing = log_in_seed_user(app.test_client())
        log_in_seed_user(app.test_client())
        old_token = token_of(pressing)
        signed_in_at = _record_of(old_token).signed_in_at

        press(pressing)

        new_token = token_of(pressing)
        assert new_token != old_token
        assert _record_of(old_token) is None
        assert sign_ins_of(user_id) == {token_hash(new_token)}
        assert _record_of(new_token).signed_in_at == signed_in_at

    def test_every_trust_ends(self, app, seed_user, press):
        """The pressing browser's own trust too: the act ends every one."""
        user_id = seed_user["user"].id
        pressing = log_in_seed_user(app.test_client())
        log_in_seed_user(app.test_client())
        assert len(trusted_device_ids(user_id)) == 2

        press(pressing)

        assert trusted_device_ids(user_id) == set()


class TestABackupCodeSignIn:
    """The sign-in a backup code finishes is the user's only one left."""

    def test_every_earlier_browser_and_its_copy_is_refused_and_every_trust_ends(
        self, app, seed_user,
    ):
        """The new sign-in is recorded after the act, so the act never ends it."""
        user_id = seed_user["user"].id
        earlier = log_in_seed_user(app.test_client())
        copy_of_earlier = copy_of(app, earlier)
        assert is_signed_in(copy_of_earlier)
        assert len(trusted_device_ids(user_id)) == 1
        store_backup_codes(db.session, user_id, [_BACKUP_CODE])

        browser = app.test_client()
        response = browser.post(
            "/login", data={"email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD},
        )
        assert redirect_path(response) == "/mfa/verify"
        response = browser.post("/mfa/verify", data={"backup_code": _BACKUP_CODE})
        assert redirect_path(response) == OWNERS_PAGE

        assert is_signed_in(browser)
        assert not is_signed_in(earlier)
        assert not is_signed_in(copy_of_earlier)
        assert sign_ins_of(user_id) == {token_hash(token_of(browser))}
        assert trusted_device_ids(user_id) == set()


def _change_her_password(owner, companion_id):
    """The owner gives the companion a new password on Settings > Companions."""
    companion = db.session.get(User, companion_id)
    response = owner.post(f"/settings/companions/{companion_id}/edit", data={
        "email": companion.email,
        "display_name": companion.display_name,
        "password": _NEW_PASSWORD,
        "password_confirm": _NEW_PASSWORD,
    })
    assert response.status_code == 302


def _deactivate_then_reactivate_her(owner, companion_id):
    """The owner deactivates the companion, then reactivates her.

    Reactivated, she is active again, so what refuses her old browser is its
    record's absence alone.
    """
    for act in ("deactivate", "reactivate"):
        response = owner.post(f"/settings/companions/{companion_id}/{act}")
        assert response.status_code == 302
    assert db.session.get(User, companion_id).is_active is True


def _reset_her_two_factor(owner, companion_id):
    """The owner resets the companion's two-factor."""
    response = owner.post(f"/settings/companions/{companion_id}/reset-two-factor")
    assert response.status_code == 302


class TestTheOwnersActsOnACompanion:
    """Each of her owner's acts on her account ends every sign-in and trust of hers."""

    @pytest.mark.parametrize(
        "act",
        [
            pytest.param(_change_her_password, id="change-her-password"),
            pytest.param(_deactivate_then_reactivate_her, id="deactivate-her"),
            pytest.param(_reset_her_two_factor, id="reset-her-two-factor"),
        ],
    )
    def test_her_browser_and_its_copy_are_refused_and_her_trust_ends(
        self, app, auth_client, seed_companion, companion_client, act,
    ):
        """The owner who pressed it stays signed in."""
        companion_id = seed_companion["user"].id
        copy = copy_of(app, companion_client)
        assert is_signed_in(copy, _HER_PAGE)
        assert len(trusted_device_ids(companion_id)) == 1

        act(auth_client, companion_id)

        assert not is_signed_in(companion_client, _HER_PAGE)
        assert not is_signed_in(copy, _HER_PAGE)
        assert sign_ins_of(companion_id) == set()
        assert trusted_device_ids(companion_id) == set()
        assert is_signed_in(auth_client)


class TestTheScripts:
    """The operator's two scripts keep no browser."""

    def test_reset_mfa_signs_the_user_out_everywhere(self, app, seed_user):
        """Every browser and copy is refused, and every trust ends."""
        user_id = seed_user["user"].id
        browser = log_in_seed_user(app.test_client())
        copy = copy_of(app, browser)
        assert is_signed_in(copy)
        assert len(trusted_device_ids(user_id)) == 1

        reset_mfa(SEED_USER_EMAIL)

        assert not is_signed_in(browser)
        assert not is_signed_in(copy)
        assert sign_ins_of(user_id) == set()
        assert trusted_device_ids(user_id) == set()

    @pytest.mark.usefixtures("seed_user", "seed_second_user")
    def test_rotate_sessions_signs_every_user_out_everywhere(self, app):
        """Both users' browsers and copies are refused, and every trust ends."""
        browsers = [
            log_in_seed_user(app.test_client()),
            sign_in_from_a_trusted_browser(
                app.test_client(), SEED_SECOND_USER_EMAIL, SEED_SECOND_USER_PASSWORD,
                OWNERS_PAGE,
            ),
        ]
        copies = [copy_of(app, browser) for browser in browsers]
        user_ids = [user.id for user in db.session.query(User).all()]
        assert len(user_ids) == 2
        assert all(is_signed_in(copy) for copy in copies)

        execute_rotation(db.session)

        for browser in browsers + copies:
            assert not is_signed_in(browser)
        for user_id in user_ids:
            assert sign_ins_of(user_id) == set()
            assert trusted_device_ids(user_id) == set()
