"""The composed read door: a definition's rule AND its destination's own stop.

Plan step **R7d-d**.  ``recurring_definition`` is the one place the two halves
of "when does this stop" meet -- what the owner authored, read by the pure
recurrence package, and what the destination allows, folded by
``loan_recurrence_sync``.  These grade the composition itself; the shapes are
graded in ``test_recurrence_describe`` and the resolver in
``test_loan_recurrence_sync``.

**The load-bearing claim is that the narrowing reaches the WALK**, not merely
the returned value.  A door that composed the two into a field nobody read
would look identical from the outside, so the tests below assert placements and
occurrence counts rather than only the ``Closing`` on the value.
"""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.pay_period import PayPeriod
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import books_start
from app.services.recurrence import (
    EMPTY,
    INDEFINITE,
    NEVER_ENDS,
    ClosesOn,
    EndsAfterOccurrences,
    EndsOnDate,
    RecurrenceResolutionError,
    describe,
    occurrence_placements,
    occurrences,
    reauthor_rule,
    recurrence_spec,
    resolved_recurrence,
)
# Pylint: ``shekel-private-module-import`` -- imported as a MODULE so the
# resolve-once control below patches the name the read door resolves at CALL
# time; patching this file's imported name would leave the composition calling
# the real one, and the package's public surface exports no module to patch.
from app.services.recurrence import _reading  # pylint: disable=shekel-private-module-import
# Pylint: ``shekel-private-module-import`` -- the same reason, one memo over:
# the pass walks a resolved recurrence through the name ITS module imported
# (plan step R7d-f-2), so the walk-once control patches
# ``_recurrence_memos.occurrence_walk`` (the module of the context's mixin
# since ruling R-BAL146).
from app.services.balance_at import _recurrence_memos  # pylint: disable=shekel-private-module-import
from app.services.balance_at import is_standing_loan_payment
from app.services.recurring_definition import (
    read_definition,
    resolved_definition,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD, MONTHLY
from tests._test_helpers import (
    bind_rule_to_loan,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    insert_trueup_event,
    loan_params_for,
    make_expense_template,
    make_loan_payment_template,
    make_transfer_template,
)

#: The day every pass in this module is measured at.  Frozen so the loan fold
#: and the derived payoff are deterministic.
_TODAY = date(2026, 7, 1)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today mid-loan so the projected schedule does not drift."""
    freeze_today(monkeypatch, _TODAY)


def _ctx(seed_user):
    """Return the read pass every composition here is measured against."""
    return BalanceContext.build(seed_user["user"].id, _TODAY)


def _restate_bound(rule, bound, ctx):
    """Re-author *rule* with a different closing bound, through the write door.

    ``reauthor_rule`` replaces a rule's WHOLE authored state, which is the
    package's partial-change idiom: read the spec, replace the one fact, write
    it back.  Reaching for the column directly would author a state the write
    door cannot produce, and the point of these tests is what a real stored
    bound does.

    Args:
        rule: The rule to re-author.
        bound: Its new closing bound.
        ctx: The read pass, for the owner's calendar.
    """
    reauthor_rule(
        rule, replace(recurrence_spec(rule), end_bound=bound), ctx.calendar(),
    )


def _loan(seed_user, db_session, **kwargs):
    """Return a 24-month $12,000 loan at 5%, originating today by default."""
    defaults = {
        "name": "Door Loan",
        "principal": Decimal("12000.00"),
        "rate": Decimal("0.05000"),
        "term": 24,
        "origination_date": _TODAY,
    }
    defaults.update(kwargs)
    return create_loan_account(seed_user, db_session, **defaults)


def _second_transfer_into(seed_user, db_session, loan):
    """Return ``(first, second)``: two recurring transfers paying *loan*.

    The FIRST is the loan's standing payment -- ``is_standing_loan_payment``
    names the account's oldest active recurring transfer -- whose opening
    bound the app writes and whose "Ends" control the form locks.  The
    SECOND's bounds are its owner's from every door, which makes it the
    subject for every case about an AUTHORED bound that a form can state.
    (Until plan step R7d-g the first's column was the chokepoints' cache and
    the door read it as such, ruling **R-R56**; every stored bound is the
    owner's word now, ruling **R-R82**, so the split is of which door WROTE
    it, not of how the door READS it.)  The first is renamed before the
    second is built because the helper names a payment after its loan and
    the pair is unique per owner.

    Args:
        seed_user: The owner.
        db_session: The test session.
        loan: The loan both transfers pay into.

    Returns:
        ``(first, second)``, flushed and not yet committed.
    """
    first = make_loan_payment_template(db_session, seed_user, loan)
    first.name = f"App-bounded payment {loan.id}"
    db_session.flush()
    # The second is the generic form's shape -- a STATED sweep with no
    # settings row -- and it states one dollar every PAYCHECK (the shared
    # builder's cadence).  Since plan step R16-b-2 the forward plan SUMS every
    # definition into the loan, so a second FULL payment here would retire
    # the loan in half its term and move every payoff these cases pin
    # (finding **D47** closing is the point, not a side effect); a dollar a
    # paycheck is a real second definition whose fifty-odd dollars over the
    # term the last installment's refund absorbs, so the loan's own stop
    # stays where the contract puts it.
    second = make_transfer_template(db_session, seed_user, loan, amount="1.00")
    second.name = f"Owner-bounded sweep {loan.id}"
    db_session.flush()
    return first, second


@pytest.mark.usefixtures("seed_periods")
class TestWhatTheDoorComposes:
    """The derived stop reaches the resolved value, or is honestly absent."""

    def test_a_definition_with_no_rule_does_not_repeat(
        self, app, db, seed_user,
    ):
        """``None`` means "does not repeat", passed through unchanged.

        The door must not invent a reading for a one-off charge: the absence
        of a ``budget.recurrence_rules`` row naming the definition IS how a
        definition says it does not repeat.

        **The definition pays into a LOAN, deliberately.**  Since plan step
        R7d-d the resolver takes the resolved recurrence, and a one-time
        transfer into a loan is the state in which there is none to hand it --
        so this is where a door that asked the loan before checking the rule
        would fail, and a savings transfer could not show that.  (The
        resolver's own case for this state moved here with that step.)
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            tpl.recurrence_rule = None
            db.session.commit()

            ctx = _ctx(seed_user)

            assert resolved_definition(tpl, ctx) is None
            reading = read_definition(tpl, ctx)
            assert reading.resolved is None
            assert len(reading.placements) == 0

    def test_a_destination_that_is_not_a_loan_carries_no_derived_stop(
        self, app, db, seed_user,
    ):
        """The 41-of-46 case, and ``None`` here is an answer rather than a gap.

        A transfer into a savings account has no derived stop, so its closing
        holds the authored bound alone -- which is what makes "this step
        changes no rendered character for a non-loan row" a property the suite
        holds rather than a claim the commit body makes.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Rainy Day",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert resolved.closing.derived is None
            assert resolved.closing.authored == NEVER_ENDS

    def test_an_expense_template_pays_into_no_account_at_all(
        self, app, db, seed_user,
    ):
        """A ``TransactionTemplate`` has no destination, so it has no stop.

        The door is kind-agnostic on purpose: the Recurring surface hands it
        income, expense and transfer definitions from one loop, and a producer
        that raised on two of the three would push the branch back up into the
        surface it exists to keep simple.
        """
        with app.app_context():
            tpl = make_expense_template(db.session, seed_user)
            db.session.commit()

            resolved = resolved_definition(tpl, _ctx(seed_user))

            assert resolved.closing.derived is None

    def test_a_loan_payment_carries_the_loans_derived_payoff(
        self, app, db, seed_user,
    ):
        """The whole point: the stop is ASKED for, never read off a column.

        Asserted against the seam's own ``payoff_date`` as well as against the
        date, so it cannot pass by agreeing with a constant the seam has since
        moved away from.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()

            ctx = _ctx(seed_user)
            figures = balance_at.loan_figures(loan, ctx)
            assert figures.payoff_date == date(2028, 7, 1)

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))

    def test_a_stored_stop_on_the_standing_payment_is_the_owners_word(
        self, app, db, seed_user,
    ):
        """Ruling **R-R82** (plan step R7d-g): the column is the owner's word, and it binds.

        Until R7d-g this case pinned the OPPOSITE: the column was the ten
        chokepoints' CACHE of the loan's payoff for the definition the app
        bounded, so the door read it as no authored bound at all (ruling
        **R-R56**) and a stale EARLIER date reached neither half.  R7d-g
        deleted the writers and NULLed the cache, so a date standing in that
        column can only be one an owner authored -- kept from before the
        definition became the loan's standing payment -- and the door
        composes it as the authored half beside the derived stop.  The walk
        stops at it and the phrase names it.

        **The derived half is INDEFINITE here, and that is the ruling read
        from the other side.**  The forward plan honours the same authored
        stop (ruling **R-R37**, the summed ESTIMATED tier of plan step
        R16-b-2): a $12,000 loan whose only payment stops after six
        installments never folds to zero, so the loan has no closing date.
        The two halves are two readings of ONE stored fact -- the owner said
        the money stops in January -- where the deleted cache arm had the
        plan honour a bound the door then ignored.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            rule = tpl.recurrence_rule
            authored = date(2027, 1, 1)
            _restate_bound(rule, EndsOnDate(on=authored), _ctx(seed_user))
            db.session.commit()

            ctx = _ctx(seed_user)
            resolved = resolved_definition(tpl, ctx)
            narrowed = list(occurrences(
                resolved, ctx.calendar(), through=date(2030, 1, 1),
            ))

            assert rule.end_date == authored, "precondition: the column holds it"
            assert is_standing_loan_payment(tpl, ctx), (
                "precondition: this is the loan's standing payment"
            )
            assert resolved.closing.authored == EndsOnDate(on=authored)
            assert resolved.closing.derived == INDEFINITE, (
                "the plan did not honour the owner's stop: with the standing "
                "payment ending 2027-01-01 the loan cannot pay off, so its "
                f"derived stop must be INDEFINITE, got {resolved.closing.derived!r}"
            )
            assert describe(resolved).stops == "until Jan 01, 2027"
            assert narrowed, "precondition: it fires at all"
            assert max(narrowed) <= authored, (
                "the owner's stop did not bind the walk: a cache arm survived R7d-g"
            )

    def test_a_null_column_composes_no_authored_bound_and_the_payoff_binds(
        self, app, db, seed_user,
    ):
        """The state every live loan payment is in since the R7d-g migration.

        With nothing stored, the authored half is ``NEVER_ENDS`` and the
        derived stop is the whole answer: the phrase names the payoff and the
        walk runs to it.  This is the same reading the deleted cache arm used
        to MANUFACTURE for the standing payment; it holds now because the
        column is empty rather than because an arm ignores it.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            assert tpl.recurrence_rule.end_date is None
            assert tpl.recurrence_rule.max_occurrences is None

            ctx = _ctx(seed_user)
            resolved = resolved_definition(tpl, ctx)
            narrowed = list(occurrences(
                resolved, ctx.calendar(), through=date(2030, 1, 1),
            ))

            assert resolved.closing.authored == NEVER_ENDS
            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))
            assert describe(resolved).stops == "until Jul 01, 2028"
            assert narrowed and max(narrowed) <= date(2028, 7, 1)
            assert max(narrowed) > date(2027, 1, 1)


@pytest.mark.usefixtures("seed_periods")
class TestTheNarrowingReachesTheWalk:
    """A composed value nobody walks under would look identical from outside."""

    def test_the_loans_life_truncates_the_occurrences(
        self, app, db, seed_user,
    ):
        """The rule names occurrences past the payoff; the walk stops at it.

        The rule is authored unbounded, so nothing but the loan can stop it --
        and every occurrence the pure walk emits past 2028-07-01 is a payment
        the app would project against a debt that is gone.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)
            calendar = ctx.calendar()
            # PAST the saved horizon deliberately.  ``occurrence_placements``
            # walks only as far as the schedule reaches (2026-05-21 for this
            # fixture) and the payoff is two years beyond it, so a placement
            # count could not see the truncation at all -- the walk is asked
            # directly, with a window that spans the payoff.
            beyond = date(2030, 1, 1)
            payoff = date(2028, 7, 1)

            unnarrowed = list(occurrences(
                resolved_recurrence(tpl.recurrence_rule, calendar),
                calendar, through=beyond,
            ))
            narrowed = list(occurrences(
                resolved_definition(tpl, ctx), calendar, through=beyond,
            ))

            assert narrowed, "precondition: the definition fires at all"
            assert len(narrowed) < len(unnarrowed), (
                "the derived stop reached the value but not the walk"
            )
            assert max(narrowed) <= payoff
            assert max(unnarrowed) > payoff, (
                "precondition: the rule's own bound does not stop it here, so "
                "the loan is the only thing that can"
            )

    def test_an_earlier_authored_bound_still_binds(
        self, app, db, seed_user,
    ):
        """ANDed, never substituted -- the direction that costs money.

        A closing bound the owner authored is a real input: a rule that
        ignored it in favour of the loan's payoff would model cash the owner
        has said will stop moving.  The authored date here precedes the
        payoff, so it is the one that must bind.

        **The bound is authored on a SECOND transfer into the loan**, the
        definition whose "Ends" control a form offers.  (The standing
        payment's is locked, and until plan step R7d-g its column was read as
        the chokepoints' cache under ruling **R-R56**, so this case could not
        have fired on it; it would now -- ruling **R-R82** -- and
        ``TestWhatTheDoorComposes`` holds that case.)
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            first, tpl = _second_transfer_into(seed_user, db.session, loan)
            db.session.commit()
            authored = date(2027, 3, 1)
            _restate_bound(
                tpl.recurrence_rule, EndsOnDate(on=authored), _ctx(seed_user),
            )
            db.session.commit()
            ctx = _ctx(seed_user)
            assert is_standing_loan_payment(first, ctx), (
                "precondition: the app bounds the first transfer"
            )
            assert not is_standing_loan_payment(tpl, ctx), (
                "precondition: the app does not bound this one"
            )

            resolved = resolved_definition(tpl, ctx)
            # Walked PAST the fixture's horizon, as the truncation case above
            # is.  ``read_definition``'s placements reach only as far as the
            # schedule does (2026-05-21), which precedes the authored date by
            # 21 months -- so "every placement is on or before 2027-03-01"
            # held with NO bound at all, and a first draft of this test
            # asserted exactly that.  An adversarial review of the step caught
            # it; the walk is asked directly, through a window that spans both
            # stops.
            beyond = date(2030, 1, 1)
            narrowed = list(occurrences(resolved, ctx.calendar(), through=beyond))

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1)), (
                "precondition: the loan alone would run 16 months past the "
                "authored date, so only the authored bound can stop the walk "
                "at it"
            )
            assert narrowed, "precondition: it fires at all"
            assert max(narrowed) <= authored
            assert max(narrowed) >= authored - timedelta(days=31), (
                f"the walk stopped at {max(narrowed)}, well short of the "
                f"authored {authored}, so something other than that bound -- a "
                f"horizon -- ended it"
            )
            assert describe(resolved).stops == "until Mar 01, 2027"

    def test_a_loan_that_closed_before_its_first_firing_names_nothing(
        self, app, db, seed_user,
    ):
        """The EMPTY window, and the cell that must not name its date.

        Originated 2026-06-20 with a ``payment_day`` of 15, so the first
        installment is 2026-07-15; trued to zero the day after origination
        (the helper's default) it retires on 2026-06-21, before the definition
        ever fires, and the window ``[2026-07-15, 2026-06-21]`` is correct at
        nought occurrences.  The closing date is the day the loan was CLEARED,
        not the read pass's now (plan step ``recurrence:R7d-h``), so the
        window is stable across read dates.
        """
        with app.app_context():
            loan = _loan(
                seed_user, db.session, name="Closed First",
                origination_date=date(2026, 6, 20), payment_day=15,
            )
            insert_trueup_event(
                loan_params_for(db.session, loan.id), Decimal("0.00"),
            )
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=15,
            )
            bind_rule_to_loan(tpl.recurrence_rule, loan.id)
            db.session.commit()
            ctx = _ctx(seed_user)
            assert balance_at.loan_figures(loan, ctx).closing_date == (
                date(2026, 6, 21)
            ), "precondition: the loan closed the day after it originated"

            reading = read_definition(tpl, ctx)

            assert reading.resolved.closing.derived == EMPTY
            assert len(reading.placements) == 0
            assert describe(reading.resolved).stops == "never runs"


class TestWhatTheDoorRefuses:
    """The refusals it inherits, and the ORDER it inherits them in."""

    def test_an_owner_with_no_pay_periods_reads_as_not_repeating(
        self, app, db, seed_user,
    ):
        """The one refusal ``resolved_recurrence`` swallows, passed through.

        The Recurring surface renders every definition a user has, and taking
        a whole page to a 500 for a schedule state no rule of this rule's is
        wrong about would be a fence rather than a fix.

        **The resolver never meets this state since plan step R7d-d.**  It
        takes the resolved recurrence, and an owner with no pay periods has
        none to hand it -- so the door answers ``None`` before the loan is
        asked, nothing says "finished" about the definition, and the
        ``rule.starts_on`` fallback the resolver used to carry for this state is
        deleted rather than kept for a caller that cannot reach it.  (What the
        Recurring surface does with that ``None`` for a rule-bearing definition
        is RAISE, as a broken invariant; ``test_recurring_view`` holds that.)

        **The definition pays into a LOAN, deliberately**, built while the
        bootstrap period still exists and the schedule emptied afterwards --
        the order the state arises in.  With a loan behind it this is the state
        where a door that handed ``resolved=None`` down would fail at
        ``resolved.starts_on``; the resolver's own case for it moved here.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session, name="No Schedule Loan")
            tpl = make_loan_payment_template(
                db.session, seed_user, loan, cadence=MONTHLY, fires_on_day=1,
            )
            db.session.commit()
            db.session.query(PayPeriod).filter_by(
                user_id=seed_user["user"].id,
            ).delete(synchronize_session=False)
            db.session.flush()

            ctx = _ctx(seed_user)
            assert not ctx.calendar().periods

            assert resolved_definition(tpl, ctx) is None
            assert len(read_definition(tpl, ctx).placements) == 0

    @pytest.mark.usefixtures("seed_periods")
    def test_a_cross_owner_pairing_is_refused_before_the_loan_is_folded(
        self, app, db, seed_user, second_user,
    ):
        """The order inside the door is graded, not merely described.

        ``resolved_recurrence`` refuses a rule paired with another owner's
        calendar with the rule's own exception.  The read pass would refuse the
        foreign loan too (``ForeignAccountError`` from ``_memoize_once``, plan
        step X-i4), but only after loading the account -- so the door resolves
        first, and this asserts the RULE's refusal alone.  Accepting either
        exception, as a first draft did, would have passed with the two calls
        reversed.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            foreign = BalanceContext.build(second_user["user"].id, _TODAY)

            with pytest.raises(RecurrenceResolutionError):
                resolved_definition(tpl, foreign)


@pytest.mark.usefixtures("seed_periods")
class TestTheDoorResolvesTheRuleOnce:
    """One rule, one resolution per pass -- ``CLAUDE.md`` rule 14's ONE WALK."""

    def test_a_loan_payment_resolves_its_rule_exactly_once(
        self, app, db, seed_user, monkeypatch,
    ):
        """The resolver TAKES the resolved recurrence; it does not re-derive it.

        Its EMPTY comparison needs the definition's first occurrence, and the
        first build of this step had ``loan_payment_window`` resolve the rule
        again on its own to get one -- the same rule, twice, on one pass.  The
        door now hands down the value it already built.  Counted at the
        definition site (``_reading.resolve``), and the control is shown to
        fire: the composed value carries the loan's stop, so the door did run.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            calls = []
            real_resolve = _reading.resolve

            def counting_resolve(spec, calendar):
                calls.append(spec.unit)
                return real_resolve(spec, calendar)

            monkeypatch.setattr(_reading, "resolve", counting_resolve)

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.derived == ClosesOn(on=date(2028, 7, 1))
            assert len(calls) == 1, (
                f"one loan payment resolved its rule {len(calls)} times on "
                f"one pass"
            )


@pytest.mark.usefixtures("seed_periods")
class TestTheDoorWalksTheDefinitionOnce:
    """One composed meaning, one walk per pass (plan step R7d-f-2, row N-513).

    R16-b-2 made the second RESOLUTION of a definition on one pass a memo hit;
    the second WALK still ran (measured 2026-09-12 before this step on a
    ``/savings`` render: ``resolve`` once, ``occurrence_placements`` twice for
    one checking-to-goal transfer).  The walk is the pass's memo now
    (``BalanceContext.placements_of``), keyed by the composed resolved value
    -- the walk's own input, the shape ruling **R-R73** chose for the
    resolution.  Counted at the DEFINITION site the memo calls, and every
    control is shown to fire before its count is read.
    """

    def _counting_walk(self, monkeypatch):
        """Patch the pass's walk with a counter; return the list it fills."""
        calls = []
        real = _recurrence_memos.occurrence_walk

        def counting(resolved, calendar, **kwargs):
            calls.append(resolved)
            return real(resolved, calendar, **kwargs)

        monkeypatch.setattr(_recurrence_memos, "occurrence_walk", counting)
        return calls

    def test_reading_one_definition_twice_walks_it_once(
        self, app, db, seed_user, monkeypatch,
    ):
        """The second ``read_definition`` on a pass is memo hits end to end."""
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Goal",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            ctx = _ctx(seed_user)
            calls = self._counting_walk(monkeypatch)

            first = read_definition(tpl, ctx)
            second = read_definition(tpl, ctx)

            assert first.placements, "precondition: the walk placed something"
            assert second.placements is first.placements, (
                "the second read must hand back the memoised tuple itself"
            )
            assert len(calls) == 1, (
                f"one definition read twice on one pass walked {len(calls)} "
                f"times; the walk is the pass's memo"
            )

    def test_two_definitions_with_one_composed_meaning_share_one_walk(
        self, app, db, seed_user, monkeypatch,
    ):
        """Keyed by the VALUE: what the walk cannot tell apart, the memo does not.

        Two every-paycheck transfers into the same savings account resolve to
        one composed value (same cadence, same first occurrence, no derived
        stop), and the walk of that value is one walk however many rows state
        it.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Goal",
            )
            one = make_transfer_template(db.session, seed_user, savings)
            one.name = "First sweep"
            two = make_transfer_template(
                db.session, seed_user, savings, amount="50.00",
            )
            two.name = "Second sweep"
            db.session.commit()
            ctx = _ctx(seed_user)
            assert resolved_definition(one, ctx) == resolved_definition(two, ctx), (
                "precondition: the two rows state one composed meaning"
            )
            calls = self._counting_walk(monkeypatch)

            assert read_definition(one, ctx).placements
            read_definition(two, ctx)

            assert len(calls) == 1

    def test_two_definitions_with_different_stops_walk_twice(
        self, app, db, seed_user, monkeypatch,
    ):
        """The composed CLOSING is part of the key, so a loan's stop misses.

        A transfer into a savings account and the loan's own payment share a
        cadence family but not a closing -- the loan's carries the derived
        payoff -- so they are two values and two walks.  The control that
        says the key is the COMPOSED value and not the spec alone.

        The loan originates on the day Goal's books start, so the two books
        floors agree and the closing is the one thing telling them apart: a
        loan payment's floor is the loan's origination (ruling plan#3954,
        task plan#4087), where it was the loan's opening record, which the
        fixtures date the same day as Goal's.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Goal",
            )
            loan = _loan(
                seed_user, db.session,
                origination_date=books_start(savings.id).opened_on,
            )
            payment = make_loan_payment_template(
                db.session, seed_user, loan, cadence=EVERY_PERIOD,
            )
            sweep = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            ctx = _ctx(seed_user)
            paying = resolved_definition(payment, ctx)
            sweeping = resolved_definition(sweep, ctx)
            assert paying.closing.derived is not None, (
                "precondition: the loan payment carries a derived stop"
            )
            assert replace(paying, closing=sweeping.closing) == sweeping, (
                "precondition: the two differ in their closing and nothing else"
            )
            calls = self._counting_walk(monkeypatch)

            read_definition(payment, ctx)
            read_definition(sweep, ctx)

            assert len(calls) == 2

    def test_a_reauthored_rule_misses_the_memo_on_the_same_pass(
        self, app, db, seed_user, monkeypatch,
    ):
        """Ruling R-R73's property, carried to the walk.

        ``reauthor_rule`` rewrites a rule's columns in place; a memo keyed by
        the ROW would hand the pre-edit walk to a pass that edited and re-read
        (the id-keyed resolution memo did exactly that, measured at R7d-c-2's
        merge).  Keyed by the value, the re-authored rule resolves to a
        different value and the walk runs again -- and the placements it
        answers are the NEW bound's.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", name="Goal",
            )
            tpl = make_transfer_template(db.session, seed_user, savings)
            db.session.commit()
            ctx = _ctx(seed_user)
            calls = self._counting_walk(monkeypatch)

            before = read_definition(tpl, ctx)
            assert len(before.placements) > 1, (
                "precondition: more than one occurrence, so a count of one "
                "is a visible change"
            )
            _restate_bound(
                tpl.recurrence_rule, EndsAfterOccurrences(count=1), ctx,
            )
            after = read_definition(tpl, ctx)

            assert len(after.placements) == 1
            assert len(calls) == 2, (
                f"a re-authored rule read on the same pass walked "
                f"{len(calls)} times; the value key must miss"
            )

    def test_the_readings_horizon_is_the_passes_calendars(
        self, app, db, seed_user,
    ):
        """Row N-514's half at this door: the reading carries how far it reached."""
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            reading = read_definition(tpl, ctx)

            assert reading.horizon == ctx.calendar().horizon()
            assert reading.horizon is not None


@pytest.mark.usefixtures("seed_periods")
class TestTheDoorAgreesWithItsOwnParts:
    """The composition equals its pieces, so neither can drift alone."""

    def test_the_reading_places_exactly_what_the_walk_places(
        self, app, db, seed_user,
    ):
        """``read_definition`` IS ``resolved_definition`` plus the walk.

        Stated as an equality rather than trusted, because the two entry
        points are what the Recurring surface's active sections and its
        archived drawer take respectively -- and a drawer describing a
        different narrowing from the list beside it is exactly the
        one-row-disagreeing-with-itself shape this step removes.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            tpl = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _ctx(seed_user)

            resolved = resolved_definition(tpl, ctx)
            reading = read_definition(tpl, ctx)

            assert reading.resolved == resolved
            assert reading.placements == occurrence_placements(
                resolved, ctx.calendar(),
            )

    def test_the_authored_half_is_the_rules_own_stored_bound(
        self, app, db, seed_user,
    ):
        """The door narrows; it does not re-author.

        Reading the column twice -- once for the authored half and once to
        rebuild it -- would be the second spelling this arc exists to delete,
        so the authored bound is carried across from the value the pure
        resolver already built.  Asserted on a SECOND transfer into the loan,
        the definition a form can author a stop on.
        """
        with app.app_context():
            loan = _loan(seed_user, db.session)
            _first, tpl = _second_transfer_into(seed_user, db.session, loan)
            db.session.commit()
            _restate_bound(
                tpl.recurrence_rule,
                EndsOnDate(on=date(2027, 3, 1)),
                _ctx(seed_user),
            )
            db.session.commit()
            ctx = _ctx(seed_user)
            assert not is_standing_loan_payment(tpl, ctx), (
                "precondition: the app does not write this transfer's bound"
            )

            resolved = resolved_definition(tpl, ctx)

            assert resolved.closing.authored == recurrence_spec(
                tpl.recurrence_rule,
            ).end_bound
            assert resolved.closing.authored == EndsOnDate(on=date(2027, 3, 1))


@pytest.mark.usefixtures("seed_periods")
class TestTheDerivedStopIsMeasuredInTheCallersPass:
    """One pass in, one answer out -- the read clock is never this module's."""

    def test_the_passes_as_of_selects_which_crossing_answers(
        self, app, db, seed_user,
    ):
        """Two passes over ONE loan answer two stops, and the pass decides which.

        The loan originates 2026-05-01 and is trued to ``$0.00`` on 2026-06-15.
        Read as of 2026-06-10 it still owes, so the derived stop is the
        FORWARD crossing, a payoff after the true-up day; read as of ``_TODAY``
        (2026-07-01) it is retired, so the stop is the day it LAST became
        closed (plan step ``recurrence:R7d-h``).  The door reads neither the
        clock nor a calendar of its own: both answers come off the pass it was
        handed, which is what the two DIFFERING shows -- a door that built its
        own pass or read ``date.today()`` would answer the frozen day's stop
        for both.
        """
        with app.app_context():
            loan = _loan(
                seed_user, db.session, name="Two Passes",
                origination_date=date(2026, 5, 1), payment_day=1,
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
            owner = seed_user["user"].id
            before = BalanceContext.build(owner, date(2026, 6, 10))
            after = BalanceContext.build(owner, _TODAY)

            still_owing = resolved_definition(tpl, before).closing.derived
            retired = resolved_definition(tpl, after).closing.derived

            assert retired == ClosesOn(on=date(2026, 6, 15))
            assert isinstance(still_owing, ClosesOn)
            assert still_owing.on > date(2026, 6, 15), (
                f"read before the true-up the loan still owes, so its stop is "
                f"a forward payoff, not {still_owing.on}"
            )
