"""Loan route tests: the target-date panel and payoff chip, the one-walk edges, and the refinance
and payoff-by-date forward projection.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

import inspect
import re
from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum, StatusEnum
from app.models.account import Account
from app.routes import loan as loan_routes
from app.services.balance_at import BalanceContext
from app.services.installment_calendar import final_installment_date
from app.services.loan_loaders import load_loan_params
from app.utils.dates import add_months
from app.services import balance_at

from tests._test_helpers import (
    create_loan_account,
    insert_trueup_event,
    loan_params_for,
    make_loan_payment_template,
)
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import (
    _behind_by_unplanned_installments,
    _parse_band_chart,
    _create_transfer_to_loan,
    _create_fresh_mortgage,
    _create_loan_account_exact,
    _create_exact_mortgage,
)


class TestTargetDatePanelAgreesWithTheChip:
    """Plan C8f: the target-date panel and the payoff chip fold ONE model.

    The panel used to binary-search the resolver's contractual schedule walk while
    the chip folded the loan's plan.  For a loan behind that schedule the two
    disagree, and the disagreement was user-visible and unlabelled: the panel
    would announce "your current payment plan already pays this loan off by the
    target date" for a target the chip's own payoff date was months PAST.
    """

    def test_a_target_the_loan_misses_is_not_called_already_paid_off(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """A target between the CONTRACTUAL and the FOLD payoff needs a top-up.

        That window is exactly where the two models disagreed.  The loan is trued
        up $5,000 above its contractual schedule and paid by a recurring payment
        at its level P&I, so the fold retires it well after the contract does; a
        target inside the gap is one the schedule walk called "already met" and
        the fold does not.  The recurring payment is what keeps the gap: a loan
        with none is planned as its contract line and closes on the contract's
        date with the balloon (ruling plan#3982), and this fixture had none
        until plan step recurrence:R16-f.
        """
        acct = create_loan_account(
            seed_user, db.session, name="Behind Schedule",
            principal=Decimal("240000.00"), term=360,
            origination_date=date(2026, 1, 1), payment_day=1,
        )
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("245000.00"),
        )
        make_loan_payment_template(db.session, seed_user, acct)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1500.00"),
            status_enum=StatusEnum.PROJECTED,
        )
        db.session.commit()

        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            account = db.session.get(Account, acct.id)
            fold_payoff = balance_at.loan_payoff_date(account, ctx)
            params = load_loan_params(acct.id)
            contractual = final_installment_date(
                params.origination_date, params.payment_day,
                params.term_months,
            )
        assert fold_payoff is not None
        assert contractual < fold_payoff, (
            "precondition: the fold must retire this loan AFTER the contractual "
            "schedule, or the target window this test needs does not exist"
        )
        # Inside the gap: after the contractual payoff, before the fold's.
        target = add_months(fold_payoff, -6)
        assert contractual < target < fold_payoff

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": target.isoformat()},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "already pays this loan off" not in html, (
            f"the panel claims the plan already clears the loan by {target}, but "
            f"the chip on the same page shows it paying off {fold_payoff} -- the "
            "panel is searching a different forward model than the chip folds"
        )
        assert "Add on Top of Your Current Plan" in html


class TestPayoffChipDisplayStates:
    """Plan C8d: the "Projected payoff" chip has THREE states, one per answer.

    The chip renders the DERIVED payoff, which is ``None`` for two very
    different loans.  Rendering both the same way is what finding B-20 was: a
    loan paid off by a lump-sum true-up showed its ORIGINATION date as a
    "projected payoff" -- a past date presented as a future event.
    """

    def test_a_paying_loan_shows_its_payoff_month(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The ordinary state: a month, from the fold to zero."""
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Projected payoff" in html
        assert "Paid off" not in html
        assert "No payoff at current payment" not in html
        with auth_client.application.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            account = db.session.get(Account, acct.id)
            payoff = balance_at.loan_payoff_date(account, ctx)
        assert payoff is not None
        assert payoff.strftime("%b %Y") in html

    def test_a_retired_loan_is_badged_paid_off(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """B-20: a loan trued up to zero is BADGED, never dated.

        The chip must not fall back to a date at all -- and specifically not to
        the origination date the retired resolver fallback used to supply, which
        is what this asserts is absent.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        db.session.commit()
        insert_trueup_event(loan_params_for(db.session, acct.id), Decimal("0.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Paid off" in html
        assert "Projected payoff" not in html
        # The pre-C8d fallback rendered the ORIGINATION month here.
        assert "Jan 2026" not in html

    def test_a_loan_that_never_clears_says_so(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The third state: legible text, not a hidden chip.

        Trued up so high the level payment cannot cover the monthly interest
        ($900,000 at 6.5% accrues ~$4,875/mo against a ~$1,580 payment), the
        balance grows and the fold never reaches zero.  The user is told, which
        reinforces C7's drift warning rather than leaving a gap where a payoff
        date used to be.  The payment is a recurring one at the level P&I: a
        loan with none is planned as its contract line, whose last installment
        closes it with the balloon (ruling plan#3982), and this fixture had
        none until plan step recurrence:R16-f.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        make_loan_payment_template(db.session, seed_user, acct)
        db.session.commit()
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("900000.00"),
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No payoff at current payment" in html
        assert "Paid off" not in html


class TestOneWalkEdges:
    """R7d-g-3's adversarial review: the shapes the one-walk re-cut must survive.

    Each test is a shape the review traced through the seam's fold and found
    a surface answering wrong: an ARM whose plan pays before its first charge
    (the rate column read ``None`` and the page raised), a retired loan (the
    plan's payments folded to refunds and a phantom row, a five-year flat
    band and a "Never" lever followed), an installment due TODAY (the
    allocation bar skipped to next month), a plan that never clears (a
    dollar "saving" beside "Never"), and a loan behind on its payments (the
    shortfall the bar must say).
    """

    def test_an_arm_paying_before_its_first_charge_renders_its_rate(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A payment no charge stands over reads the period the calendar puts it in.

        ARM at 5% originated 2026-03-01 (first installment Apr 1), with a
        $50 sweep due the 15th whose Mar 15 occurrence the plan pays the day
        after the frozen 2026-03-20 today -- before Apr 1's charge, so the
        fold hands it no standing charge (ruling R-C's early extra).  Its
        row's rate is the origination period's 5%
        (``PaymentOutcome.period``, resolved from the stream's own
        calendar), so the ARM rate column renders every row and the page
        returns 200 rather than multiplying ``None``.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Early Sweep",
            Decimal("250000.00"), Decimal("0.05000"), 360,
            date(2026, 3, 1), 1,
        )
        params = loan_params_for(db.session, acct.id)
        params.is_arm = True
        db.session.commit()
        make_loan_payment_template(
            db.session, seed_user, acct, amount="50.00",
            derive_from_loan=False, cadence=MONTHLY, fires_on_day=15,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        rows = re.findall(
            r'<tr class="(?:table-success)?">\s*<td>(\d+)</td>\s*<td>([^<]+)</td>',
            html,
        )
        # The catch-up is the first planned row, before the loan's first
        # installment (#0), paid the day after today; the sweep's April
        # occurrence is installment #1's period (the loan has no other
        # definition, so the plan is the sweep's occurrences alone).
        assert rows[0] == ("0", "Mar 21, 2026"), rows[:2]
        assert rows[1] == ("1", "Apr 15, 2026"), rows[:2]
        rates = re.findall(r'>\s*([0-9]+\.[0-9]{3})%\s*</td>', html)
        assert rates and set(rates) == {"5.000"}, rates[:3]

    def test_a_retired_loan_lists_no_plan_and_draws_no_extension(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A loan trued up to zero has no planned row, no grid past its history, no lever.

        The seam's plan for a retired loan is EMPTY (``loan_installments``
        answers nothing for a retired loan, the guard the payoff already
        answers ``None`` under): the schedule
        page lists no Projected row, the band's grid ends with the history
        (no five-year flat zero past the contract), and the pay-off-sooner
        lever says the loan is paid off rather than "Never, at this extra".
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()
        insert_trueup_event(loan_params_for(db.session, acct.id), Decimal("0.00"))
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan/schedule")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.count('badge bg-success">Confirmed</span>') == 1
        assert html.count('badge bg-secondary">Projected</span>') == 0

        band = _parse_band_chart(
            auth_client.get(f"/accounts/{acct.id}/loan").data.decode()
        )
        assert band is not None
        # The history's one point and nothing past it.
        assert len(band["labels"]) == 1
        assert band["labels"] == ["Feb 2026"]

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "200"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "This loan is paid off" in html
        assert "Never, at this extra" not in html
        assert "data-overlay=" not in html

    def test_the_allocation_bar_reads_an_installment_due_today(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """An installment due TODAY and unpaid is this month's payment, not next month's.

        A $250k / 6.5% mortgage originated 2026-02-20 on the 20th: its first
        installment is due on the frozen 2026-03-20 today and no row answers
        it, so the plan pays it tomorrow (the D1 clamp) against March's
        charge.  The bar reads March -- ``$1,580.17`` paying ``$1,354.17`` of
        interest on the untouched ``$250,000`` -- where a bar keyed on "due
        after today" would have skipped to April on the morning the payment
        is due.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 2, 20),
            payment_day=20,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Mar 2026 payment" in html
        assert "Apr 2026 payment" not in html
        assert "$1,354.17" in html
        assert "$226.00" in html

    def test_a_plan_that_never_clears_shows_no_saving(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """"Never, at this extra" carries no months and no dollars saved.

        Trued up to $900,000 against a ~$1,580 payment the balance grows and
        neither trajectory reaches zero: the lever says "Never, at this
        extra", and both savings chips read "--" -- a difference between two
        sums cut at the plan's horizon is not a saving.  The plan-vs-contract
        line says the plan never pays off.  The loan's recurring payment at the
        level P&I is what keeps it from clearing: with none, its plan is the
        contract line and the term's last installment closes it (ruling
        plan#3982), and this fixture had none until plan step
        recurrence:R16-f.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        make_loan_payment_template(db.session, seed_user, acct)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[2], Decimal("1580.17"),
            status_enum=StatusEnum.DONE,
        )
        db.session.commit()
        insert_trueup_event(
            loan_params_for(db.session, acct.id), Decimal("900000.00"),
        )
        db.session.commit()

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "extra_payment", "extra_monthly": "10"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Never, at this extra" in html
        assert re.search(r"Months Saved</div>\s*<div[^>]*>\s*--\s*</div>", html)
        assert re.search(r"Interest Saved</div>\s*<div[^>]*>\s*--\s*</div>", html)
        assert "never pays off at the current plan" in html
        assert "saved" not in html.split("Current plan vs. original")[1]

    def test_a_loan_behind_says_its_shortfall(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two installments behind, the bar floors principal and says the shortfall.

        Nothing paid on a $250k / 6.5% mortgage originated 2026-01-01, whose
        owner deleted its recurring payment's February and March rows (an
        un-planned occurrence pays nothing and its month is charged anyway,
        rulings R-R37 and R-R71): the fold charges February, March and April
        against the Apr 1 payment
        (``$1,354.17 + $1,354.17 + $1,354.17 = $4,062.51`` of interest on the
        untouched balance, less what the ledger's own accrual model lays off
        -- the review's fold simulation puts the first planned payment's
        interest at ``$2,705.88``), so the payment's principal is negative.
        The bar shows the whole ``$1,580.17`` to interest, no principal
        segment, and says how far short it fell.  Until plan step
        recurrence:R16-f the loan had no recurring payment and nothing
        deleted: its plan billed no missed installment.  It bills them the day
        after the read now, split as if paid on time (rulings plan#3982,
        plan#3983, plan#4029), so that loan is not behind.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        _behind_by_unplanned_installments(
            seed_user, db.session, acct, seed_periods,
            (date(2026, 2, 1), date(2026, 3, 1)),
        )

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "short of the interest and escrow charged" in html
        pcts = re.findall(r'data-progress-pct="([0-9.]+)"', html)
        assert pcts == ["100.0"], pcts
        assert "to principal (0.0%)" in html
        assert "$1,580.17</span>\n        to interest (100.0%)" in html


class TestRefinanceAndPayoffByDateProjectForwardMigration:
    """C7-5, C7-7, C7-8: route-level assert-unchanged locks for the
    Commit 7 migration of ``refinance_calculate`` and the
    ``mode=target_date`` payoff branch onto :func:`project_forward`.

    The migration is behavior-preserving (per D-F of the
    implementation plan).  These tests pin the rendered HTML values
    that the legacy ``generate_schedule`` path produced; any drift
    proves a real regression.
    """

    def test_refinance_unchanged_vs_pre_commit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-5: refinance partial renders byte-identical key values.

        Pre-commit snapshot (200K mortgage at 6.5% refinancing to 5.0%,
        360 months, $0 closing costs):
          - refi_monthly        = $1,073.64
          - refi_total_interest = $186,513.24
          - monthly_savings     = $190.50
          - interest_savings    = $68,572.58
        Hand calculation (matches existing
        ``test_refinance_comparison_metrics_hand_calculated``):
          Current:   M(200000, 0.065/12, 360) = $1,264.14;
                     total interest = $255,085.82.
          Refinance: M(200000, 0.05/12, 360)  = $1,073.64;
                     total interest = $186,513.24.
          Savings:   1264.14 - 1073.64 = 190.50/mo;
                     255085.82 - 186513.24 = 68572.58.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/refinance",
            data={
                "new_rate": "5.0",
                "new_term_months": "360",
                "closing_costs": "0",
            },
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$1,264.14" in html, "current monthly drifted"
        assert "$1,073.64" in html, "refi monthly drifted"
        assert "$190.50" in html, "monthly savings drifted"
        assert "$255,085.82" in html, "current total interest drifted"
        assert "$186,513.24" in html, "refi total interest drifted"
        assert "$68,572.58" in html, "interest savings drifted"

    def test_no_generate_schedule_in_refinance(self):
        """C7-7: the refinance schedule projection makes no generate_schedule call.

        Structural guarantee mirroring C7-6 at the route layer.  The
        refinance schedule is built by ``_project_refinance`` (Phase 3
        pylint cleanup decomposed ``refinance_calculate``; the route
        delegates through ``_build_refinance_comparison``).  The
        builder's function-body slice -- between its ``def`` and the
        next top-level ``def`` in ``app/routes/loan/calculators.py`` --
        must project via ``project_forward``, never reference or call
        ``generate_schedule``.
        """
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        # Found from the package's own module, never from this test's place
        # in the tree; a missing file raises and ``index`` below raises on an
        # empty read.
        calculators = Path(inspect.getfile(loan_routes)).parent / "calculators.py"
        source = calculators.read_text(encoding="utf-8")
        marker = "def _project_refinance("
        start = source.index(marker)
        next_def = source.find("\ndef ", start + len(marker))
        end = next_def if next_def != -1 else len(source)
        body = source[start:end]
        assert "amortization_engine.generate_schedule" not in body, (
            "_project_refinance must not reference "
            "amortization_engine.generate_schedule after Commit 7."
        )
        assert "generate_schedule(" not in body, (
            "_project_refinance must not call generate_schedule."
        )

    def test_target_date_route_branch_unchanged(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """C7-8: ``mode=target_date`` HTML renders byte-identical
        ``required_extra`` and ``total_monthly``.

        Uses the exact $200k / 6.5% / 30yr mortgage helper.  Today is
        frozen to 2026-03-20 by ``_freeze_today_inside_seed_range``,
        and ``_create_exact_mortgage`` originates "today," so the
        route's binary search anchors at ``2026-03-01`` (today's first
        of month) with starting_date 2026-04-01.  Target 2041-01-01.
        Pre-commit values from the legacy ``generate_schedule``-backed
        binary search (captured 2026-05-22):
          - required_extra = $489.67 (binary-search convergence
            against the contractual M(200000, 0.065/12, 360) =
            $1,264.14)
          - total_monthly  = 1264.14 + 489.67 = $1,753.81
        The HTMX partial only renders required_extra and total_monthly
        in this mode; ``monthly_payment`` is passed for context-builder
        completeness but is not surfaced as a distinct label, so the
        assertion set matches what the user actually sees.
        """
        acct = _create_exact_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payoff",
            data={"mode": "target_date", "target_date": "2041-01-01"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$489.67" in html, "required_extra drifted"
        assert "$1,753.81" in html, "total_monthly drifted"
