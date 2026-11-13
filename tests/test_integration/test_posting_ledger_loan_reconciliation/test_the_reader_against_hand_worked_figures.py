"""The loan's dated reader against hand-worked figures, and the read switch's production path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extensions import db as _db
from app.models.scenario import Scenario
from app.services import (
    balance_at,
    loan_anchor_service,
    loan_ledger,
    loan_loaders,
    pay_period_write,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.installment_calendar import monthly_due_date
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from tests._ledger_reads import posted_loan_balance_map
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    last_covered_day,
    rhythm_of,
)

from ._helpers import (
    _ANCHOR_BALANCE,
    _P1,
    _P2,
    _SCHEDULED_PI,
    _ONE_SCHEDULED_MONTH,
    _TWO_SCHEDULED_MONTHS,
    _ORIGINATION_BEFORE_P1,
    _AS_OF,
    _make_loan,
    _confirmed_rows_at,
    _resolved_at,
    _settle,
    _ledger_balance,
    _posted_balance,
    _assert_loan_reconciles,
)


def _seed_boundary_loan(bare_user):
    """Set up a fresh user with periods straddling 2025-12-31 and a loan.

    The year-boundary fixture (extracted so the boundary test does not carry the
    setup as a dozen locals).  ``seed_periods`` locks its owner to 2026 and
    ``generate_pay_periods`` rejects backfilling earlier periods, so a
    boundary-straddling window needs a periodless ``bare_user``.  Generates six
    biweekly periods from 2025-12-25 (so ``periods[0]`` straddles the year end and
    ``periods[2]`` is a distinct January month), a baseline scenario, a Checking
    account to pay from, and an origination-only $100,000 loan originated a month
    before the window (both payments post-origination -- a clean sum with no
    anchor-reset subtlety; a 360-month term so it never pays off here).

    Args:
        bare_user: The ``bare_user`` fixture dict (a user with no periods).

    Returns:
        ``(loan, ctx, checking, periods)`` -- the loan account, a seed-user-shaped
        context dict (``user`` + baseline ``scenario``) the transfer/loan helpers
        accept, the Checking account to pay from, and the ordered pay periods.
    """
    user_id = bare_user["user"].id
    periods = pay_period_write.record_paydays(
        user_id=user_id, first_payday=date(2025, 12, 25),
        num_periods=6, rhythm=rhythm_of(14),
    )
    _db.session.flush()
    scenario = Scenario(user_id=user_id, name="Baseline", is_baseline=True)
    _db.session.add(scenario)
    _db.session.flush()
    ctx = {"user": bare_user["user"], "scenario": scenario}
    checking = create_account_of_type(ctx, _db.session, "Checking", "Checking")
    # Originated the month before the first payment's 2026-01-01 installment
    # (plan step recurrence:R16-c-2, ruling R-R101): every contractual
    # installment from origination is charged now, so the 2025-11-01 it
    # carried until then left the 2025-12-01 installment unpaid ahead of it.
    loan = create_loan_account(
        ctx, _db.session, name="Boundary Loan",
        principal=Decimal("100000.00"), rate=Decimal("0.06000"),
        origination_date=date(2025, 12, 1), term=360,
    )
    return loan, ctx, checking, periods


# ---------------------------------------------------------------------------
# 7. The dated posting window against hand-worked figures (plan 4-commit-6)
# ---------------------------------------------------------------------------


class TestDatedReaderAgainstHandWorkedFigures:
    """The dated posting window reads back the balance worked by hand.

    Sections 1-6 pin the LEDGER through the read's unbounded net of the loan's
    own line.  This section pins the ``entry_date``-BOUNDED read of those same
    lines -- at a point in time and at every period boundary -- against
    literals with their arithmetic shown.  It ran beside the old schedule
    replay until plan task plan#3384 (ruling plan#3383): the replay is deleted
    with walk 3, and a literal sees a bug the reader and a second producer
    might have shared, which the replay could not.

    Non-duplicative with the ``TestPostedLoanBalanceSums`` UNIT tests: those pin
    the posted sums on the unit fixture; this drives the real settle chokepoint
    and reads every period of the window.  The ``+$10`` interest injection
    (``test_walk_interest_injection_fails_the_value_checks_not_the_sweep``)
    moves every value pinned here by exactly $10.
    """

    def test_scalar_reader_reads_the_hand_worked_balance_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On schedule, the reader's point-in-time balance is the hand-worked 99,001.12.

        Arithmetic (:data:`_SCHEDULED_PI`): interest round(100000.00 * 0.005) =
        500.00, principal 1498.88 - 500.00 = 998.88, balance 99,001.12 -- not
        the untouched $100,000 anchor.  The unbounded read of the same lines
        agrees, since no line is dated past today.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            db.session.commit()

            reader = _posted_balance(loan.id, scenario_id)
            assert reader == _ONE_SCHEDULED_MONTH
            assert reader == _ledger_balance(loan.id, scenario_id)

    def test_period_map_reads_the_hand_worked_balance_across_the_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-period map steps down as each payment lands, then holds.

        Two on-schedule payments, in period 1 (starts 2026-01-16, due 02-01) and
        period 3 (starts 2026-02-13, due 03-01), each settled on its period's
        start.  The map reads each period at its END (:data:`_SCHEDULED_PI` has
        the arithmetic):

          * period 0 (2026-01-02 .. 01-15): the opening and the 2026-01-10
            true-up, no payment -> 100,000.00;
          * periods 1-2: the first payment -> 99,001.12;
          * periods 3-9: both payments -> 97,997.25, carried flat to the end of
            the window, where no later payment is posted.

        Every anchor precedes the read window's second period, so no period
        shows a pre-true-up balance.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            _settle(seed_user, loan, seed_periods[_P2], amount=_SCHEDULED_PI)
            db.session.commit()

            balance_map = posted_loan_balance_map(loan.id, scenario_id, seed_periods)
            expected = (
                [_ANCHOR_BALANCE]
                + [_ONE_SCHEDULED_MONTH] * (_P2 - _P1)
                + [_TWO_SCHEDULED_MONTHS] * (len(seed_periods) - _P2)
            )
            assert [balance_map[period.id] for period in seed_periods] == expected

    def test_an_early_settled_payment_posts_its_split_at_settle(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled a month before its installment keeps the ledger exact.

        The R1 regression lock for the 2026-07-02 review's H2 ("early settle,
        then time passes"): with today re-frozen MID-window (2026-02-10), the
        2026-03-01 installment is paid on 2026-01-30 -- a month EARLY.  Its
        Step-2 cash entry books from the settle, and its split correction with
        it (the store posted both in the SAME moment since R1), so from the
        settle on the linked ledger nets to the REAL principal, not the raw
        cash.  On the pre-R1 stored ledger the map read LOW by the payment's
        full interest from the settle forward until the next loan write
        re-posted it; the per-period figures below catch a read that books the
        raw cash the same way.

        **"Early" is measured against the INSTALLMENT here, which is what a
        borrower means by paying early.**  Until 2026-08-04 this fixture instead
        settled the payment on a pay-period start that had not arrived: a settle
        dated three weeks in its own FUTURE, which ruling **R-EJ** now refuses at
        the write door and which the app never produced.

        Arithmetic (:data:`_SCHEDULED_PI`), the map read at each period's END:

          * period 0 (2026-01-02 .. 01-15): no payment -> 100,000.00;
          * period 1 (2026-01-16 .. 01-29): P1, the February installment --
            interest 500.00, principal 998.88 -> 99,001.12;
          * periods 2-9 (from 2026-01-30): the early payment too, the March
            installment -- interest round(99001.12 * 0.005) = 495.01, principal
            1,003.87 -> 97,997.25.  The raw cash with no interest backed out
            would read 99001.12 - 1498.88 = 97,502.24 here instead.

        The scalar at the frozen today reads 97,997.25 as well, and the full
        reconciliation sweep runs last -- completeness covers the early-settled
        payment (its correction books with its cash, from the settle).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            # The EARLY payment, and its three dates are deliberately distinct:
            # the cash moves 2026-01-30, the pay period that books it is the one
            # CONTAINING that day, and the installment it satisfies is
            # 2026-03-01 -- still unpaid and still in the future at the frozen
            # today.  The cash day is a period START, so each period's figure
            # is one value: nothing is dated strictly INSIDE a period.
            early_period = next(
                period for period in seed_periods
                if period.start_date <= frozen <= last_covered_day(period)
            )
            early = _settle(
                seed_user, loan, early_period, amount=_SCHEDULED_PI,
                settled_on=early_period.start_date,
            )
            # The installment this cash paid.  Set explicitly for the same
            # reason ``TestLatePaidPaymentDating`` does: the pay period governs
            # the CASH and the stored due date governs the INSTALLMENT, and
            # re-deriving one from the other is the defect that class pins.
            # On the transfer -- see ``_settle_late``.
            early.due_date = date(2026, 3, 1)
            db.session.commit()
            # The premise: the money HAS moved (its day is at or before today),
            # the installment it paid is NOT yet due, and it is period 2.
            assert early_period.start_date <= frozen
            assert early.due_date > frozen
            assert early_period.id == seed_periods[2].id

            balance_map = posted_loan_balance_map(loan.id, scenario_id, seed_periods)
            expected = (
                [_ANCHOR_BALANCE, _ONE_SCHEDULED_MONTH]
                + [_TWO_SCHEDULED_MONTHS] * (len(seed_periods) - 2)
            )
            assert [balance_map[period.id] for period in seed_periods] == expected
            assert _posted_balance(
                loan.id, scenario_id, frozen,
            ) == _TWO_SCHEDULED_MONTHS

            # The sweep: identities, trial balance, and the completeness
            # guarantee that now covers the early settle.
            _assert_loan_reconciles(loan, scenario_id)

    def test_reader_includes_the_pre_trueup_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-true-up payment is summed by the reader, not excluded -- the verified balance.

        Genesis retires the read-switch boundary the prior draft carved out: the
        reader sums EVERY payment from origination with no post-anchor filter, so a
        payment made BEFORE the latest anchor is absorbed by the anchor correction,
        never double-counted.  With the true-up at 2026-02-15, P1 (due 02-01) is
        pre-true-up; the reader still reads the verified $100,000 true-up (which
        subsumes P1) exactly -- no pre-anchor pollution to exclude.

        The reader VALUE here is split-INVARIANT: a wrong interest split on the
        pre-true-up payment cancels against the true-up's ``owed_before`` on the
        one running-balance walk, so ``reader == 100000`` no matter what interest
        posted.  So the split is pinned DIRECTLY (as the Section-1 sibling
        ``test_pre_anchor_payment_is_correctly_summed_under_genesis`` does) -- else
        the ``+$10`` injection would slip past this test.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # True-up AFTER P1's 2026-02-01 due date, so P1 is pre-true-up.
            loan = _make_loan(
                seed_user, anchor_date=date(2026, 2, 15),
                origination_date=_ORIGINATION_BEFORE_P1,
            )
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()

            # Pin the split value directly (the reader value below is
            # split-invariant): the pre-true-up payment splits on the $250,000
            # origination balance -> interest round(250000 * 0.005) = 1250.00.
            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, scenario_id,
            )
            assert splits[0].interest == Decimal("1250.00")

            # The pre-true-up payment is summed AND absorbed by the true-up.
            assert _posted_balance(loan.id, scenario_id) == _ANCHOR_BALANCE

    def test_reader_reflects_a_mid_life_true_up_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A user balance true-up moves the reader to the verified value.

        After a $1,000 payment the reader owes 99,500.00.  The user reconciles the
        statement and asserts the real balance is $95,000 on 2026-03-01 -- an
        append-only true-up recorded through the real chokepoint
        (``loan_anchor_service.apply_loan_anchor_true_up``), not an edit, which
        the ledger books as a correction.  The reader jumps to 95,000.00 (the true-up's ``owed_before`` absorbs the earlier
        payment) -- distinct from both the pre-true-up 99,500 and the 100,000
        anchor, so the reader demonstrably reflects the correction.  The whole
        ledger still reconciles (the correction is a balanced linked + equity
        pair).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1], amount=Decimal("1000.00"))
            db.session.commit()
            # Pre-true-up: after the $1,000 payment the reader owes 99,500.00
            # (100000 - (1000 cash - 500 interest)).
            assert _posted_balance(loan.id, scenario_id) == Decimal("99500.00")

            outcome = loan_anchor_service.apply_loan_anchor_true_up(
                account=loan, anchor_balance=Decimal("95000.00"),
                anchor_date=date(2026, 3, 1),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            assert _posted_balance(loan.id, scenario_id) == Decimal("95000.00")
            _assert_loan_reconciles(loan, scenario_id)

    def test_reader_bounds_confirmed_postings_at_the_year_boundary(
        self, app, db, bare_user,
    ):
        """The reader's settled-date bound separates December from January.

        A pay period straddling 2025-12-31 (start 2025-12-25) holds a December
        payment settled on its 12-25 start; a later period (start 2026-01-22) holds
        a January one settled on its 01-22 start.  The reader bounds by each
        payment's SETTLED date (step C2), so as of 2025-12-31 it counts the
        December payment (settled 12-25 <= 12-31) but NOT the January one (settled
        01-22 > 12-31).  As of a later date it counts both.  This proves the date
        bound holds across a calendar-year rollover, the foundation the year-end /
        tax surfaces (plan 3.6 / commit 10) build on.

        Arithmetic: $100,000 at 6% over 360 months has the contract P&I
        100000 * 0.005 / (1 - 1.005 ** -360) = 599.55, paid both times:

          * December (due 2026-01-01): interest round(100000.00 * 0.005) =
            500.00, principal 99.55 -> 99,900.45;
          * January (due 2026-02-01): interest round(99900.45 * 0.005) =
            499.50, principal 100.05 -> 99,800.40.

        Uses ``bare_user`` via ``_seed_boundary_loan``: ``seed_periods`` locks its
        owner to 2026 and ``generate_pay_periods`` rejects backfilling earlier
        periods, so a boundary-straddling window needs a periodless user.
        """
        with app.app_context():
            loan, ctx, checking, periods = _seed_boundary_loan(bare_user)
            scenario_id = ctx["scenario"].id
            contract_pi = Decimal("599.55")

            # periods[0] (2025-12-25 .. 2026-01-07) straddles 12-31; periods[2]
            # (2026-01-22 .. 2026-02-04, due 02-01) is a distinct January month.
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[0], amount=contract_pi,
                settled_on=periods[0].start_date,
            )
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[2], amount=contract_pi,
                settled_on=periods[2].start_date,
            )
            db.session.commit()

            assert _posted_balance(
                loan.id, scenario_id, date(2025, 12, 31),
            ) == Decimal("99900.45")
            assert _posted_balance(
                loan.id, scenario_id, _AS_OF,
            ) == Decimal("99800.40")
            _assert_loan_reconciles(loan, scenario_id)

    def test_reader_is_scenario_scoped_and_an_unpaid_one_reads_its_anchor(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader reads each scenario's own balance, and never another's.

        On-schedule payments net independently per scenario: the baseline gets one
        (99,001.12) and the what-if two (97,997.25; :data:`_SCHEDULED_PI` has the
        arithmetic), the what-if's second payment lowering only the what-if
        (neither leaks into the other).  A THIRD scenario holds no payment.  The
        worked-out read answers the loan's own anchor there, because it answers
        whichever scenario it is asked (``ledger_lines._worked_out``; no route
        creates a second scenario): not a misleading $0 or another scenario's
        balance (the M2 latent multi-scenario guard, plan Section 4).
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            unopened = Scenario(
                user_id=seed_user["user"].id, name="Unopened", is_baseline=False,
            )
            db.session.add_all([whatif, unopened])
            db.session.commit()

            loan = _make_loan(seed_user)
            # Baseline: one on-schedule payment.  What-if: two (a second period).
            _settle(
                seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI,
                scenario=baseline,
            )
            _settle(
                seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI,
                scenario=whatif,
            )
            _settle(
                seed_user, loan, seed_periods[_P2], amount=_SCHEDULED_PI,
                scenario=whatif,
            )
            db.session.commit()

            # Each scenario reads its OWN balance; neither leaks.
            assert _posted_balance(loan.id, baseline.id) == _ONE_SCHEDULED_MONTH
            assert _posted_balance(loan.id, whatif.id) == _TWO_SCHEDULED_MONTHS
            # A scenario holding no payment: the read answers the
            # $100,000.00 anchor no payment has reduced -- not the baseline's
            # balance or a bare $0.
            assert _posted_balance(loan.id, unopened.id) == Decimal("100000.00")
            _assert_loan_reconciles(loan, baseline.id)
            _assert_loan_reconciles(loan, whatif.id)

    def test_biweekly_due_month_collision_charges_the_month_once(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one due month: the month's interest is charged once.

        A biweekly cadence sometimes lands two monthly due dates in one calendar
        month.  Since plan step **X-au-g-2c-3b-2** the genesis walk charges that
        month ONCE -- interest is the price of time, not of a transaction -- so
        the second payment clears no fresh charge and pays pure principal.  Until
        plan task plan#3384 this case also pinned the old schedule replay's
        divergence by mechanism (it charged a month per payment RECORD and
        redistributed the second to March); that replay is deleted with walk 3
        (ruling plan#3383), so what stays is the ledger's own figure.

        ``seed_periods[1]`` (starts 2026-01-16) and ``seed_periods[2]`` (starts
        2026-01-30) both have monthly due date 2026-02-01 (payment_day=1) -- a
        February collision.  Each pays the contract's $1,498.88, by hand:

          * the first: interest round(100000.00 * 0.005) = 500.00, principal
            998.88 -> 99,001.12;
          * the second: February is already charged, so no interest, principal
            1,498.88 -> 99001.12 - 1498.88 = 97,502.24.  Charging February twice
            would leave 97,997.25 instead.

        The confirmed rows keep BOTH at the true February due date, and the
        per-period map reads 99,001.12 at period 1's end and 97,502.24 from
        period 2's.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Collision Loan")
            params = loan_loaders.load_loan_params(loan.id)

            # The collision is real: both payments' true monthly due date is 02-01.
            assert monthly_due_date(
                seed_periods[1].start_date, params.payment_day,
            ) == date(2026, 2, 1)
            assert monthly_due_date(
                seed_periods[2].start_date, params.payment_day,
            ) == date(2026, 2, 1)

            _settle(seed_user, loan, seed_periods[1], amount=_SCHEDULED_PI)
            _settle(seed_user, loan, seed_periods[2], amount=_SCHEDULED_PI)
            db.session.commit()

            assert _ledger_balance(loan.id, scenario_id) == Decimal("97502.24")
            assert _posted_balance(loan.id, scenario_id) == Decimal("97502.24")
            _assert_loan_reconciles(loan, scenario_id)

            # The confirmed view keeps BOTH rows at the true February due date.
            assert [
                row.payment_date
                for row in _confirmed_rows_at(loan.id, scenario_id, _AS_OF)
            ] == [date(2026, 2, 1), date(2026, 2, 1)]

            # The per-period buckets: each paydown in its own pay period.
            balance_map = posted_loan_balance_map(loan.id, scenario_id, seed_periods)
            assert balance_map[seed_periods[1].id] == _ONE_SCHEDULED_MONTH
            assert balance_map[seed_periods[2].id] == Decimal("97502.24")


class TestReadSwitchProductionPath:
    """The production loan read path returns the LEDGER-true balance (the flip).

    Every displayed loan balance flows through the ``balance_at`` seam scalar
    (the fold over the loan's recorded events; the seeded resolver carried a
    ledger-fed ``current_balance`` until plan step D2a deleted the field).
    Before the read switch the displayed balance replayed the SCHEDULED payment
    from the anchor and dropped the cash, so off-schedule it disagreed with the
    posted ledger.  Now the production balance EQUALS the ledger / reader, and
    both equal the balance worked by hand from the actual cash -- which off
    schedule is a figure a money-blind replay could not produce.

    This is the read switch itself, pinned end-to-end at the service the
    surfaces call.  It graded the production path against the un-seeded replay
    as well until plan task plan#3384 (ruling plan#3383), ahead of plan task
    plan#3385 deleting that replay with walk 3; the hand-worked literals carry
    the check now.
    """

    def test_production_path_reads_the_ledger_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """What the seam scalar owes == the ledger / reader == the hand-worked figure.

        Two identical $100,000 @ 6% loans (interest 500.00).  An EXTRA $2,000
        payment books real principal 2000 - 500 = 1,500 -> the production path
        owes 98,500.00; a SHORT $1,000 payment books 1000 - 500 = 500 ->
        99,500.00.  A replay of the contract's $1,498.88 would owe 99,001.12 on
        both (:data:`_SCHEDULED_PI`), so each literal is one only the actual cash
        produces.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            extra_loan = _make_loan(seed_user, name="Extra Prod Loan")
            short_loan = _make_loan(seed_user, name="Short Prod Loan")

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            # The production read path every displayed loan balance flows
            # through: the balance_at seam scalar (the fold; the seeded
            # resolver's balance field died at plan step D2a).  The seam
            # reports the balance HELD -- negative for a loan, ruling R-CC47 --
            # so each figure below is what the loan OWES, read through
            # ``owed()``: the sign the ledger and the reader state.
            bctx = BalanceContext(
                user_id=extra_loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            extra_production = owed(
                balance_at.balance_at(extra_loan, bctx, _AS_OF),
            )
            short_production = owed(
                balance_at.balance_at(short_loan, bctx, _AS_OF),
            )

            # The flip: production == ledger == reader == the hand-worked figure.
            assert extra_production == Decimal("98500.00")
            assert extra_production == _ledger_balance(extra_loan.id, scenario_id)
            assert extra_production == _posted_balance(extra_loan.id, scenario_id)
            assert short_production == Decimal("99500.00")
            assert short_production == _ledger_balance(short_loan.id, scenario_id)
            assert short_production == _posted_balance(short_loan.id, scenario_id)

    def test_production_schedule_confirmed_rows_are_the_ledger_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The production schedule's confirmed rows ARE the ledger history (C11).

        The history read switch: ``resolve_loan_bundle``'s schedule -- the
        amortization table, the chart's history prefix, the date-precise
        ``balance_at`` walk -- carries the RECORD-derived confirmed rows, equal to
        the seam's own ``confirmed_view`` rows verbatim (the walk-built view since
        plan step E1d-b; the posted-ledger reader it replaced before that).
        Off-schedule (an EXTRA $2,000 payment on the $100,000 balance) the row
        shows the ACTUAL economics, by hand: interest 500.00, principal 2000 -
        500 = 1,500.00, balance 98,500.00.  The contract's split would be
        principal 998.88 and balance 99,001.12 (:data:`_SCHEDULED_PI`), so the
        row's figures are ones only the actual cash produces.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="History Flip Loan")
            _settle(
                seed_user, loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            db.session.commit()

            resolved = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            )
            assert resolved is not None, "configured loan must resolve"
            confirmed_rows = [r for r in resolved.state.schedule if r.is_confirmed]
            assert confirmed_rows == _confirmed_rows_at(
                loan.id, scenario_id, _AS_OF,
            )
            (row,) = confirmed_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.remaining_balance == Decimal("98500.00")

    def test_production_path_reads_the_hand_worked_balance_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On schedule the seam scalar owes the hand-worked 99,001.12, as the ledger does.

        Paying exactly the contract's P&I books interest 500.00 and principal
        998.88 (:data:`_SCHEDULED_PI`), so the production path and the ledger
        both owe 99,001.12.  This guards against the seam perturbing the
        on-schedule case (a regression that would show a loan card drifting
        from its schedule even when the user pays exactly).  The seam reports
        the balance HELD (negative for a loan, ruling R-CC47), so ``production``
        is what the loan owes, read through ``owed()``.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            _settle(seed_user, loan, seed_periods[_P1], amount=_SCHEDULED_PI)
            db.session.commit()

            bctx = BalanceContext(
                user_id=loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            production = owed(balance_at.balance_at(loan, bctx, _AS_OF))
            assert production == _ONE_SCHEDULED_MONTH
            assert production == _ledger_balance(loan.id, scenario_id)
