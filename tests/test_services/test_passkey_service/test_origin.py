"""The site's origin, and the relying party id derived from it.

A ceremony's client data names its origin as a browser writes it, and the
service compares the two exactly, so a configured origin in any other form
would refuse every passkey.  Each form a browser never writes is refused.
"""

from importlib import reload

import pytest

from app import config as config_module
from app.config import TestConfig
from app.services.passkey_service import relying_party_id


@pytest.mark.parametrize("origin, host", [
    ("https://shekel.test", "shekel.test"),
    ("https://shekel.test:8443", "shekel.test"),
    ("http://localhost", "localhost"),
    ("http://localhost:5000", "localhost"),
])
def test_an_origin_as_a_browser_writes_it_gives_its_host(origin, host):
    """The relying party id is the host, never the port."""
    assert relying_party_id(origin) == host


@pytest.mark.parametrize("origin", [
    "https://shekel.test/",        # a trailing slash
    "https://shekel.test/login",   # a path
    "https://shekel.test?x=1",     # a query
    "https://Shekel.test",         # upper case
    "https://shekel.test:443",     # https's own port, which a browser leaves out
    "http://localhost:80",         # http's own port
    "http://shekel.test",          # insecure, and not localhost
    "https://user@shekel.test",    # a user name
    "https://shekel.test:port",    # a port that is not a number
    "shekel.test",                 # no scheme
    "https://bücher.example",      # a browser writes it as xn--bcher-kva.example
    "https://[abc",                # a bracket that holds no address
    "",
])
def test_a_form_no_browser_writes_is_refused_by_name(origin):
    """The message names the setting and the value."""
    with pytest.raises(ValueError, match="PASSKEY_ORIGIN must be an origin") as caught:
        relying_party_id(origin)
    assert repr(origin) in str(caught.value)


def test_the_suite_signs_for_its_own_pinned_origin():
    """TestConfig pins the origin the software authenticator signs for."""
    assert TestConfig.PASSKEY_ORIGIN == "https://shekel.test"
    assert relying_party_id(TestConfig.PASSKEY_ORIGIN) == "shekel.test"


def test_host_flask_run_defaults_to_the_address_it_serves(monkeypatch):
    """With no PASSKEY_ORIGIN in the environment, DevConfig names http://localhost:5000.

    ``app.config`` loads ``.env`` when it is imported, which would put back a
    PASSKEY_ORIGIN a checkout's ``.env`` names, so the reload loads none.
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("PASSKEY_ORIGIN", raising=False)
    try:
        assert reload(config_module).DevConfig.PASSKEY_ORIGIN == "http://localhost:5000"
    finally:
        reload(config_module)


def test_the_environment_names_the_origin_for_dev_and_production(monkeypatch):
    """An origin in the environment is DevConfig's and ProdConfig's; TestConfig keeps its own."""
    monkeypatch.setenv("PASSKEY_ORIGIN", "https://budget.example.com")
    try:
        reloaded = reload(config_module)
        assert reloaded.DevConfig.PASSKEY_ORIGIN == "https://budget.example.com"
        assert reloaded.ProdConfig.PASSKEY_ORIGIN == "https://budget.example.com"
        assert reloaded.TestConfig.PASSKEY_ORIGIN == "https://shekel.test"
    finally:
        monkeypatch.delenv("PASSKEY_ORIGIN", raising=False)
        reload(config_module)
