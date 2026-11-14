"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_create.py``, moved verbatim by ``tools/split``.
"""

from app.extensions import db
from app.services import pay_calendar, statement_match, transaction_service
from app.services.statement_match import PurchaseCreation
from app.services.statement_match import _create

from tests.test_services.test_statement_match._builders import (
    a_purchase,
    a_scope,
    a_transaction,
    an_answers,
)


def _closed_from_purchases(seed_user, *, name="Groceries", amount="500.00",
                           purchase="120.00", period=None):
    """Return an envelope CLOSED at the sum of its own purchases.

    The state 29 of the developer's 37 closed envelopes are in, and the only
    settled state a new purchase may be added to: a ``purchases`` settlement
    stores no figure, so the row's cost IS its entries and a new one raises it
    by exactly its own amount.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.
        amount: Its budget.
        purchase: The one purchase it closes at.
        period: The pay period to file it under.

    Returns:
        The settled :class:`~app.models.transaction.Transaction`.
    """
    envelope = a_transaction(
        seed_user, name=name, amount=amount, is_envelope=True, period=period,
    )
    a_purchase(seed_user, envelope, amount=purchase, description="Walmart")
    transaction_service.settle_from_entries(envelope)
    db.session.flush()
    return envelope


def _offerable(seed_user):
    """Return the destinations the screen may offer RIGHT NOW.

    Two producers rather than one since plan step **X-f6a-3c-2**, and the split
    is the point:
    :func:`~app.services.statement_match.destinations_for` answers what the
    account COULD offer, which does not change while a review pass runs, and
    :func:`~app.services.statement_match.current_destinations` re-asks each
    row as it stands now, which is exactly what the pass changes.  A screen
    and a write door both narrow the first by the second -- which is what stops
    a shared, once-derived offer set handing a pass's fourth item an envelope
    its third has just settled.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The offerable :class:`~app.services.statement_match.PurchaseDestination`
        values.
    """
    return statement_match.current_destinations(
        statement_match.destinations_for(
            seed_user["account"].id,
            pay_calendar.calendar_for(seed_user["user"].id),
        ),
    )


def _record(seed_user, line, minted=None, applied_by_rule=False, **destination):
    """Record *line* as a purchase, into whichever destination is named.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded bank line to record.
        minted: What this REQUEST has already created.  ``None`` gives each
            call its OWN empty registry, which is the HAND path -- one line,
            one request -- and is what every case in this package means unless
            it says
            otherwise.  A case about a SWEEP passes one registry to several
            calls, because that is what one press is.
        applied_by_rule: Which consent this act had (ruling **R-GT**).
            **Defaulted here and NOT at the door**, which is the split the door
            itself argues for: the production signature is keyword-only with no
            default, so no writer can claim consent by omission, and a HELPER
            that spelled it at 40 call sites would say nothing at any of them.
            Every case in this package is the review screen's own destination
            select unless it says otherwise, which is ``False``.
        **destination: The ``PurchaseCreation`` destination fields.

    Returns:
        The :class:`~app.services.statement_match.CreatedPurchase`.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(
            line_id=line.id,
            **destination,
        ),
        # DERIVED HERE, so every call sees the rows this test has staged.
        a_scope(seed_user),
        minted if minted is not None else _create.MintedEnvelopes.none_yet(),
        # ...and the ANSWERS this test has staged, for the same reason: a
        # helper handing the door an empty ``CreationBars`` would make every
        # case in this package blind to ruling R-GJ's refusal.
        an_answers(seed_user),
        applied_by_rule=applied_by_rule,
    )
