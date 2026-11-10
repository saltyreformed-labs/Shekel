"""
Shekel Budget App -- the known-device cookie.

The browser's half of a known device (plan#2772, ruling plan#2994): the cookie
carrying the token a finished sign-in or re-confirm returns
(:func:`app.services.known_device_service.record_sign_in`,
:func:`~app.services.known_device_service.record_re_confirm`).  The record
in ``auth.known_devices`` holds only that token's hash and decides what the
browser's standing is; this cookie only names which record the browser means.

**One cookie per user per browser.**  The name carries its user's id
(``shekel_device_<id>``), so two people who sign in on one browser each keep
their own.  A single name would let the second person's sign-in overwrite the
first one's cookie, leaving the first one's record named by nothing and the two
of them typing codes in turn.  What a cookie is worth is its user's record's to
say, and it signs nobody in.  A door reads the one cookie of the account it
serves (:func:`device_token`).  The exception is ``/login``'s first question,
asked before the account is found: which users is this browser known to?  It
reads every device cookie the browser sends (:func:`device_tokens`), so the
email typed never decides which cookies are looked up, and a made-up cookie
named for an account costs a real email no more work than an email no account
has (ruling plan#3130).  A name counts only as the app writes it, its id with
no sign, no leading zero and no more than an ``id`` column holds, so no two
names read as one user's.

**Its flags.**  HttpOnly, so no script reads it.  SameSite=Strict, so only
this app's own pages send it, the sign-in form among them.  Path=/, and as
long-lived as the browser stays known
(:data:`~app.models.known_device.KNOWN_LIFETIME`), so it never outlives the
record or dies before it.  Wherever the session cookie is Secure
(``SESSION_COOKIE_SECURE``, the one source), this one is Secure too and takes
the ``__Host-`` prefix, which a browser honours only for a Secure, Path=/
cookie with no Domain: the cookie is then pinned to this exact origin.
"""

from flask import current_app, request
from werkzeug.wrappers import Response

from app.models.known_device import KNOWN_LIFETIME
from app.utils.digit_strings import parse_row_id

#: The cookie's name before its user's id and any prefix.
_NAME = "shekel_device"

#: The prefix a browser pins to one origin, for a Secure cookie on Path=/.
_HOST_PREFIX = "__Host-"


def _name_before_the_id() -> str:
    """Return what every device cookie's name holds before its user's id, in this configuration.

    Returns:
        ``__Host-shekel_device_`` where the session cookie is Secure, else
        ``shekel_device_``.
    """
    name = f"{_NAME}_"
    if current_app.config["SESSION_COOKIE_SECURE"]:
        return f"{_HOST_PREFIX}{name}"
    return name


def _cookie_name(user_id: int) -> str:
    """Return the name of *user_id*'s device cookie in this configuration.

    Args:
        user_id: The ``auth.users.id`` whose cookie it is.

    Returns:
        ``__Host-shekel_device_<id>`` where the session cookie is Secure,
        else ``shekel_device_<id>``.
    """
    return f"{_name_before_the_id()}{user_id}"


def device_tokens() -> dict[int, str]:
    """Return every device cookie this request's browser sent, by its user's id.

    A cookie is one only under a name :func:`_cookie_name` writes: the
    configuration's name before the id, then an id as the app writes one,
    which :func:`~app.utils.digit_strings.parse_row_id` reads.  So each user
    has one name, and an id no ``id`` column holds never reaches the query,
    whose bind cast would raise on it.  A name sent twice counts with its
    first value, as Werkzeug's ``request.cookies`` reads one.

    Returns:
        Each user id a device cookie is named for, and its value; empty when
        the browser sent none.
    """
    before = _name_before_the_id()
    tokens: dict[int, str] = {}
    for name, token in request.cookies.items():
        if not name.startswith(before):
            continue
        user_id = parse_row_id(name[len(before):])
        if user_id is not None:
            tokens[user_id] = token
    return tokens


def device_token(user_id: int) -> str | None:
    """Return the token this request's browser holds for *user_id*, or ``None``.

    The one of :func:`device_tokens` named for *user_id*, so a door that reads
    one user's cookie and ``/login``'s question of every cookie read one token.

    Args:
        user_id: The ``auth.users.id`` signing in or signed in.

    Returns:
        The cookie's value, or ``None`` when the browser sent none.
    """
    return device_tokens().get(user_id)


def set_device_cookie(response: Response, user_id: int, token: str) -> None:
    """Put *user_id*'s device token on *response* as its cookie.

    Args:
        response: The response that completes the sign-in or re-confirm.
        user_id: The ``auth.users.id`` who finished it here.
        token: The token
            :func:`~app.services.known_device_service.record_sign_in` or
            :func:`~app.services.known_device_service.record_re_confirm`
            returned.
    """
    response.set_cookie(
        _cookie_name(user_id),
        token,
        max_age=int(KNOWN_LIFETIME.total_seconds()),
        path="/",
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite="Strict",
    )
