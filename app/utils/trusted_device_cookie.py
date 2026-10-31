"""
Shekel Budget App -- the trusted-device cookie.

The browser's half of a trusted device (plan#2173, ruling plan#2406): the
cookie carrying the token
:func:`app.services.trusted_device_service.trust_device` returns.  The record
in ``auth.trusted_devices`` holds only that token's hash and decides whether
the device is trusted; this cookie only names which record the browser means.

**One cookie per user per browser.**  The name carries its user's id
(``shekel_device_<id>``), so two people who sign in on one browser each keep
their own trust.  A single name would let the second person's trust overwrite
the first one's cookie, leaving the first one's record trusted by nothing and
the two of them typing codes in turn.  The cookie is read only after that
user's password was correct, for that user.

**Its flags.**  HttpOnly, so no script reads it.  SameSite=Strict, so only
this app's own pages send it, the sign-in form among them.  Path=/, and as
long-lived as the trust itself
(:data:`~app.models.trusted_device.TRUST_LIFETIME`), so it never outlives the
record or dies before it.  Wherever the session cookie is Secure
(``SESSION_COOKIE_SECURE``, the one source), this one is Secure too and takes
the ``__Host-`` prefix, which a browser honours only for a Secure, Path=/
cookie with no Domain: the cookie is then pinned to this exact origin.
"""

from flask import current_app, request
from werkzeug.wrappers import Response

from app.models.trusted_device import TRUST_LIFETIME

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
        response: The response that completes the sign-in.
        user_id: The ``auth.users.id`` who trusted this browser.
        token: The token :func:`~app.services.trusted_device_service.trust_device`
            returned.
    """
    response.set_cookie(
        _cookie_name(user_id),
        token,
        max_age=int(TRUST_LIFETIME.total_seconds()),
        path="/",
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=True,
        samesite="Strict",
    )
