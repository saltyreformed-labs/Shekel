"""The bank feed's doors: claim, list, map, disconnect (plan step X-f6b-2, leaf 3c).

Rulings **R-BI12**, **R-BI26**, **R-BI27**, **R-BI28**, **R-BI29**.  Bridge is a fake at
``requests.post`` / ``requests.get``, the shape ``test_auth_service``'s HIBP
tests stub.  Every fake URL names Bridge's REAL host, because the host pin
(R-BI28) refuses any other; so a forgotten stub would reach the real Bridge,
and :func:`block_real_requests` -- autouse here and in the route module --
turns that into a failure at ``requests.Session.send``, the one call every
``requests.post``/``get`` goes through and the spy sits above.  (Until the
pin, the fakes lived under RFC 2606's ``.invalid`` and a forgotten stub
failed at DNS instead.)

**The three claims these tests exist to grade, because the code's own words
cannot**:

* **a setup token is never burned for nothing** -- the claim refuses an
  unreadable paste, a foreign host and a standing feed BEFORE the request,
  and the spy counts zero calls on each;
* **the credential never reaches a sentence or a record** -- the fake
  raises the ``HTTPError`` ``requests`` really raises, its text carrying
  the URL's userinfo, and every refusal sentence and every captured log
  record is asserted free of it;
* **the host that is checked is the host that is reached** -- the pin is
  fed the URL whose host ``urlsplit`` and ``requests`` read differently, and
  refuses it; and no redirect is followed (R-BI29): every call carries
  ``allow_redirects=False`` and a ``3xx`` answer is a refusal of its own.
"""

import base64
import io
import traceback
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

from app import ref_cache
from app.enums import StatementSourceEnum
from app.exceptions import (
    BridgeHostRefused,
    BridgeRefused,
    FeedAlreadyConnected,
    FeedUnreadable,
    MappingRefused,
    NoFeedConnected,
    SetupTokenUnreadable,
)
from app.extensions import db
from app.models.bank_feed import BankFeed
from app.models.statement_import import AccountExternalIdentity
from app.services import bank_feed
from app.services.statement_import import record_identity
from app.utils.field_encryption import decrypt_secret
from tests._test_helpers import create_loan_account
from tests.test_services.test_statement_import.test_record import (
    _second_account,
)

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


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch):
    """Every test in this module runs behind :func:`block_real_requests`."""
    block_real_requests(monkeypatch)


@pytest.fixture
def bridge(monkeypatch):
    """Bridge, faked and spied."""
    return _Bridge(monkeypatch)


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


class TestTheNet:
    """A request no fixture stubbed fails here, not at Bridge."""

    def test_an_unstubbed_request_fails_before_leaving(self):
        """No ``bridge`` fixture, so ``requests.get`` reaches the net: the
        failure names the method and the host and not the credential the
        URL carries.  The one case that runs the net's own message."""
        with pytest.raises(AssertionError) as excinfo:
            requests.get(f"{_ACCESS_URL}/accounts", timeout=1)

        message = str(excinfo.value)
        assert "GET to bridge.simplefin.org" in message
        _no_secret_in(message)


class TestTheClaimNeverBurnsATokenForNothing:
    """Every refusal the claim can make before the request, it makes before."""

    def test_an_unreadable_paste_is_refused_before_any_request(
        self, app, db, seed_user, bridge,
    ):
        """Not base64 at all: refused, and the spy counted no POST."""
        with pytest.raises(SetupTokenUnreadable):
            bank_feed.claim_feed(seed_user["user"].id, "not a token!!")

        assert bridge.posts == []
        assert db.session.query(BankFeed).count() == 0

    def test_a_token_decoding_to_a_plain_http_url_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """Bridge issues https claim URLs; anything else is not a token,
        and claiming it would send the request in the clear.  On Bridge's
        own host, so it is the SCHEME this case refuses on."""
        token = base64.b64encode(b"http://bridge.simplefin.org/claim/x").decode()

        with pytest.raises(SetupTokenUnreadable):
            bank_feed.claim_feed(seed_user["user"].id, token)

        assert bridge.posts == []

    def test_a_token_decoding_to_a_malformed_host_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """``urlsplit`` raises ``ValueError`` on ``https://[x`` (measured);
        it is the same unreadable paste, refused with no request."""
        token = base64.b64encode(b"https://[x").decode()

        with pytest.raises(SetupTokenUnreadable):
            bank_feed.claim_feed(seed_user["user"].id, token)

        assert bridge.posts == []

    def test_a_standing_feed_refuses_the_claim_BEFORE_the_request(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the order: the second token is refused with
        zero calls, where a unique-key refusal after the POST would have
        consumed it at Bridge."""
        _connect(seed_user, bridge)
        db.session.flush()
        assert len(bridge.posts) == 1

        with pytest.raises(FeedAlreadyConnected):
            bank_feed.claim_feed(seed_user["user"].id, _SETUP_TOKEN)

        assert len(bridge.posts) == 1
        assert db.session.query(BankFeed).count() == 1

    def test_whitespace_around_and_inside_the_paste_is_tolerated(
        self, app, db, seed_user, bridge,
    ):
        """A paste from a mail or a terminal carries newlines and spaces."""
        wrapped = "  " + _SETUP_TOKEN[:20] + "\n" + _SETUP_TOKEN[20:] + " \n"

        bank_feed.claim_feed(seed_user["user"].id, wrapped)

        assert bridge.posts[0][0] == _CLAIM_URL


class TestTheHostPin:
    """Ruling R-BI28: a request goes to simplefin.org or a subdomain, or nowhere.

    Two doors take a URL from outside -- the pasted token and Bridge's claim
    answer -- and each refuses a foreign host before storing or sending
    anything; the sentence names the expected host and never the pasted one.
    """

    @pytest.mark.parametrize("url", [
        "https://evil.invalid/simplefin/claim/DEMO",
        "https://simplefin.org.evil.invalid/simplefin/claim/DEMO",
        "https://evilsimplefin.org/simplefin/claim/DEMO",
        "https://bridge.simplefin.org@evil.invalid/simplefin/claim/DEMO",
        "https://1.2.3.4/simplefin/claim/DEMO",
    ], ids=[
        "foreign", "suffix-lookalike", "substring-lookalike",
        "userinfo-lookalike", "ip-literal",
    ])
    def test_a_token_naming_a_foreign_host_is_refused_before_any_request(
        self, app, db, seed_user, bridge, url,
    ):
        """Its own class, zero calls, nothing stored; the sentence names
        simplefin.org and not the host that was pasted.  The lookalikes
        pin the test to a suffix: ``simplefin.org`` as a prefix, a
        substring, or a userinfo is not the host."""
        token = base64.b64encode(url.encode()).decode()

        with pytest.raises(BridgeHostRefused) as excinfo:
            bank_feed.claim_feed(seed_user["user"].id, token)

        assert bridge.posts == []
        assert db.session.query(BankFeed).count() == 0
        sentence = str(excinfo.value)
        assert "simplefin.org" in sentence
        assert urlsplit(url).hostname not in sentence
        assert excinfo.value.log_details == {}

    @pytest.mark.parametrize("url", [
        "https://bridge.simplefin.org/simplefin/claim/DEMO",
        "https://beta-bridge.simplefin.org/simplefin/claim/DEMO",
        "https://simplefin.org/simplefin/claim/DEMO",
        "HTTPS://BRIDGE.SIMPLEFIN.ORG/simplefin/claim/DEMO",
    ], ids=["bridge", "beta-bridge", "apex", "upper-case"])
    def test_bridges_own_hosts_are_accepted(
        self, app, db, seed_user, bridge, url,
    ):
        """The two hosts Bridge claims at, the apex, and the case fold
        ``requests`` applies: each is posted to as pasted."""
        token = base64.b64encode(url.encode()).decode()

        bank_feed.claim_feed(seed_user["user"].id, token)

        assert [posted for posted, _ in bridge.posts] == [url]

    def test_the_host_checked_is_the_host_requests_would_reach(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the parser the pin reads through.

        ``https://evil.invalid\\@bridge.simplefin.org/...`` is host
        ``bridge.simplefin.org`` to ``urlsplit`` (the userinfo runs to the
        last ``@``) and ``evil.invalid`` to ``requests`` (urllib3 stops the
        authority at the backslash), measured 2026-09-20 on requests 2.34.2.
        A pin over the raw string's ``urlsplit`` passes this token; the pin
        refuses it with no request.  The two premises are asserted first, so
        a ``requests`` that closes the gap fails this case rather than
        letting it pass for a reason it no longer tests.
        """
        url = "https://evil.invalid\\@bridge.simplefin.org/simplefin/claim/DEMO"
        assert urlsplit(url).hostname == "bridge.simplefin.org"
        prepared = requests.PreparedRequest()
        prepared.prepare_url(url, None)
        assert urlsplit(prepared.url).hostname == "evil.invalid"
        token = base64.b64encode(url.encode()).decode()

        with pytest.raises(BridgeHostRefused):
            bank_feed.claim_feed(seed_user["user"].id, token)

        assert bridge.posts == []

    def test_a_claim_answered_with_a_foreign_access_url_stores_nothing(
        self, app, db, seed_user, bridge,
    ):
        """Bridge (or whatever answered) names a credential on another
        server: refused as ``ForeignHost`` carrying the status it answered,
        nothing stored, and neither its host nor the password in the
        sentence.  This is why the listing and the sync need no host check
        of their own: a foreign URL is never stored."""
        bridge.post_answer = _FakeResponse(
            _CLAIM_URL, status_code=201,
            text=f"https://alice:{_SECRET}@evil.invalid/simplefin",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.error_class == "ForeignHost"
        # The status Bridge answered, not an assumed 200.
        assert excinfo.value.status == 201
        assert db.session.query(BankFeed).count() == 0
        sentence = str(excinfo.value)
        assert "simplefin.org" in sentence
        assert "evil.invalid" not in sentence
        _no_secret_in(sentence)

    @pytest.mark.parametrize("separator", ["\n", " "], ids=["newline", "space"])
    def test_an_answer_with_words_after_the_url_stores_nothing(
        self, app, db, seed_user, bridge, separator,
    ):
        """A body that starts with a Bridge URL and goes on in prose is not
        an access URL: refused as ``UnexpectedBody`` before the host is even
        read, so the whitespace check is graded apart from the pin.  The
        space case is the one a newline-only check stored."""
        bridge.post_answer = _FakeResponse(
            _CLAIM_URL, text=f"{_ACCESS_URL}{separator}Thanks for connecting!",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.error_class == "UnexpectedBody"
        assert db.session.query(BankFeed).count() == 0

    def test_a_scheme_hidden_behind_a_control_character_is_unreadable(
        self, app, db, seed_user, bridge,
    ):
        """``\\x00https://bridge.simplefin.org/...``: ``urlsplit`` strips the
        control character and reads ``https`` and Bridge's host, while
        ``requests`` leaves the string untouched and would refuse it as
        ``InvalidSchema`` before any socket.  The pin reads the scheme the
        way ``requests`` picks the adapter, so this is an UNREADABLE paste
        (its own class, no request), not "Bridge could not be reached"."""
        token = base64.b64encode(
            b"\x00https://bridge.simplefin.org/simplefin/claim/DEMO",
        ).decode()

        with pytest.raises(SetupTokenUnreadable):
            bank_feed.claim_feed(seed_user["user"].id, token)

        assert bridge.posts == []


class TestNoRedirectIsFollowed:
    """Ruling R-BI29: a request ends where it started, or is refused.

    ``requests`` follows a redirect on its own, to any host the ``Location``
    names; here every call is sent with ``allow_redirects=False`` and a
    ``3xx`` answer -- which ``raise_for_status`` lets through -- is refused
    with its status and the class ``Redirect``, before its body is read as
    an answer.
    """

    def test_both_calls_are_sent_with_redirects_off(
        self, app, db, seed_user, bridge,
    ):
        """The flag itself, on the claim POST and the listing GET: what
        keeps ``requests`` from following is the argument, so the argument
        is what is asserted -- on both doors, although one function sends
        for both, because that is the claim the module makes."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert bridge.posts[0][1]["allow_redirects"] is False
        assert bridge.gets[0][1]["allow_redirects"] is False

    @pytest.mark.parametrize(
        "status", [300, 301, 302, 304, 307, 308, 399], ids=str,
    )
    def test_a_redirected_claim_is_refused_and_stores_nothing(
        self, app, db, seed_user, bridge, status,
    ):
        """A ``3xx`` claim answer with an empty body: refused as
        ``Redirect`` carrying the status -- not as an unreadable body, which
        is what reading the empty body as an answer would have said -- and
        one POST only.  ``300``, ``304`` and ``399`` are not among the five
        statuses ``requests`` calls a redirect; the ruling says "a 3xx", and
        none of them is Bridge's answer either."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, status_code=status)

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.error_class == "Redirect"
        assert excinfo.value.status == status
        assert f"HTTP {status}" in str(excinfo.value)
        assert "DEMO-TOKEN-1234" not in str(excinfo.value)
        assert len(bridge.posts) == 1
        assert db.session.query(BankFeed).count() == 0

    def test_a_redirected_listing_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """The listing's ``302`` with no JSON body: ``Redirect``, not the
        decode error reading the body would have raised; one GET only."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", status_code=302,
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert excinfo.value.error_class == "Redirect"
        assert excinfo.value.status == 302
        assert len(bridge.gets) == 1
        _no_secret_in(str(excinfo.value))

    def test_a_redirect_sends_nothing_after_the_first_request(
        self, app, db, seed_user, monkeypatch,
    ):
        """FIRING CONTROL for the ruling's "no second request", graded
        where it can be.  The spy at ``requests.post`` counts the service's
        own calls and cannot see what ``requests`` does after a ``3xx``, so
        this case lets ``requests`` run and fakes Bridge at the adapter,
        answering every send with a 302 to a FOREIGN host: exactly one
        send, to Bridge's host, and the refusal is ``Redirect``.  With the
        flag mutated to ``True`` the same fake is asked again for
        ``evil.invalid``, thirty times, and this fails."""
        sent = _bridge_at_the_adapter(
            monkeypatch, status=302, location="https://evil.invalid/x",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.claim_feed(seed_user["user"].id, _SETUP_TOKEN)

        assert sent == [("POST", "bridge.simplefin.org")]
        assert (excinfo.value.status, excinfo.value.error_class) == (
            302, "Redirect",
        )
        assert db.session.query(BankFeed).count() == 0

    def test_a_redirect_requests_cannot_read_is_refused_not_a_500(
        self, app, db, seed_user, monkeypatch,
    ):
        """Even with redirects off, ``Session.send`` builds the unsent
        ``Response.next`` from the ``Location``, and a header it cannot
        decode raises ``UnicodeDecodeError`` -- not a ``RequestException``
        -- out of ``requests.post`` (measured 2026-09-20 on requests
        2.34.2; ``/\xe9`` is what ``http.client`` hands over for a
        latin-1 byte).  A designed refusal carrying the class, no status
        (the response is lost with the exception), one send, nothing
        stored, no cause chained, and the token nowhere in the sentence."""
        sent = _bridge_at_the_adapter(monkeypatch, status=302, location="/\xe9")

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.claim_feed(seed_user["user"].id, _SETUP_TOKEN)

        assert excinfo.value.error_class == "UnicodeDecodeError"
        assert excinfo.value.status is None
        assert len(sent) == 1
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__
        assert "DEMO-TOKEN-1234" not in str(excinfo.value)
        assert db.session.query(BankFeed).count() == 0


class TestTheClaimStoresWhatBridgeAnswers:
    """One bodiless POST to the decoded URL; the answer, encrypted."""

    def test_it_posts_once_to_the_decoded_claim_url_with_a_timeout(
        self, app, db, seed_user, bridge,
    ):
        """What was asked of Bridge, exactly."""
        _connect(seed_user, bridge)

        assert len(bridge.posts) == 1
        url, kwargs = bridge.posts[0]
        assert url == _CLAIM_URL
        assert kwargs["timeout"] == bank_feed.BRIDGE_TIMEOUT_SECONDS
        assert "data" not in kwargs and "json" not in kwargs

    def test_it_stages_the_access_url_as_ciphertext(
        self, app, db, seed_user, bridge,
    ):
        """The stored bytes decrypt to what Bridge answered, and nothing
        else in the row is the URL."""
        feed = _connect(seed_user, bridge)
        db.session.flush()

        stored = db.session.get(BankFeed, feed.id)
        assert stored.user_id == seed_user["user"].id
        assert isinstance(stored.access_url_encrypted, bytes)
        assert decrypt_secret(stored.access_url_encrypted) == _ACCESS_URL
        _no_secret_in(repr(stored))

    def test_an_answer_that_is_not_an_https_url_stores_nothing(
        self, app, db, seed_user, bridge,
    ):
        """Bridge answered 200 with prose: not a credential, not stored."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, text="Thanks!")

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.status == 200
        assert db.session.query(BankFeed).count() == 0

    def test_an_answer_whose_host_is_malformed_stores_nothing(
        self, app, db, seed_user, bridge,
    ):
        """Bridge's body reaches ``urlsplit`` too."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, text="https://[x")

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.error_class == "UnexpectedBody"
        assert db.session.query(BankFeed).count() == 0

    def test_it_asks_bridge_nothing_else(self, app, db, seed_user, bridge):
        """Ruling R-BI27's order: the listing is the ROUTE's, after the
        commit.  The service's claim makes no GET."""
        _connect(seed_user, bridge)

        assert bridge.gets == []


class TestABridgeFailureIsSaidWithoutTheCredential:
    """Class and status survive; the exception's text and its URL do not."""

    def test_an_http_error_names_the_status_and_not_the_url(
        self, app, db, seed_user, bridge,
    ):
        """The HTTPError's own text carries the claim URL; the refusal
        carries the status."""
        bridge.post_answer = _FakeResponse(_CLAIM_URL, status_code=403)

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.status == 403
        assert excinfo.value.error_class == "HTTPError"
        assert "403" in str(excinfo.value)
        assert "DEMO-TOKEN-1234" not in str(excinfo.value)
        assert db.session.query(BankFeed).count() == 0

    def test_a_transport_failure_names_its_class(
        self, app, db, seed_user, bridge,
    ):
        """No answer at all: the class is what the owner and the log get."""
        bridge.post_raises = requests.ConnectionError(
            f"Max retries exceeded with url: {_CLAIM_URL}",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.status is None
        assert excinfo.value.error_class == "ConnectionError"
        assert "ConnectionError" in str(excinfo.value)
        assert "DEMO-TOKEN-1234" not in str(excinfo.value)

    def test_an_unparseable_answer_chains_no_cause_carrying_the_url(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the ``from None`` in the pin's walk.  Bridge
        answers a credential with no host; ``requests.InvalidURL``'s own
        message quotes the whole URL, password included, and a chained
        ``__cause__`` is a traceback the scrubber cannot see.  The refusal
        carries no cause at all."""
        bridge.post_answer = _FakeResponse(
            _CLAIM_URL, text=f"https://alice:{_SECRET}@",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            _connect(seed_user, bridge)

        assert excinfo.value.error_class == "UnexpectedBody"
        assert excinfo.value.__cause__ is None
        # ``from None`` suppresses the context rather than clearing it; what
        # a log line would render is the formatted traceback, so that is
        # what is graded.
        assert excinfo.value.__suppress_context__
        _no_secret_in("".join(traceback.format_exception(excinfo.value)))
        assert db.session.query(BankFeed).count() == 0

    def test_a_listing_failure_never_repeats_the_access_url(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the whole module's rule.  The listing's
        HTTPError text is ``... for url: https://alice:s3cr3t@...``, the
        real shape; the sentence must not carry the password."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts?version=2&balances-only=1",
            status_code=401,
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert excinfo.value.status == 401
        _no_secret_in(str(excinfo.value))
        _no_secret_in(repr(excinfo.value))


class TestTheListingIsLiveAndRead:
    """``GET <access url>/accounts`` with the measured parameters, parsed."""

    def test_it_needs_a_feed(self, app, db, seed_user, bridge):
        """No feed, no listing, no request."""
        with pytest.raises(NoFeedConnected):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert bridge.gets == []

    def test_it_asks_for_balances_only_in_the_v2_shape(
        self, app, db, seed_user, bridge,
    ):
        """The decrypted access URL, ``/accounts``, the two parameters and
        the timeout -- what Bridge is asked, exactly."""
        _connect(seed_user, bridge)
        db.session.flush()

        bank_feed.list_bridge_accounts(seed_user["user"].id)

        url, kwargs = bridge.gets[0]
        assert url == f"{_ACCESS_URL}/accounts"
        assert kwargs["params"] == {"version": "2", "balances-only": "1"}
        assert kwargs["timeout"] == bank_feed.BRIDGE_TIMEOUT_SECONDS

    def test_it_reads_the_accounts_and_the_notices(
        self, app, db, seed_user, bridge,
    ):
        """Three accounts in Bridge's order with their balances; errlist
        first, then x-api-message, verbatim."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(
                errlist=["Connection to SECU needs attention"],
                api_message=["Provide a 'start-date' parameter"],
            ),
        )

        listing = bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert [account.external_id for account in listing.accounts] == [
            _CHECKING, _SHARE, _MORTGAGE,
        ]
        assert listing.accounts[0].name == "Checking (3820)"
        assert listing.accounts[0].currency == "USD"
        assert listing.accounts[0].balance == Decimal("2073.40")
        assert listing.accounts[2].balance == Decimal("-176719.77")
        assert listing.notices == (
            "Connection to SECU needs attention",
            "Provide a 'start-date' parameter",
        )

    def test_an_answer_with_no_account_list_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """JSON, but not Bridge's shape."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", json_body={"errlist": []},
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)
        assert excinfo.value.error_class == "UnexpectedShape"

    def test_a_non_json_answer_is_refused_with_its_status(
        self, app, db, seed_user, bridge,
    ):
        """An HTML error page at 200 is a ``requests`` decode error, raised
        WITHOUT the response attached (requests 2.34.2), so it is caught
        apart from the transport failures and carries the status Bridge
        did answer with rather than reading as "could not be reached"."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts", text="<html>maintenance</html>",
        )

        with pytest.raises(BridgeRefused) as excinfo:
            bank_feed.list_bridge_accounts(seed_user["user"].id)
        assert excinfo.value.error_class == "JSONDecodeError"
        assert excinfo.value.status == 200
        assert "HTTP 200" in str(excinfo.value)
        assert excinfo.value.log_details == {
            "status": 200, "error_class": "JSONDecodeError",
        }

    def test_a_stored_url_the_key_cannot_read_is_refused(
        self, app, db, seed_user, bridge, monkeypatch,
    ):
        """The rotation hazard: the row's key is gone from the key list.
        ``FeedUnreadable``, not ``InvalidToken`` escaping as a 500; and no
        request is made."""
        # Pylint: ``import-outside-toplevel`` -- a fresh key minted here.
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
        _connect(seed_user, bridge)
        db.session.flush()
        monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.delenv("FIELD_ENCRYPTION_KEY_OLD", raising=False)

        with pytest.raises(FeedUnreadable):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

        assert bridge.gets == []

    def test_an_account_missing_its_id_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """One malformed member refuses the listing rather than skipping
        it: a listing missing an account would let the owner map the rest
        and never learn one was dropped."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(accounts=[
                {"name": "Checking", "currency": "USD", "balance": "1.00"},
            ]),
        )

        with pytest.raises(BridgeRefused):
            bank_feed.list_bridge_accounts(seed_user["user"].id)

    def test_an_id_longer_than_the_column_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """``external_account_id`` is ``String(64)``; an id the mapping
        could not record is refused at the listing, before a form offers
        it."""
        _connect(seed_user, bridge)
        db.session.flush()
        bridge.get_answer = _FakeResponse(
            f"{_ACCESS_URL}/accounts",
            json_body=_listing_body(accounts=[
                {"id": "ACT-" + "x" * 61, "name": "Checking",
                 "currency": "USD", "balance": "1.00"},
            ]),
        )

        with pytest.raises(BridgeRefused):
            bank_feed.list_bridge_accounts(seed_user["user"].id)


def _declared(seed_user):
    """The owner's declared mappings as ``{external_id: account_id}``."""
    return {
        row.external_account_id: row.account_id
        for row in db.session.query(AccountExternalIdentity).filter(
            AccountExternalIdentity.user_id == seed_user["user"].id,
            AccountExternalIdentity.feed_id.isnot(None),
        )
    }


class TestTheMappingIsTheOwnersDeclaration:
    """One submission states the whole mapping the owner can see."""

    def test_it_needs_a_feed(self, app, db, seed_user):
        """No feed, nothing to declare under."""
        with pytest.raises(NoFeedConnected):
            bank_feed.map_accounts(
                seed_user["user"].id, {_CHECKING: seed_user["account"].id},
            )

    def test_it_declares_a_mapping_under_the_feed(
        self, app, db, seed_user, bridge,
    ):
        """The row carries the feed, the simplefin source and Bridge's id."""
        feed = _connect(seed_user, bridge)
        db.session.flush()

        outcome = bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: seed_user["account"].id, _MORTGAGE: None},
        )

        assert outcome == bank_feed.MappingOutcome(
            declared=1, moved=0, cleared=0, unchanged=1,
        )
        row = db.session.query(AccountExternalIdentity).one()
        assert row.feed_id == feed.id
        assert row.source_id == ref_cache.statement_source_id(
            StatementSourceEnum.SIMPLEFIN,
        )
        assert row.external_account_id == _CHECKING
        assert row.account_id == seed_user["account"].id

    def test_resubmitting_the_same_mapping_changes_nothing(
        self, app, db, seed_user, bridge,
    ):
        """The receipt says so rather than "saved"."""
        _connect(seed_user, bridge)
        db.session.flush()
        choices = {_CHECKING: seed_user["account"].id}
        bank_feed.map_accounts(seed_user["user"].id, choices)
        before = db.session.query(AccountExternalIdentity).one().id

        outcome = bank_feed.map_accounts(seed_user["user"].id, choices)

        assert outcome == bank_feed.MappingOutcome(
            declared=0, moved=0, cleared=0, unchanged=1,
        )
        assert db.session.query(AccountExternalIdentity).one().id == before

    def test_a_change_of_mind_moves_the_mapping(
        self, app, db, seed_user, bridge,
    ):
        """Checking's Bridge account is now the second account's."""
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: second.id},
        )

        assert outcome.moved == 1
        assert _declared(seed_user) == {_CHECKING: second.id}

    def test_choosing_not_mapped_clears_the_mapping(
        self, app, db, seed_user, bridge,
    ):
        """A cleared mapping is a deleted row."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: None},
        )

        assert outcome.cleared == 1
        assert _declared(seed_user) == {}

    def test_handing_an_account_from_one_bridge_account_to_another_lands(
        self, app, db, seed_user, bridge,
    ):
        """FIRING CONTROL for the endings-first order: X was Checking; now
        Y is Checking and X is unmapped, in ONE submission.  Declaring Y
        before X's row is gone would trip
        ``uq_account_external_identities_account_source``."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        outcome = bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: None, _SHARE: seed_user["account"].id},
        )

        assert outcome.cleared == 1
        assert outcome.declared == 1
        assert _declared(seed_user) == {_SHARE: seed_user["account"].id}

    def test_two_bridge_accounts_for_one_account_here_are_refused(
        self, app, db, seed_user, bridge,
    ):
        """Said in the owner's words before the key would say it."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused) as excinfo:
            bank_feed.map_accounts(
                seed_user["user"].id,
                {_CHECKING: seed_user["account"].id,
                 _SHARE: seed_user["account"].id},
            )

        assert "one account here can be one Bridge account" in str(excinfo.value)
        assert _declared(seed_user) == {}

    def test_another_owners_account_is_refused(
        self, app, db, seed_user, bridge, second_user,
    ):
        """IDOR at the service tier: the choice names a row that exists and
        is not the caller's."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(
                seed_user["user"].id, {_CHECKING: second_user["account"].id},
            )

        assert _declared(seed_user) == {}

    def test_a_loan_is_not_a_mappable_account(
        self, app, db, seed_user, bridge,
    ):
        """The mapping offers exactly the accounts the statements page
        serves; a loan has no bank statement to record against."""
        _connect(seed_user, bridge)
        db.session.flush()
        loan = create_loan_account(seed_user, db.session)

        assert loan not in bank_feed.mappable_accounts(seed_user["user"].id)
        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(seed_user["user"].id, {_MORTGAGE: loan.id})

    def test_an_account_held_by_a_bridge_id_absent_from_the_submission_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """The guard beside the key: X holds Checking and the submission
        names only Y -> Checking, so the declaration would trip
        ``uq_account_external_identities_account_source`` after the write;
        it is refused in words before it."""
        _connect(seed_user, bridge)
        db.session.flush()
        bank_feed.map_accounts(
            seed_user["user"].id, {_CHECKING: seed_user["account"].id},
        )

        with pytest.raises(MappingRefused) as excinfo:
            bank_feed.map_accounts(
                seed_user["user"].id, {_SHARE: seed_user["account"].id},
            )

        assert "this form did not include" in str(excinfo.value)
        assert _declared(seed_user) == {_CHECKING: seed_user["account"].id}

    def test_a_choice_that_repeats_what_stands_needs_no_mappable_account(
        self, app, db, seed_user, bridge,
    ):
        """A mapping to an account archived since it was declared is kept
        by resubmitting it unchanged: the mappable check applies to a
        CHANGE, and repeating what stands is no change."""
        # Pylint: ``import-outside-toplevel`` -- the sibling's builder.
        from tests.test_services.test_statement_import.test_record import (  # pylint: disable=import-outside-toplevel
            _second_account,
        )
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(seed_user["user"].id, {_SHARE: second.id})
        second.is_active = False
        db.session.flush()
        assert second not in bank_feed.mappable_accounts(seed_user["user"].id)

        outcome = bank_feed.map_accounts(
            seed_user["user"].id, {_SHARE: second.id},
        )

        assert outcome.unchanged == 1
        assert _declared(seed_user) == {_SHARE: second.id}

    def test_a_bridge_id_the_column_cannot_hold_is_refused(
        self, app, db, seed_user, bridge,
    ):
        """The service's own guard, beside the schema's."""
        _connect(seed_user, bridge)
        db.session.flush()

        with pytest.raises(MappingRefused):
            bank_feed.map_accounts(
                seed_user["user"].id, {"A" * 65: seed_user["account"].id},
            )


class TestDisconnectTakesTheDeclaredMappingsWithIt:
    """Ruling R-BI12's delete, ruling R-BI26's cascade, one receipt."""

    def test_it_needs_a_feed(self, app, db, seed_user):
        """Nothing to disconnect."""
        with pytest.raises(NoFeedConnected):
            bank_feed.disconnect_feed(seed_user["user"].id)

    def test_the_feed_and_its_mappings_go_and_a_learned_row_stays(
        self, app, db, seed_user, bridge,
    ):
        """Two declared rows counted and gone; the CSV pairing untouched."""
        _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id,
            {_CHECKING: seed_user["account"].id, _SHARE: second.id},
        )
        record_identity(
            seed_user["account"].id, seed_user["user"].id,
            ref_cache.statement_source_id(StatementSourceEnum.SECU_CHECKING_CSV),
            "******3820",
        )
        db.session.flush()

        outcome = bank_feed.disconnect_feed(seed_user["user"].id)

        assert outcome.mappings_removed == 2
        assert db.session.query(BankFeed).count() == 0
        assert _declared(seed_user) == {}
        assert db.session.query(AccountExternalIdentity).count() == 1


class TestTheFeedStateIsARead:
    """What the panel renders, without decrypting or asking Bridge."""

    def test_none_without_a_feed(self, app, db, seed_user):
        """The panel's not-connected state."""
        assert bank_feed.feed_state(seed_user["user"].id) is None

    def test_the_state_carries_the_mappings_by_bridge_id(
        self, app, db, seed_user, bridge,
    ):
        """Ordered by Bridge id, each with its account's name; and the read
        asks Bridge nothing."""
        feed = _connect(seed_user, bridge)
        db.session.flush()
        second = _second_account(db, seed_user)
        bank_feed.map_accounts(
            seed_user["user"].id,
            {_SHARE: second.id, _CHECKING: seed_user["account"].id},
        )
        gets_before = len(bridge.gets)

        state = bank_feed.feed_state(seed_user["user"].id)

        assert state.feed_id == feed.id
        assert state.claimed_at is not None
        assert [m.external_id for m in state.mappings] == sorted(
            [_CHECKING, _SHARE],
        )
        assert {m.account_name for m in state.mappings} == {
            seed_user["account"].name, "Second Checking",
        }
        assert len(bridge.gets) == gets_before
