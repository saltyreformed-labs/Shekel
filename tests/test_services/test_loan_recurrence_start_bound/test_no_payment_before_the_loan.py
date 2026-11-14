"""No payment generates before the loan exists, and the create form never asks for a date it
discards.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.models.category import Category
from app.models.transfer import Transfer
from app.services import balance_at, transfer_recurrence
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    derived_span,
    last_covered_day,
)

from ._helpers import ORIGINATION, PRINCIPAL, _upcoming_mortgage


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
            "amount": "1200.00",
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
            "amount": "100.00",
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
            "amount": "1200.00",
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
