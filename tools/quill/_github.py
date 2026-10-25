"""The one HTTP path quill takes to GitHub, under either of two identities.

The private tracker (ruling ``balance:R-BAL170``) is written by two identities
on purpose.  The developer's own login -- the token ``gh`` already holds --
configures the tracker (:mod:`setup_tracker`).  Every CARD write goes through
quill's GitHub App instead, a separate identity, so a check can tell a
tool write from the developer's own edit and never act on his.

An App proves who it is with a JSON Web Token signed by its private key, and
trades that token for an installation token, which quill narrows to the one
repository it reads and writes (:func:`installation_token`).  The signing is
done here with ``cryptography`` (pinned in ``requirements.txt``) rather than a
JWT package, because RS256 over two JSON segments is the whole of what the App
flow needs.

**A redirect is an error, never followed.**  GitHub answers 301 for an issue
transferred to another repository and for a renamed repository; followed, a read
would describe another repository's issue as the tracker's, and a write would
land where it was not aimed (``requests`` re-sends a 307 or 308 with its body).
So no request follows one, and any 3xx is raised like a 4xx.

**A rate limit is waited out, as GitHub's documentation directs, and then STOPS.**
A refusal for a rate limit (:func:`_rate_limited`) is re-sent after the wait the
answer names -- ``retry-after``; else, with ``x-ratelimit-remaining`` at zero,
until ``x-ratelimit-reset`` -- or else after a minute doubled per retry, and after
:data:`RATE_LIMIT_RETRIES` waits quill stops: "Continuing to make requests while
you are rate limited may result in the banning of your integration" (both REST
and GraphQL pages, read 2026-10-09).  GitHub's pages do not say whether a request
refused for a rate limit was performed; they say to retry it, and quill does.

**Quill never re-sends sooner than GitHub's answer asks, so a wait it cannot honour
STOPS it instead** (:func:`_wait_seconds`): a ``retry-after`` or reset it cannot
read as a time (not a number of seconds or an HTTP date, negative, or not finite),
and a wait that would carry one request's waits past :data:`RATE_LIMIT_BUDGET_SECONDS`.
Shortening the wait would be the early retry GitHub warns can ban the integration;
a stop costs a re-run, and every quill command finishes what a stopped run left.

Nothing in this module reads or writes the code repository.  Its tests feed it
recorded responses and never call GitHub.
"""
from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 30
_GRAPHQL = API + "/graphql"

#: How many times one request is re-sent after a rate-limit answer before quill stops.
#: GitHub says to "throw an error after a specific number of retries" and names no
#: number; five backoff waits from a minute come to 31 minutes, inside the hour an
#: installation token lives.
RATE_LIMIT_RETRIES = 5
#: The wait when GitHub's answer names none: "wait for at least one minute before
#: retrying", doubled for each further retry of the same request.
BACKOFF_SECONDS = 60
#: The most one request waits in all before quill stops: the hour an installation token
#: lives, past which the re-sent request would go with a dead token.  The five backoff
#: waits come to 1,860 seconds, inside it; a wait GitHub names that would cross it
#: stops quill rather than being shortened (the module docstring).  It bounds ONE
#: request's waits, not a command's: a command of many requests may wait longer in all,
#: each request within its own budget (review of A2b).
RATE_LIMIT_BUDGET_SECONDS = 3600
#: How GitHub's error message says a rate limit refused the request ("You have exceeded a
#: secondary rate limit", "API rate limit exceeded").
_RATE_LIMIT_WORDS = re.compile(r"rate limit", re.IGNORECASE)

#: Where the App's two credentials live: OUTSIDE every repository, readable by
#: the developer's account only.  ``app.json`` holds the App's client ID (an
#: identifier, not a secret); ``app.pem`` is the private key GitHub issued.
APP_DIR = Path.home() / ".config" / "shekel-quill"

#: GitHub refuses a token whose ``iat`` is in its future, and this host's clock
#: and GitHub's need not agree to the second; the App documentation's own
#: example backdates by 60 seconds.  ``exp`` may be at most ten minutes ahead.
_CLOCK_SKEW_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540


class GitHubError(RuntimeError):
    """A request GitHub answered with an error, carrying the HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        """Keep the HTTP status beside the message (200 for a GraphQL error)."""
        super().__init__(message)
        self.status = status


class CredentialsError(ValueError):
    """The App's credentials in :data:`APP_DIR` are there but cannot be used: ``app.json``
    holds no client ID, or ``app.pem`` no RSA private key quill can sign a token with.
    Raised where each file is read or parsed (:func:`app_credentials`, :func:`app_jwt`).
    ``quill`` and ``release plan`` report it as a tracker they cannot reach (exit 2) and
    ``setup_tracker`` as a difference (exit 1); ``migrate`` does not catch it, and its
    docstring names an App key file that cannot be parsed as a traceback (exit 1).  A
    file that is MISSING stays the :class:`FileNotFoundError` reading it raises."""


def _graphql_errors_say_rate_limit(response) -> bool:
    """Whether a 200 GraphQL answer is a rate-limit refusal: it carries ``errors`` and one of
    them says so, by type or by message.  An answer's DATA is never read for the words: a
    card's body may hold them."""
    payload = response.json() if response.content else None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    return any(error.get("type") == "RATE_LIMITED"
               or _RATE_LIMIT_WORDS.search(str(error.get("message", "")))
               for error in errors or ())


def _rate_limited(response, url: str) -> bool:
    """Whether GitHub refused ``response``'s request for a rate limit.

    As GitHub documents it (read 2026-10-09): a primary limit is "a ``403`` or ``429``
    response, and the ``x-ratelimit-remaining`` header will be ``0``"; a secondary one is
    "a ``403`` or ``429`` response and an error message that indicates that you exceeded a
    secondary rate limit".  GraphQL also answers either kind ``200`` "with an error
    message".  A 403 or 429 saying none of that is a refusal of another kind ("Resource
    not accessible by integration"), raised at once; one that also carries ``retry-after``
    is waited out as that header directs.  By the text, ANY 403 with
    ``x-ratelimit-remaining`` at zero is the primary limit, so it is waited out until the
    reset, a permission refusal sent with the last request of the hour included.  A 200
    whose remaining count reached zero is the last request the limit allowed, answered.
    """
    status, headers = response.status_code, response.headers
    if status == 200:
        return url == _GRAPHQL and _graphql_errors_say_rate_limit(response)
    if status not in (403, 429):
        return False
    return ("retry-after" in headers or headers.get("x-ratelimit-remaining") == "0"
            or bool(_RATE_LIMIT_WORDS.search(response.text or "")))


def _readable(value: str, now: float, *, epoch: bool) -> float | None:
    """A header's time as epoch seconds (``epoch``) or as seconds from ``now``: a finite,
    non-negative number, or (``retry-after``'s other form, RFC 9110) an HTTP date, read
    as the seconds from ``now`` to it; None for anything else."""
    try:
        number = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if epoch or when.tzinfo is None:
            return None
        return max(0.0, when.timestamp() - now)
    return number if math.isfinite(number) and number >= 0 else None


def _wait_seconds(response, retry: int, now: float) -> float:
    """How long to wait before re-sending a request refused for a rate limit, the
    ``retry``-th time (from 0), at ``now`` (epoch seconds): GitHub's three cases, in its
    order.

    Raises:
        GitHubError: When the case that applies names a time that cannot be read
            (:func:`_readable`): quill cannot honour it, and never retries sooner than
            GitHub asks (the module docstring).
    """
    headers = response.headers
    if "retry-after" in headers:
        wait = _readable(headers["retry-after"], now, epoch=False)
        named = f"retry-after {headers['retry-after']!r}"
    elif headers.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in headers:
        reset = _readable(headers["x-ratelimit-reset"], now, epoch=True)
        wait = None if reset is None else max(0.0, reset - now) + 1
        named = f"x-ratelimit-reset {headers['x-ratelimit-reset']!r}"
    else:
        return float(BACKOFF_SECONDS * 2 ** retry)
    if wait is None:
        raise GitHubError(response.status_code,
                          f"rate limited, and GitHub's {named} is no time quill can read, so "
                          "it cannot wait as asked; stopped rather than re-sent early")
    return wait


class GitHub:
    """GitHub's REST and GraphQL APIs under one bearer token.

    ``session`` is any object with ``requests.Session.request``'s signature;
    the tests pass one that replays recorded responses.  ``sleep`` and ``clock``
    wait out a rate limit (the module docstring); tests pass ones that do not wait.
    """

    def __init__(self, token: str, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time) -> None:
        """Send every request with ``token``, through ``session`` when one is given."""
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _send(self, method: str, url: str, body: dict | None) -> requests.Response:
        """Send one request, following no redirect and waiting out a rate limit; raise
        :class:`GitHubError` on any 3xx, 4xx or 5xx, on a rate limit that outlasts
        :data:`RATE_LIMIT_RETRIES` waits, and on a wait it cannot honour: one it cannot
        read (:func:`_wait_seconds`), or one that would carry this request's waits past
        :data:`RATE_LIMIT_BUDGET_SECONDS`."""
        waited = 0.0
        for retry in range(RATE_LIMIT_RETRIES + 1):
            response = self._session.request(
                method, url, headers=self._headers, json=body, timeout=TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if not _rate_limited(response, url):
                break
            if retry == RATE_LIMIT_RETRIES:
                raise GitHubError(
                    response.status_code,
                    f"{method} {url} -> still rate limited after {RATE_LIMIT_RETRIES} waits; "
                    f"stopped, as GitHub directs: {response.text[:500]}",
                )
            wait = _wait_seconds(response, retry, self._clock())
            if waited + wait > RATE_LIMIT_BUDGET_SECONDS:
                raise GitHubError(
                    response.status_code,
                    f"{method} {url} -> rate limited, and GitHub asks a wait of {wait:.0f} s "
                    f"after {waited:.0f} s waited, past the {RATE_LIMIT_BUDGET_SECONDS} s one "
                    "request may wait; stopped rather than re-sent early")
            self._sleep(wait)
            waited += wait
        if response.status_code >= 300:
            raise GitHubError(
                response.status_code,
                f"{method} {url} -> {response.status_code}: {response.text[:500]}",
            )
        return response

    def rest(self, method: str, path: str, body: dict | None = None):
        """One REST call; the decoded JSON body, or None for an empty one."""
        response = self._send(method, API + path, body)
        return response.json() if response.content else None

    def rest_text(self, method: str, path: str, body: dict | None = None) -> str:
        """One REST call whose answer is text, not JSON (``/markdown`` answers HTML); the
        text."""
        return self._send(method, API + path, body).text

    def graphql(self, query: str, **variables) -> dict:
        """One GraphQL call; its ``data``.  GraphQL reports errors in a 200."""
        payload = self.rest("POST", "/graphql", {"query": query, "variables": variables})
        if payload.get("errors"):
            raise GitHubError(200, f"graphql: {json.dumps(payload['errors'])[:500]}")
        return payload["data"]

    def graphql_lookup(self, query: str, **variables) -> dict:
        """One GraphQL call whose unresolvable fields BELOW the top-level object are
        ABSENT, not errors.

        Asked for an issue number nobody filed, GitHub answers 200 with that
        field ``null`` beside the others and a ``NOT_FOUND`` error whose ``path``
        names it under the repository (measured 2026-10-04); this answers the
        ``data`` with the field None.  Only that two-step path is read as absent: a
        top-level object it cannot find -- the repository itself, a one-step path
        (measured 2026-10-04) -- and anything deeper, which no read has measured,
        still raise, as does every other error.
        """
        payload = self.rest("POST", "/graphql", {"query": query, "variables": variables})
        others = [e for e in payload.get("errors") or []
                  if e.get("type") != "NOT_FOUND" or len(e.get("path") or ()) != 2
                  or e["path"][0] != "repository"]
        if others or payload.get("data") is None:
            raise GitHubError(200, f"graphql: {json.dumps(payload.get('errors'))[:500]}")
        return payload["data"]


def paged(read: Callable[..., dict],
          connection: Callable[[dict], dict]) -> tuple[list[dict], dict]:
    """Every node of a GraphQL connection, read a page at a time: the one walk of a
    connection, which every read of :mod:`_tracker` that pages takes.

    ``read(after=cursor)`` sends one page's query (None for the first page), and
    ``connection`` finds the connection, with its ``pageInfo``, in that page's answer.  The
    nodes in GitHub's order, and the last answer read, for a caller that reads what the
    query holds around the connection (``_tracker.Tracker.edits``)."""
    nodes, after = [], None
    while True:
        answer = read(after=after)
        page = connection(answer)
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes, answer
        after = page["pageInfo"]["endCursor"]


def user_token() -> str:
    """The developer's own token, as ``gh`` holds it."""
    result = subprocess.run(
        ["gh", "auth", "token"], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _segment(raw: bytes) -> str:
    """Base64url without padding, the encoding every JWT segment uses."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def app_jwt(client_id: str, private_key_pem: bytes, now: int) -> str:
    """A JSON Web Token proving the App's identity, valid nine minutes from ``now``.

    RS256 (RSASSA-PKCS1-v1_5 over SHA-256), issuer the App's client ID, which
    GitHub's App documentation recommends over the numeric App ID.

    Raises:
        CredentialsError: When ``private_key_pem`` is no RSA private key quill can sign
            with: not PEM or corrupt (``cryptography`` raises ValueError), encrypted
            (TypeError, no password being given), of a key type it does not support
            (UnsupportedAlgorithm), a key of another algorithm, or an RSA key too small
            to sign an RS256 token (ValueError at signing).
    """
    try:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as error:
        raise CredentialsError(f"the App's private key cannot be loaded: {error}") from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CredentialsError("a GitHub App private key is an RSA key")
    header = _segment(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = {
        "iat": now - _CLOCK_SKEW_SECONDS,
        "exp": now + _JWT_LIFETIME_SECONDS,
        "iss": client_id,
    }
    signing_input = f"{header}.{_segment(json.dumps(claims).encode())}"
    try:
        signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(),
                             hashes.SHA256())
    except ValueError as error:
        raise CredentialsError(f"the App's private key cannot sign a token: {error}") from error
    return f"{signing_input}.{_segment(signature)}"


def app_credentials(app_dir: Path = APP_DIR) -> tuple[str, bytes]:
    """The App's client ID and private key, read from ``app_dir``.

    Raises:
        CredentialsError: When ``app.json`` is not UTF-8 JSON that Python's parser takes
            (``json.loads`` raises ValueError, JSONDecodeError and UnicodeDecodeError among
            them, and RecursionError past its nesting limit), or not an object holding a
            ``client_id`` that is a non-empty string.
        OSError: When either file cannot be read (FileNotFoundError when it is missing).
    """
    path = app_dir / "app.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError) as error:
        raise CredentialsError(f"{path} is not JSON: {error}") from error
    client_id = config.get("client_id") if isinstance(config, dict) else None
    if not isinstance(client_id, str) or not client_id:
        raise CredentialsError(f"{path} holds no client_id: it is a JSON object whose "
                               "client_id is the App's client ID, a non-empty string")
    return client_id, (app_dir / "app.pem").read_bytes()


def app_installation(org: str, app_token: str, session=None) -> dict:
    """The App's installation on ``org``, as GitHub describes it (id, permissions, ...)."""
    return GitHub(app_token, session).rest("GET", f"/orgs/{org}/installation")


def installation_token(installation_id: int, app_token: str, *, repository: str | None,
                       session=None) -> str:
    """A one-hour token acting as the App, limited to what its installation grants, and,
    given a ``repository`` (a name in the installation's organization), to that one
    repository: GitHub's ``repositories`` narrowing, read back from its answer.

    ``None`` keeps every repository the installation reaches, for the one caller that
    must see them all (``setup_tracker.check_app``).
    """
    body = None if repository is None else {"repositories": [repository]}
    response = GitHub(app_token, session).rest(
        "POST", f"/app/installations/{installation_id}/access_tokens", body
    )
    if repository is not None:
        reached = [each["name"] for each in response.get("repositories") or ()]
        if reached != [repository]:
            raise GitHubError(201, f"a token narrowed to {repository!r} reaches {reached}")
    return response["token"]
