"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_adversarial/test_step_up.py``, moved verbatim by ``tools/split``.
"""

from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service


_KNOWN_TOTP_SECRET = "JBSWY3DPEHPK3PXP"


_KNOWN_BACKUP_CODES = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


# Env vars that BaseConfig consults at class-definition time for the
# three C-10 defaults (the fourth, the remember-me cookie's lifetime, went
# with "Remember me" at plan#2173).  The TestPermanentSessionLifetime suite clears
# every entry here and reloads ``app.config`` so the assertions
# measure the BUILT-IN defaults rather than whatever the developer
# happens to have in their local .env.
_C10_ENV_VARS = (
    "SESSION_LIFETIME_HOURS",
    "IDLE_TIMEOUT_MINUTES",
    "FRESH_LOGIN_MAX_AGE_MINUTES",
)


def _reload_config_with_clean_env(monkeypatch):
    """Stub ``load_dotenv`` and clear C-10 env vars, then reload config.

    Returns the freshly-reloaded ``app.config`` module so the caller
    can read ``BaseConfig.<attr>`` values that reflect the source-
    code defaults rather than env overrides.

    ``monkeypatch`` undoes both the function stub and the env
    deletes at test teardown; the caller is responsible for a
    matching final ``importlib.reload`` to restore the module's
    class-level constants for any sibling test that imports
    BaseConfig directly.

    Defined as a module-level helper rather than a fixture because
    each test that uses it also needs to do its own try/finally
    reload-restore -- a fixture would split that ownership across
    setup and teardown and obscure the intent.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    import dotenv

    from app import config as config_module

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_a, **_k: None)
    for var in _C10_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return importlib.reload(config_module)


def _restore_config_module():
    """Reload ``app.config`` so its class-level constants reflect
    the env that ``monkeypatch`` has just restored.
    """
    # pylint: disable=import-outside-toplevel
    import importlib

    from app import config as config_module
    importlib.reload(config_module)


def _enable_mfa(user_id):
    """Persist an enabled MFA config with a known secret and codes.

    Mirrors the helper in ``test_session_invalidation.py``.
    """
    config = MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(_KNOWN_TOTP_SECRET),
        backup_codes=mfa_service.hash_backup_codes(_KNOWN_BACKUP_CODES),
    )
    db.session.add(config)
    db.session.commit()


def _do_login_no_mfa(client):
    """Run a fresh password-only login on a test client.

    Returns the login response so the caller can assert on it.
    """
    return client.post("/login", data={
        "email": "test@shekel.local",
        "password": "testpass",
    }, follow_redirects=False)
