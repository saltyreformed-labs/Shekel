"""What a loan payment's settle captures: the live price, base plus extra, the extra in principal
and each payment's own installment, and nothing after the settle moves it.
"""

from datetime import date
from decimal import Decimal

from app.models.loan_features import RateHistory
from app.models.transfer import Transfer
from app.services import cash_ledger, loan_ledger, transfer_legs, transfer_recurrence
from app.services.loan_loaders import loan_payment_due_date
from app.services.balance_at import BalanceContext, cash_balance_at
from app.services.generation_schedule import GenerationSchedule
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of
from app.services import template_amount_service
from app.utils.dates import display_today
from tests._test_helpers import transfer_side_figure

from ._helpers import (
    _derived_cash,
    _state_price,
    _build_derived_loan_transfer,
    _mark_done_from_leg,
    _side_leg,
    _loan_payment_legs,
    _first_payment,
)


def test_settling_derived_loan_payment_captures_live_amount(
    app, db, auth_client, seed_user, seed_periods,
):
    """A one-click settle freezes the LIVE payment-date amount, not the estimate.

    Capture-on-settle (escrow redesign, Option A): the transfer's stored
    default is a deliberately stale $1.00, and the operator settles via the
    ``mark_done`` route WITHOUT typing an actual.  The frozen ``actual_amount``
    must be the live PITI (P&I 1,199.10 + escrow 300.00 = 1,499.10), NOT the
    $1.00 estimate -- so the settled cash carries exactly the escrow the genesis
    split subtracts (cash == split).  The split then divides 1,499.10 into
    interest 1,000.00 (200,000 * 0.06 / 12), escrow 300.00, and principal
    199.10 (= P&I 1,199.10 - interest 1,000.00).

    **The shadow's own ``estimated_amount`` was NULL, and that was the point
    of the cutover** (plan step X-au-g-2c-2).  It had held the generated
    ``$1.00`` and was deliberately left alone, because it was the base the
    deleted ``_manual_shadow_amount`` derived from -- so a settle that wrote
    it would have made the next settle derive from its own output (finding
    N-259's shape).  The column was empty on a derived row, so that hazard had
    no state to occur in, and what a PROJECTED shadow was worth was asked of
    the amount model rather than of the column; no shadow is born since plan
    step balance:X-bi-6-4d-2, and the paragraph below says what is asked now.

    After the settle, the transfer's status is the one status and each side's
    record holds the captured figure, where both were read off the twin rows
    (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).

    **The payment is the first TRANSFER into the loan since plan step
    balance:X-bi-6-4d-2** (ruling R-BAL167 class 1), where it was the first
    loan-side shadow; it is priced through its leg on the loan.  The two "the
    PLAN column is empty" reads were the shadow's ``estimated_amount`` and the
    transfer's ``amount`` before the settle, and the shadow's after it: the
    one row left is the transfer, so both are its ``amount`` -- the same
    claim with nothing left to violate it.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        payment = _first_payment(loan.id, scenario_id)
        assert payment is not None
        # Pre-settle the payment is worth the LIVE PITI, and it stores no
        # figure to have shown instead.  Asked of the amount model, because
        # ``settled_contribution`` REFUSES a derived row -- its name is the
        # assertion.  The stale $1.00 was here until plan step X-au-f emptied
        # it; the assertion below is what replaces it, and it is the stronger
        # one -- the live figure cannot be a stored copy when no copy is
        # stored.
        transfer_id = payment.id
        assert _derived_cash(seed_user, [transfer_legs.leg_of(payment, loan.id)])[
            (transfer_id, loan.id)
        ] == Decimal("1499.10")
        assert payment.amount is None

        resp = _mark_done_from_leg(auth_client, transfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        xfer = db.session.get(Transfer, transfer_id)
        assert xfer.status.is_settled is True
        # Capture-on-settle froze the LIVE PITI, not the $1.00 estimate.
        assert transfer_side_figure(
            db.session, transfer_id, loan.id,
        ) == Decimal("1499.10")
        assert settled_contribution(
            parent_of(_side_leg(transfer_id, loan.id)),
        ) == Decimal("1499.10")
        # The PLAN column stays empty: a settle RECORDS what moved beside the
        # plan (plan step X-au-c3) and never writes into it.
        assert xfer.amount is None
        # Both sides record the captured figure.
        assert transfer_side_figure(
            db.session, transfer_id, xfer.from_account_id,
        ) == Decimal("1499.10")

        # cash == split: the genesis split reads the frozen cash and subtracts
        # the same escrow, leaving principal = P&I.
        splits = loan_ledger.compute_loan_payment_splits(
            loan.id, scenario_id,
        )
        assert len(splits) == 1
        split = splits[0]
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("300.00")
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")


def _checking_today(seed_user):
    """Return what the owner's checking account holds on the app's today.

    The cash balance of the fixture's checking account on
    :func:`~app.utils.dates.display_today`, the day a leg's Mark Paid settles
    on (it states no day), so a payment paid in the case is inside it.

    Args:
        seed_user: The owner fixture; its account and user pin the read.

    Returns:
        The balance, a ``Decimal``.
    """
    return cash_balance_at(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
        display_today(),
    )


def test_nothing_after_the_settle_moves_what_a_loan_payment_paid(
    app, db, auth_client, seed_user, seed_periods,
):
    """Once a loan payment is paid, nothing that happens later moves what it paid.

    Designed from scratch by ruling **R-BAL284** (Josh, 2026-10-14), after
    rulings R-BAL276 and R-BAL283 each rested on a premise measured false.
    Its one question: once a mortgage payment is paid, can anything that
    happens LATER change what it paid?  Two later events are realistic: the
    plan changes, and "paid" is clicked again from an old tab.

    Checking holds 1,000.00.  The Feb 1 payment is paid through its leg's
    Mark Paid at P&I 1,199.10 + escrow 300.00 = **1,499.10**, so checking
    reads 1,000.00 - 1,499.10 = **-499.10**.  The owner then sets a standing
    extra principal of $100.00 a month through the payment-settings door.
    The extra has no date, so the unpaid payments now plan
    1,199.10 + 300.00 + 100.00 = **1,599.10**, and so does the paid one's
    own plan.  Then the Feb 1 leg's Mark Paid is posted again, as an old tab
    would post it.

    **Each check fails FIRST under its own one-point mutation of the app
    (measured at commit 1e778deea, whose body names each first-fail line
    at that commit):**

    * checking reads -499.10 after the pay, the starting point: it fails
      when the derive-mode price drops its escrow term
      (``_loan_installment``'s sum), so the pay books 1,199.10 and checking
      reads -199.10;
    * the UNPAID Mar 1 payment plans 1,599.10, so the change took: it fails
      when the derive-mode price drops the extra (``_loan_installment``'s
      P&I + escrow + extra sum) and when the settings door redirects
      without saving it;
    * the valuation the calendar's day cells index for a transfer leg
      (``cash_ledger.leg_contributions_by_key``), asked of the PAID Feb 1
      payment's leg on the loan, still reads 1,499.10: it fails when
      ``leg_contribution_of`` stops asking the record first
      (``fixed_contribution`` of the leg's parent skipped) and reads the
      plan's 1,599.10;
    * the repeat click is answered 200: it fails when the transfer
      workflow loses its ``done -> done`` identity edge and refuses the
      click;
    * checking still reads -499.10 after it: it fails when the repeat
      click is taken as a $0.00 close (``status_seam._side``'s
      ``withdraws`` without its ``settlement is not None`` term), which
      takes the paid 1,499.10 off the books and leaves checking at
      1,000.00.

    The other two acts' acceptance reads are graded the same way: the
    first click's 200 fails first when the transfer workflow loses its
    ``projected -> done`` edge and refuses the click (400), and the
    settings door's 302 when its definition gate never matches (404).

    The third check grades that shared valuation, not the calendar's use
    of it: the calendar draws the checking side's leg, and a swap at its
    call site (``calendar_service._contributions`` asking
    ``leg_amounts_by_key``) shows the paid payment there at 1,599.10 with
    this case green.  ``test_calendar_service``'s
    ``test_a_settled_leg_is_a_paid_cell_worth_its_record`` catches that
    swap.  The dashboard's bills load only transfers still projected, so a
    payment marked paid is not among them.

    **Left out, each measured:** checking, the loan's split and the record
    figure the grid's paid cell reads (``settled_amounts_by_key``)
    right after the plan change, which none of the one-point mutations
    measured moves (each reads only the money that moved); the PAID
    payment's own planned figure (``leg_amounts_by_key``, the grid's budget
    map), which reads 1,599.10 beside its 1,499.10 record and is an open
    question of its own, not settled here; and the first guard itself
    (``enters_settled_band`` skipped, so a repeat click runs the settle act
    again), which alone changes nothing this case reads and is graded by
    ``test_transfer_settle_freeze``'s
    ``test_settling_an_ALREADY_settled_transfer_writes_nothing``.

    The payments are the TRANSFERS due 2026-02-01 and 2026-03-01, each read
    through its leg on the loan (plan step balance:X-bi-6-4d-2, ruling
    R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()
        paid_id = _first_payment(loan.id, scenario_id, date(2026, 2, 1)).id
        unpaid_id = _first_payment(loan.id, scenario_id, date(2026, 3, 1)).id

        # STEP 1: pay Feb 1.  Checking: 1,000.00 - 1,499.10 = -499.10.
        resp = _mark_done_from_leg(auth_client, paid_id, loan.id)
        assert resp.status_code == 200, resp.data
        db.session.expire_all()
        assert _checking_today(seed_user) == Decimal("-499.10")

        # STEP 2: a standing extra of $100.00 a month, through the dashboard's
        # payment card.  The door redirects whether or not it saved, so the
        # unpaid payment's plan below is what says the extra took.
        resp = auth_client.post(
            f"/accounts/{loan.id}/loan/payments/{template.id}/settings",
            data={"extra_principal": "100.00"},
        )
        assert resp.status_code == 302, resp.data
        db.session.expire_all()
        # The unpaid Mar 1 payment plans 1,199.10 + 300.00 + 100.00.
        unpaid = db.session.get(Transfer, unpaid_id)
        assert _derived_cash(seed_user, [transfer_legs.leg_of(unpaid, loan.id)])[
            (unpaid_id, loan.id)
        ] == Decimal("1599.10")
        # The valuation the calendar's day cells index still reads the paid
        # Feb 1 payment at what it paid, not at its plan's 1,599.10.
        assert cash_ledger.leg_contributions_by_key(
            [_side_leg(paid_id, loan.id)],
            cash_ledger.derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            ),
        )[(paid_id, loan.id)] == Decimal("1499.10")

        # STEP 3: Feb 1's Mark Paid again, from an old tab.  Accepted as the
        # no-op it is, and checking still -499.10: the paid 1,499.10 stays.
        resp = _mark_done_from_leg(auth_client, paid_id, loan.id)
        assert resp.status_code == 200, resp.data
        db.session.expire_all()
        assert _checking_today(seed_user) == Decimal("-499.10")


def test_settling_with_extra_lands_the_extra_in_principal(
    app, db, auth_client, seed_user, seed_periods,
):
    """cash == split with a standing extra: the extra flows into principal.

    The Sec. 9 invariant with an overpayment.  Derive payment, escrow $3,600/yr,
    extra $100.00.  A one-click settle freezes cash = 1,199.10 P&I + 300.00
    escrow + 100.00 extra = 1,599.10.  The genesis split then divides it:
        interest  = 200,000 * 0.06 / 12 = 1,000.00
        escrow    = 300.00
        principal = 1,599.10 - 1,000.00 - 300.00 = 299.10
    which is the scheduled principal 199.10 (P&I 1,199.10 - interest 1,000.00)
    PLUS the 100.00 extra -- the residual split routes it to principal by
    construction, with no excess.

    The frozen cash is read off the loan side's record, where it was read off
    the loan-side twin row (ruling R-BAL167 class 1, plan step
    balance:X-bi-6-4d-2).  The payment is the first TRANSFER into the loan,
    settled through its leg there, where it was the first loan-side shadow
    until plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.extra_principal = Decimal("100.00")
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        payment = _first_payment(loan.id, scenario_id)
        assert payment is not None
        transfer_id = payment.id

        resp = _mark_done_from_leg(auth_client, transfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        # Frozen cash carries P&I + escrow + extra.
        assert transfer_side_figure(
            db.session, transfer_id, loan.id,
        ) == Decimal("1599.10")

        # The genesis split routes the extra into principal (cash == split).
        splits = loan_ledger.compute_loan_payment_splits(
            loan.id, scenario_id,
        )
        assert len(splits) == 1
        split = splits[0]
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("300.00")
        assert split.principal == Decimal("299.10")
        assert split.excess == Decimal("0.00")


def test_settling_manual_payment_with_extra_captures_base_plus_extra(
    app, db, auth_client, seed_user, seed_periods,
):
    """Capture-on-settle fires for a MANUAL payment carrying a standing extra.

    Manual base $1,499.10 + extra $100.00 -> the settle freezes $1,599.10, so
    the split routes the extra into principal exactly as in derive mode.  A
    manual payment with NO extra would keep its estimate (covered separately).

    The frozen cash is read off the loan side's record, where it was read off
    the loan-side twin row (ruling R-BAL167 class 1, plan step
    balance:X-bi-6-4d-2).  The payment is the first TRANSFER into the loan,
    where it was the first loan-side shadow, and "neither row has a plan
    column to write" is asked of the one row left, until plan step
    balance:X-bi-6-4d-2 (ruling R-BAL167 class 1): the shadow's
    ``estimated_amount`` half went with the shadow.
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        template.settings.extra_principal = Decimal("100.00")
        # The manual BASE is a STATED amount, so its definition holds it in the
        # SERIES (plan step X-au-a) rather than in the ``default_amount``
        # scalar.  Setting only the scalar priced this payment at the fixture's
        # $1.00 + the extra once the parent started reading its definition --
        # $101.00 against the $1,599.10 below, which is the figure this case
        # has always asserted and which is unchanged.
        template.default_amount = Decimal("1499.10")
        _state_price(template, Decimal("1499.10"))
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        payment = _first_payment(loan.id, scenario_id)
        assert payment is not None
        transfer_id = payment.id

        resp = _mark_done_from_leg(auth_client, transfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        xfer = db.session.get(Transfer, transfer_id)
        assert transfer_side_figure(
            db.session, transfer_id, loan.id,
        ) == Decimal("1599.10")
        # The manual BASE is untouched, so a second settle would freeze the
        # same 1,599.10 rather than 1,699.10: the derivation must never read
        # its own output.  **That base is on the DEFINITION since plan step
        # X-au-f**, which is what makes the compounding cycle structurally
        # impossible rather than merely not-currently-written: the manual arm
        # reads the definition's SERIES, and a settle writes neither that nor
        # the transfer's plan column -- it has none left to write, which is
        # the pairing CHECK saying so.  It read the PARENT's stored figure
        # until X-au-f emptied it (X-au-g-2c-2 had moved it off the shadow
        # first), so the base has moved one row further from anything a settle
        # can touch.
        assert xfer.amount is None
        assert template_amount_service.amount_as_of(
            xfer.template, xfer.due_date,
        ) == Decimal("1499.10")


def test_each_payments_cash_is_its_own_installments_pi(
    app, db, seed_user, seed_periods,
):
    """A payment's P&I is its INSTALLMENT's, not the read date's (ruling R-IJ).

    **Renamed at plan step balance:X-bi-6-4d-2's checkpoint 2d-a** (it was
    ``test_each_shadows_cash_is_its_own_installments_pi``; the shadows it
    priced are not born since that step, and each payment's two legs answer
    its one figure).

    Finding **N-40**, closed structurally at plan step X-au-g-2b.  The
    derivation resolved ONE ``compute_monthly_payment_baseline(..., as_of)``
    per loan per read pass and added it to every shadow alike, while the escrow
    in the same sum already resolved on each shadow's own due date.  So a loan
    whose payment recasts mid-horizon priced every projected installment at the
    payment in force on the day the page was rendered.

    The loan: $200,000 / 6% / 360 from 2026-01-01, escrow $3,600/yr, plus a
    recorded recast effective **2026-04-01** stating a $1,500.00 P&I.

        period 0 P&I = amortize(200000, 0.06, 360) = 1,199.10
        period 1 P&I = 1,500.00 (the lender's recorded recast)
        escrow       = 3600 / 12 = 300.00

        installment before 2026-04-01 -> 1,199.10 + 300.00 = 1,499.10
        installment on/after it       -> 1,500.00 + 300.00 = 1,800.00

    Both figures must appear, and the split must fall on the recast date.  A
    fixture whose payments all landed on one side of the boundary would assert
    the same thing about a producer that read one clock, which is why the
    boundary itself is asserted rather than the figures alone.

    It prices the payments' legs, keyed by each leg's installment date, where
    it priced the shadow rows until plan step balance:X-bi-6-4d-2 (ruling
    R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        # The lender's recorded recast: a rate change carrying its own P&I, so
        # period 1's level payment is stated rather than derived (and the
        # expectation below is a quoted contract term, not an amortization).
        db.session.add(RateHistory(
            account_id=loan.id,
            effective_date=date(2026, 4, 1),
            interest_rate=Decimal("0.07000"),
            monthly_pi=Decimal("1500.00"),
        ))
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        assert legs, "expected generated loan payments"

        resolved = _derived_cash(seed_user, legs)
        priced = {
            loan_payment_due_date(leg, 1): resolved[leg.cell_key]
            for leg in legs
        }

        assert priced, "expected derived cash for the derive_from_loan transfer"
        # The fixture really does straddle the recast -- otherwise a producer
        # reading one clock would satisfy the mapping below.
        assert set(priced.values()) == {Decimal("1499.10"), Decimal("1800.00")}, (
            f"the fixture did not straddle the recast: {sorted(priced.items())}"
        )
        for due, cash in priced.items():
            expected = (
                Decimal("1800.00") if due >= date(2026, 4, 1)
                else Decimal("1499.10")
            )
            assert cash == expected, (
                f"installment due {due} priced {cash}, expected {expected}"
            )
