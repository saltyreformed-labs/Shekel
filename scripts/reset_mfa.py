"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

The reset clears ALL stored MFA material -- the encrypted TOTP secret,
the hashed backup codes, the confirmation timestamp, the
replay-prevention step boundary, and any pending setup ciphertext --
even when ``is_enabled`` is already ``False`` but residual material
lingers on the row.  An emergency reset must leave no decryptable
secret at rest.  The field set is owned by
``mfa_service.clear_mfa_material`` / ``has_mfa_material``, shared with
the app's ``/mfa/disable`` route so the two paths cannot drift.

Like ``/mfa/disable``, the reset also bumps the user's
``session_invalidated_at``: it ends every session of the user and forgets
every trusted device of theirs (plan#2173, ruling plan#2406, "turning 2FA
off ... forgets them all").  A device is trusted only while it was trusted
at or after that column (``app.services.trusted_device_service``), and a
lost authenticator is exactly when a trust it vouched for must die: without
the bump, a browser trusted under the old authenticator would skip the
code of the one enrolled after the reset.

Usage:
    python scripts/reset_mfa.py <user_email>
    python scripts/reset_mfa.py --force <user_email>

Example:
    python scripts/reset_mfa.py admin@shekel.local
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reset_mfa(email: str | None) -> None:
    """Disable MFA and clear all stored MFA material for the user.

    Clears the encrypted TOTP secret, hashed backup codes, confirmation
    timestamp, replay-prevention step boundary, and any pending setup
    ciphertext via the ``mfa_service.clear_mfa_material`` rule shared
    with the app's ``/mfa/disable`` route, and bumps the user's
    ``session_invalidated_at`` in the same commit (the module docstring
    says why).  The clear also runs when
    ``is_enabled`` is already ``False`` but residual material remains
    (an orphaned secret from a manual DB intervention, an interrupted
    disable flow, or an abandoned ``/mfa/setup`` whose encrypted
    pending secret would otherwise sit at rest indefinitely): leaving
    an encrypted secret at rest after a reset would silently revive
    the old secret if the row were ever re-enabled, and keeps secret
    material alive past the moment the operator believes it was
    destroyed.

    Args:
        email: The email address of the user to reset.  ``None`` and
            unknown addresses take the user-not-found exit.

    Prints status messages to stdout.
    Exits with code 1 if the user is not found.
    """
    # Pylint: import-outside-toplevel -- importing anything under
    # ``app`` executes ``app.config``, which reads ``os.environ`` at
    # import time; deferring to call time keeps this module import
    # side-effect-free, so ``--help``, argparse errors, and the
    # confirmation prompt never load or validate app config.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.user import MfaConfig, User
    from app.services import mfa_service
    from app.utils.log_events import AUTH, log_event
    # pylint: enable=import-outside-toplevel

    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        print(f"Error: No user found with email '{email}'.")
        sys.exit(1)

    mfa_config = db.session.query(MfaConfig).filter_by(user_id=user.id).first()
    # "Nothing to clear" means no row at all, or a row already in the
    # fully-reset state.  Checking every clearable column (not just
    # ``is_enabled``) is deliberate: a disabled row can still carry an
    # orphaned encrypted secret or an abandoned pending-setup
    # ciphertext, and the reset must remove both.  The field set lives
    # in mfa_service so this check and the clear below cannot disagree.
    nothing_to_clear = mfa_config is None or (
        not mfa_config.is_enabled
        and not mfa_service.has_mfa_material(mfa_config)
    )
    if nothing_to_clear:
        print(f"MFA is not enabled for {email}.")
        return

    # Clear all MFA material + disable, via the rule shared with the
    # /mfa/disable route (see clear_mfa_material's docstring for the
    # per-field rationale, including the C-09 last_totp_timestep
    # contract).  In the same commit, end the user's sessions and forget
    # their trusted devices, as /mfa/disable's invalidate_other_sessions
    # does on the same process clock (the module docstring says why).
    mfa_service.clear_mfa_material(mfa_config)
    user.session_invalidated_at = datetime.now(timezone.utc)
    db.session.commit()

    # Audit trail for the reset action.
    logger = logging.getLogger(__name__)
    log_event(
        logger, logging.WARNING, "mfa_reset", AUTH,
        "MFA reset via admin script", user_email=email,
    )

    print(
        f"MFA has been disabled for {email}; their sessions are ended and "
        f"their trusted devices forgotten."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

    Returns:
        argparse.Namespace with ``email`` (str) and ``force`` (bool).
    """
    parser = argparse.ArgumentParser(
        description="Disable MFA for a user (emergency recovery)."
    )
    parser.add_argument("email", help="Email address of the user to reset.")
    parser.add_argument(
        "--force", action="store_true",
        help="Skip confirmation prompt (for scripted use).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()

    if not args.force:
        confirm = input(
            f"Reset MFA for {args.email}? This cannot be undone. [y/N] "
        )
        if confirm.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    from app import create_app

    app = create_app()
    with app.app_context():
        reset_mfa(args.email)
