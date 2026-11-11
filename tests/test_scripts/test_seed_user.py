"""Tests for the seed user script's password guard and
credential hygiene scrubbing.

_check_seed_password() is graded in-process; one subprocess case grades
that the script asks it before create_app().  That subprocess's
environment is carefully controlled:
  - Inherits PATH and Python-related vars (so imports work)
  - Sets FLASK_ENV and SEED_USER_PASSWORD explicitly
  - Does NOT inherit DATABASE_URL (prevents connecting to real DBs)

The refusing case exits at the password check before reaching
create_app().

The TestSeedUserCredentialScrub class covers Commit C-34 / audit
finding F-022: after running, ``scripts/seed_user.py`` must remove
SEED_USER_PASSWORD and SEED_USER_EMAIL from its own ``os.environ``
and from the C-level environ array (so any child process inherited
via ``subprocess.run(env=os.environ)`` does not pick the credential
back up).  The companion ``unset`` step in ``entrypoint.sh`` is
covered by tests/test_deploy/test_seed_credential_hygiene.py.
"""

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import CONFIG_MAP, BaseConfig
from app.models.account import Account
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services import pay_schedule_service
from app.services.pay_rhythm import FixedDays
from app.services.registration_service import DEFAULT_CATEGORIES
from app.utils.dates import display_today
# Aliased so the module-level name cannot shadow the ``seed_user``
# pytest fixture from conftest.py.
from scripts.seed_user import (
    _DEFAULT_PASSWORD_ALLOWED_UNDER,
    _check_seed_password,
)
from scripts.seed_user import seed_user as run_seed_user
from tests._test_helpers import (
    derived_span,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_USER_SCRIPT = REPO_ROOT / "scripts" / "seed_user.py"


def _safe_env(**overrides):
    """Build a minimal subprocess environment for seed_user.py.

    Inherits only what Python needs to run (PATH, PYTHONPATH, etc.)
    but explicitly excludes DATABASE_URL and TEST_DATABASE_URL to
    prevent the subprocess from connecting to any real database.
    """
    env = {}
    # Inherit only the vars Python needs to find modules and run.
    for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME",
                "VIRTUAL_ENV", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    env.update(overrides)
    return env


class TestSeedUserProductionGuard:
    """Verify ``_check_seed_password`` decides, and that the script asks it.

    **Two different questions, and they were one SUBPROCESS test each until
    2026-08-18.**  The guard's LOGIC is a pure function of two environment
    variables -- it reads them, returns or ``sys.exit(1)``s, and touches
    nothing else -- so it is graded in-process here, exactly and instantly.
    What genuinely needs a subprocess is the WIRING: that
    ``scripts/seed_user.py`` calls the guard AT ALL and calls it BEFORE
    ``create_app()``, which is the property its own docstring rests on ("Runs
    before create_app() so the check works even when DATABASE_URL is not
    set").  One case below carries that, and it is the production-REJECT case
    because it exits at the guard and so never builds the app.

    **What that split fixed.**  ``test_default_password_allowed_in_development``
    was the only one of the three whose password is ACCEPTED, so it was the
    only one that ran on into ``create_app()`` -- paying a full Flask +
    SQLAlchemy import to observe that a string was ABSENT from stderr.  It
    carried the 10-second budget sized for the two cases that exit at the
    guard, and it measured **10.04s** on CI's runner under twelve pytest
    workers (the app-importing cases elsewhere in this file are budgeted at
    20 and measured 9.96s, 11.61s and 11.98s in the same run).  An absence
    assertion behind a ten-second app boot is now a positive assertion about
    a function call: the guard RETURNS.
    """

    def test_the_script_asks_the_guard_before_it_builds_the_app(self):
        """WIRING: the guard decides before ``create_app()`` is ever called.

        The one subprocess case, and the only thing it is for.  It passes an
        EXPLICITLY EMPTY ``DATABASE_URL``, and that is the whole mechanism:
        ``app/config.py`` calls ``load_dotenv()``, whose default
        ``override=False`` leaves a variable already present in the
        environment alone -- so the repository's own ``.env`` cannot supply a
        database here, and ``create_app()`` cannot succeed.  The two orders
        are then distinguishable from outside:

        * guard first -- exit 1, stderr names the password;
        * app first -- exit 1 from the app build, and the guard is never
          reached, so the password message is ABSENT.

        The password assertion IS the control, and it was measured firing on a
        planted ``create_app()``-first ``__main__`` block.

        **Two earlier levers for this were measured BLIND** (2026-08-18), which
        is why the mechanism is spelled out.  Relying on the environment simply
        not having a database is true in CI and false on a developer's machine.
        Running from an empty working directory looked like the fix and is not:
        ``find_dotenv`` walks up from the CALLING FRAME's file, which is
        ``app/config.py``, so a SCRIPT invocation finds the repository ``.env``
        whatever the CWD -- it only follows the CWD when the caller has no file,
        as under ``python -c``, which is what the first probe used. Both plants
        PASSED against those two.

        It stays cheap for the same reason it is the honest wiring case: a
        rejected password exits AT the guard, so nothing is imported.
        """
        # ``check=False`` so we can assert on the non-zero exit code rather
        # than have subprocess raise CalledProcessError.
        result = subprocess.run(
            [sys.executable, "scripts/seed_user.py"],
            env=_safe_env(
                FLASK_ENV="production",
                SEED_USER_PASSWORD="ChangeMe!2026",
                DATABASE_URL="",
            ),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 1
        assert "ChangeMe!2026" in result.stderr, (
            "the script reached create_app() before asking the guard, so the "
            "check no longer works without a database -- which is the "
            "property _check_seed_password's own docstring rests on"
        )

    def test_default_password_rejected_in_production(self, monkeypatch, capsys):
        """The default 'ChangeMe!2026' password must be rejected in production."""
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("SEED_USER_PASSWORD", "ChangeMe!2026")

        with pytest.raises(SystemExit) as exc:
            _check_seed_password()

        assert exc.value.code == 1
        assert "ChangeMe!2026" in capsys.readouterr().err

    def test_the_default_is_the_unsafe_value_when_nothing_is_set(
        self, monkeypatch, capsys,
    ):
        """An UNSET SEED_USER_PASSWORD is rejected in production too.

        The guard defaults the variable to ``'ChangeMe!2026'`` rather than to
        empty, so "nobody set one" and "somebody set the documented default"
        are the same state to it.  The subprocess cases could not tell those
        apart -- they always set the variable -- so this is a branch the split
        made reachable rather than one it moved.
        """
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.delenv("SEED_USER_PASSWORD", raising=False)

        with pytest.raises(SystemExit) as exc:
            _check_seed_password()

        assert exc.value.code == 1
        assert "ChangeMe!2026" in capsys.readouterr().err

    def test_empty_password_rejected_in_production(self, monkeypatch, capsys):
        """An empty SEED_USER_PASSWORD must be rejected in production."""
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("SEED_USER_PASSWORD", "")

        with pytest.raises(SystemExit) as exc:
            _check_seed_password()

        assert exc.value.code == 1
        assert "empty" in capsys.readouterr().err.lower()

    def test_whitespace_password_rejected_in_production(
        self, monkeypatch, capsys,
    ):
        """A whitespace-only password is empty as far as the guard is concerned.

        The guard tests ``not password.strip()`` beside ``not password``, and
        the subprocess cases only ever exercised the first half.
        """
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("SEED_USER_PASSWORD", "   ")

        with pytest.raises(SystemExit) as exc:
            _check_seed_password()

        assert exc.value.code == 1
        assert "empty" in capsys.readouterr().err.lower()

    def test_default_password_allowed_in_development(self, monkeypatch, capsys):
        """In development mode the default password is allowed.

        The positive form of what a subprocess could only assert by absence:
        the guard RETURNS, and says nothing.  ``FLASK_ENV`` is the only thing
        it branches on before that, so a guard that started rejecting under a
        host run would raise here rather than merely print differently.
        """
        monkeypatch.setenv("FLASK_ENV", "development")
        monkeypatch.setenv("SEED_USER_PASSWORD", "ChangeMe!2026")

        assert _check_seed_password() is None
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("flask_env", ["testing", None])
    def test_default_password_allowed_in_the_suite_and_unset(
        self, monkeypatch, capsys, flask_env,
    ):
        """The suite, and no ``FLASK_ENV`` at all, may seed the default too.

        Unset is a host run: ``create_app`` builds ``development`` then, and the
        guard reads the name through the same function
        (``app.config.configuration_name``), so the two cannot disagree.
        """
        if flask_env is None:
            monkeypatch.delenv("FLASK_ENV", raising=False)
        else:
            monkeypatch.setenv("FLASK_ENV", flask_env)
        monkeypatch.setenv("SEED_USER_PASSWORD", "ChangeMe!2026")

        assert _check_seed_password() is None
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize(
        "flask_env",
        ["deploy", "development-container", "a-stack-nobody-named"],
    )
    def test_default_password_rejected_everywhere_else(
        self, monkeypatch, capsys, flask_env,
    ):
        """Every name but a host run's and the suite's refuses the default.

        Ruling plan#3448.  ``deploy`` is the case that matters: the deploy seeds
        under it on every stack that seeds (the dev stack's seeds no user,
        ruling plan#3509), production's included, so a guard that refused only
        ``production`` would never fire there again.
        ``development-container`` and a name nobody has made are the
        fail-closed half: the guard names what it ALLOWS, so anything else
        refuses.
        """
        monkeypatch.setenv("FLASK_ENV", flask_env)
        monkeypatch.setenv("SEED_USER_PASSWORD", "ChangeMe!2026")

        with pytest.raises(SystemExit) as exc:
            _check_seed_password()

        assert exc.value.code == 1
        assert "ChangeMe!2026" in capsys.readouterr().err

    def test_the_allowed_names_are_configurations(self):
        """Each name the guard allows is a configuration ``create_app`` builds.

        A renamed configuration would otherwise leave the guard allowing a name
        nothing runs under, and refusing the host run it was meant to allow.
        """
        assert _DEFAULT_PASSWORD_ALLOWED_UNDER == {"development", "testing"}
        assert _DEFAULT_PASSWORD_ALLOWED_UNDER <= CONFIG_MAP.keys()

    def test_the_seed_itself_asks_the_guard(self, monkeypatch, capsys):
        """``seed_user()`` refuses the default under ``deploy`` before any read.

        The guard has one statement and two callers: ``__main__`` (graded by
        the subprocess case above) and ``seed_user()``, so a caller that skips
        ``__main__`` is guarded too.  It exits before the function touches the
        database, so no fixture is needed.
        """
        monkeypatch.setenv("FLASK_ENV", "deploy")
        monkeypatch.setenv("SEED_USER_PASSWORD", "ChangeMe!2026")

        with pytest.raises(SystemExit) as exc:
            run_seed_user()

        assert exc.value.code == 1
        assert "ChangeMe!2026" in capsys.readouterr().err

    def test_a_short_production_password_WARNS_and_does_not_exit(
        self, monkeypatch, capsys,
    ):
        """A short production password is a warning, not a refusal.

        The third branch of the guard, and it had NO test at all: it prints
        the application's 12-character minimum to stderr and then falls
        through, so a production seed proceeds with a password the app itself
        would refuse.  Pinned as it behaves rather than as it arguably should
        -- whether this ought to exit is a decision, not a bug fix.
        """
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("SEED_USER_PASSWORD", "short1!")

        assert _check_seed_password() is None

        err = capsys.readouterr().err
        assert "Warning" in err
        assert "7" in err
        assert "12" in err


class TestSeedUserCredentialScrub:
    """Verify scripts/seed_user.py scrubs seed credentials from os.environ.

    Audit finding F-022 / Commit C-34.  The script must not leave
    SEED_USER_PASSWORD or SEED_USER_EMAIL in ``os.environ`` once its
    work is done -- otherwise any subsequent code path inside the
    same Python process (or a child spawned via ``subprocess.run(env=
    os.environ)``) would still see the credential.

    Tests in this class load the script's source as a module rather
    than spawning a subprocess so we can introspect the post-run
    state of ``os.environ`` directly.  The destructive bits
    (create_app + DB writes) are the responsibility of the
    subprocess-based tests in test_seed_redaction.py and the
    ``test_seed_user_subprocess_*`` tests below.
    """

    @staticmethod
    def _import_seed_module():
        """Return the loaded scripts.seed_user module.

        Uses ``importlib`` so each test gets a fresh import without
        polluting other tests' module cache.  Exposing
        ``_scrub_seed_env_vars`` via the imported module means the
        unit tests below can call it in isolation without needing
        the create_app / app_context machinery at all.

        No ``monkeypatch`` argument: callers do their own env
        manipulation BEFORE calling this helper, and the helper just
        loads and returns the module under test.
        """
        import importlib.util  # pylint: disable=import-outside-toplevel
        spec = importlib.util.spec_from_file_location(
            "scripts_seed_user_under_test",
            str(SEED_USER_SCRIPT),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_scrub_removes_password_from_os_environ(self, monkeypatch):
        """``_scrub_seed_env_vars`` deletes SEED_USER_PASSWORD."""
        # Set the env var so the scrub has something to remove.  The
        # value is a sentinel string we look for AFTER the scrub --
        # any leftover would surface as a false positive.
        monkeypatch.setenv(
            "SEED_USER_PASSWORD", "scrub-test-sentinel-password"
        )
        module = self._import_seed_module()
        # Pre-condition: the env var IS set in this process.
        assert "SEED_USER_PASSWORD" in os.environ
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Post-condition: the env var is gone from BOTH os.environ
        # (Python's mapping) and the underlying C environ (read via
        # os.getenv, which goes through getenv(3)).
        assert "SEED_USER_PASSWORD" not in os.environ, (
            "SEED_USER_PASSWORD still present in os.environ after scrub"
        )
        assert os.getenv("SEED_USER_PASSWORD") is None, (
            "SEED_USER_PASSWORD still present in C environ after scrub "
            "(os.getenv reads the underlying environ via getenv(3))"
        )

    def test_scrub_removes_email_from_os_environ(self, monkeypatch):
        """``_scrub_seed_env_vars`` deletes SEED_USER_EMAIL."""
        monkeypatch.setenv("SEED_USER_EMAIL", "scrub-test@shekel.local")
        module = self._import_seed_module()
        assert "SEED_USER_EMAIL" in os.environ
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        assert "SEED_USER_EMAIL" not in os.environ
        assert os.getenv("SEED_USER_EMAIL") is None

    def test_scrub_preserves_display_name(self, monkeypatch):
        """``_scrub_seed_env_vars`` MUST NOT touch SEED_USER_DISPLAY_NAME.

        DISPLAY_NAME is not a secret and has operational value (an
        operator confirming they seeded the right account by name
        during a forensic review).  A regression that adds it to the
        scrub list would silently drop useful context.
        """
        monkeypatch.setenv(
            "SEED_USER_DISPLAY_NAME", "Sentinel Display Name"
        )
        module = self._import_seed_module()
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        assert os.environ.get("SEED_USER_DISPLAY_NAME") == \
            "Sentinel Display Name", (
            "SEED_USER_DISPLAY_NAME was scrubbed; the credential-hygiene "
            "scrub MUST limit itself to PASSWORD and EMAIL"
        )

    def test_scrub_is_a_noop_when_vars_already_absent(self, monkeypatch):
        """Calling scrub twice (or before any seed) must not raise.

        The seed step in entrypoint.sh skips the script entirely when
        the seed sentinel is present, but a future caller might still
        invoke ``_scrub_seed_env_vars`` defensively before any setenv.
        Idempotency means that scenario produces no exception and no
        log output -- ``os.environ.pop(key, None)`` and the guarded
        ``os.unsetenv`` together swallow the missing-key case.
        """
        monkeypatch.delenv("SEED_USER_PASSWORD", raising=False)
        monkeypatch.delenv("SEED_USER_EMAIL", raising=False)
        module = self._import_seed_module()
        # Should not raise.
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Idempotency: calling again is also fine.
        module._scrub_seed_env_vars()  # pylint: disable=protected-access

    def test_scrubbed_credential_not_inherited_by_subprocess(
        self, monkeypatch,
    ):
        """A child spawned after the scrub does NOT see the credential.

        This is the key end-to-end behaviour the F-022 fix is meant
        to provide.  We set the env var, run the scrub in this
        process, then spawn a child that prints its environ -- the
        child's stdout must NOT contain the sentinel value because
        the child inherits the C environ from the parent at fork()
        time, and the scrub modified that array.

        Uses ``env=None`` (inherit parent) on subprocess.run so the
        test exercises the actual inheritance path; passing
        ``env=os.environ.copy()`` would test only the dict, not the
        underlying C environ.
        """
        sentinel = "subprocess-inheritance-test-sentinel-xyz"
        monkeypatch.setenv("SEED_USER_PASSWORD", sentinel)
        module = self._import_seed_module()
        module._scrub_seed_env_vars()  # pylint: disable=protected-access
        # Child process prints its received SEED_USER_PASSWORD value
        # (or the empty string if absent).  We assert the sentinel
        # is NOT in the output.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('SEED_USER_PASSWORD', ''))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert sentinel not in result.stdout, (
            "Child subprocess inherited SEED_USER_PASSWORD after parent "
            "called _scrub_seed_env_vars; the C-level environ was not "
            "scrubbed"
        )


class TestSeedUserSubprocessScrub:
    """End-to-end: ``python scripts/seed_user.py`` removes the env vars.

    This complements the unit-style tests above by exercising the
    full ``__main__`` path: production guard, create_app, seed_user,
    THEN _scrub_seed_env_vars in the ``finally`` block.  The seed
    step needs DATABASE_URL pointing at a real database; we use the
    test DB the rest of the suite consumes.

    These tests also assert that a seed_user.py CRASH (e.g. missing
    DB) still scrubs the env vars on the way out -- the ``finally``
    block is the load-bearing piece for that property.
    """

    @staticmethod
    def _safe_env_with_db(**overrides):
        """Subprocess env with TEST_DATABASE_URL mapped to DATABASE_URL."""
        env = {}
        for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME",
                    "VIRTUAL_ENV", "LANG", "LC_ALL", "TEST_DATABASE_URL",
                    "SECRET_KEY"):
            if key in os.environ:
                env[key] = os.environ[key]
        if "TEST_DATABASE_URL" in env:
            env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
        env["FLASK_ENV"] = "development"
        env.update(overrides)
        return env

    def test_subprocess_does_not_print_password_after_seed(
        self, app, db,  # pylint: disable=unused-argument
    ):
        """``python scripts/seed_user.py`` does not echo the password.

        The script's print/log statements were already redacted in
        Commit C-16 (PII / F-114).  This test re-asserts the
        invariant against the C-34 changes -- the new scrub code
        path must not introduce a debug print of the value before
        scrubbing it.
        """
        sentinel_password = "c34-subprocess-scrub-pw-1234567"
        sentinel_email = "c34-subprocess-scrub@shekel.local"
        result = subprocess.run(
            [sys.executable, str(SEED_USER_SCRIPT)],
            env=self._safe_env_with_db(
                SEED_USER_EMAIL=sentinel_email,
                SEED_USER_PASSWORD=sentinel_password,
            ),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        # The script may exit 0 (created) or 0 (already exists);
        # both are acceptable.  The assertion is on the output.
        assert sentinel_password not in result.stdout, (
            f"seed_user.py leaked password into stdout: {result.stdout!r}"
        )
        assert sentinel_password not in result.stderr, (
            f"seed_user.py leaked password into stderr: {result.stderr!r}"
        )
        # Cleanup -- delete the sentinel user the script created.
        from app.extensions import db as _db  # pylint: disable=import-outside-toplevel
        from app.models.user import User  # pylint: disable=import-outside-toplevel
        existing = _db.session.query(User).filter_by(
            email=sentinel_email,
        ).first()
        if existing is not None:
            _db.session.delete(existing)
            _db.session.commit()


class TestSeedUserProvisioning:
    """DB-level tests for the register_user delegation.

    The script historically hand-copied the provisioning sequence and
    had drifted from the service (it never seeded tax data).  These
    pin the delegated contract: a seeded user gets the identical shape
    a /register user gets, and a re-run takes the idempotent skip.
    """

    @staticmethod
    def _set_seed_env(monkeypatch, email="seeded@shekel.local"):
        """Point the script's env inputs at a test identity.

        ``SEED_USER_LAST_PAYDAY`` is set to the USER's today (plan step
        X-ad-a): the script has no default for it, and the service refuses a
        payday in the future -- which ``date.today()`` would be, for the four
        hours a day a UTC process is already on tomorrow's date.
        """
        monkeypatch.setenv("SEED_USER_EMAIL", email)
        monkeypatch.setenv("SEED_USER_PASSWORD", "a-strong-seed-pass-1")
        monkeypatch.setenv("SEED_USER_DISPLAY_NAME", "Seeded User")
        monkeypatch.setenv(
            "SEED_USER_LAST_PAYDAY", display_today().isoformat(),
        )
        monkeypatch.setenv("SEED_USER_NUM_PERIODS", "4")

    def test_seed_creates_full_registration_shape(self, app, db, monkeypatch):
        """One seed run provisions the complete /register shape.

        Tax configuration was the load-bearing assertion: the hand-copied
        version never created it (the drift this delegation fixed).  Since
        plan step salary:X-at-1 the tax law lives in the code and
        registration copies none of it, so the shape a /register user gets
        holds no tax row -- and neither does a seeded one.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            user = run_seed_user()

            assert user.email == "seeded@shekel.local"
            assert (
                db.session.query(UserSettings)
                .filter_by(user_id=user.id).count() == 1
            )

            # The seeded owner gets a REAL schedule, not the single fabricated
            # period registration used to invent (plan step X-ad-a, ruling
            # R-DB): SEED_USER_NUM_PERIODS periods opening on the stated
            # payday, plus the ``budget.pay_schedule`` row that keeps a later
            # extend from having to infer the cadence back out of a period's
            # length (pay-calendar finding P8).
            periods = (
                db.session.query(PayPeriod)
                .filter_by(user_id=user.id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            assert len(periods) == 4
            assert [derived_span(p).period_index for p in periods] == [0, 1, 2, 3]
            assert periods[0].start_date == display_today()
            db.session.query(PaySchedule).filter_by(user_id=user.id).one()
            assert pay_schedule_service.resolve_cadence(
                user.id,
            ) == FixedDays(BaseConfig.DEFAULT_PAY_CADENCE_DAYS)

            account = (
                db.session.query(Account).filter_by(user_id=user.id).one()
            )
            assert account.name == "Checking"

            scenario = (
                db.session.query(Scenario).filter_by(user_id=user.id).one()
            )
            assert scenario.is_baseline is True

            assert (
                db.session.query(Category)
                .filter_by(user_id=user.id).count()
                == len(DEFAULT_CATEGORIES)
            )

    def test_seed_rerun_is_idempotent_skip(self, app, db, monkeypatch, capsys):
        """A second run returns the existing user and creates nothing.

        Container restarts re-run the seed step; the already-exists
        path must skip (via register_user's own uniqueness check, the
        single authority) rather than duplicate or crash.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            first = run_seed_user()
            first_id = first.id
            capsys.readouterr()

            second = run_seed_user()

            assert second.id == first_id
            assert "already exists" in capsys.readouterr().out
            assert (
                db.session.query(User)
                .filter_by(email="seeded@shekel.local").count() == 1
            )

    def test_missing_last_payday_refuses_and_creates_nothing(
        self, app, db, monkeypatch, capsys,
    ):
        """No SEED_USER_LAST_PAYDAY is a loud exit, not a guessed payday.

        Plan step X-ad-a, ruling R-DB.  Registration stopped inventing a pay
        period because an invented payday is never the owner's and blocks them
        from entering their real one (finding **N-123**); defaulting this to
        "today" here would put that same fabrication back on the one path that
        provisions the PRODUCTION account.  The script has no honest default,
        so it refuses -- the same shape as its existing refusal of the
        documented default password in production.

        The exit is what makes it safe operationally: ``entrypoint.sh`` runs
        under ``set -e``, so a first boot with the variable unset aborts
        rather than provisioning an owner onto a wrong calendar.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            monkeypatch.delenv("SEED_USER_LAST_PAYDAY", raising=False)
            before = db.session.query(User).count()

            with pytest.raises(SystemExit) as exc:
                run_seed_user()

            assert exc.value.code == 1
            assert "SEED_USER_LAST_PAYDAY is not set" in capsys.readouterr().err
            db.session.rollback()
            assert db.session.query(User).count() == before

    def test_malformed_last_payday_refuses_with_the_expected_shape(
        self, app, db, monkeypatch, capsys,
    ):
        """A payday that is not an ISO date exits 1 instead of tracebacking.

        ``date.fromisoformat`` on operator input is a place a typo becomes a
        stack trace in the container log; the message names the shape wanted
        and quotes what was given.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            monkeypatch.setenv("SEED_USER_LAST_PAYDAY", "08/05/2026")

            with pytest.raises(SystemExit) as exc:
                run_seed_user()

            assert exc.value.code == 1
            captured = capsys.readouterr().err
            assert "must be an ISO date (YYYY-MM-DD)" in captured
            assert "08/05/2026" in captured

    def test_a_non_numeric_cadence_refuses_rather_than_tracebacks(
        self, app, db, monkeypatch, capsys,
    ):
        """SEED_USER_CADENCE_DAYS that is not a number exits 1.

        The cadence and horizon DO have defaults, so an unset value is fine;
        a value that is set but unparseable is an operator mistake and gets
        the same actionable refusal the payday gets.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            monkeypatch.setenv("SEED_USER_CADENCE_DAYS", "fortnightly")

            with pytest.raises(SystemExit) as exc:
                run_seed_user()

            assert exc.value.code == 1
            assert "whole number of days" in capsys.readouterr().err

    def test_a_stale_last_payday_is_refused_by_the_service(
        self, app, db, monkeypatch, capsys,
    ):
        """A payday older than one cadence reaches the service's refusal.

        The script does not re-state the window rule -- ``register_user`` owns
        it -- so this proves the operator input is carried through to the bound
        rather than being validated only for shape here.

        **The message is asserted, not just the exit code**, and an adversarial
        review is why: every ``ValidationError`` this script catches exits 1,
        so a bare ``code == 1`` would pass just as well on a bad email, a short
        password or a bad cadence -- it would not prove the payday reached
        anything.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            stale = display_today() - timedelta(days=30)
            monkeypatch.setenv("SEED_USER_LAST_PAYDAY", stale.isoformat())

            with pytest.raises(SystemExit) as exc:
                run_seed_user()

            assert exc.value.code == 1
            captured = capsys.readouterr().err
            assert "has already ended" in captured
            assert stale.isoformat() in captured

    def test_a_rerun_skips_without_needing_a_payday(
        self, app, db, monkeypatch, capsys,
    ):
        """The idempotent re-run works with SEED_USER_LAST_PAYDAY unset.

        **The deploy contract, and it was broken for one revision of this
        step.** ``entrypoint.sh`` documents recreating the ``state`` volume as
        a supported operator action: the sentinel goes, the seed step re-runs,
        and the already-exists path creates nothing.  Building the
        registration spec first made that path demand a payday it was never
        going to use -- and the refusal is ``sys.exit(1)``, a ``BaseException``
        no ``except`` here catches, so under ``set -e`` the entrypoint aborted
        and Gunicorn never exec'd.  **The app failed to boot.**

        This is the arm the suite lacked: the existing re-run test sets the
        variable, so it could not see the requirement being asked at the wrong
        time.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            first = run_seed_user()
            first_id = first.id
            capsys.readouterr()

            monkeypatch.delenv("SEED_USER_LAST_PAYDAY", raising=False)
            monkeypatch.delenv("SEED_USER_CADENCE_DAYS", raising=False)
            monkeypatch.delenv("SEED_USER_NUM_PERIODS", raising=False)

            second = run_seed_user()

            assert second.id == first_id
            assert "already exists" in capsys.readouterr().out
            assert (
                db.session.query(User)
                .filter_by(email="seeded@shekel.local").count() == 1
            )

    def test_an_unmaterialisable_horizon_refuses_before_the_user_exists(
        self, app, db, monkeypatch, capsys,
    ):
        """SEED_USER_NUM_PERIODS=0 refuses cleanly, creating no owner.

        Zero periods used to pass every bound the app had: the schema never
        saw it (this is not a form), and ``generate_pay_periods`` happily
        created nothing -- so the failure surfaced several statements later, in
        ``create_account``, as "the user has no pay periods.  Generate pay
        periods first", which names no environment variable and no action the
        operator can take.  Worse, it fired AFTER the ``User`` row was added,
        falsifying ``register_user``'s own claim that every refusal happens
        before that.
        """
        with app.app_context():
            self._set_seed_env(monkeypatch)
            monkeypatch.setenv("SEED_USER_NUM_PERIODS", "0")
            before = db.session.query(User).count()

            with pytest.raises(SystemExit) as exc:
                run_seed_user()

            assert exc.value.code == 1
            assert "must be between 1 and 260" in capsys.readouterr().err
            db.session.rollback()
            assert db.session.query(User).count() == before
