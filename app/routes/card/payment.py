"""
Shekel Budget App -- Card route package: the card's payment.

ONE door switching the card's one payment between payoff rows and a payment
the owner scheduled with the card company (plan step **credit_card:CC-6**,
task plan#2679; rulings **plan#2888** and **plan#2889**).  Every card is made
with its payoff rows, everything on the card each payday
(:mod:`app.services.card_payoff`); the owner opts into a scheduled payment
only when they really set one up with the card company, and it takes the
payoff rows' place: the statement balance, the minimum, or everything on the
card, on each statement's due date.  The same door switches back.

The write is :func:`app.services.card_payoff.switch_payment`: the settings'
amount changed, and the repeat rewritten when its kind changes.  The rows then
follow through the maintain pass, asking first about any the owner typed over
on a date the switch takes away
(:func:`app.routes.card._payments_follow.follow_the_switch_or_ask`, the
question a change of terms asks).  A question rolls the write back, and its
Apply posts this door again.

A redirect-style POST handler, flashing and returning to the cash detail page
that hosts the form (developer ruling **R-CC25**), as the terms doors do.
"""

import logging

from flask import Response, abort, flash, url_for

from app.extensions import db
from app.routes._form_errors import load_form_or_redirect
from app.routes.card._bp import card_bp
from app.routes.card._helpers import back_to_card, load_card_or_404
from app.routes.card._payments_follow import (
    PaymentsDoor,
    follow_the_switch_or_ask,
)
from app.schemas.validation import CardPaymentSchema
from app.services import card_payoff
from app.services.balance_at import BalanceContext
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)

_payment_schema = CardPaymentSchema()


@card_bp.route("/accounts/<int:account_id>/card/payment", methods=["POST"])
@require_owner
def save_payment(account_id):
    """Switch the card's payment to the one the form names.

    Gated by :func:`~app.routes.card._helpers.load_card_or_404` (404 for
    not-found, not-yours and not-a-card alike).  A card with no payment is
    404 as well: every door that brings a card into being makes its payment
    (task plan#2933), so a card without one is no door's state, and there is
    nothing here to switch.

    The refusals are sentences, ahead of any write
    (:func:`~app.services.card_payoff.switch_refusal`): an archived card,
    whose payment is archived with it, and a scheduled payment on a card with
    no terms, which has no due dates to fall on.  A save naming the payment
    the card already has changes nothing and says so.

    The read pass is built before the write, and the switch's pass runs on it:
    the repeat is authored against its calendar and day, and a rule's
    resolution is memoized by what the rule says, so the pass walks the
    rewritten repeat (``card_payoff.switch_payment``'s docstring).
    """
    account = load_card_or_404(account_id)
    back = back_to_card(account.id)
    data = load_form_or_redirect(_payment_schema, back)
    if isinstance(data, Response):
        return data
    settings = card_payoff.card_payment_on(account.id)
    if settings is None:
        abort(404)
    token = data["payment"]
    if token == card_payoff.current_choice(settings):
        flash(f"{account.name}'s payment is already that one.", "info")
        return back.to_response()
    shape = card_payoff.PAYMENT_CHOICES[token]
    refusal = card_payoff.switch_refusal(account, shape)
    if refusal is not None:
        flash(refusal, "danger")
        return back.to_response()

    ctx = BalanceContext.build(account.user_id)
    change = card_payoff.switch_payment(settings, shape, ctx)
    if change is not None:
        followed = follow_the_switch_or_ask(settings.template, change, PaymentsDoor(
            f"You changed how {account.name} is paid.",
            url_for("card.save_payment", account_id=account.id),
            back,
        ), ctx)
        if followed is not None:
            return followed
    db.session.commit()
    logger.info(
        "Switched the payment of card account %d to %s", account.id, token,
    )
    flash(f"{account.name}'s payment is saved.", "success")
    return back.to_response()
