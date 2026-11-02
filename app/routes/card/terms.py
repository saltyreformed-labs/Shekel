"""
Shekel Budget App -- Card route package: the card's terms.

ONE save door for the card's terms (plan step **credit_card:CC-2**, developer
ruling **R-CC24**, 2026-09-18): the form that posts here renders every control
every time, so a submit IS a whole version of the terms.  Since plan#2331
(developer ruling **plan#2333**) the terms are a DATED SERIES, so the door
saves the version FOR the date the form names -- creating it, or correcting
the version already on that date (**R-CC27**'s set-by-date shape, the APR's)
-- and a change governs from its day while every statement and reward before
it stands.  A redirect-style POST handler that flashes and returns to the
cash detail page hosting the form (**R-CC25**).
"""

import logging

from flask import Response, flash

from app.extensions import db
from app.routes._form_errors import load_form_or_redirect
from app.routes.card._bp import card_bp
from app.routes.card._helpers import back_to_card, load_card_or_404
from app.schemas.validation import CreditCardTermsSchema
from app.services.card_terms import set_terms
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
    set_terms(account.id, account.user_id, effective_date, data)
    db.session.commit()
    logger.info(
        "Saved card terms for account %d effective %s",
        account.id, effective_date.isoformat(),
    )
    flash("Card terms saved.", "success")
    return back.to_response()
