"""C9a: a recurring loan payment cannot start before the loan does.

``RecurrenceRule.starts_on`` is the opening half of a recurrence's validity
window, derived by ``loan_recurrence_sync.sync_loan_payment_start`` (the
opening-only sync plan step R7d-g left of ``sync_recurring_payment_bounds``)
from the loan's FIRST CONTRACTUAL INSTALLMENT.  It was ``start_date`` until plan
step R7c-b, which made the column the rule's FIRST OCCURRENCE rather than a
bound the occurrences were filtered against (ruling R-R16); for a loan payment
billing on a day of the month the two are the same date, because the first
installment IS the first occurrence.

**What it prevents, measured.** Before this bound, ``create_payment_transfer``
built its rule with no start at all and generated across every materialized pay
period, so a mortgage closing 2026-04-15 got payments due 2026-02-01,
2026-03-01 and 2026-04-01 -- three installments for a loan that did not exist.
Each debited the cash projection immediately ($3,220.92 total on the fixture's
10-period window; production materializes ~52), and each became an FU-5 money
erasure the moment it settled: the fold splits a pre-origination payment against
a ZERO balance, so it books $0.00 principal and routes the whole payment to a
Refund Receivable, then the origination anchor resets the balance over it.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.enums import (
    AcctTypeEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.schemas.validation import end_bound_before_start_message
from app.services import (
    balance_at,
    installment_calendar,
    loan_loaders,
    loan_recurrence_sync,
    transfer_recurrence,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceSpec,
    WeekdayReading,
    reauthor_rule,
    recurrence_spec,
    resolve,
    scheduling_day_of_month,
)
from app.services.generation_schedule import GenerationSchedule
from app.services.balance_at._resolution import (
    contractual_schedule_from_origination,
)
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    derived_span,
    last_covered_day,
    make_loan_payment_template,
    make_transfer_template,
)


# The suite's frozen today is 2026-03-20, so a loan originating 2026-04-15 is
# configured BEFORE it closes -- the shape the bound exists for.
ORIGINATION = date(2026, 4, 15)
PRINCIPAL = Decimal("200000.00")


def _upcoming_mortgage(seed_user, db_session, periods, payment_day=1):
    """A mortgage that has not closed yet (origination after the frozen today)."""
    return create_loan_account(
        seed_user, db_session, name="Closing In April",
        principal=PRINCIPAL, rate=Decimal("0.05000"),
        term=360, origination_date=ORIGINATION, payment_day=payment_day,
        account_type=AcctTypeEnum.MORTGAGE,
    )


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
    def test_it_equals_the_engine_schedule_row_zero(
        self, app, db, seed_user, seed_periods, origination, payment_day,
    ):
        """The DRY proof: the cheap derivation == the engine's own first row.

        ``first_installment_date`` exists so the bound does not have to build a
        360-row schedule (and load a rate feed) to learn one date.  That is only
        legitimate while the two agree, so this pins them against each other
        across shapes rather than asserting the claim in a docstring.  If the
        resolver ever changes its first-payment convention, this goes red.
        """
        with app.app_context():
            acct = create_loan_account(
                seed_user, db.session, name=f"L{origination}{payment_day}",
                principal=PRINCIPAL, rate=Decimal("0.05000"), term=360,
                origination_date=origination, payment_day=payment_day,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            params = loan_loaders.load_loan_params(acct.id)
            rows = contractual_schedule_from_origination(
                params, loan_loaders.load_rate_changes(acct.id),
            )
            assert rows[0].payment_date == installment_calendar.first_installment_date(
                origination, payment_day,
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


class TestNoPaymentGeneratesBeforeTheLoan:
    """The behaviour the bound exists for, on the route that produced the defect."""

    def test_the_route_generates_no_pre_origination_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Every generated installment falls after the loan originates.

        Pre-C9a this generated 3 pre-origination payments out of 4 (due
        2026-02-01 / 03-01 / 04-01 against a 2026-04-15 closing).

        NEGATIVE CONTROL: move the rule's ``starts_on`` back to the
        schedule's opening and this goes red with those three.  (It named a
        ``rule.start_date`` FILTER until plan step R7c-b, and that reading was
        already two steps stale: since R4a nothing filters -- the walk SEEDS at
        the first occurrence, so a pre-origination date is never emitted rather
        than emitted and dropped.)
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert xfers, "the route must still generate the payments that ARE due"
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]
        for xfer in xfers:
            assert xfer.due_date > ORIGINATION

    def test_the_cash_projection_is_unmoved_by_a_loan_that_has_not_closed(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Setting up the payment cannot debit checking before the loan exists.

        The user-visible half of the defect: the phantom installments were
        PROJECTED expenses on the funding account, in periods already past, so
        the checking projection read $3,220.92 low across the window (period 6
        went 1000.00 -> -2220.92) for a mortgage that had not closed.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        db.session.commit()

        with auth_client.application.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.balance_map(checking, bctx)

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        with auth_client.application.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            after = balance_at.balance_map(checking, bctx)

        # Every period whose payments are all pre-origination is untouched; the
        # first real installment (2026-05-01, period 8) is the first debit.
        for period in seed_periods:
            if last_covered_day(period) < date(2026, 5, 1):
                assert after[period.id] == before[period.id], (
                    f"period {derived_span(period).period_index} "
                    f"({period.start_date}..{last_covered_day(period)}) "
                    f"moved before the loan's first installment"
                )

    def test_a_regeneration_cannot_reintroduce_them(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bound holds on the path ``start_period_id`` does NOT.

        ``transfer_recurrence.regenerate_for_template`` passes its own
        ``effective_from``, which suppresses the rule's ``start_period_id``
        entirely -- so a bound expressed there would be silently discarded and
        the phantom installments would come back on the next template edit.
        ``starts_on`` is where the occurrence walk BEGINS instead, which no
        caller can bypass: since plan step R7c-b it is the cadence's first
        occurrence, so there is no earlier date for the walk to emit rather
        than an earlier date it filters out.

        NEGATIVE CONTROL: seed the occurrence walk at anything below
        ``resolved.starts_on`` and this goes red.
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

        with auth_client.application.app_context():
            transfer_recurrence.regenerate_for_template(
                template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(template.user_id), {p.id for p in seed_periods},
                ), seed_user["scenario"].id,
                effective_from=seed_periods[0].start_date,
            )
            db.session.commit()

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .all()
        )
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]

    def test_the_generic_transfers_route_bounds_a_loan_destination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan payment set up through /transfers is bounded too.

        The transfer form offers every active account as a destination, so a
        loan payment can be created here as readily as on the loan page -- but
        this route builds its rule from the FORM, so nothing ever handed it the
        loan's first installment.  Measured unbounded: 3 pre-origination
        installments (2026-02-01 / 03-01 / 04-01), byte-for-byte the defect the
        loan route produced, on a path the loan-side start sync never
        touches because it runs at loan mutations, not transfer creations.

        **The form's own "Starts on" is OVERWRITTEN here, deliberately.**  The
        posted payload carries today (2026-03-20, what a create form opens on),
        and ``settle_first_occurrence`` replaces it with the loan's contract
        -- which is the rule ``LOAN_PAYMENT_BOUND_IS_DERIVED`` states on the
        EDIT path, where the control renders locked so the user is never
        asked for a value the app is going to discard.  What this test pins
        is that the SERVER wins whatever the client posts, which is what
        makes the lock a presentation choice rather than the enforcement.
        Still true after plan step R7d-g-2 (ruling **R-R81**), because this
        loan holds no payment: the definition created IS its standing payment.

        NEGATIVE CONTROL: remove the ``settle_first_occurrence`` call from
        ``transfers.templates._settle_create_references`` and those three come
        back.  It named ``bind_rule_to_loan`` until plan step R7c-b, and that
        control is now FALSE: the derivation moved AHEAD of the rule being
        built (developer ruling 2026-08-15, so nothing is authored and then
        replaced), and the create path's later bind was DELETED at plan step
        R7d-g-2 (ruling **R-R85**) -- it found the date already right for the
        standing payment and overwrote an owner's for a second transfer.  A
        negative control that no longer fires is worse than none -- it
        reports a guard as tested.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        checking = seed_user["account"]
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Debt", item_name="Mortgage",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly = cadence_payload(unit=RecurrenceUnitEnum.MONTH)
        resp = auth_client.post("/transfers", data={
            "name": "Manual Mortgage",
            "from_account_id": str(checking.id),
            "to_account_id": str(acct.id),
            "default_amount": "1200.00",
            "category_id": str(category.id),
            **monthly,
        })
        assert resp.status_code == 302

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert [x.due_date for x in xfers] == [date(2026, 5, 1)]
        assert xfers[0].template.recurrence_rule.starts_on == date(2026, 5, 1)

    def test_a_non_loan_destination_is_left_unbounded(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The derivation is a no-op for every non-loan destination.

        ``settle_first_occurrence`` runs for every create on the generic route
        (and ``bind_rule_to_loan`` did too, unconditionally, until plan step
        R7d-g-2 deleted that call), so neither may overwrite an ordinary
        savings transfer's start -- which would move the user's own schedule
        onto a loan's contract.

        **"Unbounded" is no longer expressible as a NULL** (plan step R7c-b
        made ``starts_on`` ``NOT NULL``), and the honest statement is stronger:
        the rule keeps exactly what the FORM authored.  ``cadence_payload``
        posts what a create form opens on -- today, 2026-03-20 on this suite's
        frozen clock -- so the first monthly occurrence is that date and not
        the loan-derived 2026-05-01.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Sav",
            anchor_balance=Decimal("100.00"),
        )
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Save", item_name="Rainy Day",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly = cadence_payload(unit=RecurrenceUnitEnum.MONTH)
        auth_client.post("/transfers", data={
            "name": "To Savings",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "default_amount": "100.00",
            "category_id": str(category.id),
            **monthly,
        })

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == savings.id)
            .order_by(Transfer.due_date)
            .all()
        )
        assert xfers
        rule = xfers[0].template.recurrence_rule
        assert rule.starts_on == display_today()
        # The form's own date, untouched by the bind: the first occurrence is
        # what the user asked for, not the loan contract of a loan they do not
        # have.
        assert xfers[0].due_date == display_today()

    def test_an_already_originated_loan_generates_normally(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bound must not clip a normal, long-originated loan.

        The real loans originated 2018 and 2023, so their bounds fall far before
        any materialized period and must exclude nothing.  Without this, a fix
        that over-clipped would look green on the upcoming-loan tests alone.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Old Mortgage",
            principal=PRINCIPAL, rate=Decimal("0.05000"), term=360,
            origination_date=date(2018, 12, 1), payment_day=1,
            account_type=AcctTypeEnum.MORTGAGE,
        )
        checking = seed_user["account"]
        db.session.commit()

        auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(checking.id)},
        )

        xfers = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .order_by(Transfer.due_date)
            .all()
        )
        # One installment per month across the seeded window -- nothing clipped.
        # Feb is the first: periods start 2026-01-02, so no seeded period
        # contains the 2026-01-01 installment.  Nothing is clipped by the
        # bound (2019-01-01) -- every installment the window can hold is here.
        assert [x.due_date for x in xfers] == [
            date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ]


class TestTheCreateFormNeverAsksForADateItDiscards:
    """Plan step R7c-b's create-side half of ``LOAN_PAYMENT_BOUND_IS_DERIVED``.

    The edit form has rendered a loan payment's "Starts on" locked since plan
    step R7b-4, because the app derives it.  The CREATE form could not: the
    destination is chosen in the form, so the server does not know at render
    whether the definition will be a loan payment.  It asked anyway, and threw
    the answer away -- measured on the case above, where the form posts today
    and the rule stores the first installment.

    Plan step R7c-b closes it at the door
    (``_loan_destination.settle_first_occurrence``): a loan
    destination's first occurrence is DERIVED before the rule is built, so
    nothing is authored and replaced, and the form's control is locked by
    ``recurrence_form.js`` rather than asked.  The two cases here are the two
    sides of that: what a loan destination does with an unstated start, and
    what everything else does.
    """

    def _post_a_monthly_transfer(self, auth_client, seed_user, db, to_account):
        """POST /transfers with a MONTHLY cadence and NO ``starts_on``.

        What a locked control produces: the field is disabled, so the browser
        sends no key at all -- which is a different submission from sending a
        blank one, and the distinction is the whole of the 2026-08-15 ruling.

        Args:
            auth_client: The signed-in test client.
            seed_user: The owner fixture.
            db: The session fixture.
            to_account: The destination :class:`Account`.

        Returns:
            The Flask response.
        """
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Debt", item_name="Mortgage",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly = cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
            )
        return auth_client.post("/transfers", data={
            "name": f"To {to_account.name}",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(to_account.id),
            "default_amount": "1200.00",
            "category_id": str(category.id),
            **monthly,
        })

    def test_a_loan_destination_derives_the_start_it_was_not_given(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """No ``starts_on`` posted, and the loan's own contract supplies it.

        NEGATIVE CONTROL: drop the ``params is not None`` branch from
        ``settle_first_occurrence`` and this fails on the refusal instead --
        the submission a locked control produces would be unsavable.
        """
        acct = _upcoming_mortgage(seed_user, db.session, seed_periods)
        db.session.commit()

        resp = self._post_a_monthly_transfer(
            auth_client, seed_user, db, acct,
        )
        assert resp.status_code == 302

        xfer = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == acct.id)
            .first()
        )
        # One month after the 2026-04-15 closing, on payment day 1 -- the same
        # date the loan page's own create route writes.
        assert xfer.template.recurrence_rule.starts_on == date(2026, 5, 1)

    def test_a_non_loan_destination_still_has_to_state_one(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The requirement did not lapse, it moved to where it can be judged.

        ``TransferTemplateCreateSchema`` stopped requiring a first occurrence
        because a schema never learns which accounts are loans.  For every
        other destination the rule is unchanged, and it is a MONEY rule: an
        unstated start used to resolve to the schedule's opening, and the
        create routes generate over every period the owner has, so a $2,000.00
        rent wrote five backdated rows into pay periods that had already
        closed.

        NEGATIVE CONTROL: delete the refusal arm of
        ``settle_first_occurrence`` and this saves instead of redirecting.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Sav",
            anchor_balance=Decimal("100.00"),
        )
        db.session.commit()

        resp = self._post_a_monthly_transfer(
            auth_client, seed_user, db, savings,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers/new")
        assert not (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == savings.id)
            .all()
        ), "a submission naming no first occurrence must persist nothing"


class TestAMonthEndLoanKeepsItsMonthEnd:
    """``loan_cadence_start`` asks the READER's question (plan step R7c-b).

    ``nominal_day`` is what stops a servicer's day-31 payment decaying to the
    30th forever when the first installment lands in a 30-day month.  Whether a
    cadence can carry one is a property of the UNIT --
    :func:`~app.services.recurrence.has_day_of_month_coordinate`, which
    :func:`~app.services.recurrence.offerable_nominal_days` and
    :attr:`ResolvedRecurrence.day_of_month` both read.

    The producer asked a DIFFERENT question: ``fires_on_day_of_month``, which
    answers whether a generated ROW is dated from a day of the month (it read
    an ANCHOR FAMILY router until plan step R8-a, which deleted it).  The two agree everywhere except ``Monthly First``, whose
    occurrences ARE days of the month even though its anchor is a paycheck --
    so under that placement the producer recorded no nominal day and the reader
    then answered the CLAMPED day.
    """

    #: ``(label, placement)``: both placements a MONTH-unit loan payment can
    #: carry.  Swept rather than sampled because the defect was visible on
    #: exactly one of them, and a case fixed on the wrong one passes.
    _PLACEMENTS = (
        ("the paycheck covering the date", PeriodPlacementEnum.CONTAINING_DATE),
        (
            "the first paycheck on or after",
            PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
        ),
    )

    @pytest.mark.parametrize("label,placement", _PLACEMENTS)
    def test_a_day_31_payment_records_its_nominal_day(
        self, app, seed_user, db, label, placement,  # pylint: disable=unused-argument
    ):
        """Originate 2026-03-10 with ``payment_day`` 31: April clamps it to 30.

        ``first_installment_date`` answers the ``payment_day`` of the month
        AFTER origination, so 2026-04-30 -- April's last day, and one the month
        was too short to hold 31 on.  The pair must therefore be
        ``(2026-04-30, 31)``, which is what keeps May's occurrence on the 31st.

        Under ``Monthly First`` the producer used to answer ``nominal_day
        None`` here, and the reader then said day 30 -- moving the FUNDING
        PAYCHECK a period early in every month whose schedule puts a payday on
        the 30th, on a mortgage-sized debit, for the life of the loan.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            assert start.starts_on == date(2026, 4, 30), label
            assert start.nominal_day == 31, label

    @pytest.mark.parametrize("label,placement", _PLACEMENTS)
    def test_the_reader_then_answers_the_day_the_contract_names(
        self, app, seed_user, db, label, placement,  # pylint: disable=unused-argument
    ):
        """The half that makes the pair matter: what the walk reads back.

        Asserting only the stored pair would leave this passing against a
        reader that ignored it.  ``ResolvedRecurrence.day_of_month`` is the ONE
        place ``starts_on``'s day and ``nominal_day`` are joined, and it is
        what the occurrence walk clamps into each month.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )
            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            resolved = resolve(
                RecurrenceSpec(
                    user_id=seed_user["user"].id,
                    unit=RecurrenceUnitEnum.MONTH,
                    placement=placement,
                    starts_on=start.starts_on,
                    nominal_day=start.nominal_day,
                ),
                calendar_for(seed_user["user"].id),
            )

            assert resolved.day_of_month == 31, label

    def test_a_paycheck_cadence_records_NO_nominal_day(
        self, app, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The control: the drop is conditional on the unit, not general.

        A loan payment that bills by PAYCHECK has no day-of-month coordinate at
        all -- ``resolve`` normalises the installment onto the paycheck hosting
        it -- so there is no contractual day to keep, and recording one would
        be a pair ``RecurrenceSpec`` refuses at construction.  Without this arm
        a producer that recorded ``payment_day`` unconditionally would pass
        every case above.
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=31,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.PERIOD, params,
            )

            assert start.nominal_day is None

    def test_a_day_the_installment_month_HELD_records_nothing(
        self, app, seed_user, db,  # pylint: disable=unused-argument
    ):
        """The second control: a day no month clamped is not a nominal day.

        ``payment_day`` 15 originating 2026-03-10 first bills 2026-04-15, which
        April holds -- so the date already says which day the rule fires on and
        a nominal day beside it would be the second representation ruling
        R-R16 removes (and one ``ck_recurrence_rules_nominal_day`` refuses).
        """
        with app.app_context():
            params = SimpleNamespace(
                origination_date=date(2026, 3, 10),
                payment_day=15,
                account_id=1,
            )

            start = loan_recurrence_sync.loan_cadence_start(
                RecurrenceUnitEnum.MONTH, params,
            )

            assert start.starts_on == date(2026, 4, 15)
            assert start.nominal_day is None


class TestALoanCreateMayNotStopBeforeTheDerivedStart:
    """A SECOND transfer's window, at the CREATE door, since ruling R-R81.

    Until plan step R7d-g-2 this class graded the route-side comparison the
    schema could not make: ``require_end_bound_after_start`` early-returns
    when ``starts_on`` is absent, a loan destination's row was locked and
    posted nothing, and the route DERIVED the start after validation -- so a
    past "Ends on" reached the write door beside a start it had never been
    compared to, and ``_refuse_bound_before_derived_start`` caught it.

    **MOVED BY RULING (R-R81)**: a second transfer's start is its OWNER's,
    the row is open and posts a date, and the schema's own comparison grades
    the pair; that route-side refusal is deleted with the derivation it
    served.  Both cases still create a SECOND transfer (the loan holds its
    standing payment first, so the stop is not refused outright, ruling
    **R-R60**), and what they pin now is that the window is graded against
    the TYPED start and the stop is stored beside it.
    """

    def _paid_loan(self, seed_user, db):
        """Return a COMMITTED loan already holding its standing payment.

        Args:
            seed_user: The owner fixture.
            db: The session fixture.

        Returns:
            The loan :class:`~app.models.account.Account`.
        """
        loan = create_loan_account(
            seed_user, db.session, origination_date=ORIGINATION,
            principal=PRINCIPAL, payment_day=1,
        )
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        return loan

    #: The start the owner types for the second transfer: after the loan's
    #: origination (2026-04-15) and its first installment (2026-05-01), so
    #: nothing but the window rule can refuse the pair.
    _TYPED_START = date(2026, 5, 15)

    def _post_a_bounded_monthly_transfer(
        self, auth_client, seed_user, db, to_account, ends_on,
    ):
        """POST /transfers with a MONTHLY cadence, the typed start, and *ends_on*.

        Args:
            auth_client: The signed-in test client.
            seed_user: The owner fixture.
            db: The session fixture.
            to_account: The destination :class:`Account`.
            ends_on: The closing date to state.

        Returns:
            The Flask response.
        """
        category = Category(
            user_id=seed_user["user"].id,
            group_name="Debt", item_name="Mortgage bound",
        )
        db.session.add(category)
        db.session.commit()

        with auth_client.application.app_context():
            monthly = cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=self._TYPED_START,
            )
        return auth_client.post("/transfers", data={
            "name": f"To {to_account.name} bounded",
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(to_account.id),
            "default_amount": "1200.00",
            "category_id": str(category.id),
            "recurrence_end_mode": "on_date",
            "end_date": ends_on.isoformat(),
            **monthly,
        })

    def test_an_end_before_the_typed_start_is_refused(
        self, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """A past "Ends on" beside the owner's start, refused by the schema.

        An end of 2026-01-01 is before the typed 2026-05-15; the rule such a
        submission would author names no occurrence at all.  The message is
        asserted against the TYPED start, not the loan's first installment
        (2026-05-01): a loan create has more than one refusal that redirects
        to the same place, and only this one names the window.
        """
        loan = self._paid_loan(seed_user, db)
        rows_before = (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == loan.id)
            .count()
        )

        resp = self._post_a_bounded_monthly_transfer(
            auth_client, seed_user, db, loan, date(2026, 1, 1),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers/new")
        with auth_client.session_transaction() as sess:
            flashed = [message for _category, message in sess.get("_flashes", [])]
        assert end_bound_before_start_message(
            date(2026, 1, 1), self._TYPED_START,
        ) in flashed, flashed
        assert (
            db.session.query(Transfer)
            .filter(Transfer.to_account_id == loan.id)
            .count()
        ) == rows_before, (
            "a submission stating an impossible window must persist nothing"
        )

    def test_an_end_AFTER_it_still_saves(
        self, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The control, and it is what stops the door refusing every loan create.

        Without this arm a comparison inverted the wrong way -- or one that
        refused whenever a bound was stated at all -- would pass the refusal
        above while making a bounded second transfer into a loan uncreatable.
        The stored bound is read back: a redirect alone cannot tell a saved
        stop from a dropped one.
        """
        loan = self._paid_loan(seed_user, db)

        resp = self._post_a_bounded_monthly_transfer(
            auth_client, seed_user, db, loan, date(2030, 1, 1),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        second = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=loan.id, name=f"To {loan.name} bounded")
            .one()
        )
        assert second.recurrence_rule.end_date == date(2030, 1, 1)
        assert second.recurrence_rule.starts_on == self._TYPED_START, (
            "a second transfer's start is its owner's (R-R81)"
        )
