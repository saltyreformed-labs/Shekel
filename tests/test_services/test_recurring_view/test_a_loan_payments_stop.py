"""Where a loan payment's row stops: at the loan's derived payoff when the rule authors no stop, at
an owner's stored stop on the standing payment (archived too), and on the day a retired loan
closed; the monthly figure blank once a stop has passed, the archived drawer naming the list's
stop, and the rule resolved once per build.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from tests._test_helpers import (
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_retired_loan_payment,
)
from app.services import balance_at, recurring_view
from app.services.balance_at import is_standing_loan_payment
from app.services.recurrence import EndsOnDate, reauthor_rule, recurrence_spec
from app.services.recurrence import _reading

from ._helpers import _ctx


#: The day the loan cases below are measured at, frozen so the derived payoff
#: is a LITERAL.  The door's own tests (``test_recurring_definition``) use the
#: same day and the same $12,000 / 5% / 24-month loan, whose payoff read then is
#: 2028-07-01; a case here that disagreed with one there would be the surface
#: and its door coming apart.
_LOAN_TODAY = date(2026, 7, 1)


def _loan(seed_user, **kwargs):
    """Return a 24-month $12,000 loan at 5%, originating on the frozen day.

    Args:
        seed_user: The owner.
        **kwargs: Overrides forwarded to ``create_loan_account``.

    Returns:
        The loan ``Account``.
    """
    defaults = {
        "name": "Surface Loan",
        "principal": Decimal("12000.00"),
        "rate": Decimal("0.05000"),
        "term": 24,
        "origination_date": _LOAN_TODAY,
    }
    defaults.update(kwargs)
    return create_loan_account(seed_user, db.session, **defaults)


def _retired_loan_payment(seed_user):
    """Return a loan payment whose loan was cleared AFTER its first installment.

    The loan originates 2026-05-01 with a ``payment_day`` of 1, so its first
    contractual installment is 2026-06-01; a true-up to ``$0.00`` on 2026-06-15
    retires it.  Its closing date is therefore 2026-06-15 -- the same fixture
    ``test_loan_recurrence_sync`` pins ``ClosesOn(2026-06-15)`` on -- and the
    definition HAS fired once, so the stop is a date and not "never runs".

    Args:
        seed_user: The owner.

    Returns:
        ``(loan, template)``, committed.
    """
    # The shared builder since plan step R7d-e, so the aggregator's and the
    # /savings floor's retired-loan cases describe THIS loan and not a cousin.
    return make_retired_loan_payment(
        db.session, seed_user,
        origination_date=date(2026, 5, 1), cleared_on=date(2026, 6, 15),
    )


class TestTheDestinationsStopReachesTheRow:
    """A loan payment's row stops where the LOAN says, not where a column does.

    Plan step R7d-d.  Until it, the only way a loan's payoff reached this
    surface was the cached copy ten chokepoints wrote into
    ``budget.recurrence_rules.end_date`` -- the authored bound's own column --
    so the stop line and the next date named whichever value a chokepoint had
    most recently written (plan ledger row **D35**).  The surface now reads the
    composed door, so both name the loan's DERIVED closing date.

    **Every case leaves that column NULL and asserts it stayed so.**  A phrase
    that could have come from the column would prove nothing about the
    derivation; with the column empty the rule's authored bound is
    ``NEVER_ENDS``, and before this step each of these rows showed no stop line
    and a next date the loan had already made impossible.
    """

    @pytest.fixture(autouse=True)
    def _frozen(self, monkeypatch):
        """Freeze today mid-loan so the projected payoff does not drift."""
        freeze_today(monkeypatch, _LOAN_TODAY)

    def test_a_live_loan_payments_stop_line_is_the_derived_payoff(
        self, seed_user, seed_periods_52,
    ):
        """The rule authors NO stop; the cell names the loan's payoff anyway.

        Asserted against the seam's own ``closing_date`` as well as against
        the literal, so a drift in the loan fixture reports as a precondition
        rather than as a display defect.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        assert tpl.recurrence_rule.end_date is None, (
            "precondition: nothing stored could have supplied the stop"
        )
        ctx = _ctx(seed_user, _LOAN_TODAY)
        assert balance_at.loan_figures(loan, ctx).closing_date == (
            date(2028, 7, 1)
        ), "precondition: the fixture loan pays off 2028-07-01"

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jul 01, 2028"
        assert row.next_date is not None, (
            "a loan still owing must keep a next date; the stop reached the "
            "row but the walk was narrowed to nothing"
        )
        assert row.next_date >= _LOAN_TODAY

    def test_an_owners_stop_on_the_standing_payment_reaches_the_row(
        self, seed_user, seed_periods_52,
    ):
        """Ruling **R-R82** on the surface: a stored stop is the owner's word and the row names it.

        Until plan step R7d-g this case pinned plan ledger row **D35**'s shape
        the other way round: the column was the chokepoints' cache, EARLIER
        than the loan's payoff (``2029-01-22`` stored against ``2029-02-22``
        derived on production), and the door read it as no bound at all
        (ruling **R-R56**) so the row named the payoff.  Nothing writes a
        cache there now; a date in that column is a stop the owner authored,
        the door composes it, and the row names it.  The next date is still
        placed, because the pass reads before the stop.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        ctx = _ctx(seed_user, _LOAN_TODAY)
        authored = date(2027, 1, 1)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            ctx.calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == authored, "precondition: stored"
        assert is_standing_loan_payment(tpl, ctx), (
            "precondition: this is the loan's standing payment"
        )

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jan 01, 2027"
        assert row.next_date is not None
        assert row.next_date <= authored

    def test_an_ARCHIVED_loan_payments_stored_stop_is_read_as_its_owners(
        self, seed_user, seed_periods_52,
    ):
        """The Archived drawer reads a stored stop as the owner's word, like every reader.

        Until plan step R7d-g the drawer showed the cache the chokepoints
        wrote while the payment was active -- ruling **R-R56** keyed on the
        account's ACTIVE transfer, so an archived one's column was read as
        authored -- and this case pinned that as the interim it was.  R7d-g's
        migration NULLed that cache on every archived transfer into a loan
        (ruling **R-R80**), so a date standing there afterwards is one an
        owner authored and the drawer names it; the migration's own test
        holds the NULLing.  Read beside
        :meth:`test_the_archived_drawer_names_the_same_stop`, which holds that
        the drawer composes the derived stop at all; alone this could not
        tell "composed, and the authored minimum wins" from "never composed".
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        ctx = _ctx(seed_user, _LOAN_TODAY)
        authored = date(2027, 1, 1)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            ctx.calendar(),
        )
        tpl.is_active = False
        db.session.commit()
        assert not is_standing_loan_payment(tpl, ctx), (
            "precondition: an archived payment is not the account's active one"
        )

        rows = recurring_view.build_archived_rows([tpl], ctx)

        assert rows[0].recurrence.stops == "until Jan 01, 2027"

    def test_a_RETIRED_loan_payments_row_stops_on_the_day_the_loan_closed(
        self, seed_user, seed_periods_52,
    ):
        """The past-and-future closing date, on the surface.

        A loan cleared 2026-06-15 has no forward crossing; its closing date is
        the day it LAST became closed (plan step ``recurrence:R7d-h``), and the
        row names it.  The next date is ``None``: the definition's July
        installment falls after the loan ended, and projecting it would be a
        payment against a debt that is gone -- which is exactly what the
        NEVER_ENDS column showed before this step.

        **The monthly equivalent is asserted too, since plan step R7d-e.**  It
        comes from ``obligations_aggregator.monthly_or_none``, which reads the
        same door as the stop line and the next date; until that step it read
        the authored bound alone, so this row stated ``$200.00`` a month beside
        a stop line that said the money had stopped, and the section total
        carried a retired loan's payment.  The row's figure and the subtotal
        are both asserted: a blank cell over a total that still counts it is
        the surface disagreeing with itself.
        """
        loan, tpl = _retired_loan_payment(seed_user)
        assert tpl.recurrence_rule.end_date is None, (
            "precondition: nothing stored could have supplied the stop"
        )
        ctx = _ctx(seed_user, _LOAN_TODAY)
        assert balance_at.loan_figures(loan, ctx).closing_date == (
            date(2026, 6, 15)
        ), "precondition: the loan closed on the day it was trued to zero"

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Jun 15, 2026"
        assert row.next_date is None, (
            f"a payment dated {row.next_date} was projected against a loan "
            "that closed 2026-06-15"
        )
        assert row.equivalent.monthly is None, (
            f"a retired loan's payment states {row.equivalent.monthly} a "
            "month beside a stop line that says the money has stopped"
        )
        assert view.transfers.subtotal.monthly == Decimal("0.00")

    def test_an_owners_stop_that_has_passed_BLANKS_the_monthly_figure(
        self, seed_user, seed_periods_52,
    ):
        """One row agreeing with itself, read after an owner's stop on the standing payment.

        Until plan step R7d-g this case pinned plan ledger row **D35**'s shape
        -- a cache EARLIER than the payoff, read between the two -- and held
        that the cache blanked NOTHING, because the door read it as no bound
        (ruling **R-R56**).  A date in that column is the owner's word now
        (ruling **R-R82**), and a stop the pass reads AFTER has ended the
        definition on every reader of the door: the stop line names it, the
        next date is ``None`` and the monthly equivalent is blank, which is
        what plan step R7d-e made ONE reading rather than three.  The stop
        sits after the first installment (2026-08-01) so the stored pair is
        the ordered one ``ck_recurrence_rules_valid_window`` admits, and the
        pass reads five days past it.  ``$200.00`` monthly would be
        ``200 * 12 / (1 * 12)`` for a live row; the control beside this
        (:meth:`test_an_owners_stop_on_the_standing_payment_reaches_the_row`)
        reads BEFORE its stop and keeps a next date.
        """
        loan = _loan(seed_user)
        # The loan door's own shape -- monthly on the payment day, its start
        # bound to the first contractual installment -- which the fixture
        # authors by default since plan step R16-b-2.
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()
        authored = date(2026, 8, 15)
        assert tpl.recurrence_rule.starts_on == date(2026, 8, 1), (
            "precondition: the stop must follow the first installment"
        )
        reauthor_rule(
            tpl.recurrence_rule,
            replace(recurrence_spec(tpl.recurrence_rule), end_bound=EndsOnDate(on=authored)),
            _ctx(seed_user, _LOAN_TODAY).calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == authored, "precondition: stored"
        ctx = _ctx(seed_user, date(2026, 8, 20))
        assert is_standing_loan_payment(tpl, ctx), (
            "precondition: this is the loan's standing payment"
        )

        view = recurring_view.build_view([], [], [tpl], ctx)
        row = view.transfers.rows[0]

        assert row.recurrence.stops == "until Aug 15, 2026"
        assert row.next_date is None, (
            f"a payment dated {row.next_date} was projected past the stop "
            "its owner authored"
        )
        assert row.equivalent.monthly is None, (
            f"the row states {row.equivalent.monthly} a month beside a stop "
            "line that says the money has stopped"
        )
        assert view.transfers.subtotal.monthly == Decimal("0.00")

    def test_the_archived_drawer_names_the_same_stop(
        self, seed_user, seed_periods_52,
    ):
        """The drawer reads the same door, so it cannot disagree with the list.

        A drawer describing a different narrowing from the active row beside
        it would be one surface disagreeing with itself about one definition.
        """
        _loan_account, tpl = _retired_loan_payment(seed_user)
        tpl.is_active = False
        db.session.commit()

        rows = recurring_view.build_archived_rows(
            [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        assert rows[0].recurrence.stops == "until Jun 15, 2026"

    def test_a_loan_payment_is_RESOLVED_exactly_once_per_build(
        self, seed_user, seed_periods_52, monkeypatch,
    ):
        """The rule-14 guard on the door: one rule, one resolution per pass.

        The resolver's EMPTY test needs the definition's first occurrence, and
        the first build of plan step R7d-d had it resolve the rule AGAIN to
        get one -- so a loan payment cost two resolutions where every other
        row cost one.  The door now hands the resolver the value it already
        built.  Patched at the DEFINITION site (``_reading.resolve``) like
        :meth:`TestTheRecurrenceDescription.test_each_rule_is_RESOLVED_exactly_once_per_build`,
        and the control is shown to fire before the count is read.

        **It covers a SYNCED column too, since plan step R7d-e.**  A loan
        payment whose column held a FUTURE date was resolved a second time on
        this surface by the monthly-equivalent producer
        (``template_monthly_or_none`` -> ``has_ended`` ->
        ``EndsOnDate.has_closed`` walked the rule again off its own columns),
        so until that step this case had to scope itself to a NULL column and
        read two otherwise.  The monthly figure now derives from the door's
        one reading, and the second sweep below -- the same loan payment with
        a cached bound written into its column -- reads ONE resolution as
        well, which is the walk R7d-e deleted, shown gone rather than
        described.
        """
        loan = _loan(seed_user)
        tpl = make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()

        calls = []
        real_resolve = _reading.resolve

        def counting_resolve(spec, calendar):
            calls.append(spec.unit)
            return real_resolve(spec, calendar)

        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [], [], [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        assert view.transfers.rows[0].recurrence.stops == "until Jul 01, 2028"
        assert len(calls) == 1, (
            f"one loan payment resolved its rule {len(calls)} times in one "
            f"build; the door resolves once and hands the value down"
        )

        # The synced column: the shape the monthly producer used to resolve a
        # second time.  Written through the real door, then counted afresh.
        monkeypatch.setattr(_reading, "resolve", real_resolve)
        reauthor_rule(
            tpl.recurrence_rule,
            replace(
                recurrence_spec(tpl.recurrence_rule),
                end_bound=EndsOnDate(on=date(2028, 7, 1)),
            ),
            _ctx(seed_user, _LOAN_TODAY).calendar(),
        )
        db.session.commit()
        assert tpl.recurrence_rule.end_date == date(2028, 7, 1), (
            "precondition: the column holds a synced FUTURE bound"
        )
        calls.clear()
        monkeypatch.setattr(_reading, "resolve", counting_resolve)

        view = recurring_view.build_view(
            [], [], [tpl], _ctx(seed_user, _LOAN_TODAY),
        )

        # One installment a month at the contract's level payment: $12,000.00
        # at 5% over 24 months, r = 0.05 / 12, 12000 * r / (1 - (1 + r) ** -24)
        # = 526.4569 -> $526.46.  The payment TRACKS the loan, so since plan
        # step X-bp (ruling plan#2693) it is priced by the loan rather than by
        # the fixture's stored $200.00 snapshot; the fixture authors the loan
        # door's monthly rule since plan step R16-b-2.
        assert view.transfers.rows[0].equivalent.monthly == Decimal("526.46"), (
            "precondition: the synced payment is still a live commitment"
        )
        assert len(calls) == 1, (
            f"a loan payment with a synced column resolved its rule "
            f"{len(calls)} times in one build; the monthly figure must derive "
            f"from the door's one reading (plan step R7d-e)"
        )
