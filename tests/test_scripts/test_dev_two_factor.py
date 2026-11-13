"""Tests for ``tools/dev_two_factor.py``: the dev copy's two-factor key, written and typed.

The writer makes a dev key the two-factor of one account on a dev copy of
production, so the dev-app tools sign in through ``/mfa/verify`` (plan#3452).
What is graded here: it refuses any database off this host's loopback before
the app is even built, so production's two-factor is never touched; it
replaces every piece of the copy's two-factor material, production's secret
among them; and a code of the dev key then signs the account in through the
real password and code forms; and the dev-app tools' one sign-in
(:func:`tools.dev_two_factor.sign_in_with_dev_key`) answers whether it got in,
and why not, at each page a sign-in can stop on.

The seed owner is made with two-factor (plan#3600), so a production copy's
row is the owner's own row made to hold production's material
(:func:`_copy_of_productions_two_factor`), and an account with no two-factor
row is the seed companion, whom her owner has just made.
"""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import app as app_package
import app.config as app_config
from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tests._two_factor_sign_in import (
    SEED_COMPANION_EMAIL,
    code_now,
    two_factor_row_of,
    two_factor_secret_of,
)
from tools import dev_two_factor

#: The dev key the tests write, made the way ``.env``'s is made, so no secret
#: is written into the repository.
_DEV_KEY = pyotp.random_base32()

#: A secret standing for production's, which the copy's row carries.
_PRODUCTIONS_KEY = pyotp.random_base32()


def _copy_of_productions_two_factor(user_id):
    """Make *user_id*'s two-factor row the one a production copy carries, committed.

    On, under another secret, with backup codes, a consumed replay step and a
    pending setup: every field the writer must replace or clear.  The account
    has two-factor, so its own row is the one rewritten.
    """
    row = two_factor_row_of(user_id)
    row.is_enabled = True
    row.totp_secret_encrypted = mfa_service.encrypt_secret(_PRODUCTIONS_KEY)
    row.backup_codes = mfa_service.hash_backup_codes(["aaaaaaaa"], rounds=4)
    row.confirmed_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    row.last_totp_timestep = int(time.time()) // 30 + 1000
    row.pending_secret_encrypted = mfa_service.encrypt_secret(_PRODUCTIONS_KEY)
    row.pending_secret_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.session.commit()


class TestRefuseUnlessLoopback:
    """The writer reaches only a database on this host's loopback."""

    @pytest.fixture(autouse=True)
    def _no_redirecting_variables(self, monkeypatch):
        """An environment that moves no connection off the URL's host, whatever ran this."""
        for name in ("PGHOSTADDR", "PGSERVICE"):
            monkeypatch.delenv(name, raising=False)

    @pytest.mark.parametrize("uri", [
        "postgresql://shekel_user:pw@127.0.0.1:5432/shekel_copy",
        "postgresql://shekel_user:pw@localhost:5432/shekel_copy",
        "postgresql://shekel_user:pw@[::1]:5432/shekel_copy",
    ])
    def test_a_loopback_database_passes(self, uri):
        """A database at a loopback address, where the dev database binds, passes."""
        assert dev_two_factor.refuse_unless_loopback(uri) is None

    @pytest.mark.parametrize("query", [
        "host=172.18.0.2", "hostaddr=172.18.0.2", "service=prod", "sslmode=disable",
    ])
    def test_a_url_with_a_query_is_refused_naming_its_keys(self, query):
        """A query is refused, whatever its keys: libpq reads each as a connection setting.

        ``host=172.18.0.2`` on a loopback URL hands psycopg that host, so the
        URL's own host would decide nothing.
        """
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.refuse_unless_loopback(
                f"postgresql://shekel_user:pw@127.0.0.1:5432/shekel_copy?{query}",
            )
        key = query.split("=", 1)[0]
        assert f"carries a query ({key})" in str(refused.value)

    @pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
    def test_a_redirecting_variable_is_refused_naming_it(self, monkeypatch, name):
        """``PGHOSTADDR`` or ``PGSERVICE`` in the environment is refused, on a loopback URL."""
        monkeypatch.setenv(name, "172.18.0.2" if name == "PGHOSTADDR" else "prod")
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.refuse_unless_loopback(
                "postgresql://shekel_user:pw@127.0.0.1:5432/shekel_copy",
            )
        assert f"Refused: {name} is set" in str(refused.value)

    def test_pghost_is_not_refused_as_the_url_names_its_host(self, monkeypatch):
        """``PGHOST`` passes: a URL that names its host overrides it."""
        monkeypatch.setenv("PGHOST", "172.18.0.2")
        assert dev_two_factor.refuse_unless_loopback(
            "postgresql://shekel_user:pw@127.0.0.1:5432/shekel_copy",
        ) is None

    @pytest.mark.parametrize("uri, host", [
        # Production's own address for its database, on its docker network
        # (/opt/docker/shekel/docker-compose.yml).
        ("postgresql://shekel_user:pw@db:5432/shekel", "db"),
        ("postgresql://shekel_user:pw@172.18.0.2:5432/shekel", "172.18.0.2"),
        ("postgresql://shekel_user:pw@shekel-prod-db:5432/shekel", "shekel-prod-db"),
        # DevConfig's fallback: a unix socket, no host.
        ("postgresql:///shekel", None),
    ])
    def test_any_other_database_is_refused_naming_its_host(self, uri, host):
        """A database off the loopback is refused, the message naming its host."""
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.refuse_unless_loopback(uri)
        assert f"at host {host!r}, not this host's loopback" in str(refused.value)

    def test_the_cli_refuses_before_building_the_app(self, monkeypatch):
        """``main`` judges the database before ``create_app``, whose startup writes DDL to it.

        The class patched is ``app.config.DevConfig`` as it is when the test
        runs, the one ``main`` imports when it runs.  A test that reloads
        ``app.config`` makes a new ``DevConfig``, so one bound when this file
        was imported is not always the one ``main`` reads (plan#3766).
        """
        monkeypatch.setenv(dev_two_factor.SECRET_VARIABLE, _DEV_KEY)
        monkeypatch.setattr(
            app_config.DevConfig, "SQLALCHEMY_DATABASE_URI",
            "postgresql://shekel_user:pw@db:5432/shekel",
        )

        def built_the_app(*_args, **_kwargs):
            """Stand in for ``create_app``: reaching it fails the test, naming why."""
            raise AssertionError("main built the app though its database is off the loopback")

        monkeypatch.setattr(app_package, "create_app", built_the_app)

        with pytest.raises(SystemExit) as refused:
            dev_two_factor.main([SEED_USER_EMAIL])

        assert "at host 'db', not this host's loopback" in str(refused.value)


class TestDevSecret:
    """The dev key comes from the environment, then the checkout's ``.env``."""

    def test_the_environment_is_read_first(self, monkeypatch, tmp_path):
        """A key in the process environment wins over one in ``.env``."""
        env_file = tmp_path / ".env"
        env_file.write_text(f"{dev_two_factor.SECRET_VARIABLE}={_PRODUCTIONS_KEY}\n")
        monkeypatch.setattr(dev_two_factor, "ENV_FILE", env_file)
        monkeypatch.setenv(dev_two_factor.SECRET_VARIABLE, _DEV_KEY)
        assert dev_two_factor.dev_secret() == _DEV_KEY

    def test_the_env_file_is_read_when_the_environment_has_none(
        self, monkeypatch, tmp_path,
    ):
        """With no key in the environment, ``.env``'s is read, its quotes stripped."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"# the dev key\n{dev_two_factor.SECRET_VARIABLE}='{_DEV_KEY}'\n",
        )
        monkeypatch.setattr(dev_two_factor, "ENV_FILE", env_file)
        monkeypatch.delenv(dev_two_factor.SECRET_VARIABLE, raising=False)
        assert dev_two_factor.dev_secret() == _DEV_KEY

    def test_no_key_anywhere_says_how_to_make_one(self, monkeypatch, tmp_path):
        """With neither source set, the exit names the variable and how to make a key."""
        monkeypatch.setattr(dev_two_factor, "ENV_FILE", tmp_path / "absent.env")
        monkeypatch.delenv(dev_two_factor.SECRET_VARIABLE, raising=False)
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.dev_secret()
        message = str(refused.value)
        assert f"{dev_two_factor.SECRET_VARIABLE} is not set" in message
        assert "pyotp.random_base32()" in message

    def test_a_key_that_is_not_base32_is_refused(self, monkeypatch):
        """A value no authenticator could hold is refused, not typed as a wrong code later."""
        monkeypatch.setenv(dev_two_factor.SECRET_VARIABLE, "not-base32-at-all!")
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.dev_secret()
        assert "is not a base32 secret" in str(refused.value)


class TestWriteDevKey:
    """The dev key becomes the account's two-factor, replacing a copy's."""

    def test_productions_two_factor_on_a_copy_is_replaced_whole(self, app, seed_user):
        """Production's secret, backup codes, replay step and pending setup all go.

        Afterwards the row is on, its secret decrypts to the dev key, and
        nothing of production's material is left to sign in with or to refuse
        the dev key's first code as a replay.
        """
        user_id = seed_user["user"].id
        _copy_of_productions_two_factor(user_id)
        before = datetime.now(timezone.utc)

        dev_two_factor.write_dev_key(SEED_USER_EMAIL, _DEV_KEY)

        row = two_factor_row_of(user_id)
        assert row.is_enabled is True
        assert mfa_service.decrypt_secret(row.totp_secret_encrypted) == _DEV_KEY
        assert row.backup_codes is None
        assert row.last_totp_timestep is None
        assert row.pending_secret_encrypted is None
        assert row.pending_secret_expires_at is None
        assert row.confirmed_at >= before

    def test_an_account_without_two_factor_gets_it(self, app, seed_companion):
        """An account with no two-factor row gets one, on, under the dev key.

        The seed companion: her owner has just made her, and she has none.
        """
        user_id = seed_companion["user"].id
        assert two_factor_row_of(user_id) is None

        dev_two_factor.write_dev_key(SEED_COMPANION_EMAIL, _DEV_KEY)

        row = two_factor_row_of(user_id)
        assert row.is_enabled is True
        assert mfa_service.decrypt_secret(row.totp_secret_encrypted) == _DEV_KEY

    def test_an_unknown_email_writes_nothing(self, app, seed_user):
        """An email no account has is refused by name, and no row is written or changed.

        The owner's own two-factor keeps its secret, and the table holds the
        rows it held.
        """
        user_id = seed_user["user"].id
        secret_before = two_factor_secret_of(user_id)
        rows_before = db.session.query(MfaConfig).count()

        with pytest.raises(SystemExit) as refused:
            dev_two_factor.write_dev_key("nobody@shekel.local", _DEV_KEY)

        assert "'nobody@shekel.local'" in str(refused.value)
        assert two_factor_secret_of(user_id) == secret_before
        assert db.session.query(MfaConfig).count() == rows_before

    def test_the_dev_keys_code_signs_the_copied_account_in(self, app, client, seed_user):
        """After the write, the password then a dev-key code reach the account's pages.

        Through the real forms: the password sends the browser to the code
        form, as it does for any account with two-factor, and a code of the
        dev key finishes the sign-in, though the row held another secret and
        a replay step ahead of the clock before the write.
        """
        nav_name = seed_user["user"].display_name
        _copy_of_productions_two_factor(seed_user["user"].id)
        dev_two_factor.write_dev_key(SEED_USER_EMAIL, _DEV_KEY)

        password = client.post("/login", data={
            "email": SEED_USER_EMAIL, "password": SEED_USER_PASSWORD,
        })
        assert password.status_code == 302
        assert urlsplit(password.headers["Location"]).path == "/mfa/verify"

        code = client.post("/mfa/verify", data={"totp_code": code_now(_DEV_KEY)})
        assert code.status_code == 302
        landed = urlsplit(code.headers["Location"]).path
        assert landed not in ("/login", "/mfa/verify", "/mfa/setup"), landed

        # A signed-in page: the navigation bar renders only for a signed-in
        # user, and names them (``base.html``).
        page = client.get("/settings")
        assert page.status_code == 200
        assert nav_name == "Test User"
        assert nav_name in page.get_data(as_text=True)


class _ScriptedPage:
    """A stand-in for a Playwright page: each click lands where the case says.

    It records what was typed into each field, and its ``wait_for_url``
    raises Playwright's own timeout when the page is not where the predicate
    asks, as a real page does once its wait runs out.
    """

    def __init__(self, *landings):
        """Start blank; the Nth click lands on the Nth of *landings* (paths)."""
        self.url = "about:blank"
        self.typed = {}
        self._landings = list(landings)

    def goto(self, url, wait_until=None):  # pylint: disable=unused-argument
        """Open *url*.

        Pylint: ``unused-argument`` -- ``wait_until`` is Playwright's keyword,
        which the sign-in passes; a page here has nothing to wait for.
        """
        self.url = url

    def fill(self, selector, value):
        """Type *value* into the field *selector* names."""
        self.typed[selector] = value

    def click(self, selector):  # pylint: disable=unused-argument
        """Submit the form: the page lands on the next landing.

        Pylint: ``unused-argument`` -- ``selector`` is Playwright's; every
        click here submits the page's one form.
        """
        self.url = _DEV_APP + self._landings.pop(0)

    def wait_for_url(self, predicate, timeout):  # pylint: disable=unused-argument
        """Return at once when *predicate* holds of the page, else time out.

        Pylint: ``unused-argument`` -- ``timeout`` is Playwright's keyword,
        which the sign-in passes; the page here never moves on its own.
        """
        if not predicate(self.url):
            raise PlaywrightTimeoutError(f"still at {self.url}")


#: The dev app's origin the stand-in page answers for.
_DEV_APP = "http://127.0.0.1:5000"


class TestSignInWithDevKey:
    """The dev-app tools' one sign-in: the password, then the dev key's code."""

    @pytest.fixture(autouse=True)
    def _dev_key(self, monkeypatch):
        """The dev key the code is typed from."""
        monkeypatch.setenv(dev_two_factor.SECRET_VARIABLE, _DEV_KEY)

    def _sign_in(self, page):
        """Sign *page* in as the seed owner through the function under test."""
        return dev_two_factor.sign_in_with_dev_key(
            page, _DEV_APP, SEED_USER_EMAIL, SEED_USER_PASSWORD, timeout_ms=1,
        )

    def test_the_password_then_the_dev_keys_code_signs_in(self):
        """Sent to the code form, it types the dev key's code; landing on a page, it is in."""
        page = _ScriptedPage("/mfa/verify", "/grid")

        assert self._sign_in(page) is True
        assert page.typed["#email"] == SEED_USER_EMAIL
        assert page.typed["#password"] == SEED_USER_PASSWORD
        assert pyotp.TOTP(_DEV_KEY).verify(page.typed["#totp_code"], valid_window=1)

    def test_a_refused_code_is_not_signed_in_and_says_why(self):
        """Left at the code form, it answers no, and the sentence names the dev key's fix."""
        page = _ScriptedPage("/mfa/verify", "/mfa/verify")

        assert self._sign_in(page) is False
        why = dev_two_factor.why_not_signed_in(page.url, SEED_USER_EMAIL)
        assert why.startswith("It stopped at /mfa/verify: the code was refused.")
        assert f"python -m tools.dev_two_factor {SEED_USER_EMAIL}" in why

    def test_a_refused_password_is_not_signed_in_and_says_why(self):
        """Left at the sign-in form, it answers no without typing a code."""
        page = _ScriptedPage("/login")

        assert self._sign_in(page) is False
        assert "#totp_code" not in page.typed
        why = dev_two_factor.why_not_signed_in(page.url, SEED_USER_EMAIL)
        assert why.startswith("It stopped at /login: the email or password was refused")

    def test_an_account_held_at_setup_is_not_signed_in_and_says_why(self):
        """An account without two-factor lands on setup: no, and the fix is the dev key."""
        page = _ScriptedPage("/mfa/setup")

        assert self._sign_in(page) is False
        assert "#totp_code" not in page.typed
        why = dev_two_factor.why_not_signed_in(page.url, SEED_USER_EMAIL)
        assert why.startswith("It stopped at /mfa/setup: the account has no two-factor")
        assert f"python -m tools.dev_two_factor {SEED_USER_EMAIL}" in why
