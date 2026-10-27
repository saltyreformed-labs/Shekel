"""The loan payment feed's DATE half: ``app.services.loan_ledger._installments``.

Plan step **balance:X-bl-2a** (finding **N-432**).  Two producers live here and
they are graded on different grounds:

* :func:`~app.services.amortization_engine.schedule_dates` is pure date arithmetic, so
  it is pinned against HAND-COMPUTED slots.  Two of the cases below are the ones
  ``TestPreparePaymentsForEngine`` already states through the public door
  (``test_biweekly_redistribution``, ``test_december_to_january_rollover``);
  those are deliberately NOT moved -- they grade that
  ``prepare_payments_for_engine`` still APPLIES the rule, which is a different
  claim from the rule being right, and both claims need a test.  What is new
  here is the arithmetic no case reached before: a cascade past two allocated
  months, and the day clamp a ``payment_day`` of 31 needs in February.
* :func:`~app.services.loan_ledger.payment_installments` is a loader, so it is
  graded against the set it must not narrow.  The load-bearing case is
  :meth:`TestPaymentInstallments.test_the_two_halves_partition_the_transfers_into_the_loan`:
  the producer reads the SETTLED half and the PROJECTED half and unions them,
  which is only equal to "every live, non-excluded transfer into the loan,
  once" while those two halves exhaust the non-excluded band.  That is a claim
  about ``ref.statuses``, not about this code, so it is asserted over a loan
  carrying one transfer in EVERY status rather than assumed -- a sixth status
  would fail it.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services.amortization_engine import schedule_dates, slotted_dates
from app.services.loan_ledger import payment_installments
from app.models.transfer import Transfer
from app.services.loan_loaders import _legs
from app.utils.balance_predicates import balance_excluded_status_ids
from app.services.loan_payment_service import get_payment_history, load_loan_context
from app.services import status_seam
from app.services.cash_ledger import derived_amount_basis
from app.services.transfer_legs import TransferLeg
from app.services.transfer_service import (
    TransferSpec,
    create_transfer,
    settle_transfer,
)
from tests._test_helpers import (
    an_entered_day,
    create_loan_account,
    generate_transfer_of,
    loan_params_for,
    make_transfer_template,
    on_both_sides,
)
from app.models.amount_ownership import AmountOwnership

#: The ``payment_day`` of the loans this file builds.
_PAYMENT_DAY = 1


def _make_loan(seed_user):
    """Build a mortgage whose payments are due on the 1st."""
    return create_loan_account(
        seed_user, db.session, name="Installment Mortgage",
        principal=Decimal("250000.00"), rate=Decimal("0.06000"), term=360,
        origination_date=date(2024, 1, 1),
    )


def _transfer_to_loan(
    seed_user, loan, period, amount, status_enum, settled_on=None,
):
    """Create a transfer into *loan*, through the write door.

    Through :func:`~app.services.transfer_service.create_transfer` rather than
    by inserting rows, so the transfer this file reads is the one production
    writes: one row since plan step balance:X-bi-6-4d-2 (ruling **R-BAL258**),
    where it came with the pair of shadows this file read until then.

    **A SETTLED one is created Projected, then settled** through the settle
    verb, because the create makes no settled transfer (ruling **R-BAL379**);
    it was created in its settled status until then, and the settle stores
    what that create stored: the transfer's own figure on both sides, on
    *settled_on* (``entered``) or, with none, on the owner's today.
    **Received is then laid on BARE**: the verb settles a transfer with
    ``Done`` and no transfer door writes ``Received``, which the
    every-status case below still needs as a member of ``ref.statuses``
    (approved inside ruling R-BAL379's question: the case builds its state
    another way, its expected answer unchanged).  It is laid over the SETTLED
    transfer, so both sides keep their dated records, as the band rule
    requires; the bare lay is the technique
    ``test_routes/test_transfers/_helpers._received_transfer_of`` uses, which
    lays Received over a Projected transfer holding none.  The lay is one more
    write, so the transfer's version counter is the one stored fact that
    differs from what that create wrote (measured by 4d-4a's review).  A day
    stated beside an unsettled status is refused before any row exists, by
    the seam's own refusal, as that create refused it.
    """
    status_id = ref_cache.status_id(status_enum)
    settled = status_id in _settled_ids()
    stated = None if settled_on is None else an_entered_day(settled_on)
    status_seam.reject_settle_day_without_settled_status(status_id, stated)
    transfer = create_transfer(
        TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=loan.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=(
                ref_cache.status_id(StatusEnum.PROJECTED) if settled
                else status_id
            ),
            category_id=seed_user["categories"]["Rent"].id,
        ),
    )
    if not settled:
        return transfer
    settle_transfer(
        transfer.id, seed_user["user"].id,
        side_days=(
            () if stated is None
            else on_both_sides(seed_user["account"].id, loan.id, stated)
        ),
    )
    if transfer.status_id != status_id:
        transfer.status_id = status_id
        db.session.flush()
    return transfer


#: How many payments the eager-load controls build.  More than one, because the
#: whole distinction they grade is ONE query for the set against one per row.
_SEAM_SHADOWS = 4
#: How many of them are PROJECTED, each generated by a DEFINITION OF ITS OWN.
#: Since plan step balance:X-bi-6a the projected half is read off the parent
#: transfers and priced through their definitions, and a definition is
#: identity-mapped: two projected payments of ONE template lazy-load it once,
#: which is indistinguishable from an eager load.  Two templates make a lazy
#: walk two statements against an eager load's one.
_SEAM_PROJECTED = 2


def _seam_shadows(seed_user, loan, seed_periods):
    """Create :data:`_SEAM_SHADOWS` payments on *loan*, settled and projected.

    A mixed set, so both halves of the partition are non-empty and the eager
    load has to cover rows from each: two settled AD-HOC payments (the record
    half, valued from what they recorded) and :data:`_SEAM_PROJECTED` projected
    payments each GENERATED from its own definition, so the plan half has a
    pricing chain to walk (``Transfer.template``) and a table to be measured
    on.  An ad-hoc projected transfer owns its figure and reaches no table
    past ``budget.transfers``, which is why the first draft of this fixture
    could not grade the leg path at all.
    """
    for index in range(_SEAM_SHADOWS - _SEAM_PROJECTED):
        period = seed_periods[index + 1]
        _transfer_to_loan(
            seed_user, loan, period, Decimal("1500.00"),
            status_enum=StatusEnum.DONE, settled_on=period.start_date,
        )
    for index in range(_SEAM_PROJECTED):
        template = make_transfer_template(
            db.session, seed_user, loan, amount="1500.00",
        )
        # The helper names every definition alike and the name is unique per
        # owner, so each is renamed before the next is built.
        template.name = f"Loan payment definition {index}"
        db.session.flush()
        generate_transfer_of(
            template, seed_periods[_SEAM_SHADOWS - _SEAM_PROJECTED + index + 1],
        )
    db.session.commit()


def _reads_of(table, statements):
    """Return how many of *statements* read *table*.

    The COUNT is the measurement: one statement is an eager load of the whole
    set, N is the per-row walk, zero is no load at all.  Three different facts
    that "did any statement mention it" cannot tell apart.
    """
    return sum(1 for statement in statements if table in statement)


class TestScheduleDates:
    """The monthly slot each payment consumes -- hand-computed."""

    def test_an_uncontested_payment_keeps_its_own_date(self):
        """No collision means no invention: each due date comes back verbatim.

        Three payments due 2026-02-01, 2026-03-01 and 2026-04-01 occupy three
        distinct months, so nothing is reassigned.  This is the shape BOTH live
        loans are in -- 58 shadows, no two sharing a due month (dev, 2026-09-09)
        -- so it is the case that must not invent a date, not merely the easy
        one.
        """
        due = [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == due

    def test_two_payments_due_one_month_take_consecutive_slots(self):
        """The second of two payments due 2026-02-01 moves to 2026-03-01.

        With ``payment_day=1``, pay periods starting 2026-01-02 and 2026-01-16
        both fall before 2026-02-01, so both satisfy the February installment.
        The first keeps February; the second takes the next free month, March,
        on the contractual day.  A monthly engine that saw both in February
        would double-count that month and leave March empty.
        """
        due = [date(2026, 2, 1), date(2026, 2, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 2, 1), date(2026, 3, 1),
        ]

    def test_a_collision_rolls_over_the_year_boundary(self):
        """Two payments due 2026-12-01: the second takes 2027-01-01.

        The month walk carries into the next YEAR rather than wrapping to month
        13.  **The due month must be DECEMBER for this case to grade that**: an
        earlier draft used two payments due 2027-01-01, whose walk runs January
        to February and never enters the rollover branch at all -- spliced with
        that branch replaced by a raise, the draft still passed while the
        December case raised.  The rollover moved out of the old inline
        ``if m > 12`` into ``amortization_engine.advance_to_next_payment_date``
        at plan step **balance:X-bl-2a**, and since plan step recurrence:R25
        it is the loan calendar's month step
        (:func:`~app.services.installment_calendar.due_in_following_month`),
        whose year carry is month-ordinal arithmetic rather than a branch;
        this case still grades the slot walk across the year.
        """
        due = [date(2026, 12, 1), date(2026, 12, 1)]

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 12, 1), date(2027, 1, 1),
        ]

    def test_a_cascade_walks_past_every_allocated_month(self):
        """Four payments due 2026-02-01 fill February, March, April and May.

        The third cannot take March -- the second already has it -- so it walks
        to April, and the fourth to May.  Cascading collisions are not expected
        (at most one extra payment a month, ~2x/year), and this is the case that
        proves the walk does not stop at the first step.
        """
        due = [date(2026, 2, 1)] * 4

        assert schedule_dates(due, _PAYMENT_DAY) == [
            date(2026, 2, 1), date(2026, 3, 1),
            date(2026, 4, 1), date(2026, 5, 1),
        ]

    def test_an_invented_slot_clamps_the_day_to_the_month(self):
        """A ``payment_day`` of 31 colliding into February lands on the 28th.

        Two payments due 2026-01-31; the second is pushed into February, which
        has 28 days in 2026.  ``date(2026, 2, 31)`` does not exist, so the day
        is clamped -- through the loan calendar's one month step
        (:func:`~app.services.installment_calendar.due_in_following_month`,
        plan step recurrence:R25) over the clamp a forward projection lands
        its dates with, so a loan due on the 31st reads the same February date
        wherever it is asked.
        """
        due = [date(2026, 1, 31), date(2026, 1, 31)]

        assert schedule_dates(due, 31) == [
            date(2026, 1, 31), date(2026, 2, 28),
        ]

    def test_no_payments_means_no_slots(self):
        """An empty feed returns an empty slot list, not an error."""
        assert schedule_dates([], _PAYMENT_DAY) == []


class TestPaymentInstallments:
    """The loader: which rows, in what order, carrying which dates."""

    def test_the_two_halves_partition_the_transfers_into_the_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """The producer answers EVERY live, non-excluded transfer into the loan, ONCE.

        **The control this producer's whole shape rests on.**  It reads the
        SETTLED half and the PROJECTED half and unions them, which equals
        "every live, non-excluded transfer into the loan" only while those two
        halves exhaust the band the balance-excluded statuses leave standing.
        That is a claim about the ``ref.statuses`` seed -- exactly the kind of
        set defined by subtraction that nobody re-censuses -- so a transfer is
        created in EVERY status and the two are compared directly.  Seeding a
        sixth status that is neither projected nor settled nor
        balance-excluded would fail here rather than silently dropping its
        payments from every loan schedule.

        **Re-expressed at plan step balance:X-bi-6-4b** (developer approval
        under rule 5, 2026-09-24).  It compared the feed against
        ``query_shadow_income``'s shadow rows, a query that step deleted when
        both halves became legs of transfers; the claim is unchanged and is
        now stated over the transfers themselves.  The comparison is of
        SORTED LISTS, not sets, so a payment answered twice fails it -- the
        "exactly once" the claim always made.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            for index, status in enumerate(StatusEnum):
                _transfer_to_loan(
                    seed_user, loan, seed_periods[index], Decimal("1500.00"),
                    status_enum=status,
                    settled_on=(
                        seed_periods[index].start_date
                        if ref_cache.status_id(status) in _settled_ids()
                        else None
                    ),
                )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # The premise, read back after the commit: one transfer in each
            # status.  Received is laid on bare after the settle verb (the
            # helper says why), and a lay that stopped landing would leave a
            # second Paid transfer where the Received one belongs: the
            # comparison below would still pass, and so would a loader that
            # drops Received rows from its settled half (measured by 4d-4a's
            # review: that loader mutant fails this case only while the lay
            # lands).
            assert sorted(
                transfer.status_id
                for transfer in db.session.query(Transfer).filter(
                    Transfer.to_account_id == loan.id,
                )
            ) == sorted(ref_cache.status_id(status) for status in StatusEnum)

            queried = sorted(
                transfer.id
                for transfer in db.session.query(Transfer).filter(
                    Transfer.to_account_id == loan.id,
                    Transfer.scenario_id == scenario_id,
                    Transfer.is_deleted.is_(False),
                    ~Transfer.status_id.in_(balance_excluded_status_ids()),
                )
            )
            produced = sorted(
                installment.source.transfer.id
                for installment in payment_installments(
                    loan.id, scenario_id, loan_params_for(db.session, loan.id),
                    options=(), leg_options=(),
                )
            )

            # Non-vacuous in both directions: the query must have kept some
            # transfers and dropped some, or "the two agree" says nothing.
            assert queried, "no transfer into the loan survived to partition"
            assert len(queried) < len(list(StatusEnum)), (
                "no status was excluded -- this case would pass over a "
                "producer that simply read every transfer"
            )
            assert produced == queried

    def test_settled_ness_comes_from_which_set_a_row_arrived_in(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled shadow carries its cash day; a projected one carries none.

        The classification this producer exists to make single: the settled row
        is the one the fold's own loader returned, and its ``settled_on`` is the
        day :func:`~app.services.loan_ledger.payment_visible_on` reads off it --
        the same day the posted ledger counts that payment's principal from.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            settled_day = seed_periods[1].start_date
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE, settled_on=settled_day,
            )
            _transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )

            assert [i.dates.settled_on for i in installments] == [
                settled_day, None,
            ]
            assert [i.dates.period_start for i in installments] == [
                seed_periods[1].start_date, seed_periods[2].start_date,
            ]

    def test_a_projected_row_holding_a_stale_settle_day_reports_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected payment is NOT confirmed: it is its parent's leg, carrying no day.

        Only a bypass of the status seam could produce that row: a settle DAY
        on the payment's loan-side shadow while it was Projected, which no
        ``CHECK`` could refuse because the settled predicate lives in
        ``ref.statuses`` and a constraint cannot join.  The producer had to
        report the payment as not-yet-happened, and since plan step
        balance:X-bi-6a that was STRUCTURAL rather than a branch: a projected
        payment arrives as the leg of its PARENT transfer, which carries no
        settle day of its own, so the shadow's day was unreachable from the
        producer.  Reporting it would tell the replay an installment was paid
        that the posted ledger has no entry for.

        **Re-expressed at plan step balance:X-bi-6-4d-2** (ruling **R-BAL167**
        class 2): a transfer is one row since that step (ruling **R-BAL258**),
        so the row the bypass wrote the day onto -- the loan-side twin --
        could not be stored, and the case asserted
        ``ck_transactions_names_no_transfer`` refusing it until plan step
        balance:X-bi-6-5 dropped the column the twin named its transfer by:
        no statement can write it now, and that refusal went with the column
        (ruling plan#1850).  A side's day lives on its payment record since
        plan step balance:X-bi-6-4d-2, where a DATED record under a Projected
        transfer is money that moved (ruling **R-BAL140**) and the
        status-band rule refuses it at commit: a different state, graded in
        ``test_loan_settled_legs``.  What stands is the producer's answer on
        the Projected payment itself -- the leg of its parent, carrying no
        day.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            transfer = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()
            transfer_id = transfer.id

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )

            assert len(installments) == 1
            assert isinstance(installments[0].source, TransferLeg), (
                "the projected payment did not arrive as the leg of its "
                "parent -- a day on any other row could be READ, not "
                "structurally unreachable"
            )
            assert installments[0].source.transfer.id == transfer_id
            assert installments[0].dates.settled_on is None

    def test_two_payments_in_one_period_order_by_id(
        self, app, db, seed_user, seed_periods,
    ):
        """Same pay period: the earlier PARENT comes first, whichever SET it is in.

        The order reaches exactly one decision --
        :func:`~app.services.amortization_engine.schedule_dates`, where it settles which
        of two payments colliding on a due month keeps it -- and until plan step
        **balance:X-bl-2a** the feed ordered by ``pay_period.start_date`` alone,
        leaving that to whatever order the database returned.  Since plan step
        **balance:X-bi-6a** the tie-break is the parent transfer's id, the one
        key both relations carry.

        **The projected row is created FIRST and the settled row SECOND, and
        that is what gives this case teeth.**  The producer reads the settled
        set before the projected one, so the merged list starts out
        settled-first; a sort on ``start_date`` alone is STABLE and would keep
        the later settled row in front.  Only the tie-break puts them in the
        order the feed's chronology means.

        **Read in TRANSFER ids since plan step balance:X-bi-6-4d-2** (ruling
        **R-BAL167** class 1): it compared the loan-side shadows' ids, which
        followed their parents' (the service wrote a parent before its
        shadows), until that step made a transfer one row.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            earlier = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            later = _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1600.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()
            assert earlier.id < later.id, (
                "the projected row must carry the LOWER id, or the settled-set-"
                "first read order would already put the rows right"
            )

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )

            assert [i.source.transfer.id for i in installments] == [
                earlier.id, later.id,
            ]

    def test_a_settled_and_a_projected_row_interleave_by_period(
        self, app, db, seed_user, seed_periods,
    ):
        """The two sets merge into ONE chronology, not settled-then-projected.

        A projected payment in an EARLIER period than a settled one must sort
        first: the merged order is the feed's chronology, and it is what decides
        a due-month collision.  Concatenating the settled list and the projected
        list without re-sorting would put the later settled payment first and
        hand it a month the earlier plan should have kept.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[3].start_date,
            )
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            installments = payment_installments(
                loan.id, seed_user["scenario"].id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )

            assert [i.dates.period_start for i in installments] == [
                seed_periods[1].start_date, seed_periods[3].start_date,
            ]
            assert [
                i.dates.settled_on is None for i in installments
            ] == [True, False]

    def test_a_loan_with_no_payments_produces_nothing(
        self, app, db, seed_user,
    ):
        """No shadows means an empty feed, not an error."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()

            assert payment_installments(
                loan.id, seed_user["scenario"].id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            ) == []


class TestBothFeedsReachOneSlotAssignment:
    """The PRICED feed and the AMOUNT-FREE feed resolve a collision IDENTICALLY.

    **The claim ``slotted_dates`` exists for, and until plan step
    balance:X-bl-2b nothing graded it.**  Ruling **R-BAL7** put the
    biweekly-collision assignment in the pure engine so BOTH of a loan's feeds
    reach one producer -- and the reason is not tidiness: a slot the two do not
    agree on is a month the replay CONSUMES and the forward override PLANS
    separately, which silently drops a planned payment.  The two producers were
    pinned only through their own halves (``TestPreparePaymentsForEngine`` on the
    priced one, :class:`TestScheduleDates` on the arithmetic); nothing ran a
    collision through both and compared, so an edit that slotted one feed and not
    the other would have gone green.

    The case is the smallest collision there is: two payments in ONE pay period,
    so both satisfy the same monthly installment and the second must be pushed
    to the next free month.  **The non-vacuity arms are asserted first** -- that
    the two payments really do share a due month, and that the assignment really
    does move one of them -- because an equality between two feeds that both
    happened to leave the dates alone would pass over the defect this exists to
    catch.
    """

    def test_a_collision_gets_the_same_slots_from_both_feeds(
        self, app, db, seed_user, seed_periods,
    ):
        """Both feeds report the same due dates, and the facts are untouched."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            for amount in (Decimal("1500.00"), Decimal("1600.00")):
                _transfer_to_loan(
                    seed_user, loan, seed_periods[1], amount,
                    status_enum=StatusEnum.DONE,
                    settled_on=seed_periods[1].start_date,
                )
            db.session.commit()

            scenario_id = seed_user["scenario"].id
            installments = payment_installments(
                loan.id, scenario_id, loan_params_for(db.session, loan.id),
                options=(), leg_options=(),
            )
            amount_free = slotted_dates(
                [installment.dates for installment in installments],
                _PAYMENT_DAY,
            )
            priced = load_loan_context(
                loan.id,
                derived_amount_basis(seed_user["user"].id, scenario_id),
                loan_params_for(db.session, loan.id),
            ).payments

            # Non-vacuity 1: the two payments really do collide.
            assert len(installments) == 2
            assert (
                installments[0].dates.due_date == installments[1].dates.due_date
            ), "the fixture built no collision -- this case grades nothing"
            # Non-vacuity 2: the assignment really does move the loser.
            assert amount_free[1].due_date != installments[1].dates.due_date, (
                "no slot was invented -- an assignment that did nothing would "
                "make the equality below hold trivially"
            )

            # The claim: ONE assignment, reached by both feeds.
            assert [dates.due_date for dates in amount_free] == [
                payment.dates.due_date for payment in priced
            ]
            # ...and only the installment moved on either side.
            assert [dates.period_start for dates in amount_free] == [
                payment.dates.period_start for payment in priced
            ]
            assert [dates.settled_on for dates in amount_free] == [
                payment.dates.settled_on for payment in priced
            ]


def _settled_ids():
    """Return the settled status ids -- the band the producer's settled set is.

    Read through the shared accessor rather than named here, so a case that
    seeds "one shadow per status" cannot disagree with the producer about which
    of them need a settle day.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred so the ref cache is warm
    # inside the app context each case builds.
    # pylint: disable=import-outside-toplevel
    from app.utils.balance_predicates import settled_status_ids
    return settled_status_ids()


@contextmanager
def _statements_issued():
    """Record every SQL statement the engine executes inside the block.

    At the Engine level rather than a session's, so it sees what the PROCESS
    issued whichever session issued it -- the same probe
    ``test_loan_payment_service`` uses.  It cannot see ``BEGIN`` / ``COMMIT`` /
    ``ROLLBACK``, which psycopg issues through the connection, so the
    assertions below name TABLES rather than counting a total.

    Yields:
        The list of normalised SQL strings, appended to as the block runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


#: The amount model's chain, named by the table it reads rather than by the
#: option that asks for it, so the control grades what reached the DATABASE
#: and not what a caller passed.
#:
#: **The count matters, not the presence, and a mutation measured that.**  A
#: LAZY load reads the same table -- once per row -- so "some statement named
#: it" passes over a caller that states no options at all: spliced that way, an
#: earlier draft of the priced-feed control below stayed GREEN.  An eager
#: ``selectinload`` issues ONE query for the whole set, so the fixtures build
#: several payments and the assertions count.
#:
#: **It was ``budget.transfers`` until plan step balance:X-bi-6a**, the table
#: ``Transaction.transfer`` walked to price a projected SHADOW -- the one true
#: N+1 in ``pricing_load_options``.  That step made ``budget.transfers`` the
#: PLAN relation itself: the projected half of the feed is loaded FROM it, once,
#: whatever a caller states, so a count against it grades the loader's own
#: statement and not the caller's.  The pricing chain now starts one table
#: past it, at the parent's DEFINITION (``Transfer.template``), which is
#: reached only for a template-generated parent -- the fixture builds two, on
#: two definitions, for the reason :data:`_SEAM_PROJECTED` states.  The tables
#: past THAT (``template_amount_versions``, ``loan_payment_settings``) are
#: still not asserted: a chained ``selectinload`` issues no query when the step
#: before it found nothing, and a control that names a table its fixture cannot
#: reach grades the fixture.
_PRICING_TABLE = "budget.transfer_templates"
#: The PLAN relation, read exactly once PER HALF by either feed -- each half's
#: own statement (the projected half's since plan step balance:X-bi-6a, the
#: settled half's since balance:X-bi-6-4b), asserted so a payment that arrived
#: through a per-row walk would be seen.  *It was once, for the projected half
#: alone, until the settled half moved onto transfers (rule-5 re-expression,
#: developer approval 2026-09-24).*
_PLAN_TABLE = "budget.transfers"


class TestTheCallerStatesItsOwnEagerLoad:
    """A load that costs a round trip is the CONSUMER's declaration, not the loader's.

    Plan step **balance:X-bl-2a**.  ``query_shadow_income`` baked
    ``pricing_load_options()`` into itself, so a ROW loader decided what its
    callers would traverse -- and a producer cannot know that.  Three of its six
    consumers never touched the chain they paid five statements for; finding
    **N-296** is the same defect inverted, seven batch callers reaching a loader
    without it and paying a query per definition.

    **The pair below is DISJOINT on purpose.**  One case asserts the date feed
    reads no pricing table, the other that the priced feed reads them all.  A
    single case in either direction would pass over the wrong fix: dropping the
    options everywhere satisfies the first, and restoring them to the loader
    satisfies the second.
    """

    def test_the_date_feed_reads_no_pricing_table(
        self, app, db, seed_user, seed_periods,
    ):
        """``payment_installments(options=(), leg_options=())`` prices nothing.

        The whole point of the seam: the schedule replay's reference reads three
        dates per payment and no figure, so it must not pay for -- or be able to
        fail on -- the tier that prices one.  The plan relation itself is read
        TWICE -- each half's own load (see ``_PLAN_TABLE``), not a pricing
        walk.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _seam_shadows(seed_user, loan, seed_periods)
            db.session.expire_all()

            # Loaded before the window, so the reads counted are the feed's.
            params = loan_params_for(db.session, loan.id)
            with _statements_issued() as statements:
                installments = payment_installments(
                    loan.id, seed_user["scenario"].id, params,
                    options=(), leg_options=(),
                )

            assert len(installments) == _SEAM_SHADOWS, (
                "the feed came back short -- a control over what a load reads "
                "must first have loaded something"
            )
            assert len({
                i.source.transfer.transfer_template_id
                for i in installments
                if not i.dates.is_confirmed
            }) == _SEAM_PROJECTED, (
                "the projected payments do not carry distinct definitions -- "
                "the count below could not tell a lazy walk from an eager load"
            )
            reads = _reads_of(_PRICING_TABLE, statements)
            assert reads == 0, (
                f"the date feed issued {reads} statement(s) against "
                f"{_PRICING_TABLE}: the amount model's eager load has moved "
                f"back into the loader, so a consumer of the DATES is paying "
                f"for the tier that prices them"
            )
            assert _reads_of(_PLAN_TABLE, statements) == 2, (
                "the plan relation was read other than once per half: each "
                "half is loaded from it in ONE statement, whatever the caller "
                "states"
            )

    def test_the_priced_feed_reads_every_pricing_table(
        self, app, db, seed_user, seed_periods,
    ):
        """``get_payment_history`` states the chain, so no leg walks per-row.

        The other direction, and it is not the same assertion negated: this is
        what fails if the options are simply deleted rather than moved to the
        caller, which would turn every priced leg into a walk to its parent's
        definition.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _seam_shadows(seed_user, loan, seed_periods)
            db.session.expire_all()
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            # Loaded before the window, so the reads counted are the feed's.
            params = loan_params_for(db.session, loan.id)
            with _statements_issued() as statements:
                payments = get_payment_history(loan.id, basis, params)

            assert len(payments) == _SEAM_SHADOWS, "the priced feed came back short"
            reads = _reads_of(_PRICING_TABLE, statements)
            assert reads == 1, (
                f"the priced feed issued {reads} statements against "
                f"{_PRICING_TABLE} over {_SEAM_PROJECTED} projected payments "
                f"on {_SEAM_PROJECTED} definitions, not the ONE an eager load "
                f"takes: a count of {_SEAM_PROJECTED} is the per-row walk "
                f"finding N-296 is about, and a count of 0 means nothing was "
                f"loaded at all"
            )
            assert _reads_of(_PLAN_TABLE, statements) == 2, (
                "the plan relation was read other than once per half by the "
                "priced feed"
            )


class TestThePartitionIsTotal:
    """A shadow the partition cannot place RAISES rather than vanishing."""

    def test_an_unplaceable_status_is_refused(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A settled row stops being settled: the producer refuses it by name.

        The arm that keeps "settled or Projected, nothing else" from being a
        claim nobody checks.  Constructing a sixth ``ref.statuses`` row would
        mutate a process-wide cache other tests share, so the SEED is left alone
        and the producer's own predicate is narrowed instead -- which puts a real
        row in exactly the state a sixth status would: neither settled nor
        Projected.  The alternative to raising is that the row falls out of the
        loan's schedule, its balance and its plan, silently.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            _transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
                settled_on=seed_periods[1].start_date,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id
            # It places the row before the predicate is narrowed, so the refusal
            # below is the narrowing and not the fixture.
            assert _legs.income_legs(
                loan.id, scenario_id, options=(), leg_options=(),
            ).settled

            monkeypatch.setattr(_legs, "settled_status_ids", frozenset)

            with pytest.raises(ValueError, match="neither settled nor Projected"):
                _legs.income_legs(
                    loan.id, scenario_id, options=(), leg_options=(),
                )
