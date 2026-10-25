"""A loan payment transfer's live price: it tracks the escrow without regeneration, per leg and
date, on the loan's due date, on an off-day and for an early extra, and a non-derived transfer
has none.
"""

from datetime import date
from decimal import Decimal

from app.enums import LedgerAccountKindEnum
from app.models.escrow_line import EscrowComponentVersion
from app.models.transfer import Transfer
from app.services import loan_ledger, transfer_legs, transfer_recurrence
from app.services.installment_calendar import monthly_due_date
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.row_valuation import leg_settled_contribution
from tests._test_helpers import find_loan_ledger_account, ledger_net

from ._helpers import (
    _derived_cash,
    _build_derived_loan_transfer,
    _mark_done_from_leg,
    _side_leg,
    _loan_payment_legs,
    _first_payment,
)


def test_derived_transfer_amount_tracks_escrow_without_regeneration(
    app, db, seed_user, seed_periods,
):
    """The transfer's live cash amount = P&I + escrow, and reflows on escrow change.

    Loan $200,000 / 6% / 360mo, escrow $3,600/yr:
        P&I    = amortize(200000, 0.06, 360) = 1,199.10
        escrow = 3600 / 12 = 300.00
        PITI   = 1,199.10 + 300.00 = 1,499.10
    After escrow rises to $4,800/yr (400.00/mo):
        PITI   = 1,199.10 + 400.00 = 1,599.10
    The stored Transfer.amount never changes (no regeneration); only the
    live override reflects the new escrow.

    Both reads price the payments' LEGS, where they priced the shadow rows
    until plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 1); the
    second read is now guarded non-empty too.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        assert legs, "expected generated loan payments"

        overrides = _derived_cash(seed_user, legs)
        # Every leg of this loan's transfers gets the live PITI.
        assert overrides, "expected live overrides for the derive_from_loan transfer"
        assert all(v == Decimal("1499.10") for v in overrides.values())

        # **The stored transfer amounts are EMPTY**, which says what "untouched
        # at the stale $1.00" used to say and says it more strongly: the figure
        # is live-derived rather than regenerated because there is no stored
        # figure left to regenerate.  Plan step X-au-f's migration is what
        # emptied the column, and ``ck_transfers_amount_ownership`` is what
        # makes the pairing below one statement rather than two.
        stored = {
            (xfer.amount, xfer.amount_source_id is None)
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored == {(None, False)}

        # Raise escrow; the live override reflows without regeneration.
        escrow.annual_amount = Decimal("4800.00")
        db.session.commit()

        overrides_after = _derived_cash(seed_user, legs)
        assert overrides_after
        assert all(v == Decimal("1599.10") for v in overrides_after.values())
        # Still no regeneration, and still nothing stored to regenerate.
        stored_after = {
            (xfer.amount, xfer.amount_source_id is None)
            for xfer in db.session.query(Transfer)
            .filter_by(scenario_id=scenario_id)
            .all()
        }
        assert stored_after == {(None, False)}


def test_non_derived_transfer_has_no_live_override(
    app, db, seed_user, seed_periods,
):
    """A transfer whose template is NOT derive_from_loan is worth its DEFINITION's price.

    Confirms the loan derivation is dormant unless explicitly enabled (the
    "only new transfers" choice: every pre-existing template is False).  The
    fixture's stored base is a deliberately stale ``$1.00``, so a shadow worth
    ``$1.00`` is one the loan did not price and a shadow worth ``$1,499.10``
    is one it did.

    **It asserted an EMPTY override map until plan step X-au-g-2c-2**, which
    could not tell "the loan did not price this" from "nothing priced this at
    all".  Naming the figure is the stronger claim, and it is the one that
    would catch a shadow that had stopped being priced.  It prices the
    payments' legs, where it priced the shadow rows until plan step
    balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        template.settings.derive_from_loan = False
        db.session.flush()
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        assert legs, "expected generated loan payments"
        resolved = _derived_cash(seed_user, legs)
        assert all(v == Decimal("1.00") for v in resolved.values())


def test_derived_transfer_due_date_matches_loan_due_date(
    app, db, seed_user, seed_periods,
):
    """A derive_from_loan transfer is due on the loan's true monthly due date.

    The loan card derives its due dates from LoanParams.payment_day via
    installment_calendar.monthly_due_date.  The transfer recurrence now uses the
    shared compute_due_date, and the loan template's rule carries
    day_of_month = payment_day (1), so the transfer lands on the 1st of each
    month -- matching the loan card -- rather than the pay-period start (~2
    weeks early) it used before.  Over seed_periods (biweekly from
    2026-01-02), day 1 falls in P2/P4/P6/P8, giving due dates 2026-02-01,
    03-01, 04-01, 05-01.

    **The "both shadows mirror the parent" block went at plan step
    balance:X-bi-6-4d-2** (ruling R-BAL167 class 1, with no subject left):
    it asserted each shadow row's copied ``due_date`` (Transfer Invariant
    3), and no shadow is born.  A leg's due date IS its parent's
    (``TransferLeg.due_date`` reads the transfer), so there is no copy left
    to disagree; the parent's own assertions stand.
    """
    with app.app_context():
        _loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        created = transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        db.session.commit()

        assert sorted(x.due_date for x in created) == [
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
        ]
        for xfer in created:
            # Parent due date equals the loan's contractual monthly due date.
            assert xfer.due_date == monthly_due_date(
                xfer.pay_period.start_date, 1,
            )
            assert xfer.due_date.day == 1


def test_derived_override_is_per_leg_date_aware(
    app, db, seed_user, seed_periods,
):
    """A future-dated escrow version changes only the legs due on/after its date.

    Loan $200k / 6% / 360mo, P&I 1,199.10.  Escrow $3,600/yr (300/mo) from
    origination (2026-01-01), then a NEW version $4,800/yr (400/mo) effective
    2026-03-15 on the SAME line.  The live price resolves escrow per payment
    INSTALLMENT: a payment due before 2026-03-15 keeps PITI 1,499.10 on both
    legs; one due on or after picks up 1,599.10.  A single figure per loan
    (today's escrow for every payment) would wrongly give them all 1,599.10 --
    the bug this per-installment resolution fixes, and the cash side of the
    cash==split invariant for future-dated escrow.

    **Renamed at plan step balance:X-bi-6-4d-2's checkpoint 2d-a** (it was
    ``test_derived_override_is_per_shadow_date_aware``; the shadows it
    partitioned are not born since that step).

    The partition is by DUE date (ruling D5's contract time, finding N-34); this
    fixture's payments are due 02-01 / 03-01 / 04-01 / 05-01 against pay periods
    starting 01-30 / 02-27 / 03-27 / 04-24, so the two keyings happen to group
    them identically here.  The test that DISCRIMINATES them is
    :func:`test_live_cash_and_split_agree_on_a_mid_window_escrow_change`, which
    puts the version inside a single payment's period-start-to-due-date window.

    It partitions the payments' LEGS by their due date (the parent's), where
    it partitioned the shadow rows by theirs until plan step
    balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(seed_user, Decimal("3600.00"))
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id), {p.id for p in seed_periods},
            ), scenario_id,
        )
        # Append a second version on the SAME line: 400/mo effective 2026-03-15.
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 3, 15),
            annual_amount=Decimal("4800.00"),
        ))
        db.session.commit()

        legs = _loan_payment_legs(loan.id, scenario_id)
        overrides = _derived_cash(seed_user, legs)
        cutoff = date(2026, 3, 15)
        before = [leg for leg in legs if leg.due_date < cutoff]
        after = [leg for leg in legs if leg.due_date >= cutoff]
        assert before and after, (
            "seed_periods must place installments on both sides of 2026-03-15"
        )
        # Old escrow ($300) for pre-effective legs: 1199.10 + 300 = 1499.10.
        assert all(overrides[leg.cell_key] == Decimal("1499.10") for leg in before)
        # New escrow ($400) for on/after legs: 1199.10 + 400 = 1599.10.
        assert all(overrides[leg.cell_key] == Decimal("1599.10") for leg in after)


def test_live_cash_and_split_agree_on_a_mid_window_escrow_change(
    app, db, auth_client, seed_user, seed_periods,
):
    """An escrow version inside the period-to-due window moves BOTH cash and split.

    The discriminating control for finding N-34's cash==split half.  The
    installment due 2026-03-01 is paid from the pay period starting 2026-02-27,
    so a version effective **2026-02-28** falls STRICTLY inside that window --
    after the period start, before the due date.  The two keyings disagree there,
    and they must not be allowed to disagree with EACH OTHER:

      * DUE-date keying (ruling D5, as built): escrow 500.00, so the live cash is
        P&I 1,199.10 + 500.00 = **1,699.10**, and the split subtracts the same
        500.00 -- interest 200,000 x 0.06 / 12 = 1,000.00, principal
        1,699.10 - 1,000.00 - 500.00 = 199.10 (exactly P&I - interest).
      * PERIOD-START keying (the N-34 defect): escrow 300.00 -> cash 1,499.10.

    Reverting EITHER end alone re-opens the gap this test exists to close: the
    cash would carry one escrow figure while the split backed out the other, and
    the $200.00 difference would land silently in PRINCIPAL, moving the recorded
    balance.  Asserting ``principal == P&I - interest`` is what pins that -- it
    holds only when both ends read the same date.

    The loan originates 2026-02-01, the month before the one installment this
    case settles: every contractual installment from origination is charged
    since plan step recurrence:R16-c-2, so the builder's 2026-01-01 would
    leave February (its row generated, never settled) standing for the March
    payment to clear (ruling R-R103).  Generation runs over the pay periods
    after the origination only, since the payment door refuses the February
    occurrence as falling on it (ruling R-C).

    The settled payment is read as the transfer and its loan side's record,
    where it was read off the loan-side twin row (ruling R-BAL167 class 1,
    plan step balance:X-bi-6-4d-2).  The payment is the TRANSFER due
    2026-03-01, priced and settled through its leg on the loan, where it was
    the loan-side shadow due that day until plan step balance:X-bi-6-4d-2
    (ruling R-BAL167 class 1); the shadow copied the window's two dates off
    the transfer.
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(
                seed_user, Decimal("3600.00"),
                origination_date=date(2026, 2, 1),
            )
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods if p.start_date > date(2026, 2, 1)},
            ), scenario_id,
        )
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 2, 28),
            annual_amount=Decimal("6000.00"),
        ))
        db.session.commit()

        xfer = _first_payment(loan.id, scenario_id, date(2026, 3, 1))
        xfer_id = xfer.id
        # The version really is inside the window: after the period start,
        # before the installment it governs.
        assert (
            xfer.pay_period.start_date
            < date(2026, 2, 28)
            < xfer.due_date
        )

        overrides = _derived_cash(seed_user, [transfer_legs.leg_of(xfer, loan.id)])
        assert overrides[(xfer_id, loan.id)] == Decimal("1699.10")

        resp = _mark_done_from_leg(auth_client, xfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = _side_leg(xfer_id, loan.id)
        assert leg_settled_contribution(settled) == Decimal("1699.10")

        (split,) = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
        assert split.due_date == date(2026, 3, 1)
        assert split.escrow == Decimal("500.00")
        assert split.interest == Decimal("1000.00")
        # The cash==split invariant: every cent of escrow built into the cash is
        # backed out again, so principal is exactly P&I - interest.
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")
        assert (
            split.interest + split.escrow + split.principal + split.excess
            == leg_settled_contribution(settled)
        )


def test_an_off_day_payment_is_priced_on_its_intervals_installment(
    app, db, auth_client, seed_user, seed_periods,
):
    """An off-day payment's cash and its split read ONE installment (ruling R-R104).

    The loan is due the 22nd and originates 2026-01-22, so its first
    installment is 02-22; its payment's rule fires on the 10th, so the row due
    **2026-03-10** falls in the 02-22 interval and clears 02-22's charge
    (ruling R-R89).  Escrow is $1,200.00 a year ($100.00 a month) from
    origination, rising to $3,600.00 ($300.00) on **2026-03-01** -- between
    the installment and the payment's own due date, the one window where the
    two datings differ:

      * interval keying (ruling R-R104, as built): the cash is P&I 1,199.10 +
        02-22's 100.00 = **1,299.10**, and the split backs out the same
        100.00 -- interest 200,000 x 0.06 / 12 = 1,000.00, principal
        1,299.10 - 1,000.00 - 100.00 = 199.10, exactly P&I - interest.
      * own-date keying (ruling R-IJ before R-R104): the cash was 1,199.10 +
        300.00 = 1,499.10 against the same 100.00 backed out, so 399.10 went
        to principal and the recorded balance read $200.00 low.

    Generation runs over the pay periods after the origination, since the
    payment door refuses an occurrence at or before it (ruling R-C); the 02-10
    row it writes is an early extra and stays projected.

    The settled payment is read as the transfer and its loan side's record,
    where it was read off the loan-side twin row (ruling R-BAL167 class 1,
    plan step balance:X-bi-6-4d-2).  The payment is the TRANSFER due
    2026-03-10, priced and settled through its leg on the loan, where it was
    the loan-side shadow due that day until plan step balance:X-bi-6-4d-2
    (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(
                seed_user, Decimal("1200.00"),
                origination_date=date(2026, 1, 22),
                payment_day=22, fires_on_day=10,
            )
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods if p.start_date > date(2026, 1, 22)},
            ), scenario_id,
        )
        db.session.add(EscrowComponentVersion(
            line_id=escrow.line_id,
            effective_date=date(2026, 3, 1),
            annual_amount=Decimal("3600.00"),
        ))
        db.session.commit()

        xfer = _first_payment(loan.id, scenario_id, date(2026, 3, 10))
        xfer_id = xfer.id
        assert _derived_cash(seed_user, [transfer_legs.leg_of(xfer, loan.id)])[
            (xfer_id, loan.id)
        ] == Decimal("1299.10")

        resp = _mark_done_from_leg(auth_client, xfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = _side_leg(xfer_id, loan.id)
        assert leg_settled_contribution(settled) == Decimal("1299.10")

        (split,) = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
        assert split.due_date == date(2026, 3, 10)
        assert split.charge_date == date(2026, 2, 22)
        assert split.interest == Decimal("1000.00")
        assert split.escrow == Decimal("100.00")
        assert split.principal == Decimal("199.10")
        assert split.excess == Decimal("0.00")
        assert (
            split.interest + split.escrow + split.principal + split.excess
            == leg_settled_contribution(settled)
        )


def test_an_early_extra_is_charged_nothing_in_the_posted_ledger(
    app, db, auth_client, seed_user, seed_periods,
):
    """Ruling R-C's early extra, settled: no charge stands over it, so it is all principal.

    The off-day case's loan (due the 22nd, originated 2026-01-22, first
    installment 02-22, escrow $100.00 a month).  Its row due **2026-02-10**
    falls after the origination and before the first installment, so no
    installment's interval holds it
    (:func:`~app.services.installment_calendar.installment_of` answers
    ``None``) and it is priced on its own date: P&I 1,199.10 + 02-10's
    escrow 100.00 = **1,299.10**.  Settled, the posted walk hands it NO
    charge -- nothing has fallen due yet -- so its whole cash is principal:
    interest 0.00, escrow 0.00, principal 1,299.10, and the loan's posted
    interest ledger books nothing.  Until plan step recurrence:R16-c-2 the
    settled walk charged a month at the payment's own date; only a hand-built
    stream pinned the new rule before this case.

    The settled payment is read as the transfer and its loan side's record,
    where it was read off the loan-side twin row (ruling R-BAL167 class 1,
    plan step balance:X-bi-6-4d-2).  The payment is the TRANSFER due
    2026-02-10, settled through its leg on the loan, where it was the
    loan-side shadow due that day until plan step balance:X-bi-6-4d-2
    (ruling R-BAL167 class 1).
    """
    with app.app_context():
        loan, _escrow, scenario_id, template, _rule, _periods = (
            _build_derived_loan_transfer(
                seed_user, Decimal("1200.00"),
                origination_date=date(2026, 1, 22),
                payment_day=22, fires_on_day=10,
            )
        )
        transfer_recurrence.generate_for_template(
            template, GenerationSchedule.for_period_ids(
                BalanceContext.build(template.user_id),
                {p.id for p in seed_periods if p.start_date > date(2026, 1, 22)},
            ), scenario_id,
        )
        db.session.commit()

        xfer_id = _first_payment(loan.id, scenario_id, date(2026, 2, 10)).id
        resp = _mark_done_from_leg(auth_client, xfer_id, loan.id)
        assert resp.status_code == 200, resp.data

        db.session.expire_all()
        settled = _side_leg(xfer_id, loan.id)
        assert leg_settled_contribution(settled) == Decimal("1299.10")

        (split,) = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
        assert split.due_date == date(2026, 2, 10)
        assert split.charge_date is None
        assert (split.interest, split.escrow, split.principal, split.excess) == (
            Decimal("0.00"), Decimal("0.00"), Decimal("1299.10"), Decimal("0.00"),
        )
        # The per-loan interest ledger is minted by the first posting that
        # books interest, so a loan whose only payment books none may have
        # none -- which counts as nothing booked, and any interest posted
        # would both mint it and move its net.
        interest_ledger = find_loan_ledger_account(
            db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
        )
        interest_booked = (
            Decimal("0.00") if interest_ledger is None
            else ledger_net(db.session, interest_ledger.id, scenario_id)
        )
        assert interest_booked == Decimal("0.00")
