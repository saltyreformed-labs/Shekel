"""
Shekel Budget App -- a user's trusted devices, as two pages show and forget them.

Two doors list a user's trusted devices and forget one (plan#2173, ruling
plan#2406): each user's own Security page (``auth.security``,
``auth.forget_trusted_device``) and the owner's Settings > Companions, for
each companion (``settings.companion_forget_device``, ruling plan#3439).  The
row a device is shown as and the act of forgetting one are each stated once,
here, so both doors show the same device the same way and forget it with the
same refusal, write and record.  They lived in
:mod:`app.routes.auth.session_security` while it held the one door.

The rows and the forget read the request (the browser's device cookie, the
signed-in user), so they belong with the routes, not in
:mod:`app.services.known_device_service`, which takes plain ids.
"""

import logging
from datetime import datetime
from typing import TypedDict

from flask import abort
from flask_login import current_user

from app.exceptions import NotFoundError
from app.extensions import db
from app.services import known_device_service
from app.utils.auth_helpers import log_refused_lookup
from app.utils.browser_label import browser_label
from app.utils.device_cookie import device_token
from app.utils.log_events import AUTH, EVT_TRUSTED_DEVICE_FORGOTTEN, log_event

logger = logging.getLogger(__name__)


class TrustedDeviceRow(TypedDict):
    """One trusted device as a page shows it, its five keys the template reads.

    ``trusted_at`` and ``expires_at`` are aware UTC instants, never ``None``:
    the rows are live trusted devices, each trusted at an instant
    (:func:`~app.services.known_device_service.list_trusted_devices`).
    """

    id: int
    browser: str
    trusted_at: datetime
    expires_at: datetime
    is_this_browser: bool


def trusted_device_rows(user_id: int) -> list[TrustedDeviceRow]:
    """Return *user_id*'s live trusted devices as rows a template only displays.

    The ONE builder of a trusted-device row (plan#2173, ruling plan#2406):
    the browser's name read from its stored User-Agent, the two instants it
    is dated by (stored UTC; the template's ``local_datetime`` filter shows
    each as a display-timezone day), and whether it is the browser this page
    is rendered on, judged against the list already read rather than by a
    second lookup.  On the owner's Companions page that last says whether the
    owner's own browser is one the companion trusted: it holds her device
    cookie when she signed in on it.

    Args:
        user_id: The ``auth.users.id`` whose devices to list.

    Returns:
        One :class:`TrustedDeviceRow` per live device, most recently trusted
        first.
    """
    devices = known_device_service.list_trusted_devices(user_id)
    this_browser = known_device_service.device_named_by(
        devices, device_token(user_id),
    )
    return [
        TrustedDeviceRow(
            id=device.id,
            browser=browser_label(device.user_agent),
            trusted_at=device.trusted_at,
            expires_at=device.trust_expires_at,
            is_this_browser=device is this_browser,
        )
        for device in devices
    ]


def forget_device_or_404(user_id: int, device_id: int) -> None:
    """Forget *user_id*'s trust of device *device_id*, committed, or abort 404.

    The ONE act both forget doors run: the service forgets the trust (the
    browser stays known, :func:`~app.services.known_device_service.forget_trust`
    says why), the request commits it, and the log records it.  A device of
    another user and an id no device holds are one 404, the security response
    rule, and the refusal is logged the way every owner-scoped lookup's is
    (:func:`~app.utils.auth_helpers.log_refused_lookup`): the service looks the
    id up within *user_id*'s own devices, so it cannot tell the two apart, and
    neither can the response.

    Args:
        user_id: The ``auth.users.id`` whose device it must be: the signed-in
            user's own, or a companion the caller has already found to be the
            signed-in owner's.
        device_id: The ``auth.known_devices.id`` the request names.

    Raises:
        werkzeug.exceptions.NotFound: No device *device_id* belongs to
            *user_id*; nothing is written.
    """
    try:
        known_device_service.forget_trust(user_id, device_id)
    except NotFoundError:
        log_refused_lookup("KnownDevice", device_id)
        abort(404)
    db.session.commit()
    # ``user_id`` is who pressed Forget, as in every event of a signed-in
    # request, and ``device_user_id`` whose device it was: the same user on
    # the Security page, a companion of the owner's on Companions.
    log_event(logger, logging.INFO, EVT_TRUSTED_DEVICE_FORGOTTEN, AUTH,
              "Trusted device forgotten", user_id=current_user.id,
              device_id=device_id, device_user_id=user_id)
