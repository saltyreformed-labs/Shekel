"""Transfer route tests: the PATCH's figure correction (the Actual box), each box graded on its
own, and the lock a stale popover meets.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services import transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    an_entered_day,
    an_observed_day,
    field_is_disabled,
    linked_ledger_account,
    linked_net_by_date,
    net_posted_by_day,
    on_both_sides,
    transfer_family_journal_filter,
    transfer_side_figure,
    transfer_side_record,
    transfer_side_settle_day,
    typed,
)
from app.services.settle_day import SettleDay

from ._helpers import _create_savings_account, _create_transfer, _THREE_DAYS_AGO


class TestThePatchGradesEachBoxOnItsOwn:
    """The transfer PATCH grades each day box against what THAT box showed.

    Plan step ``balance:X-bi-6-4c-3`` (rulings **R-BAL108**, **R-BAL164**).
    The popover renders a side holding its own day PREFILLED and a side
    borrowing the other's EMPTY, and posts both on every Save.  So an untouched
    Save states nothing for either side, and any day typed into an empty box --
    the borrowed day included -- is that side's own, ``entered``.
    """

    @staticmethod
    def _checking_observed(seed_user, seed_periods_today, day):
        """A settled transfer: Checking bank-shown on *day*, Savings borrowing it."""
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.settle_transfer(
            xfer.id, seed_user["user"].id,
            side_days=(transfer_service.SideDay(
                xfer.from_account_id, an_observed_day(day),
            ),),
        )
        db.session.commit()
        return xfer

    @staticmethod
    def _days(xfer):
        """Return ``(from side, to side)`` recorded days, freshly read.

        Each off the side's payment RECORD, which hangs off the transfer since
        plan step ``balance:X-bi-6-4d-2``; it read the twins until then, which
        held no day from that step until plan step balance:X-bi-6-4d-2 deleted
        them (ruling R-BAL167 class 4, plan step balance:X-bi-6-4d-2).
        """
        db.session.expire_all()
        return tuple(
            transfer_side_settle_day(db.session, xfer.id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )

    def test_an_untouched_save_states_nothing_for_either_box(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Checking's box echoes its day; Savings' box posts empty: nothing moves."""
        with app.app_context():
            day = display_today() - timedelta(days=5)
            xfer = self._checking_observed(seed_user, seed_periods_today, day)
            before = self._days(xfer)
            assert before == (
                an_observed_day(day),
                SettleDay(day=day, basis=SettledDayBasisEnum.BORROWED),
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on_from": day.isoformat(), "settled_on_to": ""},
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert self._days(xfer) == before

    def test_the_borrowed_day_typed_into_its_empty_box_is_the_sides_own(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Typing Savings' guessed day CONFIRMS it: ``entered``, the day unchanged.

        Ruling R-BAL164's reason for rendering the box empty: prefilled, an
        unchanged day would be an echo and confirming the guess would have no
        spelling.
        """
        with app.app_context():
            day = display_today() - timedelta(days=5)
            xfer = self._checking_observed(seed_user, seed_periods_today, day)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on_from": day.isoformat(),
                    "settled_on_to": day.isoformat(),
                },
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert self._days(xfer) == (an_observed_day(day), an_entered_day(day))

    def test_another_day_typed_into_the_empty_box_moves_that_side_only(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Savings received it later: its side moves; Checking's bank day stays."""
        with app.app_context():
            day = display_today() - timedelta(days=5)
            arrived = day + timedelta(days=2)
            xfer = self._checking_observed(seed_user, seed_periods_today, day)

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_on_from": day.isoformat(),
                    "settled_on_to": arrived.isoformat(),
                },
            )
            assert response.status_code == 200, response.get_data(
                as_text=True,
            )[:300]

            assert self._days(xfer) == (
                an_observed_day(day), an_entered_day(arrived),
            )


class TestTransferActualBox:
    """The transfer PATCH's FIGURE correction -- the Actual box.

    **The settle-day door's sibling, and it exists because the card was
    inconsistent with itself** (developer ruling, 2026-08-17): a settled
    transfer's DAY was correctable in place while its FIGURE was not, so the
    only way to restate what the bank moved was to revert, edit and settle
    again -- and a revert RETAINS the recorded figure, so the re-settle silently
    re-booked the old number over the re-planned one.  The lock produced a wrong
    figure, not friction.

    The rule the whole class grades: **a lock protects a BUDGET DECISION from
    being rewritten; an OBSERVED FACT gets corrected when the statement
    disagrees.**  The estimate, the period, the category and the due date are
    locked on a finalised transfer.  What the bank moved, and the day it moved,
    are not.
    """

    @staticmethod
    def _settled_transfer(seed_user, seed_periods_today, day):
        """Return a settled transfer dated *day*, ledger in step.

        Callers pass a PAST day deliberately.  Settling stamps the user's today,
        so a fixture dated today makes every "a figure correction did not move
        the settle day" assertion blind: the mutation those assertions exist to
        catch is the seam re-stamping today, which on a today-dated row is
        indistinguishable from preserving.  That is finding **N-146**'s shape,
        which this arc has already paid for once.
        """
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            side_days=on_both_sides(
                xfer.from_account_id, xfer.to_account_id, an_entered_day(day),
            ),
        )
        db.session.commit()
        return xfer

    @staticmethod
    def _side_records(xfer_id):
        """Return *xfer_id*'s two sides' payment records, the from-side (expense) first.

        A side's record hangs off the TRANSFER since plan step
        ``balance:X-bi-6-4d-2``, where a ``_legs`` helper's twins held it
        until then; that helper went at ``balance:X-bi-6-4d-2``, which
        births no twin (ruling R-BAL167 class 4, plan step
        balance:X-bi-6-4d-2).  Read by independent SQL
        (``transfer_side_record``); ``None`` for a side holding none.
        """
        xfer = db.session.get(Transfer, xfer_id)
        return tuple(
            transfer_side_record(db.session, xfer_id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )

    def test_the_box_renders_on_a_settled_transfer_and_is_not_disabled(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A finalised transfer offers the Actual box, ENABLED.

        The whole ruling in one assertion.  A draft of this step drew the
        transaction twin of this box gated on ``locked`` -- and every
        ``is_settled`` status is also ``is_immutable``, so it rendered
        ``disabled`` on 100% of the rows it appeared on and was deleted as
        unreachable.  Being disabled WAS the defect, so the test asserts the
        box is present AND live, on both doors onto this popover.  The leg
        door's leg is the from-side, named off the transfer where it was read
        off the expense twin until plan step ``balance:X-bi-6-4d-2``, which
        births none (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )

            for url in (
                f"/transfers/{xfer.id}/full-edit",
                f"/transfers/{xfer.id}/full-edit?leg_account_id={xfer.from_account_id}",
            ):
                body = auth_client.get(url).get_data(as_text=True)
                assert 'name="settled_amount"' in body, (
                    f"{url} hid the Actual box from a settled transfer"
                )
                assert not field_is_disabled(body, "settled_amount"), (
                    f"{url} rendered the Actual box disabled -- that is the "
                    "defect, not the guard: a lock protects a decision and "
                    "what the bank moved is an observation"
                )
                # The BUDGET decision beside it stays locked, which is what
                # makes the contrast the ruling draws visible on one screen.
                assert field_is_disabled(body, "amount"), (
                    f"{url} left the plan editable on a finalised transfer"
                )

    def test_the_box_is_absent_on_a_projected_transfer(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No settle, no figure: there is nothing to correct.

        The firing control for the render condition.  A box offered on a
        Projected transfer would take a figure the service refuses, which is
        the shape ruling **R-FF** removed from the reconcile panel.
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert 'name="settled_amount"' not in body

    def test_a_correction_lands_on_BOTH_legs_and_moves_the_ledger(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """$200.00 settled, the statement says $214.37: both legs post it.

        Transfer Invariant 3 for the settlement record: a transfer's money moves
        on its two legs by the one figure the transfer stores, while each side's
        day is its own (ruling **R-BAL108**, plan step ``balance:X-bi-6-4c-3``;
        the fixture dates both sides alike).  The ledger follows in the same request, because the
        figure IS the pair's confirmed cash effect -- a correction that changed
        the record without moving the books would leave the two disagreeing.

        Hand arithmetic: the settle books $200.00 out of Checking and into
        Savings.  The correction re-books $214.37, so the net posted magnitude
        for this transfer is $214.37, not $200.00 + $14.37 twice-counted.

        The figure and who wrote it are the TRANSFER's, their one home since
        plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each leg's payment record carried a copy until
        then; each leg's day is its side's payment record's, read off the
        transfer rather than its twin (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            day = _THREE_DAYS_AGO()
            xfer = self._settled_transfer(seed_user, seed_periods_today, day)
            version = db.session.get(Transfer, xfer.id).version_id
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "settled_amount": "214.37",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            held = db.session.get(Transfer, xfer.id)
            assert (held.settled_figure, held.settled_figure_source_id) == (
                Decimal("214.37"), typed_id,
            ), (
                f"transfer {xfer.id} did not record the correction as a "
                "person's"
            )
            for record in self._side_records(xfer.id):
                assert record.settled_on == day, (
                    "a figure correction moved the settle day"
                )
            assert net_posted_by_day(
                transfer_family_journal_filter(xfer.id),
            ) == {day: Decimal("214.37")}
            # Each LEG on its own account's ledger, signed as the ledger signs
            # it (out of Checking negative, into Savings positive).  The family
            # read above keeps the LARGEST per-account residue, so a correction
            # booked on the from-side alone still reads $214.37 there.  Hand
            # arithmetic on the transfer's day: Checking -200.00 - 14.37 =
            # -214.37; Savings +200.00 + 14.37 = +214.37.  No other posting on
            # either ledger is dated that day: Checking's opening and
            # origination are dated in 2024 (the seed's bootstrap), and
            # Savings opened at $0.00, which books no entry, and asserts today.
            # Not ``account_posting_total``: an assertion absorbs a movement
            # dated before it, so Savings, asserted today, totals $0.00
            # whatever its leg booked.
            scenario_id = seed_user["scenario"].id
            for account_id, posted in (
                (xfer.from_account_id, Decimal("-214.37")),
                (xfer.to_account_id, Decimal("214.37")),
            ):
                assert linked_net_by_date(
                    db.session,
                    linked_ledger_account(db.session, account_id).id,
                    scenario_id,
                ).get(day) == posted, (
                    f"the correction did not reach the leg on account {account_id}"
                )

    def test_an_ECHO_of_the_recorded_figure_records_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Saving the popover untouched must not manufacture a correction.

        The box is PREFILLED with what the pair records, and this form submits
        every input it renders -- so an untouched Save posts the same figure
        back.  Writing it would restamp a ``derived`` record as ``corrected``
        and destroy the only stored signal that says a human read a number off a
        statement, which is what ruling **R-FB**'s production measurement is
        made of.

        Shown to FIRE: deleting ``correction_record``'s equality test leaves
        the pair's figure stamped ``corrected``.  Who wrote it is read
        off the TRANSFER, its one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each side's payment record carried a copy until
        then, and off no twin (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            resolved_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert None not in self._side_records(xfer.id)
            assert db.session.get(Transfer, xfer.id).settled_figure_source_id == (
                resolved_id
            ), "fixture precondition: a plain settle records a RESOLVED figure"
            version = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "200.00", "version_id": str(version)},
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert None not in self._side_records(xfer.id)
            assert db.session.get(Transfer, xfer.id).settled_figure_source_id == (
                resolved_id
            ), "an echoed prefill was recorded as a human's correction"

    def test_a_REVERT_carrying_the_box_drops_it_and_still_reverts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling R-EG's argument applied to the figure.

        The documented way to unlock a finalised transfer is to set Status to
        Projected in this same form -- which submits the Actual box's current
        contents alongside it.  That figure is a stale ECHO of the state being
        left, not an assertion that this much moved: the user picked Projected,
        which says it did not move at all.  The route DROPS it, so the unlock
        path keeps working; a service caller asserting both on purpose is still
        refused (see the service tests).

        What moved is RETAINED, because a revert releases the ASSERTION and
        keeps the fact -- the two have different lifetimes.  The assertion is
        read off each side's payment record, kept un-dated across the revert,
        and what moved off the TRANSFER, its one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each record carried a copy until then; neither
        off the twins (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            version = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "200.00",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.PROJECTED)
            ), "the unlock path was broken by the echoed figure"
            for record in self._side_records(xfer.id):
                assert record.settled_on is None, "a revert keeps the assertion"
            assert db.session.get(Transfer, xfer.id).settled_figure == (
                Decimal("200.00")
            ), "a revert destroyed what moved"

    def test_a_figure_on_a_PROJECTED_transfer_is_REFUSED(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An amount states what MOVED, and a Projected pair's money has not.

        A Projected transfer records NOTHING, so a submitted figure cannot be an
        echo of its record -- there is no record to echo.  It is therefore a
        real assertion about money that has not moved, and it is refused rather
        than dropped: ``TransferUpdateSchema.Meta.unknown`` is ``EXCLUDE``, and a
        silently discarded money field is how a user's typed number disappears.

        The ECHO half of the same rule -- an untouched box riding a revert, which
        must still drop so the unlock path works -- is graded by
        ``test_a_REVERT_carrying_the_box_drops_it_and_still_reverts``.  The two
        cases together are the whole of ``status_seam.figure_for_status``, and
        an earlier version of that rule read the STATUS alone and so could not
        tell them apart.

        Reached by a crafted submission rather than by the form: the box is not
        rendered on a Projected transfer at all.

        "Written anyway" is read off each side's payment record, which hangs
        off the transfer since plan step ``balance:X-bi-6-4d-2``, not off
        the twins, which held none from that step until plan step
        balance:X-bi-6-4d-2 deleted them (ruling R-BAL167 class 1, plan step
        balance:X-bi-6-4d-2).
        """
        with app.app_context():
            savings = _create_savings_account(seed_user)
            xfer = _create_transfer(seed_user, seed_periods_today, savings)
            db.session.commit()
            version = xfer.version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "50.00", "version_id": str(version)},
            )

            assert response.status_code == 400, response.status_code
            assert "has nothing to record" in response.get_data(as_text=True)
            db.session.expire_all()
            for record in self._side_records(xfer.id):
                assert record is None, "a refused figure was written anyway"

    def test_a_status_IN_HAND_carrying_a_correction_does_BOTH(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A status IN HAND with a corrected Actual records on BOTH legs.

        The transfer half of the defect this step measured on the transaction
        door: the correction and the status change were treated as
        alternatives, so one of them was silently dropped and the response
        still said 200.

        It was ``Paid -> Settled`` -- an ARCHIVE, a real status MOVE -- until
        plan step **balance:X-am** deleted that status.  A settled transfer's
        only remaining status move is the revert, and a revert carrying a
        changed figure is refused rather than composed, so the surviving legal
        instance is the identity re-submit the popover actually produces: it
        posts the whole row, so the status box arrives beside the Actual box on
        every Save.  The reduction is recorded on the transaction twin's class
        docstring, ``TestTheDoorAppliesTheStatusANDTheCorrection``.

        What this still grades that its transaction sibling cannot: the
        correction reaches BOTH SIDES and the pair stays mirrored.  The
        transfer's status is the one status since plan step
        ``balance:X-bi-6-4d-2``, and each side's day is its payment record's,
        read off the transfer where the twins' status and day were (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The figure is the
        transfer's own since that step's 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), so the per-side figure read asks that one figure once
        per side.
        """
        with app.app_context():
            day = _THREE_DAYS_AGO()
            xfer = self._settled_transfer(seed_user, seed_periods_today, day)
            stored = db.session.get(Transfer, xfer.id)
            version = stored.version_id
            paid_id = stored.status_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(paid_id),
                    "settled_amount": "187.65",
                    "version_id": str(version),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            held = db.session.get(Transfer, xfer.id)
            assert held.status_id == paid_id, (
                "the status was dropped while the figure was recorded"
            )
            for account_id in (held.from_account_id, held.to_account_id):
                assert transfer_side_settle_day(
                    db.session, xfer.id, account_id,
                ).day == day
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("187.65")
            assert net_posted_by_day(
                transfer_family_journal_filter(xfer.id),
            ) == {day: Decimal("187.65")}
            # Each LEG on its own account's ledger, signed as the ledger signs
            # it (out of Checking negative, into Savings positive).  The
            # figure read above asks the transfer's one figure once per side,
            # and the family read keeps the LARGEST per-account residue, so it
            # catches a to-side still booking the $200.00 plan only because
            # 200.00 > 187.65: a correction UP would hide the same defect
            # behind the from-side's larger figure.  Hand arithmetic on the
            # transfer's day: the settle books $200.00 and the correction
            # re-books $187.65 (200.00 - 187.65 = 12.35), so Checking -200.00
            # + 12.35 = -187.65; Savings +200.00 - 12.35 = +187.65.  No other
            # posting on either ledger is dated that day: Checking's opening
            # and origination are dated in 2024 (the seed's bootstrap), and
            # Savings opened at $0.00, which books no entry, and asserts
            # today.  Not ``account_posting_total``: an assertion absorbs a
            # movement dated before it, so Savings, asserted today, totals
            # $0.00 whatever its leg booked.
            for side, account_id, posted in (
                ("from-side", held.from_account_id, Decimal("-187.65")),
                ("to-side", held.to_account_id, Decimal("187.65")),
            ):
                assert linked_net_by_date(
                    db.session,
                    linked_ledger_account(db.session, account_id).id,
                    seed_user["scenario"].id,
                ).get(day) == posted, (
                    f"the correction did not reach the {side}'s leg on "
                    f"account {account_id}"
                )

    def test_a_recordless_settled_pair_repairs_with_the_day_AND_the_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A ``$0.00`` close: a day alone is refused, and a figure with the day repairs it.

        A settled row carrying no settlement record predated the record
        entirely (finding **N-181**), and through plan step
        ``balance:X-bi-4b-1`` ``ck_transactions_settle_day_needs_a_record``
        paired the day with the row's figure columns, so stating the DAY
        alone was a designed 400 and the repair needed both halves in one
        save.  The columns and the CHECK went at ``X-bi-4b-2``: a settled
        leg holding no entry IS the ``$0.00`` record (ruling **R-BAL82**).
        Since plan step ``balance:X-bi-6-4d-2`` a side's day lives on its
        payment RECORD alone, so a ``$0.00`` close keeps no day at all
        (ruling **R-BAL141**), and stating the day alone is REFUSED with the
        sentence the developer picked (ruling **R-BAL230**, "Refuse, say
        why"): nothing is stored and nothing is posted.  The Actual box,
        which reads ``0.00`` for the pair, is how the owner states what the bank
        really took, and a figure with the day dates both sides.

        The popover must RENDER for such a pair: a surface that refuses to
        draw cannot repair the row it is the only repair path for.  The
        ``$0.00`` close is built through the app -- a typed ``$0.00``
        correction, which takes both sides' records off -- where it was
        staged behind the seam's back on the twins, which held no record
        from that step until plan step balance:X-bi-6-4d-2 deleted them
        (ruling R-BAL167, plan step balance:X-bi-6-4d-2; the
        refused second save follows R-BAL230, a rule-5 re-expression the
        developer approved 2026-10-09).  Each save is graded on the side
        records it leaves.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            db.session.expire_all()
            assert self._side_records(xfer.id) == (None, None), (
                "fixture: the $0.00 close kept a side's record"
            )

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert 'name="settled_amount"' in body, (
                "the popover hid the Actual box from the pair that needs it"
            )
            # Sliced to the INPUT TAG: a bare membership test on the whole
            # body grades nothing, which is what a neutral review measured
            # this assertion doing (2026-08-18).
            box = body[body.index('name="settled_amount"'):]
            box = box[:box.index(">")]
            # ``0.00``: the figure the transfer STORES for a close of nothing
            # since plan step balance:X-bi-6-4d-2's 4d-4 half (ruling
            # R-BAL365), at the column's scale like every stored figure; the
            # box read ``0`` while a reader inferred it from the missing
            # records (the inference ruling R-BAL368 deletes).  The same
            # $0.00.
            assert 'value="0.00"' in box, (
                "the box did not read the $0.00 record of a pair holding no "
                "entry: " + box
            )

            version = db.session.get(Transfer, xfer.id).version_id
            day_only = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    # The day boxes as the popover posts them when the owner
                    # types the day into ONE box: the other is empty (neither
                    # side holds a day).
                    "settled_on_from": display_today().isoformat(),
                    "settled_on_to": "",
                    "version_id": str(version),
                },
            )
            assert day_only.status_code == 400, day_only.get_data(as_text=True)
            assert (
                "A $0.00 close moved no money, so it has no day. Type the "
                "amount the bank took to date it."
            ) in day_only.get_data(as_text=True)
            db.session.expire_all()
            assert self._side_records(xfer.id) == (None, None), (
                "the refused day wrote a record"
            )
            assert net_posted_by_day(
                transfer_family_journal_filter(xfer.id),
            ) == {}, "the refused day posted money"

            version = db.session.get(Transfer, xfer.id).version_id
            repair = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    # The day and figure boxes as the popover posts them when
                    # the owner types the day into ONE box and the figure into
                    # the Actual box: both day boxes render empty, since
                    # neither side holds a day.
                    "settled_on_from": display_today().isoformat(),
                    "settled_on_to": "",
                    "settled_amount": "200.00",
                    "version_id": str(version),
                },
            )

            assert repair.status_code == 200, repair.get_data(as_text=True)
            db.session.expire_all()
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )
            for account_id in (xfer.from_account_id, xfer.to_account_id):
                record = transfer_side_record(db.session, xfer.id, account_id)
                assert record is not None, (
                    f"the repair left the side on account {account_id} "
                    "holding no record"
                )
                assert record.settled_on == display_today()
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("200.00")
            # Who wrote the figure is the TRANSFER's, its one home since plan
            # step balance:X-bi-6-4d-2's 4d-4 half (ruling R-BAL366), where
            # each side's record carried a copy until then.
            assert db.session.get(Transfer, xfer.id).settled_figure_source_id == (
                typed_id
            )

    def test_the_rebook_notice_shows_what_a_re_settle_will_book(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A reverted transfer's card names the figure a re-settle re-books.

        This card TELLS the user to set Status to Projected in order to edit the
        amount, so the transfer they are most likely looking at here is exactly
        the one carrying a retained correction -- and its plan ($200.00) is not
        what a tick will book ($214.37).  Two numbers about one transfer, and
        the second was visible on no surface but the reconcile panel.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("214.37")),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert "$214.37" in body, (
                "the card showed a $200.00 plan for a transfer that will book "
                "$214.37, with the real figure on no surface"
            )

    def test_the_notice_is_absent_when_the_plan_IS_what_a_tick_books(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The notice's firing control.

        A transfer holding no ``corrected`` record re-settles at its plan, which
        is already on screen in the Amount box -- so drawing the notice would
        state a difference that does not exist.
        """
        with app.app_context():
            xfer = self._settled_transfer(
                seed_user, seed_periods_today, _THREE_DAYS_AGO(),
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            body = auth_client.get(
                f"/transfers/{xfer.id}/full-edit"
            ).get_data(as_text=True)
            assert "Marking this paid will record" not in body


class TestTheTransferLockCoversTheShadowOnlyEdits:
    """A stale popover cannot silently overwrite a figure or a day.

    **A transfer and its two shadows were ONE thing** (developer ruling,
    2026-08-18).  Both full-edit popovers pinned ``Transfer.version_id``, but
    the two facts they could correct in place -- what the bank moved and the
    day it moved -- lived on the SHADOWS, and SQLAlchemy bumps a version
    counter only for a row it actually UPDATEs.  So the pin protected everything on the form
    except the two money-adjacent fields the form exists to correct.

    Measured before the fix: parent ``version_id = 2``; tab A corrects the
    figure to `$214.37`, 200 OK, parent still `2`; stale tab B saves its
    prefilled `$200.00` against the same pin, 200 OK, both legs now record
    `$200.00`.  The statement figure gone, reported as success.

    Since plan step ``balance:X-bi-6-4d-2`` the two facts live apart, where
    they lived on the shadows until then (ruling R-BAL167 class 4).  The DAY
    is each side's payment RECORD's, still a row other than the transfer's
    own, so a day correction moves the counter only because
    ``status_seam``'s ``sync_side_records`` forces the transfer into the
    flush; ``test_a_settle_day_correction_moves_the_counter_too`` grades
    that.  The FIGURE is the transfer's own column since that step's 4d-4
    half (rulings **R-BAL365**, **R-BAL366**), so a figure correction moves
    the counter by itself, and
    ``test_a_stale_tab_cannot_overwrite_a_figure_correction`` grades the 409
    a stale tab meets, not that forced flush.
    """

    @staticmethod
    def _settled(seed_user, seed_periods_today):
        savings = _create_savings_account(seed_user)
        xfer = _create_transfer(seed_user, seed_periods_today, savings)
        transfer_service.update_transfer(
            xfer.id, seed_user["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        db.session.commit()
        db.session.expire_all()
        return xfer

    @staticmethod
    def _side_figures(xfer_id):
        """Return *xfer_id*'s settled figure as each side reads it, the from-side first.

        Each is the figure the TRANSFER stores (``transfer_side_figure``,
        ``None`` while it is not settled): a side's payment record holds no
        figure since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings
        **R-BAL365**, **R-BAL366**).  This read the twins' figures until
        that step, which they keep no longer (ruling R-BAL167 class 4, plan
        step balance:X-bi-6-4d-2).
        """
        xfer = db.session.get(Transfer, xfer_id)
        return [
            transfer_side_figure(db.session, xfer_id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        ]

    def test_a_stale_tab_cannot_overwrite_a_figure_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two tabs, one pin: the second save is a 409, not a lost update."""
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            shared_pin = db.session.get(Transfer, xfer.id).version_id

            first = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "214.37",
                      "version_id": str(shared_pin)},
            )
            assert first.status_code == 200, first.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).version_id > shared_pin, (
                "the figure correction left the transfer's counter behind"
            )

            second = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": "200.00",
                      "version_id": str(shared_pin)},
            )

            assert second.status_code == 409, (
                "a stale tab overwrote a figure correction and reported success"
            )
            db.session.expire_all()
            for figure in self._side_figures(xfer.id):
                assert figure == Decimal("214.37"), (
                    "the stale save landed anyway"
                )

    def test_a_settle_day_correction_moves_the_counter_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The SAME hole one field over, closed by the same statement.

        The settle-day box (ruling **R-ED**) shipped before the Actual box and
        writes only the sides' records (the shadows' days until plan step
        ``balance:X-bi-6-4d-2``) for the identical reason, so it carried the
        identical defect.  Fixing the figure alone would have left it standing
        on the same form.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            shared_pin = db.session.get(Transfer, xfer.id).version_id

            assert auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_on_from": _THREE_DAYS_AGO().isoformat(),
                      "settled_on_to": "",
                      "version_id": str(shared_pin)},
            ).status_code == 200
            db.session.expire_all()

            assert db.session.get(Transfer, xfer.id).version_id > shared_pin

    def test_an_ECHO_does_not_move_the_counter(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control: a write that changes nothing is not a change.

        The popover re-submits every input on every Save, so an untouched
        re-save must not bump the pin -- a counter that moved when nothing did
        would turn a second open tab into a spurious 409 on a form nobody
        edited.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            recorded = self._side_figures(xfer.id)[0]
            pin = db.session.get(Transfer, xfer.id).version_id

            assert auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={"settled_amount": str(recorded),
                      "version_id": str(pin)},
            ).status_code == 200

            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).version_id == pin, (
                "an echoed prefill bumped the aggregate's counter"
            )

    def test_a_CHANGED_figure_beside_a_revert_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The transfer twin of the silent-discard defect.

        The submission rule read the STATUS alone, so a figure the user had just
        retyped was dropped exactly like an untouched prefill -- and because a
        revert RETAINS what moved, the stale record then governed the re-settle.
        An echo still drops (the unlock path must keep working); a change is
        refused, naming both acts.
        """
        with app.app_context():
            xfer = self._settled(seed_user, seed_periods_today)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                figure=typed(Decimal("214.37")),
            )
            db.session.commit()
            db.session.expire_all()
            pin = db.session.get(Transfer, xfer.id).version_id

            response = auth_client.patch(
                f"/transfers/instance/{xfer.id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "111.11",
                    "version_id": str(pin),
                },
            )

            assert response.status_code == 400, (
                "a figure the user CHANGED was swallowed by the revert"
            )
            db.session.expire_all()
            assert db.session.get(Transfer, xfer.id).status_id == (
                ref_cache.status_id(StatusEnum.DONE)
            ), "a refused request reverted the transfer anyway"
            for figure in self._side_figures(xfer.id):
                assert figure == Decimal("214.37")
