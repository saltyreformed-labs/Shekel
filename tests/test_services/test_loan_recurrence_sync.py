"""Tests for loan_recurrence_sync: the resolver, and the one bound still written.

``app.services.loan_recurrence_sync`` answers a loan payment's CLOSING bound on
every read (:func:`~app.services.loan_recurrence_sync.loan_payment_window`,
the resolver plan step R7d-b built) and WRITES its OPENING bound from the
loan's contract (:func:`~app.services.loan_recurrence_sync.sync_loan_payment_start`,
ruling **R-R29**).  Until plan step R7d-g it also wrote the closing bound into
``budget.recurrence_rules.end_date`` from ten chokepoints, and the first class
here pinned that writer against real loans; **R7d-g deleted the writer**, so
that class now pins the opposite -- the column is NEVER written -- and the
payoff figures it used to assert on the column (``2028-07-01``,
``2028-08-01``) are asserted on the resolver, which is where they always came
from (``LoanFigures.closing_date``, plan step ``recurrence:R7d-h``).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.exceptions import BaselineMissingError, ValidationError
from app.services import balance_at, loan_recurrence_sync, template_amount_service
from app.models.transfer import Transfer
from app.services import pay_period_write, transfer_recurrence, transfer_service
from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    ClosesOn,
    DerivedStop,
    Empty,
    Indefinite,
    resolved_recurrence,
)
from app.services.loan_recurrence_sync import (
    loan_payment_window,
)
from app.services.loan_payment_service import compute_contractual_pi
from app.services.loan_loaders import load_loan_params, load_rate_changes
from app.models.transfer_template import TransferTemplate
from tests._test_helpers import (
    bind_rule_to_loan,
    create_account_of_type,
    create_loan_account,
    capture_sql_statements,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_every_period_rule,
    make_expense_template,
    make_loan_payment_template,
    make_transfer_template,
    rhythm_of,
    state_template_price,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY


def _window(template, ctx):
    """Ask the resolver about *template*, the way the composed door does.

    :func:`~app.services.loan_recurrence_sync.loan_payment_window` takes the
    definition's RESOLVED recurrence since plan step R7d-d rather than
    resolving the rule itself -- the door resolves it once to build the value
    and hands that down, so the EMPTY comparison reads the walk's first
    occurrence and never the stored column.  This is the same two-line
    composition ``recurring_definition.resolved_definition`` makes, held here
    so every case below asks the production question in the production shape.

    Args:
        template: A definition that carries a rule.  One that does not is the
            door's ``None`` ("does not repeat") and never reaches the resolver;
            ``test_recurring_definition`` holds that.
        ctx: The read pass.

    Returns:
        The resolver's answer.
    """
    return loan_payment_window(
        template,
        resolved_recurrence(template.recurrence_rule, ctx.calendar()),
        ctx,
    )


class TestTheClosingBoundIsNeverWritten:
    """The opening-only sync, driven directly against a resolvable loan (plan step R7d-g).

    ``sync_loan_payment_start`` is what ``sync_recurring_payment_bounds``
    became when its closing half was deleted: it re-derives the standing
    payment's ``starts_on`` from the loan's contract and touches NOTHING else.
    Every case here that used to assert a written ``end_date`` now asserts
    the column is left exactly as the owner stored it -- NULL, a date, or a
    count -- while the resolver still answers the payoff the column used to
    cache.  A mutation that restores the deleted write (any assignment to
    ``end_date`` or ``max_occurrences`` in the sync) turns the three
    survival cases red.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected schedule is deterministic."""
        freeze_today(monkeypatch, date(2026, 7, 1))

    def _loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originated 2025-01-01, with NO payments made."""
        return create_loan_account(
            seed_user, db_session, name="Recurring Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2025, 1, 1),
        )

    def _current_loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originating TODAY -- nothing overdue yet."""
        return create_loan_account(
            seed_user, db_session, name="Current Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 7, 1),
        )

    def test_a_current_loan_leaves_the_column_NULL_and_the_resolver_answers_the_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The control that used to read ``end_date == 2028-07-01`` off the column.

        Originated on the as-of, so its whole 24-month term is ahead of it and
        every installment is synthesized at the contractual P&I: the fold
        reaches zero on the contractual last installment, 2028-07-01.  That
        date is the RESOLVER's answer and nothing else's: the column stays
        NULL through the sync, and the composed door's derived stop is what
        generation and every display read (plan steps R7d-c-2, R7d-d).
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            assert rule.end_date is None

            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date is None
            assert rule.max_occurrences is None
            ctx = BalanceContext.build(seed_user["user"].id, date(2026, 7, 1))
            assert _window(tpl, ctx) == ClosesOn(on=date(2028, 7, 1))

    @pytest.mark.parametrize(
        ("end_date", "max_occurrences"),
        [
            pytest.param(None, None, id="unbounded"),
            pytest.param(date(2027, 3, 1), None, id="an owner's date"),
            pytest.param(None, 12, id="an owner's count"),
        ],
    )
    def test_a_stored_closing_bound_survives_the_sync_in_every_shape(
        self, app, db, seed_user, seed_periods, end_date, max_occurrences,
    ):
        """The column is the owner's word and the sync does not read it, let alone write it.

        Until plan step R7d-g the sync REPLACED whatever stood here with the
        derived payoff -- a date, and a count too (the crash plan step R7b-3's
        bound type made impossible).  Ruling **R-R82** keeps a stop authored
        before a definition became the standing payment, so all three shapes
        must come back untouched, on a loan whose derived payoff (2028-07-01)
        differs from every one of them.  Written around the form door, which
        refuses a stated bound on a standing payment; the sync must not be a
        second door.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            rule = tpl.recurrence_rule
            rule.end_date = end_date
            rule.max_occurrences = max_occurrences
            db.session.commit()

            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()
            db.session.refresh(rule)

            assert rule.end_date == end_date
            assert rule.max_occurrences == max_occurrences

    def test_a_start_that_would_pass_an_authored_stop_is_REFUSED_not_written(
        self, app, db, seed_user, seed_periods,
    ):
        """The one pair ``ck_recurrence_rules_valid_window`` refuses that a loan edit can reach.

        The standing payment carries an owner's stop (ruling **R-R82**) two
        days after its derived first installment; the loan's ``payment_day``
        then moves the installment past it.  Written, that pair is the
        constraint's ``IntegrityError`` out of an ordinary loan edit; so the
        sync refuses BEFORE writing, naming the transfer, and leaves both the
        start and the stop as they were.  The refusal is fired here rather
        than assumed: the same edit with the stop one day LATER goes through
        and moves the start.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()
            rule = tpl.recurrence_rule
            first_installment = rule.starts_on
            assert first_installment == date(2026, 8, 1)
            rule.end_date = date(2026, 8, 3)
            db.session.commit()

            params = load_loan_params(loan.id)
            params.payment_day = 15
            db.session.flush()

            with pytest.raises(ValidationError) as refused:
                loan_recurrence_sync.sync_loan_payment_start(loan.id)
            assert tpl.name in str(refused.value)
            assert "Aug 15, 2026" in str(refused.value)
            assert "Aug 3, 2026" in str(refused.value)
            db.session.rollback()
            db.session.refresh(rule)
            assert rule.starts_on == first_installment
            assert rule.end_date == date(2026, 8, 3)

            # The control: a stop the moved start does NOT pass is left alone
            # and the start moves.
            rule.end_date = date(2026, 8, 15)
            db.session.commit()
            params = load_loan_params(loan.id)
            params.payment_day = 15
            db.session.flush()
            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            assert rule.starts_on == date(2026, 8, 15)
            assert rule.end_date == date(2026, 8, 15)

    def test_the_refusal_is_graded_on_the_STORED_start_of_a_paycheck_rule(
        self, app, db, seed_user, seed_periods,
    ):
        """An every-paycheck payment stores its installment's PAYDAY, and that is what is graded.

        The contract's first installment 2026-08-15 (``payment_day`` 15) is
        hosted by the paycheck opening 2026-08-14 on this biweekly schedule,
        so the write door stores 08-14.  An owner's stop ON 08-14 is a valid
        stored pair and the edit goes through -- a comparison against the RAW
        installment would have refused it (an adversarial review of R7d-g-1
        found the first cut doing exactly that); a stop on 08-13 is refused,
        and the sentence names Aug 14, the date the row would have held, not
        Aug 15.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=EVERY_PERIOD,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 7, 31), (
                "precondition: the 08-01 installment is hosted by the paycheck "
                f"opening 07-31, got {rule.starts_on}"
            )

            rule.end_date = date(2026, 8, 14)
            db.session.commit()
            params = load_loan_params(loan.id)
            params.payment_day = 15
            db.session.flush()
            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            assert rule.starts_on == date(2026, 8, 14)
            assert rule.end_date == date(2026, 8, 14)

            # Back to the 1st (start 07-31), then a stop the 15th's payday
            # would pass.  The stop is set only once the start is back below
            # it: the table refuses the other order, which is the CHECK doing
            # its job on this test's own bare write.
            params = load_loan_params(loan.id)
            params.payment_day = 1
            db.session.flush()
            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()
            db.session.refresh(rule)
            assert rule.starts_on == date(2026, 7, 31)
            rule.end_date = date(2026, 8, 13)
            db.session.commit()
            params = load_loan_params(loan.id)
            params.payment_day = 15
            db.session.flush()
            with pytest.raises(ValidationError) as refused:
                loan_recurrence_sync.sync_loan_payment_start(loan.id)
            assert "Aug 14, 2026" in str(refused.value)
            assert "Aug 15, 2026" not in str(refused.value)
            db.session.rollback()

    def test_no_template_is_a_noop(self, app, db, seed_user, seed_periods):
        """A loan with no recurring transfer is a safe no-op (no crash)."""
        with app.app_context():
            loan = self._loan(seed_user, db.session)
            # No template created; the sync must return cleanly.
            loan_recurrence_sync.sync_loan_payment_start(loan.id)
            db.session.commit()

    def test_unconfigured_account_is_a_noop(self, app, db, seed_user):
        """A non-loan account with no recurring transfer is a safe no-op.

        Returns at the template check, before the loan is consulted at all.
        """
        with app.app_context():
            loan_recurrence_sync.sync_loan_payment_start(
                seed_user["account"].id,
            )
            db.session.commit()

    def test_an_amortizing_account_without_params_is_a_noop(
        self, app, db, seed_user, seed_periods,
    ):
        """A MORTGAGE-typed account with a recurring transfer but NO LoanParams.

        The not-a-loan guard's own shape, and it is reachable: an account whose
        TYPE is amortizing but whose loan details were never filled in still
        classifies as amortizing, so a transfer settling into it reaches the
        loan-side reconcile.  With no params there is no contract to derive a
        start from, and the sync must return before reading one.
        """
        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Unconfigured Mortgage",
            )
            db.session.flush()
            assert load_loan_params(acct.id) is None, (
                "precondition: this account must have NO LoanParams"
            )
            make_loan_payment_template(db.session, seed_user, acct)
            db.session.commit()

            loan_recurrence_sync.sync_loan_payment_start(acct.id)
            db.session.commit()


class TestIsStandingLoanPayment:
    """WHICH definition is the loan's standing payment -- asked once, off the pass.

    Plan step R7b-4 made this the ONE predicate the recurrence form's two bound
    controls lock on and the two crafted-POST refusals fire on, because the
    form was asking a DIFFERENT question and the two disagreed on live data:
    ``_recurrence_form_refusals.is_loan_payment`` reads
    ``settings is not None``, and neither of the developer's real loan payments
    carries a ``loan_payment_settings`` row -- so the R7b-3 "Ends" lock never
    fired on either loan and a typed end date would have been silently
    overwritten by the next payoff-affecting edit.

    **Since plan step R7d-f it is ``is_standing_loan_payment(template, ctx)``**
    and answers from the read pass's memoised loan resolution
    (``resolved_loan(...).standing``, the oldest definition) rather than
    re-running the two lookups the pass already held (plan ledger row
    **N-511**).  It no longer claims to name
    "the definition this module writes BOTH bounds for" -- ruling **R-R29**
    keeps only the opening bound written -- but the SET it names is unchanged,
    so every arm below is the arm it was.

    Every arm is exercised, and the THREE False ones are the point: a predicate
    that only ever returns True where it is asked is indistinguishable from no
    predicate.  Each False arm is a state in which
    :func:`~app.services.loan_recurrence_sync.sync_loan_payment_start`
    writes nothing for the template (no rule, not a configured loan, not the
    account's active payment).
    """

    @staticmethod
    def _ctx(seed_user):
        """Return the read pass the identity is read off."""
        return BalanceContext.build(seed_user["user"].id)

    def test_a_loans_active_recurring_payment_owns_its_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The True arm: exactly the template the opening-bound sync writes for."""
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_loan_payment_template(db.session, seed_user, loan)
            db.session.flush()

            assert balance_at.is_standing_loan_payment(template, self._ctx(seed_user)) is True

    def test_a_transfer_into_a_NON_loan_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The destination must be a CONFIGURED loan (``LoanParams``).

        Mirrors the sync's ``load_loan_params(...) is None`` return: a savings
        contribution has no contractual installment and no payoff, so nothing
        derives its bounds and its form must let the user set them.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            template = make_transfer_template(db.session, seed_user, savings)
            db.session.flush()

            assert balance_at.is_standing_loan_payment(template, self._ctx(seed_user)) is False

    def test_a_SECOND_recurring_payment_into_one_loan_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Only the template the lookup RETURNS is the one written for.

        ``active_recurring_transfer_template`` answers ONE active recurring
        transfer into the account, so a second is never synced -- and its form
        must therefore not claim its bounds come from the loan. This is the arm
        the older predicate got wrong in the other direction: both templates
        carry settings if both were created through the loan flow, so
        ``settings is not None`` would lock BOTH, and the user would be told
        two different rules were derived from one payoff.

        **Asserted as "exactly one", not "the first one", and the difference is
        a real property of the lookup**: it is ``.first()`` with no
        ``ORDER BY``, so which row PostgreSQL returns is unspecified. That is
        pre-existing and deliberate -- its own docstring calls two recurring
        transfers into one account "a user misconfiguration, not a modeled
        case" -- and pinning a particular winner here would assert something
        the query does not promise. What matters for the lock is that the two
        templates never both claim it.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            first = make_loan_payment_template(db.session, seed_user, loan)
            # Built directly: the shared helper hardcodes one name and
            # ``uq_transfer_templates_user_name`` refuses a second.
            second = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=loan.id,
                name="Second Payment Into One Loan",
            )
            db.session.add(second)
            db.session.flush()
            state_template_price(second, Decimal("50.00"))
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, second)
            db.session.flush()

            ctx = self._ctx(seed_user)
            owned = [
                balance_at.is_standing_loan_payment(first, ctx),
                balance_at.is_standing_loan_payment(second, ctx),
            ]
            assert owned.count(True) == 1, (
                f"exactly one of two recurring payments into one loan may own "
                f"its validity window, got {owned}"
            )

    def test_a_definition_that_does_not_repeat_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """No rule means no bounds to derive.

        Mirrors the sync's ``template.recurrence_rule is None`` return, and it
        is what stops a one-time transfer into a loan rendering locked
        controls for a rule that does not exist.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            template = make_loan_payment_template(db.session, seed_user, loan)
            template.recurrence_rule = None
            db.session.flush()

            assert balance_at.is_standing_loan_payment(template, self._ctx(seed_user)) is False

    def test_a_transaction_template_owns_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A kind with no destination account at all answers False, not raises.

        The two form helpers are deliberately kind-agnostic -- they render
        transaction templates through the same call -- so the predicate has to
        answer for a row that carries no ``to_account_id``.
        """
        with app.app_context():
            template = make_expense_template(db.session, seed_user)
            db.session.flush()

            assert balance_at.is_standing_loan_payment(template, self._ctx(seed_user)) is False


class TestTheDerivedStopShapes:
    """The three shapes, as pure values -- no loan, no database, no clock.

    :meth:`~app.services.recurrence.DerivedStop.admits` is the
    ONE question every shape answers, so every shape is asked it here and the
    boundary is asked on both sides.  A shape whose ``admits`` were left
    unwritten cannot be constructed at all (``@abstractmethod``), which is the
    half of the contract :class:`TestTheDerivedStopIsTotal` pins.
    """

    def test_a_closing_date_admits_its_own_day(self):
        """The payoff installment is OWED, so the bound is inclusive.

        The balance reaches zero AT that installment -- it is the payment that
        clears the loan, not the first one past it -- so an exclusive bound
        would drop the final payment from every projection.
        """
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2029, 2, 22)) is True

    def test_a_closing_date_refuses_the_day_after(self):
        """One day past the payoff is the first occurrence a walk must stop on."""
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2029, 2, 23)) is False

    def test_a_closing_date_admits_everything_before_it(self):
        """Every occurrence inside the loan's life stands."""
        assert ClosesOn(on=date(2029, 2, 22)).admits(date(2026, 4, 22)) is True

    def test_indefinite_admits_a_date_far_past_any_contract(self):
        """A loan that never pays off never stops its payments.

        Asked with a date past any term this application can express, because
        "unbounded" that has a hidden bound is the failure this shape exists to
        make unconstructible.
        """
        assert INDEFINITE.admits(date(2099, 12, 31)) is True

    def test_empty_refuses_the_first_occurrence_it_was_measured_against(self):
        """An empty window covers no date, including the one it opens on.

        Plan ledger row **D35**'s measurement: originate 2026-08-01 with a
        ``payment_day`` of 1 (first installment 2026-09-01) and true the balance
        to zero on 2026-08-15.  Nought occurrences is the CORRECT answer.
        """
        assert EMPTY.admits(date(2026, 9, 1)) is False

    def test_empty_refuses_a_date_before_the_window_too(self):
        """Not "everything after X" -- nothing at all, in both directions."""
        assert EMPTY.admits(date(2020, 1, 1)) is False

    def test_the_singletons_equal_a_freshly_built_shape(self):
        """A caller may compare against the module constant OR construct one.

        Frozen dataclasses compare by value, so the two spellings are one
        value; a reader holding ``INDEFINITE`` and a reader building
        ``Indefinite()`` cannot come to disagree.
        """
        assert INDEFINITE == Indefinite()
        assert EMPTY == Empty()
        assert INDEFINITE != EMPTY

    def test_an_empty_window_is_not_a_closing_date_that_admits_nothing(self):
        """The distinction the ``date``-or-``None`` shape could not carry.

        Both admit nothing, and that identity is deliberate -- generation emits
        nothing either way.  What a reader must be able to say about them
        differs: "until Aug 15, 2026" is false about a definition that fires
        from September, where "this loan is finished" is true.  If these ever
        compare equal, that distinction has been lost and every DISPLAY reader
        plan step R7d-d moves over inherits the wrong sentence.
        """
        closes_before_it_starts = ClosesOn(on=date(2026, 8, 15))
        assert closes_before_it_starts.admits(date(2026, 9, 1)) is False
        assert EMPTY.admits(date(2026, 9, 1)) is False
        assert closes_before_it_starts != EMPTY


class TestTheDerivedStopIsTotal:
    """A shape that does not answer ``admits`` cannot exist.

    The ``@abstractmethod`` is not decoration: the default it refuses -- "a
    shape that does not recognise the question keeps firing" -- is a
    commitment to go on charging a debt the owner has cleared.  Pinned by
    CONSTRUCTION rather than by reading the source, so a later step that adds a
    fourth shape and forgets the method fails here rather than in production.
    """

    def test_the_base_type_itself_cannot_be_instantiated(self):
        """``DerivedStop()`` is not a stop; it is the question."""
        with pytest.raises(TypeError):
            DerivedStop()  # pylint: disable=abstract-class-instantiated

    def test_a_shape_omitting_admits_cannot_be_instantiated(self):
        """The fourth-shape trap, sprung deliberately.

        This is what a half-written plan step R8 shape would look like; it must
        be a ``TypeError`` at construction and never a window that silently
        admits everything.
        """
        class _HalfWritten(DerivedStop):
            """A shape that states no rule for admitting an occurrence."""

        with pytest.raises(TypeError):
            _HalfWritten()  # pylint: disable=abstract-class-instantiated

    def test_every_concrete_shape_answers_admits(self):
        """Each shape returns a real bool, never ``None`` or a truthy stand-in.

        ``is True`` / ``is False`` rather than truthiness: a shape returning
        ``None`` would read as "does not admit" at every call site and pass a
        truthiness assertion while meaning nothing.
        """
        answers = [
            ClosesOn(on=date(2030, 1, 1)).admits(date(2029, 1, 1)),
            INDEFINITE.admits(date(2029, 1, 1)),
            EMPTY.admits(date(2029, 1, 1)),
        ]
        assert answers == [True, True, False]
        assert all(isinstance(answer, bool) for answer in answers)


#: One instance of each window shape, so immutability is asserted over the
#: WHOLE set rather than over three hand-written examples.  Held total by
#: ``TestTheWindowShapesAreValues.test_every_concrete_shape_is_sampled``.
_WINDOW_SAMPLES: dict[type[DerivedStop], DerivedStop] = {
    ClosesOn: ClosesOn(on=date(2029, 2, 22)),
    Indefinite: INDEFINITE,
    Empty: EMPTY,
}


class TestTheWindowShapesAreValues:
    """Frozen records -- a DERIVED window is not mutable state."""

    def test_every_concrete_shape_is_sampled(self):
        """A shape left out of the table narrows the sweep below in silence.

        The contract ``END_BOUND_KINDS`` gets in ``test_recurrence_bounds``:
        the property is asserted over the CLOSED SET, so a fourth shape a
        later step adds fails here rather than quietly sitting outside it.

        **Scoped to shapes declared in ``app/``, and this file is why.**
        ``TestTheDerivedStopIsTotal`` above declares ``_HalfWritten``
        inside a test body, ``__subclasses__()`` is a live interpreter-wide
        registry, and a class object survives until the cyclic collector takes
        it.  Unscoped, this gate fails whenever that test has already run --
        a failure unrelated to the code, which is broken rather than flaky.
        """
        declared_in_app = {
            kind for kind in DerivedStop.__subclasses__()
            if kind.__module__.startswith("app.")
        }

        assert declared_in_app == set(_WINDOW_SAMPLES)

    @pytest.mark.parametrize("kind", list(_WINDOW_SAMPLES))
    def test_no_shape_can_be_mutated_after_construction(self, kind):
        """Two of the three carry no field, and both are SHARED singletons.

        :data:`INDEFINITE` and :data:`EMPTY` hold no value, so the module
        builds ONE of each and hands the same object to every caller -- the
        comment beside them gives frozen-ness as the reason that is safe.
        Nothing asserted it until this test.

        Probed through ``admits``, the one name every shape declares: the base
        makes it ``@abstractmethod``, so a shape without one cannot be built
        at all (``TestTheDerivedStopIsTotal`` holds that), which makes it
        each shape's OWN name rather than an arbitrary one.  **One name is the
        whole claim** -- frozen is a property of the CLASS and is
        all-or-nothing, so a shape that refuses one name refuses every name,
        and walking ``dataclasses.fields`` instead would ask ZERO times for
        the two shapes that carry none.

        ``admits`` is also the name with the money on it.  An instance
        attribute shadowing the method answers for every holder of the
        singleton at once, and that is the difference between a loan that goes
        on charging a debt the owner has cleared and one that stops paying a
        debt they still owe.
        """
        with pytest.raises(FrozenInstanceError):
            setattr(_WINDOW_SAMPLES[kind], "admits", None)


class TestLoanPaymentWindowResolver:
    """The RESOLVER, against real loans (plan step R7d-b).

    Its first reader arrived at plan step R7d-d -- the composed door
    ``recurring_definition.resolved_definition``, which puts this answer on
    the resolved recurrence's ``Closing`` -- so these are no longer the whole
    of its coverage; ``test_recurring_definition`` grades what a surface does
    with the answer, and this grades the answer.

    **Two of the resolver's former refusals moved to the door with that step,
    and their cases moved with them.**  The resolver now TAKES the resolved
    recurrence, so a definition with no rule and an owner with no pay periods
    -- the two states in which there is no resolved value to hand it -- never
    reach it; the door answers *does not repeat* for both
    (``test_a_definition_with_no_rule_does_not_repeat`` and
    ``test_an_owner_with_no_pay_periods_reads_as_not_repeating`` in
    ``test_recurring_definition``).  The ``rule.starts_on`` fallback the
    second state used to take here is gone with it, so the stored column is
    unreachable from the EMPTY comparison by construction.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected schedule is deterministic."""
        freeze_today(monkeypatch, date(2026, 7, 1))

    def _ctx(self, seed_user):
        """The read pass every resolve in this class is measured against."""
        return BalanceContext.build(seed_user["user"].id, date(2026, 7, 1))

    def _current_loan(self, seed_user, db_session):
        """A 24-month $12,000 loan originating TODAY -- nothing overdue yet."""
        return create_loan_account(
            seed_user, db_session, name="Window Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 7, 1),
        )

    def test_a_live_loan_closes_on_its_DERIVED_payoff(
        self, app, db, seed_user, seed_periods,
    ):
        """The resolver answers the date the BALANCE folds to zero.

        The same $12,000 / 24-month / 5% loan the sync tests use, whose level
        P&I is $526.46 and whose fold therefore reaches zero on 2028-07-01
        (origination 2026-07-01, first installment 2026-08-01).  Asserted
        against the seam's own figure as well as against the date, so this
        cannot pass by agreeing with a hardcoded constant the seam has moved
        away from.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date == date(2028, 7, 1)

            assert _window(tpl, ctx) == ClosesOn(
                on=date(2028, 7, 1),
            )

    def test_unpaid_overdue_installments_push_the_payoff_out(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan whose past installments were never PAID pays off later (B-9).

        This 24-month loan originated 2025-01-01 and is read on 2026-07-01 with
        no settled payment at all, so it still owes the full $12,000.00 with only
        seven contractual installments left.  The pre-C8d bound came off the
        resolver's schedule walk, which amortizes an installment per month
        whether or not one was paid, and so reported the CONTRACTUAL 2027-01-01 --
        a payoff the borrower has not remotely earned.  The fold reports when the
        balance actually reaches zero: the seven remaining contractual
        installments plus the post-contractual extension (plan C8c) at the same
        level payment.  Hand-checked: the level P&I on $12,000.00 / 24 months /
        5% is $526.46, and $12,000.00 at 5%/12 amortizes in exactly 24 payments
        at that figure -- so a borrower who has paid NOTHING is a full 24
        installments from zero PLUS the arrears.  Since plan step R16-b-2 the
        seventeen skipped months from 2025-02-01 to 2026-06-01 each accrue
        their $50.00 of interest (ruling R-R71: a skipped month owes its
        interest whichever side of today it is on): with July's own charge,
        $900.00 stands when the first payment lands on 2026-07-01, which
        clears it and CAPITALIZES the $373.54 shortfall (balance $12,373.54);
        the 08-01 payment then pays $474.90 of principal, and counting from
        there the balance reaches zero on 2028-08-01 -- 26 payments, seven
        contractual installments and nineteen from the extension, 20 months
        past the contractual 2027-01-01.  It read 2028-06-01 while the skipped
        months charged nothing (B-9's holds-flat, repealed by the ruling).
        Re-derived with ``installment_interest`` / ``apply_payment_cash``
        after an adversarial review found this paragraph's first count wrong.

        **"Never paid" is a fact the OWNER states since plan step R16-b-2, and
        this fixture states it.**  Ruling **R-R64**: an occurrence the
        schedule places that no row in any state answers is "not generated
        yet" and the plan prices it as generation would -- so a loan whose
        rows were merely never generated is NOT delinquent, it is unplanned,
        and the plan would pay its 2026 installments the day after ``as_of``.
        What makes an installment unpaid is a row the owner un-planned: a
        cancelled or deleted row still answers its occurrence and pays
        nothing.  So the schedule is extended to cover ``as_of`` (a live
        schedule always does; ``seed_periods`` stops in May), the rows are
        GENERATED, and every overdue one is soft-deleted, which is the state a
        delinquent owner's books are in; the 2025 installments fall before the
        schedule opens (2026-01-02), where nothing is generated or estimated
        either way.

        Asserted on the RESOLVER since plan step R7d-g; until then the same
        figure was read off the column the sync wrote.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Recurring Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2025, 1, 1),
            )
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            pay_period_write.record_paydays(
                user_id=seed_user["user"].id,
                first_payday=seed_periods[0].start_date,
                num_periods=16,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            # Every SAVED period is open for writing -- the whole schedule,
            # not only the six paydays just recorded -- so every in-schedule
            # occurrence gets its row.
            generation_ctx = BalanceContext.build(tpl.user_id)
            transfer_recurrence.generate_for_template(
                tpl,
                GenerationSchedule.for_period_ids(
                    generation_ctx,
                    {
                        period.period_id
                        for period in generation_ctx.calendar().periods
                    },
                ),
                seed_user["scenario"].id,
            )
            db.session.flush()
            overdue = (
                db.session.query(Transfer)
                .filter(
                    Transfer.transfer_template_id == tpl.id,
                    Transfer.due_date < date(2026, 7, 1),
                )
                .all()
            )
            assert overdue, "precondition: the schedule reaches overdue installments"
            for row in overdue:
                transfer_service.delete_transfer(
                    row.id, seed_user["user"].id, soft=True,
                )
            db.session.commit()

            window = _window(tpl, self._ctx(seed_user))

            assert isinstance(window, ClosesOn)
            assert window.on > date(2027, 1, 1), (
                f"the payoff {window.on} is at or before the CONTRACTUAL "
                "payoff 2027-01-01, so the bound is still coming off the "
                "schedule walk that pays down installments nobody paid (B-9)."
            )
            assert window == ClosesOn(on=date(2028, 8, 1))
            assert tpl.recurrence_rule.end_date is None, (
                "the resolver's answer reached the column: a writer survived R7d-g"
            )

    def test_a_STATED_price_at_the_contractual_figure_closes_identically(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that STATES the contract's figure closes where DERIVE does.

        **The arm production is actually in.**  ``budget.loan_payment_settings``
        holds ZERO rows on the developer's database, so both live loan payments
        state a price rather than deriving one -- and plan step R7d-a made that
        distinction decide how every uncovered installment is priced.  An
        adversarial review found a fixture repair had moved every sync test
        into the DERIVE arm, where the new rule reduces to the old behaviour,
        leaving the production arm untested through any door.

        A definition stating exactly the contractual P&I must resolve to the
        same stop as one deriving it: 2028-07-01, the control above's figure,
        from the same $12,000.00 / 24-month / 5% loan whose level payment is
        $526.46.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            params = load_loan_params(loan.id)
            contractual_pi = compute_contractual_pi(
                params, load_rate_changes(loan.id),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan,
                amount=str(contractual_pi), derive_from_loan=False,
            )
            template_amount_service.set_amount(
                tpl, contractual_pi, effective_on=params.origination_date,
            )
            db.session.commit()

            assert _window(tpl, self._ctx(seed_user)) == ClosesOn(
                on=date(2028, 7, 1),
            )

    def test_a_SECOND_recurring_transfer_into_one_loan_gets_the_SAME_window(
        self, app, db, seed_user, seed_periods,
    ):
        """The property that DISSOLVES plan ledger row **D47** for the bound.

        Nothing states which recurring transfer into a loan is "the" payment,
        and
        :func:`~app.services.recurring_transfer_query.active_recurring_transfer_template`
        tie-breaks it on ``id``.  Measured on a production clone, a
        ``$200.00``/mo transfer into the Mortgage created before the real
        ``$1,910.95`` one wins that tie-break and drives the derived payoff
        from ``2048-12-01`` to ``None``.

        The resolver never asks: every recurring transfer into a loan is
        paying it down (the settled fold and the PLANNED tier both already sum
        every one of them with no template filter), and each stops when the
        loan does.  So the two definitions here must get the SAME answer --
        which is why the RESOLVER carries no "is it the one the lookup
        returns" clause at all.

        Contrast ``TestIsStandingLoanPayment``'s twin, which asserts exactly
        ONE of two such templates is the loan's standing payment: that
        identity survives for the form's two LOCKS and the door's cache arm
        (plan step R7d-f reads it off the pass), never for the resolver, and
        the difference between the two assertions is the whole ruling
        (**R-R35**).

        **What it does NOT assert is that the shared answer is RIGHT**, and an
        adversarial review of this step is why that is written down. The
        window's SUBJECT is no longer tie-broken, but its VALUE still reaches
        the tie-break through ``loan_figures`` -> ``standing_payment`` ->
        ``active_recurring_transfer_template``, so with the sweep winning that
        pick both definitions can agree on a WRONG window. Plan ledger row
        **D47** carries that half and **R16** closes it.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            # **The SWEEP is built FIRST, and that ordering is the point.**
            # ``active_recurring_transfer_template`` tie-breaks on ascending
            # ``id``, so a definition created earlier WINS it. Building the
            # loan payment first would let the tie-break pick the RIGHT
            # template, and the test would pass without exercising anything.
            # Built directly: the shared helper hardcodes one name and
            # ``uq_transfer_templates_user_name`` refuses a second.
            sweep = TransferTemplate(
                user_id=seed_user["user"].id,
                from_account_id=seed_user["account"].id,
                to_account_id=loan.id,
                name="Sweep Into One Loan",
            )
            db.session.add(sweep)
            db.session.flush()
            # Priced, as ``POST /transfers`` prices every definition it
            # creates: the forward plan sums this sweep's occurrences too
            # (plan step R16-b-2) and refuses a series nobody stated.
            state_template_price(sweep, Decimal("50.00"))
            # The definition first, then the cadence onto it (plan step R-F6).
            make_every_period_rule(db.session, sweep)
            payment = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            assert sweep.id < payment.id, (
                "precondition: the sweep must WIN the id tie-break, or this "
                "test never exercises the wrong pick"
            )

            ctx = self._ctx(seed_user)
            sweep_window = _window(sweep, ctx)
            payment_window = _window(payment, ctx)

            assert sweep_window == payment_window, (
                "two recurring transfers into one loan resolved to different "
                "windows, so the bound still depends on WHICH definition is "
                "asked -- the identity question R-R35 deletes"
            )

    def test_a_loan_that_never_pays_off_is_INDEFINITE(
        self, app, db, seed_user, seed_periods,
    ):
        """The shape that must not be spelled as a date.

        A $240,000 / 30-year contract at 6% trued up to $900,000: the ~$1,439
        level payment cannot cover $4,500 of monthly interest, so the balance
        grows and the fold never reaches zero.  The payments must keep
        generating -- the loan still owes -- which is why ``None`` from
        :attr:`~app.services.balance_at.LoanFigures.closing_date` is a window
        shape rather than a missing answer.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Never Clears",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("900000.00"),
            )
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date is None, "precondition: it never clears"
            assert figures.is_retired is False, "precondition: it still owes"

            assert _window(tpl, ctx) == INDEFINITE

    def test_a_RETIRED_loan_closes_on_the_day_it_BECAME_closed(
        self, app, db, seed_user, seed_periods,
    ):
        """A finished loan whose payment HAS already fired closes, not empties.

        The CONTROL for the EMPTY case below, and the pair is what proves the
        two shapes are told apart rather than collapsed.  The ONLY difference
        between them is where the loan's closing date falls relative to the
        rule's first occurrence.  This loan originated 2026-05-01 with a
        ``payment_day`` of 1, so its first contractual installment is
        2026-06-01, and it is trued to zero on 2026-06-15 -- AFTER that
        installment -- so the window is a real closing date.

        **The true-up is dated explicitly, and plan step ``recurrence:R7d-h``
        is why.**  A retired loan's bound used to be the read pass's own now
        (2026-07-01), so this case and the EMPTY one below were separated by
        nothing but their first occurrence and the helper's default true-up
        date -- one day after origination -- never mattered.  The bound is now
        the day the loan LAST became closed, a fact about the LOAN, so the
        true-up date IS the closing date and retiring this loan the day after
        origination would put it before the first installment and make this
        EMPTY too, collapsing the pair.

        The rule's opening bound is written by ``bind_rule_to_loan``, the
        production door, rather than by a fixture day -- so the date this rests
        on is the one the app itself derives from the contract.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 5, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 6, 15),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.is_retired is True, "precondition: it owes nothing"
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 6, 1), (
                "precondition: the first contractual installment is one month "
                f"after origination, got {rule.starts_on}"
            )
            assert rule.starts_on <= date(2026, 6, 15), (
                "precondition: this loan's payment had already fired when it "
                "was cleared, which is the ONLY thing separating it from the "
                "EMPTY case below"
            )
            assert figures.closing_date == date(2026, 6, 15), (
                "precondition: the loan closed on the day it was trued to "
                f"zero, got {figures.closing_date}"
            )

            assert _window(tpl, ctx) == ClosesOn(
                on=date(2026, 6, 15),
            )

    def test_a_loan_RETIRED_before_its_payment_first_fires_is_EMPTY(
        self, app, db, seed_user, seed_periods,
    ):
        """Plan ledger row **D35**'s own measurement, resolved.

        A loan originated 2026-06-20 with a ``payment_day`` of 15 owes its
        first installment 2026-07-15; true its balance to zero the day after
        origination and it retires on 2026-06-21, BEFORE the rule ever fires.
        That pair is ``[2026-07-15, 2026-06-21]``: correct at nought
        occurrences, and exactly the state
        ``ck_recurrence_rules_valid_window`` was drafted for and then HELD
        BACK on, because a CHECK cannot tell it from an owner's mistake and
        would turn a true-up into an unhandled ``CheckViolation``.

        **The closing date is the day the loan was CLEARED, not the read
        pass's now** (plan step ``recurrence:R7d-h``).  This case used to reach
        EMPTY because the retired bound was ``as_of`` and ``as_of`` happened to
        fall before the first installment; it now reaches it because the loan
        genuinely finished before its payment ever fired -- the same verdict
        for a reason that is a fact about the loan.

        The control above clears AFTER its first installment and reaches
        ``ClosesOn``, so what this pins is the EMPTY test itself and not the
        retired mapping.

        **A loan that has not ORIGINATED cannot stand in for this**, and the
        first draft of this test used one: an unborrowed loan owes ``$0.00``
        and is emphatically NOT retired (its whole debt line is ahead of it),
        so it resolves to a live ``ClosesOn`` at its contractual payoff.  The
        loan must be borrowed AND cleared.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired Before It Fires",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.terms.is_originated is True, (
                "precondition: the loan must be BORROWED, or it is not retired"
            )
            assert figures.is_retired is True, "precondition: it owes nothing"
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 7, 15), (
                "precondition: the first contractual installment is one month "
                f"after origination, got {rule.starts_on}"
            )
            assert figures.closing_date == date(2026, 6, 21), (
                "precondition: the loan closed BEFORE its first installment, "
                f"got {figures.closing_date}"
            )

            assert _window(tpl, ctx) == EMPTY

    def test_a_loan_RETIRING_ON_the_day_it_first_fires_is_NOT_empty(
        self, app, db, seed_user, seed_periods,
    ):
        """The boundary between the two shapes above: ``[D, D]`` is ONE occurrence.

        A loan originated 2026-06-01 with a ``payment_day`` of 1 owes its first
        installment 2026-07-01, and it is trued to zero ON that day, so its
        window closes on the date it first fires.  (Before plan step
        ``recurrence:R7d-h`` the retired bound was the read pass's now, which
        landed on 2026-07-01 by coincidence of the frozen clock; it is now the
        day the loan was cleared, so the true-up carries that date explicitly.)

        A window whose ends coincide is not
        empty -- it admits exactly the occurrence on that date, because
        ``ClosesOn`` is INCLUSIVE, and here that is the boundary being pinned
        rather than a claim about which installment cleared the loan: this
        loan was cleared by a true-up BEFORE its first installment ever fired,
        so the 2026-07-01 payment is emphatically not the one that paid it off.
        The inclusive comparison is what separates a window whose ends coincide
        from an empty one, and getting it wrong turns a loan's last payment
        into a loan that never had one.

        **This is the case the other two cannot see.** The control's closing
        date is a month past its first occurrence and the EMPTY case's is two
        weeks before, so ``closes < starts_on`` and ``closes <= starts_on``
        answer identically for both; only here do they differ, and getting it
        wrong turns a loan's last payment into a loan that never had one.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Retired On The Day",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 6, 1), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
                anchor_date=date(2026, 7, 1),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()

            ctx = self._ctx(seed_user)
            rule = tpl.recurrence_rule
            assert rule.starts_on == date(2026, 7, 1), (
                "precondition: the loan must CLOSE on its own first occurrence "
                f"for this boundary to exist, got {rule.starts_on}"
            )
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.is_retired is True
            assert figures.closing_date == rule.starts_on, (
                "precondition: the closing date and the first occurrence must "
                f"COINCIDE, got {figures.closing_date} vs {rule.starts_on}. "
                "Without this the test passes on the frozen clock rather than "
                "on the boundary it names -- the as-of also falls on this day"
            )

            window = _window(tpl, ctx)

            assert window == ClosesOn(on=date(2026, 7, 1))
            assert window.admits(date(2026, 7, 1)) is True

    def test_the_EMPTY_test_uses_the_RESOLVED_first_occurrence_not_the_column(
        self, app, db, seed_user, seed_periods,
    ):
        """The `PERIOD`-unit case that REFUTED this step's first premise.

        The premise was that ``budget.recurrence_rules.starts_on`` IS the
        rule's first occurrence, so the EMPTY test could read the column.
        True for MONTH, WEEK and YEAR -- ``_authoring._author`` writes
        ``resolve``'s normalised date, and it was measured equal for all 43
        live rules on a production clone -- and FALSE for ``PERIOD``, which
        ``_resolution`` re-normalises on every read to the START of the
        paycheck covering the stored date.  Once a schedule edit leaves that
        date mid-period, the walk's first occurrence lands BEFORE the column
        (plan ledger row **D39**'s drift).

        This test puts the rule in exactly that state and asserts the window
        the WALK implies.  Reading the column answers `EMPTY` -- "this loan is
        finished" about a definition with a live occurrence -- which is what
        plan step R7d-f would then tell the owner.

        The two dates are read off the calendar rather than hardcoded, so the
        test states the RELATION (the resolved occurrence precedes the stored
        column, and the loan's life ends between them) rather than a pair of
        literals a schedule change would silently invalidate.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Paycheck-Cadence Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 2), payment_day=1,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            # The PERIOD unit is this test's subject: only a paycheck-space
            # cadence normalises its first occurrence onto a payday, which is
            # the drift the column can then leave behind.  Stated, since the
            # fixture's default became the loan door's monthly rule at plan
            # step R16-b-2.
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=EVERY_PERIOD,
            )
            rule = tpl.recurrence_rule
            db.session.flush()

            ctx = self._ctx(seed_user)
            calendar = ctx.calendar()
            # Drift the column INTO a period rather than onto its payday --
            # the state a pay-schedule edit leaves behind, which no door
            # writes but every schedule rebuild can produce.
            resolved_first = rule.starts_on
            drifted = resolved_first + timedelta(days=3)
            rule.starts_on = drifted
            db.session.flush()
            assert resolved_recurrence(rule, calendar).starts_on == (
                resolved_first
            ), (
                "precondition: the resolved first occurrence must precede the "
                f"stored column; got {resolved_recurrence(rule, calendar).starts_on} "
                f"against a column of {drifted}"
            )

            # A read pass BETWEEN the two: the loan's life covers the real
            # occurrence and stops before the column claims the rule starts.
            between = BalanceContext.build(
                seed_user["user"].id, resolved_first + timedelta(days=1),
            )
            figures = balance_at.loan_figures(loan, between)
            assert figures.is_retired is True, "precondition: it owes nothing"

            window = _window(tpl, between)

            assert window == ClosesOn(on=figures.closing_date), (
                "the window was decided against the stored column, so a "
                "definition with a live occurrence reads as finished"
            )
            assert window.admits(resolved_first) is True

    def test_a_transfer_into_a_NON_loan_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """``None`` is "no loan bounds this", never a fourth window shape.

        A savings contribution has no contractual installment and no payoff, so
        nothing about a loan stops it and its own authored bound is the whole
        answer.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            assert _window(tpl, self._ctx(seed_user)) is None

    def test_an_amortizing_account_without_params_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """A MORTGAGE-typed account whose loan details were never filled in.

        Reachable rather than hypothetical: an account classifies as amortizing
        by TYPE, so a transfer can settle into one that has no ``LoanParams``
        at all.  The seam answers ``None`` for it BEFORE its scenario guard,
        which is what this rests on.
        """
        with app.app_context():
            acct = create_account_of_type(
                seed_user, db.session, "Mortgage", "Unconfigured Mortgage",
            )
            db.session.flush()
            assert load_loan_params(acct.id) is None, (
                "precondition: this account must have NO LoanParams"
            )
            tpl = make_loan_payment_template(db.session, seed_user, acct)
            db.session.commit()

            assert _window(tpl, self._ctx(seed_user)) is None

    def test_a_transaction_template_has_no_derived_window(
        self, app, db, seed_user, seed_periods,
    ):
        """A kind with no destination account answers ``None``, not raises.

        The readers plan steps R7d-d through R7d-f move over are kind-agnostic
        -- the Recurring surface and the two form helpers render transaction
        templates through the same calls -- so the resolver has to answer for a
        row that carries no ``to_account_id`` at all.
        """
        with app.app_context():
            tpl = make_expense_template(db.session, seed_user)
            db.session.commit()

            assert _window(tpl, self._ctx(seed_user)) is None

    def test_resolving_a_SECOND_definition_on_one_pass_costs_NO_queries(
        self, app, db, seed_user, seed_periods,
    ):
        """The cost property plan step R7d-c rests on: the pass answers once.

        Generation walks every recurring definition an owner has, so the
        resolver is about to be asked once per definition.  Two things make
        that free after the first loan it touches, and neither is an accident:
        ``TransferTemplate.to_account`` is ``lazy="joined"``, so the
        destination arrives with the template rather than costing a lookup per
        definition; and the loan's resolution and its payoff are both memoized
        on the :class:`~app.services.balance_at.BalanceContext`, so a second
        definition into the SAME loan re-reads nothing.

        **The templates are loaded BEFORE the capture opens**, deliberately: a
        capture that straddles the load charges the subject its own eager
        loads, and the control would then measure the fixture rather than the
        resolver.

        Asserted as a MARGINAL cost of zero rather than against an absolute
        statement count, which would break on any unrelated query the seam
        adds and would say nothing about the property this pins.

        **BOTH axes are measured, because an adversarial review of this step
        pointed out that only one of them is the shape generation walks.**
        Several definitions into ONE loan is the state
        ``active_recurring_transfer_template``'s own docstring calls a
        misconfiguration; definitions across DIFFERENT loans is the ordinary
        one, and each new loan legitimately pays a full ``resolve_loan_bundle``.
        So the second loan below is asserted to COST something -- a control
        that measured zero there would be measuring a memo keyed on nothing --
        while a further definition into a loan already touched costs nothing.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            make_loan_payment_template(db.session, seed_user, loan)
            for index in (2, 3):
                extra = TransferTemplate(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    name=f"Into One Loan {index}",
                )
                db.session.add(extra)
                db.session.flush()
                state_template_price(extra, Decimal("50.00"))
                make_every_period_rule(db.session, extra)
            db.session.commit()

            ctx = self._ctx(seed_user)
            # Staged OUTSIDE the capture: this is the query generation has
            # already run to find the definitions, and its eager loads are
            # its own cost rather than the resolver's.
            templates = (
                db.session.query(TransferTemplate)
                .filter_by(to_account_id=loan.id)
                .order_by(TransferTemplate.id)
                .all()
            )
            assert len(templates) == 3, "precondition: three definitions"
            # Resolved OUTSIDE the capture too: since plan step R7d-d the
            # resolver TAKES the resolved recurrence, so resolving the rule is
            # the door's cost -- reading the rule's columns and the pass's
            # memoized calendar -- and not the resolver's.
            resolved = {
                t.id: resolved_recurrence(t.recurrence_rule, ctx.calendar())
                for t in templates
            }
            # The first resolve warms the pass's loan resolution and payoff.
            first, first_statements = capture_sql_statements(
                lambda: loan_payment_window(
                    templates[0], resolved[templates[0].id], ctx,
                ),
            )
            rest, rest_statements = capture_sql_statements(
                lambda: [
                    loan_payment_window(t, resolved[t.id], ctx)
                    for t in templates[1:]
                ],
            )

            assert isinstance(first, ClosesOn)
            assert rest == [first, first], (
                "every definition into one loan must resolve to ONE window"
            )
            assert len(rest_statements) == 0, (
                f"resolving two further definitions into the same loan cost "
                f"{len(rest_statements)} queries after a first that cost "
                f"{len(first_statements)}; the pass is re-reading the loan "
                "per definition, which generation would pay per template: "
                + "; ".join(text for text, _params in rest_statements[:5])
            )

            # The OTHER axis: a SECOND loan is a second subject, so it costs.
            other_loan = create_loan_account(
                seed_user, db.session, name="Second Window Loan",
                principal=Decimal("8000.00"), rate=Decimal("0.04000"),
                term=24, origination_date=date(2026, 7, 1),
            )
            other_tpl = make_loan_payment_template(
                db.session, seed_user, other_loan,
            )
            db.session.commit()
            staged = db.session.get(TransferTemplate, other_tpl.id)
            other, other_statements = capture_sql_statements(
                lambda: _window(staged, ctx),
            )

            assert isinstance(other, ClosesOn)
            assert len(other_statements) > 0, (
                "a SECOND loan resolved for free, so this control is keyed on "
                "something that is not the loan and would read zero however "
                "many loans a generation pass walked"
            )

    def test_a_configured_loan_REFUSES_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-R30**: a producer needing a scenario refuses, never guesses.

        The early return this replaces left the last-written bound standing --
        and once plan step R7d-g stops writing the column there is nothing to
        stand, so the honest answer is the refusal every other producer needing
        a baseline already makes.  ONE application-level handler answers it
        (ruling **R-BW**), so no caller pre-checks.
        """
        with app.app_context():
            loan = self._current_loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None,
                as_of=date(2026, 7, 1),
            )
            with pytest.raises(BaselineMissingError):
                _window(tpl, ctx)

    def test_a_NON_loan_still_resolves_without_a_baseline_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """The not-a-loan answer is reached BEFORE the scenario guard.

        The other half of R-R30, and it is what keeps a savings or investment
        transfer's form rendering for an owner whose baseline is missing -- a
        broken invariant that must not take down every unrelated recurrence
        surface with it.  If the guard ever moves ahead of the loan test, this
        raises instead.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None,
                as_of=date(2026, 7, 1),
            )
            assert _window(tpl, ctx) is None
