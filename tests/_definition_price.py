"""A definition's stated price, as a test reads or posts it now that the series is its only home.

Plan step ``balance:X-bp`` (card plan#1045, rulings R-IY and R-BAL16) dropped
``default_amount`` from both template tables: the copy of the newest price a
definition's series states, which the write door kept in step.  An assertion
that read the copy reads the series here instead, with its figure kept
(:func:`newest_price`), and a payload standing in for an edit form posts the
Amount box as the form renders it (:func:`amount_box_payload`).

Its own module rather than ``tests/_test_helpers.py``: that module is over the
1,000-line ceiling, so nothing new goes into it (ruling R-IR).
"""
from decimal import Decimal


def newest_price(template):
    """Return the NEWEST price *template*'s series states, or ``None`` for none.

    What a template's ``default_amount`` column held -- the write door kept it
    on the series' newest version -- until plan step X-bp dropped it (rulings
    R-IY, R-BAL16), so an assertion of the column is re-pointed here with its
    figure kept.  ``None`` for a definition with no series: a salary profile's
    or a loan payment that follows its loan, which state no price.

    Args:
        template: A transaction or transfer template.

    Returns:
        The newest version's amount as a ``Decimal``, or ``None``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (the test helpers' collection-time-safety
    # convention).
    # pylint: disable=import-outside-toplevel
    from app.services import template_amount_service

    versions = template_amount_service.amount_versions(template)
    return Decimal(str(versions[-1].amount)) if versions else None


def amount_box_payload(template):
    """The Amount key a definition's edit form posts, as the browser would.

    Since plan step X-bp (ruling plan#2694) a definition whose amount is
    WORKED OUT -- a salary profile's, a loan payment that follows its loan --
    shows a line in place of the Amount box, and its update door refuses a
    typed amount, so a payload standing in for the browser posts the box only
    where the form renders one.  Where it does, the box is prefilled with
    what the price history states TODAY
    (``routes/_amount_version_actions.edit_form_amount``), which is what an
    unchanged save posts.

    Args:
        template: The transaction or transfer template whose form is posted.

    Returns:
        ``{"amount": "<today's price>"}``, or ``{}`` for a worked-out definition.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import template_amount_service
    from app.utils.dates import display_today

    if not template_amount_service.owns_its_amount(template):
        return {}
    return {
        "amount": str(template_amount_service.amount_as_of(
            template, display_today(),
        )),
    }
