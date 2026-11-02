"""
Shekel Budget App -- Card route package: the card's terms.

ONE save door for the card's terms (plan step **credit_card:CC-2**, developer
ruling **R-CC24**, 2026-09-18): the form that posts here renders every control
every time, so a submit IS a whole version of the terms.  Since plan#2331
(developer ruling **plan#2333**) the terms are a DATED SERIES, so the door
saves the version FOR the date the form names -- creating it, or correcting
the version already on that date (**R-CC27**'s set-by-date shape, the APR's)
-- and a change governs from its day: every statement that closes before it,
and every reward on a day before it, stands (:mod:`app.services.card_statement`
states where the boundary falls).  A second door REMOVES a version (developer
ruling **plan#2587**), the fix a mistyped date needs, and refuses the card's
only one.  Both are redirect-style POST handlers that flash and return to the
cash detail page hosting the form (**R-CC25**).  A third, a GET, fills that
form from one version, its date included, so a correction states that
version's own terms rather than the ones in force today (plan#2331's review,
plan#2576).

The writes are :mod:`app.services.card_terms`'s; the transaction is this
module's.
"""

import logging

from flask import Response, abort, flash, render_template, request

from app.exceptions import CardTermsRemovalRefused
from app.extensions import db
from app.routes._form_errors import load_form_or_redirect
from app.routes.card._bp import card_bp
from app.routes.card._helpers import back_to_card, load_card_or_404
from app.schemas.validation import CreditCardTermsSchema
from app.services import card_terms
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)

_terms_schema = CreditCardTermsSchema()


@card_bp.route("/accounts/<int:account_id>/card/terms", methods=["POST"])
@require_owner
def save_terms(account_id):
    """Record the card's terms from a date: a new version, or a correction.

    The ownership and kind gate is :func:`~app.routes.card._helpers.load_card_or_404`
    (404 for not-found, not-yours and not-a-card alike).  The payload is
    loaded ONCE through :class:`~app.schemas.validation.CreditCardTermsSchema`
    (``load_form_or_redirect``: a refusal flashes and returns to the page, and
    a refusal raised at any stage of the load is heard); its ``@pre_load`` has
    already divided the two percent fields by 100, so the loaded values are
    the storage-domain fractions the model's CHECKs pin -- the route stores
    them verbatim, with no second divide, through
    :func:`~app.services.card_terms.set_terms`'s one statement.

    ``user_id`` is written from the ACCOUNT's owner, not the session's: the
    gate has already proved the two equal, and ``fk_credit_card_params_owner``
    would refuse the row otherwise, so the column is the account's fact
    restated where the database can hold it true.

    Before its first version the card is a dormant plain liability (design
    3.4); this door is the one act that ends that dormancy, and nothing in the
    application writes a version on the owner's behalf.
    """
    account = load_card_or_404(account_id)
    back = back_to_card(account.id)

    data = load_form_or_redirect(_terms_schema, back)
    if isinstance(data, Response):
        return data

    effective_date = data.pop("effective_date")
    card_terms.set_terms(account.id, account.user_id, effective_date, data)
    db.session.commit()
    logger.info(
        "Saved card terms for account %d effective %s",
        account.id, effective_date.isoformat(),
    )
    flash("Card terms saved.", "success")
    return back.to_response()


@card_bp.route("/accounts/<int:account_id>/card/terms/<int:row_id>/edit")
@require_owner
def edit_terms(account_id, row_id):
    """HTMX partial: the terms form filled from one version, to correct it.

    The page fills its form from the version in force TODAY, and a save on a
    date already listed rewrites every term of that version (ruling
    **plan#2587**: "Saving the same date again corrects an entry's terms").
    So an older version corrected from the page would take today's terms
    for every control the owner did not retype.  Each version's Edit button
    swaps this fragment in instead: the same form
    (``accounts/_card_terms_form.html``), filled with THAT version's seven
    terms and its date, so a save on that date corrects only what the owner
    changed, and a save on another date adds a version starting from them.

    Gated by :func:`~app.routes.card._helpers.load_card_or_404` (404 for
    not-found, not-yours and not-a-card alike); the version is found among
    the card's own (:func:`~app.services.card_terms.version_with_id`), so
    another account's row, and every row of a dormant card, is 404 too.  A
    request without HTMX is sent to the page, as the cash detail band's
    fragment does: this is a piece of that page, not a page.
    """
    if not request.headers.get("HX-Request"):
        return back_to_card(account_id).to_response()
    account = load_card_or_404(account_id)
    version = card_terms.version_with_id(
        card_terms.load_card_term_versions(account.id), row_id,
    )
    if version is None:
        abort(404)
    return render_template(
        "accounts/_card_terms_form.html",
        account=account, form_terms=version, editing=True,
    )


@card_bp.route(
    "/accounts/<int:account_id>/card/terms/<int:row_id>/delete",
    methods=["POST"],
)
@require_owner
def remove_terms(account_id, row_id):
    """Remove one of the card's terms versions, unless it is the only one.

    Gated by :func:`~app.routes.card._helpers.load_card_or_404` (404 for
    not-found, not-yours and not-a-card alike) and not by the
    configured-card gate: :func:`~app.services.card_terms.remove_terms` reads
    the card's versions once, and a dormant card holds none, so its every
    row answers 404 there -- the configured gate's answer, without its
    second read of the same versions in one request.  A row of another
    account answers 404 the same way.

    The card's only version is refused in plain words (developer ruling
    **plan#2587**), not with a 404: the row is the owner's and real, and the
    request was drawn when the card held two (a stale tab, or the second of
    two removes, which waits on the owner's write lock and counts after the
    first commits).  Nothing was written, and the rollback says so.
    """
    account = load_card_or_404(account_id)
    back = back_to_card(account.id)
    try:
        removed = card_terms.remove_terms(account.id, row_id)
    except CardTermsRemovalRefused as refused:
        db.session.rollback()
        flash(str(refused), "danger")
        return back.to_response()
    if not removed:
        abort(404)
    db.session.commit()
    logger.info("Removed card terms row %d from account %d", row_id, account.id)
    flash("Card terms removed.", "success")
    return back.to_response()
