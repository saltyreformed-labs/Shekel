"""The two halves the route composes (R-R38): populate_periods_from_active_templates fills new
periods from each active template, transfer templates included, and nothing twice;
extend_pay_periods tail-appends contiguous periods on the stored cadence, leaves them empty and
refuses an owner with no periods.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.routes._period_population import populate_new_periods
from app.services import pay_era_write, pay_period_admin
from scripts.integrity_check import check_balance_anomalies, check_referential_integrity
from tests._test_helpers import (
    record_paydays_across_a_hole,
    era_of,
    rhythm_of,
    assert_pay_period_invariants,
    create_savings_account,
    derived_span,
    last_covered_day,
    make_expense_template,
    make_transfer_template,
    populate_in_a_fresh_pass,
    resolved_amount,
    seam_cash_balance_at,
)


def _future_periods(db_session, seed_user, count=4, start=date(2026, 7, 3)):
    """Generate `count` biweekly future periods (indices 1..count)."""
    periods = record_paydays_across_a_hole(
        user_id=seed_user["user"].id,
        first_payday=start,
        num_periods=count,
        rhythm=rhythm_of(14),
    )
    db_session.commit()
    return periods


def _extend_andpopulate_in_a_fresh_pass(user_id, num_periods):
    """Run BOTH halves of an extend, exactly as the route does.

    ``extend_pay_periods`` records the paydays; ``populate_new_periods`` opens
    the generate pass afterwards and fills them.  Ruling **R-R38**: the pass
    may only be opened above the service layer, and it must be opened AFTER
    the write, so the two are separate calls in that order.

    Args:
        user_id: The owning user's id.
        num_periods: How many periods to append.

    Returns:
        The newly created periods, now populated.
    """
    new_periods = pay_period_admin.extend_pay_periods(user_id, num_periods)
    populate_new_periods(user_id, new_periods)
    return new_periods


def _period_length(period):
    """Inclusive day-span of a period == its cadence."""
    return (last_covered_day(period) - period.start_date).days + 1


class TestPopulateFromActiveTemplates:
    """The repopulation helper fills periods with txns and transfers."""

    def test_populates_one_transaction_per_period(self, app, db, seed_user):
        """An active every-period template yields one txn per period."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()

            assert created == 3
            for period in periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert resolved_amount(txns[0]) == Decimal("1200.00")

    def test_includes_transfer_templates(self, app, db, seed_user):
        """Active transfer templates generate transfers too.

        New periods must never silently miss a recurring transfer, so the
        helper runs the transfer engine too, and counts each transfer once.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            make_transfer_template(db.session, seed_user, savings)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()

            transfers = (
                db.session.query(Transfer)
                .filter_by(user_id=seed_user["user"].id)
                .all()
            )
            assert created == 3
            assert len(transfers) == 3
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_generates_nothing(self, app, db, seed_user):
        """An inactive (archived) template produces no rows."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user, is_active=False)
            created = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            assert created == 0

    def test_idempotent_second_run_creates_nothing(self, app, db, seed_user):
        """Re-running over already-populated periods creates nothing.

        ``OccurrenceClaims`` skips any occurrence already answered by a
        template-linked row, so a retried extend / top-up is safe.
        """
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=3)
            make_expense_template(db.session, seed_user)
            first = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            second = populate_in_a_fresh_pass(
                seed_user["user"].id, {p.id for p in periods},
            )
            db.session.commit()
            assert first == 3
            assert second == 0

    def test_no_baseline_scenario_returns_zero(self, app, bare_periods):
        """A user with no baseline scenario is a no-op (returns 0)."""
        with app.app_context():
            created = populate_in_a_fresh_pass(
                bare_periods[0].user_id, {p.id for p in bare_periods},
            )
            assert created == 0

    def test_empty_period_list_returns_zero(self, app, seed_user):
        """An empty period list short-circuits to 0."""
        with app.app_context():
            assert populate_in_a_fresh_pass(seed_user["user"].id, set()) == 0


class TestExtendPayPeriods:
    """``extend_pay_periods`` tail-appends; the ROUTE repopulates."""

    def test_appends_contiguously_after_last_period(self, app, db, seed_user):
        """New periods continue the index sequence and start the next day."""
        with app.app_context():
            existing = _future_periods(db.session, seed_user, count=3)
            last = existing[-1]
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()

            assert [derived_span(p).period_index for p in new_periods] == [
                derived_span(last).period_index + 1, derived_span(last).period_index + 2,
            ]
            assert new_periods[0].start_date == last_covered_day(last) + timedelta(days=1)
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_this_door_takes_no_cadence_at_all(self, app, db, seed_user):
        """Finding **P29**: the parameter is GONE, not newly honoured.

        This test asserted the opposite -- that an explicit ``cadence_days``
        beat the stored one -- and that was the defect: the extend card renders
        NO control for it, so the only way to reach it was a direct POST, and
        what it produced was 7-day paychecks beside a ``budget.pay_schedule``
        still saying 14.  ``resolve_cadence``, the derived horizon and the next
        rolling top-up then all continued at 14.

        Extend CONTINUES an existing schedule, so the cadence is not a question
        it gets to ask.  Plan step C3-b deleted the parameter, its Marshmallow
        field and the rolling top-up's redundant pass-through -- which closes
        the finding by making the state unreachable rather than by adding the
        write finding **P30** objected to.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)

            with pytest.raises(
                TypeError, match="unexpected keyword argument 'cadence_days'",
            ):
                # Pylint: ``unexpected-keyword-arg`` -- the call passes the
                # parameter plan step C3-b deleted; its TypeError is this case's
                # subject.
                pay_period_admin.extend_pay_periods(  # pylint: disable=unexpected-keyword-arg
                    seed_user["user"].id, num_periods=1, cadence_days=7,
                )

            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 14
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_stored_schedule_cadence_used_when_unspecified(
        self, app, db, seed_user,
    ):
        """A persisted schedule cadence wins over the inferred one."""
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=2)
            # "Correct my cadence going forward" is a new ERA from the last
            # recorded payday (plan step C17-a): the day is on the 14-day grid
            # the record holds, and the 7-day grid from it is what the extend
            # continues.
            pay_era_write.mint_era(
                seed_user["user"].id, era_of(periods[-1].start_date, 7),
            )
            db.session.commit()
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=1,
            )
            db.session.commit()
            assert _period_length(new_periods[0]) == 7

    # ``test_infers_cadence_for_legacy_user`` stood here until plan step
    # **C4-b-2**.  It deleted the owner's ``budget.pay_schedule`` row, left
    # their 14-day periods standing, and asserted the extend path still
    # produced 14 -- from ``resolve_cadence``'s inference off the last
    # period's stored length.  That state is what ``fk_pay_periods_schedule``
    # now forbids (ledger rows **P8** and **P35**), so the case was deleted
    # with its subject rather than reworded.  What extend actually does with
    # the cadence is unchanged and covered by
    # ``test_stored_schedule_cadence_used_when_unspecified`` directly above,
    # which is the only source there is now.

    def test_the_door_leaves_the_new_periods_EMPTY(self, app, db, seed_user):
        """The door RECORDS and stops; nothing recurring is generated.

        The other half of ruling **R-R38**, and the reason it is asserted
        rather than assumed: the door used to generate too, and a caller that
        re-couples the two here would make ``populate_new_periods`` a
        no-op-looking second run whose absence at a NEW call site nothing
        would catch.  The very next case runs both halves over the same
        fixture and finds the rows, so this one cannot pass by the template
        being unable to generate at all.
        """
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user)
            new_periods = pay_period_admin.extend_pay_periods(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                assert (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .count()
                ) == 0, (
                    "extend_pay_periods generated a recurring row; since "
                    "R-R38 the read pass that resolves one may only be opened "
                    "above this layer, so the door must record and return"
                )

    def test_new_periods_get_recurring_rows(self, app, db, seed_user):
        """Extend + populate fills the new periods from the active templates."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user)
            new_periods = _extend_andpopulate_in_a_fresh_pass(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                txns = (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .all()
                )
                assert len(txns) == 1
                assert resolved_amount(txns[0]) == Decimal("1200.00")
            assert_pay_period_invariants(db.session, seed_user["user"].id)

    def test_archived_template_leaves_new_periods_empty(self, app, db, seed_user):
        """An archived template generates nothing into the new periods."""
        with app.app_context():
            _future_periods(db.session, seed_user, count=2)
            make_expense_template(db.session, seed_user, is_active=False)
            new_periods = _extend_andpopulate_in_a_fresh_pass(
                seed_user["user"].id, num_periods=2,
            )
            db.session.commit()
            for period in new_periods:
                assert (
                    db.session.query(Transaction)
                    .filter_by(pay_period_id=period.id)
                    .count()
                ) == 0

    def test_empty_schedule_raises(self, app, bare_user_with_cadence):
        """Extending a user with no periods raises ValidationError.

        ``bare_user_with_cadence`` since plan step ``pay_calendar:C4-d``: an
        owner with no ``budget.pay_schedule`` row is refused by
        ``calendar_for`` before this door reaches its own empty branch, so a
        bare owner would grade that refusal instead of this message.  The
        refusal itself is graded at
        ``test_pay_period_admin.TestAnOwnerWithNoPaydaysReachesEveryDoor``.
        """
        with app.app_context():
            with pytest.raises(ValidationError, match="Generate your first"):
                pay_period_admin.extend_pay_periods(
                    bare_user_with_cadence["user"].id, num_periods=2,
                )

    def test_balances_correct_after_extend(self, app, db, seed_user):
        """Disciplines 1-3: as-of balances march correctly after extend.

        Anchor $1000 at the bootstrap period (index 0, no expense).  A
        $1200 every-period expense fills indices 1..4, so the projected
        end balance at index N is 1000 - N*1200.  Extending by 2 fills
        indices 5..6 with the same expense, so the projection continues to
        1000 - 6*1200 in the new window while the retained window is
        untouched, and the production integrity checker flags nothing.
        """
        account = seed_user["account"]
        scen = seed_user["scenario"].id
        user_id = seed_user["user"].id
        with app.app_context():
            periods = _future_periods(db.session, seed_user, count=4)  # idx 1..4
            make_expense_template(db.session, seed_user, amount="1200.00")
            populate_in_a_fresh_pass(user_id, {p.id for p in periods})
            db.session.commit()

            # Pre-extend: 1000 - N*1200 at index N's end.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(periods[3]),  # index 4
            ) == Decimal("-3800.00")  # 1000 - 4*1200
            retained = seam_cash_balance_at(
                account, scen, last_covered_day(periods[1]),  # index 2
            )
            assert retained == Decimal("-1400.00")  # 1000 - 2*1200

            # Extend by 2 -> indices 5, 6, each repopulated with the expense.
            new_periods = _extend_andpopulate_in_a_fresh_pass(user_id, num_periods=2)
            db.session.commit()

            # New window: the projection continues. Index 6 -> 1000 - 6*1200.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(new_periods[-1]),  # index 6
            ) == Decimal("-6200.00")  # 1000 - 6*1200
            # Retained window is untouched by the append.
            assert seam_cash_balance_at(
                account, scen, last_covered_day(periods[1]),
            ) == retained

            # Discipline 1: structure sound.
            assert_pay_period_invariants(db.session, user_id)
            # Discipline 3: production integrity checker flags nothing.
            assert all(r.passed for r in check_balance_anomalies(db.session))
            assert all(r.passed for r in check_referential_integrity(db.session))
