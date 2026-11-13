"""Each paycheck is priced under its own year's tax rules; a new year moves no projection."""

from datetime import date
from decimal import Decimal

from app.services import paycheck_calculator
from tests._tax_configs import hand_built_law, tax_configs_for
from app.tax_law import FicaRules, TaxLaw
from tests._test_helpers import (
    made_up_state,
    made_up_year,
    all_periods,
    freeze_today,
    payroll_basis,
)

from ._helpers import _create_profile, _make_salary_template, _make_txn, _net_map, _derived


class TestLiveProjectedNetUsesPerYearTaxConfigs:
    """DH-#30: the salary derivation resolves tax configs PER period year.

    The recurrence engine GENERATES the stored grid amount using each
    period's OWN tax year; the live recompute must resolve the same way or
    the stored cache and the live value silently disagree -- the exact
    reconciliation contract amount rule 2 advertises.  Pre-fix it
    loaded a single current-year config set and applied it across the whole
    ~2-year horizon, so a future-year period was recomputed against the
    wrong year's tax.
    """

    def test_future_year_txn_uses_future_year_state_rate(
        self, app, db, monkeypatch, seed_user, seed_periods_52, tax_law,
    ):
        """A 2027 salary income row recomputes against 2027's state rate.

        Installs NC flat state tax at different rates for 2026 (3.99%) and
        2027 (6.00%), then asserts a 2027 period's live net equals the
        2027-rate projection and differs from the 2026-rate one.  ``today``
        is frozen to 2026 so the pre-fix single current-year load would
        have used the 2026 rate -- the revert-proof property (without the
        freeze, a suite run in 2027 would make the old code pick 2027 by
        coincidence).
        """
        freeze_today(monkeypatch, date(2026, 6, 1))
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)

            # A NC state rate per year and nothing else: a law year cannot
            # lack federal rules or FICA, so each carries the zero ones.
            tax_law(TaxLaw(years=tuple(
                made_up_year(year, states={
                    "NC": made_up_state(rate, standard_deduction=Decimal("0.00")),
                })
                for year, rate in ((2026, Decimal("0.0399")), (2027, Decimal("0.0600")))
            )))
            db.session.commit()

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(seed_user, period_2027, template=template)
            db.session.commit()

            # Engine-faithful expectations that isolate WHICH year's rate
            # was applied (the resolution under test, not the paycheck
            # math): project the same periods with one year's config set at
            # a time and read the 2027 period's net from each.
            net_2027_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    tax_configs_for(profile, 2027),
                )
            }[period_2027.id]
            net_2026_rate = {
                bd.period.period_id: bd.earnings.net_pay
                for bd in paycheck_calculator.project_salary(
                    payroll_basis(profile, _derived(user_id)),
                    _derived(user_id),
                    tax_configs_for(profile, 2026),
                )
            }[period_2027.id]
            # The two state rates genuinely diverge, so the test cannot
            # pass vacuously.
            assert net_2027_rate != net_2026_rate

            overrides = _net_map(user_id, scenario_id, [txn])
            assert overrides[txn.id] == net_2027_rate
            assert overrides[txn.id] != net_2026_rate


class TestTheProjectionDoesNotMoveWhenTheCalendarYearTURNS:
    """The New Year cliff: a projection may not change because a date passed.

    The law carries only the years that have been published, so on a
    January 1 the CURRENT year can be one it lacks.  The retired
    resolution rule substituted "the current calendar year", which cannot
    answer for the year it is itself: a request for the now-current year found
    nothing to redirect to and resolved to no configuration at all.  The
    paycheck engine reads a missing ``fica_config`` as ZERO Social Security
    (``tax_calculator.capped_social_security`` documents that arm for
    bootstrap), so the whole SS line silently vanished from every projected
    paycheck in that year and later.

    Measured on a clone of production 2026-08-11, before the fix: on
    2027-01-01, with no write and no user action, 40 of 51 live-priced salary
    rows changed and projected income over the horizon rose by **$8,460.50**;
    counting the 11 periods the grid's own rolling top-up creates that same
    day, **$10,914.93** over 51 of 62 rows.  One period went from
    ``NET 2,639.30`` (ss 205.19) to ``NET 2,844.49`` (ss 0.00).

    Neither figure was only a display defect.  A settle writes the live amount
    into ``estimated_amount`` before the status flip
    (``transaction_service._reconcile_cached_amount``), after which the row
    leaves this producer's Projected-only candidate set and nothing can repair
    it; and any salary or tax-config save regenerates every row from today
    forward.  The read-time gap had two write-back doors.
    """

    @staticmethod
    def _law_2026_only():
        """Return a made-up law: NC state tax and FICA for 2026, and no other year.

        No federal bracket set, as the rows it replaced held none: the year
        carries the zero federal rules a law year cannot lack.
        """
        return TaxLaw(years=(
            made_up_year(
                2026,
                fica=FicaRules(
                    ss_rate=Decimal("0.0620"),
                    ss_wage_base=Decimal("184500.00"),
                    medicare_rate=Decimal("0.0145"),
                    medicare_surtax_rate=Decimal("0.0090"),
                    medicare_surtax_threshold=Decimal("200000.00"),
                ),
                states={"NC": made_up_state(
                    Decimal("0.0399"), standard_deduction=Decimal("0.00"),
                )},
            ),
        ))

    def test_a_2027_paycheck_is_priced_the_same_in_2026_and_in_2027(
        self, app, db, monkeypatch, seed_user, seed_periods_52, tax_law,
    ):
        """The same row, the same inputs, two different "todays", one answer.

        The property the resolution rule exists for.  Nothing about the row or
        its inputs changes between the two reads -- only the wall clock -- so
        any difference is the app rewriting a projection because a date passed.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            template = _make_salary_template(seed_user, profile)
            tax_law(self._law_2026_only())

            periods = all_periods(user_id)
            period_2027 = next(
                (p for p in periods if p.start_date.year == 2027), None,
            )
            assert period_2027 is not None, "seed_periods_52 must reach 2027"
            txn = _make_txn(seed_user, period_2027, template=template)
            db.session.commit()

            freeze_today(monkeypatch, date(2026, 6, 1))
            priced_in_2026 = _net_map(user_id, scenario_id, [txn])[txn.id]

            freeze_today(monkeypatch, date(2027, 6, 1))
            priced_in_2027 = _net_map(user_id, scenario_id, [txn])[txn.id]

            assert priced_in_2027 == priced_in_2026

    def test_every_withholding_line_is_what_an_unresolved_year_deletes(
        self, app, db, seed_user, seed_periods_52, tax_law,
    ):
        """Non-vacuity: an unresolved 2027 really does zero the withholding.

        Without this the sibling above could pass with both reads equally
        wrong.  It prices the same 2027 period against the three ``None``s the
        retired rule produced on 2027-01-01 (the exact-year loader that also
        returned them was deleted at plan step salary:X-at-1) and shows every
        withholding line collapsing to zero, which raises the net by their sum.

        On production only the Social Security line moved, because that
        profile then carried an ACTIVE calibration and the calibrated path
        (deleted at plan step salary:S11-c-2c) took federal and state from
        stored effective rates; only SS read ``fica_config``.  This fixture
        holds no pay stub, so the formulas alone price it and all three lines
        move.  Both are the same defect -- a config set that resolved to
        nothing -- seen through different tax paths.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            profile = _create_profile(user_id, scenario_id)  # $104k, NC
            _make_salary_template(seed_user, profile)
            tax_law(self._law_2026_only())

            periods = all_periods(user_id)
            period_2027 = next(
                p for p in periods if p.start_date.year == 2027
            )

            derived = _derived(user_id)
            derived_2027 = next(
                p for p in derived if p.start_date.year == 2027
            )
            basis = payroll_basis(profile, derived)
            resolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                tax_configs_for(profile, 2027),
            )
            # The three ``None``s the retired rule produced on 2027-01-01.
            unresolved = paycheck_calculator.calculate_paycheck(
                basis, derived_2027,
                hand_built_law(
                    2027, bracket_set=None, state_config=None, fica_config=None,
                ),
            )

            # $104,000 / 26 = $4,000.00 gross, no pre-tax deductions, so each
            # line is a flat rate on the full gross:
            #   state    4000.00 * 0.0399 = 159.60
            #   SS       4000.00 * 0.0620 = 248.00  (under the $184,500 base)
            #   medicare 4000.00 * 0.0145 =  58.00
            #   federal                    =   0.00  (zero federal rules)
            # net = 4000.00 - 159.60 - 248.00 - 58.00 = 3,534.40
            assert resolved.earnings.gross_biweekly == Decimal("4000.00")
            assert resolved.taxes.state == Decimal("159.60")
            assert resolved.taxes.social_security == Decimal("248.00")
            assert resolved.taxes.medicare == Decimal("58.00")
            assert resolved.earnings.net_pay == Decimal("3534.40")

            assert unresolved.taxes.state == Decimal("0.00")
            assert unresolved.taxes.social_security == Decimal("0.00")
            assert unresolved.taxes.medicare == Decimal("0.00")
            assert unresolved.earnings.net_pay == Decimal("4000.00")

            # The whole withholding, handed back to the projection as income.
            assert (
                unresolved.earnings.net_pay - resolved.earnings.net_pay
                == Decimal("465.60")
            )
