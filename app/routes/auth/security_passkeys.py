"""
Shekel Budget App -- Auth route package: the Security page's passkeys.

Each user's own passkeys, an owner's and a companion's alike (plan#2538,
ruling plan#3472): the Security page lists them
(:func:`~app.routes.auth.session_security.security`), and here they are
added and removed.

**Adding asks Face ID twice** (ruling plan#3525): once to make the passkey,
once to sign in with it, and only a passkey that so signs is kept, so it is
listed only once the second succeeds.  The add has a page of its own,
``/security/passkeys/add``, and four requests, every one behind
``@fresh_login_required()``, the re-confirm Shekel asks before a sensitive
change:

* ``GET /security/passkeys/add`` -- the page: the passkey's name, suggested
  from the browser's system (``iPhone``), and its two steps, which
  ``passkey.js`` runs.
* ``POST /security/passkeys/add/options`` -- begins the add
  (:func:`~app.services.passkey_service.registration_options`), which writes
  the account's add in flight, so a command; its state is kept in the
  session and its options answered for ``navigator.credentials.create()``.
* ``POST /security/passkeys/add/registration`` -- the device's new passkey,
  and the name, checked (:func:`~app.services.passkey_service.complete_registration`);
  it keeps nothing and answers the options of a sign-in with it.
* ``POST /security/passkeys/add`` -- that sign-in, which keeps the passkey
  (:func:`~app.services.passkey_service.add_passkey`) and raises the "was
  this you?" banner (``passkey_added``) in the same transaction.

The three posts are ``fetch`` requests answered with JSON: the add's options
must be in hand before the person's tap, because Safari shows the passkey
sheet only when asked inside it, and so must the sign-in's, so the second
Face ID has a tap of its own.  A keeping that succeeds answers where the page
goes next, the Security page, with the passkey's flash.

**What a refusal answers** (:func:`_refused`): the service's sentence, with
422, and two things ``passkey.js`` does next.  ``retry``: the sign-in was by
another of the device's passkeys (``NOT_THE_NEW_PASSKEY`` says "try again and
choose the one you just made"), so the page asks again with the options it
holds.  After any other refusal of the registration or the keeping the page
begins a new add, which replaces the refused one on the server.
``forget``: the page then tells the device that the passkey it made is kept
nowhere (``PublicKeyCredential.signalUnknownCredential``), where the browser
can, so its pickers stop offering it.  Whether to is the database's answer
alone (:func:`~app.services.passkey_service.may_be_kept`): never while any
account keeps a passkey of the id the answer names (one the device answered
with although the options excluded it, or one a replayed keeping finds
already kept), nor for an answer naming no id that can be read.  The options'
refusal (``TOO_MANY``) ends nothing and asks nothing of the page but the
sentence.  Each refusal is committed: the service only flushes the delete of
an add it finds expired, and a rollback would leave the dead row (ruling
plan#3578 deletes the rest at anyone's next add).

The state kept between the requests names the ceremony and the add's row,
``{"ceremony": ..., "add": <id>}``, under one session key for both of the
add's completions.  The row is the add's one home and its once-only guard
(ruling plan#3543), so neither a refusal nor a keeping touches the state: a
kept add's names a row that is gone, which completes nothing, and the next
add the page begins replaces both the state and the row.

**Removing** asks no re-confirm, as forgetting a trusted device asks none
(:func:`~app.routes._passkeys.remove_passkey_or_404`).
"""

import logging

from flask import (
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session as flask_session,
    url_for,
)
from flask_login import current_user

from app.exceptions import ValidationError
from app.extensions import db
from app.models.passkey import NAME_MAX_LENGTH
from app.routes._passkeys import remove_passkey_or_404
from app.routes.auth._bp import auth_bp
from app.services import passkey_service
from app.services.passkey_service import PasskeyRefusal, PasskeyRefused
from app.utils.auth_helpers import fresh_login_required
from app.utils.browser_label import system_label
from app.utils.log_events import (
    AUTH,
    EVT_PASSKEY_ADDED,
    EVT_PASSKEY_ADD_REFUSED,
    log_event,
)
from app.utils.security_events import SecurityEventKind, record_security_event

logger = logging.getLogger(__name__)

#: The session key holding the add in flight: the state
#: :func:`~app.services.passkey_service.registration_options` returned, then
#: the one :func:`~app.services.passkey_service.complete_registration` did.
_ADD_STATE_KEY = "_passkey_add_state"

#: The add form's field carrying the device's answer, the new passkey's and
#: then its sign-in's, as ``passkey.js`` writes them.
_CREDENTIAL_FIELD = "credential"

#: The add form's field carrying the passkey's name.
_NAME_FIELD = "name"

#: The status of every refusal the add's requests answer.
_REFUSED = 422

#: The refusals after which the page asks the device again with the options
#: it holds: the add is still in flight, and the answer it wants is the new
#: passkey's.
_RETRIED = frozenset({PasskeyRefusal.NOT_THE_NEW_PASSKEY})


def _refused(
    message: str, *, refusal: PasskeyRefusal | None, answer: str | None,
) -> tuple[Response, int]:
    """Answer a refused step of the add, committing what the refusal deleted.

    Args:
        message: The sentence the person is told.
        refusal: Why the service refused, or ``None`` for a name it refused.
        answer: The device's answer the step posted, or ``None`` for the
            options, before the device has made anything.

    Returns:
        The JSON answer, with :data:`_REFUSED`: ``message``, ``retry`` (the
        page asks again with the options it holds) and ``forget`` (the page
        begins a new add, and no account keeps the passkey the answer names,
        so the page tells the device it is kept nowhere).
    """
    # An expired add's row is deleted by a flush the refusal followed.
    db.session.commit()
    retry = refusal in _RETRIED
    forget = (
        answer is not None and not retry
        and not passkey_service.may_be_kept(answer)
    )
    return jsonify(message=message, retry=retry, forget=forget), _REFUSED


def _log_refusal(step: str, refused: PasskeyRefused) -> None:
    """Record a refused step of the add.

    Args:
        step: ``options``, ``registration`` or ``keeping``.
        refused: The service's refusal.
    """
    log_event(
        logger, logging.WARNING, EVT_PASSKEY_ADD_REFUSED, AUTH,
        "Passkey add refused", step=step, refusal=refused.refusal.value,
        user_id=current_user.id, ip=request.remote_addr,
    )


@auth_bp.route("/security/passkeys/add", methods=["GET"])
@fresh_login_required()
def passkey_add_form() -> str:
    """Render the page that adds a passkey to the signed-in account.

    The name is suggested from the system the browser runs on
    (:func:`~app.utils.browser_label.system_label`), which the person may
    change; none is suggested where no system is recognised.

    Returns:
        The rendered page.
    """
    return render_template(
        "auth/passkey_add.html",
        suggested_name=system_label(request.headers.get("User-Agent", "")) or "",
        name_max_length=NAME_MAX_LENGTH,
    )


@auth_bp.route("/security/passkeys/add/options", methods=["POST"])
@fresh_login_required()
def passkey_add_options() -> Response | tuple[Response, int]:
    """Begin an add: keep its state in the session, answer its options.

    Returns:
        The options for ``navigator.credentials.create()``, as WebAuthn's
        JSON, or the refusal of an account holding the most passkeys it may
        (``TOO_MANY``, ruling plan#3472) in the service's words.
    """
    try:
        options, state = passkey_service.registration_options(
            current_user, origin=current_app.config["PASSKEY_ORIGIN"],
        )
    except PasskeyRefused as refused:
        _log_refusal("options", refused)
        return _refused(str(refused), refusal=refused.refusal, answer=None)
    db.session.commit()
    flask_session[_ADD_STATE_KEY] = state
    return jsonify(options)


@auth_bp.route("/security/passkeys/add/registration", methods=["POST"])
@fresh_login_required()
def passkey_add_registration() -> Response | tuple[Response, int]:
    """Check the passkey the device made, and its name; answer a sign-in by it.

    Returns:
        The options for ``navigator.credentials.get()``, as WebAuthn's JSON,
        or a refusal (:func:`_refused`).
    """
    answer = request.form.get(_CREDENTIAL_FIELD, "")
    try:
        options, state = passkey_service.complete_registration(
            current_user, answer, request.form.get(_NAME_FIELD, ""),
            origin=current_app.config["PASSKEY_ORIGIN"],
            state=flask_session.get(_ADD_STATE_KEY),
        )
    except ValidationError as invalid:
        return _refused(str(invalid), refusal=None, answer=answer)
    except PasskeyRefused as refused:
        _log_refusal("registration", refused)
        return _refused(str(refused), refusal=refused.refusal, answer=answer)
    db.session.commit()
    flask_session[_ADD_STATE_KEY] = state
    return jsonify(options)


@auth_bp.route("/security/passkeys/add", methods=["POST"])
@fresh_login_required()
def passkey_add() -> Response | tuple[Response, int]:
    """Keep the new passkey once it has signed in, raising the banner with it.

    Returns:
        Where the page goes next, the Security page, as JSON
        (``{"location": ...}``), the passkey's flash set; or a refusal
        (:func:`_refused`).
    """
    answer = request.form.get(_CREDENTIAL_FIELD, "")
    try:
        passkey = passkey_service.add_passkey(
            current_user, answer,
            origin=current_app.config["PASSKEY_ORIGIN"],
            state=flask_session.get(_ADD_STATE_KEY),
        )
    except PasskeyRefused as refused:
        _log_refusal("keeping", refused)
        return _refused(str(refused), refusal=refused.refusal, answer=answer)
    # Read before the commit expires the row.  The banner's moment is the
    # passkey's own, so the two say one instant.
    passkey_id, name = passkey.id, passkey.name
    record_security_event(
        current_user, SecurityEventKind.PASSKEY_ADDED, now=passkey.created_at,
    )
    db.session.commit()
    log_event(
        logger, logging.INFO, EVT_PASSKEY_ADDED, AUTH, "Passkey added",
        user_id=current_user.id, passkey_id=passkey_id,
    )
    flash(f'Passkey "{name}" added. It signs you in from now on.', "success")
    return jsonify(location=url_for("auth.security"))


@auth_bp.route("/security/passkeys/<int:passkey_id>/remove", methods=["POST"])
def remove_passkey(passkey_id: int) -> Response:
    """Remove one of the signed-in user's passkeys (plan#2538).

    It no longer signs in.  A passkey of another user and an id no passkey
    holds are one 404 (:func:`~app.routes._passkeys.remove_passkey_or_404`).
    No re-confirm and no owner gate: a passkey is its own user's, whatever
    their role.

    Returns:
        A redirect to the Security page.
    """
    remove_passkey_or_404(current_user, passkey_id)
    flash("Passkey removed. It no longer signs you in.", "success")
    return redirect(url_for("auth.security"))
