"""The loan reader run beside the resolver, and the read switch's production path."""

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
    loan_payment_service,
    loan_posting_service,
    loan_resolver,
    pay_period_write,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.installment_calendar import monthly_due_date
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from tests._test_helpers import (
    rhythm_of,
    amount_basis_for_scenario,
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    freeze_today,
    derived_span,
    last_covered_day,
    posted_loan_balance_at,
    posted_loan_balance_map,
)

from ._helpers import (
    _ANCHOR_BALANCE,
    _P1,
    _P2,
    _P3,
    _ORIGINATION_BEFORE_P1,
    _AS_OF,
    _make_loan,
    _confirmed_rows_at,
    _resolved_at,
    _settle,
    _resolver_balance,
    _ledger_balance,
    _posted_balance,
    _posted_period_map,
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
# 7. The dated posting-window parallel run (plan 4-commit-6)
# ---------------------------------------------------------------------------


class TestReaderParallelRunAgainstResolver:
    """The dated posting window reads back == the resolver.

    Sections 1-6 pin the posted LEDGER against the resolver through the test's OWN
    unbounded ``-(sum of linked postings)`` query.  This section pins the
    ``entry_date``-BOUNDED read of those same postings -- at a point in time and
    at every period boundary -- as a THIRD derivation run in the SAME test as the
    resolver.  On an on-schedule
    payment the two must agree to the penny; off-schedule they must diverge by
    exactly the extra / short principal (the reader books the REAL principal from
    the cash, the resolver only the SCHEDULED principal).

    Non-duplicative with the ``TestPostedLoanBalanceSums`` UNIT tests: those pin
    the posted sums against hand-computed literals; this pins them against the
    resolver, an independent producer that shares none of the ledger's code path
    and never reads it -- so a ledger bug the literal happened to share is still
    caught.  The ``+$10`` interest injection (module docstring) fails every
    test here that asserts a value, exactly as it fails Sections 1-3.
    """

    def test_scalar_reader_matches_resolver_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On-schedule, the reader's point-in-time balance == the resolver's.

        Paying exactly the scheduled monthly P&I is on-schedule (the loan carries
        no escrow, so cash == P&I), so the reader's real principal (cash - interest)
        equals the resolver's scheduled principal (P&I - interest) and the two
        balances -- derived by disjoint code paths from the same $100,000 anchor --
        agree to the penny.  Non-vacuity: the balance dropped below the anchor by
        exactly the real principal (scheduled P&I - round(100000 * 0.005) = P&I -
        500.00 interest), and the production reader equals the test's own
        independent linked-net query (a genuine third opinion, not the resolver).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # Three independent producers agree: the production reader, the resolver
            # replay, and the test's own independent linked-net query.
            assert reader == resolver, f"reader {reader} != resolver {resolver}"
            assert reader == _ledger_balance(loan.id, scenario_id)
            # Non-vacuity: the balance is the $100,000 anchor less the real
            # principal (P&I - 500.00 interest), not the untouched anchor.
            assert reader == _ANCHOR_BALANCE - (scheduled_pi - Decimal("500.00"))

    def test_period_map_matches_resolver_across_the_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-period map == the resolver at every period start (walk + tail).

        Two on-schedule payments (periods 1 and 3, due 02-01 / 03-01) net in as
        their pay periods begin.  The reader map keys each period by its START, and
        the resolver caps its replay by the SAME pay-period start
        (``rate_period_engine.replay_schedule``), so ``map[P]`` equals the resolver
        resolved as of ``P.start_date`` for every period here -- through the
        stepping-down region (periods 0-3) and the carried-flat tail (periods 4-9,
        where ``current_balance`` counts only confirmed payments, so it too carries
        flat).  Non-vacuity: the map is not constant -- it steps strictly down as
        each payment lands, then holds.

        The equivalence is exact because every anchor precedes the read window: the
        opening (clamped to period 0) and the SPLIT_LOAN true-up (2026-01-10, in
        period 0) both net in at period 0, so no period shows a pre-true-up
        balance.  The map across a MID-LIFE true-up -- where the reader keeps
        pre-true-up history while the resolver reseeds from the later anchor, a
        deliberate divergence -- is the per-period read switch's concern (plan
        Section 9), not this gate's.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[_P2], amount=scheduled_pi)
            db.session.commit()

            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            # Per-period parallel run: the map's value for each period equals the
            # resolver resolved as of that period's START (both select confirmed
            # postings / payments by pay-period start <= the date).
            for period in seed_periods:
                resolver_at_start = _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                )
                assert balance_map[period.id] == resolver_at_start, (
                    f"period {derived_span(period).period_index} "
                    f"(start {period.start_date}): "
                    f"map {balance_map[period.id]} != resolver {resolver_at_start}"
                )
            # Non-vacuity: not trivially constant -- 100,000 before any payment,
            # strictly down as P1 (period 1) then P2 (period 3) net in, then flat.
            assert balance_map[seed_periods[0].id] == _ANCHOR_BALANCE
            assert (
                balance_map[seed_periods[_P1].id]
                < balance_map[seed_periods[0].id]
            )
            assert (
                balance_map[seed_periods[_P2].id]
                < balance_map[seed_periods[_P1].id]
            )
            # The tail carries the last confirmed balance flat (no later payment).
            assert (
                balance_map[seed_periods[9].id]
                == balance_map[seed_periods[_P2].id]
            )

    def test_early_settled_payment_keeps_the_parallel_run_exact(
        self, app, db, monkeypatch, seed_user, seed_periods,
    ):
        """A payment settled a month before its installment never desyncs the ledger.

        The R1 regression lock for the 2026-07-02 review's H2 ("early settle,
        then time passes"): with today re-frozen MID-window (2026-02-10), the
        2026-03-01 installment is paid on 2026-01-30 -- a month EARLY.  Its
        Step-2 cash entry posts at settle -- and since R1 its split correction
        posts in the SAME moment -- so when that installment later comes due,
        the linked ledger nets to the REAL principal, not the raw cash.  Pinned
        via the independent resolver: the map equals the un-seeded replay at
        EVERY period start, through the early-settled period and the
        carried-flat tail (on-schedule cash, so the two producers must agree to
        the penny).  On the pre-R1 ledger the map read LOW by the payment's
        full interest from the settle forward until the next loan write --
        exactly what this parallel run catches.

        **"Early" is measured against the INSTALLMENT here, which is what a
        borrower means by paying early -- and it is the widest early settle
        BOTH producers can see.**  Until 2026-08-04 this fixture instead settled
        the payment on a pay-period start that had not arrived: a settle dated
        three weeks in its own FUTURE, which ruling **R-EJ** now refuses at the
        write door and which the app never produced.  Moving the CASH before its
        own pay period begins is a different case and the app DOES produce it:
        the ledger dates the entry by the day the money moved, while the
        resolver's replay/projection cut keys on the pay-period start, so the
        two diverge there BY CONSTRUCTION.  That is a producer defect, not a
        property of this fixture -- finding **N-187**, owned by plan step
        **X-an** -- so this test does not encode it, and re-dating this fixture
        into it would report the defect as an expectation.

        Today's displays stay untouched: the scalar reader at the frozen today
        equals the resolver at today.  The full reconciliation sweep runs last
        -- completeness now covers the early-settled payment (its correction
        must exist at settle).
        """
        with app.app_context():
            frozen = date(2026, 2, 10)
            freeze_today(monkeypatch, frozen)
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            monthly_pi = _resolved_at(
                loan.id, scenario_id, frozen,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=monthly_pi)
            # The EARLY payment, and its three dates are deliberately distinct:
            # the cash moves 2026-01-30, the pay period that books it is the one
            # CONTAINING that day (so neither producer is asked to guess), and
            # the installment it satisfies is 2026-03-01 -- still unpaid and
            # still in the future at the frozen today.
            #
            # The cash day is a period START, not the frozen today, because the
            # assertion loop below compares a map read at each period's END
            # against the resolver read at that period's START: the two agree
            # only when nothing is dated strictly INSIDE a period.  A settle on
            # the frozen today lands mid-period and makes the map (which has
            # seen it) differ from the resolver (which has not) -- a divergence
            # in the FIXTURE's sampling, not in the producers being compared.
            early_period = next(
                period for period in seed_periods
                if period.start_date <= frozen <= last_covered_day(period)
            )
            early = _settle(
                seed_user, loan, early_period, amount=monthly_pi,
                settled_on=early_period.start_date,
            )
            # The installment this cash paid.  Set explicitly for the same
            # reason ``TestLatePaidPaymentDating`` does: the pay period governs
            # the CASH and the stored due date governs the INSTALLMENT, and
            # re-deriving one from the other is the defect that class pins.
            # On the transfer -- see ``_settle_late``.
            early.due_date = date(2026, 3, 1)
            db.session.flush()
            loan_posting_service.sync_loan_postings_all_scenarios(loan.id)
            db.session.commit()
            # The premise: the money HAS moved (its day is at or before today)
            # and the installment it paid is NOT yet due -- an early settle.
            assert early_period.start_date <= frozen
            assert early.due_date > frozen

            # The parallel run holds at EVERY period start -- including the
            # period the early installment comes due in and the tail after it,
            # where the pre-R1 ledger carried the raw cash (no interest backout)
            # and this assertion fails.
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            for period in seed_periods:
                resolver_at_start = _resolver_balance(
                    loan.id, scenario_id, period.start_date,
                )
                assert balance_map[period.id] == resolver_at_start, (
                    f"period {derived_span(period).period_index} "
                    f"(start {period.start_date}):"
                    f" map {balance_map[period.id]} != resolver "
                    f"{resolver_at_start}"
                )
            # Non-vacuity: the early-settled period genuinely steps the balance
            # down (the map is not trivially flat past P1).
            assert (
                balance_map[seed_periods[_P3].id]
                < balance_map[seed_periods[_P1].id]
            )

            # Today's scalar is untouched by the early settle: reader ==
            # resolver at the frozen today (both have seen the early cash).
            assert _posted_balance(
                loan.id, scenario_id, frozen,
            ) == _resolver_balance(loan.id, scenario_id, frozen)

            # The sweep: identities, per-entry balance, trial balance, and the
            # completeness guarantee that now covers the early settle.
            _assert_loan_reconciles(loan, scenario_id, frozen)

    def test_scalar_reader_diverges_by_the_exact_principal_delta_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """Off-schedule, the reader diverges from the resolver by exactly the delta.

        The reader books the REAL principal from the actual cash; the resolver
        replays only the SCHEDULED principal (it ignores the cash) and would need an
        anchor true-up to catch up.  On the $100,000 balance @ 6% (interest 500.00):
        an EXTRA $2,000 payment -> real principal 1,500, reader owes 98,500.00, LESS
        than the resolver by exactly (cash 2,000 - scheduled P&I); a SHORT $1,000
        payment -> real principal 500, reader owes 99,500.00, MORE than the resolver
        by exactly (scheduled P&I - cash 1,000).  Two loans with identical params,
        so the same scheduled P&I governs both deltas.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            extra_loan = _make_loan(seed_user, name="Extra Loan")
            short_loan = _make_loan(seed_user, name="Short Loan")
            # Identical params -> identical scheduled P&I (rate-period level
            # payment, balance-independent), so one figure governs both deltas.
            monthly_pi = _resolved_at(
                extra_loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(
                seed_user, extra_loan, seed_periods[_P1], amount=Decimal("2000.00"),
            )
            _settle(
                seed_user, short_loan, seed_periods[_P1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            extra_reader = _posted_balance(extra_loan.id, scenario_id)
            extra_resolver = _resolver_balance(extra_loan.id, scenario_id, _AS_OF)
            short_reader = _posted_balance(short_loan.id, scenario_id)
            short_resolver = _resolver_balance(short_loan.id, scenario_id, _AS_OF)

            # Extra: real principal 2000 - 500 = 1500 -> 98,500.00, owing LESS than
            # the resolver by exactly (cash - scheduled P&I).
            assert extra_reader == Decimal("98500.00")
            assert extra_reader < extra_resolver
            assert extra_resolver - extra_reader == Decimal("2000.00") - monthly_pi
            # Short: real principal 1000 - 500 = 500 -> 99,500.00, owing MORE than
            # the resolver by exactly (scheduled P&I - cash).
            assert short_reader == Decimal("99500.00")
            assert short_reader > short_resolver
            assert short_reader - short_resolver == monthly_pi - Decimal("1000.00")

    def test_reader_includes_the_pre_trueup_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A pre-true-up payment is summed by the reader, not excluded -- == resolver.

        Genesis retires the read-switch boundary the prior draft carved out: the
        reader sums EVERY payment from origination with no post-anchor filter, so a
        payment made BEFORE the latest anchor is absorbed by the anchor correction,
        never double-counted.  With the true-up at 2026-02-15, P1 (due 02-01) is
        pre-true-up; the reader still reproduces the resolver's $100,000 anchor
        balance (which subsumes P1) exactly -- no pre-anchor pollution to exclude.

        The reader VALUE here is split-INVARIANT: a wrong interest split on the
        pre-true-up payment cancels against the true-up's ``owed_before`` on the
        one running-balance walk, so ``reader == 100000`` no matter what interest
        posted.  So the split is pinned DIRECTLY (as the Section-1 sibling
        ``test_pre_anchor_payment_is_correctly_summed_under_genesis`` does) -- else
        the ``+$10`` injection would slip past this test, which the class docstring
        claims it does not.
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

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The pre-true-up payment is summed AND absorbed by the true-up, so the
            # reader reproduces the resolver's anchor balance to the penny.
            assert reader == resolver
            assert reader == _ANCHOR_BALANCE  # 100000.00, no pre-anchor pollution

    def test_reader_reflects_a_mid_life_true_up_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A user balance true-up moves the reader to the verified value -- == resolver.

        After a $1,000 payment the reader owes 99,500.00.  The user reconciles the
        statement and asserts the real balance is $95,000 on 2026-03-01 -- an
        append-only true-up correction posted through the real chokepoint
        (``loan_anchor_service.apply_loan_anchor_true_up``), not an edit.  The reader
        jumps to 95,000.00 (the true-up's ``owed_before`` absorbs the earlier
        payment) and the resolver, reseeded from the new latest anchor, agrees --
        95,000 is distinct from both the pre-true-up 99,500 and the 100,000 anchor,
        so the reader demonstrably reflects the correction.  The whole ledger still
        reconciles (the correction is a balanced linked + equity pair).
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

            reader = _posted_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            # The reader jumps to the verified value; the resolver reseeds to it.
            assert reader == Decimal("95000.00")
            assert reader == resolver
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_bounds_confirmed_postings_at_the_year_boundary(
        self, app, db, bare_user,
    ):
        """The reader's settled-date bound separates December from January.

        A pay period straddling 2025-12-31 (start 2025-12-25) holds a December
        payment settled on its 12-25 start; a later period (start 2026-01-22) holds
        a January one settled on its 01-22 start.  The reader bounds by each
        payment's SETTLED date (step C2), so as of 2025-12-31 it counts the
        December payment (settled 12-25 <= 12-31) but NOT the January one (settled
        01-22 > 12-31) -- matching the resolver, which caps its replay at the same
        boundary.  As of a later date it counts both.  This proves the date bound
        (and the resolver parallel run) hold across a calendar-year rollover, the
        foundation the year-end / tax surfaces (plan 3.6 / commit 10) build on.

        Uses ``bare_user`` via ``_seed_boundary_loan``: ``seed_periods`` locks its
        owner to 2026 and ``generate_pay_periods`` rejects backfilling earlier
        periods, so a boundary-straddling window needs a periodless user.
        """
        with app.app_context():
            loan, ctx, checking, periods = _seed_boundary_loan(bare_user)
            scenario_id = ctx["scenario"].id
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # periods[0] (2025-12-25 .. 2026-01-07) straddles 12-31; periods[2]
            # (2026-01-22 .. 2026-02-04, due 02-01) is a distinct January month.
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[0], amount=scheduled_pi,
                settled_on=periods[0].start_date,
            )
            create_settled_transfer(
                ctx, db.session, checking, loan, periods[2], amount=scheduled_pi,
                settled_on=periods[2].start_date,
            )
            db.session.commit()

            year_end = date(2025, 12, 31)
            reader_dec = _posted_balance(loan.id, scenario_id, year_end)
            # As of Dec 31: only the December (straddling) payment has netted in --
            # 100,000 less its real principal (scheduled P&I - 500.00 interest).
            assert reader_dec == _resolver_balance(loan.id, scenario_id, year_end)
            assert reader_dec == Decimal("100000.00") - (
                scheduled_pi - Decimal("500.00")
            )
            # As of after both periods: both payments have netted in, still ==
            # resolver, and strictly below the Dec-31 balance (January lowered it).
            reader_both = _posted_balance(loan.id, scenario_id, _AS_OF)
            assert reader_both == _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert reader_both < reader_dec
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

    def test_reader_is_scenario_scoped_and_none_when_unopened(
        self, app, db, seed_user, seed_periods,
    ):
        """The reader reads each scenario's own balance, and None where unopened.

        On-schedule payments net independently per scenario: the baseline gets one
        (balance 100,000 - one principal) and the what-if two (100,000 - two
        principals), each reading its OWN balance == its OWN resolver, the what-if's
        second payment lowering only the what-if (neither leaks into the other).  A
        THIRD scenario the loan was never opened into has no OPENING posting, so the
        reader returns ``None`` -- routing a read-switch caller to needs-setup,
        never a misleading $0 or another scenario's balance (the M2 latent
        multi-scenario guard, plan Section 4).
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
            scheduled_pi = _resolved_at(
                loan.id, baseline.id, _AS_OF,
            ).state.monthly_payment
            # Baseline: one on-schedule payment.  What-if: two (a second period).
            _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
                scenario=baseline,
            )
            _settle(
                seed_user, loan, seed_periods[_P1], amount=scheduled_pi,
                scenario=whatif,
            )
            _settle(
                seed_user, loan, seed_periods[_P2], amount=scheduled_pi,
                scenario=whatif,
            )
            db.session.commit()

            baseline_reader = _posted_balance(loan.id, baseline.id)
            whatif_reader = _posted_balance(loan.id, whatif.id)
            # Each scenario reads its OWN balance == its own resolver.
            assert baseline_reader == _resolver_balance(
                loan.id, baseline.id, _AS_OF,
            )
            assert whatif_reader == _resolver_balance(loan.id, whatif.id, _AS_OF)
            # Isolation: the what-if's SECOND payment lowers only the what-if; the
            # baseline still reflects just its one payment (neither leaks).
            assert whatif_reader < baseline_reader
            assert baseline_reader == _ANCHOR_BALANCE - (
                scheduled_pi - Decimal("500.00")
            )
            # A scenario the loan was never opened into -> None (needs-setup),
            # never the baseline's balance or a bare $0.  Both readers agree.
            assert posted_loan_balance_at(
                loan.id, unopened.id, _AS_OF,
            ) is None
            assert posted_loan_balance_map(
                loan.id, unopened.id, seed_periods,
            ) is None
            _assert_loan_reconciles(loan, baseline.id, _AS_OF)
            _assert_loan_reconciles(loan, whatif.id, _AS_OF)

    def test_biweekly_due_month_collision_reconciles_and_only_row_dates_differ(
        self, app, db, seed_user, seed_periods,
    ):
        """Two payments in one due month: the two GENESIS producers agree, and
        the resolver's replay differs by exactly one month's interest.

        A biweekly cadence sometimes lands two monthly due dates in one calendar
        month.  Since plan step **X-au-g-2c-3b-2** the genesis walk charges that
        month ONCE -- interest is the price of time, not of a transaction -- so
        the second payment clears no fresh charge and pays pure principal.  The
        resolver's replay still charges a month per payment RECORD
        (``rate_period_engine.replay_schedule`` calls ``_replay_payment_row``
        once per record and applies the CONTRACTUAL P&I, not the actual cash),
        and it additionally redistributes the second record to the next month for
        display (``loan_payment_service._redistribute_to_distinct_months``).

        **So this test no longer asserts a three-way equality, and that is a
        RULING, not a regression** (developer, 2026-09-02).  The ledger and the
        posted reader still agree, as they must -- the postings are a projection
        of the one walk.  The resolver's replay is the FOURTH walk over a loan
        and the last one still charging per record; its balance is ALREADY dead
        in production (``loan_resolver._payoff._build_forward_inputs`` takes
        ``replay.balance_as_of`` only ``if confirmed_view is None``, and every
        production read supplies one since plan step E1d-b), so nothing rendered
        moves.  It is graded here on the unseeded path deliberately, as an
        independent oracle, and merging it into the one walk is its own arc.
        The gap is pinned by its MECHANISM below rather than by a figure.

        The per-period BALANCE divergence this test used to pin (review M7 /
        Step-4 note M2) is now CLOSED.  It existed because redistribution
        overwrote the shifted payment's ``payment_date`` -- the pay period the
        cash actually moved in -- with the invented next-month DUE date, which
        the replay then read as a pay-period start.  Its "has this period begun?"
        cap became ``2026-03-01 <= 2026-01-30`` (false), so the replay dropped a
        payment that had genuinely been made and the resolver read HIGHER than the
        ledger at period 2.  Redistribution now shifts only ``due_date`` and
        carries ``payment_date`` through untouched, so both producers bucket every
        paydown into its TRUE pay period and the per-period maps agree.

        ``seed_periods[1]`` (starts 2026-01-16) and ``seed_periods[2]`` (starts
        2026-01-30) both have monthly due date 2026-02-01 (payment_day=1) -- a
        February collision.  Both are paid on-schedule (cash == the scheduled
        P&I), and no rate change spans the shifted month, so the resolver's
        scheduled-principal walk and the reader's real-principal walk stay locked
        step-for-step and the scalar balances agree to the penny.  The reader then
        dates BOTH rows 2026-02-01 (the true due date), where the resolver, having
        shifted the second payment's display slot, dates its rows 2026-02-01 and
        2026-03-01 -- the one legitimate, display-only disagreement that remains.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, name="Collision Loan")
            params = loan_loaders.load_loan_params(loan.id)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            # The collision is real: both payments' true monthly due date is 02-01.
            assert monthly_due_date(
                seed_periods[1].start_date, params.payment_day,
            ) == date(2026, 2, 1)
            assert monthly_due_date(
                seed_periods[2].start_date, params.payment_day,
            ) == date(2026, 2, 1)

            _settle(seed_user, loan, seed_periods[1], amount=scheduled_pi)
            _settle(seed_user, loan, seed_periods[2], amount=scheduled_pi)
            db.session.commit()

            # The two GENESIS producers agree, as they always must -- the posted
            # ledger is a projection of the same walk.
            ledger = _ledger_balance(loan.id, scenario_id)
            resolver = _resolver_balance(loan.id, scenario_id, _AS_OF)
            reader = _posted_balance(loan.id, scenario_id)
            assert ledger == reader
            _assert_loan_reconciles(loan, scenario_id, _AS_OF)

            # Attribution, DISPLAY rows: the confirmed view keeps BOTH rows at the
            # true February due date; the resolver replay redistributes the second
            # to March (inlined -- the row-date lists ARE the assertion, not
            # locals).
            assert [
                row.payment_date
                for row in _confirmed_rows_at(loan.id, scenario_id, _AS_OF)
            ] == [date(2026, 2, 1), date(2026, 2, 1)]
            ctx = loan_payment_service.load_loan_context(
                loan.id, amount_basis_for_scenario(scenario_id), params,
            )
            replay_state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments, ctx.rate_changes,
                ),
                _AS_OF,
            )
            resolver_rows = [
                row for row in replay_state.schedule if row.is_confirmed
            ]
            assert [row.payment_date for row in resolver_rows] == [
                date(2026, 2, 1), date(2026, 3, 1),
            ]

            # THE BALANCE DIVERGENCE, pinned by its MECHANISM rather than by a
            # magic number: the resolver charges a month per RECORD, so its
            # second row accrues a month the walk does not, and the gap is
            # exactly that row's own interest.  Reading it off the resolver's own
            # row is what makes this a statement about the rule rather than a
            # transcription of today's arithmetic.
            assert resolver - ledger == resolver_rows[1].interest
            assert resolver_rows[1].interest > Decimal("0.00")

            # Attribution, LEDGER per-period buckets (the branch's namesake): the
            # reader's per-period map reflects BOTH paydowns by period 2, so it
            # equals the final balance and steps strictly below period 1.  The
            # resolver's per-period view now AGREES: redistribution shifts only a
            # payment's DUE date, never its ``payment_date`` (the pay period the
            # cash actually moved in), so payment 2 still clears the replay's
            # "has this period begun?" cap at period 2's start.  Overwriting
            # ``payment_date`` with the invented March due date (the pre-fix
            # behaviour) made that cap read ``2026-03-01 <= 2026-01-30`` -- false
            # -- so the replay DROPPED a payment that had genuinely been made,
            # and the resolver read HIGHER than the ledger.  That was the
            # per-period divergence (review M7 / Step-4 M2); it is now CLOSED.
            balance_map = _posted_period_map(loan.id, scenario_id, seed_periods)
            assert balance_map[seed_periods[2].id] == reader
            assert balance_map[seed_periods[2].id] < balance_map[seed_periods[1].id]
            # The resolver's per-period view carries the SAME one-month gap, for
            # the same reason -- not a second defect.
            assert _resolver_balance(
                loan.id, scenario_id, seed_periods[2].start_date,
            ) - balance_map[seed_periods[2].id] == resolver_rows[1].interest


class TestReadSwitchProductionPath:
    """The production loan read path returns the LEDGER-true balance (the flip).

    Every displayed loan balance flows through the ``balance_at`` seam scalar
    (the fold over the loan's recorded events; the seeded resolver carried a
    ledger-fed ``current_balance`` until plan step D2a deleted the field).
    Before the read switch the displayed balance replayed the SCHEDULED payment
    from the anchor and dropped the cash, so off-schedule it disagreed with the
    posted ledger (that disagreement is what the classes above pin, via the
    un-seeded ``_resolver_balance``).  Now the production balance EQUALS the
    ledger / reader and DIVERGES from the un-seeded schedule replay by exactly
    the extra / short principal.

    This is the read switch itself, pinned end-to-end at the service the surfaces
    call.  It is the deliberate complement of ``_resolver_balance``: that helper
    stays on the un-seeded replay to keep the oracle's parallel run honest; this
    class proves the SEEDED production path moved onto the ledger.
    """

    def test_production_path_reads_the_ledger_off_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """What the seam scalar owes == the ledger / reader, NOT the schedule replay.

        Two identical $100,000 @ 6% loans (interest 500.00, so one scheduled P&I
        governs both deltas).  An EXTRA $2,000 payment books real principal 1,500
        -> the production path owes 98,500.00, equal to the reader and the
        independent linked-net query, and LESS than the un-seeded replay by exactly
        (cash 2,000 - scheduled P&I).  A SHORT $1,000 payment books real principal
        500 -> 99,500.00, MORE than the replay by exactly (scheduled P&I - cash
        1,000).  The strict divergence from the replay is what makes this
        non-vacuous: before the flip the production path WAS the replay, so it
        would have equalled it here.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            extra_loan = _make_loan(seed_user, name="Extra Prod Loan")
            short_loan = _make_loan(seed_user, name="Short Prod Loan")
            # Identical params -> one scheduled P&I (balance-independent) governs
            # both deltas; reading it does not perturb the balance being tested.
            monthly_pi = _resolved_at(
                extra_loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

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
            # ``owed()``: the sign the ledger, the reader and the replay state.
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

            # The flip: production == ledger == reader (the hand-computed balances).
            assert extra_production == Decimal("98500.00")
            assert extra_production == _ledger_balance(extra_loan.id, scenario_id)
            assert extra_production == _posted_balance(extra_loan.id, scenario_id)
            assert short_production == Decimal("99500.00")
            assert short_production == _ledger_balance(short_loan.id, scenario_id)
            assert short_production == _posted_balance(short_loan.id, scenario_id)

            # Non-vacuous: production is NOT the un-seeded schedule replay -- it
            # diverges by exactly the extra / short principal the replay drops.
            extra_replay = _resolver_balance(extra_loan.id, scenario_id, _AS_OF)
            short_replay = _resolver_balance(short_loan.id, scenario_id, _AS_OF)
            assert extra_production < extra_replay
            assert extra_replay - extra_production == Decimal("2000.00") - monthly_pi
            assert short_production > short_replay
            assert short_production - short_replay == monthly_pi - Decimal("1000.00")

    def test_production_schedule_confirmed_rows_are_the_ledger_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The production schedule's confirmed rows ARE the ledger history (C11).

        The history read switch: ``resolve_loan_bundle``'s schedule -- the
        amortization table, the chart's history prefix, the date-precise
        ``balance_at`` walk -- carries the RECORD-derived confirmed rows, equal to
        the seam's own ``confirmed_view`` rows verbatim (the walk-built view since
        plan step E1d-b; the posted-ledger reader it replaced before that).  Off-schedule (an
        EXTRA $2,000 payment on the $100,000 balance: interest 500.00, real
        principal 1,500.00, balance 98,500.00) the row shows the ACTUAL
        economics, while the un-seeded replay's row still shows the SCHEDULED
        principal (period P&I - 500.00) and a higher balance -- the strict
        per-row divergence that makes the flip non-vacuous.
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
            state = resolved.state
            confirmed_rows = [r for r in state.schedule if r.is_confirmed]
            assert confirmed_rows == _confirmed_rows_at(
                loan.id, scenario_id, _AS_OF,
            )
            (row,) = confirmed_rows
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("1500.00")
            assert row.remaining_balance == Decimal("98500.00")

            # The un-seeded replay's row is the SCHEDULED split -- strictly
            # different, so "production rows == ledger rows" is non-vacuous.
            params = loan_loaders.load_loan_params(loan.id)
            ctx = loan_payment_service.load_loan_context(
                loan.id, amount_basis_for_scenario(scenario_id), params,
            )
            replay_state = loan_resolver.resolve_loan(
                loan_resolver.LoanInputs(
                    params, loan_loaders.load_loan_anchor_facts(params),
                    ctx.payments, ctx.rate_changes,
                ),
                _AS_OF,
            )
            (replay_row,) = [
                r for r in replay_state.schedule if r.is_confirmed
            ]
            assert replay_row.principal != row.principal
            assert replay_row.remaining_balance > row.remaining_balance

    def test_production_path_matches_replay_on_schedule(
        self, app, db, seed_user, seed_periods,
    ):
        """On-schedule the seed is invisible: production == replay == ledger.

        Paying exactly the scheduled P&I books the same real principal the replay
        schedules, so seeding the confirmed balance changes nothing: the production
        path, the un-seeded replay, and the ledger all agree.  This guards against
        the seed perturbing the on-schedule case (a regression that would show a
        loan card drifting from its schedule even when the user pays exactly).
        The seam reports the balance HELD (negative for a loan, ruling R-CC47),
        so ``production`` is what the loan owes, read through ``owed()``.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            scheduled_pi = _resolved_at(
                loan.id, scenario_id, _AS_OF,
            ).state.monthly_payment

            _settle(seed_user, loan, seed_periods[_P1], amount=scheduled_pi)
            db.session.commit()

            bctx = BalanceContext(
                user_id=loan.user_id, scenario=seed_user["scenario"],
                as_of=_AS_OF,
            )
            production = owed(balance_at.balance_at(loan, bctx, _AS_OF))
            assert production == _resolver_balance(loan.id, scenario_id, _AS_OF)
            assert production == _ledger_balance(loan.id, scenario_id)
