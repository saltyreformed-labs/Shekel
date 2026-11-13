"""
Shekel Budget App -- removing a user's passkey, the one act a page's Remove runs.

A user removes their own passkeys on their Security page (``auth.security``,
``auth.remove_passkey``; plan#2538), and the owner removes a companion's on
Settings > Companions (``settings.companion_remove_passkey``, as ruled for
her trusted devices, plan#3439).  The act, its refusal, its write, its
"was this you?" banner and its record are stated once, here, as forgetting a
trusted device is (:func:`app.routes._trusted_devices.forget_device_or_404`),
so a second door that removes a passkey calls this rather than copying its
body, which would carry the write and none of the refusal.

It reads the request (the signed-in user, for the log), so it belongs with
the routes, not in :mod:`app.services.passkey_service`, which takes plain ids.
"""

import logging

from flask import abort
from flask_login import current_user

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.user import User
from app.services import passkey_service
from app.utils.auth_helpers import log_refused_lookup
from app.utils.log_events import AUTH, EVT_PASSKEY_REMOVED, log_event
from app.utils.security_events import SecurityEventKind, record_security_event

logger = logging.getLogger(__name__)


def remove_passkey_or_404(user: User, passkey_id: int) -> None:
    """Remove *user*'s passkey *passkey_id*, committed with its banner, or abort 404.

    The service removes it; *user*'s "was this you?" banner is raised in the
    same transaction (``passkey_removed``), so a rollback of one rolls back
    the other; the request commits both; and the log records it.  The banner
    is *user*'s whoever pressed Remove: a companion whose passkey the owner
    removes is told so at her next page, as she is told of one she removes.  A passkey
    of another user and an id no passkey holds are one 404, the security
    response rule, and the refusal is logged the way every owner-scoped
    lookup's is (:func:`~app.utils.auth_helpers.log_refused_lookup`): the
    service looks the id up within *user*'s own passkeys, so it cannot tell
    the two apart, and neither can the response.

    No re-confirm is asked, as forgetting a trusted device asks none: removing
    a passkey takes a way in away and grants none.

    Args:
        user: The :class:`~app.models.user.User` whose passkey it must be:
            the signed-in user's own, or a companion the caller has already
            found to be the signed-in owner's.
        passkey_id: The ``auth.passkeys.id`` the request names.

    Raises:
        werkzeug.exceptions.NotFound: No passkey *passkey_id* belongs to
            *user*; nothing is written.
    """
    # Both read before the commit expires the rows, so the log reloads
    # nothing.
    user_id, actor_id = user.id, current_user.id
    try:
        passkey_service.remove_passkey(user_id, passkey_id)
    except NotFoundError:
        log_refused_lookup("Passkey", passkey_id)
        abort(404)
    record_security_event(user, SecurityEventKind.PASSKEY_REMOVED)
    db.session.commit()
    # ``user_id`` is who pressed Remove, as in every event of a signed-in
    # request, and ``passkey_user_id`` whose passkey it was.
    log_event(logger, logging.INFO, EVT_PASSKEY_REMOVED, AUTH,
              "Passkey removed", user_id=actor_id,
              passkey_id=passkey_id, passkey_user_id=user_id)
