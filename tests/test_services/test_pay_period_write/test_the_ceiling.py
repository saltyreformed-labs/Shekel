"""The CEILING (R-PC67, R-PC76), the floor's mirror in the one writer: a batch whose earliest new
payday skips a planned paycheck is refused, whichever door sent it.
"""

from datetime import date

import pytest

from app.exceptions import ValidationError
from app.services import pay_period_write, pay_schedule_service
from tests._test_helpers import rhythm_of, all_periods, record_paydays_across_a_hole


class TestTheCeilingRefusesASkippedPaycheck:
    """Rulings **R-PC67** and **R-PC76**: the floor's mirror, in the one writer.

    Plan step ``pay_calendar:C17-c-2a``, closing ledger row **P80**.  A
    batch whose earliest new payday falls at or past the SECOND payday the
    owner's plan projects after their record leaves a whole paycheck
    missing, and the derived calendar -- which has no way to say "no
    paycheck here" -- files every day between into the paycheck before.
    ``pay_period_batch.reject_skipped_paycheck`` refuses it from EVERY
    batch that has a survivor, whichever door sent it: this class grades a
    plain append with no ``retiring_ids`` at all, because the refusal it
    replaced (``reject_unconfirmed_gap``) graded only a batch that retired
    something and a stated append inherited nothing.
    """

    def _two_fortnightly_periods(self, user_id):
        """Record 2026-01-02 and 2026-01-16; the plan's next paydays are 01-30 and 02-13."""
        return pay_period_write.record_paydays(
            user_id=user_id, first_payday=date(2026, 1, 2),
            num_periods=2, rhythm=rhythm_of(14),
        )

    def test_a_plain_append_on_the_second_planned_payday_is_refused(
        self, app, db, bare_user,
    ):
        """02-13 skips the 01-30 paycheck; refused, and nothing is written."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError) as caught:
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 13),
                    num_periods=3, rhythm=rhythm_of(14),
                )
            db.session.rollback()
            message = str(caught.value)
            assert "before 2026-02-13" in message
            assert "2026-01-30" in message
            assert len(all_periods(user_id)) == 2

    def test_the_window_is_the_floor_up_to_the_ceiling_exclusive(
        self, app, db, bare_user,
    ):
        """01-30 (the floor) and 02-12 are accepted; 02-13 is not.

        Both directions of the bound in one case, so a ceiling tightened to
        any tolerance or loosened by a day fails here.  02-12 is a phase
        correction: the 01-16 paycheck runs 27 days and the era's phase
        moves to 02-12.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="before 2026-02-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 13),
                    num_periods=1, rhythm=rhythm_of(14),
                )
            db.session.rollback()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 12),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [date(2026, 2, 12)]
            assert pay_schedule_service.resolve_schedule(
                user_id,
            ).eras[-1].effective_from == date(2026, 2, 12)

    def test_it_reads_the_stored_plan_not_the_batchs_own_rhythm(
        self, app, db, bare_user,
    ):
        """A weekly batch opening 02-13 still skips the fortnightly 01-30.

        The ceiling is where the OWNER'S plan pays a second time, so a batch
        stating a shorter cadence cannot buy itself a later window: the
        paycheck skipped is one of the rhythm they have, not of the one they
        are about to state.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(ValidationError, match="before 2026-02-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 13),
                    num_periods=4, rhythm=rhythm_of(7),
                )
            db.session.rollback()
            assert len(all_periods(user_id)) == 2

    def test_a_batch_whose_first_day_the_record_holds_is_bounded_on_what_it_adds(
        self, app, db, bare_user,
    ):
        """Re-running from 01-02 with a larger count adds 01-30 -- the plan's own next."""
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 2),
                num_periods=4, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [
                date(2026, 1, 30), date(2026, 2, 13),
            ]

    def test_a_first_time_schedule_has_no_plan_to_skip(self, app, db, bare_user):
        """No survivor, no ceiling: the opening batch may start anywhere."""
        with app.app_context():
            user_id = bare_user["user"].id
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2030, 7, 5),
                num_periods=1, rhythm=rhythm_of(14),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [date(2030, 7, 5)]

    def test_the_ceiling_reads_the_piecewise_plan_across_a_seam(
        self, app, db, bare_user,
    ):
        """After a cadence change the second planned payday is the NEW era's.

        Paydays 01-02 and 01-16 at 14, then a regenerate to weekly from 01-30
        (on the plan's next payday, so it mints an era there and retires
        nothing).  The record now ends 01-30 in the weekly era: the plan's
        next is 02-06 and the ceiling 02-13, and a batch opening 02-13 is
        refused where the old fortnightly rhythm would have allowed it.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()

            with pytest.raises(ValidationError, match="before 2026-02-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 13),
                    num_periods=1, rhythm=rhythm_of(7),
                )
            db.session.rollback()
            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 12),
                num_periods=1, rhythm=rhythm_of(7),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [date(2026, 2, 12)]

    def test_it_reads_the_eras_the_batch_leaves_standing_not_the_stored_ones(
        self, app, db, bare_user,
    ):
        """A rebuild that RETIRES an era is bounded by the kept rhythm's plan.

        This step's adversarial review built the case.  Fortnightly from
        01-02 (01-02, 01-16), then a legal 30-day era minted at 01-30 (01-30,
        03-01).  A fortnightly rebuild retiring that era's two periods: from
        02-20 it must be REFUSED -- against the stored plan the ceiling was
        the 30-day era's 03-01 and the batch wrote a 35-day 01-16 paycheck
        with 01-30 and 02-13 missing; the kept rhythm's own window is
        ``[01-30, 02-13)``.  From 02-06 it must be ACCEPTED: a 7-day move of
        the kept rhythm's next paycheck, which the stored plan would have
        refused as skipping the 30-day era's own second payday.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            thirty = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 1, 30),
                num_periods=2, rhythm=rhythm_of(30),
            )
            db.session.commit()
            assert [p.start_date for p in thirty] == [
                date(2026, 1, 30), date(2026, 3, 1),
            ]
            retiring = {p.id for p in thirty}

            with pytest.raises(ValidationError, match="before 2026-02-13"):
                pay_period_write.record_paydays(
                    user_id=user_id, first_payday=date(2026, 2, 20),
                    num_periods=2, rhythm=rhythm_of(14), retiring_ids=retiring,
                )
            db.session.rollback()

            created = pay_period_write.record_paydays(
                user_id=user_id, first_payday=date(2026, 2, 6),
                num_periods=2, rhythm=rhythm_of(14), retiring_ids=retiring,
            )
            db.session.commit()
            assert [p.start_date for p in created] == [
                date(2026, 2, 6), date(2026, 2, 20),
            ]
            eras = pay_schedule_service.resolve_schedule(user_id).eras
            assert [(e.effective_from, e.rhythm.cadence.days) for e in eras] == [
                (date(2026, 1, 2), 14), (date(2026, 2, 6), 14),
            ]

    def test_the_tree_helper_writes_the_hole_the_writer_refuses_and_only_that(
        self, app, db, bare_user,
    ):
        """``record_paydays_across_a_hole`` is for the refused state alone.

        It writes the rows the writer used to and mints the era the writer
        would (a weekly block beside a fortnightly record), so a fixture
        that needs a 2026 block beside a 2024 opening payday keeps every
        observable it had -- and it REFUSES a batch the writer would accept,
        so it cannot quietly become the default door.
        """
        with app.app_context():
            user_id = bare_user["user"].id
            self._two_fortnightly_periods(user_id)
            db.session.commit()

            with pytest.raises(AssertionError, match="would accept"):
                record_paydays_across_a_hole(
                    user_id, date(2026, 1, 30), 1, rhythm_of(14),
                )
            with pytest.raises(ValidationError, match="Choose 2026-01-30 or later"):
                record_paydays_across_a_hole(
                    user_id, date(2026, 1, 20), 1, rhythm_of(14),
                )
            created = record_paydays_across_a_hole(
                user_id, date(2026, 7, 3), 2, rhythm_of(7),
            )
            db.session.commit()
            assert [p.start_date for p in created] == [
                date(2026, 7, 3), date(2026, 7, 10),
            ]
            eras = pay_schedule_service.resolve_schedule(user_id).eras
            assert [(e.effective_from, e.rhythm.cadence.days) for e in eras] == [
                (date(2026, 1, 2), 14), (date(2026, 7, 3), 7),
            ]
            assert [p.start_date for p in all_periods(user_id)] == [
                date(2026, 1, 2), date(2026, 1, 16),
                date(2026, 7, 3), date(2026, 7, 10),
            ]
