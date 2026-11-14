"""Shekel Budget App -- Adversarial Tests for Session Lifetime,
Idle Timeout, and Step-Up Re-Auth (C-10).

End-to-end coverage for the three findings closed by commit C-10
of the 2026-04-15 security remediation plan:

  * F-006 (CWE-613): no idle-timeout check on the session.  Closed
    by ``IDLE_TIMEOUT_MINUTES`` in config plus the
    ``_session_last_activity_at`` check in ``app.load_user`` and
    the ``before_request`` activity-refresh hook in
    ``app/__init__.py``.

  * F-035 (CWE-613): ``PERMANENT_SESSION_LIFETIME`` defaulted to
    Flask's 31-day fallback.  Closed by an explicit 12-hour default
    in ``BaseConfig`` (env-overridable via ``SESSION_LIFETIME_HOURS``),
    which Flask applies to every session cookie, permanent or not: the
    activity hook re-signs the cookie on each signed-in request, so it
    was a second 12-hour idle rule beside F-006's.  Since ruling
    plan#3926 it is ``KNOWN_LIFETIME``, the thirty days a session lasts
    after its sign-in (ruling plan#3069), so it never cuts in first and
    the idle timeout is the one idle rule (graded in
    ``test_idle_timeout.py``, ``TestTheOneIdleRule``).

  * F-045 (CWE-306): no step-up re-auth for high-value operations.
    Closed by the ``fresh_login_required`` decorator in
    ``app/utils/auth_helpers.py``, the ``/reauth`` route in
    ``app/routes/auth/session_security.py``, and the high-value routes the decorator
    is applied to.  The decorated set was later narrowed to the
    account-control and credential operations -- account hard-delete
    and companion create / edit / deactivate / reactivate -- after the
    owner judged step-up on the reversible data edits (anchor balance,
    account-edit form, state tax / FICA config, recurring
    transaction- and transfer-template hard-delete) to be friction
    without a matching benefit for this single-owner, low-sensitivity
    app.  Those routes remain owner-only via ``@require_owner``.

The tests in this package run against the full Flask app via
``test_client`` so that load_user, the before_request hook, the
decorator, the /reauth route, and the lifecycle stamping all
exercise together.  A regression in any one piece would break a
test here even if the unit tests pass individually.

Why adversarial, not unit:  the C-10 contract is a composition of
five cooperating pieces (config, loader, hook, decorator, route).
The unit tests in ``tests/test_utils/`` and ``tests/test_routes/
test_auth/`` cover each piece in isolation; this package catches the
"each piece works alone but they are wired together wrong" class of
bug.
"""
