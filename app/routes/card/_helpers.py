"""
Shekel Budget App -- Card route package: the card gate.

Which accounts this package's doors serve, in one leaf module every door
imports, so a door added later cannot be written without it -- the argument
:mod:`app.routes.accounts._cash_page` makes for the cash detail page's gate,
learned there when a fragment shipped without one.
"""

from flask import abort

from app.models.account import Account
from app.routes._redirect_target import RedirectTarget
from app.services.account_projection import is_revolving
from app.services.card_terms import is_configured_card
from app.utils.auth_helpers import get_or_404


def load_card_or_404(account_id: int) -> Account:
    """Load the current user's revolving account *account_id*, or 404.

    Two refusals, one answer.  ``get_or_404`` resolves a missing or
    cross-owner id to ``None`` (the project's "404 for not-found and
    not-yours" rule), and an account of any other kind is refused the same
    way: a card door reached with a checking id is a forged or stale request
    -- the terms form renders only for a card (**R-CC25**) -- and it is the
    cash detail page's own disposition for a kind it does not serve
    (:func:`~app.routes.accounts._cash_page.load_cash_account_or_404`).  The
    kind is decided by :func:`~app.services.account_projection.is_revolving`,
    the ONE predicate every card feature gates on (CC-1), never by a type
    name.

    Args:
        account_id: The URL's account id.

    Returns:
        The :class:`~app.models.account.Account`, owned by the current user
        and revolving.
    """
    account = get_or_404(Account, account_id)
    if account is None or not is_revolving(account):
        abort(404)
    return account


def load_configured_card_or_404(account_id: int) -> Account:
    """Load the current user's card *account_id*, which must have terms, or 404.

    :func:`load_card_or_404`'s three refusals plus a fourth: a card with no
    terms version is a DORMANT plain liability and every card feature gates
    on its terms (design 3.4; the terms door alone ends the dormancy).  A door
    behind this gate serves a form the page renders only once terms exist
    (developer ruling **R-CC28** for the APR), so a request reaching it for
    a dormant card is a forged or stale one and gets the same answer a wrong
    kind does.  The versions are not returned: no door behind this gate
    reads them yet, and one that does will load them through
    :func:`~app.services.card_terms.load_card_term_versions`.

    Args:
        account_id: The URL's account id.

    Returns:
        The :class:`~app.models.account.Account`, owned by the current user,
        revolving, with its terms stated.
    """
    account = load_card_or_404(account_id)
    if not is_configured_card(account):
        abort(404)
    return account


def back_to_card(account_id: int) -> RedirectTarget:
    """The cash detail page every card door returns to, as a redirect target.

    One spelling for the two terms doors and the two APR doors (developer
    rulings **R-CC25** and **R-CC28**: the page that hosts every card form
    until CC-11's cockpit).

    Args:
        account_id: The card's id.

    Returns:
        The :class:`~app.routes._redirect_target.RedirectTarget`.
    """
    return RedirectTarget("accounts.cash_detail", {"account_id": account_id})
