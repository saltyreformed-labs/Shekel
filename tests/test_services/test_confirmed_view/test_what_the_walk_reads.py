"""What the walk reads: contract time governs the split's rate and escrow, a raw transaction typed
onto a loan moves no figure of the fold, and the resolved schedule's confirmed rows are this
view's.
"""

from datetime import date
from decimal import Decimal

from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_features import RateHistory
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._ledger_reads import posted_loan_balance_at
from tests._test_helpers import create_settled_cash_transaction

from ._helpers import _P1, _AS_OF, _make_loan, _settle, _view


class TestSplitInputsKeyOnTheDueDate:
    """Contract time governs the split's RATE and ESCROW (ruling D5, finding N-34).

    Ruling D5 (and R-A, which restates it) says the split INPUTS -- ordering,
    rate, AND escrow -- key on the payment's DUE date, "so out-of-order or late
    settlement can never re-split an installment".  ORDERING moved to the due
    date at step C2 (``loan_ledger.loan_event_stream``, which was
    ``merge_anchor_and_payment_events`` until plan step X-au-g-2c-3b-2) and
    VISIBILITY to the settled date, but the RATE and the ESCROW were left on the
    payment's ``pay_period.start_date`` until finding **N-34** measured the gap;
    these two tests are the fix's pins, replacing the control that pinned the
    defect.

    A pay period starts up to ~2 weeks BEFORE the installment it pays, so a
    version effective inside that window would govern the wrong side of the
    boundary: the rate case misattributes interest as principal, the escrow case
    misattributes escrow as principal.  Both propagate to the owed balance, the
    posted ledger's interest leg, the payment-history table, the Schedule-A tax
    figure, and the paid-YTD chips -- and no check of the posted ledger against
    the walk could catch either (E1a's checked-projection assert could not,
    until plan task plan#3093 deleted it), because both sides derive from the
    same walk.

    Each test puts the version change STRICTLY inside the window and asserts the
    installment's own date governs; reverting either call site to
    ``pay_period.start_date`` flips it back to the value named in its docstring.
    """

    def test_a_rate_change_inside_the_period_to_due_window_governs(
        self, app, db, seed_user, seed_periods,
    ):
        """A 12% rate effective mid-window splits the payment at 12%, not 6%.

        Period 1 runs 2026-01-16..01-29 and its installment is due 2026-02-01, so
        2026-01-25 is STRICTLY inside the window: after the pay-period start,
        before the due date.  Keyed on the DUE date the payment accrues at 12% --
        interest round(100000 x 0.01) = 1000.00, principal 1000 - 1000 = 0.00,
        balance held at 100,000.00.  Keyed on the pay-period start (the N-34
        defect) it accrued at 6%: interest 500.00, principal 500.00, balance
        99,500.00.

        **$500.00 of interest on ONE payment**, which under the defect was
        misattributed to principal -- and which also moves the Schedule-A
        interest figure the Taxes tab reports.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            db.session.add(RateHistory(
                account_id=loan.id, effective_date=date(2026, 1, 25),
                interest_rate=Decimal("0.12"),
            ))
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            # The window really is a window: the change lands strictly inside it.
            period = seed_periods[_P1]
            assert period.start_date < date(2026, 1, 25) < date(2026, 2, 1)

            (row,) = _view(loan, seed_user).history_rows
            assert row.payment_date == date(2026, 2, 1)
            assert row.interest_rate == Decimal("0.12000")
            assert row.interest == Decimal("1000.00")
            assert row.principal == Decimal("0.00")
            assert row.remaining_balance == Decimal("100000.00")

    def test_an_escrow_change_inside_the_period_to_due_window_governs(
        self, app, db, seed_user, seed_periods,
    ):
        """An escrow version effective mid-window is the escrow the split backs out.

        The loan opens with $1,200/yr escrow ($100/mo) from origination; a second
        version on the SAME line takes effect 2026-01-25 at $6,000/yr ($500/mo) --
        again strictly inside period 1's 2026-01-16 start .. 2026-02-01 due
        window.  Keyed on the DUE date the payment backs out $500.00: interest
        round(100000 x 0.005) = 500.00, principal 1000 - 500 - 500 = 0.00,
        balance held at 100,000.00.  Keyed on the pay-period start it backed out
        the superseded $100.00 and booked principal 400.00 -> 99,600.00.

        **$400.00 of escrow on ONE payment**, misattributed to principal.  The
        escrow half matters as much as the rate half because the CASH is built
        from the same figure (``_installment_cash``): if the two ends key on
        different dates, the difference silently lands in principal.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            opening = (
                db.session.query(EscrowComponentVersion)
                .filter_by(line_id=loan.escrow_lines[0].id)
                .one()
            )
            db.session.add(EscrowComponentVersion(
                line_id=opening.line_id,
                effective_date=date(2026, 1, 25),
                annual_amount=Decimal("6000.00"),
            ))
            db.session.commit()
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            period = seed_periods[_P1]
            assert period.start_date < date(2026, 1, 25) < date(2026, 2, 1)

            (row,) = _view(loan, seed_user).history_rows
            assert row.payment_date == date(2026, 2, 1)
            assert row.interest == Decimal("500.00")
            assert row.principal == Decimal("0.00")
            assert row.remaining_balance == Decimal("100000.00")


class TestRawLoanTransactionIsInvisibleToTheWalk:
    """N-11: a raw transaction typed onto a loan moves the postings, not the fold."""

    def test_a_forced_raw_loan_transaction_diverges_the_two_derivations(
        self, app, db, seed_user, seed_periods,
    ):
        """The posted balance moves $300; the confirmed view does not.

        Bypassing ruling D4's create guard with a direct settled-cash insert (the
        shape a pre-guard legacy row would be), a $300 transaction posts onto the
        loan's linked ledger.  The sum-of-postings counts it; the walk -- whose
        payment set is the transfers into the loan -- cannot see it.  That is
        precisely why the shape is FORBIDDEN AT SOURCE (ruling R-E, shipped at
        step BG: both transaction-create routes, the recurrence-template form,
        and the salary-profile picker all refuse an amortizing account), which is
        what makes the fold complete by construction rather than by luck.  Do not
        read a divergence of this shape as a stale cache to repair.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            before = _view(loan, seed_user)
            assert before.balance == posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )

            create_settled_cash_transaction(
                seed_user, seed_periods[_P1], Decimal("300.00"),
                account=loan, name="Typed On Loan",
                settled_on=seed_periods[_P1].start_date,
            )
            db.session.commit()

            after = _view(loan, seed_user)
            posted = posted_loan_balance_at(
                loan.id, scenario_id, _AS_OF,
            )
            assert after == before
            assert abs(posted - after.balance) == Decimal("300.00")


def test_the_confirmed_view_is_the_resolvers_confirmed_slice(
    app, db, seed_user, seed_periods,
):
    """The cutover pin: the resolved schedule's confirmed rows ARE this view's.

    Plan step E1d-b threads :func:`app.services.balance_at.confirmed_view` into
    the seam's whole-loan read, so the amortization table, the band chart's
    history prefix, and the forward projection's seed all come from it.  A $2,000
    payment makes the check non-vacuous: the view's row carries the REAL 1500.00
    principal (hand-computed above), which a contractual replay could not produce
    -- so equality here proves the production path reads this producer, not that
    two producers happen to agree on an on-schedule loan.
    """
    with app.app_context():
        loan = _make_loan(seed_user)
        _settle(seed_user, loan, seed_periods[_P1], Decimal("2000.00"))
        db.session.commit()

        ctx = BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"],
            as_of=_AS_OF,
        )
        view = balance_at.confirmed_view(loan, ctx)
        resolved = resolved_loan(loan, ctx)
        confirmed = [row for row in resolved.state.schedule if row.is_confirmed]

        assert confirmed == view.history_rows
        assert confirmed[0].principal == Decimal("1500.00")
