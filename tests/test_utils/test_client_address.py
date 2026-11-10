"""
Shekel Budget App -- Tests for the client's real address.

Behind nginx, ``REMOTE_ADDR`` is nginx.  ``believe_forwarded_address_from``
makes ``request.remote_addr`` the client for a direct caller on the one trusted
list, and for no other caller.

``NGINX`` is an address on ``TestConfig.FORWARDED_ALLOW_IPS``, production's
bridge.  ``nginx_forwarded`` spells the header the way nginx writes it:
``$proxy_add_x_forwarded_for`` appends the client to whatever arrived.
"""

import os
import re
import secrets
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import request
from flask_login import login_user

from app import CONFIG_MAP, create_app
from app.extensions import db
from app.models.known_device import KnownDevice
from app.services import known_device_service
from app.rate_limits import rate_limit_key
from app.utils.client_address import (
    believe_forwarded_address_from,
    parse_trusted_networks,
)
from tests._test_helpers import capture_sql_statements

NGINX = "172.32.0.4"
CLIENT = "8.8.8.8"
FORGED = "6.6.6.6"
NOT_ON_THE_LIST = "172.33.0.9"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def nginx_forwarded(client: str, arrived_with: str | None = None) -> str:
    """Return ``X-Forwarded-For`` as nginx writes it for *client*.

    Args:
        client: The address nginx saw, which it appends.
        arrived_with: The header the request arrived with, or ``None``.

    Returns:
        The header's value.
    """
    return client if arrived_with is None else f"{arrived_with}, {client}"


def _environ_seen_by_the_app(environ: dict[str, object], allow_list: str) -> dict:
    """Run *environ* through the wrapper and return what the app received.

    Args:
        environ: The WSGI environment as gunicorn would build it.
        allow_list: The value of ``FORWARDED_ALLOW_IPS``.

    Returns:
        The environment the wrapped application was called with.
    """
    seen = {}

    def application(wsgi_environ, _start_response):
        seen.update(wsgi_environ)
        return [b""]

    wrapped = believe_forwarded_address_from(
        application, parse_trusted_networks(allow_list),
    )
    wrapped(environ, lambda *args: None)
    return seen


class TestParseTrustedNetworks:
    """The list is read once at start, and what it cannot read refuses start."""

    @pytest.mark.parametrize("allow_list", ["", "   ", ",", " , "])
    def test_empty_trusts_nobody(self, allow_list):
        """No entries trusts no caller, which is not an error."""
        assert parse_trusted_networks(allow_list) == ()

    @pytest.mark.parametrize(("compose_file", "bridge"), [
        ("docker-compose.yml", "172.31.0.0/24"),
        ("deploy/docker-compose.prod.yml", "172.32.0.0/24"),
    ])
    def test_reads_what_each_compose_file_sets(self, compose_file, bridge):
        """Each compose file's value parses, since a bad one refuses the app's start."""
        text = (_REPO_ROOT / compose_file).read_text(encoding="utf-8")
        setting = re.search(
            r"^\s*FORWARDED_ALLOW_IPS:\s*(\S+)\s*$", text, re.MULTILINE,
        )
        assert setting is not None, f"{compose_file} sets no FORWARDED_ALLOW_IPS"
        # ``docker-compose.yml`` spells its value as a default an env can override.
        value = re.sub(
            r"^\$\{FORWARDED_ALLOW_IPS:-(.*)\}$", r"\1", setting.group(1),
        )
        assert [str(n) for n in parse_trusted_networks(value)] == [bridge]

    def test_reads_addresses_and_spaces(self):
        """A bare address is a network of one, and spaces after commas are fine."""
        assert [str(n) for n in parse_trusted_networks("127.0.0.1, ::1")] == [
            "127.0.0.1/32", "::1/128",
        ]

    @pytest.mark.parametrize(
        "entry", ["*", "nginx", "172.32.0.4/24", "172.32.0.0/99"],
    )
    def test_a_malformed_entry_is_refused_and_named(self, entry):
        """Wildcard, name, host bits and bad prefix all refuse, naming the entry."""
        with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS") as refused:
            parse_trusted_networks(f"10.0.0.1, {entry}")
        assert repr(entry) in str(refused.value)


class TestWhoseHeaderIsBelieved:
    """Only a direct caller on the list changes the address, and only to the right entry."""

    def test_a_trusted_callers_rightmost_entry_is_the_client(self):
        """Nginx's own entry becomes the address."""
        seen = _environ_seen_by_the_app(
            {
                "REMOTE_ADDR": NGINX,
                "HTTP_X_FORWARDED_FOR": nginx_forwarded(CLIENT),
            },
            "172.32.0.0/24",
        )
        assert seen["REMOTE_ADDR"] == CLIENT

    def test_a_client_supplied_leftmost_entry_is_ignored(self):
        """What the client sent sits left of nginx's entry and changes nothing."""
        seen = _environ_seen_by_the_app(
            {
                "REMOTE_ADDR": NGINX,
                "HTTP_X_FORWARDED_FOR": nginx_forwarded(CLIENT, FORGED),
            },
            "172.32.0.0/24",
        )
        assert seen["REMOTE_ADDR"] == CLIENT

    def test_an_untrusted_callers_header_is_ignored(self):
        """A caller not on the list keeps its own address, whatever it claims."""
        seen = _environ_seen_by_the_app(
            {"REMOTE_ADDR": NOT_ON_THE_LIST, "HTTP_X_FORWARDED_FOR": CLIENT},
            "172.32.0.0/24",
        )
        assert seen["REMOTE_ADDR"] == NOT_ON_THE_LIST

    def test_an_empty_list_believes_no_caller(self):
        """A process with no trusted list ignores the header from everyone."""
        seen = _environ_seen_by_the_app(
            {"REMOTE_ADDR": NGINX, "HTTP_X_FORWARDED_FOR": CLIENT}, "",
        )
        assert seen["REMOTE_ADDR"] == NGINX

    def test_a_trusted_caller_with_no_header_keeps_its_address(self):
        """A health check from the bridge has no header, and is itself."""
        seen = _environ_seen_by_the_app({"REMOTE_ADDR": NGINX}, "172.32.0.0/24")
        assert seen["REMOTE_ADDR"] == NGINX

    def test_a_trusted_caller_on_a_dual_stack_socket_is_still_trusted(self):
        """``::ffff:172.32.0.4`` is the caller on the list, not a stranger."""
        seen = _environ_seen_by_the_app(
            {"REMOTE_ADDR": f"::ffff:{NGINX}", "HTTP_X_FORWARDED_FOR": CLIENT},
            "172.32.0.0/24",
        )
        assert seen["REMOTE_ADDR"] == CLIENT

    @pytest.mark.parametrize("peer", ["", "not-an-address"])
    def test_a_caller_with_no_readable_address_is_not_trusted(self, peer):
        """A unix socket gives no address, which is on no list."""
        seen = _environ_seen_by_the_app(
            {"REMOTE_ADDR": peer, "HTTP_X_FORWARDED_FOR": CLIENT},
            "172.32.0.0/24",
        )
        assert seen["REMOTE_ADDR"] == peer

    def test_the_scheme_stays_gunicorns(self):
        """Only the address is taken: gunicorn owns the scheme from the same list."""
        seen = _environ_seen_by_the_app(
            {
                "REMOTE_ADDR": NGINX,
                "HTTP_X_FORWARDED_FOR": CLIENT,
                "HTTP_X_FORWARDED_PROTO": "https",
                "wsgi.url_scheme": "http",
            },
            "172.32.0.0/24",
        )
        assert seen["wsgi.url_scheme"] == "http"


class TestWhatALimitCountsASignedOutVisitorAs:
    """The key a signed-out request counts against, per kind of address."""

    @pytest.mark.parametrize(
        "address, expected",
        [
            pytest.param("8.8.8.8", "ip:8.8.8.8", id="public-ipv4-alone"),
            pytest.param("100.64.0.5", "ip:100.64.0.5", id="tailnet-ipv4-alone"),
            pytest.param("10.10.100.2", "ip:10.10.100.2", id="lan-ipv4-alone"),
            pytest.param(
                "2606:4700:1234:5678:aaaa:bbbb:cccc:dddd",
                "ip:2606:4700:1234:5678::/64",
                id="public-ipv6-by-its-64",
            ),
            pytest.param(
                "2606:4700:1234:5678::1",
                "ip:2606:4700:1234:5678::/64",
                id="same-64-same-key",
            ),
            pytest.param(
                "2606:4700:1234:5679::1",
                "ip:2606:4700:1234:5679::/64",
                id="next-64-next-key",
            ),
            pytest.param(
                "fd7a:115c:a1e0:ab12:4843:cd96:6258:ac6c",
                "ip:fd7a:115c:a1e0:ab12:4843:cd96:6258:ac6c",
                id="tailnet-ipv6-alone",
            ),
            pytest.param("::ffff:8.8.8.8", "ip:8.8.8.8", id="mapped-is-its-ipv4"),
            pytest.param("not-an-address", "ip:not-an-address", id="unparseable"),
        ],
    )
    def test_the_key(self, app, address, expected):
        """Each kind of address counts as the test's name says.

        The mapped case is the trap: this Python calls ``::ffff:8.8.8.8``
        global, so a /64 rule reading it unconverted would put every such
        visitor in one count, ``::/64``.
        """
        with app.test_request_context(environ_base={"REMOTE_ADDR": address}):
            assert rate_limit_key() == expected


class TestWhatALimitCountsASignedInPersonAs:
    """A signed-in request counts against its known browser's own budget, else its account's.

    Wherever it arrives from (ruling plan#3108).  The browser's budget is its
    record's, so only a cookie that names a live record of the signed-in
    user buys one.
    """

    def test_the_key_is_the_account_whatever_the_address(self, app, seed_user):
        """Two addresses, one signed-in account, one key: its id.

        Read straight from the key function because a real session cannot move:
        strong session protection signs out a session whose address changes.
        """
        user = seed_user["user"]
        for address in ("8.8.8.8", "9.9.9.9"):
            with app.test_request_context(environ_base={"REMOTE_ADDR": address}):
                login_user(user)
                assert rate_limit_key() == f"user:{user.id}"

    def test_a_known_browser_counts_as_its_record(self, app, seed_user):
        """A cookie naming the user's live record: that record's own key."""
        user = seed_user["user"]
        token, device_id = _a_known_browser(user.id)

        with _signed_in_request(app, user, token):
            assert rate_limit_key() == f"known-device:{device_id}"

    def test_a_made_up_cookie_counts_as_the_account(self, app, seed_user):
        """A cookie naming no record, beside the user's live one: the account's key."""
        user = seed_user["user"]
        _a_known_browser(user.id)

        with _signed_in_request(app, user, secrets.token_urlsafe(32)):
            assert rate_limit_key() == f"user:{user.id}"

    def test_one_request_reads_the_record_once(self, app, seed_user):
        """Asked by two limits, as the app's defaults are, the record is read once."""
        user = seed_user["user"]
        token, device_id = _a_known_browser(user.id)

        with _signed_in_request(app, user, token):
            keys, statements = capture_sql_statements(
                lambda: (rate_limit_key(), rate_limit_key()),
            )

        assert keys == (f"known-device:{device_id}",) * 2
        assert len([
            statement for statement, _ in statements
            if "known_devices" in statement
        ]) == 1, statements

    def test_a_shared_g_is_not_answered_for_another_cookie(self, app, seed_user):
        """Two request contexts on the test's one ``g``: each answered for its own cookie.

        A test's app context reuses one ``g`` for every request context it
        pushes, so a read kept by user alone would answer the second request
        with the first one's record.
        """
        user = seed_user["user"]
        token, device_id = _a_known_browser(user.id)

        with _signed_in_request(app, user, token):
            assert rate_limit_key() == f"known-device:{device_id}"
        with _signed_in_request(app, user, secrets.token_urlsafe(32)):
            assert rate_limit_key() == f"user:{user.id}"


def _a_known_browser(user_id):
    """Record a finished sign-in for *user_id* on a new browser, committed.

    Returns:
        ``(token, device_id)``: the browser's cookie value and its record's
        id, read from the table rather than through the key's own lookup.
    """
    token = known_device_service.record_sign_in(
        user_id, None, "", now=datetime.now(timezone.utc),
    )
    db.session.commit()
    device_id = db.session.query(KnownDevice).filter_by(user_id=user_id).one().id
    return token, device_id


@contextmanager
def _signed_in_request(app, user, token):
    """Push a request from *user*'s session whose browser sends *token* as its device cookie.

    Yields:
        Nothing; the request context is current inside the block.
    """
    with app.test_request_context(
        environ_base={"REMOTE_ADDR": CLIENT},
        headers={"Cookie": f"shekel_device_{user.id}={token}"},
    ):
        login_user(user)
        yield


class TestTheFactoryInstallsIt:
    """``create_app`` wires the list from configuration into every request."""

    @pytest.fixture
    def echo_client(self, app):
        """A client of a fresh app whose route answers with the address it saw.

        Yields:
            The test client.
        """
        # pylint: disable=unused-argument
        echo_app = create_app("testing")
        echo_app.config["LOGIN_DISABLED"] = True

        @echo_app.route("/test-who-is-asking")
        def who_is_asking():
            """Answer with what the rate limits and the log lines read."""
            return request.remote_addr

        yield echo_app.test_client()
        with echo_app.app_context():
            db.engine.dispose()

    def test_nginxs_entry_is_request_remote_addr(self, echo_client):
        """The whole way in: production's peer and headers give the client."""
        response = echo_client.get(
            "/test-who-is-asking",
            environ_base={"REMOTE_ADDR": NGINX},
            headers={"X-Forwarded-For": nginx_forwarded(CLIENT, FORGED)},
        )
        assert response.get_data(as_text=True) == CLIENT

    def test_a_direct_callers_header_is_not_request_remote_addr(self, echo_client):
        """The whole way in: the test client's own address is on no list."""
        response = echo_client.get(
            "/test-who-is-asking", headers={"X-Forwarded-For": FORGED},
        )
        assert response.get_data(as_text=True) == "127.0.0.1"

    def test_a_malformed_list_refuses_to_build_the_app(self, app, monkeypatch):
        """A typo in the trusted list stops the start, not the first request."""
        # pylint: disable=unused-argument
        monkeypatch.setattr(CONFIG_MAP["testing"], "FORWARDED_ALLOW_IPS", "*")
        with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
            create_app("testing")

    def test_production_reads_the_list_from_the_environment(self):
        """The setting a real process reads is the variable the compose files set.

        ``TestConfig`` pins its own value, so nothing else in the suite reads
        the environment: a misspelling here would leave production trusting
        nobody, every client counted as nginx, with every other test green.
        """
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from app.config import BaseConfig; "
                "print(BaseConfig.FORWARDED_ALLOW_IPS)",
            ],
            cwd=_REPO_ROOT,
            env={**os.environ, "FORWARDED_ALLOW_IPS": "172.32.0.0/24"},
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "172.32.0.0/24"
