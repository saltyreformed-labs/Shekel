"""Shekel Budget App -- Adversarial Tests for TOTP Replay Prevention (C-09).

End-to-end coverage for the two findings closed by commit C-09 of the
2026-04-15 security remediation plan:

  * F-005 (CWE-294): ``pyotp.TOTP.verify(..., valid_window=1)`` is
    stateless, so any observed 6-digit TOTP code remains replayable
    for ~90 seconds (the +-1 step drift window) until the code rotates
    out.  Closed by ``mfa_configs.last_totp_timestep`` plus the
    strict-greater check in ``mfa_service.verify_totp_code``.

  * F-142 (Low): no structured log event for replay detection.  Closed
    by emitting ``totp_replay_rejected`` from
    ``app/routes/auth/mfa.py:mfa_verify`` and ``mfa_disable_confirm``
    (via the shared ``_helpers`` verifier) when ``verify_totp_code``
    returns ``REPLAY``.

The unit-level coverage in ``tests/test_services/test_mfa_service.py``
exercises the verifier in isolation.  This file exercises the route
glue: the column update is committed across requests, the log event is
emitted on the right path, the replay-rejection branch returns the
right HTTP response, and the cross-route invariants (mfa_verify,
mfa_disable_confirm, mfa_confirm) all interact with
``last_totp_timestep`` consistently.

**The seed owner is made with two-factor** (plan#3600), a fresh secret
per test (``tests._test_helpers.enable_mfa``), so each test types codes
for the owner's own secret (:func:`_its_two_factor`).  The confirm tests
need an account without two-factor, so they sign in as the owner after a
reset (``no_two_factor_client``).
"""

import logging
import time

import pyotp

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from tests._two_factor_sign_in import two_factor_row_of, two_factor_secret_of


def _its_two_factor(user_id, last_step=None):
    """Set a shared account's last accepted step; return its two-factor secret.

    The account is made with two-factor and a fresh secret, and its shared
    sign-in types no code, so its row's ``last_totp_timestep`` is ``None``
    until a test sets it.

    Args:
        user_id: ``auth.users.id`` of the shared account.
        last_step: The ``last_totp_timestep`` to store.  Defaults to
            ``None`` (mirrors a brand-new enrollment that has not yet
            completed any verifications).

    Returns:
        The plaintext base32 secret the row holds.
    """
    row = two_factor_row_of(user_id)
    row.last_totp_timestep = last_step
    db.session.commit()
    return two_factor_secret_of(user_id)


class TestTotpReplayPreventionAtMfaVerify:
    """Replay rejection on the ``/mfa/verify`` (login completion) route.

    Each test runs the full /login -> /mfa/verify sequence on a real
    test client so the assertion about ``last_totp_timestep`` reflects
    a database-committed state, not just an in-memory mutation.
    """

    def test_replay_within_drift_window_is_rejected(
        self, app, client, seed_user,
    ):
        """A captured-and-replayed code is rejected on the second call.

        Precise model of the F-005 attack: an attacker observes a
        valid 6-digit code (over the shoulder, from a phishing-page
        reflection, etc.) and submits it through their own session
        moments later.  Without C-09 the second submission would
        complete the login because the code is still inside its +-1
        step drift window.  With C-09 the matched step is now
        ``<= last_totp_timestep`` and the second submission must
        flash "Invalid verification code" without authenticating.
        """
        with app.app_context():
            secret = _its_two_factor(seed_user["user"].id)

            # Capture a code at the current wall-clock instant.  Use
            # ``totp.at(time.time())`` rather than ``totp.now()`` so
            # the code's source-of-truth timestamp matches the one
            # the production verifier consults.  See the explanation
            # in tests/test_services/test_mfa_service.py.
            now_unix = int(time.time())
            captured_code = pyotp.TOTP(secret).at(now_unix)

            # First attempt: legitimate user logs in.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            first = client.post("/mfa/verify", data={
                "totp_code": captured_code,
            }, follow_redirects=False)
            assert first.status_code == 302
            assert "/mfa" not in first.headers.get("Location", "")
            assert "/login" not in first.headers.get("Location", "")

            # Second attempt from a fresh client (the "attacker"):
            # the captured code MUST be rejected even though the +-1
            # drift window has not elapsed.
            attacker = app.test_client()
            attacker.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            second = attacker.post("/mfa/verify", data={
                "totp_code": captured_code,
            }, follow_redirects=False)
            # Stay on /mfa/verify (re-render with flash), not 302 to
            # any logged-in destination.
            assert second.status_code == 200
            assert b"Invalid verification code" in second.data

            # Pin the DB-side proof: ``last_totp_timestep`` was
            # advanced by the first request and remained at that
            # step after the rejected second request.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.last_totp_timestep is not None
            assert mfa_config.last_totp_timestep == now_unix // 30

    def test_legitimate_sequential_logins_are_not_blocked(
        self, app, seed_user,
    ):
        """Two logins with fresh codes from two different steps both succeed.

        Strict replay prevention must not turn into a self-DoS for the
        common case: a user logs in at step S, logs out, and logs back
        in at step S+1.  The matched step advances monotonically, so
        the second verify is accepted.
        """
        with app.app_context():
            secret = _its_two_factor(seed_user["user"].id)

            # Round 1: code for step S.
            step_s_unix = int(time.time())
            code_s = pyotp.TOTP(secret).at(step_s_unix)
            c1 = app.test_client()
            c1.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            r1 = c1.post("/mfa/verify", data={
                "totp_code": code_s,
            }, follow_redirects=False)
            assert r1.status_code == 302
            assert "/mfa" not in r1.headers.get("Location", "")

            # Round 2: code for step S+1.  Generated by giving
            # pyotp.TOTP.at the next step's anchor timestamp.  The
            # production verifier reads time.time() at request time,
            # so its current_step computation may still be S; that is
            # fine because S+1 is inside the +-1 drift window.  When
            # the wall-clock has advanced to step S+1 by the time the
            # request runs, S+1 == current_step (drift 0).
            step_s_plus_one_unix = (step_s_unix // 30 + 1) * 30
            code_s1 = pyotp.TOTP(secret).at(step_s_plus_one_unix)
            assert code_s1 != code_s, (
                "Setup error: two adjacent steps coincidentally "
                "produced the same OTP -- regenerate the secret."
            )
            c2 = app.test_client()
            c2.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            r2 = c2.post("/mfa/verify", data={
                "totp_code": code_s1,
            }, follow_redirects=False)
            assert r2.status_code == 302
            assert "/mfa" not in r2.headers.get("Location", "")

            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.last_totp_timestep == step_s_plus_one_unix // 30

    def test_replay_emits_totp_replay_rejected_log(
        self, app, client, seed_user, caplog,
    ):
        """The replay-rejection branch emits ``totp_replay_rejected``.

        F-142 fix: SOC tooling needs to distinguish a replay attempt
        (live attacker with a captured code) from a benign typo
        (rotated code submitted out-of-window).  The log-event name
        and category must be exactly ``totp_replay_rejected`` /
        ``auth`` so existing search rules can pick it up.

        Includes the user_id and IP fields so the log is actionable
        without joining other sources -- audit V8.3 wants the
        attacker's address recorded with the event.
        """
        with app.app_context():
            secret = _its_two_factor(seed_user["user"].id)

            now_unix = int(time.time())
            captured_code = pyotp.TOTP(secret).at(now_unix)

            # First call -- legitimate -- consumes the step.
            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            client.post("/mfa/verify", data={
                "totp_code": captured_code,
            })

            # Second call -- replay from a fresh client.  Capture
            # logs at WARNING so the structured event surfaces.
            attacker = app.test_client()
            attacker.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            with caplog.at_level(logging.WARNING, logger="app.routes.auth"):
                attacker.post("/mfa/verify", data={
                    "totp_code": captured_code,
                })

            replay_events = [
                r for r in caplog.records
                if getattr(r, "event", None) == "totp_replay_rejected"
                and getattr(r, "category", None) == "auth"
            ]
            assert len(replay_events) == 1, (
                "Expected exactly one totp_replay_rejected event from "
                f"the replay attempt; got {len(replay_events)}.  All "
                f"records: {[(r.name, getattr(r, 'event', None)) for r in caplog.records]}"
            )
            event = replay_events[0]
            assert event.user_id == seed_user["user"].id
            # IP field is present even if the test client reports
            # the placeholder 127.0.0.1 -- audit field requirement.
            assert hasattr(event, "ip")

    def test_invalid_code_does_not_emit_replay_event(
        self, app, client, seed_user, caplog,
    ):
        """Wrong-but-not-replay codes do NOT emit ``totp_replay_rejected``.

        The structured event must be specific to replay detection.
        If every wrong code surfaced as a replay event, SOC tooling
        would drown in false positives (typos, expired codes,
        copy-paste errors) and the F-142 signal would be useless.
        """
        with app.app_context():
            _its_two_factor(seed_user["user"].id)

            client.post("/login", data={
                "email": "test@shekel.local",
                "password": "testpass",
            })
            with caplog.at_level(logging.WARNING, logger="app.routes.auth"):
                response = client.post("/mfa/verify", data={
                    "totp_code": "000000",
                })
            assert response.status_code == 200
            assert b"Invalid verification code" in response.data

            replay_events = [
                r for r in caplog.records
                if getattr(r, "event", None) == "totp_replay_rejected"
            ]
            assert replay_events == [], (
                "A wrong-but-not-replay code must not emit "
                f"totp_replay_rejected; got {len(replay_events)} events."
            )


class TestTotpReplayPreventionAtMfaDisable:
    """Replay rejection on the ``/mfa/disable`` (privileged action) route.

    The disable form is a second factor on a destructive operation:
    an attacker who has the user's password and a captured TOTP code
    can use it to weaken the account's auth factors.  C-09's replay
    prevention must apply on this surface as well, with the same
    ``totp_replay_rejected`` signal.
    """

    def test_replay_at_disable_after_login_is_rejected(
        self, app, auth_client, seed_user,
    ):
        """A code already consumed by /mfa/verify cannot be reused at /mfa/disable.

        Direct test of the cross-route replay surface: the same step
        bounds both /mfa/verify and /mfa/disable, so a code consumed
        on either surface must be rejected on the other.  Without
        this defense an attacker who captured a code mid-login could
        use the residual ~30 seconds to rapidly disable MFA before
        the user gets back to a screen that would let them notice.
        """
        with app.app_context():
            # auth_client is already logged in as the seed user, on a
            # browser it trusts.  Pre-record a step consumption, as a
            # /login + /mfa/verify with this step's code leaves it.
            now_unix = int(time.time())
            current_step = now_unix // 30
            secret = _its_two_factor(
                seed_user["user"].id, last_step=current_step,
            )
            consumed_code = pyotp.TOTP(secret).at(now_unix)

            # POST /mfa/disable with the already-consumed code.
            response = auth_client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": consumed_code,
            }, follow_redirects=False)
            assert response.status_code == 302
            # Redirect target is /mfa/disable (re-show the form),
            # NOT /settings (which is the success path).
            assert "/mfa/disable" in response.headers.get("Location", "")

            # MFA must STILL be enabled.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.is_enabled is True
            assert mfa_config.totp_secret_encrypted is not None
            # Step pointer was not advanced by the rejected attempt.
            assert mfa_config.last_totp_timestep == current_step

    def test_legitimate_disable_advances_last_totp_timestep_then_clears(
        self, app, auth_client, seed_user,
    ):
        """A successful /mfa/disable consumes a step then resets the column.

        The route does two things in one transaction: it ACCEPTS the
        TOTP code (mutating ``last_totp_timestep`` to the matched
        step), then disables MFA and clears every column including
        ``last_totp_timestep``.  Asserting on the final state pins
        that the disable path uses the verify-then-clear ordering --
        a refactor that cleared first and verified after would
        accept any code, including replays, against an empty secret.
        """
        with app.app_context():
            now_unix = int(time.time())
            secret = _its_two_factor(
                seed_user["user"].id, last_step=now_unix // 30 - 1,
            )
            fresh_code = pyotp.TOTP(secret).at(now_unix)

            response = auth_client.post("/mfa/disable", data={
                "current_password": "testpass",
                "totp_code": fresh_code,
            }, follow_redirects=False)
            assert response.status_code == 302
            # The Security page, /security since ruling plan#3435.
            assert response.headers.get("Location", "") == "/security"

            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.is_enabled is False
            assert mfa_config.totp_secret_encrypted is None
            # last_totp_timestep is reset to NULL so a re-enrollment
            # under a fresh secret does not inherit the step boundary
            # recorded against the now-cleared old secret.  Verifies
            # the route's explicit ``last_totp_timestep = None`` step.
            assert mfa_config.last_totp_timestep is None


class TestMfaConfirmSeedsLastTotpTimestep:
    """The /mfa/confirm route seeds ``last_totp_timestep`` on success.

    Without seeding, the confirming code would still be replayable on
    the user's first /mfa/verify after enrollment.  This is the C-09
    handoff: setup-code verification returns the matched step, and
    the route persists it onto the row in the same commit that
    promotes the pending secret to active.

    Setup is for an account without two-factor: the seed owner, signed
    in after a reset (``no_two_factor_client``).
    """

    def test_successful_confirm_writes_matched_step(
        self, app, no_two_factor_client, seed_user, monkeypatch,
    ):
        """After /mfa/confirm succeeds, ``last_totp_timestep`` equals the
        step the confirming code matched.

        Patches ``verify_totp_setup_code`` to return a deterministic
        sentinel step so the test can pin the column value precisely
        regardless of wall-clock at run time.  This is the cleanest
        way to assert "the column equals the value the helper
        returned" because the alternative -- using a real code --
        introduces +-1 step jitter that obscures the contract.
        """
        sentinel_step = 99_999_991
        with app.app_context():
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code",
                lambda s, c: sentinel_step,
            )

            # Visit setup to write a pending secret.
            no_two_factor_client.get("/mfa/setup")

            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "123456",
            })
            assert response.status_code == 200

            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.is_enabled is True
            assert mfa_config.last_totp_timestep == sentinel_step

    def test_invalid_confirm_does_not_seed_column(
        self, app, no_two_factor_client, seed_user, monkeypatch,
    ):
        """A failed /mfa/confirm leaves ``last_totp_timestep`` unchanged.

        Pre-condition: column starts as NULL (the reset cleared it).
        Post-condition after a wrong code: column stays NULL because
        the row is not committed in any failure branch.  Pins the
        contract that the column update is gated on success.
        """
        with app.app_context():
            monkeypatch.setattr(
                mfa_service, "verify_totp_setup_code", lambda s, c: None,
            )

            no_two_factor_client.get("/mfa/setup")
            response = no_two_factor_client.post("/mfa/confirm", data={
                "totp_code": "000000",
            }, follow_redirects=False)
            assert response.status_code == 302

            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            db.session.refresh(mfa_config)
            assert mfa_config.is_enabled in (False, None)
            assert mfa_config.last_totp_timestep is None
