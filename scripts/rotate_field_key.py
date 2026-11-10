"""
Shekel Budget App -- Field Encryption Key Rotation

One-shot operations utility that re-wraps every ciphertext column the
app stores under the current primary key (``FIELD_ENCRYPTION_KEY``):
``auth.mfa_configs.totp_secret_encrypted`` and, since plan step
``bank_import:X-f6b-2`` (ruling R-BI12), ``budget.bank_feeds
.access_url_encrypted`` -- the registry is ``_ciphertext_columns``.
Intended to be run AFTER an operator has:

    1. Generated a new primary key
       (``Fernet.generate_key().decode()``).
    2. Moved the previous primary key to ``FIELD_ENCRYPTION_KEY_OLD``.
    3. Set the new key as ``FIELD_ENCRYPTION_KEY``.
    4. Restarted the application container so the new key list takes
       effect at the runtime layer.

The application is fully usable between steps 4 and 5 because
``field_encryption.get_encryption_key()`` returns a ``MultiFernet`` that
decrypts under either the new primary or the retired key.  This
script's role is to migrate the at-rest ciphertexts forward so the
operator can safely remove the retired key from
``FIELD_ENCRYPTION_KEY_OLD`` at the next deploy.

Usage:
    python scripts/rotate_field_key.py --confirm

The ``--confirm`` flag is mandatory: running without it prints a
short usage hint and exits with code 1, never touching the database.

Idempotency:
    The script is safe to run repeatedly.  Each row is first probed
    with the primary key alone; rows that already decrypt under the
    primary are counted as ``already_current`` and left untouched.
    Only rows that fail the primary probe are re-encrypted.

Exit codes:
    0  Successful rotation, every row accounted for.
    1  ``--confirm`` flag was not supplied.
    2  Successful run but at least one row could not be decrypted
       under any configured key.  Operator action required: do NOT
       remove ``FIELD_ENCRYPTION_KEY_OLD`` until the row is recovered,
       the user re-enrolls MFA, or the owner reconnects the feed --
       the ERROR line names the table and the row.

Test entry point:
    ``execute_rotation(db.session)`` returns a
    ``(rotated, already_current, skipped)`` triple.  Tests call this
    directly with the test session and never create a separate Flask
    app.
"""

import argparse
import logging
import os
import sys

from cryptography.fernet import InvalidToken, MultiFernet

# Ensure the project root is on sys.path so 'app' and 'scripts' are
# importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- this import must follow the sys.path
# bootstrap above; 'scripts' is only importable once the project root
# is on the path.
from scripts._script_lib import (  # pylint: disable=wrong-import-position
    confirm_gate,
    parse_confirm_args,
    run_in_app_context,
)


# Per-row outcome sentinels.  Returned by ``_rotate_one`` and
# tallied by ``execute_rotation``.  Module-level constants rather than
# magic strings so a typo at the call site fails the linter.
_OUTCOME_ALREADY_CURRENT = "already_current"
_OUTCOME_ROTATED = "rotated"
_OUTCOME_SKIPPED = "skipped"


def _ciphertext_columns() -> tuple[tuple[type, str], ...]:
    """The ciphertext columns the rotation re-wraps: ``(model, attribute)``.

    ONE registry, so a column the key protects and this script does not
    visit is a diff against this tuple rather than a silence.  The
    imports are deferred for the reason ``execute_rotation`` states.

    Not listed, deliberately: ``auth.mfa_configs.pending_secret_encrypted``,
    the in-flight enrolment secret.  It lives for ``MFA_SETUP_PENDING_TTL``
    and ``/mfa/confirm`` clears one no configured key can read and sends
    the user back to setup, so a key pruned between setup and confirm
    costs a restart of a minutes-long enrolment rather than a locked-out
    user.  Adding it is a change to the MFA rotation's behaviour and its
    counts, outside ``bank_import:X-f6b-2``'s scope; reported in that
    step's handoff (``HANDOFF-X-f6b.md`` s.0.2) for the coordinator's
    ledger batch.

    Returns:
        The ``(model, attribute name)`` pairs, MFA first.
    """
    # Pylint: import-outside-toplevel -- importing anything under ``app``
    # executes ``app.config``, which reads ``os.environ`` at import time;
    # deferring to call time keeps this module import side-effect-free
    # (the same reason ``execute_rotation`` gives for its own imports).
    # pylint: disable=import-outside-toplevel
    from app.models.bank_feed import BankFeed
    from app.models.user import MfaConfig
    # pylint: enable=import-outside-toplevel
    return (
        (MfaConfig, "totp_secret_encrypted"),
        (BankFeed, "access_url_encrypted"),
    )


def _rotate_one(row, column: str, primary_only, multi, logger) -> str:
    """Rotate one ciphertext column of one row in place.

    Encapsulates the per-row classification logic so
    ``execute_rotation`` only has to drive the loop and aggregate
    counts.  Splitting the logic also lets unit tests target a single
    row without seeding a full session.

    The row is classified as one of:

      - ``_OUTCOME_ALREADY_CURRENT`` -- the ciphertext already decrypts
        under the primary key alone.  No mutation.  This is the
        idempotency guard.
      - ``_OUTCOME_ROTATED`` -- the ciphertext required a retired key
        for decryption; ``MultiFernet.rotate`` re-wrapped it under the
        primary.  The column is mutated in place.
      - ``_OUTCOME_SKIPPED`` -- no configured key could decrypt the
        ciphertext.  Logged at ERROR level naming the table, the column
        and the row id; no mutation.

    Args:
        row: The model instance (an ``MfaConfig``, a ``BankFeed``) whose
            ciphertext will be classified and optionally re-wrapped.
        column: The attribute holding the ciphertext, from
            ``_ciphertext_columns``.
        primary_only: A bare ``Fernet`` initialised on the primary
            key.  Used as the idempotency probe.
        multi: The ``MultiFernet`` initialised on primary plus any
            retired keys.  Used to actually rotate.
        logger: ``logging.Logger`` instance used to surface the row
            id of any skipped row.

    Returns:
        One of the ``_OUTCOME_*`` sentinel strings.
    """
    ciphertext = getattr(row, column)

    # Idempotency probe: does the ciphertext already decrypt under
    # the primary alone?  If yes, no rotation is needed; skip.
    try:
        primary_only.decrypt(ciphertext)
        return _OUTCOME_ALREADY_CURRENT
    except InvalidToken:
        # Fall through to the rotation path below.  Both real
        # decrypt failures (wrong key) and malformed ciphertexts
        # raise InvalidToken from Fernet.decrypt.
        pass

    # The row is encrypted under a non-primary key.  MultiFernet
    # tries primary first and then each retired key; on success it
    # re-encrypts under the primary with a fresh IV and timestamp.
    try:
        setattr(row, column, multi.rotate(ciphertext))
    except InvalidToken:
        # No configured key matches.  Log the table, column and row id
        # (NEVER the ciphertext or any plaintext) and report the skip
        # so the remaining rows still get migrated.  The non-zero exit
        # code on the CLI side surfaces this to the operator, and the
        # table tells them which remedy applies (reset MFA, or
        # reconnect the feed).
        logger.error(
            "%s.%s id=%d cannot be decrypted under any "
            "configured key. Row left untouched. Investigate "
            "before removing FIELD_ENCRYPTION_KEY_OLD.",
            row.__table__.fullname, column, row.id,
        )
        return _OUTCOME_SKIPPED

    return _OUTCOME_ROTATED


def execute_rotation(db_session) -> tuple[int, int, int]:
    """Re-encrypt every ciphertext column under the current primary key.

    The core data operation, separated from app creation so tests can
    call it directly with the test database session.  For each
    ``(model, column)`` in ``_ciphertext_columns`` the function fetches
    every row whose column is non-NULL and dispatches each to
    ``_rotate_one``, which classifies it as already-current, rotated,
    or skipped.  The classification rules and the choice to use
    ``MultiFernet.rotate`` (rather than a manual decrypt+encrypt) are
    documented in ``_rotate_one``.

    Args:
        db_session (sqlalchemy.orm.Session): A SQLAlchemy session
            bound to a database that already has the
            ``auth.mfa_configs`` and ``budget.bank_feeds`` tables.

    Returns:
        A ``(rotated, already_current, skipped)`` triple of row counts,
        ONE triple over every column: each value counts rows by outcome
        across ``auth.mfa_configs`` and ``budget.bank_feeds`` together.
        The three values always sum to the number of rows with a
        non-NULL ciphertext column at the time the queries were issued.

    Raises:
        RuntimeError: If ``FIELD_ENCRYPTION_KEY`` is unset or empty.
            Without a primary key the rotation has no target, so we
            fail fast rather than silently leaving the table in its
            previous state.
        ValueError: If any configured key cannot be parsed as a Fernet
            key -- the same refusal ``field_encryption.get_encryption_key``
            raises, since both build the list the same way.

    Side effects:
        - Mutates the ciphertext column on rows that need rotation.
        - Commits the transaction once at the end (single commit so
          either the whole rotation succeeds or the whole rotation
          rolls back on a database error).
        - Emits a structured log event ``field_key_rotated`` at
          ``WARNING`` level with the three counts.
    """
    # Pylint: import-outside-toplevel -- importing anything under
    # ``app`` executes ``app.config``, which reads ``os.environ`` at
    # import time; deferring to call time keeps this module import
    # side-effect-free (``--help`` and a missing ``--confirm`` never
    # load app config) and preserves run_in_app_context's pre-import
    # DATABASE_URL override contract.
    # pylint: disable=import-outside-toplevel
    from app.utils.field_encryption import build_fernet_list
    from app.utils.log_events import AUTH, log_event
    # pylint: enable=import-outside-toplevel

    logger = logging.getLogger(__name__)

    # ONE parse of the environment (``build_fernet_list``, the list
    # ``field_encryption.get_encryption_key`` wraps): its index 0 is the
    # primary alone, which is the idempotency probe, and the whole
    # list is the rotating cipher.  A first version re-read the env
    # var here, a second spelling of the primary that could disagree
    # with the cipher's.
    fernets = build_fernet_list()
    primary_only = fernets[0]
    multi = MultiFernet(fernets)

    counts = {
        _OUTCOME_ROTATED: 0,
        _OUTCOME_ALREADY_CURRENT: 0,
        _OUTCOME_SKIPPED: 0,
    }
    for model, column in _ciphertext_columns():
        rows = (
            db_session.query(model)
            .filter(getattr(model, column).isnot(None))
            .all()
        )
        for row in rows:
            outcome = _rotate_one(row, column, primary_only, multi, logger)
            counts[outcome] += 1

    db_session.commit()

    log_event(
        logger,
        logging.WARNING,
        "field_key_rotated",
        AUTH,
        "Field encryption key rotation completed.",
        rotated=counts[_OUTCOME_ROTATED],
        already_current=counts[_OUTCOME_ALREADY_CURRENT],
        skipped=counts[_OUTCOME_SKIPPED],
    )
    return (
        counts[_OUTCOME_ROTATED],
        counts[_OUTCOME_ALREADY_CURRENT],
        counts[_OUTCOME_SKIPPED],
    )


def run_rotation() -> tuple[int, int, int]:
    """Create the Flask app and execute the rotation.

    Convenience wrapper for CLI use.  Tests should call
    ``execute_rotation()`` directly with the test db session.

    Returns:
        The ``(rotated, already_current, skipped)`` triple from
        ``execute_rotation``.
    """
    return run_in_app_context(execute_rotation)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

    Returns:
        argparse.Namespace with ``confirm`` (bool).
    """
    return parse_confirm_args(
        argv,
        description=(
            "Re-wrap every ciphertext column (auth.mfa_configs, "
            "budget.bank_feeds) under the "
            "current FIELD_ENCRYPTION_KEY primary key.  Run during a "
            "key rotation, after the new key has been promoted to "
            "FIELD_ENCRYPTION_KEY and the previous key has been moved "
            "to FIELD_ENCRYPTION_KEY_OLD.  See "
            "docs/runbook_secrets.md."
        ),
        acknowledgment=(
            "Acknowledge that the script will mutate every MFA "
            "configuration row and every bank feed row."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` when
            ``None``).

    Returns:
        Process exit code:

          - ``0`` -- rotation completed and every row was decryptable.
          - ``1`` -- ``--confirm`` was not supplied; database untouched.
          - ``2`` -- rotation completed but at least one row was
            skipped because no configured key could decrypt it.  The
            operator must reconcile the row before pruning
            ``FIELD_ENCRYPTION_KEY_OLD``.
    """
    refusal = confirm_gate(parse_args(argv), "rotate_field_key.py")
    if refusal is not None:
        return refusal
    rotated, already_current, skipped = run_rotation()
    print(
        f"Rotated {rotated}; already current {already_current}; "
        f"skipped {skipped}."
    )
    if skipped > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
