"""Which side rests on an assumption, a walk not performed, and the runs the report carries."""

from datetime import date, timedelta
from decimal import Decimal

from app.services import cash_ledger
from app.services.scenario_resolver import get_baseline_scenario
from tests.test_services.test_statement_import.test_anchor import _seed_import

from ._helpers import _FILE_CHAIN, _UNCORROBORATED, _ZERO, _settled, _agreement, _day


class TestWhichSideRestsOnAnAssumption:
    """Ruling R-GF, finding N-342."""

    def test_no_anchor_means_no_bank_balance_and_no_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """The state BOTH of the developer's real imports are in.

        The movement half still answers -- it needs no anchor -- which is why
        the report is worth rendering at all on an account whose files state no
        balance.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, date(2026, 3, 2))

            assert not agreement.anchored
            assert [run.anchor for run in agreement.runs] == [None]
            assert row.bank_balance is None
            assert row.gap is None
            assert agreement.constant_offset is None
            assert row.residue == Decimal("60.00")

    def test_a_CONSTANT_offset_is_reported_when_every_movement_agrees(
        self, app, seed_user, seed_periods, db,
    ):
        """A level wrong by K shifts every day equally; movements stay right.

        **Two days that both move, not one.**  Its first form seeded a single
        day, where "every gap is equal" is satisfied vacuously and the
        assertion could not distinguish a constant offset from there being
        nothing to be constant across -- and the population that hits that is
        exactly N-342's, an owner importing a statement the week they start
        recording.  Measured by adversarial review 2026-08-24.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = walk.anchor_facts[0].observed_on
            first, second = (
                opening + timedelta(days=1), opening + timedelta(days=2),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=second, evidence=_UNCORROBORATED,
                lines=[(first, "-40.00"), (second, "-10.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", first,
            )
            _settled(
                db, seed_user, seed_periods[0], "Coffee", "10.00", second,
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) >= 2
            assert agreement.disagreeing == []
            assert agreement.constant_offset is not None
            assert agreement.constant_offset != _ZERO
            (run,) = agreement.runs
            assert run.anchor.evidence is _UNCORROBORATED

    def test_ONE_compared_day_cannot_demonstrate_a_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the vacuous case.

        One day satisfies "every gap is equal" whatever the gap is, so
        reporting it as the signature of a wrong starting figure is an
        inference from a single observation -- rendered on screen as *"Every
        compared day is out by exactly $X"*.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            only = walk.anchor_facts[0].observed_on + timedelta(days=1)
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=only, evidence=_UNCORROBORATED,
                lines=[(only, "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", only,
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) == 1
            assert agreement.constant_offset is None


class TestThePageCannotClaimAWalkItDidNotPerform:
    """A derivation named over a column of dashes."""

    def test_days_in_the_GAP_between_two_anchored_runs_are_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """Reproduced by adversarial review 2026-08-24; half of it since fixed.

        A ``file_chain`` anchor in an old run was the STRONGEST, so it was
        the one walked from for the whole account -- and it reached nothing
        in a later, disconnected run, including that run's own anchored day
        whose crossing is empty (finding **N-343**).  Since plan step
        ``balance:X-bj-1b`` (ruling **R-BAL63**) each run walks from its own
        anchor, so the March day is PRICED -- the developer flipped this
        assertion with that ruling -- and what remains unpriced is the gap
        between the runs, which the report must still count rather than name
        a figure over dashes.  The day BEFORE the March run's first line is
        reached from the March anchor, so the gap counts 02-01..03-13.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 20), "-5.00")],
                period=(date(2026, 3, 15), date(2026, 3, 20)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.anchored
            assert [run.anchor.day for run in agreement.runs] == [
                date(2026, 1, 31), date(2026, 3, 20),
            ]
            # 02-01..03-13 inclusive: 28 February days and 13 March days.
            assert agreement.unpriced_days == 41
            assert _day(agreement, date(2026, 3, 13)).bank_balance is None
            # The day before the March run's first line is the balance its
            # lines start from, walked back from the March anchor.
            assert _day(agreement, date(2026, 3, 14)).bank_balance == Decimal(
                "505.00",
            )
            # The later run's own anchored day, priced from its own anchor.
            assert _day(agreement, date(2026, 3, 20)).bank_balance == Decimal(
                "500.00",
            )

    def test_an_account_with_NO_anchor_reports_zero_unpriced_days(
        self, app, seed_user, seed_periods, db,
    ):
        """One absence must not be reported as two.

        With no anchor the page already says no statement places a balance;
        adding "232 days not priced" beside it would name the same fact twice
        and read as a second, separate problem.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert not agreement.anchored
            assert agreement.unpriced_days == 0


class TestTheReportCarriesTheRuns:
    """Plan step ``balance:X-bj-1b``: the runs are the ONE home of coverage.

    Ruling **R-BAL65**: ``BankAgreement.runs`` carries every recorded run
    with its anchor and checkpoints, and the per-day bank balances are each
    run's own walk.  The ``imports`` list derived from the runs was deleted at
    plan step balance:X-f3c-2b-2c, which removed its last reader in ``app/``,
    so the spans are read off the runs themselves.
    """

    def test_runs_carry_each_anchor_and_each_span(
        self, app, seed_user, seed_periods, db,
    ):
        """Two anchored runs: two anchors, two spans, and one source of both.

        The hero (``headline``) walks back to the latest day both records
        state a balance for, which is now the later run's own anchored day
        rather than the older run's, and the standing gap on the span's last
        day is a figure.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
                file_name="january.csv",
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 15), "50.00"),
                       (date(2026, 3, 20), "-5.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert [
                (run.first_day, run.last_day) for run in agreement.runs
            ] == [
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 3, 15), date(2026, 3, 20)),
            ]
            assert [run.anchor.file_name for run in agreement.runs] == [
                "january.csv", "march.csv",
            ]
            assert agreement.headline.day == date(2026, 3, 20)
            assert agreement.headline.bank_balance == Decimal("500.00")
            # The level on the span's last day: the books less the bank's
            # 500.00 there, a figure now rather than the ``None`` the
            # account-wide walk left on every day it could not reach.
            assert agreement.standing_gap == (
                _day(agreement, date(2026, 3, 20)).app_balance
                - Decimal("500.00")
            )

    def test_a_checkpoint_rides_on_its_run_and_moves_no_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The disagreeing statement is listed; the column is the anchor's walk."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 5), evidence=_FILE_CHAIN,
                file_name="ytd.csv",
                lines=[(date(2026, 3, 1), "100.00"),
                       (date(2026, 3, 3), "-40.00"),
                       (date(2026, 3, 5), "25.00")],
            )
            _seed_import(
                db, seed_user["account"], stated="1075.00",
                effective_on=date(2026, 3, 3), evidence=_UNCORROBORATED,
                file_name="guessed.csv", lines=[],
                period=(date(2026, 3, 1), date(2026, 3, 3)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            (run,) = agreement.runs
            (checkpoint,) = run.checkpoints
            assert checkpoint.file_name == "guessed.csv"
            assert checkpoint.difference == Decimal("-100.00")
            assert _day(agreement, date(2026, 3, 3)).bank_balance == Decimal(
                "975.00",
            )

    def test_a_run_with_no_level_counts_its_days_unpriced_beside_an_anchored_one(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling **R-BAL64** on the report: the count names both causes.

        February (02-20) is anchored; March 1..5 holds lines and no figure.
        Unpriced: the gap 02-21..02-28 (8 days) and March's 5 days, 13 --
        March's day-before-first-line among the gap's, since no anchor
        reaches it either.  (The service suite's today is 2026-03-20.)
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 2, 20), evidence=_UNCORROBORATED,
                file_name="february.csv",
                lines=[(date(2026, 2, 20), "-5.00")],
            )
            _seed_import(
                db, seed_user["account"], stated=None, file_name="march.csv",
                lines=[(date(2026, 3, 1), "50.00"),
                       (date(2026, 3, 5), "-20.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            february, march = agreement.runs
            assert february.anchor is not None
            assert march.anchor is None
            assert agreement.anchored
            assert agreement.unpriced_days == 13
            assert _day(agreement, date(2026, 2, 28)).bank_balance is None
            assert _day(agreement, date(2026, 3, 5)).bank_balance is None
