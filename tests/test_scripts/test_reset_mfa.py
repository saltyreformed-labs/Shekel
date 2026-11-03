"""Tests for the MFA reset CLI script."""

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models.user import MfaConfig, User
from app.services import mfa_service
from scripts.reset_mfa import parse_args, reset_mfa


class TestResetMfa:
    """Tests for scripts/reset_mfa.py reset_mfa() function."""

    def _enable_mfa(self, user_id):
        """Helper to enable MFA for a user with a known secret.

        Args:
            user_id: The user's primary key.
        """
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
            backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa", "bbbbbbbb"]),
        )
        db.session.add(mfa_config)
        db.session.commit()

    def test_reset_mfa_disables_for_user(self, app, db, seed_user):
        """reset_mfa() clears MFA config for the given email."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

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
        """The reset bumps ``session_invalidated_at`` to the moment it ran.

        That bump is what ends the user's sessions and forgets every trusted
        device (plan#2173, ruling plan#2406), as ``/mfa/disable``'s does.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            self._enable_mfa(user_id)
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

    def test_reset_mfa_not_enabled(self, app, db, seed_user, capsys):
        """reset_mfa() prints message when MFA is not enabled."""
        with app.app_context():
            # No MFA enabled for seed_user -- just call reset_mfa.
            reset_mfa(seed_user["user"].email)

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
            # Create an MfaConfig with is_enabled=False but leftover secret.
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
                is_enabled=False,
                totp_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
                backup_codes=None,
                confirmed_at=None,
            )
            db.session.add(mfa_config)
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

    def test_reset_clears_abandoned_pending_setup(self, app, db, seed_user, capsys):
        """reset_mfa() scrubs a pending-setup ciphertext on a disabled row.

        An abandoned /mfa/setup leaves ONLY pending_secret_encrypted +
        pending_secret_expires_at behind (never enabled, nothing else
        set).  The expiry only gates promotion at /mfa/confirm -- the
        encrypted pending secret itself sits at rest indefinitely until
        the next setup overwrites it.  The reset's own contract ("no
        decryptable secret at rest") requires clearing it; historically
        the residue check ignored the pending fields and took the
        "MFA is not enabled" early exit, leaving the ciphertext behind.
        """
        with app.app_context():
            mfa_config = MfaConfig(
                user_id=seed_user["user"].id,
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

            reset_mfa(seed_user["user"].email)

            captured = capsys.readouterr()
            assert "MFA has been disabled" in captured.out

            config = (
                db.session.query(MfaConfig)
                .filter_by(user_id=seed_user["user"].id)
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
            self._enable_mfa(seed_user["user"].id)
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
            self._enable_mfa(seed_user["user"].id)

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

    def _enable_mfa(self, user_id):
        """Helper to enable MFA for a user."""
        mfa_config = MfaConfig(
            user_id=user_id,
            is_enabled=True,
            totp_secret_encrypted=mfa_service.encrypt_secret("JBSWY3DPEHPK3PXP"),
            backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa", "bbbbbbbb"]),
        )
        db.session.add(mfa_config)
        db.session.commit()

    def test_reset_mfa_logs_audit_event(self, app, db, seed_user, caplog):
        """reset_mfa() emits a WARNING-level audit log entry on success."""
        with app.app_context():
            self._enable_mfa(seed_user["user"].id)

            with caplog.at_level(logging.WARNING, logger="scripts.reset_mfa"):
                reset_mfa(seed_user["user"].email)

            assert any("mfa_reset" in r.message or
                       getattr(r, "event", None) == "mfa_reset"
                       for r in caplog.records)
