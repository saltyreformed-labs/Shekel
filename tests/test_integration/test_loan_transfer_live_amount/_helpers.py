"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_integration/test_loan_transfer_live_amount.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.extensions import db
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import cash_ledger, transfer_legs
from app.services import template_amount_service
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    loan_params_for,
    make_cadence_rule,
    transfer_side_record,
)
from tests.oracles.recurrence_baseline import MONTHLY


def _derived_cash(seed_user, legs):
    """What a SCREEN shows for *legs* -- the figure the amount model resolves.

    Plan step X-au-c2b split ``live_loan_transfer_amounts`` into an owner-scoped
    DERIVATION and a per-row lookup, and collapsed the settle-time twin
    ``live_loan_payment_amount`` into the same rule; plan step X-au-g-2c-2 then
    deleted that rule outright, because a transfer shadow stores no figure for a
    read-time override to supersede.  So the map this file grades is the
    amount model's own, and the assertions below keep their original figures.
    It was ``cash_ledger.amounts_by_key`` over the shadow rows, keyed by
    transaction id, until plan step balance:X-bi-6-4d-2 stopped the shadows
    being born; it is ``cash_ledger.amounts_by_key`` over the legs now,
    keyed by ``leg.cell_key`` -- what the grid publishes for a leg since plan
    step ``balance:X-bi-6-1`` (ruling **R-BAL167** class 4).

    **It covers EVERY leg it is given, where the override map covered only the
    rows that had one.**  That is the substantive difference, and two cases turn
    on it: a manual payment and a non-derive transfer used to be ABSENT from the
    map (``assert overrides == {}``) and are now PRESENT with their definition's
    own price, which is a sharper claim -- absence could not distinguish "no
    override" from "not priced at all".

    A FRESH basis per call, deliberately: the cases that mutate an escrow line
    and re-read must see the new figure, and a basis memoizes each loan for the
    length of the read pass it was built for.

    Args:
        seed_user: The owner fixture; its user and scenario pin the basis.
        legs: The :class:`~app.services.transfer_legs.TransferLeg` values to
            price.

    Returns:
        ``{(transfer_id, account_id): Decimal}`` covering every leg.
    """
    basis = cash_ledger.derived_amount_basis(
        seed_user["user"].id, seed_user["scenario"].id,
    )
    return cash_ledger.amounts_by_key(legs, basis)


def _state_price(template, figure):
    """State *figure* as the template's price, through the one write door.

    ``template_amount_service.set_amount`` is what every app-side create calls,
    and since plan step X-au-f it is what prices the transfers a template
    generates.  Dated at the epoch the suite's periods sit after, so every
    generated row resolves to it whatever its own due date -- these cases grade
    a MODE, not the series' time dimension, which
    ``test_amount_source.TestTheTransferRule`` already owns.

    Args:
        template: The TransferTemplate to price.
        figure: The amount it states.
    """
    template_amount_service.set_amount(
        template, figure, effective_on=date(2000, 1, 1),
    )
    db.session.flush()


def _build_derived_loan_transfer(
    seed_user, escrow_annual, origination_date=date(2026, 1, 1),
    payment_day=1, fires_on_day=1,
):
    """Create a $200k/6%/360 mortgage + a derive_from_loan recurring transfer.

    The loan is due on *payment_day* and its payment's rule fires on
    *fires_on_day*; both are the 1st unless a case states otherwise, and a
    case that parts them builds a payment due OFF the contractual day.

    Returns ``(loan_account, escrow_version, scenario_id)``.  The
    transfer's stored default amount is intentionally a stale value so
    the test can prove the live override, not the stored amount, drives
    the result.

    The loan is built by the shared :func:`create_loan_account` factory, so it
    carries the genesis posting ledger every production loan-write path opens in
    the same transaction as the ``LoanParams`` insert (``routes/loan/params.py``)
    -- a hand-rolled ``LoanParams`` insert would leave it on the no-ledger
    fallback production never takes.
    """
    user = seed_user["user"]
    scenario_id = seed_user["scenario"].id
    checking = seed_user["account"]

    loan = create_loan_account(
        seed_user, db.session, name="Live Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=origination_date, payment_day=payment_day,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    params = loan_params_for(db.session, loan.id)

    escrow = add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )

    # Authored through the write door (plan step R7c-b): the day a rule fires
    # on is its first occurrence's own day, so "the 1st" is a DATE the fixture
    # schedule reaches rather than a separate column.
    template = TransferTemplate(
        user_id=user.id,
        from_account_id=checking.id,
        to_account_id=loan.id,
        name="Live Mortgage Payment",
        # Deliberately stale stored amount -- the live override must win.
        default_amount=Decimal("1.00"),
    )
    db.session.add(template)
    db.session.flush()
    # **The definition STATES its price through the one write door**, which is
    # what every app-side transfer-template create does
    # (``routes/transfers/templates``, ``routes/loan/payment_transfer``) and
    # what a fixture had not needed to until plan step X-au-f.  A generated
    # transfer stored its own figure before that step, so a MANUAL payment's
    # base was read off the row's column and the series was never asked; the
    # parent reads its definition now, and a template carrying a
    # ``default_amount`` and no version states no price at all -- amount rule 3
    # REFUSES it rather than falling back to that scalar (plan step X-au-a).
    # Stated BEFORE the derive-mode settings are attached, because
    # ``owns_its_amount`` is False for a derive-mode template and the write door
    # reads the mode: that is the same order ``track_payment`` takes, and the
    # versions stay as the record of what was stated while it was manual.
    _state_price(template, template.default_amount)
    # derive_from_loan moved off transfer_templates into the 1:1
    # loan_payment_settings row (decision B); attach it via the relationship.
    # AFTER the price above, for the reason stated there.
    template.settings = LoanPaymentSettings(derive_from_loan=True)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_cadence_rule(
        template, MONTHLY, fires_on_day=fires_on_day,
    )

    periods = seed_user["periods"] if "periods" in seed_user else None
    return loan, escrow, scenario_id, template, rule, periods


def _mark_done_from_leg(auth_client, transfer_id, account_id):
    """Settle the transfer the way the grid does since plan step balance:X-bi-6-1.

    The grid draws a transfer as a LEG read off its parent and the leg's
    Mark Paid posts to the TRANSFER's door with ``leg_account_id`` (ruling
    R-BAL87); these cases posted to the SHADOW row's transaction door until
    that leaf deleted the shadow branch.  The leg is *transfer_id*'s on
    *account_id*; the response is the leg's cell.  It took the loan-side
    shadow row and read only its ``transfer_id`` and ``account_id`` until plan
    step balance:X-bi-6-4d-2 stopped the shadow being born, so it takes those
    two now (ruling **R-BAL167** class 4).
    """
    return auth_client.post(
        f"/transfers/instance/{transfer_id}/mark-done",
        data={"leg_account_id": str(account_id)},
    )


def _side_leg(transfer_id, account_id):
    """Return *transfer_id*'s leg on *account_id*, its record checked by its own SQL.

    What these cases value a settled side as since plan step
    ``balance:X-bi-6-4d-2`` (ruling **R-BAL167** class 4): a side's payment
    record hangs off the TRANSFER and nothing keeps a twin's status or figure,
    so a settled side is the transfer's status and the side's record, where
    these cases read the twin row until then.  ``test_transfer_settle_freeze``
    imports it.  The leg reads its record off the transfer's list since ruling
    **plan#1984** and can no longer be handed one, so the independent SQL over
    the side links (``transfer_side_record``, never ``transfer_legs``' own
    read) stays as a check on that read rather than as its source.
    """
    leg = transfer_legs.leg_of(db.session.get(Transfer, transfer_id), account_id)
    assert leg.record is transfer_side_record(db.session, transfer_id, account_id)
    return leg


def _loan_payment_legs(loan_id, scenario_id):
    """Return both legs of every transfer into *loan_id* in *scenario_id*.

    From-side first, transfers in id order.  It was
    ``_loan_transfer_shadows`` and returned every shadow row in the scenario
    -- both sides, and IGNORING ``loan_id`` -- until plan step
    balance:X-bi-6-4d-2 stopped the shadows being born (ruling **R-BAL167**
    class 4).  The legs are read off each transfer with
    :func:`~app.services.transfer_legs.leg_of`, so both sides stay covered
    and the parameter now selects what it names.
    """
    payments = (
        db.session.query(Transfer)
        .filter(
            Transfer.to_account_id == loan_id,
            Transfer.scenario_id == scenario_id,
        )
        .order_by(Transfer.id)
        .all()
    )
    return [
        transfer_legs.leg_of(xfer, account_id)
        for xfer in payments
        for account_id in (xfer.from_account_id, loan_id)
    ]


def _first_payment(loan_id, scenario_id, due_date=None):
    """Return the transfer into *loan_id* due on *due_date*, or the first one.

    Where these cases found the loan-side SHADOW row due that day (or the
    first by id), until plan step balance:X-bi-6-4d-2 stopped the shadows
    being born (ruling **R-BAL167** class 4): the shadow copied its due date
    and pay period off this transfer, and its leg on the loan is
    ``transfer_legs.leg_of(xfer, loan_id)``.  With *due_date* it insists on
    exactly one, as the shadow lookup did.
    """
    query = db.session.query(Transfer).filter(
        Transfer.to_account_id == loan_id,
        Transfer.scenario_id == scenario_id,
    )
    if due_date is not None:
        return query.filter(Transfer.due_date == due_date).one()
    return query.order_by(Transfer.id).first()
