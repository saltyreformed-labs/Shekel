"""When the confirmed view withholds an answer, and where it no longer does; and a withheld view
resolves exactly as the unseeded resolver.
"""

from datetime import date
from decimal import Decimal

from app.models.scenario import Scenario
from app.services import balance_at, loan_loaders, loan_payment_service, loan_resolver
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import seam_confirmed_view

from ._helpers import (
    _ORIGINATION_PRINCIPAL,
    _ORIGINATION_DATE,
    _ANCHOR_BALANCE,
    _P1,
    _AS_OF,
    _loan_params,
    _make_loan,
    _settle,
    _view,
)


class TestConfirmedViewGuards:
    """When the view withholds an answer -- and where it no longer does.

    Every ``None`` seeds the resolver from the loan's latest assertion with no
    history (the anchor replay it routed to until plan step recurrence:R16-e),
    so a withheld view is never a wrong one.
    """

    def test_no_baseline_scenario_returns_none(self, app, db, seed_user):
        """No scenario -> ``None`` (there is no scenario to scope the walk to)."""
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            ctx = BalanceContext(
                user_id=seed_user["user"].id, scenario=None, as_of=_AS_OF,
            )
            assert balance_at.confirmed_view(loan, ctx) is None

    def test_an_account_that_is_not_a_configured_loan_returns_none(
        self, app, db, seed_user,
    ):
        """A non-loan account -> ``None`` (its walk carries no opening fact).

        The view asks the WALK whether the account is a configured loan rather
        than re-loading ``LoanParams``: an account with none walks to an empty
        :class:`LoanLedgerWalk`, which carries no ``is_opening`` anchor.  One
        load, one source -- the origination date the rows are numbered from
        cannot be mismatched to the walk they are folded from.
        """
        with app.app_context():
            checking = seed_user["account"]
            assert loan_loaders.load_loan_params(checking.id) is None
            assert _view(checking, seed_user) is None

    def test_an_as_of_before_origination_returns_none(
        self, app, db, seed_user, seed_periods,
    ):
        """Before the loan exists -> ``None``; its honest 0.00 must not seed (B-1).

        A loan configured before it closes HAS an opening fact, so the fold
        answers ``0.00`` for a date before it -- correct ("nothing has happened")
        and exactly why it must never reach the forward projection: seeding zero
        collapsed a 360-row schedule to none and held $200,000 flat forever
        (outage B-1).  The view withholds instead, and the resolver seeds from
        the latest assertion.

        NEGATIVE CONTROL: delete the origination guard in ``confirmed_view`` and
        this returns ``ConfirmedLedgerView(balance=0.00, history_rows=[])``.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            assert _ORIGINATION_DATE == date(2025, 1, 1)
            assert _view(loan, seed_user, date(2024, 12, 31)) is None
            # ...and on the origination date itself it answers, holding the
            # opening principal flat (nothing has been paid yet).
            opening = _view(loan, seed_user, _ORIGINATION_DATE)
            assert opening.balance == _ORIGINATION_PRINCIPAL
            assert opening.history_rows == []

    def test_a_what_if_scenario_folds_its_own_anchors_not_the_baseline_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario scoping holds: a what-if sees the anchors, never the payments.

        The baseline carries a settled $1,000 payment; a second scenario carries
        none.  The walk is scenario-scoped through its settled-payment load, so
        the what-if folds the loan's ANCHORS alone -- $100,000, the trueup value,
        with no rows -- while the baseline's row is untouched.  (The posting
        reader returned ``None`` here, because the what-if had no postings at
        all; answering from the anchors is the same fact its fallback replay
        would have reconstructed, arrived at directly.)
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(what_if)
            db.session.commit()

            baseline_view = _view(loan, seed_user)
            assert baseline_view.balance == Decimal("99500.00")
            assert len(baseline_view.history_rows) == 1

            what_if_view = seam_confirmed_view(loan.id, what_if.id, _AS_OF)
            assert what_if_view.balance == _ANCHOR_BALANCE
            assert what_if_view.history_rows == []

    def test_a_configured_loan_with_no_payments_has_rows_but_a_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """No confirmed payment yet reads ``[]`` rows -- never ``None``.

        The loan exists and its anchors fold, so the view ANSWERS: the trueup
        balance with an empty confirmed slice.  ``None`` here would wrongly send
        the resolver back to the latest assertion for a loan whose facts are
        complete.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()

            view = _view(loan, seed_user)
            assert view.history_rows == []
            assert view.balance == _ANCHOR_BALANCE


class TestAWithheldViewResolvesExactlyAsTheUnseededResolver:
    """The load-bearing safety property behind every ``None``: nothing changes.

    Every guard in :class:`TestConfirmedViewGuards` seeds the resolver from the
    loan's latest assertion.  This pins what that is WORTH: the resolution is byte-identical
    to calling the pure resolver with no seed at all, so a withheld view can never
    be a wrong one: it is the resolver's own answer from the latest assertion.

    Kept from the deleted ``resolve_loan_seeded`` suite, whose version used a loan
    with no OPENING posting; finding B-12 retired that premise (such a loan now
    FOLDS), so the property is re-pinned on a guard that survives -- a loan read
    before it originated.
    """

    def test_a_pre_origination_read_resolves_identically_to_no_seed(
        self, app, db, seed_user, seed_periods,
    ):
        """Read before origination, the bundle equals the un-seeded resolver.

        Non-vacuous: the resolver really ran (a real, positive schedule), and a
        WRONG seed would move the forward rows rather than leave them equal.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.commit()
            before_origination = date(2024, 12, 31)
            ctx = BalanceContext(
                user_id=seed_user["user"].id,
                scenario=seed_user["scenario"],
                as_of=before_origination,
            )
            assert balance_at.confirmed_view(loan, ctx) is None

            params = _loan_params(loan)
            loan_ctx = loan_payment_service.load_loan_context(loan.id)
            loan_inputs = loan_resolver.LoanInputs(
                params, loan_loaders.load_loan_anchor_facts(params),
                loan_ctx.rate_changes,
            )
            unseeded = loan_resolver.resolve_loan(
                loan_inputs, before_origination,
            )
            seeded = resolved_loan(loan, ctx)

            assert seeded.state == unseeded
            assert seeded.state.schedule, "resolver produced no schedule rows"
            assert seeded.state.schedule[0].remaining_balance > Decimal("0.00")
