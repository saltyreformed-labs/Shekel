"""
Shekel Budget App -- Auth Route Package

Split of the historical monolithic ``app/routes/auth.py`` (1350+ lines)
into a package of per-sub-domain modules (decision #5, the
``app/routes/salary/`` / ``app/routes/accounts/`` precedent).  One
``auth_bp`` blueprint is shared across every sub-module; the
declaration lives in :mod:`app.routes.auth._bp` (cycle-break).  Each
per-domain module imports the blueprint from ``_bp`` and registers its
route decorators against it.  Every URL and endpoint name is preserved
verbatim from the pre-split file, so no ``url_for`` call site,
template, or ``app/__init__.py`` import needed an edit
(``app/__init__.py`` continues to resolve ``auth_bp`` from this
package by the same name, re-exported below).

Module map:

* :mod:`app.routes.auth._bp` -- ``auth_bp`` blueprint declaration
  (leaf module; cycle-break).
* :mod:`app.routes.auth._helpers` -- the shared security constants
  (``MFA_SETUP_PENDING_TTL``, ``_MFA_PENDING_MAX_AGE``,
  ``_MFA_PENDING_KEYS``) and the cross-module private helpers: the
  MFA pending-state pair (``_clear_mfa_pending_state`` /
  ``_mfa_pending_is_fresh``), the replay-logging TOTP verifier
  (``_verify_totp_with_replay_logging``) and backup-code consumer
  (``_consume_backup_code``), the form-error flattener
  (``_first_validation_message``), and the open-redirect guard
  (``_is_safe_redirect``).
* :mod:`app.routes.auth.credentials` -- primary credentials
  (``login``, ``register_form``, ``register``, ``logout``).
* :mod:`app.routes.auth.mfa` -- the MFA lifecycle, co-located
  (``mfa_verify``, ``mfa_setup``, ``mfa_confirm``,
  ``regenerate_backup_codes``, ``mfa_disable``,
  ``mfa_disable_confirm``).
* :mod:`app.routes.auth.session_security` -- authenticated-session
  security (``change_password``, ``invalidate_sessions``, ``reauth``,
  ``dismiss_security_event``).
* :mod:`app.routes.auth.passkeys` -- signing in with a passkey
  (``passkey_sign_in_options``, ``passkey_sign_in``; plan#2538).
"""

# Re-export ``auth_bp`` from the leaf declaration module so consumers
# that import it from the package (notably ``app/__init__.py``'s
# blueprint-registration loop at factory time) resolve without an edit.
from app.routes.auth._bp import auth_bp

# Import sub-modules for the side effect of registering their route
# decorators against ``auth_bp``.  The ``noqa`` markers suppress the
# unused-import / out-of-order-import warnings that would otherwise fire
# on what is, by design, a deferred-import side-effect registration.
from app.routes.auth import credentials  # noqa: F401, E402
from app.routes.auth import mfa  # noqa: F401, E402
from app.routes.auth import passkeys  # noqa: F401, E402
from app.routes.auth import session_security  # noqa: F401, E402


__all__ = ["auth_bp"]
