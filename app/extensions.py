"""
Shekel Budget App -- Flask Extension Instances

Extensions are instantiated here (without an app) and bound to the
Flask app inside the create_app() factory.  This avoids circular
imports: models can import `db` from here without importing the app.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# Project-wide constraint-naming convention.  Documented here so the
# constant has a single canonical location and can be reused by tests,
# manual migrations, and developer tooling.  Closes audit finding
# F-078 ("FK naming-convention violation across the DB").
#
# This dictionary is NOT applied to the SQLAlchemy MetaData -- doing
# so would cause Alembic's chain-replay path (the test template build,
# which runs every migration from scratch via
# ``scripts/build_test_template.py``) to compute new constraint names
# for the un-named ``sa.ForeignKeyConstraint`` calls in pre-C-43
# migrations, which would then break the later migrations that DROP
# those constraints by their original dialect-default names.  C-43's
# audit-trail commit message documents the trade-off in full.
#
# Forward enforcement is therefore manual: every new ``db.Column(
# ForeignKey(...))`` or ``db.UniqueConstraint(...)`` or
# ``db.CheckConstraint(...)`` MUST carry an explicit ``name=``
# argument shaped by this convention.  The contract is enforced by
# code review and by the regression test
# ``tests/test_models/test_c43_ondelete_and_naming_convention.py::
# TestNamingConventionContract`` which asserts the explicit-name
# rule for every FK in the model registry.
#
# Convention keys (mirroring SQLAlchemy's ``MetaData.naming_convention``
# template placeholders so the dictionary can be re-instated globally
# in the future if the migration-chain replay issue is resolved):
#
#   ix -- ``ix_<column_label>``  (single-column indexes)
#   uq -- ``uq_<table>_<column>``  (unique constraints)
#   ck -- ``ck_<table>_<rule>``    (CHECK constraints; the ``rule``
#                                  is the human-readable suffix
#                                  passed as ``name="ck_<...>"``)
#   fk -- ``fk_<table>_<column>``  (foreign keys; the ``column`` is
#                                  the source-side column name)
#   pk -- ``pk_<table>``           (primary keys; one per table)
SHEKEL_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# Database ORM.  Flask-SQLAlchemy builds its own ``MetaData`` without
# a naming convention so the migration chain replays cleanly (see
# SHEKEL_NAMING_CONVENTION above for the full rationale).
db = SQLAlchemy()

# Alembic migration manager
migrate = Migrate()

# Session-based authentication
login_manager = LoginManager()
login_manager.login_view = "auth.login"  # Where the login gate sends anonymous requests
login_manager.login_message_category = "warning"

# Session-fixation defence.  Flask-Login's default ("basic") flips the
# session's ``_fresh`` flag to False when the per-request session
# identifier (sha512 of remote address + User-Agent) drifts from the
# value captured at login_user() time, but leaves the rest of the
# session intact -- so an attacker who replays a stolen signed-cookie
# session from a different IP/UA would still be treated as the
# original user for any endpoint that does not require ``fresh_login``.
# "strong" mode pops every Flask-Login session key on identifier drift
# (see flask_login/login_manager.py:_session_protection_failed) and
# additionally schedules the remember-me cookie for clearing, forcing
# a complete re-authentication.  This is required for ASVS L2 V3.2.1
# (Session Protection) and closes audit finding F-038.  The protection
# is small defence-in-depth gain today (Flask's signed-cookie session
# already resists classic fixation) but is load-bearing once the
# project migrates to a server-side session store (planned remediation
# Commit C-53) where stolen session IDs would otherwise be replayable
# from any origin.  See shekel-audits/security-2026-04-15/
# remediation-plan.md "Commit C-07".
login_manager.session_protection = "strong"

# CSRF protection
csrf = CSRFProtect()

# Rate limiting.  All operational settings (storage backend, default
# limits, swallow_errors, in-memory fallback, strategy) are intentionally
# resolved from app.config inside Limiter.init_app(), NOT from constructor
# arguments here.  Reason: Flask-Limiter 4.x (_extension.py:371-376)
# preferentially uses the constructor's storage_uri over the value in
# app.config[ConfigVars.STORAGE_URI], which means a value set here would
# silently override TestConfig's "memory://" override and ProdConfig's
# Redis URI.  Keeping the constructor minimal lets each environment
# (DevConfig / TestConfig / ProdConfig) own its own posture without
# touching this file.  See audit finding F-034 and remediation Commit
# C-06.
limiter = Limiter(key_func=get_remote_address)
