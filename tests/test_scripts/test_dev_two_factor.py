"""Tests for ``tools/dev_two_factor.py``: the dev copy's two-factor key, written and typed.

The writer makes a dev key the two-factor of one account on a dev copy of
production, so the dev-app tools sign in through ``/mfa/verify`` (plan#3452).
What is graded here: it refuses any database off this host's loopback before
the app is even built, so production's two-factor is never touched; it
replaces every piece of the copy's two-factor material, production's secret
among them; and a code of the dev key then signs the account in through the
real password and code forms.
"""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pyotp
import pytest

import app as app_package
from app.config import DevConfig
from app.extensions import db
from app.models.user import MfaConfig
from app.services import mfa_service
from tests._seed_owner_fixtures import SEED_USER_EMAIL, SEED_USER_PASSWORD
from tools import dev_two_factor

#: The dev key the tests write, made the way ``.env``'s is made, so no secret
#: is written into the repository.
_DEV_KEY = pyotp.random_base32()

#: A secret standing for production's, which the copy's row carries.
_PRODUCTIONS_KEY = pyotp.random_base32()


def _code_now(secret):
    """Return *secret*'s code at the instant ``time.time()`` reads, the verifier's clock."""
    return pyotp.TOTP(secret).at(int(time.time()))


def _row_of(user_id):
    """Return *user_id*'s two-factor row, read fresh."""
    db.session.expire_all()
    return db.session.query(MfaConfig).filter_by(user_id=user_id).one_or_none()


def _copy_of_productions_two_factor(user_id):
    """Give *user_id* the two-factor row a production copy carries, committed.

    On, under another secret, with backup codes, a consumed replay step and a
    pending setup: every field the writer must replace or clear.
    """
    db.session.add(MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(_PRODUCTIONS_KEY),
        backup_codes=mfa_service.hash_backup_codes(["aaaaaaaa"], rounds=4),
        confirmed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        last_totp_timestep=int(time.time()) // 30 + 1000,
        pending_secret_encrypted=mfa_service.encrypt_secret(_PRODUCTIONS_KEY),
        pending_secret_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    ))
    db.session.commit()


class TestRefuseUnlessLoopback:
    """The writer reaches only a database on this host's loopback."""

    @pytest.mark.parametrize("uri", [
        "postgresql://shekel_user:pw@127.0.0.1:5432/shekel_copy",
        "postgresql://shekel_user:pw@localhost:5432/shekel_copy",
        "postgresql://shekel_user:pw@[::1]:5432/shekel_copy",
    ])
    def test_a_loopback_database_passes(self, uri):
        """A database at a loopback address, where the dev database binds, passes."""
        assert dev_two_factor.refuse_unless_loopback(uri) is None

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
        """``main`` judges the database before ``create_app``, whose startup writes DDL to it."""
        monkeypatch.setenv(dev_two_factor.SECRET_VARIABLE, _DEV_KEY)
        monkeypatch.setattr(
            DevConfig, "SQLALCHEMY_DATABASE_URI",
            "postgresql://shekel_user:pw@db:5432/shekel",
        )
        built = []
        monkeypatch.setattr(app_package, "create_app", lambda *a, **k: built.append(a))

        with pytest.raises(SystemExit) as refused:
            dev_two_factor.main([SEED_USER_EMAIL])

        assert "at host 'db', not this host's loopback" in str(refused.value)
        assert not built, "the app was built before the database was judged"


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

        row = _row_of(user_id)
        assert row.is_enabled is True
        assert mfa_service.decrypt_secret(row.totp_secret_encrypted) == _DEV_KEY
        assert row.backup_codes is None
        assert row.last_totp_timestep is None
        assert row.pending_secret_encrypted is None
        assert row.pending_secret_expires_at is None
        assert row.confirmed_at >= before

    def test_an_account_without_two_factor_gets_it(self, app, seed_user):
        """An account with no two-factor row gets one, on, under the dev key."""
        user_id = seed_user["user"].id
        assert _row_of(user_id) is None

        dev_two_factor.write_dev_key(SEED_USER_EMAIL, _DEV_KEY)

        row = _row_of(user_id)
        assert row.is_enabled is True
        assert mfa_service.decrypt_secret(row.totp_secret_encrypted) == _DEV_KEY

    def test_an_unknown_email_writes_nothing(self, app, seed_user):
        """An email no account has is refused by name, and no row is written."""
        with pytest.raises(SystemExit) as refused:
            dev_two_factor.write_dev_key("nobody@shekel.local", _DEV_KEY)
        assert "'nobody@shekel.local'" in str(refused.value)
        assert _row_of(seed_user["user"].id) is None

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

        code = client.post("/mfa/verify", data={"totp_code": _code_now(_DEV_KEY)})
        assert code.status_code == 302
        landed = urlsplit(code.headers["Location"]).path
        assert landed not in ("/login", "/mfa/verify", "/mfa/setup"), landed

        # A signed-in page: the navigation bar renders only for a signed-in
        # user, and names them (``base.html``).
        page = client.get("/settings")
        assert page.status_code == 200
        assert nav_name == "Test User"
        assert nav_name in page.get_data(as_text=True)
