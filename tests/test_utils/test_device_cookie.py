"""The device cookie's names: every one the browser sent, read as the app writes them.

Plan#2772, ruling **plan#3130**.  ``/login`` asks of every device cookie the
browser sends, before the account is found
(:func:`app.utils.device_cookie.device_tokens`), so how a name is read decides
which cookies are looked up: only a name the app writes counts, each user has
one, and an id no ``id`` column holds never reaches the query, whose bind cast
would raise on it (:data:`app.utils.digit_strings.MAX_INTEGER_COLUMN`).
"""

import pytest

from app.utils.device_cookie import device_token, device_tokens
from app.utils.digit_strings import MAX_INTEGER_COLUMN


def _tokens(app, cookie_header):
    """Return :func:`device_tokens` for a request whose Cookie header is *cookie_header*."""
    with app.test_request_context(headers={"Cookie": cookie_header}):
        return device_tokens()


class TestTheNames:
    """Which of the cookies a browser sent are device cookies, and whose."""

    def test_every_device_cookie_is_read_by_its_users_id(self, app):
        """Two people's device cookies beside an unrelated one: the two, each by its id."""
        assert _tokens(app, "shekel_device_7=a; shekel_device_12=b; other=c") == {
            7: "a", 12: "b",
        }

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param("shekel_device_07", id="a-second-spelling-of-7"),
            pytest.param("shekel_device_0", id="an-id-no-row-has"),
            pytest.param("shekel_device_-7", id="a-sign"),
            pytest.param("shekel_device_+7", id="a-plus"),
            pytest.param("shekel_device_7_0", id="a-separator-int-reads"),
            pytest.param("shekel_device_²", id="a-superscript-digit"),
            pytest.param("shekel_device_", id="no-id"),
            pytest.param("shekel_device_7x", id="not-all-digits"),
            pytest.param("xshekel_device_7", id="a-longer-name"),
            pytest.param("__Host-shekel_device_7", id="the-secure-prefix"),
            pytest.param(f"shekel_device_{MAX_INTEGER_COLUMN + 1}", id="past-the-column"),
        ],
    )
    def test_a_name_the_app_never_writes_is_not_read(self, app, name):
        """A name :func:`~app.utils.device_cookie.set_device_cookie` never writes here: no cookie.

        The ``__Host-`` name is one only where the session cookie is Secure,
        which the test configuration's is not.
        """
        assert _tokens(app, f"{name}=a") == {}

    def test_the_largest_id_a_column_holds_is_read(self, app):
        """The ceiling itself is an id, so its cookie is read."""
        assert _tokens(app, f"shekel_device_{MAX_INTEGER_COLUMN}=a") == {
            MAX_INTEGER_COLUMN: "a",
        }

    def test_a_secure_configuration_reads_only_its_prefixed_names(self, app, monkeypatch):
        """With the session cookie Secure, only ``__Host-`` names are device cookies."""
        monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", True)

        assert _tokens(app, "__Host-shekel_device_7=a; shekel_device_8=b") == {7: "a"}

    def test_a_name_sent_twice_counts_with_its_first_value(self, app):
        """Both readers take the first of a name's two values."""
        header = "shekel_device_7=first; shekel_device_7=second"
        with app.test_request_context(headers={"Cookie": header}):
            assert device_tokens() == {7: "first"}
            assert device_token(7) == "first"
