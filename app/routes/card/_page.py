"""
Shekel Budget App -- Card route package: what the card's page shows of it.

The context of the two cards a revolving account shows below its balance band
on the cash detail page (developer ruling **R-CC25**: the page that hosts
every card form until CC-11's cockpit): the Card terms card with the APR
(plan steps credit_card:CC-2 and CC-3) and the Payment card (task plan#2679).
The cash detail route composes it (:func:`card_page_context`), and the
partials render it: ``accounts/_card_terms.html`` and
``accounts/_card_payment.html``.

It moved here from ``routes/accounts/detail.py`` with the payment's half,
because both halves are the card's and that module was at the 1,000-line
ceiling.
"""

from dataclasses import dataclass

from app.models.account import Account
from app.services import card_apr, card_payoff
from app.services.balance_at import BalanceContext
from app.services.card_statement import terms_in_effect
from app.services.card_terms import load_card_term_versions


@dataclass(frozen=True)
class PaymentOnPage:
    """The card's payment, as its Payment card shows it.

    Attributes:
        name: The payment's name, as the Recurring page lists it.
        payer: The name of the account it comes out of.
        choice: The :data:`~app.services.card_payoff.PAYMENT_CHOICES` token
            of the payment it is, which the form starts from; ``None`` for a
            repeat the card's page did not set (no door makes one).
    """

    name: str
    payer: str
    choice: str | None


def _payment_on_page(account: Account) -> PaymentOnPage | None:
    """Return *account*'s payment for its Payment card, or ``None``.

    Args:
        account: A card.

    Returns:
        The :class:`PaymentOnPage`, or ``None`` for a card with no payment,
        which no door makes (task plan#2933) and whose card says so.
    """
    settings = card_payoff.card_payment_on(account.id)
    if settings is None:
        return None
    template = settings.template
    return PaymentOnPage(
        name=template.name,
        payer=template.from_account.name,
        choice=card_payoff.current_choice(settings),
    )


def card_page_context(
    account: Account, ctx: BalanceContext, is_card: bool,
) -> dict:
    """The card's two cards' context: the terms and the APR, and the payment.

    Three states under one key shape.  A non-card carries the keys empty
    (the partials are not included; the template's context has one shape).  A
    dormant card carries its ``None`` terms and its payment: the terms
    partial renders the terms form blank and no APR section, so the APR
    series is not read -- whatever rows the account holds are unread while it
    is dormant, and the doors are gated the same way (plan step
    credit_card:CC-3, developer ruling **R-CC28**); and the payment partial
    offers payoff rows alone, a scheduled payment's dates being the terms'.
    A configured card carries the terms and APR in force on the pass's own
    day (``ctx.as_of``), both dated series newest first (plan#2331, ruling
    **plan#2333**), and its payment.

    A card with no terms is the opposite disposition to the interest-bearing
    account's params, and deliberately so (plan step credit_card:CC-2, design
    3.4): an interest-bearing account's params row is written by every door
    that makes it interest-bearing, so a missing one is corrupt data and the
    page refuses; a card's terms are written by the OWNER alone (the terms
    door), so none is the ordinary state of a card not yet configured.

    Args:
        account: The account being rendered.
        ctx: The page's read pass.
        is_card: The ONE card predicate's answer for *account*, decided once
            by the caller and also handed to the template.

    Returns:
        ``card_terms``, ``card_term_versions``, ``card_aprs``,
        ``card_apr_today`` and ``card_payment``.
    """
    if not is_card:
        return {"card_terms": None, "card_term_versions": [],
                "card_aprs": [], "card_apr_today": None, "card_payment": None}
    payment = _payment_on_page(account)
    versions = load_card_term_versions(account.id)
    if not versions:
        return {"card_terms": None, "card_term_versions": [],
                "card_aprs": [], "card_apr_today": None,
                "card_payment": payment}
    aprs = card_apr.load_card_aprs(account.id)
    return {
        "card_terms": terms_in_effect(versions, ctx.as_of),
        "card_term_versions": versions[::-1],
        "card_aprs": aprs,
        "card_apr_today": card_apr.apr_in_effect(aprs, ctx.as_of),
        "card_payment": payment,
    }
