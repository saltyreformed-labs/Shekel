"""The bank feed's claim: the net blocked, the token never burnt for nothing, the host pin, no
redirect followed, what Bridge answers stored, and a failure said without the credential.
"""

import base64
import traceback
from urllib.parse import urlsplit

import pytest
import requests

from app.exceptions import (
    BridgeHostRefused,
    BridgeRefused,
    FeedAlreadyConnected,
    SetupTokenUnreadable,
)
from app.models.bank_feed import BankFeed
from app.services import bank_feed
from app.utils.field_encryption import decrypt_secret

from ._helpers import (
    _CLAIM_URL,
    _SETUP_TOKEN,
    _SECRET,
    _ACCESS_URL,
    _FakeResponse,
    _bridge_at_the_adapter,
    _connect,
    _no_secret_in,
)


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
