"""Tests for the MFA reset CLI script.

**The seed owner is made with two-factor** (plan#3600), as every account past
setup has it, so a reset here resets the owner's own.  A case that needs its
row to hold backup codes gives it some (:func:`tests._test_helpers.store_backup_codes`).
An account with no two-factor row at all is the seed companion, whom her owner
has just made and who has not set it up.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import MfaConfig, User
from app.services import mfa_service
from scripts.reset_mfa import parse_args, reset_mfa
from tests._request_race import race_two_requests, signed_in_clients
from tests._test_helpers import store_backup_codes
from tests._two_factor_sign_in import two_factor_row_of

#: Backup codes the owner's row holds, so a reset has them to clear.
_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb"]


class TestResetMfa:
    """Tests for scripts/reset_mfa.py reset_mfa() function."""

    def test_reset_mfa_disables_for_user(self, app, db, seed_user):
        """reset_mfa() clears MFA config for the given email."""
        with app.app_context():
            store_backup_codes(db.session, seed_user["user"].id, _BACKUP_CODES)

            reset_mfa(seed_user["user"].email)

            # Reload and verify all MFA fields are cleared.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None
            # last_totp_timestep is cleared too -- a stale step
            # boundary on a re-enrollment under a new secret would
            # be a subtle source of legitimate-code rejections.  See
            # commit C-09.
            assert config.last_totp_timestep is None

    def test_reset_mfa_ends_sessions_and_forgets_devices(
        self, app, db, seed_user,
    ):
        """The reset moves ``session_invalidated_at`` to the moment it ran.

        That is what forgets every trusted device (plan#2173, ruling
        plan#2406); the one act it runs ends the user's sessions by deleting
        their sign-ins' records (ruling plan#3927,
        ``tests/test_adversarial/test_every_sign_out_everywhere.py``).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            assert db.session.get(User, user_id).session_invalidated_at is None
            before = datetime.now(timezone.utc)

            reset_mfa(seed_user["user"].email)

            after = datetime.now(timezone.utc)
            bumped = db.session.query(User.session_invalidated_at).filter_by(
                id=user_id,
            ).scalar()
            assert bumped is not None
            assert before <= bumped <= after

    def test_reset_mfa_user_not_found(self, app, db, capsys):
        """reset_mfa() prints error and exits for unknown email."""
        with app.app_context():
            with pytest.raises(SystemExit) as exc_info:
                reset_mfa("nobody@example.com")

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

    def test_reset_mfa_not_enabled(self, app, db, seed_companion, capsys):
        """reset_mfa() prints message when MFA is not enabled.

        The seed companion has no two-factor row: her owner has just made her.
        """
        with app.app_context():
            assert db.session.query(MfaConfig).filter_by(
                user_id=seed_companion["user"].id,
            ).first() is None
            reset_mfa(seed_companion["user"].email)

            captured = capsys.readouterr()
            assert "MFA is not enabled" in captured.out

    def test_reset_empty_email(self, app, db, seed_user, capsys):
        """reset_mfa('') exits with code 1 and prints error without DB changes."""
        with app.app_context():
            mfa_count_before = db.session.query(MfaConfig).count()

            with pytest.raises(SystemExit) as exc_info:
                reset_mfa("")

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

            # Verify no database state was altered.
            mfa_count_after = db.session.query(MfaConfig).count()
            assert mfa_count_after == mfa_count_before

    def test_reset_none_email(self, app, db, seed_user, capsys):
        """reset_mfa(None) exits with code 1 and prints error without DB changes."""
        with app.app_context():
            mfa_count_before = db.session.query(MfaConfig).count()

            with pytest.raises(SystemExit) as exc_info:
                reset_mfa(None)

            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "No user found" in captured.out

            # Verify no database state was altered.
            mfa_count_after = db.session.query(MfaConfig).count()
            assert mfa_count_after == mfa_count_before

    def test_reset_partial_mfa_state(self, app, db, seed_user, capsys):
        """reset_mfa() clears an orphaned TOTP secret on a disabled config.

        A row can carry is_enabled=False with leftover
        totp_secret_encrypted bytes (a manual DB intervention or an
        interrupted disable flow).  The reset must clear the orphan
        rather than returning early on the is_enabled check: a
        lingering encrypted secret outlives the moment the operator
        believes it was destroyed, and would silently revive if the
        row were ever re-enabled.  (Historically the script returned
        early here and left the orphan in place; that was a bug.)
        """
        with app.app_context():
            # The owner's row, disabled by hand with its secret left behind.
            mfa_config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .one()
            )
            assert mfa_config.totp_secret_encrypted is not None
            mfa_config.is_enabled = False
            mfa_config.backup_codes = None
            mfa_config.confirmed_at = None
            db.session.commit()

            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA has been disabled" in captured.out

            # Reload and check final state: every MFA field cleared,
            # including the previously-orphaned encrypted secret.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None
            assert config.last_totp_timestep is None

    def test_reset_clears_abandoned_pending_setup(
        self, app, db, seed_companion, capsys,
    ):
        """reset_mfa() scrubs a pending-setup ciphertext on a disabled row.

        An abandoned /mfa/setup leaves ONLY pending_secret_encrypted +
        pending_secret_expires_at behind (never enabled, nothing else
        set).  The expiry only gates promotion at /mfa/confirm -- the
        encrypted pending secret itself sits at rest indefinitely until
        the next setup overwrites it.  The reset's own contract ("no
        decryptable secret at rest") requires clearing it; historically
        the residue check ignored the pending fields and took the
        "MFA is not enabled" early exit, leaving the ciphertext behind.
        The seed companion, who has never set two-factor up, abandons hers.
        """
        with app.app_context():
            mfa_config = MfaConfig(
                user_id=seed_companion["user"].id,
                is_enabled=False,
                pending_secret_encrypted=mfa_service.encrypt_secret(
                    "ORSXG5A2ORSXG5A2"
                ),
                pending_secret_expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=15)
                ),
            )
            db.session.add(mfa_config)
            db.session.commit()

            reset_mfa(seed_companion["user"].email)

            captured = capsys.readouterr()
            assert "MFA has been disabled" in captured.out

            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_companion["user"].id)
                .first()
            )
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None
            assert config.is_enabled is False

    def test_reset_clears_pending_alongside_active_material(
        self, app, db, seed_user, capsys
    ):
        """reset_mfa() on an enabled row clears pending fields too.

        Pending material cannot coexist with an enabled row through
        normal flows, but a manual DB intervention can produce the
        combination -- the emergency reset must scrub every field
        regardless (the clear_mfa_material shared rule).
        """
        with app.app_context():
            store_backup_codes(db.session, seed_user["user"].id, _BACKUP_CODES)
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            config.pending_secret_encrypted = mfa_service.encrypt_secret(
                "ORSXG5A2ORSXG5A2"
            )
            config.pending_secret_expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=15)
            )
            db.session.commit()

            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA has been disabled" in captured.out

            db.session.refresh(config)
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None
            assert config.last_totp_timestep is None
            assert config.pending_secret_encrypted is None
            assert config.pending_secret_expires_at is None

    def test_reset_mfa_idempotent(self, app, db, seed_user, capsys):
        """Calling reset_mfa() twice does not crash on the second call."""
        with app.app_context():
            store_backup_codes(db.session, seed_user["user"].id, _BACKUP_CODES)

            # First reset disables MFA.
            reset_mfa(seed_user["user"].email)
            first_out = capsys.readouterr().out
            assert "MFA has been disabled" in first_out

            # Second reset -- MFA is already disabled.
            reset_mfa(seed_user["user"].email)
            second_out = capsys.readouterr().out
            assert "MFA is not enabled" in second_out

            # Final DB state: MFA fully cleared.
            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
                .first()
            )
            assert config.is_enabled is False
            assert config.totp_secret_encrypted is None
            assert config.backup_codes is None
            assert config.confirmed_at is None


def _run_the_reset(app, email):
    """Run the script's reset for *email* in an app context of its own, as the script runs it.

    Its own session and connection, so the race below meets it as production
    does: a process beside the app's, holding no request.
    """
    with app.app_context():
        reset_mfa(email)


class TestTheResetWaitsForASaveInFlight:
    """The reset takes every account's write lock before it reads anything (ruling plan#3823)."""

    def test_a_reset_during_regenerate_lands_after_it(self, app, seed_user):
        """A reset started while Regenerate backup codes saves waits for it, then clears its codes.

        Without the lock the reset committed between the login gate's read of
        the two-factor row and Regenerate's write, and Regenerate wrote ten
        new backup codes back onto the row the reset had just cleared
        (measured 2026-11-13, plan#3775).  With it the reset waits on the
        owner's lock the save holds, and runs once the save has committed,
        so the row ends reset, with none of the save's codes.
        """
        with app.app_context():
            # Read here: each side of the race runs on a thread with no app
            # context, where an expired instance cannot refresh itself.
            owner_id, email = seed_user["user"].id, seed_user["user"].email
            (browser,) = signed_in_clients(app, count=1)

            save, reset = race_two_requests(
                app, owner_id,
                lambda: browser.post("/mfa/regenerate-backup-codes"),
                lambda: _run_the_reset(app, email),
            )

            assert reset.waited, (
                "the reset did not wait on the owner's write lock the save held, "
                "so it could commit partway through the save"
            )
            assert save.response.status_code == 200
            assert b"Save Your Backup Codes" in save.response.data
            row = two_factor_row_of(owner_id)
            assert row.is_enabled is False
            assert row.totp_secret_encrypted is None
            assert row.backup_codes is None, (
                f"the row the reset cleared holds {len(row.backup_codes)} backup "
                f"codes: the save wrote them after the reset"
            )


class TestParseArgs:
    """Tests for parse_args CLI argument validation."""

    def test_parse_args_accepts_email(self):
        """parse_args accepts a positional email argument."""
        args = parse_args(["admin@shekel.local"])
        assert args.email == "admin@shekel.local"
        assert args.force is False

    def test_parse_args_force_flag(self):
        """parse_args accepts --force to skip confirmation."""
        args = parse_args(["--force", "admin@shekel.local"])
        assert args.force is True

    def test_parse_args_missing_email_exits(self):
        """parse_args exits when no email is provided."""
        with pytest.raises(SystemExit):
            parse_args([])


class TestResetMfaAuditLog:
    """Tests for audit logging on MFA reset."""

    def test_reset_mfa_logs_audit_event(self, app, db, seed_user, caplog):
        """reset_mfa() emits a WARNING-level audit log entry on success."""
        with app.app_context():
            with caplog.at_level(logging.WARNING, logger="scripts.reset_mfa"):
                reset_mfa(seed_user["user"].email)

            assert any("mfa_reset" in r.message or
                       getattr(r, "event", None) == "mfa_reset"
                       for r in caplog.records)
