"""Loan route tests: a transfer's closing bound, never written by a route; schedule rows resolving
their own terms; and the loan page naming a payment by its installment.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import AcctTypeEnum
from app.models.escrow_line import EscrowComponentVersion
from app.models.loan_features import RateHistory
from app.models.ref import AccountType
from app.services.loan_loaders import load_loan_params
from app.utils.money import round_money
from app.services import account_service, escrow_calculator, loan_loaders

from tests._test_helpers import (
    add_escrow_line,
    bind_rule_to_loan,
    create_settled_transfer,
    make_cadence_rule,
)
from tests.oracles.recurrence_baseline import MONTHLY

from ._helpers import (
    _create_loan_account,
    _create_mortgage,
    _create_other_loan,
    _parse_band_chart,
    _create_fresh_mortgage,
    _create_loan_account_exact,
    _create_transfer_template,
    _assert_column_untouched_and_stop_derived,
)


class TestTheClosingBoundIsNeverWrittenByARoute:
    """No payoff-affecting mutation writes ``end_date`` (plan step R7d-g).

    Until R7d-g these cases pinned the opposite: ten chokepoints -- recurring-
    transfer creation, a settled payment, a params / rate edit, a balance
    true-up, the extra-principal and track-payment doors, the tracking start,
    a payment leaving the loan -- each synced the rule's ``end_date`` to the
    projected payoff, and these tests pinned that WIRING through the real
    routes.  The writer is deleted, so the same routes are driven and the same
    column is asserted UNTOUCHED, beside the derived stop that replaced it
    (``recurring_definition.resolved_definition``, the door generation reads
    since plan step R7d-c-2).  A route that grows the write back turns its
    case red.  The payoff computation itself is graded in
    tests/test_services/test_loan_recurrence_sync.py.
    """

    def test_transfer_creation_writes_the_start_and_not_the_stop(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Creating the recurring transfer bounds its START to the contract only.

        The create-transfer POST authors the rule with its first occurrence
        from the loan's contract (``loan_recurrence_sync.loan_cadence_start``;
        ``bind_rule_to_loan`` until plan step R7d-g-3) before generating any
        transfer; the closing bound is the door's derived payoff, years out
        for a mortgage, and the column stays NULL.
        """
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        acct = _create_mortgage(seed_user, db.session)
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/create-transfer",
            data={"source_account_id": str(seed_user["account"].id)},
        )
        assert resp.status_code == 302

        tpl = (
            db.session.query(TransferTemplate)
            .filter_by(to_account_id=acct.id, user_id=seed_user["user"].id)
            .first()
        )
        rule = tpl.recurrence_rule
        assert isinstance(rule.starts_on, date)
        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        # Mortgage payoff is years in the future.
        assert stop.on > date.today()

    def test_a_settled_payment_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Settling a loan payment moves the payoff and writes nothing (settle path).

        The transfer settle chokepoint (``_loan_posting._sync_loan_postings_if_loan``)
        used to re-sync ``end_date``, then reconciled the loan's stored ledger
        only, until plan task plan#3075 deleted it: a settle writes no loan state.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        # Settle a payment in a period that has begun, so the resolver replays it.
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], acct,
            seed_periods[4], amount=Decimal("1580.17"),
        )
        db.session.commit()

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_dashboard_get_is_read_only(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loading the loan dashboard NEVER writes the recurrence rule (R-4).

        A mortgage with a recurring transfer stays as stored across a GET:
        the detail page is read-only, and since plan step R7d-g nothing else
        writes the closing bound either.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

    def test_no_update_when_no_transfer(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Mortgage without recurring transfer: no error, no update.

        The dashboard should render normally when there is no
        recurring transfer template to update.
        """
        acct = _create_mortgage(seed_user, db.session)
        # No transfer template created.

        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        assert b"Balance owed" in resp.data

    def test_a_params_edit_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Editing loan params moves the payoff and writes no stop (params path).

        The one chokepoint that still syncs anything: a term / rate /
        payment-day edit re-derives the rule's START (ruling **R-R29**) and
        nothing else.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "1",
                "term_months": "360",
            },
        )
        assert resp.status_code == 302

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_a_params_edit_that_moves_the_start_past_an_authored_stop_is_refused(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The ONE state the window CHECK refuses that a loan edit can reach (ruling **R-R82**).

        The standing payment carries a stop its owner authored two days after
        the loan's first installment (a stop kept from before the definition
        became the standing payment); moving ``payment_day`` past it would
        write ``starts_on > end_date``.  The route refuses the edit WHOLE --
        the params are as they were, the rule is as it was, the flash names
        the transfer -- rather than letting the flush answer with an
        ``IntegrityError``.
        """
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        # Bound to the contract the way every production door binds a new
        # rule, so the start the edit would move is the loan's own.
        bind_rule_to_loan(rule, acct.id)
        params = load_loan_params(acct.id)
        first_installment = rule.starts_on
        assert first_installment.day == params.payment_day
        rule.end_date = first_installment + timedelta(days=2)
        db.session.commit()
        payment_day_before = params.payment_day
        term_before = params.term_months
        assert payment_day_before != 15

        assert term_before != 300
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/params",
            data={
                "interest_rate": "6.500",
                "payment_day": "15",
                "term_months": "300",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert tpl.name in body
        # The remedy clause, worded once for every door since plan step
        # R7d-g-2 (ruling R-R85) -- see the setup case above.
        assert "Set it up again without an end date, or archive it." in body

        db.session.expire_all()
        params = load_loan_params(acct.id)
        assert params.payment_day == payment_day_before
        assert params.term_months == term_before
        db.session.refresh(rule)
        assert rule.starts_on == first_installment
        assert rule.end_date == first_installment + timedelta(days=2)

    def test_a_rate_change_leaves_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Recording an ARM rate change moves the payoff and writes nothing (rate path)."""
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM End Date Mortgage",
            Decimal("250000.00"), Decimal("0.05000"), 360,
            date(2023, 6, 1), 1, is_arm=True,
        )
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/rate",
            data={"effective_date": "2026-01-01", "interest_rate": "7.000"},
        )
        assert resp.status_code == 200

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on > date.today()

    def test_a_true_up_to_zero_leaves_the_column_alone_and_the_stop_is_PAST(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Trueing a loan up to $0 retires it: the derived stop is past, the column NULL.

        Recording a $0 balance retires the loan, so the door's derived stop is
        the day it became closed -- a past date that stops generation -- and
        it is read, never stored.  Until R7d-g this route wrote that past
        date into ``end_date``; a loan cleared BEFORE its first installment
        then stored the inverted pair that held ``ck_recurrence_rules_valid_window``
        back, which is unconstructible now.
        """
        acct = _create_loan_account_exact(
            seed_user, db.session, AcctTypeEnum.AUTO_LOAN, "Paid Off Loan",
            Decimal("1000.00"),
            Decimal("0.05000"), 12, date(2026, 1, 1), 1,
        )
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={"anchor_date": "2026-03-01", "anchor_balance": "0.00"},
        )
        assert resp.status_code == 302

        stop = _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)
        assert stop.on <= date.today(), (
            f"Paid-off loan should stop generation with a past derived stop, "
            f"got {stop.on}"
        )

    def test_the_extra_principal_and_track_doors_leave_the_column_alone(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The two settings-row doors move the payoff and write no stop."""
        acct = _create_mortgage(seed_user, db.session)
        tpl, rule = _create_transfer_template(seed_user, db.session, acct)
        assert rule.end_date is None

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/settings",
            data={"extra_principal": "250.00"},
        )
        assert resp.status_code == 302
        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/payments/{tpl.id}/track",
        )
        assert resp.status_code == 302
        _assert_column_untouched_and_stop_derived(rule, tpl, seed_user)

    def test_end_date_no_params_no_crash(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Loan account with no LoanParams: dashboard renders setup page.

        Nothing about the rule is reached because the dashboard returns
        early when params are missing.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        loan_type = db.session.query(AccountType).filter_by(name="Auto Loan").one()
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="No Params Loan",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.add(account)
        db.session.flush()

        # Create a template even though no LoanParams exist.
        tpl = TransferTemplate(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=account.id,
            name="Premature Payment",
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6),
        # committed before the request (plan step X-i3).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        db.session.commit()

        resp = auth_client.get(f"/accounts/{account.id}/loan")
        assert resp.status_code == 200
        assert b"Configure" in resp.data

        # Rule should be unchanged -- nothing reached it.
        db.session.refresh(rule)
        assert rule.end_date is None

    def test_end_date_idor(
        self, auth_client, seed_user, second_user, db, seed_periods,
    ):
        """Other user's loan: 404-redirect, no rule modification.

        Confirms the ownership check prevents cross-user mutation of a
        recurrence rule.
        """
        from app.models.recurrence_rule import RecurrenceRule  # pylint: disable=import-outside-toplevel
        from app.models.transfer_template import TransferTemplate  # pylint: disable=import-outside-toplevel

        other_loan = _create_other_loan(second_user, db.session, AcctTypeEnum.MORTGAGE)

        # Create a transfer template for the other user's loan.
        tpl = TransferTemplate(
            user_id=second_user["user"].id,
            from_account_id=second_user["account"].id,
            to_account_id=other_loan.id,
            name="Other User Payment",
            is_active=True,
        )
        db.session.add(tpl)
        db.session.commit()
        # The definition first, then the cadence onto it (plan step R-F6).
        rule = make_cadence_rule(
            tpl, MONTHLY,
            fires_on_day=1,
        )
        # Committed before the request (plan step X-i3): a query request opens
        # a transaction of its OWN, so the rule this test asserts is UNTOUCHED
        # has to exist as far as that request is concerned.
        db.session.commit()
        start_before = rule.starts_on

        # Access other user's loan as the primary user.
        resp = auth_client.get(f"/accounts/{other_loan.id}/loan")
        assert resp.status_code == 404

        # Other user's recurrence rule should be untouched.
        db.session.refresh(rule)
        assert rule.end_date is None
        assert rule.starts_on == start_before


class TestScheduleRowsResolveTheirOwnTerms:
    """Every schedule row's escrow and rate come from ITS OWN installment.

    Ruling **R-IJ** (plan step X-au-g-2b): a loan's contractual terms resolve
    on the installment they govern, never on a read date.  The amortization
    table read a single ``LoanContext.monthly_escrow``, resolved at
    ``date.today()``, and added it to all 360 rows alike -- finding **N-410**
    -- while every other tier (the genesis split, the forward plan, a
    materialised payment's live cash) already keyed on the installment.  Its
    ARM Rate column carried a second wall-clock read as a per-row fallback for
    a state no row could be in.

    The context is captured through Flask's ``template_rendered`` signal, so
    these read exactly what the route handed the template -- the whole wiring,
    route through builder -- rather than parsing 360 rows of HTML.
    """

    @staticmethod
    def _capture_schedule_context(app, auth_client, account_id):
        """Return the context GET ``/loan/schedule`` handed its template."""
        # Pylint: import-outside-toplevel -- the file-wide test convention.
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded = []

        def _record(sender, template, context, **extra):  # noqa: ARG001
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get(f"/accounts/{account_id}/loan/schedule")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"the schedule returned {response.status_code}; expected 200"
        )
        contexts = [c for t, c in recorded if t.name == "loan/schedule.html"]
        assert contexts, "loan/schedule.html did not render"
        return contexts[0], response.data.decode()

    @staticmethod
    def _mortgage_with_a_future_escrow_step(seed_user, db_session):
        """A mortgage escrowing $300/mo, stepping to $400/mo on 2027-01-01.

        Today is frozen at 2026-03-20 for this file, so the second version is
        strictly in the FUTURE: a builder resolving one figure at ``today``
        cannot see it at all, which is the shape N-410 names.

        Originated 2026-03-01 so its first installment (Apr 1) is after
        today and nothing is overdue: a planned row's escrow is what the
        fold impounded for its installment (plan step R7d-g-3), and an
        installment left unpaid past its due date leaves its escrow to be
        impounded with the next payment's -- three months' worth on the
        first row of a loan originated in January, which is the fold's
        truth about a loan behind on its payments and not the
        version-boundary property these tests grade.
        """
        acct = _create_fresh_mortgage(
            seed_user, db_session, origination_date=date(2026, 3, 1),
        )
        opening = add_escrow_line(
            db_session, acct.id, "Property Tax", Decimal("3600.00"),
            effective_date=date(2026, 3, 1),
        )
        db_session.add(EscrowComponentVersion(
            line_id=opening.line_id,
            effective_date=date(2027, 1, 1),
            annual_amount=Decimal("4800.00"),
        ))
        db_session.commit()
        return acct

    def test_each_row_carries_the_escrow_in_force_for_its_installment(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """A future-dated escrow version reaches exactly the rows it governs.

        ``$3,600/yr`` (``$300.00``/month) from origination, superseded by
        ``$4,800/yr`` (``$400.00``/month) effective 2027-01-01.  Rows due before
        that date charge the old figure and rows due on or after it the new one;
        both sets must be non-empty, or the fixture would be satisfied by a
        builder that read one date.
        """
        acct = self._mortgage_with_a_future_escrow_step(seed_user, db.session)

        context, _html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]
        assert len(escrow) == len(rows)

        before = [
            e for row, e in zip(rows, escrow)
            if row.payment_date < date(2027, 1, 1)
        ]
        after = [
            e for row, e in zip(rows, escrow)
            if row.payment_date >= date(2027, 1, 1)
        ]
        assert before and after, (
            "the schedule did not straddle the escrow version boundary"
        )
        assert set(before) == {Decimal("300.00")}
        assert set(after) == {Decimal("400.00")}

    def test_the_escrow_total_sums_the_rows_rather_than_scaling_one(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The footer's escrow total is the SUM, not one figure times the count.

        The mutation arm of the test above.  ``monthly_escrow * num_months``
        was the old total, and on this loan neither figure times the row count
        reproduces the truth -- so a builder that kept the multiplication is
        refuted whichever figure it picked.
        """
        acct = self._mortgage_with_a_future_escrow_step(seed_user, db.session)

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]
        total = context["schedule_totals"]["total_escrow"]

        assert total == sum(escrow, Decimal("0.00"))
        for scaled in (Decimal("300.00"), Decimal("400.00")):
            assert total != scaled * len(rows), (
                f"the escrow total equals ${scaled} x {len(rows)} rows, so "
                "this fixture cannot tell a sum from a multiplication"
            )
        # The column is shown because the schedule CHARGES escrow, and the
        # per-row cells really render (both figures reach the page).
        assert context["show_escrow_column"] is True
        assert "$300.00" in html and "$400.00" in html
        # And the row totals carry the row's own escrow, not a constant --
        # through ``round_money``, the boundary the producer applies, so the
        # assertion cannot pass on a total that skipped it.
        assert [
            round_money(row.payment + e + row.extra_payment)
            for row, e in zip(rows, escrow)
        ] == context["schedule_row_totals"]

    def test_a_loan_with_no_escrow_hides_the_column(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """No escrow line anywhere in the schedule means no Escrow column.

        The other side of ``show_escrow_column``: the flag is about what the
        schedule charges, so a loan charging nothing hides the column exactly
        as the old ``monthly_escrow > 0`` test did.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        assert set(context["schedule_row_escrow"]) == {Decimal("0.00")}
        assert context["show_escrow_column"] is False
        assert ">Escrow</th>" not in html

    def test_a_loan_whose_escrow_STARTS_mid_schedule_shows_the_column(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The case the OLD predicate got wrong, and the one it was changed for.

        ``show_escrow_column`` was ``monthly_escrow > 0`` -- today's figure --
        and is now ``total_escrow > 0``, what the schedule actually charges.
        The two disagree in exactly one direction that a real loan reaches: a
        loan whose FIRST escrow version is effective in the future escrows
        ``$0.00`` today, so the old predicate hid the column entirely and with
        it every dollar of escrow the schedule goes on to charge.

        Today is frozen at 2026-03-20 for this file and the only version is
        effective 2027-01-01, so ``escrow_monthly_as_of(lines, today)`` is
        ``0.00`` and the old predicate answers False.  **An adversarial review
        reverted the flag to that exact expression and every test in this file
        still passed**, which is why this fixture exists: the change's own
        stated reason was ungraded.
        """
        acct = _create_fresh_mortgage(
            seed_user, db.session, origination_date=date(2026, 1, 1),
        )
        add_escrow_line(
            db.session, acct.id, "Property Tax", Decimal("4800.00"),
            effective_date=date(2027, 1, 1),
        )
        db.session.commit()

        context, html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        escrow = context["schedule_row_escrow"]

        # The premise: nothing is escrowed TODAY, so the old predicate is False.
        assert escrow_calculator.escrow_monthly_as_of(
            loan_loaders.load_escrow_lines(acct.id), date(2026, 3, 20),
        ) == Decimal("0.00")
        # The behaviour: the column is shown, and starts at zero.
        assert context["show_escrow_column"] is True
        assert ">Escrow</th>" in html
        assert {
            e for row, e in zip(rows, escrow)
            if row.payment_date < date(2027, 1, 1)
        } == {Decimal("0.00")}
        assert {
            e for row, e in zip(rows, escrow)
            if row.payment_date >= date(2027, 1, 1)
        } == {Decimal("400.00")}

    def test_every_rendered_row_carries_its_own_rate(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """The Rate column reads each row's rate; there is no fallback to today's.

        ``build_schedule_context`` had
        ``row.interest_rate if row.interest_rate is not None else current_rate``,
        where ``current_rate`` was a ``date.today()`` rate lookup.  Every
        construction of an ``AmortizationRow`` sets ``interest_rate`` from a
        rate period's ``annual_rate``, so that branch guarded an unconstructible
        state; plan step X-au-g-2b deleted it and the route's clock read with
        it.  This pins the premise the deletion rests on -- across a rate
        BOUNDARY, so the column is genuinely varying rather than trivially
        constant.
        """
        acct = _create_loan_account(
            seed_user, db.session, AcctTypeEnum.MORTGAGE, "ARM Own Rate",
            Decimal("100000.00"), Decimal("0.05000"), 360,
            date(2024, 1, 1), 1, is_arm=True,
        )
        db.session.add(RateHistory(
            account_id=acct.id,
            effective_date=date(2027, 2, 1),
            interest_rate=Decimal("0.07000"),
        ))
        db.session.commit()

        context, _html = self._capture_schedule_context(
            app, auth_client, acct.id,
        )
        rows = context["amortization_schedule"]
        assert rows, "expected a non-empty ARM schedule"
        assert all(row.interest_rate is not None for row in rows), (
            "a rendered row carried no rate, so the deleted fallback was live"
        )
        assert context["schedule_row_rates_pct"] == [
            row.interest_rate * Decimal("100") for row in rows
        ]
        # Two rates really are rendered, so a column reading ONE date would
        # differ from this one somewhere.
        assert {row.interest_rate for row in rows} == {
            Decimal("0.05000"), Decimal("0.07000"),
        }


class TestTheLoanPageNamesAPaymentByItsInstallment:
    """Rulings R-R108 and R-R109: the page names a payment by the installment it pays.

    A payment due off the loan's contractual day pays the installment whose
    interval its due date falls in (ruling R-R104): a payment due Mar 10 on a
    loan due the 22nd pays Feb 22's, and its charge, cash price, balance and
    ledger entry already read Feb 22.  The page now names it the same way:
    the payment-history card lists it under ``Feb 2026`` (R-R108), and the
    schedule's row for it is ``#1 . Feb 22, 2026`` in every column -- its date,
    its number, the escrow it is priced with (R-R109).  The band chart does not
    name payments at all: it plots every installment, each at the balance the
    ledger holds that day (ruling R-R110, which replaced R-R109's "plots it
    there").

    Josh's worked example for R-R109, rendered through both routes: a
    ``$200,000.00`` mortgage at 6%, due the 22nd from 2026-01-22 (P&I
    ``$1,199.10``), escrowing ``$100.00`` a month rising to ``$300.00`` on
    Mar 1, and one payment due Mar 10 that moves ``$1,299.10`` -- P&I plus
    Feb 22's ``$100.00`` escrow.  Feb 22's charge is ``200000.00 * 0.06 / 12
    = 1000.00`` of interest and ``$100.00`` of escrow, so the payment splits
    interest ``1,000.00``, escrow ``100.00``, principal ``199.10``.  Before
    R-R109 the row read ``#2 . Mar 10, 2026`` with Mar 10's ``$300.00``
    escrow, a row total of ``$1,499.10`` -- ``$200.00`` more than moved.
    Today is frozen at 2026-03-20 for this file, after the payment.
    """

    @staticmethod
    def _mortgage_paid_off_day(seed_user, seed_periods, db_session):
        """The worked example's mortgage, escrow step and Mar 10 payment."""
        acct = _create_fresh_mortgage(
            seed_user, db_session, principal=Decimal("200000.00"),
            rate=Decimal("0.06000"), payment_day=22,
            origination_date=date(2026, 1, 22),
        )
        opening = add_escrow_line(
            db_session, acct.id, "Property Tax", Decimal("1200.00"),
            effective_date=date(2026, 1, 22),
        )
        db_session.add(EscrowComponentVersion(
            line_id=opening.line_id,
            effective_date=date(2026, 3, 1),
            annual_amount=Decimal("3600.00"),
        ))
        db_session.commit()
        create_settled_transfer(
            seed_user, db_session, seed_user["account"], acct,
            seed_periods[4], amount=Decimal("1299.10"),
            settled_on=date(2026, 3, 10), due_date=date(2026, 3, 10),
        )
        db_session.commit()
        return acct

    def test_the_schedule_row_is_the_installment_in_every_column(
        self, app, auth_client, seed_user, db, seed_periods,
    ):
        """``#1 . Feb 22, 2026``, escrow ``$100.00``, total ``$1,299.10`` = the cash."""
        acct = self._mortgage_paid_off_day(seed_user, seed_periods, db.session)

        context, html = (
            TestScheduleRowsResolveTheirOwnTerms._capture_schedule_context(  # pylint: disable=protected-access
                app, auth_client, acct.id,
            )
        )
        rows = context["amortization_schedule"]
        assert [row.is_confirmed for row in rows[:2]] == [True, False]
        confirmed = rows[0]
        assert (
            context["schedule_row_numbers"][0],
            confirmed.payment_date,
            confirmed.interest,
            confirmed.principal,
            context["schedule_row_escrow"][0],
            context["schedule_row_totals"][0],
        ) == (
            1, date(2026, 2, 22), Decimal("1000.00"), Decimal("199.10"),
            Decimal("100.00"), Decimal("1299.10"),
        )
        assert "Feb 22, 2026" in html
        assert "Mar 10, 2026" not in html

    def test_the_card_and_the_chart_name_the_feb_22_installment(
        self, app, auth_client, seed_user, db, seed_periods,
    ):  # pylint: disable=unused-argument
        """The card lists it under ``Feb 2026``; the chart plots every installment.

        Ruling R-R110 (amends R-R109's "the loan chart plots the payment on
        Feb 22"): the chart's months are the loan's installment dates, each
        the balance the ledger holds that day.  Feb 22: ``$200,000.00`` (the
        payment's money moves Mar 10).  Mar 22: the Mar 10 payment's
        ``$199.10`` of principal and the planned Mar 22 installment's
        ``$200.10`` (interest ``199800.90 * 0.06 / 12 = 999.00`` of its
        ``$1,199.10`` P&I) -- ``$199,600.80``.  Apr 22: ``$201.10`` more
        (interest ``998.00``) -- ``$199,399.70``.  Before R-R110 the chart read
        Feb $200,000.00 then Apr: the payment never showed and March was gone.
        """
        acct = self._mortgage_paid_off_day(seed_user, seed_periods, db.session)

        response = auth_client.get(f"/accounts/{acct.id}/loan")
        assert response.status_code == 200
        html = response.data.decode()
        assert "Ledger-confirmed through Feb 2026" in html
        assert "<td>Feb 2026</td>" in html
        assert "<td>Mar 2026</td>" not in html
        band = _parse_band_chart(html)
        assert list(zip(band["labels"][:3], band["balance"][:3])) == [
            ("Feb 2026", 200000.0), ("Mar 2026", 199600.8),
            ("Apr 2026", 199399.7),
        ]
