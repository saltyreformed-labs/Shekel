"""An owner's edits through the app's doors: a row or transfer re-priced, a row moved or deleted,
and a definition firing twice in one paycheck.
"""

from datetime import date as _real_date
from decimal import Decimal

from app.models.amount_ownership import AmountOwnership

from ._rows import generate_row_of
from ._prices_and_rules import bare_expense_template, make_cadence_rule
from ._templates import make_expense_template
from ._calendar import rebuild_calendar


def repriced_by_the_owner(row, figure):
    """Re-price *row* the way the edit door does, and hand it back.

    **The OWNER's row, stated once** (plan step balance:X-cf-3b, ruling
    R-BAL17): a fixture wanting a row a human re-priced takes the engine's
    row (:func:`generate_row_of`) and performs the two acts
    ``routes/transactions/mutations`` performs on a typed figure -- the
    figure through ``amount_ownership.state_own_amount``, which stores it and
    releases the relation that priced the row in one attribute, and
    ``is_override = True`` beside it, because the row is the owner's now
    rather than the rule's.  The pair was spelled at every site that wanted
    it before this helper, which is rule 14's tell; a site that performs one
    act without the other builds a row the write doors cannot produce.

    Args:
        row: The flushed :class:`~app.models.transaction.Transaction`, the
            engine's.
        figure: The figure the owner typed (str or Decimal-coercible).

    Returns:
        *row*, flushed, owning *figure*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.services.amount_ownership import state_own_amount

    state_own_amount(row, Decimal(str(figure)))
    row.is_override = True
    db.session.flush()
    return row


def transfer_repriced_by_the_owner(xfer, figure):
    """Re-price the transfer *xfer* the way the transfer edit door does.

    :func:`repriced_by_the_owner` one table over (plan step balance:X-ch,
    ruling R-BAL17 on the twin), through the twin table's own door: every
    write of a transfer goes through ``transfer_service``, so the two acts
    ``routes/transfers/mutations`` performs on a typed figure for a recurring
    transfer go through ``transfer_service.update_transfer``: the figure as
    OWNERSHIP, and ``is_override=True`` beside it because the transfer is
    the owner's now rather than the rule's.  The PLANNED figure lives on the
    transfer alone, and both legs are drawn off it: it lived on THREE rows
    -- the parent and its two shadow legs, which that door moved together
    -- until plan step ``balance:X-bi-6-4d-2`` made a transfer one row.

    Args:
        xfer: The flushed :class:`~app.models.transfer.Transfer`, the
            engine's (:func:`generate_transfer_of`).
        figure: The figure the owner typed (str or Decimal-coercible).

    Returns:
        *xfer*, flushed, owning *figure*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import transfer_service

    transfer_service.update_transfer(
        xfer.id, xfer.user_id,
        amount_ownership=AmountOwnership.own(Decimal(str(figure))),
        is_override=True,
    )
    return xfer


def moved_by_the_owner(row, *, into):
    """Move *row* into the paycheck *into* the way the move door does.

    The other of the edit door's two acts on a row of a definition (plan
    step balance:X-cf-3b): the period, and ``is_override = True`` beside it
    -- a moved row is the owner's, and the flag is what keeps the maintain
    pass and the generate pass off it.  The row keeps the occurrence it was
    generated for, so it never collides with the target paycheck's own
    canonical on the occurrence index; it is one of the two ways the
    application puts an override beside a canonical, the other being
    carry-forward.  A fixture wanting "an override sibling in paycheck N"
    generates the definition's row in ANOTHER paycheck and moves it here.

    Args:
        row: The flushed :class:`~app.models.transaction.Transaction`, the
            engine's.
        into: The :class:`~app.models.pay_period.PayPeriod` the owner moved
            it into.

    Returns:
        *row*, flushed, in *into*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db

    row.pay_period_id = into.id
    row.is_override = True
    db.session.flush()
    return row


def a_deleted_occurrence(db_session, owner, period, **definition):
    """*owner*'s every-period expense, its row in *period* DELETED the way the delete door does.

    A row the owner deleted, built and deleted as the application does both:
    :func:`make_expense_template` given *definition* (only the keywords a
    caller passes, so every other default stays that builder's own), its row
    from :func:`generate_row_of`, committed, then
    ``transaction_service.delete_transaction`` and a second commit.  A
    recurring row's delete HIDES it rather than removing it
    (``transaction_service._delete._leaves_the_books``: ``soft = txn.recurs``,
    ``app/services/transaction_service/_delete.py:237``; the soft arm sets
    ``is_deleted`` at ``:446-447``), so the row stays in the table, deleted and
    carrying its name: the row a transaction door names only to a requester
    who may reach it (ruling **R-CC104**).  Added at plan step
    ``balance:X-bi-6-4d-2`` for the stranger's and the companion's probes of
    such a row, and the two route cases that built it by hand.

    Args:
        db_session: The test's SQLAlchemy session; committed twice.
        owner: The owning user's fixture dict (``seed_user``'s shape).
        period: The owner's :class:`~app.models.pay_period.PayPeriod` the row
            is generated in.
        **definition: Keywords for :func:`make_expense_template` (``name``,
            ``amount``, ``category_key``, ``companion_visible``, ...).

    Returns:
        The deleted :class:`~app.models.transaction.Transaction`, committed.
    """
    # Pylint: ``import-outside-toplevel`` -- the lazy-app-import convention, as
    # :func:`settlement_if_settling` imports the transaction service.
    # pylint: disable=import-outside-toplevel
    from app.services import transaction_service

    template = make_expense_template(db_session, owner, **definition)
    row = generate_row_of(template, period)
    db_session.commit()
    transaction_service.delete_transaction(row, owner["user"].id)
    db_session.commit()
    return row


def definition_firing_twice_in_a_paycheck(db_session, seed_user, *, name):
    """Pin a 60-day calendar and author a MONTHLY definition that names ONE
    occurrence in its first paycheck and TWO in its second; return
    ``(template, first paycheck, second paycheck)``.

    The one fixture for "a paycheck holding two rows of one definition" -- the
    state plan step R17 made storable -- so the cases that need it (the
    builder's 2+ refusal in ``test_fixture_validation``, the R17 downgrade
    guard, DC-06's acceptance of the pair, carry-forward's earliest-occurrence
    tie-break) state it once and the calendar arithmetic is argued once.
    :func:`generate_row_of` REFUSES this definition in the second paycheck by
    design; the cases that want the pair written take it from
    :func:`populate_in_a_fresh_pass`.  In the FIRST paycheck it answers the
    definition's one row there, which is what a case rolling a leftover INTO
    the pair needs as its source (plan step balance:X-cf-3b).

    **The calendar is pinned to a stated date rather than derived from today,
    and the reason was measured, not argued.**  A 60-day paycheck that opens
    in January or February of a non-leap year spans THREE firsts of the month
    (Jan+Feb and Feb+Mar are 59 days each), and ``seed_schedule_at_cadence(60)``
    opens its second paycheck 180-186 days before today -- so the same cases
    read three rows for about two months of every non-leap year, from late
    June into early September (2026-06-29..08-30, 2027-07-05..08-29,
    2029-07-02..09-02, 2030-07-01..09-01; none in 2028).  Found by
    adversarial review 2026-09-11 under ``SHEKEL_FAKE_TODAY=2026-07-15``,
    which is the way to see it.  The first paycheck here opens 2026-03-02 and
    closes 2026-04-30, the second opens 2026-05-01 and closes 2026-06-29:
    the rule's first occurrence is 2026-04-01, so the first paycheck holds
    that one and no other (the 1st of March precedes the rule); May and June
    are 61 days, so the second holds the 1st of May and the 1st of June and no
    third, on every clock.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict; its calendar is REBUILT.
        name: The definition's name.

    Returns:
        ``(template, first, second)``: the flushed
        :class:`~app.models.transaction_template.TransactionTemplate` carrying
        the monthly rule, the :class:`~app.models.pay_period.PayPeriod` it
        fires once in, and the one it fires twice in.
    """
    # Pylint: ``import-outside-toplevel`` -- a tests-package import kept
    # local so this module's import graph stays as it was.
    # pylint: disable=import-outside-toplevel
    from tests.oracles.recurrence_baseline import MONTHLY

    periods = rebuild_calendar(
        seed_user["user"].id, _real_date(2026, 3, 2), 6, 60,
    )
    template = bare_expense_template(db_session, seed_user, name=name)
    make_cadence_rule(template, MONTHLY, starts_on=_real_date(2026, 4, 1))
    return template, periods[0], periods[1]
