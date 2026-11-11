"""
Shekel Budget App -- The rate limiter, and what its limits count against.

**Built here, above the services, not beside the other extensions.**  What a
signed-in request's limits count against reads a service, the browser's
record (ruling plan#3108), and :mod:`app.extensions` is the module every model
imports for ``db``: a limiter built there could reach that service only
through a loop of imports (ruling plan#3147).  Nothing below the routes and
the app factory imports this module, so no such loop can form.

:func:`rate_limit_key` is what every limit counts by, and :func:`whose_attempt`
the one rule for whose budget a signed-in or signing-in person spends: a known
browser's own, else the account's.  A signed-out visitor counts by the address
:func:`app.utils.client_address.counted_client` says it is.

**The storage settings are not the constructor's.**  The store, the default
limits, ``swallow_errors``, the in-memory fallback and the strategy are all
resolved from ``app.config`` inside ``Limiter.init_app()``.  Flask-Limiter 4.x
(``_extension.py:371-376``) prefers the constructor's ``storage_uri`` over the
value in ``app.config[ConfigVars.STORAGE_URI]``, so a value set here would
silently override TestConfig's ``memory://`` and ProdConfig's Valkey socket.
Keeping the constructor minimal lets each configuration own its own posture
(audit finding F-034, remediation Commit C-06).
"""

from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user

from app.services import known_device_service
from app.utils.client_address import counted_client
from app.utils.device_cookie import device_token

#: Where :func:`_known_browser_id` keeps, on ``g``, the record id it read for
#: each user and cookie this request was asked of.
_KNOWN_BROWSER_IDS = "_known_browser_ids"


def whose_attempt(user_id: int, device_id: int | None) -> str:
    """Return whose budget a request of *user_id*'s browser spends.

    The card's one rule for whose attempt it is (ruling plan#3108): a known
    browser's own, by its record's id, else the account's, which every other
    browser of the account shares.  So someone who spends the account's
    budget, with a copy of the owner's session or with guesses, cannot spend
    the owner's browser's too.  The signed-in pages' default limits
    (:func:`rate_limit_key`) and the tries the code form and re-confirm spend
    (:func:`app.routes.auth._helpers._spend_a_try`) both count by it.

    Args:
        user_id: The ``auth.users.id`` signed in or signing in.
        device_id: The ``auth.known_devices.id`` of this browser's record
            for *user_id*, as the known-device service read it, or ``None``
            for a stranger.  Never anything the browser sent: a key built
            from that would give a fresh budget for each made-up cookie.

    Returns:
        ``known-device:<id>`` or ``user:<id>``.
    """
    if device_id is None:
        return f"user:{user_id}"
    return f"known-device:{device_id}"


def _known_browser_id(user_id: int) -> int | None:
    """Return the id of this browser's record for *user_id*, read once per request.

    Each limit on a request asks for its key, and the app's defaults are two
    limits, so the record is read the first time and kept on ``g`` for the
    rest: one statement per signed-in request, and none for a browser that
    sent no cookie for *user_id*.  Kept by the user and the cookie asked of,
    so a request context that a test pushes inside its own, sharing its
    ``g``, is never answered for another user's or another cookie's record.

    Args:
        user_id: The signed-in ``auth.users.id``.

    Returns:
        What :func:`~app.services.known_device_service.known_browser_id`
        answered for the cookie this browser sent for *user_id*.
    """
    token = device_token(user_id)
    read = g.setdefault(_KNOWN_BROWSER_IDS, {})
    if (user_id, token) not in read:
        read[(user_id, token)] = known_device_service.known_browser_id(
            user_id, token,
        )
    return read[(user_id, token)]


def rate_limit_key() -> str:
    """Return what the current request's rate limits count against.

    A signed-in request counts against its browser's own budget when the
    browser is known, else its account's (:func:`whose_attempt`, ruling
    plan#3108).  So nobody else's traffic can spend that account's budget,
    and someone holding a copy of the owner's session but not the browser's
    cookie spends the account's and leaves the owner's browser its own.  A
    copy that took that cookie too shares the browser's budget, as at every
    per-browser defense.  The owner's browser itself spends the account's on
    a page it opens from another site, a link in an email for one: the device
    cookie is SameSite=Strict (:mod:`app.utils.device_cookie`), so it rides
    only this app's own navigations, and the next page opened from here
    spends the browser's own again.

    Anyone else counts against their address, which includes a signed-out
    visitor to a gated page: the default limits run before the login gate, so
    they spend their own count for that page and are then sent to sign in
    (ruling plan#3009).  Re-confirm's two doors, ``/reauth`` and
    ``/reauth/passkey``, the gated routes with a limit of their own, spend
    their tries inside their views
    (:func:`app.routes.auth._helpers._spend_a_try`), after the gate, so a
    signed-out visitor spends none of them.

    ``current_user`` costs no statement here: the request-id hook has already
    resolved it by the time any limit runs.  The browser's record costs one
    (:func:`_known_browser_id`), read before the owner's lock, as an id alone.

    Returns:
        ``known-device:<id>``, ``user:<id>`` or ``ip:<client>``.
    """
    if current_user.is_authenticated:
        return whose_attempt(current_user.id, _known_browser_id(current_user.id))
    return f"ip:{counted_client(get_remote_address())}"


limiter = Limiter(key_func=rate_limit_key)
