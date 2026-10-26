"""The GitHub client and the App's token, against recorded responses; nothing calls GitHub."""
from __future__ import annotations

import base64
import json

import pytest
import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from tools.quill._github import (
    API,
    CUT_OFF,
    CUT_OFF_RETRIES,
    RATE_LIMIT_RETRIES,
    WAIT_BUDGET_SECONDS,
    CredentialsError,
    GitHub,
    GitHubError,
    app_credentials,
    app_jwt,
    installation_token,
)


class _Response:
    """The slice of ``requests.Response`` the client reads."""

    def __init__(self, status_code, body=None, headers=None):
        """Record one response: its status, JSON body and headers (none by default)."""
        self.status_code = status_code
        self.content = b"" if body is None else json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = headers or {}
        self._body = body

    def json(self):
        """The recorded body."""
        return self._body


class _Session:
    """Replays responses in order and records every request."""

    def __init__(self, *responses):
        """Hold the responses to replay, in order."""
        self.responses = list(responses)
        self.sent = []

    def request(self, method, url, **kwargs):
        """Record the request; answer with the next recorded response."""
        self.sent.append((method, url, kwargs))
        return self.responses.pop(0)


def _decode(segment: str) -> dict:
    """One JWT segment's JSON."""
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _pem(key) -> bytes:
    """A private key as the PEM GitHub issues."""
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_app_jwt_is_rs256_signed_by_the_key_with_the_documented_claims():
    """GitHub verifies the token with the App's public key; so does this test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = app_jwt("Iv23client", _pem(key), now=1_000_000)
    header, claims, signature = token.split(".")
    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(claims) == {"iat": 999_940, "exp": 1_000_540, "iss": "Iv23client"}
    raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    key.public_key().verify(
        raw, f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    tampered = f"{header}.{claims[:-2]}xx".encode()
    with pytest.raises(InvalidSignature):
        key.public_key().verify(raw, tampered, padding.PKCS1v15(), hashes.SHA256())


def test_app_jwt_refuses_a_key_that_is_not_rsa():
    """RS256 is the only algorithm GitHub accepts for an App; another key is a wrong file."""
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ValueError, match="RSA"):
        app_jwt("Iv23client", _pem(key), now=1_000_000)


def _unknown_key_type() -> bytes:
    """A PKCS#8 private key whose algorithm is the OID 1.2.3.4, which ``cryptography``
    raises UnsupportedAlgorithm for (measured on 50.0.2)."""
    oid = bytes([0x06, 0x03, 0x2A, 0x03, 0x04])
    body = bytes([0x02, 0x01, 0x00, 0x30, len(oid)]) + oid + bytes([0x04, 0x02, 0x00, 0x00])
    der = bytes([0x30, len(body)]) + body
    return (b"-----BEGIN PRIVATE KEY-----\n" + base64.encodebytes(der)
            + b"-----END PRIVATE KEY-----\n")


def _too_small_to_sign():
    """An RSA key with a 64-bit modulus: it loads, and signing an RS256 token with it
    raises ValueError ("Digest or salt length too long for key size", measured)."""
    p, q, e = 4294967291, 4294967279, 65537
    d = pow(e, -1, (p - 1) * (q - 1))
    return rsa.RSAPrivateNumbers(p, q, d, d % (p - 1), d % (q - 1), pow(q, -1, p),
                                 rsa.RSAPublicNumbers(e, p * q)).private_key()


def _encrypted(key) -> bytes:
    """A private key as PEM, encrypted under a password quill is never given."""
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.BestAvailableEncryption(b"pw"))


@pytest.mark.parametrize("pem", [
    pytest.param(lambda: b"not a pem", id="not-pem"),
    pytest.param(lambda: _pem(rsa.generate_private_key(public_exponent=65537,
                                                       key_size=2048))[:200], id="cut-short"),
    pytest.param(lambda: _encrypted(rsa.generate_private_key(public_exponent=65537,
                                                             key_size=2048)), id="encrypted"),
    pytest.param(_unknown_key_type, id="unknown-type"),
    pytest.param(lambda: _pem(ec.generate_private_key(ec.SECP256R1())), id="not-rsa"),
    pytest.param(lambda: _pem(_too_small_to_sign()), id="too-small"),
])
def test_a_key_quill_cannot_sign_with_is_a_credentials_error(pem):
    """L9d: a corrupt, encrypted, unknown or non-RSA ``app.pem`` raised ValueError,
    TypeError or UnsupportedAlgorithm, which escaped every command as a traceback with
    exit 1, the code that means refused; each is now the one named error."""
    with pytest.raises(CredentialsError):
        app_jwt("Iv23client", pem(), now=1_000_000)


@pytest.mark.parametrize(("text", "why"), [
    pytest.param(b"{not json", "is not JSON", id="not-json"),
    pytest.param(b'{"client_id": "\xff"}', "is not JSON", id="not-utf8"),
    pytest.param(b'["Iv23client"]', "holds no client_id", id="not-an-object"),
    pytest.param(b'{"app_id": 7}', "holds no client_id", id="no-client-id"),
    pytest.param(b'{"client_id": ""}', "holds no client_id", id="empty"),
    pytest.param(b'{"client_id": 7}', "holds no client_id", id="not-a-string"),
    pytest.param(b'{"client_id": "x", "n": ' + b"1" * 5000 + b"}", "is not JSON",
                 id="integer-too-long"),
])
def test_an_app_json_holding_no_client_id_is_a_credentials_error(tmp_path, text, why):
    """L9d: each of these escaped as a traceback (JSONDecodeError, UnicodeDecodeError,
    ValueError, KeyError, AttributeError or TypeError), or passed on a value that is no
    client ID.  The parser's RecursionError, the one more escape, has its own case:
    :func:`test_an_app_json_the_parser_cannot_nest_that_deep_is_a_credentials_error`."""
    (tmp_path / "app.json").write_bytes(text)
    (tmp_path / "app.pem").write_bytes(b"pem")
    with pytest.raises(CredentialsError, match=why):
        app_credentials(tmp_path)


def test_an_app_json_the_parser_cannot_nest_that_deep_is_a_credentials_error(tmp_path,
                                                                            monkeypatch):
    """L9d: ``json.loads`` raising RecursionError on nesting deeper than it can parse
    escaped as a traceback; it is ``app.json`` "is not JSON".  No real input pins this arm
    on every host: Python 3.14's C parser is bounded by the stack it uses, so where it
    gives up depends on the host and the Python build.  Measured: on the developer's host
    (CPython 3.14.8, ``ulimit -s`` 8192) 50,000 nested arrays parse and 75,000 raise
    "Stack overflow (used 8148 kB) while decoding a JSON array"; GitHub's runner parsed
    100,000 (CI job 117731545129, 2026-10-18), so a case built on 100,000 failed there.
    Any deeper input is the same bet on a stack size, so the parse is made to raise what
    the host measured raises."""
    def too_deep(_text):
        """The parser giving up as the host measured it does."""
        raise RecursionError("Stack overflow (used 8148 kB) while decoding a JSON array "
                             "from a unicode string")

    monkeypatch.setattr("tools.quill._github.json.loads", too_deep)
    (tmp_path / "app.json").write_text('{"client_id": "Iv23client"}', encoding="utf-8")
    (tmp_path / "app.pem").write_bytes(b"pem")
    with pytest.raises(CredentialsError, match="is not JSON: Stack overflow"):
        app_credentials(tmp_path)


def test_the_app_credentials_are_read_from_their_two_files(tmp_path):
    """The client ID from ``app.json``, the key's bytes from ``app.pem``; a missing file
    stays the FileNotFoundError reading it raises, which every command reports already."""
    with pytest.raises(FileNotFoundError):
        app_credentials(tmp_path)
    (tmp_path / "app.json").write_text('{"client_id": "Iv23client"}', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        app_credentials(tmp_path)
    (tmp_path / "app.pem").write_bytes(b"pem")
    assert app_credentials(tmp_path) == ("Iv23client", b"pem")


def test_rest_sends_the_token_and_api_version_and_decodes_the_body():
    """Every call carries the bearer token and pins the API version."""
    session = _Session(_Response(200, {"name": "shekel-plan"}))
    assert GitHub("t0k", session).rest("GET", "/repos/o/r") == {"name": "shekel-plan"}
    method, url, kwargs = session.sent[0]
    assert (method, url) == ("GET", f"{API}/repos/o/r")
    assert kwargs["headers"]["Authorization"] == "Bearer t0k"
    assert kwargs["headers"]["X-GitHub-Api-Version"] == "2022-11-28"


def test_rest_answers_none_for_an_empty_body():
    """A DELETE answers 204 with no body."""
    session = _Session(_Response(204))
    assert GitHub("t", session).rest("DELETE", "/repos/o/r/labels/bug") is None


def test_rest_raises_with_the_status_on_an_error():
    """A 404 is a fact a caller branches on (the repository does not exist yet)."""
    session = _Session(_Response(404, {"message": "Not Found"}))
    with pytest.raises(GitHubError) as raised:
        GitHub("t", session).rest("GET", "/repos/o/missing")
    assert raised.value.status == 404
    assert "Not Found" in str(raised.value)


def test_graphql_raises_on_errors_reported_inside_a_200():
    """GraphQL reports a refused mutation with status 200 and an ``errors`` list."""
    session = _Session(_Response(200, {"data": None, "errors": [{"message": "nope"}]}))
    with pytest.raises(GitHubError, match="nope"):
        GitHub("t", session).graphql("mutation { x }")


def test_graphql_lookup_reads_a_missing_field_as_absent_but_raises_on_a_missing_repository():
    """Review cp3 L-c and cp4 L-10: every NOT_FOUND was dropped, so a repository GitHub could
    not find left ``data.repository`` null and the caller hit a TypeError.  Only a card the
    repository holds no number for -- a two-step path -- is absent; the repository's own
    NOT_FOUND and any deeper one still raise (the first two shapes recorded 2026-10-04 in
    ``recorded/tracker.json``, graded against it in ``test__tracker.py``; no read has
    measured a deeper one)."""
    below = {"data": {"repository": {"c1": None}}, "errors": [
        {"type": "NOT_FOUND", "path": ["repository", "c1"], "message": "no issue 1"}]}
    assert GitHub("t", _Session(_Response(200, below))).graphql_lookup("q") == below["data"]
    top = {"data": {"repository": None}, "errors": [
        {"type": "NOT_FOUND", "path": ["repository"], "message": "no repository x"}]}
    with pytest.raises(GitHubError, match="no repository x"):
        GitHub("t", _Session(_Response(200, top))).graphql_lookup("q")
    deeper = {"data": {"repository": {"c1": {"parent": None}}}, "errors": [
        {"type": "NOT_FOUND", "path": ["repository", "c1", "parent"], "message": "unseen"}]}
    with pytest.raises(GitHubError, match="unseen"):
        GitHub("t", _Session(_Response(200, deeper))).graphql_lookup("q")


def test_graphql_lookup_reads_as_absent_only_a_card_under_the_repository():
    """A two-step NOT_FOUND under any other top-level field (an organization's) still raises:
    only a card the repository holds no number for was measured as absent."""
    other = {"data": {"organization": {"x": None}}, "errors": [
        {"type": "NOT_FOUND", "path": ["organization", "x"], "message": "elsewhere"}]}
    with pytest.raises(GitHubError, match="elsewhere"):
        GitHub("t", _Session(_Response(200, other))).graphql_lookup("q")


def _client(session, slept=None, now=1_000.0):
    """A client over ``session`` whose waits are kept in ``slept`` instead of slept."""
    return GitHub("t", session, sleep=(slept if slept is not None else []).append,
                  clock=lambda: now)


def test_no_redirect_is_followed_and_a_3xx_is_an_error():
    """A transferred issue or a renamed repository answers 301; followed, a read would
    describe another repository's issue and a write would land where it was not aimed."""
    session = _Session(_Response(301, {"message": "Moved Permanently", "url": "elsewhere"}))
    with pytest.raises(GitHubError) as raised:
        _client(session).rest("GET", "/repos/o/r/issues/5")
    assert raised.value.status == 301
    assert session.sent[0][2]["allow_redirects"] is False


_SECONDARY = {"message": "You have exceeded a secondary rate limit. Please wait a few minutes "
                         "before you try again."}


def test_a_secondary_limit_is_waited_out_as_retry_after_says_then_resent():
    """GitHub: "If the retry-after response header is present, you should not retry your
    request until after that many seconds has elapsed"."""
    session = _Session(_Response(403, _SECONDARY, {"retry-after": "7"}), _Response(200, {"ok": 1}))
    slept = []
    assert _client(session, slept).rest("POST", "/repos/o/r/issues", {"title": "t"}) == {"ok": 1}
    assert slept == [7.0]
    assert [sent[2]["json"] for sent in session.sent] == [{"title": "t"}, {"title": "t"}]


def test_a_spent_limit_is_waited_out_until_its_reset():
    """GitHub: with ``x-ratelimit-remaining`` at 0, not "until after the time, in UTC epoch
    seconds, specified by the x-ratelimit-reset header"; a 429 too."""
    session = _Session(_Response(429, {"message": "API rate limit exceeded"},
                                 {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}),
                       _Response(200, {"ok": 1}))
    slept = []
    _client(session, slept, now=1_000.0).rest("GET", "/repos/o/r")
    assert slept == [101.0]


def test_with_no_header_the_wait_doubles_from_a_minute_and_then_quill_stops():
    """GitHub: "wait for at least one minute before retrying", then "an exponentially
    increasing amount of time between retries, and throw an error after a specific number
    of retries" -- continuing "may result in the banning of your integration"."""
    session = _Session(*[_Response(403, _SECONDARY) for _ in range(RATE_LIMIT_RETRIES + 1)])
    slept = []
    with pytest.raises(GitHubError, match="still rate limited after 5 waits; stopped"):
        _client(session, slept).rest("GET", "/repos/o/r")
    assert slept == [60.0, 120.0, 240.0, 480.0, 960.0]
    assert len(session.sent) == RATE_LIMIT_RETRIES + 1


def test_a_403_that_names_no_rate_limit_is_raised_at_once():
    """The refusal the recordings hold for an unlink of a card that is not linked: a 403
    saying nothing of a rate limit is no rate limit, so it is neither waited on nor resent."""
    session = _Session(_Response(403, {"message": "Resource not accessible by integration"}))
    slept = []
    with pytest.raises(GitHubError) as raised:
        _client(session, slept).rest("DELETE", "/repos/o/r/issues/1/sub_issue", {"x": 1})
    assert (raised.value.status, slept, len(session.sent)) == (403, [], 1)


def test_a_graphql_rate_limit_inside_a_200_is_waited_out_and_its_data_never_read_for_it():
    """GitHub's GraphQL answers a rate limit 200 "with an error message"; that is waited out
    and resent.  A 200 whose DATA holds the words (a card's body may), or whose remaining
    count reached zero with no error, is an answer like any other."""
    limited = _Response(200, {"data": None, "errors": [
        {"type": "RATE_LIMITED", "message": "API rate limit exceeded for installation."}]})
    answer = {"data": {"repository": {"body": "the secondary rate limit, explained"}}}
    session = _Session(limited, _Response(200, answer),
                       _Response(200, answer, {"x-ratelimit-remaining": "0"}))
    slept = []
    client = _client(session, slept)
    assert client.graphql("query { x }") == answer["data"]
    assert client.graphql("query { x }") == answer["data"]
    assert slept == [60.0] and len(session.sent) == 3


def test_a_rest_answer_is_never_read_as_a_graphql_rate_limit():
    """Only a GraphQL answer carries a rate limit inside a 200; a REST 200 whose body has an
    ``errors`` key saying the words is returned as it came."""
    body = {"errors": [{"message": "rate limit"}]}
    assert _client(_Session(_Response(200, body))).rest("GET", "/repos/o/r") == body


def test_an_installation_token_is_narrowed_to_one_repository_and_read_back():
    """GitHub's ``repositories`` narrowing; the answer must name exactly that repository, or
    the token is refused.  ``None`` keeps the installation's every repository (the check of
    what the App reaches)."""
    narrowed = _Session(_Response(201, {"token": "x", "repositories": [{"name": "shekel-plan"}]}))
    assert installation_token(7, "jwt", repository="shekel-plan", session=narrowed) == "x"
    assert narrowed.sent[0][1].endswith("/app/installations/7/access_tokens")
    assert narrowed.sent[0][2]["json"] == {"repositories": ["shekel-plan"]}
    wider = _Session(_Response(201, {"token": "x", "repositories": [
        {"name": "shekel-plan"}, {"name": "shekel-plan-rehearsal"}]}))
    with pytest.raises(GitHubError, match="narrowed to 'shekel-plan' reaches"):
        installation_token(7, "jwt", repository="shekel-plan", session=wider)
    whole = _Session(_Response(201, {"token": "y"}))
    assert installation_token(7, "jwt", repository=None, session=whole) == "y"
    assert whole.sent[0][2]["json"] is None


def test_a_reset_already_past_waits_one_second_not_a_negative_time():
    """``time.sleep`` refuses a negative wait; a reset the clock has passed waits the one
    second "until after" it."""
    session = _Session(_Response(403, {"message": "API rate limit exceeded"},
                                 {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "900"}),
                       _Response(200, {"ok": 1}))
    slept = []
    _client(session, slept, now=1_000.0).rest("GET", "/repos/o/r")
    assert slept == [1.0]


def test_a_429_that_says_nothing_of_a_rate_limit_is_raised_at_once():
    """GitHub's text names a 429 a rate limit beside a zero remaining count or an error
    message saying so; quill reads that, or a retry-after (how long GitHub says to wait),
    and nothing else."""
    session = _Session(_Response(429, {"message": "Too many requests"}))
    slept = []
    with pytest.raises(GitHubError) as raised:
        _client(session, slept).rest("GET", "/repos/o/r")
    assert (raised.value.status, slept) == (429, [])


def test_a_403_carrying_only_retry_after_is_a_rate_limit_waited_out_as_it_says():
    """GitHub names a secondary limit by ``retry-after`` alone too: a 403 whose message says
    nothing of a limit but carries the header waits exactly what it says, once, then
    succeeds (review of A2, M1: with the header's test dropped, no other test failed)."""
    session = _Session(_Response(403, {"message": "Forbidden"}, {"retry-after": "2"}),
                       _Response(200, {"ok": 1}))
    slept = []
    assert _client(session, slept).rest("GET", "/repos/o/r") == {"ok": 1}
    assert slept == [2.0] and len(session.sent) == 2


def test_a_retry_after_written_as_an_http_date_waits_until_that_date():
    """RFC 9110's other form of ``retry-after``: 00:17:00 on 1970-01-01 is epoch 1,020, so
    at epoch 1,000 the wait is 20 seconds."""
    dated = {"retry-after": "Thu, 01 Jan 1970 00:17:00 GMT"}
    session = _Session(_Response(403, _SECONDARY, dated), _Response(200, {"ok": 1}))
    slept = []
    _client(session, slept, now=1_000.0).rest("GET", "/repos/o/r")
    assert slept == [20.0]


@pytest.mark.parametrize("headers", [
    {"retry-after": "-1"},
    {"retry-after": "nan"},
    {"retry-after": "inf"},
    {"retry-after": "soon"},
    {"retry-after": "Thu, 01 Jan 1970 00:17:00"},
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "never"},
    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "inf"},
], ids=["negative", "nan", "infinite", "words", "date-without-zone", "reset-words",
        "reset-infinite"])
def test_a_wait_quill_cannot_read_stops_it_unslept_and_unsent(headers):
    """A wait that is no time quill can read cannot be honoured, and re-sending sooner than
    GitHub asks is what its documentation warns can ban the integration: quill stops,
    sleeping nothing and re-sending nothing (review of A2, M2: ``time.sleep`` raised on a
    negative, hung on an infinity, and a date raised a ValueError no command caught)."""
    session = _Session(_Response(403, _SECONDARY, headers), _Response(200, {"ok": 1}))
    slept = []
    with pytest.raises(GitHubError, match="no time quill can read.*stopped rather than "
                                          "re-sent early") as raised:
        _client(session, slept).rest("GET", "/repos/o/r")
    assert (raised.value.status, slept, len(session.sent)) == (403, [], 1)


def test_a_wait_past_the_budget_stops_quill_rather_than_being_shortened():
    """``retry-after: 100000`` (about 28 hours) is not slept, nor cut short: quill stops at
    once.  Waits add up per request, so a second 3,000 s wait after a first is refused too,
    and :data:`_github.WAIT_BUDGET_SECONDS` bounds the whole."""
    assert WAIT_BUDGET_SECONDS == 3600
    huge = _Session(_Response(403, _SECONDARY, {"retry-after": "100000"}))
    slept = []
    with pytest.raises(GitHubError, match="wait of 100000 s after 0 s waited, past the 3600 s"):
        _client(huge, slept).rest("POST", "/repos/o/r/issues", {"title": "t"})
    assert (slept, len(huge.sent)) == ([], 1)
    twice = _Session(_Response(403, _SECONDARY, {"retry-after": "3000"}),
                     _Response(403, _SECONDARY, {"retry-after": "3000"}),
                     _Response(200, {"ok": 1}))
    slept = []
    with pytest.raises(GitHubError, match="wait of 3000 s after 3000 s waited"):
        _client(twice, slept).rest("GET", "/repos/o/r")
    assert (slept, len(twice.sent)) == ([3000.0], 2)
    whole = _Session(_Response(403, _SECONDARY, {"retry-after": "3600"}),
                     _Response(200, {"ok": 1}))
    slept = []
    assert _client(whole, slept).rest("GET", "/repos/o/r") == {"ok": 1}
    assert slept == [3600.0]


class _NotJson(_Response):
    """A 200 whose body is not JSON, as an HTML page GitHub's front end serves is not."""

    def __init__(self):
        """A body of HTML."""
        super().__init__(200)
        self.content = b"<html><body>unicorn</body></html>"
        self.text = self.content.decode()

    def json(self):
        """What ``requests`` raises for it."""
        raise requests.JSONDecodeError("Expecting value", self.text, 0)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_read_github_cut_off_is_re_sent_three_times_waiting_longer_each_time(status):
    """Ruling plan#1816: "a cut-off READ is re-sent up to 3 times, waiting longer each
    time" -- the same request each time, then the answer read as if it came first."""
    session = _Session(*[_Response(status, {"message": "cut off"}) for _ in range(3)],
                       _Response(200, {"ok": 1}))
    slept = []
    assert _client(session, slept).rest("GET", "/repos/o/r/issues/5") == {"ok": 1}
    assert slept == [2.0, 4.0, 8.0] and CUT_OFF_RETRIES == 3
    assert [sent[:2] for sent in session.sent] == [("GET", f"{API}/repos/o/r/issues/5")] * 4


@pytest.mark.parametrize("query", ["query($after: String) { x }", "  query { x }", "{ x }"])
def test_a_graphql_query_is_a_read_in_both_its_forms(query):
    """A GraphQL query is POSTed like a write, so the text decides: its keyword or its
    shorthand brace (the GraphQL specification) makes it a read, re-sent after a cut-off."""
    answer = {"data": {"x": 1}}
    session = _Session(_Response(504, {"message": "timeout"}), _Response(200, answer))
    slept = []
    assert _client(session, slept).graphql(query) == {"x": 1}
    assert slept == [2.0] and len(session.sent) == 2


def test_a_read_cut_off_a_fourth_time_stops_quill_naming_its_re_sends():
    """The third re-send's cut-off is the last: quill stops with the status and how many
    times it re-sent the read, never a fifth send."""
    session = _Session(*[_Response(504, {"message": "We couldn't respond in time"})
                         for _ in range(4)])
    slept = []
    with pytest.raises(GitHubError, match=r"GET .*/repos/o/r -> 504 after 3 re-sends: .*"
                                          "couldn't respond") as raised:
        _client(session, slept).rest("GET", "/repos/o/r")
    assert (raised.value.status, slept, len(session.sent)) == (504, [2.0, 4.0, 8.0], 4)


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/repos/o/r/issues", {"title": "t"}),
    ("PATCH", "/repos/o/r/issues/5", {"state": "closed"}),
    ("DELETE", "/repos/o/r/issues/5/labels/filing", None),
    ("POST", "/graphql", {"query": "mutation($p: ID!) { x }", "variables": {}}),
    ("POST", "/graphql", {"query": "# a comment first\nquery { x }", "variables": {}}),
    ("POST", "/graphql", {"variables": {}}),
], ids=["filing", "edit", "unmark", "mutation", "unreadable-query", "no-query"])
@pytest.mark.parametrize("status", sorted(CUT_OFF))
def test_a_write_github_cut_off_is_sent_once_and_says_to_read_it_back(method, path, body,
                                                                      status):
    """GitHub may have done a write before it cut the answer off, and a filing sent twice
    makes two cards (plan#1816): a write's 5xx is raised at once, and says so, for each
    answer a READ is re-sent after.  A GraphQL body whose text is not known to be a query is
    a write too: never re-sent."""
    session = _Session(_Response(status, {"message": "cut off"}), _Response(200, {"ok": 1}))
    slept = []
    with pytest.raises(GitHubError, match=f"-> {status}: .*a write is never re-sent, as GitHub "
                                          "may have done it.*read the tracker back") as raised:
        _client(session, slept).rest(method, path, body)
    assert (raised.value.status, slept, len(session.sent)) == (status, [], 1)


def test_a_500_to_a_read_is_no_cut_off_and_is_raised_at_once():
    """Only 502, 503 and 504 are a cut-off; a 500 is raised at once, with no write's note."""
    session = _Session(_Response(500, {"message": "Server Error"}), _Response(200, {"ok": 1}))
    with pytest.raises(GitHubError) as raised:
        _client(session, []).rest("GET", "/repos/o/r")
    assert raised.value.status == 500 and len(session.sent) == 1
    assert str(raised.value) == f'GET {API}/repos/o/r -> 500: {{"message": "Server Error"}}'


def test_a_cut_off_read_waits_as_long_as_its_retry_after_says():
    """A 503 naming its wait is re-sent no sooner (the module docstring's rule for any
    re-send), and one naming no time quill can read stops it unsent."""
    session = _Session(_Response(503, {"message": "busy"}, {"retry-after": "30"}),
                       _Response(200, {"ok": 1}))
    slept = []
    assert _client(session, slept).rest("GET", "/repos/o/r") == {"ok": 1}
    assert slept == [30.0]
    unreadable = _Session(_Response(503, {"message": "busy"}, {"retry-after": "soon"}))
    with pytest.raises(GitHubError, match="answered 503, and GitHub's retry-after 'soon' is "
                                          "no time quill can read"):
        _client(unreadable, []).rest("GET", "/repos/o/r")


def test_a_rate_limit_and_a_cut_off_share_one_budget():
    """One request's waits, for either reason, add up against WAIT_BUDGET_SECONDS: 3,595 s
    for a rate limit, then 2 s for a cut-off, fit; the next cut-off's 4 s does not."""
    session = _Session(_Response(403, _SECONDARY, {"retry-after": "3595"}),
                       _Response(504, {"message": "cut off"}),
                       _Response(504, {"message": "cut off"}),
                       _Response(200, {"ok": 1}))
    slept = []
    with pytest.raises(GitHubError, match="-> 504, and the next re-send needs a wait of 4 s "
                                          "after 3597 s waited, past the 3600 s"):
        _client(session, slept).rest("GET", "/repos/o/r")
    assert (slept, len(session.sent)) == ([3595.0, 2.0], 3)


def test_a_get_answered_with_an_empty_body_is_an_error_naming_it():
    """Every GET quill sends reads a JSON body; an empty one would reach a caller as None."""
    with pytest.raises(GitHubError, match=r"GET .*/repos/o/r/issues/5 -> 200 with an empty body"):
        GitHub("t", _Session(_Response(200))).rest("GET", "/repos/o/r/issues/5")


@pytest.mark.parametrize("call", [
    lambda github: github.rest("GET", "/repos/o/r"),
    lambda github: github.graphql("query { x }"),
    lambda github: github.graphql_lookup("query { x }"),
], ids=["rest", "graphql", "lookup"])
def test_a_body_that_is_not_json_is_an_error_naming_the_request(call):
    """``requests``' JSONDecodeError says only where the parse failed; the error names the
    request, and the rate-limit check never trips on the body first."""
    with pytest.raises(GitHubError, match=r"-> 200 with a body that is not JSON: '<html>"):
        call(GitHub("t", _Session(_NotJson())))


@pytest.mark.parametrize("answer", [None, {"data": None}, {}, [1], {"data": [1]}],
                         ids=["empty", "null", "absent", "a-list", "data-a-list"])
@pytest.mark.parametrize("lookup", [False, True], ids=["graphql", "lookup"])
def test_a_graphql_answer_with_no_data_object_is_an_error_not_a_traceback(answer, lookup):
    """plan#1812: two runs ended in ``AttributeError: 'NoneType' object has no attribute
    'get'`` while GitHub answered 5xx.  An answer holding no ``data`` object is refused where
    it is read, so every caller's read takes quill's clean failed path."""
    github = GitHub("t", _Session(_Response(200, answer)))
    read = github.graphql_lookup if lookup else github.graphql
    with pytest.raises(GitHubError, match="graphql: an answer with no data object"):
        read("query { x }")


@pytest.mark.parametrize(("call", "answer"), [
    (lambda github: github.graphql("mutation { x }"), _Response(200, {"data": None})),
    (lambda github: github.rest("PATCH", "/repos/o/r/issues/5", {"title": "t"}), _NotJson()),
    (lambda github: github.rest("POST", "/repos/o/r/issues", {"title": "t"}), _Response(201)),
], ids=["mutation-no-data", "patch-not-json", "post-empty"])
def test_an_answer_quill_cannot_read_to_a_write_says_github_answered_it(call, answer):
    """GitHub answered the write, so it may stand: the error says so and to read the tracker
    back, as a write's 5xx does, and the write is sent once (plan#1812's class)."""
    session = _Session(answer, _Response(200, {"ok": 1}))
    with pytest.raises(GitHubError, match="; GitHub answered the write, which quill never "
                                          "re-sends: read the tracker back before running "
                                          "the command again$"):
        call(GitHub("t", session))
    assert len(session.sent) == 1


@pytest.mark.parametrize("call", [
    lambda github: github.graphql("query { x }"),
    lambda github: github.rest("GET", "/repos/o/r"),
], ids=["query", "get"])
def test_an_answer_quill_cannot_read_to_a_read_says_nothing_of_a_write(call):
    """A read's unreadable answer names the request and no write."""
    with pytest.raises(GitHubError) as raised:
        call(GitHub("t", _Session(_NotJson())))
    assert "write" not in str(raised.value)


def test_only_a_delete_may_be_answered_with_an_empty_body():
    """GitHub answers 204 No Content to a DELETE; every other REST request quill sends is
    answered a JSON body its caller reads, so an empty one is refused (plan#1812).  GraphQL's
    is refused for holding no data object (the test above)."""
    assert GitHub("t", _Session(_Response(204))).rest("DELETE", "/repos/o/r/x") is None
    for method in ("GET", "POST", "PATCH"):
        with pytest.raises(GitHubError, match=f"{method} .* -> 200 with an empty body"):
            GitHub("t", _Session(_Response(200))).rest(method, "/repos/o/r/x", None)


def test_a_read_re_sent_once_then_refused_says_one_re_send():
    """The count of re-sends is read as English: one re-send, not "1 re-sends"."""
    session = _Session(_Response(504, {"message": "cut off"}), _Response(404, {"message": "no"}))
    with pytest.raises(GitHubError, match=r"-> 404 after 1 re-send: "):
        _client(session, []).rest("GET", "/repos/o/r")
