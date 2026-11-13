"""The transfer engine's schedule, and the regenerate sweep's domain."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.services import recurrence_engine
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from tests._test_helpers import make_cadence_rule, populate_in_a_fresh_pass
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY_FIRST

from ._helpers import _make_template, _rows, _append_period


class TestTheTransferEngineTakesTheSameSchedule:
    """Half the changed surface, and the migration ships a DELETE for it."""

    def _transfer_template(self, seed_user, cadence):
        """Create a recurring transfer template into a fresh savings account.

        Args:
            seed_user: The ``seed_user`` fixture dict.
            cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.

        Returns:
            The flushed :class:`~app.models.transfer_template.TransferTemplate`.
        """
        # Pylint: ``import-outside-toplevel`` -- the account helper and the
        # transfer model are needed by this class alone; at module scope they
        # would load for every test in the file.
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- see above.
        from tests._test_helpers import create_savings_account  # pylint: disable=import-outside-toplevel

        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
        )
        template = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=savings.id,
            name="Savings Sweep",
        )
        db.session.add(template)
        db.session.flush()
        # The definition first, then the cadence onto it (plan step R-F6).
        make_cadence_rule(template, cadence)
        return template

    def test_an_extend_does_not_duplicate_a_monthly_first_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """The transfer engine inherits the fix through the shared preamble.

        ``transfer_recurrence`` reaches the schedule through
        ``recurrence_engine.resolve_generation_plan``, so D22 is structurally
        shared -- but "shared by construction" is an argument, and the two
        engines are deliberate parallels that have drifted before.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the transfer engine and
            # its model are this class's collaborators only.
            from app.models.transfer import Transfer  # pylint: disable=import-outside-toplevel
            # Pylint: ``import-outside-toplevel`` -- see above.
            from app.services import transfer_recurrence  # pylint: disable=import-outside-toplevel

            scenario_id = seed_user["scenario"].id
            template = self._transfer_template(
                seed_user, MONTHLY_FIRST,
            )
            transfer_recurrence.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()

            def may_starts():
                """Return the May 2026 pay-period starts holding a transfer."""
                rows = (
                    db.session.query(Transfer)
                    .filter_by(
                        transfer_template_id=template.id,
                        scenario_id=scenario_id,
                    ).all()
                )
                return sorted(
                    row.pay_period.start_date for row in rows
                    if row.pay_period.start_date.month == 5
                )

            assert may_starts() == [date(2026, 5, 8)]

            appended = _append_period(seed_user, seed_periods)
            populate_in_a_fresh_pass(seed_user["user"].id, {p.id for p in appended})
            db.session.flush()

            assert may_starts() == [date(2026, 5, 8)]


class TestRegenerateSweepsOnlyWhatItRewrites:
    """The delete bound and the write bound are ONE bound.

    Found by adversarial review of this step: ``regenerate_for_template``'s
    ``effective_from=None`` default used to take the SCHEDULE's opening payday
    while ``generate_for_template`` wrote only inside the window, so a narrow
    window deleted every row from the owner's first payday forward and
    recreated only a few.  No route reaches it -- both callers pass a
    whole-schedule window -- and nothing in the signature said it was illegal.
    """

    def test_a_narrow_window_does_not_sweep_the_whole_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Regenerating into periods 5-9 leaves periods 0-4 alone."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(
                seed_user, EVERY_PERIOD,
            )
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(BalanceContext.build(seed_user["user"].id)),
                scenario_id,
            )
            db.session.flush()
            assert len(_rows(template, scenario_id)) == 10

            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), {p.id for p in seed_periods[5:]},
                ),
                scenario_id,
            )
            db.session.flush()

            # All ten survive: 0-4 were never in the sweep's window, and 5-9
            # were deleted and regenerated.
            assert sorted(_rows(template, scenario_id)) == [
                period.start_date for period in seed_periods
            ]


class TestTheSweepDomainIsTheWINDOWAndNotABoundOnIt:
    """What let ``regeneration_bound`` go, graded where it is visible.

    The helper existed because the sweep was SQL against a column: "no lower
    bound" had to become a concrete date, and the date had to be the WRITE
    WINDOW's opening or the sweep reached rows the pass would never rewrite.
    Plan step C2-f3c deleted it on the strength of a stronger property -- the
    domain IS the window, so it cannot exceed the window whatever the bound is.

    **The sibling class's narrow-window case cannot see that property**
    (adversarial review of C2-f3c).  It uses a contiguous TAIL, where "the ids
    in the window" and "every period from the window's opening onward" select
    the identical rows -- so it passes byte-identically on both trees and
    measures nothing about the change.  Only a window with a HOLE in it
    separates the two readings, and nothing else in the suite builds one.

    Not reachable from ``app/`` today: both ``regenerate_for_template`` callers
    pass a whole-schedule window.  That was equally true of the asymmetry
    ``regeneration_bound`` was written to close, which is the argument for
    grading it rather than trusting it.
    """

    def test_a_window_with_a_HOLE_sweeps_neither_side_of_the_hole(
        self, app, db, seed_user, seed_periods,
    ):
        """Periods 2 and 6 are the window; 3, 4 and 5 keep their rows."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            template = _make_template(seed_user, EVERY_PERIOD)
            recurrence_engine.generate_for_template(
                template,
                GenerationSchedule.for_pass(
                    BalanceContext.build(seed_user["user"].id),
                ),
                scenario_id,
            )
            db.session.flush()
            assert len(_rows(template, scenario_id)) == 10

            # The rule is CLEARED, so every row the pass can see is an orphan
            # and the RETIRE branch is what decides.  That makes the domain
            # visible: exactly the rows it reaches are the rows that go.
            db.session.delete(template.recurrence_rule)
            db.session.flush()
            db.session.refresh(template)

            holed = {seed_periods[2].id, seed_periods[6].id}
            recurrence_engine.regenerate_for_template(
                template,
                GenerationSchedule.for_period_ids(
                    BalanceContext.build(seed_user["user"].id), holed,
                ),
                scenario_id,
            )
            db.session.flush()

            survivors = {
                row.pay_period_id
                for rows in _rows(template, scenario_id).values()
                for row in rows
            }
            assert survivors == {
                period.id for period in seed_periods
            } - holed, (
                "a sweep bounded by the window's OPENING rather than shaped by "
                "the window itself takes periods 3, 4 and 5 with it -- they "
                "sit inside the hole and no pass would recreate them"
            )
