"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_transfers.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry
from app.models.settled_figure import SettledFigure
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.ref import AccountType, Status
from app.services import posting_service, transfer_service
from app.services.auth_service import hash_password
from app.services import account_service
from app.utils.dates import display_today
from tests._test_helpers import (
    generate_transfer_of,
    make_every_period_rule,
    make_transfer_template,
    open_books_before_the_first_assertion,
    record_paydays_across_a_hole,
    rhythm_of,
    state_template_price,
    transfer_repriced_by_the_owner,
)
from app.models.amount_ownership import AmountOwnership


def _create_savings_account(seed_user):
    """Helper: create a second (savings) account for the test user."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    # Its BOOKS open the day before its origination assertion (plan step
    # X-f3c-2b, ruling **R-HG**).  The factory defaults ``observed_on`` to the
    # owner's today and this suite settles transfers on days around it, so
    # leaving the books on today makes every such fixture unrecordable -- an
    # opening equity is the CLOSING balance for its own day.
    open_books_before_the_first_assertion(db.session, acct)
    db.session.commit()
    return acct


def _ledger_of(seed_user, checking_id, savings_id, scenario_id):
    """``(Checking's posted total, Savings', the owner's journal entry count)``, re-read."""
    db.session.expire_all()
    return (
        posting_service.account_posting_total(checking_id, scenario_id),
        posting_service.account_posting_total(savings_id, scenario_id),
        db.session.query(JournalEntry).filter(
            JournalEntry.user_id == seed_user["user"].id,
        ).count(),
    )


def _create_template(seed_user, savings_acct, with_rule=True):
    """Helper: create a transfer template with optional recurrence rule.

    The definition STATES its price, as every app-side create door does: a
    transfer the engine writes from it is derived and priced by this series
    on its own due date (plan step balance:X-ch).
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings_acct.id,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    if with_rule:
        # The definition first, then the cadence onto it (plan step R-F6).
        make_every_period_rule(db.session, template)
    db.session.commit()
    return template


def _create_transfer(
    seed_user, seed_periods_today, savings_acct,
    template=None, amount=Decimal("200.00"), name="Monthly Savings",
):
    """Helper: create a transfer in the first paycheck.

    Two arms (plan step balance:X-ch).  With a *template* the transfer is the
    ENGINE's transfer of that definition (:func:`generate_transfer_of`):
    derived, dated, priced by the definition's series, so ``amount`` and
    ``name`` do not apply -- both are the definition's.  Without one it is an
    AD-HOC transfer through the service, owning ``amount``; ``amount`` and
    ``name`` are parameterised so callers that need multiple ad-hoc transfers
    in the same period can distinguish them and avoid the F-050 / C-22
    partial unique index ``uq_transfers_adhoc_dedupe`` (which legitimately
    rejects two active ad-hoc rows with identical parameters).
    """
    if template is not None:
        xfer = generate_transfer_of(template, seed_periods_today[0])
        db.session.commit()
        return xfer
    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings_acct.id,
            pay_period_id=seed_periods_today[0].id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=projected.id,
            category_id=seed_user["categories"]["Rent"].id,
            name=name,
        ),
    )
    db.session.commit()
    return xfer


def _received_transfer_of(template, period, seed_user, figure):
    """Return the engine's transfer of *template* in *period*, RECEIVED, owning *figure*.

    The specimen the CRIT-05 hard-delete cases are about, and one no door
    writes: ``Received`` is not a transfer status at all (the state machine
    settles a transfer with ``Done``; ``Received`` is a display convention for
    income rows), so the cases are a defence-in-depth grade of the route's
    ``is_settled`` filter over a state the application cannot produce.  The
    transfer is the ENGINE's (plan step balance:X-ch), re-priced by the owner
    through the transfer door so the figure SURVIVES the hard delete's
    ``ON DELETE SET NULL`` on the link -- a derived transfer whose definition
    is gone can be priced by nothing -- and the status is then laid on BARE,
    because the one door that settles refuses this status.  The PARENT's
    status is the one status since plan step ``balance:X-bi-6-4d-2``, and
    laid bare it leaves both sides holding no payment record (the shape of a
    ``$0.00`` close, which the band rule stores).  So the figure a ``$0.00``
    close stores is laid beside it, ``0.00`` and typed (ruling **R-BAL365**):
    since that step's 4d-4b-2 leaf the band trigger's figure arm refuses a
    settled transfer storing no figure.  It gave both twins the status too
    until plan step ``balance:X-bi-6-4d-2``, which births none: that loop
    ran over nothing (ruling R-BAL167 class 4, plan step
    balance:X-bi-6-4d-2).
    """
    xfer = transfer_repriced_by_the_owner(
        generate_transfer_of(template, period), figure,
    )
    received = db.session.query(Status).filter_by(name="Received").one()
    xfer.status_id = received.id
    xfer.settled_figure_pair = SettledFigure(
        Decimal("0.00"),
        ref_cache.movement_figure_source_id(MovementFigureSourceEnum.TYPED),
    )
    db.session.flush()
    return xfer


def _create_other_user_with_template():
    """Create a second user with their own template and transfer.

    Returns:
        dict with keys: user, account, savings, template, transfer.
    """
    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()


    # The account_service factory requires the user to have at least one pay
    # period to anchor against.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    from datetime import date as _date
    from tests._test_helpers import open_owner_calendar as _open_calendar
    _bootstrap = _open_calendar(other_user.id, _date(2024, 1, 5))[0]
    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()

    checking = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add_all([checking, savings])
    # Both sets of BOOKS open the day before their origination -- see
    # ``_create_savings_account`` above (plan step X-f3c-2b, ruling R-HG).
    open_books_before_the_first_assertion(db.session, checking)
    open_books_before_the_first_assertion(db.session, savings)

    scenario = Scenario(
        user_id=other_user.id, name="Baseline", is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    from datetime import date
    periods = record_paydays_across_a_hole(
        user_id=other_user.id,
        first_payday=date(2026, 1, 2),
        num_periods=3,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    # The other owner's definition, priced and with a cadence, and its
    # transfer is the ENGINE's (plan step balance:X-ch); the builder reads
    # only the owner and the source account off the seed-shaped dict.
    template = make_transfer_template(
        db.session, {"user": other_user, "account": checking}, savings,
        amount="100.00",
    )
    template.name = "Other Transfer"
    xfer = generate_transfer_of(template, periods[0])
    db.session.commit()

    return {
        "user": other_user,
        "template": template,
        "transfer": xfer,
    }


# ── Helpers for Negative-Path Tests ───────────────────────────────


def _create_second_user_transfer(second_user_data):
    """Create a transfer for the second_user fixture (IDOR testing).

    Creates a savings account, pay periods, and a transfer instance
    for the second user.

    Args:
        second_user_data: Dict from the second_user conftest fixture.

    Returns:
        Transfer: the created transfer.
    """
    from datetime import date as _date  # pylint: disable=import-outside-toplevel

    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user_data["user"].id,
            account_type_id=savings_type.id,
            name="Other Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(savings)
    db.session.flush()

    periods = record_paydays_across_a_hole(
        user_id=second_user_data["user"].id,
        first_payday=_date(2026, 1, 2),
        num_periods=3,
        rhythm=rhythm_of(14),
    )
    db.session.flush()

    projected = db.session.query(Status).filter_by(name="Projected").one()
    xfer = Transfer(
        user_id=second_user_data["user"].id,
        from_account_id=second_user_data["account"].id,
        to_account_id=savings.id,
        pay_period_id=periods[0].id,
        scenario_id=second_user_data["scenario"].id,
        status_id=projected.id,
        name="Other Transfer",
        amount_ownership=AmountOwnership.own(Decimal("100.00")),
    )
    db.session.add(xfer)
    db.session.commit()
    return xfer


def _THREE_DAYS_AGO():
    """A settle day that is NOT the user's today.

    Every "a figure correction did not move the settle day" assertion needs a
    fixture day the seam could not have re-stamped, or it grades nothing: a
    settle stamps ``display_today()``, so on a today-dated row "preserved" and
    "re-stamped" read the same.  Three days back is inside any generated
    schedule, so it clears ruling **R-EL**'s floor, and it is in the past, so it
    clears ruling **R-EJ**'s future refusal.
    """
    return display_today() - timedelta(days=3)
