"""compute_contractual_pi, a loan's monthly principal and interest: the original terms for a fixed
rate and an ARM, the level payment after an ARM adjustment, and a price read from the terms
alone, never from the loan's own payments, on one calendar per read pass.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.models.loan_params import LoanParams
from app.models.transfer import Transfer
from tests._test_helpers import loan_params_for
from app.services.balance_at import BalanceContext
from app.services.loan_ledger import LoanCalendars
from app.services.loan_payment_service import compute_contractual_pi, get_payment_history

from ._helpers import _basis, _create_loan_account, _create_transfer_to_loan


# ── Tests for compute_contractual_pi ─────────────────────────────


class TestComputeContractualPi:
    """Tests for the contractual P&I calculation."""

    def test_fixed_rate_uses_original_terms(self, app, db, seed_user):
        """C1-1: Fixed-rate loan uses original principal and full term.

        $240,000 at 6.5% for 360 months.
        M = P * [r(1+r)^n] / [(1+r)^n - 1]
        r = 0.065/12; n = 360
        The engine's Decimal arithmetic produces $1,516.96.

        DH-#56: the rate is sourced from the rate-change feed (the
        origination RateChangeRecord), not the retired
        ``LoanParams.interest_rate`` column.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("240000.00"),
                term_months=360,
                origination_date=date(2025, 1, 1),
                payment_day=1,
                is_arm=False,
            )

            result = compute_contractual_pi(
                params,
                rate_changes=[
                    RateChangeRecord(
                        effective_date=params.origination_date,
                        interest_rate=Decimal("0.06500"),
                        monthly_pi=None,
                    ),
                ],
            )

            # Standard amortization payment for $240k at 6.5% / 30yr.
            # Uses original_principal (not current) and full term.
            assert result == Decimal("1516.96")

    def test_arm_rate_from_origination_feed_uses_original_terms(
        self, app, db, seed_user,
    ):
        """C1-2: ARM loan whose only rate is the origination row uses original terms.

        DH-#56 retired the legacy pure-LoanParams fallback (it read the
        dropped ``LoanParams.interest_rate`` column).  The rate now comes
        from the rate-change feed; production callers go through
        :func:`load_loan_context`, which loads anchor_events and routes
        through :func:`loan_resolver.compute_monthly_payment_baseline`
        for the ARM-aware SSOT value (see
        :class:`TestComputeContractualPiArmAware`).  This pins the same
        value via the feed: an ARM whose rate-change feed carries only
        the origination 7.0% row (no recorded adjustment) holds the
        original-terms level payment in its first period.

            P = 250000.00, r = 0.07/12, n = 360
            M = P * [r(1+r)^n] / [(1+r)^n - 1] approx $1,663.26
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        with app.app_context():
            params = LoanParams(
                account_id=1,
                original_principal=Decimal("250000.00"),
                term_months=360,
                origination_date=date(2024, 1, 1),
                payment_day=1,
                is_arm=True,
            )

            result = compute_contractual_pi(
                params,
                rate_changes=[
                    RateChangeRecord(
                        effective_date=params.origination_date,
                        interest_rate=Decimal("0.07000"),
                        monthly_pi=None,
                    ),
                ],
            )

            # Origination-feed only: the first rate period holds the
            # original-terms amortization at the 7.0% origination rate.
            # Hand-computed: 250000 * 0.07/12 * (1.005833)^360 /
            # ((1.005833)^360 - 1) ~= 1663.26.
            assert result == Decimal("1663.26")


class TestComputeContractualPiArmAware:
    """C1-3..C1-5: ARM-aware behavior of compute_contractual_pi.

    Exercises the production path where :func:`load_loan_context`
    passes anchor_events + rate_changes + as_of through to
    :func:`loan_resolver.compute_monthly_payment_baseline`.  Locks
    the SSOT invariant: the returned value matches
    ``LoanState.monthly_payment`` for the same inputs, so the escrow-
    subtraction threshold in :func:`prepare_payments_for_engine`
    cannot under-subtract escrow for an ARM whose rate has adjusted
    since origination (the user-reported symptom -- schedule shows
    a 'Payment' value $33 above the loan card's total because the
    threshold leaked the original-terms P&I).
    """

    def test_arm_post_adjustment_holds_level_period_payment(
        self,
    ):
        """C1-3 (re-pinned): post-adjustment ARM holds the level period payment.

        Re-pinned under the rate-period model (CLAUDE rule 5 exception;
        the developer chose to hold the ARM payment constant within each
        fixed-rate period).  The prior test pinned $1,295.19 -- the
        payment from re-amortizing the reduced anchor balance
        ($177,999.54) over the remaining term.  A lender does NOT recast
        the payment unless the rate actually adjusts, so that was the
        symptom-#4 error; the anchor balance no longer influences the
        payment.

        Here the recorded rate never changes (a single 6.875% entry at
        origination), so by the amortization identity the period recast
        reproduces the original level payment: amortize($202,000,
        6.875%, 360) ~= $1,327, which the from-origination period walk
        reproduces to $1,326.99 (a cent of walk rounding).
        """
        from app.services.amortization_engine import RateChangeRecord
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("202000.00"),
            term_months=360,
            origination_date=date(2018, 12, 1),
            payment_day=1,
            is_arm=True,
            arm_first_adjustment_months=60,  # 5/1 ARM, window ended 2023-12.
        )
        # ARM rate at 6.875% since origination (no recorded adjustment).
        # (No anchor data at all: the reduced $177,999.54 balance can no
        # longer even be EXPRESSED to this function -- the structural form
        # of "the anchor balance no longer influences the payment".)
        rate_changes = [
            RateChangeRecord(
                effective_date=date(2018, 12, 1),
                interest_rate=Decimal("0.06875"),
            ),
        ]
        result = compute_contractual_pi(
            params,
            rate_changes=rate_changes,
            as_of=date(2026, 5, 21),
        )
        # The level period payment -- with the recorded rate unchanged it
        # equals the original-terms payment within the walk's rounding,
        # NOT the old $1,295.19 re-amortization of the reduced balance.
        assert result == Decimal("1326.99")

    def test_fixed_rate_with_anchor_still_returns_original_terms(self):
        """C1-4: fixed-rate loans return original-terms regardless of anchors.

        Pre-payments accelerate the payoff date on a fixed-rate
        loan; the contractual P&I stays at the original amount.  Since
        the read switch's final commit the anchor-independence is
        STRUCTURAL: the function takes no anchor feed at all -- the
        period P&I derives from the immutable params + rate-change feed
        only, so no balance data can perturb it.

        DH-#56: the rate is sourced from the origination
        RateChangeRecord, not the retired ``LoanParams.interest_rate``
        column.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("240000.00"),
            term_months=360,
            origination_date=date(2025, 1, 1),
            payment_day=1,
            is_arm=False,
        )
        result = compute_contractual_pi(
            params,
            rate_changes=[
                RateChangeRecord(
                    effective_date=params.origination_date,
                    interest_rate=Decimal("0.06500"),
                    monthly_pi=None,
                ),
            ],
            as_of=date(2026, 5, 21),
        )
        # Original-terms: $240k at 6.5% / 360 = $1,516.96.
        assert result == Decimal("1516.96")

    def test_empty_anchor_events_still_returns_original_terms(self):
        """C1-5: the period P&I derives from params + rate feed alone.

        The old signature accepted an unused ``anchor_events`` feed for
        caller compatibility; the read switch's final commit removed it,
        making the anchor-independence structural.  This pins the
        original-terms amortization from exactly the two inputs the
        function reads -- the immutable params and the rate-change feed
        (DH-#56 retired the ``LoanParams.interest_rate`` column); an
        empty feed is the only thing that raises.
        """
        from app.services.amortization_engine import RateChangeRecord  # pylint: disable=import-outside-toplevel
        params = LoanParams(
            account_id=1,
            original_principal=Decimal("240000.00"),
            term_months=360,
            origination_date=date(2025, 1, 1),
            payment_day=1,
            is_arm=False,
        )
        result = compute_contractual_pi(
            params,
            rate_changes=[
                RateChangeRecord(
                    effective_date=params.origination_date,
                    interest_rate=Decimal("0.06500"),
                    monthly_pi=None,
                ),
            ],
            as_of=date(2026, 5, 21),
        )
        # Original-terms amortization, independent of anchor data.
        assert result == Decimal("1516.96")


@contextmanager
def _statements_issued():
    """Record every SQL statement the engine executes inside the block.

    The same probe ``tests/test_arch`` uses, at the Engine level rather than a
    session's, so it sees what the PROCESS issued whichever session issued it.
    It cannot see ``BEGIN`` / ``COMMIT`` / ``ROLLBACK`` -- psycopg issues those
    through the connection rather than a cursor -- which is why the assertion
    below names a TABLE rather than counting a total.

    Yields:
        The list of normalised SQL strings, appended to as the block runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


class TestALoansPriceDoesNotReadItsOwnPayments:
    """A loan's monthly P&I is its TERMS, so the payment feed cannot move it.

    The cycle these tests exist to keep deleted: the loan's pricing terms
    (``_resolve_loan_basis`` until plan step recurrence:R25, the pass's
    :class:`~app.services.loan_ledger.LoanCalendars` since) used to
    run :func:`load_loan_context` -- and therefore
    :func:`get_payment_history` -- purely to read
    ``resolve_loan(...).monthly_payment`` back out, which put the loan's own
    payment rows on the path that PRICES those rows.

    **Two tests and not one, because they fail on different reintroductions.**
    The value test catches a producer that reads the feed and lets it change the
    answer; the statement test catches one that reads the feed and happens to
    agree today, which is the shape that returns silently.  The second is the
    one that would have caught the original coupling, since that producer also
    answered the right number.

    Measured on a production clone 2026-08-31 by
    ``tests/manual/verify_loan_pricing_ignores_payment_feed.py``: both live
    loans answer one figure (``1293.96`` and ``531.94``) across a FULL 29-record
    feed, an EMPTY one, the confirmed 5 alone, and a DOUBLED 58.

    **The producer moved to ``cash_ledger`` at plan step X-au-g-2a and this
    control did NOT follow it**, deliberately: what it grades is the
    RELATIONSHIP between the two packages -- the first test calls
    :func:`get_payment_history` to prove the feed is non-empty before emptying
    it -- and both tests build their loan with this module's
    ``_create_loan_account`` / ``_create_transfer_to_loan``.  Moving the class
    would either duplicate those two fixtures or move them for one caller.
    """

    def test_the_price_is_unchanged_when_every_payment_row_is_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting the whole payment feed does not move the monthly P&I.

        The feed is emptied by DELETE rather than by soft-deleting or
        cancelling: those two are read as statements about whether a row counts,
        and a producer could honour them while still reading the rows.  Removing
        the rows leaves nothing for a coupled producer to read at all.

        **The feed is ONE relation, ``budget.transfers``, since plan step
        balance:X-bi-6-4d-2**, so the transfers into the loan are what is
        deleted.  It was two from plan step balance:X-bi-6a: the shadow rows
        in ``budget.transactions`` (the settled half) and the parent rows
        (the projected half, as derived legs), and deleting the shadows alone
        left the projected half standing -- measured at 3 records before and
        after.  The settled half moved onto the transfers at plan step
        balance:X-bi-6-4b, and since a transfer became one row (ruling
        **R-BAL258**) the shadow delete this case also ran removed NOTHING
        (measured: 0 rows, 2026-10-15): vacuous code, deleted with nothing
        re-expressed in its place.  The feed is asserted EMPTY after the
        delete, which is the non-vacuity the old "non-empty before" assertion
        could not supply on its own.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            for period in seed_periods[:3]:
                _create_transfer_to_loan(
                    seed_user, loan, period, Decimal("1500.00"),
                )
            db.session.commit()

            before = LoanCalendars().loan_calendar_of(loan.id)
            assert before is not None
            assert get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            ), "the feed must be non-empty for emptying it to mean anything"

            db.session.query(Transfer).filter(
                Transfer.to_account_id == loan.id,
            ).delete(synchronize_session=False)
            db.session.commit()
            assert get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            ) == [], "the feed is not empty: a transfer into the loan survived"

            after = LoanCalendars().loan_calendar_of(loan.id)
            assert after is not None
            # The whole TERM SET, period by period -- not one resolved figure.
            # A producer that read the feed could agree on the period governing
            # one date while differing on another; comparing the set closes
            # that.  (The basis holds periods rather than a scalar since plan
            # step X-au-g-2b, ruling R-IJ.)
            assert [
                (p.start_date, p.annual_rate, p.period_pi) for p in after.periods
            ] == [
                (p.start_date, p.annual_rate, p.period_pi) for p in before.periods
            ]
            assert after.payment_day == before.payment_day

    def test_pricing_a_loan_issues_no_statement_against_the_payment_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The pricing path queries neither ``budget.transactions`` nor ``budget.transfers``.

        The direction that matters: a producer reading the feed and agreeing
        with it is indistinguishable from one that never read it, by value
        alone.  Naming the TABLE rather than counting statements is what makes
        this survive an eager load, which folds into a parent query and moves no
        count.  **Both of the feed's relations are named since plan step
        balance:X-bi-6a**: the projected half of a loan's payments is read from
        ``budget.transfers`` now, which is also the table
        ``_loan_pricing._load_live_payment_configs`` once read -- the coupling
        this control exists to keep out.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            for period in seed_periods[:3]:
                _create_transfer_to_loan(
                    seed_user, loan, period, Decimal("1500.00"),
                )
            db.session.commit()
            db.session.expire_all()

            with _statements_issued() as seen:
                basis = LoanCalendars().loan_calendar_of(loan.id)

            assert basis is not None
            assert seen, "the probe recorded nothing, so it graded nothing"
            touching = [
                sql for sql in seen
                if "budget.transactions" in sql or "budget.transfers" in sql
            ]
            assert not touching, (
                "pricing a loan read its own payment rows: "
                f"{touching}"
            )


#: The tables a loan's CONTRACT TERMS load from: its params, its rate history
#: and its escrow lines with their versions.
_TERMS_TABLES = (
    "budget.loan_params", "budget.rate_history",
    "budget.escrow_lines", "budget.escrow_component_versions",
)


class TestOnePassReadsOneLoanCalendar:
    """A loan's charges and its payments' price read ONE calendar per read pass.

    Plan step recurrence:R25 (ruling **R-R105**: "one bundle that pricing and
    charging both read"; finding **REC-545**).  Pricing kept a second bundle of
    a loan's terms beside the calendar the walk's charges are built from, so a
    pass that walked a derive-mode loan and priced its payments loaded the
    terms twice.  Both now read the pass's one
    :class:`~app.services.loan_ledger.LoanCalendars`.
    """

    def test_pricing_after_the_walk_loads_no_terms_again(
        self, app, db, seed_user, seed_periods,
    ):
        """The walk loads the terms; the pass's pricer then issues no terms query."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            ctx.loan_walk(loan)

            with _statements_issued() as seen:
                cash = ctx.amounts().loans.derive_cash(
                    date(2026, 3, 1), date(2026, 2, 20), loan.id, Decimal("0.00"),
                )

            assert cash is not None
            touching = [
                sql for sql in seen if any(table in sql for table in _TERMS_TABLES)
            ]
            assert not touching, (
                f"the pricer loaded the loan's terms a second time: {touching}"
            )

    def test_the_probe_sees_the_terms_load_without_the_walk(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: with no walk first, the same price DOES load the terms.

        Without it the case above could pass on a probe that sees nothing.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            with _statements_issued() as seen:
                ctx.amounts().loans.derive_cash(
                    date(2026, 3, 1), date(2026, 2, 20), loan.id, Decimal("0.00"),
                )

            assert any("budget.loan_params" in sql for sql in seen), seen
            assert any("budget.rate_history" in sql for sql in seen), seen
