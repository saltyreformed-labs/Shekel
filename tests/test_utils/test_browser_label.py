"""A browser's name, read from its User-Agent (plan#2173, ruling plan#2406).

Settings > Security names each trusted device by its browser.  Every case is
a real header of its browser's current shape, and the ones that matter are
the ones whose header claims an ancestor's name too: Edge, Opera and Samsung
Internet carry ``Chrome/``, every Chromium browser carries ``Safari/``, and an
Android phone carries ``Linux``.  Each of those would read as the ancestor if
the module's tables were out of order.
"""

import pytest

from app.utils.browser_label import browser_label

_FIREFOX_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
)
_CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_EDGE_WINDOWS = f"{_CHROME_WINDOWS} Edg/130.0.0.0"
_OPERA_WINDOWS = f"{_CHROME_WINDOWS} OPR/115.0.0.0"
_SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)
_SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 "
    "Safari/604.1"
)
_CHROME_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.6723.90 "
    "Mobile/15E148 Safari/604.1"
)
_FIREFOX_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) FxiOS/131.0 Mobile/15E148 Safari/605.1.15"
)
_CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)
_SAMSUNG_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile "
    "Safari/537.36"
)
_CHROME_CHROMEOS = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (_FIREFOX_LINUX, "Firefox on Linux"),
        (_CHROME_WINDOWS, "Chrome on Windows"),
        (_EDGE_WINDOWS, "Edge on Windows"),
        (_OPERA_WINDOWS, "Opera on Windows"),
        (_SAFARI_MAC, "Safari on Mac"),
        (_SAFARI_IPHONE, "Safari on iPhone"),
        (_CHROME_IPHONE, "Chrome on iPhone"),
        (_FIREFOX_IPAD, "Firefox on iPad"),
        (_CHROME_ANDROID, "Chrome on Android"),
        (_SAMSUNG_ANDROID, "Samsung Internet on Android"),
        (_CHROME_CHROMEOS, "Chrome on ChromeOS"),
    ],
)
def test_a_known_browser_is_named_with_its_system(user_agent, label):
    """Each real header reads as its own browser and system, not an ancestor's."""
    assert browser_label(user_agent) == label


def test_no_header_is_an_unknown_browser():
    """A browser that sent no User-Agent is stored as ``""``: unknown."""
    assert browser_label("") == "Unknown browser"


def test_a_header_naming_no_browser_is_unknown():
    """A client that is no browser this module knows reads as unknown."""
    assert browser_label("curl/8.10.1") == "Unknown browser"


def test_an_unknown_browser_still_names_its_system():
    """The system is read on its own, so a strange browser still says where."""
    assert browser_label("SomeClient/1.0 (Windows NT 10.0)") == (
        "Unknown browser on Windows"
    )


def test_a_known_browser_with_no_system_is_named_alone():
    """No system token: the browser's name with nothing after it."""
    assert browser_label("Firefox/131.0") == "Firefox"
