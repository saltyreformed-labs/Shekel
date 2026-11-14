"""The first installment's derivation, and the sync that writes it as the rule's start."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.services import (
    installment_calendar,
    loan_loaders,
    loan_recurrence_sync,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    WeekdayReading,
    reauthor_rule,
    recurrence_spec,
    scheduling_day_of_month,
)
from app.services.generation_schedule import GenerationSchedule
# Pylint: ``shekel-private-module-import`` -- the seam publishes no reading of
# the contract from origination; its one reader is the property chart, so the
# first-installment pin reaches the seam module that composes it, as
# ``test_the_contract_line`` does.
# pylint: disable-next=shekel-private-module-import
from app.services.balance_at._loan_stream import origination_contract_line
from tests._test_helpers import create_loan_account, make_transfer_template

from ._helpers import PRINCIPAL, _upcoming_mortgage


class TestFirstInstallmentDate:
    """The derivation itself: the loan engine's convention, not a calendar guess."""

    @pytest.mark.parametrize("origination, payment_day, expected", [
        # One month after origination, ON payment_day -- NOT the next
        # payment_day, which would be 2026-04-20 for the second case.
        (date(2026, 4, 15), 1, date(2026, 5, 1)),
        (date(2026, 4, 15), 20, date(2026, 5, 20)),
        (date(2026, 4, 15), 15, date(2026, 5, 15)),
        (date(2026, 4, 1), 1, date(2026, 5, 1)),
        # Year rollover.
        (date(2026, 12, 10), 5, date(2027, 1, 5)),
        # Day clamped to a short month (31 -> Feb 28 in a non-leap year).
        (date(2026, 1, 20), 31, date(2026, 2, 28)),
        (date(2028, 1, 20), 31, date(2028, 2, 29)),
    ])
    def test_the_convention(self, origination, payment_day, expected):
        """The first installment is the payment_day of the month AFTER origination."""
        assert installment_calendar.first_installment_date(
            origination, payment_day,
        ) == expected

    def test_it_is_not_the_next_payment_day(self):
        """The distinction from ``monthly_due_date``, which answers a DIFFERENT question.

        ``monthly_due_date`` returns the first ``payment_day`` ON OR AFTER a date
        (the installment a pay period contains).  For a loan originating
        2026-04-15 with payment_day 20 that is 2026-04-20 -- five days after
        closing, and NOT what the loan bills.  Sourcing the bound from the wrong
        one of these two would admit an installment the engine never schedules.
        """
        assert installment_calendar.monthly_due_date(
            date(2026, 4, 15), 20,
        ) == date(2026, 4, 20)
        assert installment_calendar.first_installment_date(
            date(2026, 4, 15), 20,
        ) == date(2026, 5, 20)

    @pytest.mark.parametrize("origination, payment_day", [
        (date(2026, 4, 15), 1),
        (date(2026, 4, 15), 20),
        (date(2018, 12, 1), 1),
        (date(2023, 2, 14), 22),
        (date(2026, 12, 10), 5),
        (date(2026, 1, 20), 31),
    ])
    def test_it_is_the_contracts_first_installment_from_origination(
        self, app, db, seed_user, seed_periods, origination, payment_day,
    ):
        """The bound's derivation is the first installment the contract bills.

        ``first_installment_date`` exists so the bound does not have to replay
        a 360-installment contract to learn one date.  That is only legitimate
        while the two agree, so this pins them against each other across
        shapes rather than asserting the claim in a docstring: the contract
        from origination (``_loan_stream.origination_contract_line``) begins
        its installments after the opening by its own lookup
        (``installment_after``), so a convention that billed a payment day
        later in the origination month would part them.  It was named
        ``test_it_equals_the_engine_schedule_row_zero`` and read the
        from-origination schedule's first row until plan step
        recurrence:R16-f deleted that schedule (ruling plan#4107).
        """
        with app.app_context():
            acct = create_loan_account(
                seed_user, db.session, name=f"L{origination}{payment_day}",
                principal=PRINCIPAL, rate=Decimal("0.05000"), term=360,
                origination_date=origination, payment_day=payment_day,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            walk = origination_contract_line(
                BalanceContext.build(acct.user_id).loan_walk(acct).stream,
            )
            assert walk.projected_splits[0].due_date == (
                installment_calendar.first_installment_date(
                    origination, payment_day,
                )
            )


class TestStartBoundIsSynced:
    """``sync_loan_payment_start`` writes the start bound from the loan."""

    def test_creating_the_payment_bounds_the_rule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The create-transfer route leaves the rule's START bounded and its stop unwritten.

        Both ends were written here until plan step R7d-g; the closing bound
        is derived on every read now and the column stays NULL.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        assert resp.status_code == 302

        xfer = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        )
        rule = xfer.template.recurrence_rule
        # One month after the 2026-04-15 closing, on payment day 1.
        assert rule.starts_on == date(2026, 5, 1)
        assert rule.end_date is None
        assert rule.max_occurrences is None

    def test_a_payment_day_edit_moves_the_bound_and_the_billing_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A ``payment_day`` edit moves the bound and the billing day AS ONE.

        Both used to be separate columns deriving from ``params.payment_day``,
        and moving one without the other was worse than leaving both stale.
        Measured on the real rule shape when only the bound moved (payment_day
        1 -> 20): it advanced to the 20th while the rule still matched the 1st,
        no surviving period contained a matching day, and regeneration produced
        **ZERO** installments -- the loan's entire recurring payment silently
        disappeared.

        **Plan step R7c-b made that divergence unconstructible** rather than
        guarded against: ``starts_on`` is the first occurrence, so its own day
        IS the cycle's day, and ``day_of_month`` is a storage encoding derived
        from it in the write door.  This test still asserts BOTH, because the
        encoded column is what ``recurrence.compute_due_date`` dates
        every generated row from until plan step R5 deletes it -- so a write
        door that stopped deriving it would reproduce the same empty list.

        This must use the loan route's real MONTHLY rule: an EVERY_PERIOD
        template has no day-of-month coordinate at all, so it cannot observe
        the divergence.

        NEGATIVE CONTROL: drop the ``day_of_month`` assignment from
        ``recurrence._authoring._author`` and the regenerated due-date list
        goes empty.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )
        template = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        ).template
        rule = template.recurrence_rule
        assert rule.starts_on == date(2026, 5, 1)
        assert scheduling_day_of_month(rule) == 1

        params = loan_loaders.load_loan_params(acct.id)
        params.payment_day = 20
        db.session.commit()

        with auth_client.application.app_context():
            loan_recurrence_sync.sync_loan_payment_start(acct.id)
            db.session.commit()

        db.session.refresh(rule)
        assert rule.starts_on == date(2026, 5, 20)
        assert scheduling_day_of_month(rule) == 20, (
            "day_of_month must follow payment_day, or the rule bills a day the "
            "bound excludes"
        )

        # And the pair still generates: the loan now bills the 20th, so its
        # first installment after the 2026-04-15 closing is 2026-05-20.
        with auth_client.application.app_context():
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
                effective_from=seed_periods[0].start_date,
            )
            db.session.commit()
        dues = [
            x.due_date for x in db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
        ]
        assert dues == [date(2026, 5, 20)]

    def test_a_re_pointed_rule_drops_a_weekday_reading_for_the_contract_day(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The loan's coordinate is its contract day, whatever the rule read before.

        Plan step recurrence:R8-c, ruling R-R96.  The rule's first date Fri
        2026-05-01 read FROM ITS START is "the first Friday"; a ``payment_day``
        edit to the 20th re-points it onto Wed 2026-05-20.  Carried across,
        the reading would make that "the third Wednesday" -- Jun 17, Jul 15 --
        where the contract bills the 20th every month.

        NEGATIVE CONTROL: drop ``weekday_reading=None`` from
        ``loan_recurrence_sync.loan_cadence_spec`` and the row keeps
        ``'from_start'`` and schedules on no day of the month.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        db.session.commit()
        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )
        rule = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        ).template.recurrence_rule
        assert rule.starts_on == date(2026, 5, 1)
        reauthor_rule(
            rule,
            replace(
                recurrence_spec(rule),
                weekday_reading=WeekdayReading.FROM_START,
            ),
            calendar_for(rule.user_id),
        )
        db.session.commit()
        assert rule.weekday_reading == "from_start"

        params = loan_loaders.load_loan_params(acct.id)
        params.payment_day = 20
        db.session.commit()
        with auth_client.application.app_context():
            loan_recurrence_sync.sync_loan_payment_start(acct.id)
            db.session.commit()

        db.session.refresh(rule)
        assert rule.starts_on == date(2026, 5, 20)
        assert rule.weekday_reading is None
        assert scheduling_day_of_month(rule) == 20

    def test_a_day_less_rule_keeps_its_pay_period_cadence(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck loan payment is bounded but NOT re-dayed.

        A rule that bills by PAYCHECK has no day-of-month coordinate, so it has
        no contractual due day to keep in step; writing one would re-date every
        generated instance from the period start onto a monthly day. The start
        bound still applies -- it is about the loan's life, not its cadence.

        **This is the one shape whose stored ``starts_on`` is not the
        installment date itself** (plan ledger row **D6**).  A pay-period
        cadence's occurrences are PAYDAYS, so ``resolve`` normalises the
        2026-05-01 first installment onto the payday of the paycheck that hosts
        it.  On this fixture's biweekly schedule from 2026-01-02 that paycheck
        is period 8, 2026-04-24..2026-05-07, so the column holds 2026-04-24 --
        the SAME paycheck the old ``end_date >= start_date`` filter admitted
        first, so nothing about generation moved.
        """
        with app.app_context():
            acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
            template = make_transfer_template(
                db.session, seed_user, to_account=acct,
            )
            db.session.commit()
            assert scheduling_day_of_month(template.recurrence_rule) is None

            loan_recurrence_sync.sync_loan_payment_start(acct.id)

            assert template.recurrence_rule.starts_on == date(2026, 4, 24)
            assert template.recurrence_rule.nominal_day is None
            assert scheduling_day_of_month(template.recurrence_rule) is None

    def test_the_start_bound_survives_a_missing_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """C8e's lesson: the start bound is not scenario-scoped, so it must not
        sit behind the scenario guard.

        The END bound is a fold over the forward plan and genuinely needs a
        baseline scenario; the START bound is ``origination_date`` +
        ``payment_day`` and needs nothing.  Deriving them in one pass makes it
        easy to strand the start behind the end's guard -- which would leave a
        loan configured while the baseline was gone (finding G1: a real,
        repairable state) generating payments from the beginning of time.

        NEGATIVE CONTROL, historical: while ``sync_recurring_payment_bounds``
        still wrote the END bound behind a ``ctx.scenario is None`` return,
        moving the ``_sync_loan_cadence`` call below that return turned this
        red.  Plan step R7d-g deleted the END half and the return with it:
        ``sync_loan_payment_start`` builds no read pass at all, so this now
        pins that it never grows one.  (It was ``_sync_start_date`` until plan
        step R7c-b gave the function the whole cadence to keep, and the
        control named the old symbol for a step after it stopped existing.)
        """
        with app.app_context():
            acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
            template = make_transfer_template(
                db.session, seed_user, to_account=acct,
            )
            db.session.commit()

            # Delete the baseline scenario the loan resolves against.
            db.session.query(Scenario).filter(
                Scenario.id == seed_user["scenario"].id,
            ).delete()
            db.session.commit()

            loan_recurrence_sync.sync_loan_payment_start(acct.id)
            # The pay-period normalisation, as in the day-less test above:
            # the 2026-05-01 installment is billed in period 8's paycheck.
            assert template.recurrence_rule.starts_on == date(2026, 4, 24)
