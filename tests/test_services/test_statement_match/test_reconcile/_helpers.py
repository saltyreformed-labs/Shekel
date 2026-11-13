"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_reconcile.py``, moved verbatim by
``tools/split``.
"""

from app.services.statement_match import reconcile_page
from app.services.statement_match._accepted_view import REGISTER_LIMIT

from tests.test_services.test_statement_match._builders import a_scope


#: What SECU files a card payment under, which ruling **R-GJ** reads: a
#: merchant a source files as paying an account the owner holds.
_CARD_PAYMENT = "Financial Services/Credit Card Payment"


def _page(seed_user, tab, agreement=None, limit=REGISTER_LIMIT):
    """Return the Reconcile page for one tab of the seeded account.

    Args:
        seed_user: The seeded user bundle.
        tab: Which :class:`~app.services.statement_match.Tab`.
        agreement: The bank agreement, or ``None`` -- which is the state of an
            account no import has anchored, and the one most cases are in.
        limit: How many SETTLED acts a settled tab may render, or ``None`` for
            the whole record.  **It defaults to the SHIPPED bound** rather than
            to ``None``, so a case that says nothing about it grades the page
            the route actually renders.

    Returns:
        The page.
    """
    return reconcile_page(a_scope(seed_user), agreement, tab, limit)


def _cards(page):
    """Return every card on a page, across its sections.

    Args:
        page: The page.

    Returns:
        The cards, in section order.
    """
    return [card for section in page.sections for card in section.cards]


def _counts(page):
    """Return the tab bar as a plain dict.

    Args:
        page: The page.

    Returns:
        A dict of :class:`~app.services.statement_match.Tab` to count.
    """
    return {count.tab: count.count for count in page.counts}
