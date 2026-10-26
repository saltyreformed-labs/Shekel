"""The loan fold: ``app.services.balance_at._fold.fold_loan_balances``.

Plan step B1 (``docs/audits/balance_architecture/README.md``).  These tests pin
the fold's CONTRACT -- totality, the delta each event contributes, and the two
visibility rules it reproduces -- against HAND-COMPUTED figures.  The fold moved
into the balance seam at step D-fold (*a fold is a balance*); the WALK it samples
and the two visibility rules it reproduces stay in the ``loan_ledger`` leaf (*a
walk is a fact*), and this file also pins those leaf primitives directly.  The
leaf's CALENDAR names left at pay-calendar plan step C2-d -- see
``TestTheFoldTakesNoCalendarAndThePackageHoldsNone`` for what replaced their
tests, and ``test_pay_calendar_value.py`` for the rule that replaced them.

**Hand-computed, deliberately.**  The fold and the posted ledger share the walk
(that is the design: the postings are a PROJECTION of the fold), so grading the
fold by comparing it to the ledger would be two readers of one walk agreeing --
"two wrong implementations agreeing is not a proof" (plan Section 8).  Every
expected balance below is arithmetic done here, in the docstring, from the loan's
terms.  The exhaustive every-day parallel run against the shipping seam is step
B2's job and a different question (is the posted cache faithful?); this is the
reference's own.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.loan_features import RateHistory
from app.services import loan_ledger, loan_loaders, loan_resolver
from app.services.balance_at._fold import fold_loan_balances
from app.services.rate_period_engine import period_for_date
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_savings_account,
    create_settled_transfer,
    insert_tracking_start_event,
    last_covered_day,
    posted_loan_balance_at,
)

# The controlled loan: $250,000 originated 2025-01-01 at 6%, trued up to
# $100,000.00 as of 2026-01-05 (before every seeded period, so the true-up is the
# balance every payment amortizes down from).
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")

# seed_periods: 10 biweekly periods from 2026-01-02.  Period 0 = 01-02..01-15.
_P0_START = date(2026, 1, 2)


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="Fold Loan",
        **kwargs,
    )


def _fold(loan, seed_user, on_dates):
    """Fold *loan*'s balance at *on_dates*."""
    return fold_loan_balances(
        loan.id, seed_user["scenario"].id, on_dates,
    )


def _settle(seed_user, db, loan, period, amount, due_date=None):
    """Settle a Checking -> loan payment, visible from its period start (C2).

    Pins the settle day to the period start so the payment is visible from that day
    under C2's settled-date clock -- the deterministic past date these
    hand-computed folds value the balance from.

    ``due_date`` states the installment explicitly; ``None`` (the default) leaves
    it to the pay-period-start fallback (``monthly_due_date``), which is what an
    ad-hoc transfer produces.  A test needing two payments in ONE accrual period
    at DIFFERENT installments must state them, because that fallback resolves
    every period inside a month to the same date.
    """
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount, settled_on=period.start_date, due_date=due_date,
    )


class TestFoldIsTotal:
    """The fold cannot answer ``None`` and cannot raise -- the arc's whole premise.

    The posting readers this arc inherited were PARTIAL: they answered ``None``
    with no opening posting and RAISED for a future date.  A partial function
    cannot be the single source, so every caller composes it with something else
    -- a projection, a seed, a flag, a fallback -- and every composition is a new
    producer that can disagree with the others.  Every piece of machinery this arc
    deletes exists to manage that partiality (plan Section 1); the readers
    themselves were deleted at plan step E1e, once the fold had taken every one
    of their callers.  These tests pin the property that made that possible.
    """

    def test_a_date_before_every_event_folds_to_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Before the loan's first event the fold is 0.00 -- an empty prefix.

        The loan originates 2025-01-01; asked about 2024-12-31 the fold has no
        events to apply, and the fold of an empty stream is 0.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            folded = _fold(loan, seed_user, [date(2024, 12, 31)])
            assert folded[date(2024, 12, 31)] == Decimal("0.00")

    def test_an_unconfigured_loan_folds_to_zero_and_does_not_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """No LoanParams at all: 0.00 everywhere, never ``None``, never a raise.

        The ledger reader answers ``None`` here (its needs-setup sentinel) and the
        seam's AMORTIZING branch degrades to the cash producer.  The fold takes no
        position: it reports the honest fold of a stream with no events.  A caller
        that must tell "owed nothing" from "no loan" asks ``origination_date``.
        """
        with app.app_context():
            plain = create_savings_account(
                seed_user, db.session, name="Plain",
                anchor_balance=Decimal("2500.00"),
            )
            db.session.commit()
            folded = _fold(
                plain, seed_user, [date(2026, 1, 2), date(2026, 3, 1)],
            )
            assert folded == {
                date(2026, 1, 2): Decimal("0.00"),
                date(2026, 3, 1): Decimal("0.00"),
            }

    def test_a_future_date_does_not_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """A future date holds the last RECORDED balance flat instead of raising.

        The posting reader raised for ``as_of > today`` -- the partiality that
        forced its callers to fork on the clock.  The fold does not: it reports
        what it knows.  That is NOT the projection the seam shows (PLANNED
        payments arrive at C3), so this pins the no-raise contract only, not a
        forward balance.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            far = date(2099, 1, 1)
            assert _fold(loan, seed_user, [far])[far] == (
                _ANCHOR_BALANCE
            )

    def test_no_dates_folds_to_an_empty_map(
        self, app, db, seed_user, seed_periods,
    ):
        """An empty date list is an empty map, not a raise or a default date."""
        with app.app_context():
            loan = _make_loan(seed_user, db)
            assert _fold(loan, seed_user, []) == {}

    def test_duplicate_and_unsorted_dates_are_answered_independently(
        self, app, db, seed_user, seed_periods,
    ):
        """Each date is folded on its own; order and duplicates do not matter.

        The fold walks once and samples, so a caller may pass its dates in any
        order.  Duplicates collapse (a dict key), and a date's answer never
        depends on which dates accompany it.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            late, early = date(2026, 1, 20), date(2024, 12, 31)
            folded = _fold(loan, seed_user, [late, early, late])
            assert folded == {
                late: Decimal("100000.00"),
                early: Decimal("0.00"),
            }

    def test_the_fold_needs_no_owner_calendar(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The fold consults NO owner calendar -- an anchor counts from its own date.

        Totality is over the QUESTION (any date, any account).  Pre-C2 an anchor's
        visible-on date was ``LEAST(anchor_date, containing period.start)``, so the
        fold loaded the owner's whole calendar and RAISED when it was empty.  The
        one clock counts an anchor from its OWN date, so the fold reaches no
        calendar at all.

        **Pinned against the calendar doors this arc has built, and the
        REWRITE found the old guard had no teeth.**  It used to make
        ``loan_ledger.owner_pay_periods`` raise if the fold called it, patched
        on ``_visible`` -- but the posting writer imported that name DIRECTLY,
        so the patched attribute was a binding nothing in ``app/`` ever read.
        The fixture built a loan through the writer with the poison installed
        and never tripped it.  Pay-calendar plan step C2-d deleted the loader,
        and poisoning its successor properly is what exposed that.

        **Three bindings, and the ORDER is now load-bearing.**
        ``calendar_for`` is imported by name in ``_posting_reconcile``, so
        patching the defining module alone would leave the binding both anchor
        writers actually call.  Those writers legitimately DO consult the
        calendar -- filing an anchor correction is what C2-d pointed at it --
        so the loan is built FIRST and the poison installed afterwards, around
        the fold alone.  A poison installed before setup grades the fixture.

        This is not every route to a calendar: ``recurrence._authoring``'s own
        ``calendar_for`` and ``pay_period_service.get_all_periods`` are two
        more, deliberately un-poisoned, and a fold regression reaching either
        would slip past.  The STRUCTURAL half of the claim is
        ``test_the_leaf_owns_no_database_session`` below.
        """
        def _must_not_call(user_id):
            raise AssertionError(
                "the fold must not consult the owner calendar (step C2)"
            )
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for binding in (
                "app.services.pay_calendar.calendar_for",
                "app.services.pay_calendar._loader.calendar_for",
                "app.services._posting_reconcile.calendar_for",
            ):
                monkeypatch.setattr(binding, _must_not_call)
            # Trued up to $100,000 as of 2026-01-05; a later date holds it flat.
            assert _fold(loan, seed_user, [date(2026, 1, 20)])[
                date(2026, 1, 20)
            ] == _ANCHOR_BALANCE


class TestTheFoldTakesNoCalendarAndThePackageHoldsNone:
    """The fold takes no period list, and since C2-d the package holds no calendar.

    Pre-C2 an anchor's visible-on date was derived from the period CONTAINING it,
    so a PARTIAL calendar silently moved the answer (a window excluding the
    true-up's period shifted the same loan by $150,000.00), and the fold had to
    load the owner's WHOLE calendar itself rather than take one -- the balance
    seam's period argument IS a six-period window in production (the grid).  Step
    C2's one clock counts an anchor from its OWN date, so the fold consults no
    calendar at all; the no-period-argument property below stays a structural
    guard against re-introducing that coupling.

    **The three calendar names are GONE as of pay-calendar plan step C2-d.**
    ``owner_pay_periods``, ``find_period_containing_date`` and
    ``resolve_anchor_pay_period`` had one consumer left between them -- the two
    anchor-posting writers -- and those now file through
    ``pay_calendar.PayCalendar.filing_period``.  The tests that pinned the
    locator's three branches went with it, because the branches themselves did:
    that chain is one clamp now, proven equal to it over 1,800 (shape, day) pairs
    in ``test_pay_calendar_value.py`` and re-measured against production at the
    cutover.  What replaces them here is the property their deletion CREATED --
    the leaf is pure.
    """

    def test_fold_loan_balances_takes_no_period_argument(self):
        """The divergence vector is absent by signature, not by discipline.

        A structural guard: the fold must never take a ``periods`` parameter -- a
        caller's window would either be silently ignored (C2 folds off event
        dates) or, if a future change wired it in, reintroduce the $150,000.00
        partial-calendar divergence with every other gate silent.
        """
        import inspect
        params = inspect.signature(fold_loan_balances).parameters
        assert list(params) == ["loan_account_id", "scenario_id", "dates"]

    def test_the_package_exports_no_calendar_name(self):
        """The three deleted names are gone from the public surface, not shadowed.

        A deletion that left the names importable would let a future consumer
        reach the old three-branch chain and file an anchor by a second rule --
        the exact duplication plan step C2 exists to remove.  Asserted on
        ``__all__`` and on the module attributes, because a stale ``__all__``
        entry and a stale re-export fail differently.
        """
        for name in (
            "owner_pay_periods",
            "find_period_containing_date",
            "resolve_anchor_pay_period",
        ):
            assert name not in loan_ledger.__all__, name
            assert not hasattr(loan_ledger, name), name

    def test_the_leaf_owns_no_database_session(self):
        """``owner_pay_periods`` was the package's ONE query, and it is deleted.

        The structural half of the claim above: rather than proving one consumer
        does not call one loader, this proves no module in the leaf OWNS a
        session -- neither importing :mod:`app.extensions` nor reaching a
        ``.query`` off a model, the two ways this codebase spells one.

        **"Owns", not "is pure", and the weaker word is the honest one.**
        :func:`walk_loan_ledger` still takes ids and reaches
        :mod:`app.services.loan_loaders`, which holds the session; a walk is a
        function of the facts a loader hands it, and that collaborator is
        declared in the package docstring.  What this pins is that the leaf
        stopped issuing queries of its OWN, which is the property C2-d created
        and the one a future calendar loader would break.

        Read through the AST rather than by substring, so a docstring that
        MENTIONS ``db.session`` -- this file's siblings do -- cannot fail it,
        and so a spelling the substring scan missed cannot pass.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        import ast  # pylint: disable=import-outside-toplevel
        import pathlib  # pylint: disable=import-outside-toplevel

        offenders = []
        for module in sorted(pathlib.Path(loan_ledger.__file__).parent.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "app.extensions":
                    offenders.append(f"{module.name}: imports app.extensions")
                elif isinstance(node, ast.Import) and any(
                    alias.name == "app.extensions" for alias in node.names
                ):
                    offenders.append(f"{module.name}: imports app.extensions")
                elif isinstance(node, ast.Attribute) and node.attr == "query":
                    offenders.append(f"{module.name}: reaches a .query")
        assert offenders == [], (
            f"loan_ledger owns no session as of plan step C2-d; found {offenders}"
        )


class TestFoldValue:
    """Hand-computed balances: the fold's arithmetic, checked against paper."""

    def test_the_opening_folds_to_the_true_up_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """With no payments the fold is the LATEST anchor: $100,000.00.

        Two anchors: origination ($250,000.00 on 2025-01-01) and the true-up
        ($100,000.00 on 2026-01-05).  The walk seeds at 0.00, the origination
        resets to $250,000.00, the true-up resets to $100,000.00.  Deltas:
        +250,000.00 then -150,000.00, summing to $100,000.00 -- the assertion the
        operator actually made, NOT the origination principal.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            on = date(2026, 1, 20)
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("100000.00")
            )

    def test_one_payment_folds_off_its_REAL_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,000.00 payment on a $100,000.00 balance at 6% pays down $500.00.

        Hand-computed, and the whole point of folding CASH rather than a schedule:

            interest  = round(100,000.00 * 0.06 / 12) = $500.00
            escrow    = $0.00 (no escrow line on this loan)
            principal = 1,000.00 - 500.00 - 0.00      = $500.00
            balance   = 100,000.00 - 500.00           = $99,500.00

        The contractual P&I is irrelevant: principal is whatever the cash left
        after interest and escrow, so an extra or short payment lands here
        automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
            db.session.commit()
            on = last_covered_day(seed_periods[1])
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("99500.00")
            )

    def test_an_extra_payment_folds_off_more_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """$1,500.00 on the same balance pays down $1,000.00, not $500.00.

            interest  = $500.00 (unchanged -- same balance, same rate)
            principal = 1,500.00 - 500.00 = $1,000.00
            balance   = 100,000.00 - 1,000.00 = $99,000.00

        The negative control for the one above: the SAME loan, a different cash
        amount, a different balance.  If the fold read the schedule instead of the
        cash, both tests would report $99,500.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1500.00"))
            db.session.commit()
            on = last_covered_day(seed_periods[1])
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("99000.00")
            )

    def test_two_payments_compound_on_the_reduced_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """The second payment accrues on what the first left -- one running balance.

            payment 1: interest = round(100,000.00 * .005) = $500.00
                       principal = 1,000.00 - 500.00       = $500.00
                       balance                              = $99,500.00
            payment 2: interest = round( 99,500.00 * .005) = $497.50
                       principal = 1,000.00 - 497.50       = $502.50
                       balance                              = $98,997.50

        If the two payments were split independently (each against the anchor)
        the second would report $497.50 of interest against $500.00 and land at
        $99,000.00.  The $2.50 is the running balance doing its job.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3]):
                _settle(seed_user, db, loan, period, Decimal("1000.00"))
            db.session.commit()
            on = last_covered_day(seed_periods[3])
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("98997.50")
            )

    def test_the_fold_is_a_step_function_between_events(
        self, app, db, seed_user, seed_periods,
    ):
        """The balance holds flat between events and steps exactly at one.

        The day BEFORE the payment's period starts it is still $100,000.00; from
        the period's first day it is $99,500.00 and stays there. One walk, sampled
        at four dates, is one call: the fold takes a date LIST for exactly this.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
            db.session.commit()
            start = seed_periods[1].start_date
            folded = _fold(loan, seed_user, [
                start - timedelta(days=1), start,
                start + timedelta(days=1), seed_periods[2].start_date,
            ])
            assert folded[start - timedelta(days=1)] == Decimal("100000.00")
            assert folded[start] == Decimal("99500.00")
            assert folded[start + timedelta(days=1)] == Decimal("99500.00")
            assert folded[seed_periods[2].start_date] == Decimal("99500.00")


class TestFoldCountsAnEventOnTheDayItHappened:
    """The one clock (step C2, ruling R-A): an event counts from the day it HAPPENED.

    A payment counts from its SETTLED date, an anchor from its OWN date -- the
    same day each posting carries in ``entry_date``, so the fold and the shipping
    readers agree by construction (B2).  These flipped the two tests that pinned
    the pre-C2 boundary rules (a payment from its period start, an anchor from
    ``LEAST(anchor_date, period.start)``, N-10).  See ``loan_ledger/_visible.py``.
    """

    def test_a_payment_counts_from_its_SETTLED_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Payment visibility is the SETTLED date -- not the period start, not the due date.

        A $1,000 payment budgeted to period 1 (2026-01-16..01-29) satisfies the
        2026-02-01 installment but SETTLES on 2026-01-20.  Under the one clock the
        balance steps on 2026-01-20 (the settled date), not on 2026-01-16 (the
        period start, the pre-C2 rule) and not on 2026-02-01 (the due date, which
        the split MATH still keys on).  Three axes, one visibility answer.

        The loan is trued up to $100,000 as of 2026-01-05, so before the payment
        the balance is $100,000; the $1,000 pays $500 interest + $500 principal,
        stepping to $99,500.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            settled = date(2026, 1, 20)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
                settled_on=settled,
            )
            db.session.commit()
            # The installment this payment satisfies is 2026-02-01 (the split key).
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_legs(
                loan.id, seed_user["scenario"].id, options=(),
            )
            due = loan_loaders.loan_payment_due_date(
                shadows[0], params.payment_day,
            )
            assert due == date(2026, 2, 1)
            folded = _fold(loan, seed_user, [
                seed_periods[1].start_date,        # period start (pre-C2 rule)
                settled - timedelta(days=1),       # day before settle
                settled,                           # the settled date
                due,                               # the installment date
            ])
            # NOT visible from the period start (pre-C2) or the day before settle...
            assert folded[seed_periods[1].start_date] == Decimal("100000.00")
            assert folded[settled - timedelta(days=1)] == Decimal("100000.00")
            # ...steps exactly on the settled date, and stays down through the due date.
            assert folded[settled] == Decimal("99500.00")
            assert folded[due] == Decimal("99500.00")

    def test_an_anchor_counts_from_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """N-10 closed: an anchor is visible from its OWN date, never days early.

        A tracking-start asserted 2026-01-08 sits in period 0 (2026-01-02..01-15).
        Under the pre-C2 ``LEAST(anchor_date, period.start)`` it was visible from
        2026-01-02 -- six days before the operator asserted anything (N-10).  The
        one clock counts it from 2026-01-08: on 2026-01-02..01-07 the balance is
        the ORIGINATION principal held flat (the C1 plateau, $250,000 -- the loan
        exists, its tracking just has not begun), stepping to $80,000 on 01-08.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                loan_loaders.load_loan_params(loan.id),
                Decimal("80000.00"), date(2026, 1, 8),
            )
            db.session.commit()
            folded = _fold(loan, seed_user, [
                date(2024, 12, 31), _P0_START, date(2026, 1, 7), date(2026, 1, 8),
            ])
            # Before origination: 0.00.  From origination to the tracking-start:
            # the $250,000 plateau (NOT visible-early at the period start).
            assert folded[date(2024, 12, 31)] == Decimal("0.00")
            assert folded[_P0_START] == _ORIGINATION_PRINCIPAL
            assert folded[date(2026, 1, 7)] == _ORIGINATION_PRINCIPAL
            # Steps to the tracking-start value on its OWN date.
            assert folded[date(2026, 1, 8)] == Decimal("80000.00")

    def test_an_anchor_predating_every_period_counts_from_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """An anchor before period 0 counts from its own date -- one clock, no calendar.

        The origination is 2025-01-01, a year before the user's first pay period.
        ``journal_entries.pay_period_id`` is NOT NULL, so the writer files the
        opening under the EARLIEST period (2026-01-02) -- which, read by a
        period-bounded reader, would push a 2025 fact into 2026 and report the loan
        owing NOTHING for all of 2025.  The one clock counts the anchor from its
        OWN civil date instead, so the fold reports $250,000.00 from 2025-01-01
        with no calendar consulted at all.

        The loan here carries ONLY its origination (no true-up), so the figure is
        unambiguous.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Old Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            db.session.commit()
            folded = _fold(loan, seed_user, [
                date(2024, 12, 31), _ORIGINATION_DATE, date(2025, 6, 1),
            ])
            assert folded[date(2024, 12, 31)] == Decimal("0.00")
            assert folded[_ORIGINATION_DATE] == _ORIGINATION_PRINCIPAL
            assert folded[date(2025, 6, 1)] == _ORIGINATION_PRINCIPAL


class TestFoldAgreesWithThePostedSum:
    """A first parallel run: the fold reads SOURCE, the reader reads POSTINGS.

    Narrow on purpose.  It proves the two answer alike on one shape's every day;
    making it exhaustive -- every generated shape, every day, plus real data --
    is step B2, which gates all of Phase C.  ``sampling is forbidden`` there: a
    14-day sample once scored perfect while wrong by $178,103.41 on 22% of days.
    """

    def test_fold_equals_the_ledger_reader_on_every_day(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Every day from the opening to the last period: fold == posted ledger.

        Two disjoint paths to one number -- the fold walks the loan's anchors and
        settled payment rows; the reader sums the posted journal legs the sync
        wrote -- so an agreement on every day is evidence the postings faithfully
        project the fold. That equality is what C3's cutover rests on.
        """
        from tests._test_helpers import freeze_today
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                _settle(seed_user, db, loan, period, Decimal("1000.00"))
            db.session.commit()
            last = seed_periods[6].start_date
            # Pin today past the window so every settled payment is posted.
            freeze_today(monkeypatch, last + timedelta(days=1))

            days = []
            day = _ORIGINATION_DATE
            while day <= last:
                days.append(day)
                day += timedelta(days=1)
            folded = _fold(loan, seed_user, days)

            mismatches = [
                (day, folded[day], read)
                for day in days
                if (read := posted_loan_balance_at(
                    loan.id, seed_user["scenario"].id, day,
                )) != folded[day]
            ]
            assert not mismatches, (
                f"fold and posted ledger disagree on "
                f"{len(mismatches)}/{len(days)} days: {mismatches[:5]}"
            )
            # Guard the loop itself: an agreement over three days would prove
            # nothing.  This spans origination to the last payment's period --
            # the whole pre-true-up year plus the payment window, day by day.
            assert days[0] == _ORIGINATION_DATE and days[-1] == last
            assert len(days) > 400


class TestFoldNegativeControls:
    """Every guard gets a control that is SHOWN to fire (plan Section 7.3)."""

    @pytest.mark.parametrize("bad_principal,expected", [
        (Decimal("0.00"), Decimal("100000.00")),
        (Decimal("500.00"), Decimal("99500.00")),
    ])
    def test_the_value_assertions_track_the_split_they_claim_to(
        self, app, db, seed_user, seed_periods, monkeypatch,
        bad_principal, expected,
    ):
        """Forcing the split's principal moves the fold by exactly that much.

        The teeth check.  ``test_one_payment_folds_off_its_REAL_principal``
        asserts $99,500.00; if the fold ignored the outcome's ``principal`` that
        assertion could pass for the wrong reason.  Here the replay's outcome is
        replaced by one carrying a chosen principal, and the fold tracks it:
        principal 0.00 leaves the anchor untouched, principal 500.00 reproduces
        the real answer.  So the fold provably reads the outcome, and the value
        tests are not passing unconditionally.  (The injection point was
        ``split_one_payment`` until plan step recurrence:R16-c-1 deleted that
        copy; the replay itself is the one producer now.)
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
            db.session.commit()

            real_replay = loan_ledger._walk.replay_loan_events

            def fake(seed, stream, extra_per_period=Decimal("0.00")):
                replay = real_replay(seed, stream, extra_per_period)
                return replace(replay, payments=[
                    replace(
                        outcome,
                        split=replace(outcome.split, principal=bad_principal),
                    )
                    for outcome in replay.payments
                ])

            monkeypatch.setattr(
                "app.services.loan_ledger._walk.replay_loan_events", fake,
            )
            on = last_covered_day(seed_periods[1])
            assert _fold(loan, seed_user, [on])[on] == expected


class TestTheSettledWalkChargesOncePerInstallment:
    """One interest accrual and one escrow per ACCRUAL PERIOD, not per payment.

    Plan step **X-au-g-2c-3b-2**.  The settled walk charged a month inside each
    payment's own split until this step, so two payments landing in one month
    charged the loan TWO months of interest and TWO months of escrow -- the
    payment COUNT was the clock, which is the defect plan step R16-a had already
    removed from the FORWARD fold.  These pin the settled half.

    **FOUR places in the suite give different money under the two rules, and
    this docstring claimed to be the only one until an adversarial review
    measured it false.**  The other three are pre-existing and all three FAILED
    on the cutover -- ``test_confirmed_view`` 's
    ``test_two_payments_in_one_due_month_show_as_two_rows_in_that_month`` and
    ``test_a_late_settled_payment_is_dated_at_its_installment_but_visible_late``,
    and ``test_posting_ledger_loan_reconciliation.test_the_reader_runs_beside_the_resolver`` 's
    ``test_biweekly_due_month_collision_reconciles_and_only_row_dates_differ``.
    All four are re-anchored on the one-charge rule (developer, 2026-09-02).
    *The false sentence is recorded rather than deleted because of what it was:
    a claim a fix wrote about ITSELF, which its own tests could not grade, and a
    reader who believed it would have concluded the change was measured against
    the only shape that moves and stopped looking.*

    Every OTHER loan test puts one payment in each month, where charging per
    payment and charging per period are the same arithmetic -- which is why the
    rest of the suite is untouched, and why a green suite alone would not have
    been evidence of anything.
    """

    def test_two_payments_in_one_month_charge_ONE_interest_and_ONE_escrow(
        self, app, db, seed_user, seed_periods,
    ):
        """Two $1,000 payments due 2026-02-01: one $500 accrual, one $100 escrow.

        The loan is trued to $100,000.00 on 2026-01-05 at 6% with a $1,200.00/yr
        ($100.00/mo) escrow line.  Periods 1 and 2 start on 2026-01-16 and
        2026-01-30, both before 2026-02-01, so both payments resolve to the SAME
        installment
        (``monthly_due_date`` gives the first payment_day-of-month on or after the
        period start), which is the shape this step exists to get right.

          Charge 2026-02-01: interest round(100,000.00 * 0.06 / 12) = $500.00,
                             escrow $1,200.00 / 12 = $100.00.
          P1 ($1,000.00): clears both -> principal 1000 - 500 - 100 = $400.00,
                          balance $99,600.00.
          P2 ($1,000.00): nothing stands -> principal $1,000.00, interest $0.00,
                          escrow $0.00, balance $98,600.00.

        Folded at 2026-02-12 (both settled and visible): $98,600.00.

        **Charging per PAYMENT gives $99,198.00** -- P2 would accrue
        round(99,600.00 * 0.005) = $498.00 and impound $100.00 again, paying only
        $402.00 down.  So the assertion is $598.00 away from the rule it replaced,
        and cannot pass under it.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, db, escrow_annual=Decimal("1200.00"),
            )
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
            _settle(seed_user, db, loan, seed_periods[2], Decimal("1000.00"))
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            # The precondition, asserted rather than assumed: both payments
            # satisfy ONE installment.  If a fixture change ever separated them
            # this test would silently stop measuring anything.
            assert [split.due_date for split in splits] == [
                date(2026, 2, 1), date(2026, 2, 1),
            ]
            assert [
                (split.interest, split.escrow, split.principal)
                for split in splits
            ] == [
                (Decimal("500.00"), Decimal("100.00"), Decimal("400.00")),
                (Decimal("0.00"), Decimal("0.00"), Decimal("1000.00")),
            ]

            on = last_covered_day(seed_periods[2])
            assert _fold(loan, seed_user, [on])[on] == Decimal("98600.00")
            # THE POSTED MONEY.  The corrections are a projection of this walk,
            # so the general ledger moves with it -- and the second payment's
            # correction is EMPTY (an all-principal payment owes none), which is
            # a path the writer already supported.
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, on,
            ) == Decimal("98600.00")

    def test_a_later_payment_in_a_period_carries_that_periods_rate(
        self, app, db, seed_user, seed_periods,
    ):
        """A rate change INSIDE an accrual period never re-rates a later payment.

        The split carries the rate period its interest accrued at, and the
        confirmed schedule row displays that rate.  Before plan step
        X-au-g-2c-3b-2 the split re-resolved the period on each payment's OWN due
        date, which agreed with the accrual only while one payment fell in each
        period.

        The loan is trued to $100,000.00 on 2026-01-05 at 6%, with a rate change
        to 12% effective 2026-02-15.  Two payments satisfy installments in ONE
        accrual period, on either side of that boundary:

          Charge 2026-02-01 (the EARLIEST installment in the period), at 6%:
                             interest round(100,000.00 * 0.005) = $500.00.
          P1 (due 02-01, $1,000.00): principal $500.00, balance $99,500.00.
          P2 (due 02-20, $1,000.00): nothing stands -> principal $1,000.00.

        P2's own due date sits in the 12% period -- asserted below, so the claim
        that it reads 6% is a measurement and not a coincidence of a loan with
        one rate.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            db.session.add(RateHistory(
                account_id=loan.id,
                effective_date=date(2026, 2, 15),
                interest_rate=Decimal("0.12"),
                monthly_pi=None,
            ))
            db.session.commit()
            _settle(
                seed_user, db, loan, seed_periods[2], Decimal("1000.00"),
                due_date=date(2026, 2, 1),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], Decimal("1000.00"),
                due_date=date(2026, 2, 20),
            )
            db.session.commit()

            # The negative control: the boundary really does fall between the two
            # installments, so re-resolving on P2's own due date would answer 12%.
            periods = loan_resolver.resolve_periods(
                loan_loaders.load_loan_params(loan.id),
                loan_loaders.load_rate_changes(loan.id),
            )
            assert period_for_date(
                periods, date(2026, 2, 20),
            ).annual_rate == Decimal("0.12")

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [split.due_date for split in splits] == [
                date(2026, 2, 1), date(2026, 2, 20),
            ]
            assert [split.interest for split in splits] == [
                Decimal("500.00"), Decimal("0.00"),
            ]
            # The docstring computes these, so the test asserts them: a walked
            # figure a docstring shows and no assertion checks is a claim the
            # suite cannot grade.
            assert [split.principal for split in splits] == [
                Decimal("500.00"), Decimal("1000.00"),
            ]
            on = last_covered_day(seed_periods[3])
            assert _fold(loan, seed_user, [on])[on] == Decimal("98500.00")
            # BOTH carry the accrual period's own rate -- one resolution, so the
            # rate a row displays is the rate its interest accrued at.
            assert [split.period.annual_rate for split in splits] == [
                Decimal("0.06"), Decimal("0.06"),
            ]

    def test_an_earlier_installment_arriving_later_REVERSES_the_posted_charge(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that becomes a period FOLLOWER has its posted charge reversed.

        The transition the cutover will actually exercise, and the one an
        incremental sync hides: whichever payment OPENS an accrual period carries
        its charge, and which payment that is can CHANGE when an earlier-due one
        arrives afterwards.  The first payment's correction must then reverse the
        interest and escrow it already posted, at the ``(period, entry_date)`` it
        posted them under, and its loan-linked leg must move with them.

        Settled in this order, both satisfying February:

          1. due 2026-02-20 (period 3), $1,000.00.  It is the only payment, so it
             OPENS the period: charge 02-20 -> interest $500.00, escrow $100.00,
             principal $400.00.  Those legs are POSTED.
          2. due 2026-02-01 (period 2), $1,000.00.  The charge is dated at the
             EARLIEST installment now, so it moves to 02-01 and the 02-01 payment
             clears it; the 02-20 payment becomes a follower and clears nothing.

        Final: (02-01) interest $500.00, escrow $100.00, principal $400.00;
        (02-20) interest $0.00, escrow $0.00, principal $1,000.00; owed
        $98,600.00.  **The posted balance agreeing is the assertion that matters**
        -- it can only hold if step 1's interest and escrow legs were reversed,
        since they are still on the ledger otherwise.

        The sibling test above cannot reach this: settling in pay-period order
        makes the opener arrive first, so its follower posts an EMPTY correction
        the first time and never has to reverse one.
        """
        with app.app_context():
            loan = _make_loan(
                seed_user, db, escrow_annual=Decimal("1200.00"),
            )
            _settle(
                seed_user, db, loan, seed_periods[3], Decimal("1000.00"),
                due_date=date(2026, 2, 20),
            )
            db.session.commit()
            # It opened the period on its own, and the charge WAS posted.
            (opener,) = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert (opener.interest, opener.escrow) == (
                Decimal("500.00"), Decimal("100.00"),
            )

            _settle(
                seed_user, db, loan, seed_periods[2], Decimal("1000.00"),
                due_date=date(2026, 2, 1),
            )
            db.session.commit()

            splits = loan_ledger.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id,
            )
            assert [
                (split.due_date, split.interest, split.escrow, split.principal)
                for split in splits
            ] == [
                (date(2026, 2, 1), Decimal("500.00"), Decimal("100.00"),
                 Decimal("400.00")),
                (date(2026, 2, 20), Decimal("0.00"), Decimal("0.00"),
                 Decimal("1000.00")),
            ]

            on = last_covered_day(seed_periods[3])
            assert _fold(loan, seed_user, [on])[on] == Decimal("98600.00")
            # THE REVERSAL.  Only true if the 02-20 payment's posted interest and
            # escrow legs were negated when it stopped opening the period.
            assert posted_loan_balance_at(
                loan.id, seed_user["scenario"].id, on,
            ) == Decimal("98600.00")



class TestTheFirstTrackingStartClearsMonthByMonth:
    """Ruling R-R117 through the real stack: the setup door's start, a real settle.

    The pure rule is pinned in ``test_loan_replay_one_stream.py`` on hand-built
    streams; this pins the one thing those cannot reach -- that a stored
    ``tracking_start`` row reaches the replay AS a tracking start (the stream's
    builder copies the fact's kind onto its reset event), so the walk, the fold
    and the POSTED ledger all split the held payment by R-R117.
    """

    def test_a_payment_the_start_holds_pays_its_own_month_on_every_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """R-R117's worked example: tracked from Feb 20, Feb 15's paid Feb 25.

        $30,000.00 at 5% from 2025-01-01, due the 15th, set up with a balance
        of $24,604.17 as of 2026-02-20 (the setup door's ``tracking_start``).
        Feb 15's $500.00 is then marked paid, its money moving Feb 25 (period
        3, 02-13..02-26).  The start holds it and is the loan's first balance,
        so every month before Feb 15 with no payment of its own is dropped:

          Feb 15: interest round(30,000.00 x 0.05 / 12) = $125.00,
                  principal 500.00 - 125.00 = $375.00, owing $29,625.00.
          Feb 20: the start corrects $29,625.00 to $24,604.17.

        Read on Feb 19: the opening held flat, $30,000.00.  Feb 20: the start
        plus the principal not yet visible, $24,979.17.  Feb 25: $24,604.17.
        Without R-R117 the payment paid thirteen months, $1,625.00 of interest
        and -$1,125.00 of principal, and Feb 20 read $23,479.17.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Tracked Loan",
                principal=Decimal("30000.00"), rate=Decimal("0.05000"),
                term=60, origination_date=date(2025, 1, 1), payment_day=15,
                tracked_balance=Decimal("24604.17"),
                tracked_from=date(2026, 2, 20),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[3], amount=Decimal("500.00"),
                settled_on=date(2026, 2, 25), due_date=date(2026, 2, 15),
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            [feb] = loan_ledger.compute_loan_payment_splits(loan.id, scenario_id)
            assert (feb.due_date, feb.interest, feb.principal) == (
                date(2026, 2, 15), Decimal("125.00"), Decimal("375.00"),
            )
            reads = {
                date(2026, 2, 19): Decimal("30000.00"),
                date(2026, 2, 20): Decimal("24979.17"),
                date(2026, 2, 25): Decimal("24604.17"),
            }
            assert _fold(loan, seed_user, list(reads)) == reads
            # THE POSTED MONEY: the start's correction and the payment's split
            # are posted by the same walk, so the general ledger reads the same
            # three days.
            assert {
                on: posted_loan_balance_at(loan.id, scenario_id, on)
                for on in reads
            } == reads
