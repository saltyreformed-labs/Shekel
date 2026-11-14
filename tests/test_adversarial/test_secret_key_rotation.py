"""
Shekel Budget App -- Adversarial Tests for SECRET_KEY Rotation

Regression tests that verify the two protections against forged or
replayed session cookies after a SECRET_KEY rotation:

1. **Signature mismatch** (covered by Flask itself): a cookie signed
   with the old SECRET_KEY fails signature verification under the new
   key, so the session is treated as empty and the user is treated as
   anonymous.
2. **Every sign-in's record deleted** (``scripts/rotate_sessions.py``,
   through the one sign-out-everywhere act, ruling plan#3927): even if an
   attacker preserves cookies that *would* verify under the same key (for
   example, a downgrade attack that re-uses the old key), ``load_user``
   refuses a session whose sign-in's record is gone.  That every browser
   and every copy of every user is refused is graded in
   ``tests/test_adversarial/test_every_sign_out_everywhere.py``
   (``TestTheScripts``); here, that a sign-in after the rotation is not.

If either test starts failing, the post-rotation security posture
described in ``docs/runbook_secrets.md`` is broken.

Related audit findings: F-001, F-016 (and the operational control
shipped in ``scripts/rotate_sessions.py``).
"""

from app.extensions import db
from scripts.rotate_sessions import execute_rotation
from tests._seed_owner_fixtures import log_in_seed_user
from tests._session_copies import is_signed_in


class TestSecretKeyRotation:
    """Cookie/session rejection paths after SECRET_KEY rotation."""

    def test_session_cookie_signed_with_old_key_is_rejected(
        self, app, auth_client, monkeypatch
    ):
        """A cookie signed under the original SECRET_KEY no longer
        opens a session once the key has been rotated.

        Setup: log the user in, capturing a cookie signed under the
        current key K1.  Action: swap ``app.config["SECRET_KEY"]`` to a
        different value K2.  Verification: the next request
        ``GET /dashboard`` with the same cookie redirects to ``/login``,
        because Flask cannot verify the cookie's signature against K2.

        Without this protection, an attacker who captured a cookie
        before the rotation could continue using it (for as long as the
        old key remained accepted) -- which is exactly the threat that
        SECRET_KEY rotation is meant to close.
        """
        # Sanity: the auth_client has an active session pre-rotation.
        pre = auth_client.get("/dashboard")
        assert pre.status_code == 200, (
            "Setup failed: auth_client should be logged in before the "
            f"rotation; got {pre.status_code}"
        )

        # Rotate the key.  monkeypatch.setitem registers a teardown so
        # subsequent tests are not affected.  The new key must be
        # distinct from the conftest test key and must satisfy any
        # length floor that ProdConfig would enforce (here, ProdConfig
        # is not in play, but using a 64-char value matches reality).
        new_key = "post-rotation-key-distinct-from-conftest-test-suite-fixed-key"
        assert new_key != app.config["SECRET_KEY"], (
            "Test setup error: rotation key must differ from the test "
            "SECRET_KEY for the signature mismatch to fire."
        )
        monkeypatch.setitem(app.config, "SECRET_KEY", new_key)

        # Same cookie, new key: signature mismatch.  Flask treats the
        # session as empty, current_user is anonymous, and any
        # @login_required route redirects to /login.
        post = auth_client.get("/dashboard", follow_redirects=False)
        assert post.status_code == 302
        assert "/login" in post.headers["Location"]

    def test_a_sign_in_after_the_rotation_is_accepted(self, app, auth_client):
        """The complement of the rotation's refusal.

        A browser that signs in AFTER the rotation must be accepted --
        otherwise ``load_user`` would lock everyone out forever after the
        first rotation.  The control is the browser signed in before it,
        refused.
        """
        execute_rotation(db.session)
        assert not is_signed_in(auth_client)

        assert is_signed_in(log_in_seed_user(app.test_client()))
