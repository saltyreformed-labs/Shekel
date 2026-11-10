"""C3c: balance_at.loan_interest_in_year -- interest PAID in a year, one producer.

Plan step **C3c** (``docs/audits/balance_architecture/README.md``).  Direct unit
coverage of the balance seam's ONE loan-interest producer, which replaced the
ledger-reader-plus-schedule HYBRID that lived in ``tax_report_service``
(``_compute_mortgage_interest`` / ``_loan_year_interest``, both deleted).  The LIVE
Schedule-A wiring and hand-computed value stay in the ``test_tax_report_service``
package (``TestScheduleAMortgageInterest``, in
``test_degrades_households_and_schedule_a.py``); these pin the producer's INTERNALS
the live value test alone does not reach:

* the SETTLED (past) interest is the FOLD's actual per-payment figure -- correct
  for an off-schedule payment where the schedule replay is not -- attributed to
  each payment's DISPLAY-timezone civil paid YEAR (the L9 tax basis, which diverges
  from the balance ledger's UTC clock across the New Year);
* the PROJECTED (future) interest is folded from the loan's forward payment PLAN
  (step C6c), the SAME plan the projected BALANCE folds -- an early-settled
  installment counts once because the plan excludes its slot BY CONSTRUCTION (the
  C3c schedule-slot merge is gone; the de-dup lives in ``loan_plan``);
* the figure comes from the loan's SOURCE events, so it answers even for a loan the
  posting cache cannot value (no genesis opening posting) -- closing B-6, where the
  old hybrid fell back to the schedule and reported the wrong number.

Relocated from the retired ``test_tax_mortgage_interest.py`` (which tested the
deleted hybrid); the value assertions are hand-computed, never the producer as its
own oracle (plan N-7).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.services import balance_at
from app.services.balance_at._loan_stream import loan_timeline
from app.services.balance_at._plan import loan_plan
from app.services.balance_at import BalanceContext
from app.services.liability_sign import owed
from tests._stored_ledger import stored_loan_balance_at
from tests.oracles.loan_monthly_composition import charge_then_allocate
from tests._test_helpers import (
    clear_loan_ledger,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    SPLIT_LOAN,
)

ZERO = Decimal("0.00")


def _paid_utc(year, month, day):
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _split_loan(seed_user):
    """Create a bare SPLIT_LOAN (origination + true-up), no payments yet."""
    (orig_principal, orig_date, rate, anchor_balance,
     anchor_date, _p1, _p2, _p3) = SPLIT_LOAN
    return create_loan_with_trueup(
        seed_user, db.session, origination_principal=orig_principal,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=orig_date,
    )


def _plan_projected_interest(loan, ctx, year, *, exclude_slots=frozenset()):
    """Independently fold the loan's PLAN records to its projected interest in *year*.

    A test-side parallel of the producer's projected half (step C6c): it seeds from
    what the loan OWES at the read day, the SAME balance the timeline carries there
    (the seam's held figure read through ``owed()``), walks the loan's
    :func:`~app.services.balance_at._plan.loan_plan` records in due order, and sums
    each payment's interest (the RETIRED one-payment-a-month composition) by its EFFECTIVE
    year, dropping any due-month slot in *exclude_slots* (the settled-slot merge) --
    WITHOUT calling ``plan_interest_in_year``.  So the producer's WIRING (the right
    seed, the right plan, the right year key, the merge, the two halves summed) is
    checked here, while the arithmetic VALUE is pinned by hand in
    ``test_loan_plan_forward_oracle`` (never the producer as its own oracle, N-7).
    """
    # The balance the projection starts from: what the loan OWES at the read
    # day (what ``DebtSchedule.projection_seed`` was until plan step
    # recurrence:R16-c-1 replayed the whole timeline from the origination).
    # The seam reports it HELD, negative when owed (ruling R-CC47), and the
    # loan domain folds what is OWED, so the seed crosses once through owed().
    seed = owed(balance_at.balance_at(loan, ctx, ctx.as_of))
    plan = loan_plan(loan, ctx)
    # The CHARGE standing against each accrual period, keyed by the period it
    # opens.  Since plan step R16-a a month's interest and escrow are charged
    # once per period rather than once per payment, and this oracle folds
    # through the RETIRED one-payment-per-month composition
    # (``tests.oracles.loan_monthly_composition``, deleted from ``app/`` at plan
    # step X-au-g-2c-3b-2) because every plan it grades puts one payment in each
    # period.  The charges are the one timeline's since plan step
    # recurrence:R16-c-2 (ruling R-R100): every installment from origination,
    # of which only the periods the plan's payments occupy are looked up.
    stream = loan_timeline(loan, ctx).stream
    charged = {
        (charge.on_date.year, charge.on_date.month): charge
        for charge in stream.charges
    }
    # A plan payment due ON or BEFORE the loan's latest recorded FACT -- a
    # settled payment or a balance assertion, in contract time -- is a
    # catch-up: it is walked behind that fact, which cleared every charge
    # standing (plan step recurrence:R16-c-2, rulings R-R72 part (1) and
    # R-R100), so it faces nothing and pays pure principal.  Stated here from
    # the stream's facts rather than read off the replay's own boundary, so
    # the oracle does not grade the producer against itself.  No case below
    # puts a plan payment ON the latest fact's date or behind an assertion
    # (the plan drops those at the assertion), so those two arms match the
    # producer's rule for fidelity and are graded by nothing yet.
    latest_fact = max(
        (event.on_date for event in (*stream.payments, *stream.resets)),
        default=None,
    )
    balance = seed
    total = ZERO
    for payment in sorted(
        plan.payments, key=lambda p: (p.due_date, p.effective_date),
    ):
        slot = (payment.due_date.year, payment.due_date.month)
        if latest_fact is not None and payment.due_date <= latest_fact:
            parts = charge_then_allocate(payment.cash, balance, ZERO, ZERO)
        else:
            charge = charged[slot]
            parts = charge_then_allocate(
                payment.cash, balance, charge.period.annual_rate, charge.escrow,
            )
        balance = parts.balance_after
        if payment.effective_date.year == year and slot not in exclude_slots:
            total += parts.interest
    return total


class TestLoanInterestInYearValue:
    """The figure is the FOLD's actual interest, on the paid-date (display) basis."""

    def test_current_year_is_fold_actual_plus_projected(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Off-schedule confirmed history (fold-actual) + the year's projection.

        Trued to $100,000 @ 6% on 2026-01-10, then a $2,000 (extra) and a $1,000
        payment, both PAID mid-2026 and both begun by the frozen 2026-04-01:

          P1 ($2,000, due 02-01): interest round(100000 * 0.005) = 500.00.
          P2 ($1,000, due 03-01): interest round(98500 * 0.005) = 492.50 -- on the
            REAL balance the extra payment left, NOT the schedule replay's ~99,900.

        Fold-actual 2026 interest = 992.50, which the schedule replay does not
        reproduce.  The producer is that fold-actual PLUS the year's PROJECTED plan
        interest, both halves non-zero.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 4, 1))
            _, _, _, _, _, p1, p2, _ = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("2000.00"),
                settled_on=date(2026, 2, 15),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p2], amount=Decimal("1000.00"),
                settled_on=date(2026, 3, 15),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            # The settled half, hand-computed (P1 500.00 + P2 492.50).
            fold_actual = Decimal("992.50")

            # P1/P2 are settled and out of the forward plan; the remaining 2026
            # installments (Apr onward) are ESTIMATED, folded from the confirmed
            # present -- a genuinely non-zero projected half.
            projected = _plan_projected_interest(loan, ctx, 2026)
            assert projected > ZERO  # genuine hybrid, not a vacuous zero

            result = balance_at.loan_interest_in_year(loan, ctx, 2026)
            assert result == fold_actual + projected

    def test_interest_deducts_in_the_year_it_was_paid(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A 2026-scheduled payment PAID in 2025 deducts in 2025 (pure fold).

        Mortgage interest deducts in the year PAID.  A period-``P1`` payment
        (scheduled 2026-02-01) settled 2025-12-20 attributes its 500.00 interest to
        2025 -- a year with NO projected payments (the plan is all 2026+), so the
        figure is PURE fold and the projection is structurally zero.  A
        payment-DATE basis would report 0.00 for 2025 (the negative control).
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                settled_on=date(2025, 12, 20),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            # 2025 carries no projected plan payment, so the figure is pure fold.
            assert _plan_projected_interest(loan, ctx, 2025) == ZERO
            assert balance_at.loan_interest_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")

    def test_new_years_eve_evening_settle_deducts_in_the_display_year(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """THE L9 CASE: 8:05 PM EST Dec 31 deducts in the OLD (display) year.

        A payment settled 2025-12-31 20:05 Eastern is stored 2026-01-01 01:05 UTC,
        so the balance ledger's UTC clock (and the fold's visible date) is already
        2026.  The tax figure follows the user's WALL clock (L9): the 500.00
        interest deducts in 2025.  2025 has NO schedule rows, so the figure is pure
        fold -- and a UTC-basis attribution (the fold's balance clock) would report
        0.00 here, which is exactly the defect that made interest-in-year a
        dedicated display-clock function rather than the fold's UTC-keyed
        cum-interest.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                settled_on=date(2025, 12, 31),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")


class TestLoanInterestInYearMerge:
    """One record per installment: an early-settled slot is not counted twice."""

    def test_early_settled_slot_counted_exactly_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An early-settled payment's interest counts ONCE -- in the settled half.

        A payment settled BEFORE its pay period begins is in the FOLD (its actual
        interest, at its paid date), and the forward PLAN excludes its installment
        slot BY CONSTRUCTION -- ``loan_plan``'s ESTIMATED tier skips a slot already
        covered by a seed-settled payment (the de-dup, proven at the plan level in
        ``test_loan_plan_assembly``).  So the C3c schedule-slot merge this class
        once tested has no work left to do here: no projected record re-counts the
        early-settled installment.

        Frozen 2026-02-10: P1 (due 02-01, begun) and P2 (due 03-01, EARLY) both
        settle.  Fold interest = P1 500.00 (100000 * 0.005) + P2 497.50
        (round(99500 * 0.005)) = 997.50, both paid 2026.  The March slot P2
        satisfies must appear in NEITHER the plan nor the projected half -- if it
        did, it would double-count P2's installment (the +$489.97 the retired merge
        subtracted by hand).  The early payment is P2's and not a later
        installment's since plan step recurrence:R16-c-2: every contractual
        installment is charged now, so an early payment for April would clear
        March's standing interest too (the fixture is restated so nothing is
        left unpaid, ruling R-R103).
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 2, 10))
            _, _, _, _, _, p1, p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                settled_on=date(2026, 2, 5),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p2], amount=Decimal("1000.00"),
                settled_on=date(2026, 2, 10),
            )
            db.session.commit()
            # Premise: P2's period has not begun by the frozen today (early settle).
            assert seed_periods[p2].start_date > date(2026, 2, 10)
            ctx = BalanceContext.build(seed_user["user"].id)

            # P2 satisfies the March installment early, so its 497.50 is in the
            # SETTLED half (paid Feb 2026); the settled half is hand-computed.
            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("997.50")

            # The plan de-dups the early-settled March slot OUT, while its
            # neighbours are genuinely present -- so the exclusion is real, not a
            # vacuously empty plan.
            plan_slots = {
                (payment.due_date.year, payment.due_date.month)
                for payment in loan_plan(loan, ctx).payments
            }
            assert (2026, 3) not in plan_slots      # P2's slot, de-duped
            assert (2026, 4) in plan_slots          # its uncovered neighbours ARE
            assert (2026, 5) in plan_slots          # planned

            # So the whole 2026 figure counts March ONCE: the settled fold (incl.
            # P2) plus a projected half that carries no March record.
            projected = _plan_projected_interest(loan, ctx, 2026)
            assert projected > ZERO                 # non-vacuous
            result = balance_at.loan_interest_in_year(loan, ctx, 2026)
            assert result == Decimal("997.50") + projected

    def test_evening_settle_utc_rollover_counted_exactly_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A payment settled evening-Eastern is counted exactly once.

        **The two-clock trap this test was written for is GONE at its source**
        (ruling R-DH (b), 2026-07-31).  ``payment_visible_on`` derived the UTC
        civil date, so a payment settled in the evening of a UTC-behind zone was
        visible TOMORROW -- outside ``confirmed_shadows_through(as_of)``, which
        the plan's ESTIMATED de-dup keys on -- while for TAX it was paid TODAY
        (display).  The plan therefore re-synthesized the installment and the
        deduction doubled unless a merge against the WALK removed it.  Both
        clocks are the user's day now, so the de-dup and the tax attribution
        agree and the slot is never synthesized in the first place.

        Frozen display today 2026-01-31 (EST, UTC-5).  A period-``p2`` payment
        (due 2026-03-01) is settled EARLY at ``2026-02-01 02:00 UTC`` =
        ``2026-01-31 21:00 EST``: display-paid 2026-01-31 (year 2026, in the
        settled half) AND visible 2026-01-31 <= as_of, so ``loan_plan`` does
        not synthesize a March ESTIMATED record at all.  Its interest is
        1,000.00: every contractual installment is charged since plan step
        recurrence:R16-c-2 (rulings R-R72 part (1) and R-R100), and the 02-01
        installment falls before it in contract time with nothing paying it,
        so the payment clears February (500.00 on the $100,000 trued balance)
        and then March (500.00) -- all $1,000.00 of its cash.  The plan's
        February installment -- not yet due on the 01-31 read, but due before
        the settled March payment in contract time -- becomes a catch-up behind
        it, paying pure principal.  Until R16-c-2 February was charged to that
        catch-up and
        this payment's interest was 500.00; the developer approved the moved
        figure (rule 5).

        **The walk-merge IS deleted at plan step recurrence:R16-c-1**, when the
        settled and projected halves became ONE list of outcomes (a payment is
        either recorded or projected, never both), so this shape can no longer
        double-count by construction.  The oracle below still folds the plan
        with and without the March slot excluded, which now grades the
        upstream mechanism alone: the plan does not synthesize March.  The
        final assertion is unchanged: the producer counts this payment once.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 1, 31))
            _, _, _, _, _, _p1, p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            # p2's pay period starts AFTER as_of (a genuine early settle), so the
            # seed excludes this payment too -- isolating the interest double-count
            # from any balance-seed effect.
            assert seed_periods[p2].start_date > date(2026, 1, 31)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p2], amount=Decimal("1000.00"),
                settled_on=date(2026, 1, 31),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            # The payment is display-paid in 2026 (its interest is February's
            # and March's 500.00 each on the $100,000 trued balance), so the
            # settled half counts it.
            assert balance_at.loan_interest_paid_in_year(
                loan, ctx, 2026,
            ) == Decimal("1000.00")

            # It is now VISIBLE by as_of on the same clock the tax attribution
            # uses, so the plan does not synthesize March at all -- the gap is
            # closed at its source rather than patched downstream.
            march = (2026, 3)
            plan_slots = {
                (payment.due_date.year, payment.due_date.month)
                for payment in loan_plan(loan, ctx).payments
            }
            assert march not in plan_slots

            # With the slot absent, excluding it is a no-op: the two projections
            # agree, which is the arithmetic statement of "no double-count to
            # remove".  Under the UTC clock these differed by ~$495, a full
            # installment's interest and a real deduction error.
            naive = _plan_projected_interest(loan, ctx, 2026)
            merged = _plan_projected_interest(
                loan, ctx, 2026, exclude_slots=frozenset({march}),
            )
            assert naive == merged

            # The producer counts this payment ONCE either way.
            result = balance_at.loan_interest_in_year(loan, ctx, 2026)
            assert result == Decimal("1000.00") + merged


class TestLoanInterestAnswersFromTheFold:
    """B-6: the figure comes from source events, not the posting cache."""

    def test_cleared_ledger_still_answers_from_the_fold(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """No genesis opening posting -> the FOLD answers, where the old hybrid did not.

        The old hybrid asked the posting reader, which returns ``None`` for a loan
        with no opening posting, and then fell back to the schedule -- reporting
        0.00 for a 2025 payment (no 2025 rows) that genuinely paid 500.00 of
        interest.  The producer folds the loan's SOURCE events, so a cleared posting
        cache does not change the answer.  This is B-6 closed: the interest figure
        and the balance both come from the total fold.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            scenario_id = seed_user["scenario"].id
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                settled_on=date(2025, 12, 20),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.loan_interest_in_year(loan, ctx, 2025)
            assert before == Decimal("500.00")

            # Break the posting cache: with no opening posting the sum-of-postings
            # balance reader declines (None), which is what makes this test's teeth
            # real -- the postings genuinely no longer answer for this loan.
            clear_loan_ledger(loan.id)
            db.session.commit()
            assert stored_loan_balance_at(
                loan.id, scenario_id, date(2025, 12, 31),
            ) is None

            # The fold-based producer is unchanged: it never read the postings.
            ctx_after = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_interest_in_year(
                loan, ctx_after, 2025,
            ) == Decimal("500.00")
