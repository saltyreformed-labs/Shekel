"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_bank_feed.py``, moved verbatim by ``tools/split``.
"""

import base64
import io
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

from app.services import bank_feed


# The claim URL a setup token decodes to, on Bridge's real host (the pin
# admits no other); the token part is what a sentence must never carry.
_CLAIM_URL = "https://bridge.simplefin.org/simplefin/claim/DEMO-TOKEN-1234"


_SETUP_TOKEN = base64.b64encode(_CLAIM_URL.encode()).decode()


# The access URL Bridge answers with: the userinfo IS the secret.
_SECRET = "s3cr3t-passw0rd"


_ACCESS_URL = f"https://alice:{_SECRET}@bridge.simplefin.org/simplefin"


# Bridge's ids as measured 2026-09-18: ``ACT-`` + a uuid.
_CHECKING = "ACT-125e0df6-8f88-4a46-888a-37e0342ed307"


_SHARE = "ACT-d361df65-8f21-4be9-812e-8f270dcc7f5b"


_MORTGAGE = "ACT-c5800d94-cf34-4015-8697-0586f228bcbc"


def _listing_body(accounts=None, errlist=(), api_message=()):
    """Return the v2 listing shape Bridge answered on 2026-09-18."""
    if accounts is None:
        accounts = [
            {"id": _CHECKING, "name": "Checking (3820)", "currency": "USD",
             "balance": "2073.40", "available-balance": "2073.40",
             "balance-date": 1789775211, "transactions": [], "holdings": []},
            {"id": _SHARE, "name": "Share (9361)", "currency": "USD",
             "balance": "25.80", "available-balance": "25.80",
             "balance-date": 1789775211, "transactions": [], "holdings": []},
            {"id": _MORTGAGE, "name": "Mortgage Loan (6190)",
             "currency": "USD", "balance": "-176719.77",
             "available-balance": "0.00", "balance-date": 1789775211,
             "transactions": [], "holdings": []},
        ]
    return {
        "accounts": accounts,
        "connections": [],
        "errlist": list(errlist),
        "x-api-message": list(api_message),
    }


class _FakeResponse:
    """The subset of ``requests.Response`` the service reads.

    ``raise_for_status`` raises the ``HTTPError`` the real one raises, with
    the real one's text -- ``"<status> Client Error: <reason> for url:
    <url>"`` -- and the response attached, because that text is exactly the
    thing the service must never repeat.
    """

    def __init__(self, url, *, status_code=200, text="", json_body=None):
        self.url = url
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def raise_for_status(self):
        """Raise as ``requests`` does, URL and all."""
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error: Refused for url: "
                f"{self.url}",
                response=self,
            )

    def json(self):
        """Return the body, or raise as ``requests`` does on a non-JSON body."""
        if self._json is None:
            raise requests.JSONDecodeError("Expecting value", self.text, 0)
        return self._json


class _Bridge:
    """A spy standing in for Bridge at ``requests.post`` and ``requests.get``.

    Records every call's URL, params and timeout so a test can assert what
    was asked, and answers from a queue so a test can script Bridge.
    """

    def __init__(self, monkeypatch):
        self.posts = []
        self.gets = []
        self.post_answer = _FakeResponse(_CLAIM_URL, text=_ACCESS_URL)
        self.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", json_body=_listing_body(),
        )
        self.post_raises = None
        self.get_raises = None
        monkeypatch.setattr(requests, "post", self._post)
        monkeypatch.setattr(requests, "get", self._get)

    def _post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_raises is not None:
            raise self.post_raises
        return self.post_answer

    def _get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        if self.get_raises is not None:
            raise self.get_raises
        return self.get_answer


#: ``requests.Session.send`` as imported, before the net below replaces it:
#: the adapter-level cases put it back so ``requests`` itself runs down to a
#: fake ``HTTPAdapter.send``, which is the only place "what ``requests`` does
#: after a 3xx" can be seen.
_REAL_SESSION_SEND = requests.Session.send


def block_real_requests(monkeypatch) -> None:
    """Make any real ``requests`` call in this test fail loudly.

    The ONE spelling of the net both feed test modules hang on their autouse
    fixture (module docstring).  ``requests.post`` and ``requests.get`` both
    end in ``requests.Session.send``; the :class:`_Bridge` spy replaces the
    two above it, so a stubbed call never reaches this and an unstubbed one
    always does.  The message names the method and the host only: the
    prepared URL of a listing carries the credential.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.
    """
    def _refuse(self, request, **kwargs):
        raise AssertionError(
            f"a test let a real request out: {request.method} to "
            f"{urlsplit(request.url).hostname}; stub Bridge with the "
            f"`bridge` fixture"
        )
    monkeypatch.setattr(requests.Session, "send", _refuse)


def _bridge_at_the_adapter(monkeypatch, *, status, location):
    """Fake Bridge one layer DOWN, at ``HTTPAdapter.send``, for one test.

    The :class:`_Bridge` spy replaces ``requests.post``/``get`` and so cannot
    see what ``requests`` does after a ``3xx``; this restores the real
    ``Session.send`` (the net sits there) and answers every adapter send
    with *status* and a ``Location`` of *location*, so ``requests``' own
    redirect handling runs and every request it would make is counted.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.
        status: The status every send is answered with.
        location: The ``Location`` header, as ``http.client`` would hand it
            over (a ``str``).

    Returns:
        The list every send appends ``(method, host)`` to.
    """
    sent = []

    def _send(self, request, **kwargs):
        sent.append((request.method, urlsplit(request.url).hostname))
        answer = requests.Response()
        answer.status_code = status
        answer.headers = CaseInsensitiveDict({"Location": location})
        answer.raw = io.BytesIO(b"")
        answer.url = request.url
        answer.request = request
        answer.reason = "Found"
        return answer

    monkeypatch.setattr(requests.Session, "send", _REAL_SESSION_SEND)
    monkeypatch.setattr(HTTPAdapter, "send", _send)
    return sent


def _connect(seed_user, bridge):
    """Claim the demo token through the real door; return the feed."""
    return bank_feed.claim_feed(seed_user["user"].id, _SETUP_TOKEN)


def _no_secret_in(text: str) -> None:
    """Assert the credential's password is nowhere in *text*."""
    assert _SECRET not in text, text
