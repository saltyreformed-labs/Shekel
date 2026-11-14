"""Savings dashboard service tests: the debt-free date: unclearing debt, a retired loan, and its
one derivation.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.services import balance_at, savings_dashboard_service
from app.services.balance_at import BalanceContext

from ._helpers import _with_badging_predicate


class TestUnclearingDebtHasNoDebtFreeDate:
    """A loan that never pays off must not be dropped from the debt-free date.

    Plan C8d made ``payoff_date`` legitimately ``None`` for a loan whose payment
    never clears it.  Both debt-free producers filtered those out and took
    ``max()`` over what remained, so a borrower owing $900,000 on a loan the loan
    page labels "No payoff at current payment" was told they go debt-free when
    their small loan ends -- and with every loan in that state, the Horizon fell
    through to its LOAN-FREE fallback window entirely.  Before C8d this could not
    happen: ``LoanState.payoff_date`` was non-nullable.
    """

    def _never_clearing_loan(self, seed_user, db_session, periods):
        """A loan whose recurring level payment cannot cover its own monthly interest.

        Trued up to $900,000 against a $240,000/30yr contract (a ~$1,439 payment
        versus $4,500 of monthly interest at 6%), so the balance grows and the
        fold never reaches zero.  The payment is the loan door's recurring
        one: a loan with none is planned as its contract line, whose last
        installment closes it with the balloon (ruling plan#3982), and this
        fixture had none until plan step recurrence:R16-f.
        """
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account, insert_trueup_event, loan_params_for,
            make_loan_payment_template,
        )
        acct = create_loan_account(
            seed_user, db_session, name="Never Clears",
            principal=Decimal("240000.00"), rate=Decimal("0.06000"), term=360,
            origination_date=date(2026, 1, 1),
        )
        make_loan_payment_template(db_session, seed_user, acct)
        insert_trueup_event(
            loan_params_for(db_session, acct.id), Decimal("900000.00"),
        )
        db_session.commit()
        return acct

    def test_the_debt_summary_reports_no_debt_free_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The date is ``None`` and the reason is flagged, not silently dropped."""
        with app.app_context():
            from app.services import balance_at  # pylint: disable=import-outside-toplevel
            acct = self._never_clearing_loan(seed_user, db.session, seed_periods)

            ctx = BalanceContext.build(seed_user["user"].id)
            figures = balance_at.loan_figures(acct, ctx)
            assert figures.payoff_date is None, "precondition: it never clears"
            assert figures.is_retired is False

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            summary = data["debt_summary"]
            assert summary is not None
            assert summary.total_debt > Decimal("0.00")
            assert summary.payoff_outlook.all_clear_on is None, (
                "the cockpit reports a debt-free date while a loan on the same "
                "page never pays off"
            )
            assert summary.payoff_outlook.never_clears is True

    def test_a_clearing_loan_beside_it_does_not_supply_the_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The healthy loan's payoff must NOT stand in as the debt-free date.

        The measured shape: a $12,000 loan that clears in 2028 alongside a
        $900,000 loan that never does.  Taking ``max()`` over the loans that DO
        clear yields 2028 -- a date at which the borrower still owes $900,000.
        """
        with app.app_context():
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_loan_account,
            )
            self._never_clearing_loan(seed_user, db.session, seed_periods)
            create_loan_account(
                seed_user, db.session, name="Healthy Small",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert data["debt_summary"].payoff_outlook.all_clear_on is None

    def test_the_horizon_is_not_loan_free(
        self, app, db, seed_user, seed_periods,
    ):
        """The Horizon plants no "Debt-free" flag and does not go loan-free.

        Two assertions on two producers, because plan step X-q2 put each fact
        where it is derived.  The Horizon plants no flag -- its domain resolver
        has no date to plant one at.  Whether the user is loan-free is the
        OUTLOOK's, and the resolver used to republish it as a third tuple
        element nothing read (finding N-100); a "no date" that means "a loan
        never clears" and a "no date" that means "no loans" are what
        :class:`~..._debt_line.LoanPayoffOutlook` exists to tell apart, and the
        cockpit footer on the same page renders the difference.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._never_clearing_loan(seed_user, db.session, seed_periods)

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            _end, debt_free = _resolve_horizon_domain(
                data["account_data"], date(2026, 3, 20),
            )
            assert debt_free is None
            assert loan_payoff_outlook(
                data["account_data"],
            ).is_loan_free is False, (
                "a borrower carrying a loan that never pays off was reported "
                "loan-free"
            )


class TestARetiredLoanHasNoDebtLine:
    """The Horizon asks the debt-line question with the debt-line predicate.

    Finding B-16, plan step X-o.  ``LoanFigures`` states the split in terms --
    "Use ``is_retired`` to decide whether a loan has a debt line; use this
    [``is_paid_off``] to decide whether to CONGRATULATE the user" -- because
    ``is_paid_off`` adds a confirmed-payment guard that exists for BADGING.  A
    loan paid off by a LUMP SUM recorded as a balance true-up has no payment
    rows, so it owes ``$0.00`` and reads ``is_paid_off=False``.

    The Horizon's domain resolver asked the debt-LINE question with that
    badging predicate, so such a loan stayed in the ACTIVE set -- and, being
    retired, it has no forward payoff to date, which fired the "an active loan
    with no payoff never clears" branch: no debt-free date, every STRUCTURAL
    flag gone (the payoffs and "Debt-free"; the net-worth crossing flags are
    built from the trajectory and survived), and the axis cut back to the
    loan-free fallback window, while the debt-summary caption on the SAME page
    (which selects on the loan's BALANCE) still reported the real date.
    Measured on the developer's own two loans: the axis ended 2036-12-31 where
    the debt line ends 2049-12-31.  The same collapse drew $197,049.32 of
    phantom debt on the property equity chart -- the incident the seam's
    contract was written by.

    **Only the DOMAIN resolver was a defect.**  ``_structural_milestones``
    took the same predicate, and plan step X-o moved it onto the shared
    selection too -- but its own ``payoff is not None`` test already excluded
    every retired loan (a retired loan has no forward crossing to date), so
    that half is behaviour-neutral by construction and has no firing control
    because none can exist.  Said here rather than discovered at a review.
    """

    # today + _LOAN_FREE_HORIZON_YEARS, on this module's frozen 2026-03-20
    # clock: the window the resolver falls back to when it has no debt-free
    # date to size an axis with.
    _FALLBACK_END = date(2036, 12, 31)
    # The clearing loan's DERIVED payoff.  Its 24 installments run 2026-02-01
    # .. 2028-01-01, and at the frozen 2026-03-20 as-of TWO (2026-02-01 and
    # 2026-03-01) are already due and unpaid.  It has no recurring payment, so
    # its plan is its contract line (ruling plan#3982): the two count from
    # 2026-03-21 and split as if paid on time (rulings plan#3983, plan#4029),
    # and the term's last installment closes it on the contractual date.
    # Until plan step recurrence:R16-f the plan billed no missed installment,
    # so its zero crossing landed past the contract: 2028-03-01, then
    # 2028-04-01 once the two skipped months were charged (plan step R16-b-2,
    # ruling R-R71).
    _CLEARING_PAYOFF = date(2028, 1, 1)
    # The domain end the resolver derives from it: the payoff year plus one,
    # at that year's end.
    _CLEARING_DOMAIN_END = date(2029, 12, 31)

    def _two_loans(self, seed_user, db_session, periods):
        """A loan retired by a lump-sum true-up, beside one that still clears.

        The retired loan has ZERO settled payment rows -- the shape the app's
        own true-up UI produces -- so it is ``is_retired`` without being
        ``is_paid_off``.  Its true-up is dated two months AFTER origination so
        the fixture tells the story it claims (a loan borrowed, then cleared by
        a lump sum) rather than the degenerate ``$0``-opening shape
        ``is_paid_off``'s guard exists to catch.

        The second loan is what makes the debt-free date OBSERVABLE: with only
        the retired one there is no date to report either way, and the test
        could not tell the predicates apart.
        """
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account, insert_trueup_event, loan_params_for,
        )
        retired = create_loan_account(
            seed_user, db_session, name="Lump Sum Payoff",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1),
        )
        insert_trueup_event(
            loan_params_for(db_session, retired.id), Decimal("0.00"),
            anchor_date=date(2026, 3, 1),
        )
        clearing = create_loan_account(
            seed_user, db_session, name="Still Clearing",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1),
        )
        db_session.commit()
        return retired, clearing

    def test_the_retired_loan_is_not_a_debt_line(
        self, app, db, seed_user, seed_periods,
    ):
        """The debt-free date is the SURVIVING loan's payoff, and it is flagged.

        End to end through ``compute_dashboard_data``: the domain runs to the
        clearing loan's payoff plus a year and the "Debt-free" flag lands on
        that payoff.  Every expected value is a pinned literal, not a value
        read back off the producer: a regression that moved the clearing
        loan's payoff to some other future date must fail here.  Its firing
        control is :meth:`test_the_badging_predicate_loses_the_debt_free_date`,
        which re-runs the same data through the predicate this step replaced.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _DEBT_FREE_MILESTONE_LABEL,
                _resolve_horizon_domain,
            )
            retired, clearing = self._two_loans(
                seed_user, db.session, seed_periods,
            )

            ctx = BalanceContext.build(seed_user["user"].id)
            retired_figures = balance_at.loan_figures(retired, ctx)
            clearing_figures = balance_at.loan_figures(clearing, ctx)
            # The precondition that makes this fixture discriminating: the loan
            # owes nothing and is NOT badged, because nothing was ever paid.
            assert balance_at.balance_at(
                retired, ctx, ctx.as_of,
            ) == Decimal("0.00")
            assert retired_figures.is_retired is True
            assert retired_figures.is_paid_off is False
            assert retired_figures.payoff_date is None
            assert clearing_figures.payoff_date == self._CLEARING_PAYOFF

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            assert _resolve_horizon_domain(
                data["account_data"], date(2026, 3, 20),
            ) == (self._CLEARING_DOMAIN_END, self._CLEARING_PAYOFF)

            horizon = data["net_worth"].horizon
            assert {
                (m["label"], m["date"]) for m in horizon["milestones"]
            } >= {(_DEBT_FREE_MILESTONE_LABEL, self._CLEARING_PAYOFF)}

            # And the caption on the same page agrees, which is the point:
            # the debt summary always read the real date here (it selects on
            # the loan's BALANCE) while the chart beside it did not.  Since
            # plan step X-q both fold ONE derivation
            # (``_debt_line.loan_payoff_outlook``), so this asserts an
            # agreement that is now structural; the shape that proves the
            # merge is ``TestTheDebtFreeDateIsOneDerivation``.
            assert data["debt_summary"].payoff_outlook.all_clear_on == (
                self._CLEARING_PAYOFF
            )

    def test_the_badging_predicate_loses_the_debt_free_date(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the replaced predicate, on the same real data.

        ``_debt_line.debt_line_loans`` selects on ``is_retired``; before plan
        step X-o it selected on ``is_paid_off`` (and lived in ``_horizon``).  Substituting the old predicate into the
        projection dicts -- exactly what the old line read -- must produce the
        defect: no debt-free date, and the domain cut back to the loan-free
        fallback window.  A control that cannot fail is not a control
        (Section 7.3 of the balance plan of record).
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._two_loans(seed_user, db.session, seed_periods)

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            account_data = data["account_data"]
            # The fixed producer, on unmodified data.
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (self._CLEARING_DOMAIN_END, self._CLEARING_PAYOFF)

            # The same producer, reading the badging predicate instead.  The
            # substitution is made through ``dataclasses.replace`` on the real
            # frozen types at all three levels, so the control exercises the
            # production shapes rather than dicts arranged to look like them.
            assert _resolve_horizon_domain(
                _with_badging_predicate(account_data), date(2026, 3, 20),
            ) == (self._FALLBACK_END, None), (
                "the control does not fire: the badging predicate produced the "
                "same domain as the debt-line predicate, so this fixture "
                "cannot tell them apart"
            )

    def test_a_user_whose_only_loan_is_retired_is_loan_free(
        self, app, db, seed_user, seed_periods,
    ):
        """One loan, retired by a true-up: the user IS loan-free.

        The single-loan half of the same defect.  ``is_loan_free`` separates
        "no debt line at all" from "a debt line that never clears", and the
        badging predicate collapsed a retired loan into the second.

        **This one pins a producer CONTRACT, not a rendered figure, and the
        distinction is stated so the coverage is not over-read.**  With a
        single retired loan the Horizon's domain resolver returns the same
        answer either way -- the fallback window and no date -- so the sample
        dates, both series and the milestone list are identical whichever
        predicate is used, and the assertion below records that rather than
        claiming a chart moved.

        **The discriminating value is the OUTLOOK's ``is_loan_free``, and it is
        asserted where it is derived** (plan step X-q2).  The resolver
        republished it as a third tuple element until then, which is finding
        N-100 -- so this test was asserting the state of a copy nothing read.
        The copy is gone; the fact is not, and a producer that reports "still
        in debt" for a borrower who owes nothing is wrong whether or not a
        screen has asked yet.  The cockpit footer is the screen that asks.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_loan_account, insert_trueup_event, loan_params_for,
            )
            retired = create_loan_account(
                seed_user, db.session, name="Only Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            insert_trueup_event(
                loan_params_for(db.session, retired.id), Decimal("0.00"),
            )
            db.session.commit()

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            account_data = data["account_data"]
            assert loan_payoff_outlook(account_data).is_loan_free is True
            # The chart is unmoved either way, and that is the point of the
            # note above: the axis has no payoff to size itself on.
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (self._FALLBACK_END, None)

            # FIRING CONTROL: the badging predicate keeps the retired loan in
            # the active set, and a retired loan has no forward payoff to date
            # -- so the user is reported NOT loan-free, on a loan they have
            # already cleared.
            badged = _with_badging_predicate(account_data)
            assert loan_payoff_outlook(badged).is_loan_free is False, (
                "the control does not fire: the badging predicate reported the "
                "same loan-free state as the debt-line predicate, so this "
                "fixture cannot tell them apart"
            )
            assert _resolve_horizon_domain(
                badged, date(2026, 3, 20),
            ) == (self._FALLBACK_END, None)


class TestTheDebtFreeDateIsOneDerivation:
    """The caption and the Horizon flag read ONE debt-free derivation.

    Finding N-98, plan step X-q.  ``/savings`` renders both on one page and
    derived the date twice from the same ``account_data``: the cockpit's
    ``Debt-free <month>`` caption through ``_debt_summary._compute_debt_summary``,
    which selected loans by their current BALANCE, and the Horizon chart's
    ``Debt-free`` flag through ``_horizon._resolve_horizon_domain``, which
    selected them by the debt-line predicate.

    They part on a loan that has NOT been borrowed yet.  It owes ``$0.00``
    today, so the balance rule dropped a mortgage whose whole 30-year line is
    ahead of it and the caption reported the date the OTHER loans finish --
    measured 19 years early on the developer's own mortgage rewritten into
    that state, and 28 years early on the fixture below.

    Both now fold :func:`.._debt_line.loan_payoff_outlook`.  The MONEY figures
    keep their own membership deliberately (``total_debt`` and the monthly
    payments answer "what do you owe today", where an unclosed mortgage
    contributes nothing and pays nothing), which the last assertion pins so a
    later "simplification" onto one set has to fail here first.
    """

    # A 24-month, $12,000 @ 5% loan originated 2026-01-01 (payment_day 1) has
    # installments due 2026-02-01 .. 2028-01-01; the two already due and
    # unpaid at the module's frozen 2026-03-20 clock count the day after it,
    # and the term's last closes it on the contractual date (the same loan as
    # ``TestARetiredLoanHasNoDebtLine._CLEARING_PAYOFF``, where the rulings
    # and the earlier figures are).
    _CAR_PAYOFF = date(2028, 1, 1)
    # A 360-month mortgage originating 2026-06-01 (payment_day 1) has not been
    # borrowed at the frozen clock, so nothing is overdue and its plan folds
    # from its opening anchor to the contractual last installment.
    _MORTGAGE_PAYOFF = date(2056, 6, 1)

    def _car_now_and_mortgage_later(self, seed_user, db_session, periods):
        """A loan already running, beside a mortgage that closes in June."""
        # Pylint: ``import-outside-toplevel`` -- test-local helpers, matching
        # this module's convention of importing them where used.
        from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
            create_loan_account,
        )
        create_loan_account(
            seed_user, db_session, name="Car Loan",
            principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
            origination_date=date(2026, 1, 1),
        )
        create_loan_account(
            seed_user, db_session, name="Future Mortgage",
            principal=Decimal("200000.00"), rate=Decimal("0.06000"), term=360,
            origination_date=date(2026, 6, 1),
            account_type=AcctTypeEnum.MORTGAGE,
        )
        db_session.commit()

    def test_an_unclosed_mortgage_sets_the_date_on_both_surfaces(
        self, app, db, seed_user, seed_periods,
    ):
        """Both surfaces report the mortgage's payoff, not the car loan's."""
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _DEBT_FREE_MILESTONE_LABEL,
                _resolve_horizon_domain,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            account_data = data["account_data"]
            by_name = {
                ad.account.name: ad for ad in account_data
                if ad.loan is not None
            }
            # The preconditions that make the fixture discriminating: the
            # mortgage owes nothing today and is NOT retired -- its whole line
            # is ahead of it -- and it clears far later than the car loan.
            assert by_name["Future Mortgage"].current_balance == (
                Decimal("0.00")
            )
            assert by_name["Future Mortgage"].loan.figures.terms.is_originated is False
            assert by_name["Future Mortgage"].loan.figures.is_retired is False
            assert by_name["Future Mortgage"].loan.figures.payoff_date == (
                self._MORTGAGE_PAYOFF
            )
            assert by_name["Car Loan"].loan.figures.payoff_date == self._CAR_PAYOFF

            # ONE derivation, and both surfaces read it.
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            )
            assert data["debt_summary"].payoff_outlook.all_clear_on == (
                self._MORTGAGE_PAYOFF
            )
            assert _resolve_horizon_domain(
                account_data, date(2026, 3, 20),
            ) == (date(2057, 12, 31), self._MORTGAGE_PAYOFF)
            assert {
                (m["label"], m["date"]) for m in data["net_worth"].horizon[
                    "milestones"
                ]
            } >= {(_DEBT_FREE_MILESTONE_LABEL, self._MORTGAGE_PAYOFF)}

            # The MONEY figures keep the owed-today set: the mortgage is not
            # borrowed, so it owes nothing and pays nothing this month.  Pinned
            # as literals, not read back off the producer: the car loan has no
            # settled payment at the frozen clock, so the fold is still its
            # $12,000.00 opening anchor, and its level payment is
            # 12000 * r(1+r)^24 / ((1+r)^24 - 1) at r = 0.05/12 = $526.46.
            assert data["debt_summary"].total_debt == Decimal("12000.00")
            assert data["debt_summary"].total_monthly_payments == (
                Decimal("526.46")
            )

    def test_the_balance_rule_reports_the_wrong_date(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the replaced membership rule, on the same data.

        Before plan step X-q the caption's date was derived inside the
        owed-today loop, so its membership was "owes something today" --
        spelled "current balance > 0" then, when a loan's balance was its owed
        figure.  The balance is HELD since plan step credit_card:CC-5-5c, so the
        control reads :attr:`~.._types.AccountProjection.owed`.  This
        substitutes that MEMBERSHIP into the new fold rather than resurrecting
        the deleted loop -- the fold itself (latest payoff, poisoned by an
        absent one) is unchanged between them, so the set is the whole
        difference and is what this isolates.  It must produce the CAR LOAN's
        payoff: the borrower told they are done with debt 28 years before a
        mortgage they are about to close is paid off.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producer under
            # test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            account_data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["account_data"]
            owed_today = [
                ad for ad in account_data
                if ad.loan is not None
                and ad.owed > Decimal("0.00")
            ]
            assert loan_payoff_outlook(owed_today).all_clear_on == (
                self._CAR_PAYOFF
            ), (
                "the control does not fire: the owed-today set produced the "
                "same date as the debt-line set, so this fixture cannot tell "
                "the two membership rules apart"
            )
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            )

    def test_a_past_payoff_is_reported_but_cannot_size_the_axis(
        self, app, db, seed_user, seed_periods,
    ):
        """The developer's ruling on a payoff date that is already behind us.

        The derived payoff is the DUE date the balance first folds to
        zero, and an overdue-but-still-projected installment that clears the
        loan folds at a date behind today.  The ruling (plan step X-q): the
        outlook REPORTS that date -- it is a fact about the loan's plan -- and
        the Horizon, whose axis is today-forward, falls back to its fixed
        window for AXIS SIZING only, because
        ``savings._milestone_axis_x`` clamps a target at or before
        ``dates[0]`` to index 0.0 and the flag would be planted on "Today".

        The state is exercised by asking the domain resolver about a ``today``
        past every payoff, which is what the producer's own signature allows
        and is far cheaper than manufacturing an overdue installment.  **This
        is the branch that CHANGED**: the rule it replaced dropped past
        payoffs per loan and then read the empty list as "no loans at all",
        so it returned ``is_loan_free=True`` for a borrower whose loan had
        not cleared.

        **Plan step X-q2 made that answer unreachable from here rather than
        merely correct.**  The resolver no longer returns a loan-free flag at
        all, and the outlook that does own one takes no reader ``today`` to
        filter payoffs on -- so a reader's clock can size an axis and can
        decide whether a flag is drawable, and it can no longer decide whether
        the borrower is out of debt.  Both assertions below are therefore about
        different producers on purpose.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- the private producers
            # under test, imported where used.
            from app.services.savings_dashboard_service._debt_line import (  # pylint: disable=import-outside-toplevel
                loan_payoff_outlook,
            )
            from app.services.savings_dashboard_service._horizon import (  # pylint: disable=import-outside-toplevel
                _resolve_horizon_domain,
            )
            self._car_now_and_mortgage_later(
                seed_user, db.session, seed_periods,
            )

            account_data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["account_data"]
            # Read at a date past BOTH payoffs.
            after_everything = date(2060, 1, 1)
            assert loan_payoff_outlook(account_data).all_clear_on == (
                self._MORTGAGE_PAYOFF
            ), "the outlook reports the date whatever the reader's clock"
            assert loan_payoff_outlook(account_data).is_loan_free is False, (
                "a borrower whose loans have not cleared was reported loan-free"
            )
            assert _resolve_horizon_domain(
                account_data, after_everything,
            ) == (date(2070, 12, 31), None), (
                "the axis was sized to a past date, or the flag was kept for "
                "``_milestone_axis_x`` to clamp onto Today"
            )

    def test_a_revolving_balance_is_named_beside_the_date(
        self, app, db, seed_user, seed_periods,
    ):
        """A card the payoff date cannot cover is reported, not implied away.

        Developer ruling on finding N-99 (plan step X-q3): the derivation
        stays over the debts that HAVE a payoff model, and the surfaces say
        so.  A revolving Credit Card has no PAYOFF model -- its forward balance
        is its cash fold (plan step credit_card:CC-1), which no schedule pays
        off -- and it is invisible to :func:`.._debt_line.loan_payoff_outlook`.
        Without the
        caveat a borrower reads a payoff month on a page whose own liability
        band never touches zero.

        The card is anchored OWED-AS-NEGATIVE, which is the app's convention
        (``TestNegativelyAnchoredLiability``), and the caveat reports what it
        OWES: the seam's one flip of ``-500.00``, floored at zero (ruling
        R-CC49, plan step credit_card:CC-5-5a; it read ``abs`` until then, which
        agrees here and parts on a card holding a credit --
        ``TestTheRevolvingFooterCountsWhatEachAccountOwes``).
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helper,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            summary = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )["debt_summary"]

            # The card is NOT in the payoff derivation and does NOT poison it
            # -- that is the ruling, not an accident.
            assert summary.payoff_outlook.all_clear_on == (
                self._CAR_PAYOFF
            )
            assert summary.payoff_outlook.never_clears is False
            # And it is not in the loan money aggregates either.
            assert summary.total_debt == Decimal("12000.00")
            # It IS named, at its owed magnitude.
            assert summary.debt_without_payoff_date == Decimal("500.00")

    def test_the_narrow_producer_reports_the_same_debt_without_payoff_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The two paths to the debt summary agree on EVERY key.

        ``compute_debt_summary`` promises "identical figures to
        ``compute_dashboard_data(...)['debt_summary']`` by construction", and
        it kept that promise by projecting the same loans.  Plan step X-q3
        added a key that is NOT about loans -- the revolving debt the payoff
        date cannot speak for -- so a loans-only projection reported
        ``$0.00`` there while the full build reported the real figure.
        Nothing rendered the difference, which is what made it worth fixing:
        two paths to one number that quietly disagree are the shape this arc
        exists to remove.
        """
        with app.app_context():
            # Pylint: ``import-outside-toplevel`` -- test-local helpers,
            # matching this module's convention of importing where used.
            from tests._test_helpers import (  # pylint: disable=import-outside-toplevel
                create_account_of_type, create_loan_account,
            )
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("12000.00"), rate=Decimal("0.05000"),
                term=24, origination_date=date(2026, 1, 1),
            )
            create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            user_id = seed_user["user"].id

            full = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(user_id),
            )["debt_summary"]
            narrow = savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(user_id),
            )

            assert narrow.debt_without_payoff_date == Decimal("500.00")
            assert narrow.debt_without_payoff_date == full.debt_without_payoff_date
            # And every other key still agrees, which is the promise itself.
            assert narrow == full
