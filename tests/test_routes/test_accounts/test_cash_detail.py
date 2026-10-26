"""Account route tests: the cash detail page's context, its redirect stubs, its wrong-type matrix
and its coverage.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.extensions import db
from app.utils.dates import display_today
from tests._test_helpers import (
    all_periods,
    current_pay_period,
    derived_span,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    strip_owner_schedule,
)
from app.models.interest_params import InterestParams
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType, TransactionType
from app.services import account_service, cash_ledger

from ._helpers import _capture_cash_detail_context


class TestCashDetailContext:
    """The merged cash-detail route's context contract (Fable 5 rebuild).

    Asserts the route hands the template the exact figures the rebuild
    specifies -- the hero balance, the horizon chip rows with Decimal
    deltas, the interest-next-year health figure, the anchored-as-of event
    date, and the Chart.js series -- read straight from the render context
    so the assertions are template-presentation independent.
    """

    def _checking_with_income(self, seed_user, num_periods=27, cadence_days=14):
        """Create a checking account with +$500/period net income.

        Anchor $5,000 at ``periods[0]`` (today), then a $2,000 income and a
        $1,500 expense in every post-anchor period -- the same shape as
        ``test_checking_detail_projection_values_are_correct`` so the
        balances are the hand-computed 5000 + n*500.  Returns
        ``(account, periods)``.

        ``cadence_days`` defaults to the biweekly rhythm every case here used
        before recurrence plan step **R-F17**; the horizon cases pass another
        one, because at 14 days the derived offsets and the hardcoded 6 / 13 /
        26 they replaced are the same numbers and no case could tell them
        apart.
        """
        periods = record_paydays_across_a_hole(
            user_id=seed_user["user"].id,
            first_payday=display_today(),
            num_periods=num_periods, rhythm=rhythm_of(cadence_days),
        )
        checking_type = db.session.query(AccountType).filter_by(
            name="Checking",
        ).one()
        acct = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=checking_type.id,
                name="Ctx Checking",
                anchor_balance=Decimal("5000.00"),
            ),
        )
        db.session.add(acct)
        db.session.flush()

        income_type = db.session.query(TransactionType).filter_by(
            name="Income",
        ).one()
        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense",
        ).one()
        category = seed_user["categories"]["Salary"]
        for period in periods[1:]:
            one_off_row_of(
                period,
                name="Paycheck",
                amount=Decimal("2000.00"),
                user_id=period.user_id,
                account_id=acct.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type.id,
                category_id=category.id,
            )
            one_off_row_of(
                period,
                name="Bills",
                amount=Decimal("1500.00"),
                user_id=period.user_id,
                account_id=acct.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=expense_type.id,
                category_id=category.id,
            )
        db.session.commit()
        return acct, periods

    def test_horizons_carry_decimal_deltas(self, app, auth_client, seed_user):
        """The horizon chip rows carry the projected value and the Decimal delta.

        With anchor $5,000 at the current period (period 0, no transactions
        there) and +$500 net per post-anchor period, the hero balance is
        $5,000 and the horizons are (value, delta = value - current):

          3 months  -> period  6: 5000 + 6*500  = 8000.00,  delta 3000.00
          6 months  -> period 13: 5000 + 13*500 = 11500.00, delta 6500.00
          1 year    -> period 26: 5000 + 26*500 = 18000.00, delta 13000.00
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=27)
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["current_balance"] == Decimal("5000.00")
            triples = [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ]
            assert triples == [
                ("3 months", Decimal("8000.00"), Decimal("3000.00")),
                ("6 months", Decimal("11500.00"), Decimal("6500.00")),
                ("1 year", Decimal("18000.00"), Decimal("13000.00")),
            ]

    def test_the_horizons_follow_the_owners_cadence(
        self, app, auth_client, seed_user,
    ):
        """A WEEKLY owner reads 13 / 26 / 52 periods out, not 6 / 13 / 26.

        Recurrence plan step **R-F17**, ledger row **F-17**.  Identical
        account shape to ``test_horizons_carry_decimal_deltas`` above --
        $5,000 anchored at the current period, +$500 net per period after it
        -- so the two cases differ in the owner's cadence and in nothing else,
        and the dollars are the same arithmetic:

          3 months  -> period 13: 5000 + 13*500 = 11500.00, delta  6500.00
          6 months  -> period 26: 5000 + 26*500 = 18000.00, delta 13000.00
          1 year    -> period 52: 5000 + 52*500 = 31000.00, delta 26000.00

        Before the derivation this owner's "1 year" chip read period 26 --
        $18,000.00, their SIX-month balance, under a label saying a year.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(
                seed_user, num_periods=53, cadence_days=7,
            )
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["current_balance"] == Decimal("5000.00")
            assert [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ] == [
                ("3 months", Decimal("11500.00"), Decimal("6500.00")),
                ("6 months", Decimal("18000.00"), Decimal("13000.00")),
                ("1 year", Decimal("31000.00"), Decimal("26000.00")),
            ]

    def test_a_horizon_no_paycheck_reaches_is_not_shown_at_all(
        self, app, auth_client, seed_user,
    ):
        """An owner paid every 300 days is shown "1 year" and nothing shorter.

        Ruling **R-R31**: no paycheck arrives inside three months, and the pay
        period is the finest forward resolution this page has -- so there is
        no column to value and the chip is absent rather than repeating the
        hero's own figure under a label naming a shorter span.  The "1 year"
        chip survives, one period out: $5,000 + $500 = $5,500.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(
                seed_user, num_periods=3, cadence_days=300,
            )
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert [
                (h["label"], h["value"], h["delta"])
                for h in context["horizons"]
            ] == [("1 year", Decimal("5500.00"), Decimal("500.00"))]

    def test_an_owner_with_no_paydays_gets_the_page_and_no_chips(
        self, app, auth_client, seed_user, db,
    ):
        """No PAYDAYS: 200 with empty chips, not a 500.

        **The state that makes the no-current-period return load-bearing**, and
        it is measured rather than assumed: an account no longer carries an
        anchor PERIOD (rulings R-EH / R-EO deleted both columns), so an owner
        can hold accounts and no pay periods.

        Recurrence plan step **R-F17** made both forward figures on this page
        functions of the owner's cadence.  ``_build_horizons`` reads it past
        its own ``None`` return and the interest chip reads it inside its
        conditional expression, which is what keeps this case answering.

        **The owner keeps their ``budget.pay_schedule`` row since plan step
        ``pay_calendar:C4-d``** (ruling **R-PC45**), and the split is the step.
        This case deleted the row too, so the ORDERING of the cadence read was
        what stood between it and a 500 --
        :attr:`app.services.pay_calendar.PayCalendar.cadence` refused a
        calendar carrying none.  That property is total now and that owner has
        no calendar at all: they are refused at ``calendar_for`` and get
        ``errors/no_pay_calendar.html``, which is
        :meth:`test_an_owner_with_no_schedule_row_gets_the_repair_page` below.
        What is left here is the state the chips' guards are actually for --
        paydays gone, rhythm stated -- and those guards are unchanged, because
        ``_interest_next_year`` dereferences ``current_period.period_index``
        and a lapsed schedule reaches it too.

        **The two conjuncts of the interest chip's guard are covered by
        SEPARATE cases, and a first draft covered only one.**  That draft
        asserted both on the seeded Checking account, where ``is_interest and
        current_period is not None`` short-circuits on the FIRST conjunct -- so
        ``interest_next_year is None`` was satisfied by the account kind alone
        and the second conjunct was never exercised.  An adversarial review
        proved it by deleting that conjunct: all 268 cases in this file still
        passed.

        **What THIS case grades, stated narrowly because an adversarial review
        of plan step ``pay_calendar:C4-d`` measured the wider claim false.**  It
        used to be the half where reading the cadence would RAISE, and it was
        that raise the assertions rode on.  With the schedule row kept, both
        assertions are over-determined: deleting ``or current_period is None``
        from ``_build_horizons`` leaves this green (a zero-period owner has an
        empty window anyway), and ``interest_next_year is None`` is satisfied
        by ``is_interest`` being False on the seeded Checking account.  What
        survives is worth keeping and is ALL it claims: an owner with a
        schedule row and no paydays gets a 200 with empty forward figures
        rather than a 500.  The CURRENT-PERIOD conjunct is graded by the
        lapsed-schedule case below, on an interest account, where it is not
        short-circuited.
        """
        with app.app_context():
            uid = seed_user["user"].id
            # The paydays go and the ``budget.pay_schedule`` row STAYS: the
            # state is CONSTRUCTED here rather than inherited from a fixture
            # that happened to supply it, which is the stronger form -- the
            # assertion that used to stand here could only report that the
            # fixture had changed.  ``seed_user``'s opening payday comes from
            # ``pay_period_write.record_paydays``, which upserts the schedule
            # row in the same call (plan step ``pay_calendar:C4-b-1``), so
            # deleting the periods alone leaves exactly the owner this case
            # wants: a stated rhythm and nothing recorded under it.
            db.session.query(PayPeriod).filter_by(user_id=uid).delete()
            db.session.commit()

            context = _capture_cash_detail_context(
                app, auth_client, seed_user["account"].id,
            )

            assert context["current_period"] is None
            assert context["horizons"] == []
            assert context["interest_next_year"] is None

    def test_an_owner_with_no_schedule_row_gets_the_repair_page(
        self, app, auth_client, seed_user, db,
    ):
        """The other half of "no paydays", and it is the repair page now.

        **This case is the ANSWER that plan step ``pay_calendar:C4-d`` (ruling
        R-PC45) moved**, split out of the one above rather than deleted with
        it.  An owner with no ``budget.pay_schedule`` row -- the companion's
        shape, production's user 2 -- used to reach this page and render it
        with three blank chips, because ``calendar_for`` handed them an empty
        calendar carrying no cadence and each per-period feature guarded
        separately.  They are refused at ``calendar_for`` now and get
        ``errors/no_pay_calendar.html``, which is the one answer the
        application gives that state and which carries the repair link.

        Still a 200 rather than a 500: the ``PayCalendarError`` handler renders
        the page.  ``@require_owner`` 404s a companion before any of this, so
        no live page changes.
        """
        with app.app_context():
            uid = seed_user["user"].id
            # Children FIRST: ``fk_pay_periods_schedule`` and
            # ``fk_pay_eras_schedule`` are ON DELETE RESTRICT, so the parent
            # cannot go under live children; the helper states the order once.
            strip_owner_schedule(db.session, uid)
            db.session.commit()

        # The SAME url ``_capture_cash_detail_context`` drives, because this
        # case is about that page's answer: a hand-written path is how a route
        # test comes to grade a 405 or a 404 from the url map and read it as
        # the refusal under test.
        response = auth_client.get(
            f"/accounts/{seed_user['account'].id}/details",
        )

        assert response.status_code == 200
        assert b"Pay Calendar Unavailable" in response.data
        assert b"/pay-periods/generate" in response.data

        # **The link is FOLLOWED, because offering a repair and being a repair
        # are different claims** -- and an adversarial review of plan step
        # ``pay_calendar:C4-d`` measured them apart.  ``generate_form``
        # redirects into the pay-periods settings section, which read
        # ``calendar_for`` unconditionally, so the recovery page's own repair
        # answered the recovery page again: a loop, for the one owner the page
        # is written for.  Asserting the link's PRESENCE cannot see that.
        repair = auth_client.get("/pay-periods/generate", follow_redirects=True)

        assert repair.status_code == 200
        assert b"Pay Calendar Unavailable" not in repair.data
        assert b'name="cadence_days"' in repair.data

    def test_an_interest_account_on_a_lapsed_schedule_still_renders(
        self, app, auth_client, seed_user, db,
    ):
        """No paycheck covers TODAY, on an account that DOES show the chip.

        The other half of the interest chip's guard, and the ordinary one: the
        owner has paydays -- so their cadence is perfectly readable -- but the
        schedule has lapsed and none of them covers the read pass's day.
        ``_interest_next_year`` dereferences ``current_period.period_index``,
        so without the ``current_period is not None`` conjunct this is an
        ``AttributeError`` and a 500 on an interest-bearing account whose owner
        simply has not extended their calendar.

        ``seed_user``'s only period is its 2024 bootstrap, and the HYSA is
        anchored INTO it -- ``AccountSpec.observed_on`` takes a past day by
        design, which is what lets an account exist on a schedule that no
        longer reaches today.
        """
        with app.app_context():
            uid = seed_user["user"].id
            bootstrap = (
                db.session.query(PayPeriod).filter_by(user_id=uid)
                .order_by(PayPeriod.start_date).first()
            )
            assert bootstrap.start_date < display_today(), (
                "this case needs a schedule that no longer reaches today"
            )
            hysa_type = db.session.query(AccountType).filter_by(
                name="HYSA",
            ).one()
            hysa = account_service.create_account(
                account_service.AccountSpec(
                    user_id=uid,
                    account_type_id=hysa_type.id,
                    name="Lapsed HYSA",
                    anchor_balance=Decimal("2500.00"),
                    observed_on=bootstrap.start_date,
                ),
            )
            db.session.add(hysa)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=hysa.id, apy=Decimal("0.04000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, hysa.id)

            # is_interest is TRUE, so the guard's first conjunct passes and the
            # SECOND is what keeps the page at 200.
            assert context["is_interest"] is True
            assert context["current_period"] is None
            assert context["interest_next_year"] is None
            assert context["horizons"] == []

    def test_chart_json_structure_and_current_index(
        self, app, auth_client, seed_user,
    ):
        """chart_json parses to the labeled float series with an int current_index.

        The series is every period that has a projected balance, in
        ``period_index`` order -- which since plan step X-c2b2 is EVERY period
        the user has, because the fold answers a pre-anchor period with the
        balance in force then instead of omitting it (finding cash D3).  The
        chart therefore opens on the ``seed_user`` bootstrap period that
        precedes this fixture's own range, carrying the account's opening
        $5,000.00 back-projected flat (ruling R-I), and ``current_index`` is
        the current period's position in that full list rather than 0.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        import json  # pylint: disable=import-outside-toplevel
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=27)
            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["has_chart"] is True
            chart = json.loads(context["chart_json"])
            assert set(chart.keys()) == {"labels", "balance", "current_index"}
            n = len(chart["balance"])
            assert n > 0
            assert len(chart["labels"]) == n
            assert all(isinstance(v, float) for v in chart["balance"])
            assert isinstance(chart["current_index"], int)
            assert 0 <= chart["current_index"] < n
            # The current period's position in the user's FULL period list --
            # one past the bootstrap period the chart now also draws.
            owner_periods = all_periods(
                seed_user["user"].id,
            )
            assert n == len(owner_periods)
            assert chart["current_index"] == [
                p.id for p in owner_periods
            ].index(_periods[0].id) == 1
            # Every period before the anchor holds the opening flat (R-I), so
            # the series opens on the hero figure either way.
            assert chart["balance"][0] == 5000.0

    def test_anchor_as_of_is_event_date_not_period_start(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """anchor_as_of is the AccountAnchorHistory event date, not the period start.

        The account is anchored at ``seed_periods_today[0]`` (whose
        ``start_date`` is roughly eight weeks before today), but its
        origination ``AccountAnchorHistory`` row is created now, so the
        anchor EVENT date (today) differs from the anchor PERIOD's start
        date.  The context must carry the event INSTANT (the audit's
        finding #2 fix), NOT the period start; the template renders that
        instant in the user's display timezone.

        **The non-vacuity check reads the event's DISPLAY day**, which is what
        the caption shows.  It read ``AnchorPoint.as_of_date`` -- a UTC day --
        until that field was deleted (finding N-133 / F12): it had no
        production reader, and its own docstring justified the UTC choice by an
        index that now keys on the stored ``observed_on`` instead.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.utils.dates import to_display_date  # pylint: disable=import-outside-toplevel
        with app.app_context():
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Anchor Date Checking",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            anchor = cash_ledger.resolve_anchor(acct)
            # Non-vacuity: the event date and the period start genuinely differ.
            # The period comes off the FIXTURE now, not off the AnchorPoint --
            # ruling R-EO deleted ``AnchorPoint.period``, because an assertion
            # is a day and a balance.
            anchor_period_start = seed_periods_today[0].start_date
            assert to_display_date(anchor.created_at) != anchor_period_start

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            # The context carries the day the balance was TRUE
            # (``AnchorPoint.observed_on``), not the day the row was recorded.
            # The two were the same day by construction until ``observed_on``
            # became a stored, user-supplied column (ruling R-DH, plan step 2);
            # a caption reading ``created_at`` would name the keystroke.
            assert context["anchor_as_of"] == anchor.observed_on
            assert context["anchor_as_of"] != anchor_period_start

    def test_interest_next_year_zero_for_zero_apy(
        self, app, auth_client, seed_user, db,
    ):
        """A zero-APY interest account's next-year figure is exactly $0.00.

        Zero APY accrues no interest in any period, so the next-year window
        sum is ``Decimal("0.00")`` -- a legitimate value (E-12: zero is a
        value, not "missing"), not ``None``.  ``None`` is reserved for
        plain (non-interest) accounts.
        """
        with app.app_context():
            # The CALL is the setup: the next-year window needs a year
            # of periods to sum interest across.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=30, rhythm=rhythm_of(14),
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Zero APY HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is True
            assert context["interest_next_year"] == Decimal("0.00")

    def test_interest_next_year_sums_only_the_next_year_window(
        self, app, auth_client, seed_user, db,
    ):
        """interest_next_year sums exactly the periods in [current+1, current+26].

        Thirty-three periods are generated so periods beyond the one-year
        window (``current.period_index + 26``) still accrue interest; the
        route's figure must sum ONLY the 26 in-window periods, so it equals
        the independently-summed window and is STRICTLY LESS than the sum
        over every period (the out-of-window tail proves the window bites).
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.services.balance_at import _kernel as net_worth_kernel  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=33, rhythm=rhythm_of(14),
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Window HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.05000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            db.session.query(InterestParams).filter_by(
                account_id=acct.id,
            ).one()
            current = current_pay_period(seed_user["user"].id)
            # pylint: disable=import-outside-toplevel
            from app.services.balance_at import BalanceContext
            ibp = net_worth_kernel.interest_by_period_for_account(
                acct, BalanceContext.build(seed_user["user"].id),
            )
            lo = derived_span(current).period_index + 1
            hi = derived_span(current).period_index + 26  # 26 biweekly periods = 1 year.
            window_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= derived_span(p).period_index <= hi),
                Decimal("0.00"),
            )
            grand_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods),
                Decimal("0.00"),
            )

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["interest_next_year"] == window_total
            # Non-vacuity: periods beyond the window accrue interest that the
            # route must NOT include, so the window sum is strictly smaller.
            assert window_total < grand_total

    def test_interest_window_is_the_owners_year_not_twenty_six_periods(
        self, app, auth_client, seed_user, db,
    ):
        """A WEEKLY owner's "next 12 mo" chip sums 52 periods, not 26.

        Recurrence plan step **R-F17**, ledger row **F-17**.  The window was a
        hardcoded ``_ONE_YEAR_PERIODS = 26`` whose own comment asserted it
        matched the "1 year" balance chip beside it; at this cadence 26
        periods is SIX months, so the chip summed half the interest it named
        and the two chips disagreed about what a year is.

        Sixty periods are generated so the tail beyond the window still
        accrues -- the route's figure must equal the independently summed
        ``[current + 1, current + 52]`` and be STRICTLY LESS than the sum over
        every period, which is what proves the window bites rather than
        happening to cover everything.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        from app.services.balance_at import _kernel as net_worth_kernel  # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext  # pylint: disable=import-outside-toplevel
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=60, rhythm=rhythm_of(7),
            )
            hysa_type = db.session.query(AccountType).filter_by(name="HYSA").one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="Weekly HYSA",
                    anchor_balance=Decimal("10000.00"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InterestParams(
                account_id=acct.id, apy=Decimal("0.05000"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            current = current_pay_period(seed_user["user"].id)
            ibp = net_worth_kernel.interest_by_period_for_account(
                acct, BalanceContext.build(seed_user["user"].id),
            )
            current_index = derived_span(current).period_index
            lo = current_index + 1
            hi = current_index + 52  # 52 WEEKLY periods = 1 year.
            window_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= derived_span(p).period_index <= hi),
                Decimal("0.00"),
            )
            grand_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods),
                Decimal("0.00"),
            )
            half_year_total = sum(
                (ibp.get(p.id, Decimal("0.00")) for p in periods
                 if lo <= derived_span(p).period_index <= current_index + 26),
                Decimal("0.00"),
            )

            context = _capture_cash_detail_context(app, auth_client, acct.id)

            assert context["interest_next_year"] == window_total
            assert window_total < grand_total
            # The number the hardcoded 26 would have produced, shown to be a
            # DIFFERENT figure rather than merely a different expression.
            assert half_year_total < window_total

    def test_plain_account_interest_next_year_is_none(
        self, app, auth_client, seed_user,
    ):
        """A plain (non-interest) account carries interest_next_year = None.

        The next-year interest chip is interest-only; a checking account
        has no interest projection, so the figure is ``None`` (the template
        omits the chip), NOT ``Decimal("0.00")``.
        """
        with app.app_context():
            acct, _periods = self._checking_with_income(seed_user, num_periods=10)
            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is False
            assert context["interest_next_year"] is None
            assert context["params"] is None
            assert context["compounding_frequencies"] == []


class TestCashDetailRedirectStubs:
    """The legacy /checking and /interest URLs redirect to the merged page.

    The Fable 5 overhaul kept ``checking_detail`` / ``interest_detail`` as
    thin redirect stubs (not deletions) so external bookmarks and the
    not-yet-updated cockpit ``detail_endpoint`` macro still resolve; the
    ``setup=1`` onboarding arg is forwarded so a post-create redirect still
    lands on the wizard banner.
    """

    def test_checking_stub_redirects_to_details(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/checking 302-redirects to /accounts/<id>/details."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/checking")
            assert resp.status_code == 302
            assert f"/accounts/{acct_id}/details" in resp.headers["Location"]

    def test_interest_stub_redirects_to_details(
        self, app, auth_client, seed_user,
    ):
        """GET /accounts/<id>/interest 302-redirects to /accounts/<id>/details."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/interest")
            assert resp.status_code == 302
            assert f"/accounts/{acct_id}/details" in resp.headers["Location"]

    def test_interest_stub_preserves_setup_param(
        self, app, auth_client, seed_user,
    ):
        """The interest stub forwards ?setup=1 so onboarding survives the redirect."""
        with app.app_context():
            acct_id = seed_user["account"].id
            resp = auth_client.get(f"/accounts/{acct_id}/interest?setup=1")
            assert resp.status_code == 302
            location = resp.headers["Location"]
            assert f"/accounts/{acct_id}/details" in location
            assert "setup=1" in location


class TestCashDetailWrongTypeMatrix:
    """Non-cash account kinds 404 out of the merged cash detail page.

    The page serves cash accounts only; loans (has_amortization), physical
    assets (has_appreciation), and retirement / investment accounts
    (category RETIREMENT / INVESTMENT) keep their own screens and must 404
    here -- resolved by boolean type flag and integer category id, never a
    ref-table name string.
    """

    @pytest.mark.parametrize(
        "type_name", ["Mortgage", "Property", "401(k)", "Brokerage"],
    )
    def test_non_cash_type_404(
        self, app, auth_client, seed_user, db, type_name,
    ):
        """A loan / property / retirement / investment account 404s on /details."""
        with app.app_context():
            acct_type = db.session.query(AccountType).filter_by(
                name=type_name,
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=acct_type.id,
                    name=f"WrongType {type_name}",
                    anchor_balance=Decimal("1000.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 404


class TestCashDetailNewCoverage:
    """Previously page-less cash types are now served (audit Surface 6)."""

    def test_credit_card_served_as_plain(
        self, app, auth_client, seed_user, db,
    ):
        """A Credit Card account renders on the cash page with the plain shape.

        Credit Card (Liability category, no amortization / appreciation /
        interest) had no detail page before the merge; it is now served
        with the plain context shape -- no interest params, no compounding
        list.
        """
        with app.app_context():
            cc_type = db.session.query(AccountType).filter_by(
                name="Credit Card",
            ).one()
            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=cc_type.id,
                    name="My Card",
                    anchor_balance=Decimal("-250.00"),
                ),
            )
            db.session.add(acct)
            db.session.commit()

            context = _capture_cash_detail_context(app, auth_client, acct.id)
            assert context["is_interest"] is False
            assert context["params"] is None
            assert context["compounding_frequencies"] == []

            resp = auth_client.get(f"/accounts/{acct.id}/details")
            assert resp.status_code == 200
            assert b"My Card" in resp.data
            # Plain cash: no interest parameters card (no APY input).
            assert b'name="apy"' not in resp.data
