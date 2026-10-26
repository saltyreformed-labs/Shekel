"""Grid route tests: interest accrual, the kind gate, and the range selector and plan window
derived from the owner's cadence.
"""

import re
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.user import UserSettings
from app.models.ref import AccountType
from app.services.cash_flow_set import CashFlowSet
from app.services import template_amount_service
from app.services import account_service, balance_at
from app.services.pay_rhythm import FixedDays
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    APP_PACKAGE_DIR,
    create_hysa_account,
    current_pay_period,
    generate_row_of,
    make_income_template,
)


class TestGridInterestAccrual:
    """The grid accrues interest + shows an Interest row for an INTEREST account.

    The kind-correct-grid feature: when the grid is pointed at an
    interest-bearing account (HYSA / Money Market / CD / HSA) the projected
    balance accrues interest and a read-only "Interest" row explains the part
    of the balance change the transactions do not.  Every other account kind
    keeps the cash-flow view with no accrual row.
    """

    def test_interest_account_shows_accrual_row_and_accrued_balance(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An HYSA grid account renders the Interest row + interest-accrued balance.

        Seeds a $100,000 HYSA at 5% APY with no transactions: the cash-flow
        balance would sit flat at the $100,000 anchor, so any balance above it
        is interest the grid now accrues.  The route must render the seam's
        interest-accrued current-period balance and the per-period Interest
        row figure (cross-checked against the seam so the numbers are not
        hand-guessed).
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        bctx = BalanceContext.build(seed_user["user"].id)
        user_id = seed_user["user"].id
        current = current_pay_period(user_id)
        # Seam truth the route must render (current is the leftmost visible col).
        view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)
        accrued = view.columns[current.id].balance
        interest = view.columns[current.id].accrual

        resp = auth_client.get(f"/grid?account_id={hysa.id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        # The read-only accrual row renders for an interest grid account.
        assert "modelled-accrual-row" in html
        # Interest accrues: the current-period balance exceeds the $100,000
        # anchor, and the grid renders exactly the seam's accrued figure.
        assert accrued > Decimal("100000.00")
        assert f"${accrued:,.0f}" in html
        # The per-period interest is positive and rendered in the accrual row,
        # to the CENT and with its gain sign (developer ruling 2026-07-27): the
        # row's precision differs from the balance row's above it, so asserting
        # it at whole dollars would pass on a substring of the cents rendering
        # and stop grading the thing that changed.
        assert interest > Decimal("0.00")
        row = html[html.index("modelled-accrual-row"):]
        row = row[:row.index("</tr>")]
        assert f"+${interest:,.2f}" in row

    def test_plain_account_has_no_accrual_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The default checking (PLAIN) grid shows no Interest accrual row."""
        resp = auth_client.get("/grid")
        assert resp.status_code == 200
        assert b"modelled-accrual-row" not in resp.data

    def test_interest_account_balance_row_refresh_shows_accrual_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The balance-row HTMX refresh renders the Interest row for an HYSA.

        The self-refresh endpoint must reproduce the full render's accrual row
        (it reads the same seam view the full render does), so a mark-paid
        that fires ``balanceChanged`` keeps the Interest row and the accrued
        balance current instead of reverting to the cash-flow view.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        resp = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "modelled-accrual-row" in html
        assert "Projected End Balance" in html

    def test_mobile_summary_refresh_shows_interest_for_hysa(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile This-Period summary refresh shows the Interest bar (HYSA).

        The self-refreshing mobile summary endpoint must reproduce the
        interest accrual (it reads the same seam view the full render does),
        so a mobile mark-paid that fires ``mobileCardSettled`` keeps the
        Interest bar instead of reverting to the cash-flow view.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("100000.00"),
        )
        current = current_pay_period(seed_user["user"].id)
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}"
            f"&account_id={hysa.id}",
        )
        assert resp.status_code == 200
        assert b"modelled-accrual-row" in resp.data

    def test_mobile_summary_refresh_no_interest_for_plain(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The mobile This-Period summary refresh shows no Interest bar (PLAIN)."""
        current = current_pay_period(seed_user["user"].id)
        resp = auth_client.get(
            f"/grid/this-period-summary?period_id={current.id}",
        )
        assert resp.status_code == 200
        assert b"modelled-accrual-row" not in resp.data

    def test_refresh_uses_the_same_income_basis_as_the_full_render(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The balance-row refresh and the full render fold ONE income basis.

        Ruling R-Q retired the caller's choice: the SEAM owns the basis, so an
        interest account cannot be projected on a different income figure by a
        caller that forgot to thread one.  Before it, ``grid_balance_view``
        fell back to the STORED amount on a bare ``None`` for the interest
        path, so a refresh after a mark-paid could revert the balance to the
        stored figure while the full page showed the live one -- a flicker with
        no argument to fix it at the call site.

        **The divergent figure is a real DATA state since plan step
        balance:X-au-d**, where it was a monkeypatched read-time repair: the
        income row DECLARES a definition whose price series states ``$5,000``
        and stores no figure itself, so a surface reading the column would
        contribute ``None`` and one asking the amount model contributes
        ``$5,000``.  Both surfaces must render the same accrued balance, with
        no override passed anywhere -- there is none to pass.
        """
        hysa = create_hysa_account(
            seed_user, db.session, seed_periods_today[0], Decimal("10000.00"),
        )
        bctx = BalanceContext.build(seed_user["user"].id)
        user_id = seed_user["user"].id
        period = seed_periods_today[2]
        # NOT the graded figure: the definition is built stating $7.77 (its
        # scalar, and a version dated TODAY), and the $5,000 the assertions
        # expect is a version dated at the row's own paycheck below -- so a
        # producer reading the scalar instead of the series on the row's due
        # date answers a number no assertion here expects.  A first version
        # set both to $5,000 and an adversarial review of plan step X-au-d
        # showed that makes ``_stated_amount``'s empty-series refusal
        # invisible.  The row is the engine's, on the definition's HYSA (plan
        # step balance:X-cf-4); its due date is the paycheck's start, which
        # is where the $5,000 version is in force.
        template = make_income_template(
            db.session, seed_user, amount="7.77", name="Paycheck",
            category_key=next(iter(seed_user["categories"])), account=hysa,
        )
        template_amount_service.set_amount(
            template, Decimal("5000.00"), effective_on=period.start_date,
        )
        income = generate_row_of(template, period)
        db.session.commit()
        assert income.estimated_amount is None
        assert template.default_amount == Decimal("7.77"), (
            "the scalar must still disagree with the series on the row's day, "
            "or the control cannot see a producer reading the scalar"
        )
        current = current_pay_period(user_id)
        # The seam builds the live map itself (ruling R-Q), so no override is
        # threaded here or by the route -- this IS the live figure.
        live_view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)
        accrued_live = live_view.columns[current.id].balance
        # Sanity: the definition's $5,000 is reflected -- the balance clears
        # the $10,000 anchor + the deposit.
        assert accrued_live > Decimal("15000.00")

        full = auth_client.get(f"/grid?account_id={hysa.id}").data.decode()
        refresh = auth_client.get(
            f"/grid/balance-row?periods=6&offset=0&account_id={hysa.id}",
        ).data.decode()
        # Both surfaces render the live-income accrued balance -- no flicker.
        assert f"${accrued_live:,.0f}" in full
        assert f"${accrued_live:,.0f}" in refresh


class TestGridKindGate:
    """The D4 / A1 gate: the grid never renders an AMORTIZING account.

    Finding B-3 (live): with ``?account_id=<loan>`` the grid rendered
    the real Mortgage's balance RISING by the full PITI each month --
    the cash-flow producer reads payment transfers INTO the loan as
    inflows.  The resolver now treats a loan override exactly like a
    missing account and falls back.
    """

    @staticmethod
    def _mortgage(seed_user):
        """Create a bare active Mortgage-type account (kind is the gate)."""
        mortgage_type = db.session.query(AccountType).filter_by(
            name="Mortgage",
        ).one()
        loan = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=mortgage_type.id,
                name="Gate Mortgage",
                anchor_balance=Decimal("0"),
            ),
        )
        db.session.commit()
        return loan

    def test_account_id_override_with_loan_falls_back_to_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET /grid?account_id=<loan> renders the checking fallback."""
        with app.app_context():
            loan = self._mortgage(seed_user)

            response = auth_client.get(f"/grid?account_id={loan.id}")

            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Gate Mortgage Balance" not in response.data

    def test_default_grid_setting_with_loan_falls_back_to_checking(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A saved loan default (pre-gate data) no longer re-points the grid."""
        with app.app_context():
            loan = self._mortgage(seed_user)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.default_grid_account_id = loan.id
            db.session.commit()

            response = auth_client.get("/grid")

            assert response.status_code == 200
            assert b"Checking Balance" in response.data
            assert b"Gate Mortgage Balance" not in response.data


class TestTheRangeSelectorIsDerivedFromTheOwnersCadence:
    """The 6M / 1Y / 2Y buttons name a SPAN and resolve it per owner.

    Recurrence plan step **R-F17**, ledger row **F-17**, ruling **R-R31**.
    ``grid/grid.html`` held the selector as a Jinja literal --
    ``("6M", 13, "6 months"), ("1Y", 26, "1 year"), ("2Y", 52, "2 years")`` --
    which is ``months x 26 / 12`` and so told the truth for an owner paid every
    fourteen days and for nobody else: a weekly owner pressing ``1Y`` got six
    months of columns, a monthly one got two years, each under a button naming
    a year.  The ``3P`` / ``6P`` buttons name a COUNT of paychecks and are
    correct for every owner, so they are untouched.

    The selector had NO test at all before this step, which is how the defect
    stayed on the highest-traffic screen in the application.
    """

    def test_biweekly_is_the_literal_the_template_held(self):
        """The exact five-row table ``grid.html`` carried, in order.

        The regression pin behind the step's "no figure moved" claim on the
        developer's own cadence: the derived table and the deleted literal are
        the same list, so the cutover changed nothing they see.
        """
        from app.routes.grid.page import _range_options
        from app.services.pay_calendar import PayCadence

        assert _range_options(PayCadence(FixedDays(14))) == [
            ("3P", 3, "3 pay periods"),
            ("6P", 6, "6 pay periods"),
            ("6M", 13, "6 months"),
            ("1Y", 26, "1 year"),
            ("2Y", 52, "2 years"),
        ]

    def test_a_weekly_owner_gets_twice_the_columns_for_the_same_span(self):
        """``1Y`` is 52 columns weekly where the literal offered 26.

        26 weekly paychecks is six months.  The paycheck-count buttons are
        unchanged, which is the contrast that shows the derivation applies to
        the SPAN buttons alone.
        """
        from app.routes.grid.page import _range_options
        from app.services.pay_calendar import PayCadence

        assert _range_options(PayCadence(FixedDays(7))) == [
            ("3P", 3, "3 pay periods"),
            ("6P", 6, "6 pay periods"),
            ("6M", 26, "6 months"),
            ("1Y", 52, "1 year"),
            ("2Y", 104, "2 years"),
        ]

    def test_a_window_already_offered_is_not_offered_twice(self):
        """At a monthly cadence "6 months" IS "6 pay periods", so it is offered once.

        **A defect this step CREATED, and its adversarial review caught.**  The
        deleted literal was ``3 / 6 / 13 / 26 / 52``, which can never collide;
        deriving the span buttons makes one land on the fixed 3 or 6 of a
        paycheck button at 64 of the 365 legal cadences, the shortest being 28
        days -- so a monthly-paid owner got ``6P`` and ``6M`` both linking to
        ``?periods=6``, and since the template marks a button active with
        ``num_periods == count``, BOTH lit up.

        The paycheck labels are exact and cadence-independent, so the DERIVED
        label yields: the WINDOW is still offered, under the label that was
        already true.
        """
        from app.routes.grid.page import _range_options
        from app.services.pay_calendar import PayCadence

        assert _range_options(PayCadence(FixedDays(30))) == [
            ("3P", 3, "3 pay periods"),
            ("6P", 6, "6 pay periods"),
            ("1Y", 12, "1 year"),
            ("2Y", 24, "2 years"),
        ]

    def test_no_cadence_offers_one_window_under_two_labels(self):
        """Swept over the WHOLE domain: every offered count is distinct.

        The single case above names the shape; this is the axis the defect
        actually lived on, and no case varied it until the review said so
        (``feedback_oracle_coverage_holes``).
        """
        from app.routes.grid.page import _range_options
        from app.services.pay_calendar import PayCadence

        for cadence_days in range(1, 366):
            counts = [
                count for _, count, _
                in _range_options(PayCadence(FixedDays(cadence_days)))
            ]

            assert len(counts) == len(set(counts)), (
                f"cadence {cadence_days} offers one window twice: {counts}"
            )

    def test_a_span_no_paycheck_reaches_is_not_offered_at_all(self):
        """An annually paid owner is offered no ``6M`` button.

        Ruling **R-R31**, and here it is also what keeps the control working
        rather than merely honest: the derived count would be 0, and a ``6M``
        button linking to ``?periods=0`` renders the empty-schedule page for a
        user whose schedule is not empty.
        """
        from app.routes.grid.page import _range_options
        from app.services.pay_calendar import PayCadence

        labels = [
            label for label, _, _
            in _range_options(PayCadence(FixedDays(365)))
        ]

        assert labels == ["3P", "6P", "1Y", "2Y"]

    def test_the_template_no_longer_names_a_period_count_for_a_span(self):
        """STATIC guard: no hardcoded ``("6M", 13, ...)`` back in the template.

        A Jinja literal is invisible to every Python gate in this repository,
        which is how this one survived plan step R7a-2a's sweep of the
        hardcoded 26s.  The guard reads the template source directly so a
        re-introduction fails here rather than on someone's screen.
        """
        # Found from the app package's own location, never from this test's
        # place in the tree.
        source = (
            APP_PACKAGE_DIR / "templates" / "grid" / "grid.html"
        ).read_text(encoding="utf-8")

        # **The POSITIVE fact, not an enumeration of negative ones.**  The
        # first draft of this guard asserted `"set range_options" not in
        # source` and a regex for a double-quoted `"6M", 13` pair, and BOTH
        # arms passed on the real defect: an adversarial review re-inlined the
        # literal single-quoted, directly in the `{% for %}` expression, and
        # the guard stayed green while the selector was fully hardcoded again.
        # Jinja accepts either quote style and this template's house style is
        # single quotes, so a guard that enumerates spellings is one spelling
        # from useless.  There is exactly one way to iterate the route's
        # resolved options, and asserting it cannot be satisfied by any
        # re-inlined literal in any quote style, in a `set` or in a `for`.
        assert "{% for label, count, tooltip in range_options %}" in source, (
            "grid.html no longer iterates the route's resolved range_options; "
            "the counts are the OWNER's paychecks and only the route can "
            "derive them"
        )

    def test_the_rendered_page_carries_the_derived_hrefs(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
    ):
        """End to end: a WEEKLY owner's ``1Y`` button links to ``periods=52``.

        The route resolves the buttons and the template renders them, so this
        is the one case that proves the context key actually reaches the page
        -- a Jinja loop over an undefined name renders nothing at all and
        would drop the whole selector SILENTLY.
        """
        with app.app_context():
            seed_schedule_at_cadence(cadence_days=7, num_periods=120)

            html = auth_client.get("/grid").data.decode()

        # **Scoped to the button group, because the page-wide scrape was
        # blind.**  The first draft matched `periods=(\d+)&offset=0` across the
        # whole document, which also matches the show-all toggle -- and that
        # link carries `periods=6`, the `grid_default_periods` default -- so
        # deleting the `6P` button entirely left this assertion passing.  An
        # adversarial review measured that.
        group = html.split('class="btn-group period-btn-group"', 1)[1]
        group = group.split("</div>", 1)[0]
        offered = {
            int(count) for count in
            re.findall(r"periods=(\d+)&(?:amp;)?offset=0", group)
        }

        # 3P / 6P are absolute; 6M / 1Y / 2Y are 26 / 52 / 104 weekly.  The
        # biweekly 13 / 26 / 52 would be a passing SUBSET of nothing here: 26
        # and 52 appear under different labels, so the set equality is what
        # makes this case distinguish the derivation from the old literal.
        assert offered == {3, 6, 26, 52, 104}, (
            f"the weekly owner was offered {sorted(offered)} columns"
        )
        assert 'title="1 year"' in html
        assert 'title="6 months"' in html
        assert 'title="6 pay periods"' in html


class TestTheGridRefusesBeforeItReadsACadence:
    """`/grid` answers a repair page rather than a 500, for both no-rhythm owners.

    Both grid reads of the owner's cadence are UNGUARDED --
    ``_range_options(ctx.balance_ctx.calendar().cadence)`` in the render kwargs
    and ``calendar.cadence.paychecks_within(...)`` in ``_build_plan_view``.

    **They used to be safe only by ORDERING**, and plan step
    ``pay_calendar:C4-d`` (ruling **R-PC45**) removed the thing that ordering
    was standing between.  ``PayCalendar.cadence`` REFUSED a calendar holding
    no cadence, and these reads were reached only because
    ``_resolve_visible_window`` had already returned ``None`` and ``index`` had
    rendered ``no_periods.html``: a correctness property held up by the order
    of two statements, which is what this class's own name records.  A calendar
    cannot carry an absent cadence now, so those reads cannot raise whatever
    order they run in.

    **What the owner sees split in two, and each half is a case below.**  An
    owner with a schedule row and no COVERING paycheck still gets
    ``no_periods.html`` from ``index``'s early return -- unchanged, and it is
    the state that page's copy is for.  An owner with no schedule ROW is
    refused at ``calendar_for`` and gets the application-wide
    ``errors/no_pay_calendar.html``, which carries the same repair link.  That
    is one answer for one state instead of the grid improvising its own, and it
    leaves ``no_periods.html`` naming a single state -- its copy currently
    reads "No pay periods have been generated yet, OR there is no period
    covering today's date", which is ``balance:X-x4``'s subject.

    **Nothing pinned any of it.** The account page and the savings service each
    got an explicit zero-payday case in plan step R-F17 and the grid did not,
    which an adversarial test review measured: the existing
    ``test_grid_shows_no_periods_page`` uses bare ``seed_user``, and that
    fixture carries a bootstrap pay period.
    """

    def test_an_owner_with_no_SCHEDULE_ROW_gets_the_repair_page(
        self, app, auth_client, seed_user, db,
    ):
        """Zero pay periods, zero schedule rows: 200 and the repair page.

        **This case changed its ANSWER at plan step ``pay_calendar:C4-d``**
        (ruling **R-PC45**) and is kept rather than deleted, because the change
        is the decision.  It asserted ``no_periods.html`` -- the grid's own
        empty state -- which the route reached because ``calendar_for``
        answered this owner an empty calendar carrying no cadence.  It now
        asserts ``errors/no_pay_calendar.html``, the application-wide answer,
        because ``calendar_for`` refuses them.

        **What is NOT lost is what the assertion checks for.**  Both pages are
        a 200 rather than a 500, both carry ``/pay-periods/generate``, and the
        htmx-fragment behaviour the handler defines is graded in
        ``test_pay_calendar_error_handler.py``.  What IS gained is that the
        grid stops being one of three different answers to one state, and that
        this class's ordering property -- see its docstring -- has no subject.

        The ``pytest.raises`` is kept and moved onto ``calendar_for`` itself,
        so this case still cannot pass vacuously: it fails if the loader ever
        stops refusing, rather than reporting a repair page it did not earn.
        """
        from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
            PayCalendarError,
            calendar_for,
        )
        from tests._test_helpers import strip_owner_schedule  # pylint: disable=import-outside-toplevel
        with app.app_context():
            user_id = seed_user["user"].id
            # **The owner must hold no stored cadence, and since plan step
            # ``pay_calendar:C4-b-1`` the fixture writes one** -- the seeded
            # opening payday comes from ``pay_period_write.record_paydays``,
            # which upserts the ``budget.pay_schedule`` row in the same call.
            # So this state is CONSTRUCTED here rather than inherited from a
            # fixture that happened to supply it, which is the stronger form:
            # the assertion that used to stand here could only report that the
            # fixture had changed.  It is still a real owner -- somebody who
            # has never generated a schedule holds neither row -- and the
            # periods go FIRST, which is the order the foreign key plan step
            # ``pay_calendar:C4-b-2`` adds makes the only non-cascading one.
            strip_owner_schedule(db.session, user_id)
            db.session.commit()

            with pytest.raises(PayCalendarError):
                _ = calendar_for(user_id)

            response = auth_client.get("/grid")

            assert response.status_code == 200
            assert b"Pay Calendar Unavailable" in response.data
            assert b"/pay-periods/generate" in response.data


class TestThePlanWindowIsDerivedFromTheOwnersCadence:
    """The mobile Plan tab looks forward a SPAN, not a period count.

    ``PLAN_WINDOW_PERIODS = 13`` was a count whose own comment asserted it
    matched the desktop ``6M`` button.  Recurrence plan step **R-F17** made
    that button derive from the owner's cadence, so the count had to derive
    with it or the stated agreement would hold only at 14 days.  The window's
    WIDTH had no test before this step -- every Plan case renders the partial
    with a hand-supplied ``plan_periods`` list -- so the route's own arithmetic
    was uncovered.
    """

    @staticmethod
    def _plan_periods(app, auth_client):
        """Return the ``plan_periods`` the grid route handed the template."""
        from flask import template_rendered  # pylint: disable=import-outside-toplevel

        recorded: list[tuple] = []

        def _record(sender, template, context, **extra):
            recorded.append((template, context))

        template_rendered.connect(_record, app)
        try:
            response = auth_client.get("/grid")
        finally:
            template_rendered.disconnect(_record, app)
        assert response.status_code == 200, (
            f"GET /grid returned {response.status_code}; expected 200"
        )
        grid = [c for t, c in recorded if t.name == "grid/grid.html"]
        assert grid, (
            "GET /grid did not render grid/grid.html; templates rendered: "
            f"{[t.name for t, _ in recorded]!r}"
        )
        return grid[0]["plan_periods"]

    def test_biweekly_is_the_thirteen_periods_the_constant_held(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
    ):
        """13 columns -- what ``PLAN_WINDOW_PERIODS`` produced, unchanged."""
        with app.app_context():
            seed_schedule_at_cadence(cadence_days=14, num_periods=40)

            assert len(self._plan_periods(app, auth_client)) == 13

    def test_a_weekly_owner_sees_the_same_HALF_YEAR_in_more_columns(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
    ):
        """26 columns weekly, where the fixed 13 covered three months.

        The window is the same span of TIME for both owners; what changes is
        how many paychecks that span holds.
        """
        with app.app_context():
            seed_schedule_at_cadence(cadence_days=7, num_periods=120)

            assert len(self._plan_periods(app, auth_client)) == 26

    def test_an_owner_whose_paycheck_outlasts_the_span_sees_that_paycheck(
        self, app, auth_client, seed_user, seed_schedule_at_cadence,
    ):
        """A 300-day cadence reaches no further paycheck, so Plan shows one.

        The tab must render something, so this is where the honest zero
        ``paychecks_within`` answers is turned into a real window rather than
        an empty one: the paycheck the owner is currently IN already spans the
        whole of the next six months, so it IS the answer.  The desktop
        selector omits its ``6M`` button for the same owner, because a button
        is optional where a tab is not.
        """
        with app.app_context():
            # One period of history, not the default four.  *The reason was
            # that the writer is forward-only and four 300-day periods back
            # starts before ``seed_user``'s own opening payday; plan step
            # ``pay_calendar:C4-b-1`` moved this fixture onto the reset door,
            # which retires every surviving payday in the same call, so that
            # bound is gone.*  What replaces it is the BOOKS: a calendar may
            # not open at or before the day the seeded account's books open
            # (ruling ``balance:R-HG``), and four 300-day periods back is about
            # three and a half years -- well before the 2024 books -- which
            # ``_test_helpers.rebuild_calendar`` now refuses outright.  One
            # period back keeps this case inside that bound.  *The day itself
            # is not named because it moves with the wall clock: the fixture
            # derives it from ``display_today()``.*
            seed_schedule_at_cadence(
                cadence_days=300, num_periods=6, periods_before=1,
            )

            plan_periods = self._plan_periods(app, auth_client)

            assert len(plan_periods) == 1
