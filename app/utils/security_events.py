"""Shekel Budget App -- Security-event "was this you?" notifications.

Audit reference: F-091 (Low) / C-16 of the 2026-04-15 security
remediation plan.  C-16 also folds in F-114 (seed-script PII redaction)
and F-160 (log scrubber); see ``scripts/seed_user.py`` and
``app/utils/logging_config.py`` for those (and ``scripts/seed_tax_brackets.py``
until plan step salary:X-at-1 deleted it).

What this module does
---------------------

When the user (or someone holding their session) performs a security-
relevant change -- password rotation, MFA enrolment / disable, backup-
code regeneration -- we stamp the moment and the kind of change on the
``auth.users`` row.  The next time the user loads any authenticated
page, the base template renders a dismissible "was this you?" banner
explaining what changed and offering a link to the security settings
section in case it was not.

The persisted columns are:

  * ``users.last_security_event_at`` -- timezone-aware datetime of the
    most recent security-relevant change.  ``NULL`` for new accounts
    that have never had one.
  * ``users.last_security_event_kind`` -- short machine-readable code
    naming the change (one of :class:`SecurityEventKind`).  ``NULL``
    iff ``last_security_event_at`` is also ``NULL``.
  * ``users.last_security_event_acknowledged_at`` -- timezone-aware
    datetime of the user's most recent banner dismissal.  ``NULL``
    until the user dismisses one.  The banner is visible iff
    ``last_security_event_at`` is set AND
    (``last_security_event_acknowledged_at`` is NULL OR
    ``acknowledged_at < event_at``).

Storing acknowledgement on the row (not in ``flask.session``) is a
deliberate robustness choice: a user who logs in on a new device or
clears cookies still sees the banner if a recent change has not been
acknowledged anywhere, AND an attacker who triggers a change cannot
hide the banner from the legitimate user just by dismissing it on
their own session.

What this module does NOT do
----------------------------

  * It does NOT itself emit ``log_event`` calls -- the route handlers
    that motivate the security event already log via
    ``EVT_PASSWORD_CHANGED`` / ``EVT_MFA_ENABLED`` / ``EVT_MFA_DISABLED``
    / ``EVT_BACKUP_CODES_REGENERATED``.  Adding a second log line here
    would double-count the same event in dashboards.
  * It does NOT commit the SQLAlchemy session.  The caller batches
    the security-event stamp with the credential change so a failure
    in either rolls both back atomically.  See the docstring on
    :func:`record_security_event` for the exact contract.
  * It does NOT send an email.  C-16 / F-091 explicitly defers email
    notifications -- the in-app banner is the MVP path.

The kind enum
-------------

:class:`SecurityEventKind` is a ``str``-valued ``enum.Enum`` so kind
values can travel through SQLAlchemy as plain ``VARCHAR`` while still
being comparable with ``is`` in Python.  The full set of allowed
values is mirrored at the database layer by
``ck_users_security_event_kind`` (see the migration), so a future
typo in this enum cannot persist a kind that the banner template
cannot render.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from app.models.user import User


class SecurityEventKind(str, enum.Enum):
    """Allowed values for ``users.last_security_event_kind``.

    ``str``-valued so SQLAlchemy can serialise the member directly
    via ``member.value`` without a custom converter, AND so the
    Jinja banner template can compare ``user.last_security_event_kind
    == SecurityEventKind.PASSWORD_CHANGED.value`` without importing
    the enum.

    The backing database CHECK constraint enumerates these same six
    values; see the migration that last rebuilt it.  Adding a kind
    requires:

      1. Adding the member here.
      2. Adding the value to ``KIND_DISPLAY`` below.
      3. Updating the DB CHECK constraint via a new migration.
      4. Wiring the route handler that motivates the new kind.
    """

    PASSWORD_CHANGED = "password_changed"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    BACKUP_CODES_REGENERATED = "backup_codes_regenerated"
    PASSKEY_ADDED = "passkey_added"
    PASSKEY_REMOVED = "passkey_removed"


# Per-kind display copy rendered by ``_security_event_banner.html``.
# Keeping the strings here (not in the template) keeps the security-
# critical wording in version-controlled Python so a careless template
# edit cannot silently soften the alert.  Two fields per kind:
#
#   * ``title``  -- short heading used as the banner's emphasized
#                   text.  Renders inside a ``<strong>``.
#   * ``detail`` -- one-sentence body copy, ending in a period.  No
#                   HTML; the template handles emphasis.
#
# The "was this you?" phrasing is intentional.  It frames the banner
# as a verification prompt rather than a status notification, which
# nudges the user to act if the change was unauthorised.  The link to
# the security settings page is added by the template, not embedded
# in these strings, so the security copy is plain text and trivially
# auditable for HTML-injection (it has none).
KIND_DISPLAY: dict[str, dict[str, str]] = {
    SecurityEventKind.PASSWORD_CHANGED.value: {
        "title": "Your password was changed.",
        "detail": (
            "If this was not you, change your password immediately and "
            "review your active sessions."
        ),
    },
    SecurityEventKind.MFA_ENABLED.value: {
        "title": "Two-factor authentication was enabled.",
        "detail": (
            "If you did not set this up, sign in and remove the "
            "authenticator before it locks you out."
        ),
    },
    SecurityEventKind.MFA_DISABLED.value: {
        "title": "Two-factor authentication was disabled.",
        "detail": (
            "If this was not you, re-enable two-factor authentication "
            "immediately and change your password."
        ),
    },
    SecurityEventKind.BACKUP_CODES_REGENERATED.value: {
        "title": "Your backup codes were regenerated.",
        "detail": (
            "Any previously printed codes no longer work.  If this was "
            "not you, change your password and disable MFA."
        ),
    },
    # A passkey signs in on its own (ruling plan#3465), so one added by
    # someone else is a way into the account: the copy says to remove it.
    SecurityEventKind.PASSKEY_ADDED.value: {
        "title": "A passkey was added to your account.",
        "detail": (
            "If this was not you, remove it on your Security page and "
            "review your active sessions."
        ),
    },
    # Whoever removed it was signed in to the account, and the password and
    # code still sign it in beside any passkey (ruling plan#3541), so the
    # copy points at the password.
    SecurityEventKind.PASSKEY_REMOVED.value: {
        "title": "A passkey was removed from your account.",
        "detail": (
            "If this was not you, change your password and review your "
            "active sessions."
        ),
    },
}


def record_security_event(
    user: User,
    kind: SecurityEventKind,
    *,
    now: datetime | None = None,
) -> None:
    """Stamp a security event onto the user row.

    Sets ``last_security_event_at`` to ``now`` (defaulting to the
    current UTC time) and ``last_security_event_kind`` to
    ``kind.value``.  Does NOT commit -- the caller is expected to
    batch this with the credential change that motivated it so a
    rollback of one rolls back the other.

    Because the helper writes the kind enum's ``.value`` directly
    into a ``VARCHAR``, the database CHECK constraint
    ``ck_users_security_event_kind`` is the load-bearing safety net:
    a programming-error attempt to call this with an unrecognised
    kind would still fail at commit time when the CHECK rejects the
    value.  The Python-side ``isinstance`` guard below upgrades that
    failure mode from "IntegrityError 500" to "TypeError at the
    call site", which surfaces in tests and code review.

    Args:
        user: The persistent ``User`` ORM instance to stamp.  Must
            be the user whose credential just changed -- passing a
            different user would attribute the event to the wrong
            account.
        kind: One of :class:`SecurityEventKind`.  Must be the enum
            member, not a bare string, so a typo at the call site
            fails at function-resolution time rather than at commit.
        now: Optional override for the timestamp.  Defaults to
            ``datetime.now(timezone.utc)``.  Tests pass an explicit
            value to make the banner-visibility comparison
            deterministic.

    Raises:
        TypeError: ``kind`` is not a :class:`SecurityEventKind`.
        ValueError: ``user`` is None.
    """
    if user is None:
        raise ValueError(
            "record_security_event requires a User instance; got None"
        )
    if not isinstance(kind, SecurityEventKind):
        raise TypeError(
            "record_security_event requires a SecurityEventKind member; "
            f"got {type(kind).__name__}({kind!r})"
        )
    user.last_security_event_at = now or datetime.now(timezone.utc)
    user.last_security_event_kind = kind.value


def acknowledge_security_event(
    user: User,
    *,
    now: datetime | None = None,
) -> None:
    """Record that the user dismissed the banner for the latest event.

    Sets ``last_security_event_acknowledged_at`` to ``now``.  Does
    NOT clear ``last_security_event_at`` / ``last_security_event_kind``
    -- those are kept for forensic reference and for the banner
    visibility check (which compares the two timestamps).

    Idempotent: calling twice writes the second timestamp on top of
    the first; the banner stays hidden either way.

    Does NOT commit; the caller is the dismiss route handler, which
    commits its own write.

    Args:
        user: The persistent ``User`` ORM instance to stamp.  Must
            be the authenticated user issuing the dismiss request --
            the route handler is responsible for that check.
        now: Optional override for the acknowledgement timestamp.
            Defaults to ``datetime.now(timezone.utc)``.

    Raises:
        ValueError: ``user`` is None.
    """
    if user is None:
        raise ValueError(
            "acknowledge_security_event requires a User instance; got None"
        )
    user.last_security_event_acknowledged_at = (
        now or datetime.now(timezone.utc)
    )


def banner_visible_for(user: User) -> bool:
    """Return True iff the security-event banner should render for ``user``.

    Visible iff:

      * ``last_security_event_at`` is set (an event has happened), AND
      * ``last_security_event_acknowledged_at`` is unset OR is
        strictly older than the event timestamp.

    The strict less-than comparison (not less-than-or-equal) means a
    second event recorded at the exact same microsecond as a prior
    acknowledgement still re-shows the banner.  That edge case is
    impossible in practice because each event takes at least one
    bcrypt round to produce, but documenting the comparison
    direction here keeps the invariant testable.

    Args:
        user: The authenticated ``User`` whose banner state to check.
            Anonymous / unauthenticated visitors must be filtered out
            before this is called -- the helper does no auth check.

    Returns:
        bool: True when the banner should render; False otherwise.
    """
    event_at = user.last_security_event_at
    if event_at is None:
        return False
    acknowledged_at = user.last_security_event_acknowledged_at
    if acknowledged_at is None:
        return True
    return acknowledged_at < event_at
