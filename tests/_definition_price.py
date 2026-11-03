"""A definition's stated price, as a test reads it now that the series is its only home.

Plan step ``balance:X-bp`` (card plan#1045, rulings R-IY and R-BAL16) dropped
``default_amount`` from both template tables: the copy of the newest price a
definition's series states, which the write door kept in step.  An assertion
that read the copy reads the series here instead, with its figure kept.

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
