"""
Shekel Budget App -- Auth route package: signing in with a passkey.

A passkey signs in on its own, with no password and no code (plan#2538,
ruling plan#3465), in two requests, both reached before sign-in and so both
named in :data:`~app.login_gate.PUBLIC_ENDPOINTS`:

* ``POST /login/passkey/options`` begins the ceremony
  (:func:`~app.services.passkey_service.sign_in_options`), keeps its state in
  the session and answers the options as JSON, for ``passkey.js`` to hand the
  device.  A POST because it writes the session, so the form token guards it.
* ``POST /login/passkey`` completes it.  A form post, not a fetch, so the
  redirect, the flash and the form token work as the password form's do.  The
  account is the one the passkey names
  (:func:`~app.services.passkey_service.sign_in_account`), its owner's lock is
  taken (:func:`~app.db_transaction.bind_sign_in_owner`), the answer is
  checked under it (:func:`~app.services.passkey_service.complete_sign_in`),
  and the sign-in finishes through
  :func:`~app.routes.auth._helpers._complete_sign_in`, the one completion,
  with nothing of the strangers' record to clear and no trust: the browser
  becomes known, and the next password sign-in on it still asks the code.

**The strangers' lock is not asked, and a refused passkey counts nowhere**
(ruling plan#3469): a passkey cannot be guessed, so an account the lock holds
still signs in by one, and a refusal writes no count.  Tries are limited per
internet address as the password form's are.  Every refusal tells the person
one plain sentence (:class:`~app.services.passkey_service.PasskeyRefused`)
that names nothing about the account: with no live ceremony every answer is
refused before any account is looked up, and with one, a passkey no account
holds and one that does not verify read alike.  What the sentence cannot
hide is time: a passkey that exists takes its owner's lock and has its
signature checked, which tells someone who already holds its id that it is
still registered, and nothing else.

The landing page is the password form's.  ``/login``'s form posts without
the query, so no sign-in honours a ``next`` today, and this one passes none:
plan#2174 records that, and that ``_is_safe_redirect`` lets a ``next`` of
``/\\evil.com`` leave the site, so the step that revives ``/login``'s
``next`` revives this one's with it.
"""

import logging

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user

from app.db_transaction import bind_sign_in_owner
from app.rate_limits import limiter
from app.routes.auth._bp import auth_bp
from app.routes.auth._helpers import (
    _SIGN_IN_TRIES_PER_ADDRESS,
    _complete_sign_in,
    _landing_page,
)
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from app.utils.log_events import (
    AUTH,
    EVT_PASSKEY_SIGN_IN,
    EVT_PASSKEY_SIGN_IN_FAILED,
    log_event,
)

logger = logging.getLogger(__name__)

#: The session key holding the sign-in ceremony in flight: the state
#: :func:`~app.services.passkey_service.sign_in_options` returned.  One per
#: session, so newer options replace older ones, and the sign-in takes it out
#: before checking the answer, so a ceremony is completed once.
_SIGN_IN_STATE_KEY = "_passkey_sign_in_state"

#: The sign-in form's field carrying the device's answer: the JSON of
#: ``navigator.credentials.get()``'s result, as ``passkey.js`` writes it.
_CREDENTIAL_FIELD = "credential"


@auth_bp.route("/login/passkey/options", methods=["POST"])
def passkey_sign_in_options():
    """Begin a passkey sign-in: keep its state in the session, answer its options.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON.
        They name no account, so the device offers whichever passkey it holds
        for this site.
    """
    options, state = passkey_service.sign_in_options(
        origin=current_app.config["PASSKEY_ORIGIN"],
    )
    flask_session[_SIGN_IN_STATE_KEY] = state
    return jsonify(options)


@auth_bp.route("/login/passkey", methods=["POST"])
@limiter.limit(_SIGN_IN_TRIES_PER_ADDRESS)
def passkey_sign_in():
    """Sign in the account whose passkey answered, or say why not.

    The account is found before its owner's lock, by the passkey's id alone
    (``app.db_transaction`` lists that read among those before the lock), and
    everything the answer is checked against is read under it: the passkey
    ``FOR UPDATE``, and the account again, so a passkey removed or an account
    disabled meanwhile refuses.

    Returns:
        A redirect: where the account lands, carrying the device cookie, or
        back to the sign-in page with the refusal's sentence flashed.  A
        browser signed in already goes where ``/login`` sends it.
    """
    # Out of the session whatever happens next, so a second submit of the
    # same answer meets no ceremony in flight.
    state = flask_session.pop(_SIGN_IN_STATE_KEY, None)
    if current_user.is_authenticated:
        return redirect(_landing_page(current_user, None))
    response_json = request.form.get(_CREDENTIAL_FIELD, "")
    user_id = None
    try:
        # The ceremony first, then the account: with none in flight, nothing
        # about any passkey is read or told.
        user = passkey_service.sign_in_account(response_json, state=state)
        if user is None:
            raise PasskeyRefused(PasskeyRefusal.UNKNOWN_CREDENTIAL)
        # Read before the lock expires the row, so a refusal's log reloads
        # nothing.
        user_id = user.id
        bind_sign_in_owner(user_id, user.data_owner_id)
        passkey = passkey_service.complete_sign_in(
            user, response_json,
            origin=current_app.config["PASSKEY_ORIGIN"], state=state,
        )
    except PasskeyRefused as refused:
        log_event(
            logger, logging.WARNING, EVT_PASSKEY_SIGN_IN_FAILED, AUTH,
            "Passkey sign-in refused",
            refusal=refused.refusal.value, user_id=user_id,
            ip=request.remote_addr,
        )
        flash(str(refused), "danger")
        return redirect(url_for("auth.login"))
    passkey_id = passkey.id
    # The strangers' record is neither asked nor cleared (ruling plan#3469),
    # and a passkey asks no code, so it is never a trust.  No ``next``: the
    # module docstring says why (plan#2174).
    response = _complete_sign_in(user, None, checked=None)
    log_event(
        logger, logging.INFO, EVT_PASSKEY_SIGN_IN, AUTH,
        "User signed in with a passkey",
        user_id=user_id, passkey_id=passkey_id,
    )
    return response
