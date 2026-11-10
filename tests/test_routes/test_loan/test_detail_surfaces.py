"""Loan route tests: the loan detail page's payment breakdown and measured surfaces.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
import traceback
from datetime import date, time
from decimal import Decimal

from sqlalchemy import event

from app.enums import AcctTypeEnum, StatusEnum
from app.extensions import db as _db
from app.models.ref import AccountType
from app.services.balance_at import BalanceContext
from app.services import account_service, balance_at
from app.services.loan_ledger import walk_loan_ledger

from tests._test_helpers import (
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    make_loan_payment_template,
)
from tests.conftest import log_in_seed_user
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import (
    _create_loan_account,
    _create_auto_loan,
    _create_mortgage,
    _create_transfer_to_loan,
    _add_escrow,
    _create_fresh_mortgage,
)


#: The walk's module: every walk of a loan's facts is assembled in it -- the
#: read pass's (``load_loan_stream``, behind ``BalanceContext.loan_walk``) and
#: the ledger's (``_ledger_walks``, behind ``walk_loan_ledger`` and
#: ``walk_loan_ledgers``).
_WALK_MODULE = "app/services/loan_ledger/_walk.py"
#: What each walk's load reads exactly once: the loan's anchor facts.
_ANCHOR_FACTS_READ = "FROM budget.loan_anchor_events"


def _walk_loads(fn):
    """Run *fn* and count the loan walks it loaded, by their statements.

    A statement counts when it reads the loan's anchor facts while a frame of
    the walk's module is on the stack.  Each walk's load reads them once (the
    pass's through ``load_loan_anchor_facts``, the ledger's through
    ``load_loan_records``' standing statements), so the count is the number
    of walks loaded.  The frames are what make it a count of WALKS: the page
    reads the same table outside any walk too (the seam's resolution and the
    payoff scenarios' inputs), so a count of the table's reads alone would pin
    those beside the walks.

    Args:
        fn: A zero-argument callable to run under the count.

    Returns:
        ``(result, walks)`` -- *fn*'s return value and the walk loads counted.
    """
    walks = []

    def _count(_conn, _cursor, statement, *_args, **_kwargs):
        if _ANCHOR_FACTS_READ in statement and any(
            frame.filename.endswith(_WALK_MODULE)
            for frame in traceback.extract_stack()
        ):
            walks.append(statement)

    event.listen(_db.engine, "before_cursor_execute", _count)
    try:
        result = fn()
    finally:
        event.remove(_db.engine, "before_cursor_execute", _count)
    return result, len(walks)


class TestPaymentBreakdown:
    """Tests for the payment allocation breakdown card on the loan dashboard.

    Verifies that the breakdown shows correct P/I/E split, handles
    edge cases, and renders the progress bar with accurate percentages.
    """

    def test_breakdown_shows_on_dashboard(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage with escrow: breakdown card shows P/I/E amounts.

        Setup: $250,000 mortgage at 6.5%, 360 months, origination 2023-06-01.
        LoanParams created by _create_mortgage: balance stated at $250K,
        original_principal=$255K, rate=0.065, term=360, payment_day=1.
        Escrow: $7,200 property tax + $2,400 insurance = $9,600/yr = $800/mo.

        Monthly P&I from original terms ($255K, 6.5%, 360mo): ~$1,611.64.
        The breakdown should show the P/I split for the current period
        plus the escrow portion.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Property Tax", Decimal("7200.00"))
        _add_escrow(db.session, acct.id, "Insurance", Decimal("2400.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown card renders.
        assert "Payment allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        assert "to escrow" in html
        # Escrow = $800/mo (9600/12).
        assert "800.00" in html

    def test_breakdown_no_escrow(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Auto loan with no escrow: only P/I shown, escrow line absent."""
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to principal" in html
        assert "to interest" in html
        # Escrow line should not appear.
        assert "to escrow" not in html

    def test_breakdown_proportions_sum_to_100(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Displayed percentages sum to exactly 100.0%.

        Parse the three percentage values from the HTML and verify
        their sum.  Uses a mortgage with escrow for three components.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(db.session, acct.id, "Tax", Decimal("6000.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Extract percentages from "to principal (XX.X%)" pattern.
        import re as _re
        pcts = _re.findall(r"to (?:principal|interest|escrow) \((\d+\.\d)%\)", html)
        assert len(pcts) == 3, f"Expected 3 percentages, found {len(pcts)}: {pcts}"
        total = sum(Decimal(p) for p in pcts)
        assert total == Decimal("100.0"), (
            f"Percentages sum to {total}, expected 100.0"
        )

    def test_breakdown_hidden_no_params(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan without LoanParams: breakdown card not shown."""
        # Create a bare account with no params.
        loan_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Mortgage",
                anchor_balance=Decimal("200000.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        # Without params, renders setup page (no breakdown).
        assert resp.status_code == 200
        assert b"Payment allocation" not in resp.data

    def test_breakdown_with_extra_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Extra payments included in the principal portion.

        When the committed schedule includes extra payments (from
        transfers), the "principal" line in the breakdown should
        reflect both standard principal and extra_payment.
        """
        acct = _create_mortgage(seed_user, db.session)
        # Create a transfer that overpays the standard P&I.
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("2000.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to principal" in html

    def test_breakdown_confirmed_row_labeled(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Confirmed payment: card header shows Confirmed badge.

        When all schedule rows are confirmed (loan fully paid through
        transfers), the breakdown should label the data as confirmed.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The first non-confirmed (projected) row is shown, but
        # the confirmed payment's row would show "Confirmed" badge.
        assert "Payment allocation" in html

    def test_breakdown_escrow_zero_hidden(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage where all escrow components are inactive: escrow hidden.

        Even though the loan type typically has escrow, if all components
        are inactive, the escrow line should not render.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No escrow components added (none active).
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment allocation" in html
        assert "to escrow" not in html

    def test_breakdown_uses_committed_schedule(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Breakdown uses committed (payment-aware) schedule, not original.

        When payments exist, the committed schedule's P/I split differs
        from the original because real payments affect the balance
        trajectory.  The breakdown should reflect the committed values.
        """
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Breakdown renders from committed data.
        assert "Payment allocation" in html
        assert "to principal" in html

    def test_breakdown_escrow_inflation_note(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """O-3: Escrow with inflation_rate shows projected increase note.

        When escrow components have non-null inflation rates, the
        breakdown should show a note about projected escrow increase.
        """
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Property Tax",
            Decimal("7200.00"), inflation_rate=Decimal("0.0300"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" in html

    def test_breakdown_sums_the_coming_months_payments_and_skips_catch_ups(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The bar is the coming installment MONTH: every payment due in it, summed.

        A $250k / 6.5% / 360-month mortgage originated 2026-03-01 (first
        installment Apr 1 2026; nothing overdue at the frozen 2026-03-20
        today) paid by TWO definitions: a tracking payment carrying a $125
        extra, due the 1st, and a fixed $50 sweep due the 15th -- whose Mar
        15 occurrence no row answers, so the plan prices it as a catch-up
        paid the day after the read (ruling R-R64, the D1 clamp) and its Jan
        and Feb occurrences fall inside the origination assertion (R-R72).

        The bar reads April: ``$1,580.17 + $125 + $50 = $1,755.17``, not the
        catch-up on top ($1,805.17) and not the next installment alone
        ($1,705.17).  The catch-up still moves the balance the fold charges
        on: ``$250,000 - $50 = $249,950`` at Apr 1, so April's interest is
        ``249950 * 0.065 / 12 = $1,353.90`` (HALF_UP) and the month's
        principal ``$1,755.17 - $1,353.90 = $401.27`` (the sweep's $50 is all
        principal: April's charge is met by the payment before it).
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 3, 1),
        )
        tracking = make_loan_payment_template(
            db.session, seed_user, acct, extra_principal="125.00",
        )
        # The builder names every template after the loan; two into one loan
        # need distinct names (``uq_transfer_templates_user_name``).
        tracking.name = "Mortgage payment"
        db.session.flush()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Apr 2026 payment" in html
        assert "$1,755.17" in html
        assert "$1,353.90" in html
        assert "$401.27" in html
        assert "$1,805.17" not in html
        pcts = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert sum(Decimal(p) for p in pcts) == Decimal("100.0")

    def test_breakdown_no_inflation_note_when_zero(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Escrow with zero or null inflation_rate: no inflation note."""
        acct = _create_mortgage(seed_user, db.session)
        _add_escrow(
            db.session, acct.id, "Insurance",
            Decimal("2400.00"), inflation_rate=None,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "inflation estimates" not in html


# DH-#56: TestLoanParamsInterestRateUpperBoundCheck was retired here.  It
# exercised the storage-tier CHECK on budget.loan_params.interest_rate
# (ck_loan_params_interest_rate_upper) and the column's nullable demotion --
# both dropped, with the column, by DH-#56's migration (b7d2f4a619c5).  The
# equivalent storage-tier rate-domain [0, 1] guard now lives on
# budget.rate_history.interest_rate (ck_rate_history_valid_interest_rate),
# already covered by tests/test_routes/test_c24_range_check_sweep.py::
# TestRateHistoryCheck -- so no coverage is lost.


class TestLoanDetailMeasuredSurfaces:
    """The band's measured chips + the payment-history / balance-anchors sections.

    Loop B surfaces the genesis ledger's real facts on the loan detail page: the
    interest / principal actually paid this year (band chips), the confirmed
    payment history, and the balance-anchor drift scorecard.  These end-to-end
    (route wiring + template) assertions complement the exact-value producer
    tests in ``tests/test_services/test_loan_display_producers.py``.
    """

    def test_measured_surfaces_render_with_confirmed_payment(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A settled 2026 payment surfaces the YTD chips, history, and anchors.

        Loan trued to $100,000 (origination $250,000 on 2023-06-01, a user
        true-up on 2026-01-01); one $1,000 payment settled 2026-03-15 (before
        the autouse-frozen today 2026-03-20) posts its real interest / principal
        split to the ledger, so the two YTD chips, the payment-history row, and
        the origination + true-up anchor rows all render.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Measured Mortgage",
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[3], amount=Decimal("1000.00"),
            settled_on=date(2026, 3, 15),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{loan.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Measured YTD chips (the ledger's Schedule-A interest + principal
        # sibling).
        assert "Interest paid, YTD" in html
        assert "Principal paid, YTD" in html
        # Confirmed payment-history section with the one settled payment.
        assert "Payment history" in html
        assert "1 confirmed" in html
        # Balance-anchor scorecard: the origination row plus the user true-up.
        assert "Balance anchors" in html
        assert "Origination" in html

    def test_one_get_walks_the_loan_once(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """One GET of the page loads the loan's walk ONCE: the read pass's.

        Finding plan#2874: the payment history and the drift scorecard each
        walked the loan's ledger again beside the pass's walk, so one GET
        loaded three walks, measured at plan task plan#3081's start.  Since
        that task both read the pass's walk, which the balance hero, the YTD
        chips and the schedule already fold.  The page still renders both
        tables off it: the payment table counts its one payment, and the
        scorecard's card renders only when it holds a row.

        The control walks the ledger on the same data and must count one, so
        the count can see a walk loaded by the door the tables used to take;
        the GET's own one is the pass's.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Walked Mortgage",
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[3], amount=Decimal("1000.00"),
            settled_on=date(2026, 3, 15),
        )
        db.session.commit()

        resp, walks = _walk_loads(
            lambda: auth_client.get(f"/accounts/{loan.id}/loan"),
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "1 confirmed" in html
        assert "Balance anchors" in html
        assert walks == 1

        _walk, control = _walk_loads(
            lambda: walk_loan_ledger(loan.id, seed_user["scenario"].id),
        )
        assert control == 1

    def test_a_loan_with_no_confirmed_payment_shows_no_payment_history(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A configured loan with no payment hides the table and reads "Projected".

        The empty table is the page's whole answer for a loan the walk opened
        with nothing paid: no section, and the hero's caption says the balance
        is projected.  The scorecard still shows, so the page rendered its
        measured surfaces.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Unpaid Mortgage",
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{loan.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Payment history" not in html
        assert "Projected from schedule" in html
        assert "Balance anchors" in html

    def test_no_duplicate_oob_chip_ids_on_initial_load(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The band chips' OOB ids appear exactly once on the initial page load.

        The escrow / rate-history partials emit their out-of-band chip copies
        only when served as an HTMX response (``oob_swaps``), never when the
        dashboard includes them inline -- so the initial DOM carries no duplicate
        element ids (only the in-band chip and the escrow-header badge).
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "Dup-ID Mortgage",
            Decimal("200000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.count('id="total-payment-chip"') == 1
        assert html.count('id="escrow-badge"') == 1
        assert html.count('id="interest-rate-chip"') == 1

    def test_ytd_chips_use_display_tz_year_at_new_year_boundary(
        self, auth_client, seed_user, db, seed_periods, monkeypatch,
    ):
        """loan_audit deferred follow-up #3: YTD chips sum by the Eastern civil year.

        The two YTD chips select their year from the user's display-tz clock
        (matching the analytics Taxes tab + the L9 civil-date attribution), not
        the backend UTC clock.  Frozen at 2027-01-01 00:00 UTC -- which is still
        2026-12-31 in America/New_York -- ``display_today().year`` is 2026 while
        ``date.today().year`` (UTC) is 2027.  A payment settled 2026-03-15 posts
        its interest to civil year 2026, so the "Interest paid, YTD" chip must
        show the 2026 figure (the Taxes-tab number), NOT the empty 2027 figure
        the pre-fix UTC code would render.
        """
        loan = create_loan_with_trueup(
            seed_user, db.session,
            origination_principal=Decimal("250000.00"),
            anchor_balance=Decimal("100000.00"),
            anchor_date=date(2026, 1, 1),
            rate=Decimal("0.06000"),
            origination_date=date(2023, 6, 1),
            name="Boundary Mortgage",
        )
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], loan,
            seed_periods[3], amount=Decimal("1000.00"),
            settled_on=date(2026, 3, 15),
        )
        db.session.commit()

        # The measured facts: the 2026 (Eastern) year holds the payment's
        # interest; the 2027 (UTC) year holds nothing.  The fold-based chip
        # producer is TOTAL, so it returns $0.00 (not None) for 2027 -- exactly
        # the value the pre-fix UTC code would have rendered into the chip.
        ctx = BalanceContext.build(seed_user["user"].id)
        interest_2026 = balance_at.loan_interest_paid_in_year(loan, ctx, 2026)
        interest_2027 = balance_at.loan_interest_paid_in_year(loan, ctx, 2027)
        assert interest_2026 > Decimal("0.00")
        assert interest_2027 == Decimal("0.00")

        # Re-freeze onto the New Year boundary: midnight UTC Jan 1 2027 is
        # still Dec 31 2026 Eastern.  ``at_time`` is explicit because this test
        # means that INSTANT -- ``freeze_today``'s default is noon, so that a
        # test meaning "today is D" gets D from the display-tz readers too.
        freeze_today(monkeypatch, date(2027, 1, 1), at_time=time.min)
        # Sign in again on the moved date: ``auth_client`` signed in under the
        # module freeze, and the idle timeout measures against the clock this
        # freeze moves, so the old session is expired here as a real one would be.
        log_in_seed_user(auth_client)

        html = auth_client.get(f"/accounts/{loan.id}/loan").data.decode()
        match = re.search(
            r'Interest paid, YTD</div>\s*'
            r'<div class="pulse-chip__value font-mono">([^<]+)</div>',
            html,
        )
        assert match is not None, (
            "The 'Interest paid, YTD' chip did not render -- the display-tz "
            "year should surface the 2026 payment's interest."
        )
        chip_value = match.group(1).strip()
        # The chip shows the Eastern-year (2026) figure, formatted by the money
        # macro ("$1,234.56"), NOT the $0.00 the UTC year (2027) would give.
        assert chip_value == f"${interest_2026:,.2f}"
        assert chip_value != "$0.00"
