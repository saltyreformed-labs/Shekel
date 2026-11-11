"""
Shekel Budget App -- Seed User & Default Data

Creates the single Phase 1 user by delegating to
``registration_service.register_user`` -- the same provisioning path the
/register route uses -- so the seeded user is identical in shape to a
self-registered one: user, settings, the pay-period schedule, checking
account (with origination anchor history), baseline scenario, default
categories, and default tax configuration.  This script owns only the
operator-facing policy around that call: env parsing, the seed
password guard, the idempotent already-exists skip, redacted
logging, and the credential scrub.

**The published default password is refused everywhere but a host run
and the suite** (ruling plan#3448).  The deploy runs this script under
``FLASK_ENV=deploy`` on any stack it seeds (ruling plan#3451; the dev
stack's seeds no user, ruling plan#3509), so the configuration's name
cannot say which stack is production: the guard refuses unless the name
is one of the two that
never deploy (:data:`_DEFAULT_PASSWORD_ALLOWED_UNDER`), and a stack it
does not know refuses too.  (Historically it hand-copied the
provisioning sequence and had already drifted from the service --
e.g. it never seeded tax data.)

Validates that the password is at least 12 characters, matching the
minimum enforced by the application's change_password() and
register_user() functions.  Exits with code 1 if the password is
too short.

**SEED_USER_LAST_PAYDAY is REQUIRED and has no default** (plan step X-ad-a,
ruling R-DB).  Registration stopped inventing a pay period at sign-up because
an invented payday is never the owner's real one and blocks them from entering
it (finding **N-123**); defaulting the seeded operator's payday to "today"
here would put that same fabrication back on the one path that provisions the
production account.  There is no honest default for the day somebody was last
paid, so the script refuses rather than guesses -- the same shape as its
refusal of the documented default password in every deploy.  The cadence and the
horizon DO default, to the app-wide constants, because those are stated
premises rather than facts about one person.

After seeding completes (or returns early on an existing user), the
SEED_USER_PASSWORD and SEED_USER_EMAIL values are scrubbed from
``os.environ`` and the C-level environment.  This is defense-in-depth
against a future caller (or a child process spawned during seeding)
reading the credential back out of the process environment after it
has served its one-shot purpose.  Gunicorn never holds them at all,
which closes the ``cat /proc/<gunicorn>/environ`` exposure called out
in audit finding F-022: this script runs in the migrate service
(``scripts/deploy.sh``), and the web server's container is not given
the seed's variables (card plan#2166).  See audit finding F-022 and
remediation Commit C-34.

Usage:
    FLASK_ENV=deploy python scripts/seed_user.py

Environment variables (or .env file):
    SEED_USER_EMAIL         -- default: admin@shekel.local
    SEED_USER_PASSWORD      -- default: ChangeMe!2026, refused outside a
                               host run and the suite
    SEED_USER_DISPLAY_NAME  -- default: Budget Admin
    SEED_USER_LAST_PAYDAY   -- REQUIRED, ISO ``YYYY-MM-DD``: the day the
                               operator was last paid.  Must fall within one
                               cadence of today, which the service enforces.
    SEED_USER_CADENCE_DAYS  -- default: the app's DEFAULT_PAY_CADENCE_DAYS
    SEED_USER_NUM_PERIODS   -- default: the app's DEFAULT_PAY_PERIOD_HORIZON
"""

import os
import sys
from datetime import date


# Names of the seed-only env vars that must be scrubbed from
# ``os.environ`` after the seed step completes.  SEED_USER_DISPLAY_NAME
# is intentionally omitted -- the display name is not a secret, never
# leaves the user record, and may be useful for ops queries that want
# to identify the seeded account by name without paying the audit-log
# cost of resolving by id.
_SEED_SECRET_ENV_VARS: tuple[str, ...] = (
    "SEED_USER_PASSWORD",
    "SEED_USER_EMAIL",
)

#: The password the seed falls back to when ``SEED_USER_PASSWORD`` is unset,
#: published in this file, ``.env.example`` and the README, so known to anyone.
_DEFAULT_PASSWORD = "ChangeMe!2026"

#: The configurations (``app.config.CONFIG_MAP`` keys) under which the seed may
#: use :data:`_DEFAULT_PASSWORD` (ruling plan#3448): a host run and the suite,
#: the two that never deploy.  Named by what is ALLOWED, so every other name,
#: ``deploy`` and ``production`` among them and any added later, refuses.
_DEFAULT_PASSWORD_ALLOWED_UNDER: frozenset[str] = frozenset({
    "development",
    "testing",
})

# Add project root to path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the sys.path bootstrap above must run
# before these imports so ``app`` resolves when invoked as
# ``python scripts/seed_user.py`` (sys.path[0] is scripts/, not the repo
# root, in that mode).
# pylint: disable=wrong-import-position
from app import create_app
from app.config import BaseConfig, configuration_name
from app.enums import BusinessDayShiftEnum
from app.exceptions import ConflictError, ValidationError
from app.extensions import db
from app.models.user import User
from app.services import pay_rhythm, registration_service
# pylint: enable=wrong-import-position


def _read_int_env(name: str, default: int, unit: str) -> int:
    """Return an integer env var, exiting 1 on a value that is not one.

    ``int()`` on operator input is a place a typo becomes a traceback.  The
    service refuses an out-of-RANGE value with a message the operator can act
    on; this refuses an unparseable one the same way, rather than letting a
    ``ValueError`` escape as a stack trace in the container log.

    Args:
        name: The environment variable to read.
        default: The value to use when the variable is unset or empty.
        unit: What the number counts, for the refusal message.  Required
            rather than defaulted: this helper reads a count of DAYS and a
            count of PAY PERIODS, and one hardcoded noun made the message
            wrong for whichever caller did not pick it.

    Returns:
        The parsed integer, or *default*.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(
            f"Error: {name} must be a whole number of {unit} (got {raw!r}).",
            file=sys.stderr,
        )
        sys.exit(1)


def _read_last_payday() -> date:
    """Return SEED_USER_LAST_PAYDAY, exiting 1 when it is absent or malformed.

    REQUIRED with no default (plan step X-ad-a): see the module docstring for
    why the one thing this script may not invent is the day somebody was paid.

    Returns:
        The parsed civil date.
    """
    raw = os.getenv("SEED_USER_LAST_PAYDAY", "").strip()
    if not raw:
        print(
            "Error: SEED_USER_LAST_PAYDAY is not set.  Set it to the ISO date "
            "(YYYY-MM-DD) the seeded user was last paid -- Shekel budgets by "
            "paycheck and there is no honest default for that day.  It must "
            "fall within one pay cadence of today.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        print(
            f"Error: SEED_USER_LAST_PAYDAY must be an ISO date (YYYY-MM-DD); "
            f"got {raw!r}.",
            file=sys.stderr,
        )
        sys.exit(1)


def seed_user():
    """Create the seeded user via ``registration_service.register_user``.

    Applies the script-side guards (the seed password guard,
    :func:`_check_seed_password`, the 12-character minimum with operator
    guidance, and the required last-payday input), then delegates the
    provisioning itself to the registration service so the seeded user's
    shape is identical to a /register user's.  Idempotent: an existing
    user takes the already-exists skip and is returned unchanged.  Exits
    with code 1 on a password or input problem.

    Returns:
        The created (or pre-existing) User.
    """
    email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
    password = os.getenv("SEED_USER_PASSWORD", _DEFAULT_PASSWORD)
    display_name = os.getenv("SEED_USER_DISPLAY_NAME", "Budget Admin")

    # The guard runs here as well as in ``__main__`` (where it runs before
    # ``create_app``), so it protects every caller of this function.
    _check_seed_password()

    # Enforce the same 12-character minimum as the app's change_password()
    # and register_user() functions.  Prevents deploying with a weak
    # default that cannot be changed through the UI.
    if len(password) < 12:
        print(
            f"Error: SEED_USER_PASSWORD must be at least 12 characters "
            f"(got {len(password)}).  Set SEED_USER_PASSWORD in .env or "
            f"environment."
        )
        sys.exit(1)

    # THE ALREADY-EXISTS SKIP COMES FIRST, and it must (adversarial review of
    # plan step X-ad-a).  A re-run over an existing owner creates nothing, so
    # it needs no creation inputs -- but Python evaluates a call's arguments
    # before the call, so building the spec below would run
    # ``_read_last_payday()`` first, and that ``sys.exit(1)`` raises
    # ``SystemExit``, a ``BaseException`` no ``except`` clause here catches.
    # A production stack whose ``state`` volume was recreated (an operator
    # path ``scripts/deploy.sh`` documents as supported) would then fail its
    # deploy under ``set -e``, so the web server never starts -- the app
    # failing to boot for want of a payday that a skipped creation was never
    # going to use.
    #
    # ``register_user``'s own uniqueness check REMAINS the authority: it still
    # raises ``ConflictError`` below, which is now the racing-loser path
    # rather than the ordinary one.  Audit finding F-114 / C-16: stdout is
    # captured by the container log driver and shipped off-host, so log lines
    # carry the synthetic primary key, never the email.
    existing = (
        db.session.query(User)
        .filter_by(email=email.strip().lower())
        .first()
    )
    if existing is not None:
        print(
            f"User id={existing.id} already exists (email redacted).  "
            "Skipping."
        )
        return existing

    # Provision via the canonical registration service -- the same
    # path /register uses -- so the seeded user's shape (settings,
    # pay-period schedule, checking account, baseline scenario,
    # categories, tax configuration) cannot drift from a
    # self-registered user's.
    #
    # The pay-schedule inputs are read HERE rather than at the top of the
    # function so the password guards above still run first: an operator with
    # both problems is told about the credential one before being sent to look
    # up a payday.
    try:
        user = registration_service.register_user(
            registration_service.RegistrationSpec(
                email=email,
                password=password,
                display_name=display_name,
                first_payday=_read_last_payday(),
                # ``none`` with no env var, and that is the answer rather than an
                # omission (plan step pay_calendar:C14-b, ruling R-PC56): every
                # schedule starts with the payday convention OFF, and a seeded
                # owner is one nobody has asked, so stating anything else would be
                # a claim made on their behalf.  The owner answers on the
                # pay-periods settings card.
                rhythm=pay_rhythm.Rhythm(
                    cadence=pay_rhythm.FixedDays(_read_int_env(
                        "SEED_USER_CADENCE_DAYS",
                        BaseConfig.DEFAULT_PAY_CADENCE_DAYS,
                        unit="days",
                    )),
                    shift=BusinessDayShiftEnum.NONE,
                ),
                num_periods=_read_int_env(
                    "SEED_USER_NUM_PERIODS",
                    BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
                    unit="pay periods",
                ),
                # No env var, and that is the answer rather than an omission (plan
                # step balance:X-bh-2).  ``None`` means NOT STATED: the engine
                # counts only the paydays this seed records, which is exactly what
                # it did before that step and is the reading a script may honestly
                # supply.  Unlike the payday above there is nothing to fabricate --
                # a seed script cannot know when the owner's job began, and saying
                # so is what ``None`` now means.  The owner states it themselves on
                # the pay-periods settings section, which is the door for it.
                history_opens_on=None,
            ),
        )
    except ConflictError:
        # The race: another process created this owner between the check
        # above and this call.  Same outcome, re-read rather than assumed.
        raced = (
            db.session.query(User)
            .filter_by(email=email.strip().lower())
            .one()
        )
        print(
            f"User id={raced.id} already exists (email redacted).  "
            "Skipping."
        )
        return raced
    except ValidationError as exc:
        # Operator input problem (e.g. a malformed SEED_USER_EMAIL, or a
        # payday outside the window the service bounds it to).  The message
        # is safe to print: it describes the rule, not a secret.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    db.session.commit()
    print(
        f"Created user id={user.id} (email redacted from log) with "
        "settings, pay-period schedule, checking account, baseline "
        "scenario, default categories, and default tax configuration."
    )
    # Final summary stays on user_id only -- the email is the same
    # value the operator passed in via SEED_USER_EMAIL (or the
    # documented default), so re-emitting it here would only add a
    # PII surface to the captured container log.
    print("\nSeed complete.  You can now log in with:")
    print(f"  User ID:  {user.id} (email passed via SEED_USER_EMAIL)")
    print("  Password: [set via SEED_USER_PASSWORD env var or default]")
    return user


def _check_seed_password():
    """Reject an unsafe seed password unless this is a host run or the suite.

    The one statement of the guard (ruling plan#3448): :func:`seed_user`
    and ``__main__`` both call it, ``__main__`` before ``create_app()`` so
    the check works even when DATABASE_URL is not set (e.g., direct script
    invocation outside Docker).  Under a configuration named in
    :data:`_DEFAULT_PASSWORD_ALLOWED_UNDER` it returns at once; under any
    other, the deploy's on every stack among them, it refuses an empty or
    whitespace-only password and the published default, and warns of one
    shorter than the application's minimum.
    """
    if configuration_name() in _DEFAULT_PASSWORD_ALLOWED_UNDER:
        return
    password = os.getenv("SEED_USER_PASSWORD", _DEFAULT_PASSWORD)
    if not password or not password.strip():
        print(
            "Error: SEED_USER_PASSWORD is empty or whitespace-only. "
            "Set a strong password in .env or environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    if password == _DEFAULT_PASSWORD:
        print(
            f"Error: SEED_USER_PASSWORD is still the published default "
            f"'{_DEFAULT_PASSWORD}', which only a host run or the test suite "
            "may seed.  Set a unique password in .env or environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(password) < 12:
        print(
            f"Warning: SEED_USER_PASSWORD is only {len(password)} "
            f"characters. The application requires at least 12.",
            file=sys.stderr,
        )


def _scrub_seed_env_vars() -> None:
    """Remove SEED_USER_PASSWORD/SEED_USER_EMAIL from this process env.

    The seed credentials are needed only at one-shot invocation time.
    Once the user row is in the database (whether created by this run
    or already present from a prior run), the values have served their
    purpose and should not linger in ``os.environ`` where any
    subsequent code path -- application logging, debug introspection,
    a child process inherited via ``subprocess.run(env=os.environ)`` --
    could surface them.

    Removal is performed at two layers for defense-in-depth:

    1. ``os.environ.pop`` -- removes the key from Python's environment
       mapping.  In CPython 3.9+ this also calls ``os.unsetenv`` under
       the hood, so the C-level ``environ`` array is updated too.
    2. An explicit ``os.unsetenv`` -- documents intent and protects
       against any future change to ``os.environ.pop``'s implementation
       that decouples it from the C-level environ.

    This scrub helps only within this script's process tree.  Gunicorn's
    ``/proc/<pid>/environ`` is kept clear another way: this script runs in
    the migrate service (``scripts/deploy.sh``), and the web server's
    container is not given the seed's variables (card plan#2166).

    DISPLAY_NAME is intentionally retained.  It is not a secret and
    has operational value (e.g. an operator confirming they seeded
    the right account by name).  See audit finding F-022 and
    remediation Commit C-34.
    """
    for key in _SEED_SECRET_ENV_VARS:
        # ``os.environ.pop`` is a no-op when the key is absent.  No
        # try/except guard is needed because the default argument
        # silences a missing key.
        os.environ.pop(key, None)
        # ``os.unsetenv`` is a no-op when the underlying environ entry
        # is already absent on POSIX (Linux containers, the only
        # production target).  Wrapped in a guard nonetheless because
        # CPython documents the behaviour as platform-dependent and
        # raising here would propagate as a script failure that masks
        # the actual seed result.
        try:
            os.unsetenv(key)
        except OSError:
            pass


if __name__ == "__main__":
    _check_seed_password()
    app = create_app()
    try:
        with app.app_context():
            seed_user()
    finally:
        # Scrub credentials regardless of the seed outcome.  A failed
        # seed must not leave the password in os.environ for a future
        # retry to read; the next run sources the value from the
        # docker-compose env or the docker secret afresh.
        _scrub_seed_env_vars()
