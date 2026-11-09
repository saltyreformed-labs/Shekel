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
of them typing codes in turn.  It is read for that user alone, once the account
is found: what it is worth is that account's record's to say, and it signs
nobody in.

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

#: The cookie's name before its user's id and any prefix.
_NAME = "shekel_device"

#: The prefix a browser pins to one origin, for a Secure cookie on Path=/.
_HOST_PREFIX = "__Host-"


def _cookie_name(user_id: int) -> str:
    """Return the name of *user_id*'s device cookie in this configuration.

    Args:
        user_id: The ``auth.users.id`` whose cookie it is.

    Returns:
        ``__Host-shekel_device_<id>`` where the session cookie is Secure,
        else ``shekel_device_<id>``.
    """
    name = f"{_NAME}_{user_id}"
    if current_app.config["SESSION_COOKIE_SECURE"]:
        return f"{_HOST_PREFIX}{name}"
    return name


def device_token(user_id: int) -> str | None:
    """Return the token this request's browser holds for *user_id*, or ``None``.

    Args:
        user_id: The ``auth.users.id`` signing in or signed in.

    Returns:
        The cookie's value, or ``None`` when the browser sent none.
    """
    return request.cookies.get(_cookie_name(user_id))


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
