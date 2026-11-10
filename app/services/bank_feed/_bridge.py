"""
Shekel Budget App -- The bank feed's TRANSPORT: every request to Bridge, and what refuses it

Plan step ``bank_import:X-f6b-2``: the leaf of :mod:`app.services.bank_feed`
that SENDS.  Born in the package's move-only split (the package docstring
says how the move was graded); the two paragraphs below stood at the head of
the flat module and moved here with their subjects.

**Every request goes to Bridge's host and nowhere else** (rulings
**R-BI28**, the host pin, and **R-BI29**, no redirect is followed).  The
pasted token and Bridge's answered access URL are both URLs this module
would otherwise send requests to unread, so each is refused unless the host
``requests`` would reach is :data:`BRIDGE_HOST` or a subdomain of it --
read by ``requests``' own preparation of the URL, because ``urlsplit`` and
``requests`` disagree on a crafted authority and a check over the wrong
parser is no check (:func:`_refuse_unless_bridge`).  The listing and,
later, the sync read the stored URL and need no check of their own: the
claim refused a foreign one before storing it, and the claim is the only
writer.  And a request ENDS where it started: ``requests`` would follow a
redirect to any host the ``Location`` named, so every request is sent with
``allow_redirects=False`` and a ``3xx`` answer is refused -- spelled once,
in :func:`_ask_bridge`, the module's only request site.

**The credential never reaches a sentence, by construction rather than by
the scrubber** (the 3b review's M1: the log scrubber cannot see a
TRACEBACK).  ``requests`` keeps the URL's userinfo in ``response.url`` and in
``str(HTTPError)`` (measured 2026-09-20: ``401 Client Error: Unauthorized for
url: https://alice:s3cr3t@...``), so nothing here repeats ``str(exc)`` or
puts a URL into a refusal: a transport failure becomes a
:class:`~app.exceptions.BridgeRefused` carrying its CLASS and, when Bridge
answered, its STATUS, and the sentence the owner reads names those two
facts.  This module logs nothing itself: every refusal it raises is logged
once, at WARNING with its class, by the door that catches it
(:func:`app.routes.accounts._statement_doors.log_refusal`, ledger row
**BI-499**), which is why a refusal is never logged twice.  Bridge's own
guidance strings (``errlist``, ``x-api-message``) are shown on the panel, as
Bridge asks ("always show those errors to your end users"), and are not
logged: they are Bridge's text about the owner's connection, not the app's
event.

Services boundary: plain data in, plain data out, no Flask import.  Nothing
here reads or writes the database.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import requests

from app.exceptions import BankFeedError, BridgeRefused

#: ``(connect, read)`` seconds for every request to Bridge.  Bridge answers a
#: claim and a balances-only listing in well under a second; the read bound
#: is generous because a listing is Bridge asking the aggregator, and the
#: connect bound is short because an unreachable host should not hold a
#: request thread for the read bound.
BRIDGE_TIMEOUT_SECONDS = (5, 30)

#: The host every URL this module sends a request to must be, or be a
#: subdomain of: ruling **R-BI28** (the host pin, strict and fail-closed).
#: A setup token is base64 of a URL the app POSTs to, and the claim's
#: answer is a URL the app then GETs from for as long as the feed stands,
#: so without the pin a crafted paste could point the container at any
#: ``https`` host it can reach.  Bridge claims at ``bridge.simplefin.org``
#: and ``beta-bridge.simplefin.org``; a non-Bridge SimpleFIN server is
#: unusable until this constant is widened, the ruling's stated cost.
BRIDGE_HOST = "simplefin.org"


def _refuse_unless_bridge(
    url: str, *, unreadable: BankFeedError, foreign: BankFeedError,
) -> None:
    """Refuse *url* unless it is an ``https`` URL on Bridge's host.

    **The ONE walk that decides whether this module may send a request to a
    URL**, for the pasted claim URL and for the access URL Bridge answers
    with alike (ruling **R-BI28**); the callers differ only in which refusal
    each state is.

    **The host is read the way ``requests`` will connect to it, not the way
    ``urlsplit`` reads the raw string**, because the two disagree and the
    disagreement is the bypass.  Measured 2026-09-20 (requests 2.34.2,
    urllib3 2.6.3): ``https://evil.invalid\\@bridge.simplefin.org/x`` is host
    ``bridge.simplefin.org`` to ``urlsplit`` -- the userinfo ends at the last
    ``@`` -- while ``requests`` stops the authority at the backslash and
    connects to ``evil.invalid`` with ``/%5C@bridge.simplefin.org/x`` as the
    path.  A pin over ``urlsplit`` would have passed that token.  So this
    prepares the URL exactly as ``requests.post``/``get`` do
    (``PreparedRequest.prepare_url``: strip leading whitespace, parse with
    urllib3, IDNA-encode a non-ASCII host, requote) and reads the hostname
    off the PREPARED url, which is the two calls the adapter itself makes to
    pick the connection host (``requests/adapters.py``,
    ``_urllib3_request_context``: ``urlparse(request.url).hostname``).  What
    is checked is what is reached, by construction rather than by a table of
    characters this module would have to keep in step with two parsers.

    The suffix test is exact: the host is :data:`BRIDGE_HOST` itself or ends
    in ``"." + BRIDGE_HOST``, so ``simplefin.org.evil.invalid`` and
    ``evilsimplefin.org`` are foreign.  ``requests`` lowercases the host, so
    the comparison needs no case fold of its own.

    Args:
        url: The URL as this module would hand it to ``requests``.
        unreadable: Raised when ``requests`` would send nothing to *url* at
            all (no scheme or host, a scheme other than ``https``, a host
            urllib3 refuses to parse, an IDNA label it cannot encode).
        foreign: Raised when the request would reach a host that is neither
            :data:`BRIDGE_HOST` nor a subdomain of it.

    Raises:
        BankFeedError: One of the two the caller supplied.
    """
    prepared = requests.PreparedRequest()
    try:
        prepared.prepare_url(url, None)
        # ``prepare_url`` leaves a non-http scheme's URL untouched, and
        # ``urlsplit`` raises ``ValueError`` on a bracket-malformed host in
        # one (``ftp://[x``); an ``https`` URL that prepared is a URL urllib3
        # built, which ``urlsplit`` reads.
        parts = urlsplit(prepared.url)
    except (requests.RequestException, ValueError):
        # ``from None``: ``InvalidURL``'s own message quotes the URL it could
        # not parse -- the access URL's credential, or the claim URL's token
        # -- and a chained cause is a traceback the scrubber cannot see
        # (module docstring; the ``from None`` at every ``_refused`` site).
        raise unreadable from None
    # The SCHEME is read the way ``requests`` picks the adapter
    # (``Session.get_adapter``: the prepared url, lower-cased, starts with
    # ``https://``), as the host is read the way it picks the connection:
    # ``urlsplit`` strips a leading control character and reads ``https``
    # off ``\\x00https://...``, which ``prepare_url`` left untouched and
    # ``requests`` would refuse as ``InvalidSchema`` -- unreadable, not
    # unreachable.
    if not prepared.url.lower().startswith("https://") or not parts.hostname:
        raise unreadable
    host = parts.hostname
    if host != BRIDGE_HOST and not host.endswith("." + BRIDGE_HOST):
        raise foreign


def _refused(exc: requests.RequestException, doing: str) -> BridgeRefused:
    """Turn a ``requests`` failure into the refusal the owner reads.

    **Neither the exception's text nor its URL survives this**: both carry
    the credential (module docstring).  What survives is the class and, when
    Bridge answered at all, the status.

    Args:
        exc: What ``requests`` raised.
        doing: The act, as a noun phrase: ``"the claim"``, ``"the listing"``.

    Returns:
        The :class:`BridgeRefused` to raise.
    """
    response = getattr(exc, "response", None)
    status = response.status_code if response is not None else None
    error_class = type(exc).__name__
    if status is None:
        sentence = (
            f"Bridge could not be reached for {doing} ({error_class}), so "
            f"nothing was changed.  Try again in a moment."
        )
    else:
        sentence = (
            f"Bridge answered HTTP {status} to {doing}, so nothing was "
            f"changed."
        )
    return BridgeRefused(sentence, status=status, error_class=error_class)


def _ask_bridge(method: str, url: str, doing: str, **kwargs) -> requests.Response:
    """Send the ONE kind of request this module makes, or refuse.

    **The only ``requests`` call site under this module**, so what every
    request to Bridge carries and refuses is spelled once: the claim, the
    listing, and the sync's request when it lands, all come through here.

    * ``timeout=BRIDGE_TIMEOUT_SECONDS``.
    * ``allow_redirects=False`` (ruling **R-BI29**): ``requests`` would
      otherwise follow a redirect to any host and any scheme the
      ``Location`` named, which is exactly the reach ruling **R-BI28** pins
      away; with the flag off it sends NOTHING after a ``3xx`` (read
      2026-09-20 on requests 2.34.2: ``Session.send`` skips the
      ``resolve_redirects`` send loop and the adapter is called with
      ``redirect=False``; pinned at the adapter by
      ``test_a_redirect_sends_nothing_after_the_first_request``).
    * A transport failure or an error status is :func:`_refused` -- the
      class and the status, never the exception's text or URL.
    * A ``3xx`` answer, which ``raise_for_status`` lets through, is
      refused HERE as ``Redirect`` carrying its status, before a body that
      is not Bridge's answer can be read as one.  Every ``3xx``, not only
      the five ``requests`` calls a redirect: a ``300`` or a ``304`` is not
      Bridge's answer either.
    * A ``3xx`` whose ``Location`` ``requests`` cannot decode or parse
      raises OUTSIDE ``RequestException`` even with redirects off, because
      ``Session.send`` still builds the unsent ``Response.next`` from it
      (measured 2026-09-20: a latin-1 byte in the header raises
      ``UnicodeDecodeError``, ``http://[::1`` raises ``ValueError``; the
      ``Response`` is lost with the exception, so no status survives).
      That is refused as its own class rather than escaping as a 500 htmx
      cannot swap; nothing was sent to the ``Location`` either way.

    Args:
        method: ``"post"`` or ``"get"``; looked up on ``requests`` at call
            time, which is where the tests' spy stands in for Bridge.
        url: Where to.  Every URL that reaches here passed
            :func:`_refuse_unless_bridge`, at the claim.
        doing: The act, as a noun phrase: ``"the claim"``, ``"the account
            listing"``.
        **kwargs: The request's own arguments (``params``).

    Returns:
        Bridge's answer: a ``2xx``, past ``raise_for_status``.

    Raises:
        BridgeRefused: Bridge could not be reached, answered an error,
            answered a redirect, or answered a redirect this app could not
            read.
    """
    try:
        response = getattr(requests, method)(
            url, timeout=BRIDGE_TIMEOUT_SECONDS, allow_redirects=False,
            **kwargs,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _refused(exc, doing) from None
    except ValueError as exc:
        raise BridgeRefused(
            f"Bridge answered {doing} with a redirect this app could not "
            f"read ({type(exc).__name__}), which it would not have followed, "
            f"so nothing was changed.",
            status=None, error_class=type(exc).__name__,
        ) from None
    if 300 <= response.status_code < 400:
        raise BridgeRefused(
            f"Bridge answered {doing} with a redirect (HTTP "
            f"{response.status_code}), which this app does not follow, "
            f"rather than an answer; nothing was changed.",
            status=response.status_code, error_class="Redirect",
        )
    return response
