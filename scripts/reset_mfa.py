"""
Shekel Budget App -- MFA Reset Script

Emergency script to disable MFA for a user when backup codes are
exhausted and the TOTP device is lost.  Requires direct database access.

The reset clears ALL stored MFA material -- the encrypted TOTP secret,
the hashed backup codes, the confirmation timestamp, the
replay-prevention step boundary, and any pending setup ciphertext --
even when ``is_enabled`` is already ``False`` but residual material
lingers on the row.  An emergency reset must leave no decryptable
secret at rest.

The reset also bumps the user's ``session_invalidated_at``: it ends every
session of the user and forgets every trusted device of theirs (plan#2173,
ruling plan#2406, "turning 2FA off ... forgets them all").  No account can
turn two-factor off in the app (ruling plan#3604), so this, and for a
companion the owner's Reset, is how an account's two-factor is cleared.
Every account must have two-factor (plan#3452), so the user's next sign-in
lands on its setup, and the login gate holds them there until they enrol an
authenticator again.

**The reset is ONE act, ``mfa_service.reset_two_factor``** (ruling
plan#3441), which the owner's "Reset two-factor" for a companion on
Settings > Companions runs too: its docstring says why each part is there.
This script takes every account's write lock, finds the user, runs it,
commits, logs and reports.

**It takes the lock a signed-in save takes, every account's, before it
reads anything** (ruling plan#3823), as the deploy reconciles do
(``app.services.user_write_lock.lock_every_user_writes``).  So it waits for
any save in flight and no save overlaps it: the login gate's answer to "is
two-factor on?" holds for the whole of each save, and the doors behind the
gate act on the row it read (ruling plan#3826).

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

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def reset_mfa(email: str | None) -> None:
    """Disable MFA and clear all stored MFA material for the user.

    Under every account's write lock, taken before anything is read (ruling
    plan#3823) and held until its transaction ends, runs the one reset act,
    ``mfa_service.reset_two_factor``: it clears
    the encrypted TOTP secret, hashed backup codes, confirmation
    timestamp, replay-prevention step boundary, and any pending setup
    ciphertext, and bumps the user's ``session_invalidated_at``, which
    this commits together.  The clear also runs when
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
    from app.models.user import User
    from app.services import mfa_service
    from app.services.user_write_lock import lock_every_user_writes
    from app.utils.log_events import AUTH, EVT_MFA_RESET, log_event
    # pylint: enable=import-outside-toplevel

    # Every account's write lock first, before anything is read (ruling
    # plan#3823): a signed-in save holds its owner's from its transaction's
    # start, and the login gate reads the two-factor row under it, so this
    # waits for any save in flight and none starts until this transaction ends.
    # Unlocked, a reset landing between the gate's read and Regenerate's
    # write left ten new backup codes on the row it had cleared (plan#3775).
    # Every account's rather than the one's: no key need be read unlocked to
    # find it, as a companion's is her owner's.
    lock_every_user_writes()
    user = db.session.query(User).filter_by(email=email).first()
    if not user:
        print(f"Error: No user found with email '{email}'.")
        sys.exit(1)

    # "Nothing to clear" (no row, or one already fully reset) writes
    # nothing, and its sessions are left alone.
    if not mfa_service.reset_two_factor(user):
        print(f"MFA is not enabled for {email}.")
        return
    db.session.commit()

    # Audit trail for the reset action.
    logger = logging.getLogger(__name__)
    log_event(
        logger, logging.WARNING, EVT_MFA_RESET, AUTH,
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
