"""The gross: a per-paycheck rate rather than a share of a year, its rounding contract and where
that contract is stated, and the earning lines a paycheck adds to base pay.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.services.paycheck_calculator import calculate_paycheck, project_salary
from app.services.paycheck_calculator._calendar_questions import _get_cumulative_wages
from tests._test_helpers import payroll_basis

from ._helpers import (
    TWO_PLACES,
    FakeRaise,
    _period,
    FakeRule,
    twelve_rule,
    FakeDeduction,
    FakeFicaConfig,
    FakeProfile,
    _WALK_OPENS,
    _walked_pay,
)


class TestTheGrossIsARateAndNotAShareOfAYear:
    """Plan step **balance:X-aw** / ruling **balance:R-HW**: the per-paycheck
    gross is the salary over the owner's paycheck count, and nothing else.

    These replace ``TestBiweeklyResidueReconciliation``, which graded audit
    MED-05 / PA-07's contract -- the annual quantisation residue distributed
    across a calendar year so the year summed to the annual salary exactly.
    That contract is superseded, and the case that mattered most is the one it
    could not have: :meth:`test_the_gross_does_not_move_with_the_period_list`
    is finding **N-239**, and it FAILS on the superseded rule by construction,
    because deciding which paychecks got the residue cent required counting the
    period rows the caller happened to pass.
    """

    @pytest.mark.parametrize(
        "annual_salary,expected_gross",
        [
            # Hand-computed: annual / 26, ROUND_HALF_UP at the cent.
            # $50,000 / 26 = $1923.0769... -> $1923.08
            (Decimal("50000"), Decimal("1923.08")),
            # $75,000 / 26 = $2884.6153... -> $2884.62
            (Decimal("75000"), Decimal("2884.62")),
            # $100,000 / 26 = $3846.1538... -> $3846.15
            (Decimal("100000"), Decimal("3846.15")),
            # $60,000 / 26 = $2307.6923... -> $2307.69
            (Decimal("60000"), Decimal("2307.69")),
            # $78,000 / 26 = $3000.00 exact -- no rounding at all.
            (Decimal("78000"), Decimal("3000.00")),
            # The owner's own salary, whose stub pays a flat $3,526.00:
            # $91,675 / 26 = $3525.9615... -> $3525.96.
            (Decimal("91675"), Decimal("3525.96")),
        ],
    )
    def test_every_paycheck_of_a_salary_segment_pays_the_same_figure(
        self, annual_salary, expected_gross, biweekly_periods,
        simple_tax_configs,
    ):
        """All 26 paychecks carry ONE figure, which is what a real stub does.

        The superseded rule gave the earliest few paychecks of the year an
        extra cent, so a year held two distinct grosses differing by $0.01.
        A real employer pays one number: the owner's own nine measured payroll
        deposits all carry a gross of $3,526.00.
        """
        profile = FakeProfile(
            # The parametrized yearly figure / 26, ROUND_HALF_UP at the cent:
            # what migration 70680a4a7405 converts a stored salary to.
            pay=(annual_salary / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        grosses = {r.earnings.gross_biweekly for r in results}
        assert grosses == {expected_gross}, (
            f"expected one gross {expected_gross} across the year, "
            f"got {sorted(grosses)}"
        )

    def test_the_gross_does_not_move_with_the_period_list(
        self, simple_tax_configs,
    ):
        """Finding **N-239**: extending the schedule must not re-price a paycheck.

        Prices ONE fixed paycheck -- 2026-01-02, the owner's first payday of
        the year -- against period lists of five very different sizes, and
        asserts every caller gets the same answer.  A route preview passes a
        single period; a year-scoped caller passes that year; the recurrence
        engine's schedule extend passes only the rows it just created.

        **The SUBJECT has to sit where the superseded rule put its residue, or
        this case grades nothing.**  An earlier draft used the 14th payday and
        PASSED on the pre-X-aw code, because MED-05 / PA-07 gave the extra cent
        to the earliest 6 periods of the year and the 14th was never one of
        them -- the assertion held under both rules and the test measured
        nothing.  Period 1 is inside that window, so the old rule answers
        $2307.70 from a 26- or 27-period list and $2307.69 from a shorter one
        (which fell to its half-up fallback): two figures for one paycheck.

        $60,000 / 26 = $2307.6923... -> $2307.69, whoever asks and however
        much of the schedule they hold.

        The real-data form of the same defect: on the owner's own schedule,
        filling 2028 from its 16 stored rows to its 26 paydays moved six
        already-settled paychecks by a cent each ($3,930.07 -> $3,930.06).
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        # 27 is not a typo: a biweekly calendar year holds 27 paydays about
        # one year in eleven.  A Jan 2 phase is NOT one of them -- Jan 2 plus
        # 26 x 14 days lands 2027-01-01, so 2026 holds 26 -- and the 27th
        # element here therefore falls in the NEXT year.  It still discriminates
        # (the superseded rule counted 26 same-year periods either way, so both
        # the 26- and 27-element lists answered $2307.70), and it is the case
        # that exercises a list running past its own year's end.  The owner's
        # real phase, Jan 1, is a genuine 27-payday 2026.
        list_sizes = (1, 3, 16, 26, 27)
        subject = _period(start_date=date(2026, 1, 2), period_id=1)

        answers = {}
        for size in list_sizes:
            all_periods = [subject] + [
                _period(start_date=date(2026, 1, 2) + timedelta(days=14 * i),
                        period_id=i + 1)
                for i in range(1, size)
            ]
            assert len(all_periods) == size
            answers[size] = calculate_paycheck(
            payroll_basis(profile, all_periods), subject,
            simple_tax_configs,
            ).earnings.gross_biweekly

        assert set(answers.values()) == {Decimal("2307.69")}, (
            "one paycheck must have ONE gross however much of the schedule "
            f"the caller holds; got {answers}"
        )

    def test_a_mid_year_raise_gives_two_constant_figures(
        self, biweekly_periods, simple_tax_configs,
    ):
        """A raise changes the rate ONCE; each side of it is flat.

        A non-recurring 10% raise effective July 2026 splits the year at the
        14th period (Jan 2 + 13*14 days = Jul 3).  Before it every paycheck is
        $60,000 / 26 = $2307.69; after it every paycheck is
        $2307.69 x 1.10 = $2538.459 -> $2538.46 (the raise steps the
        per-paycheck pay since plan step salary:X-av-3a, ruling R-SAL60; the
        yearly walk's $66,000 / 26 rounded to the same figure).  The
        superseded rule gave the first three of the pre-raise run and the
        first two of the post-raise run an extra cent, so each run held two
        figures.  The yearly figure each paycheck reports is pay x 26 (ruling
        R-SAL59): $59,999.94 and $65,999.96.
        """
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
            raises=[
                FakeRaise(
                    percentage="0.10",
                    effective_month=7, effective_year=2026,
                    is_recurring=False,
                ),
            ],
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        assert len(results) == 26
        pre = {r.earnings.gross_biweekly for r in results[:13]}
        post = {r.earnings.gross_biweekly for r in results[13:]}
        assert [r.earnings.annual_salary for r in results[:13]] == (
            [Decimal("59999.94")] * 13
        )
        assert [r.earnings.annual_salary for r in results[13:]] == (
            [Decimal("65999.96")] * 13
        )
        assert pre == {Decimal("2307.69")}, f"pre-raise run: {sorted(pre)}"
        assert post == {Decimal("2538.46")}, f"post-raise run: {sorted(post)}"

        # What ruling R-HW costs in a RAISE year, pinned: the superseded rule
        # made each segment total its exact pro-rata share ($30,000.00 and
        # $33,000.00, summing to $63,000.00). Each is now the flat rate times
        # the segment, three cents and two cents under respectively.
        pre_total = sum(r.earnings.gross_biweekly for r in results[:13])
        post_total = sum(r.earnings.gross_biweekly for r in results[13:])
        assert pre_total == Decimal("29999.97"), f"pre-raise total {pre_total}"
        assert post_total == Decimal("32999.98"), f"post-raise total {post_total}"
        assert pre_total + post_total == Decimal("62999.95")

    @pytest.mark.parametrize(
        "annual_salary,expected_year_total,expected_gap",
        [
            # 26 * round(annual/26) against the annual salary.  The gap is
            # what ruling R-HW accepts and MED-05 / PA-07 existed to close;
            # it is stated per row so the cost is reviewable rather than
            # asserted as "close enough".
            # 26 * 2307.69 = 59999.94, $0.06 under $60,000.
            (Decimal("60000"), Decimal("59999.94"), Decimal("-0.06")),
            # 26 * 1923.08 = 50000.08, $0.08 OVER $50,000 -- the gap has no
            # fixed sign, because the per-paycheck figure rounds either way.
            (Decimal("50000"), Decimal("50000.08"), Decimal("0.08")),
            # 26 * 3000.00 = 78000.00 exactly: a salary that divides evenly
            # has no gap at all.
            (Decimal("78000"), Decimal("78000.00"), Decimal("0.00")),
            # The owner's: 26 * 3525.96 = 91674.96, $0.04 under $91,675 --
            # and the employer's own flat $3,526.00 sums to $91,676.00, which
            # is $1.00 over.  Neither the app nor payroll hits the salary
            # exactly, which is why the identity was given up (finding N-391).
            (Decimal("91675"), Decimal("91674.96"), Decimal("-0.04")),
        ],
    )
    def test_the_year_no_longer_sums_to_the_annual_salary_exactly(
        self, annual_salary, expected_year_total, expected_gap,
        biweekly_periods, simple_tax_configs,
    ):
        """The COST of ruling R-HW, pinned so it cannot drift unnoticed.

        MED-05 / PA-07 added the residue distribution to make this sum exact.
        Ruling R-HW gives that up deliberately: the identity is not one payroll
        honours, and buying it cost a per-paycheck figure that no stub shows
        and a dependence on which pay-period rows exist (finding N-239).

        **Since plan step salary:X-av-3a the pay is the stored fact and the
        year is derived** (ruling R-SAL59: the yearly figure is pay x
        paychecks a year), so the total below is 26 x the pay by
        construction and the gap is the migration's one-time rounding of the
        old yearly salary (pinned in ``test_pay_entries_migration``).  What
        this case grades of the ENGINE is that the yearly figure every
        paycheck reports is that same total -- the one derivation R-SAL59
        names -- and never the contract salary it was converted from.
        """
        profile = FakeProfile(
            # The parametrized yearly figure / 26, ROUND_HALF_UP at the cent:
            # what migration 70680a4a7405 converts a stored salary to.
            pay=(annual_salary / 26).quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
            created_at=date(2026, 1, 1),
        )

        results = project_salary(
            payroll_basis(profile, biweekly_periods), biweekly_periods, simple_tax_configs
        )

        total = sum(r.earnings.gross_biweekly for r in results)
        assert total == expected_year_total
        assert total - annual_salary.quantize(TWO_PLACES) == expected_gap
        assert {r.earnings.annual_salary for r in results} == {expected_year_total}


    # **``test_the_investment_projection_prices_the_same_gross`` was DELETED
    # at plan step salary:R14-b, and what it graded is worth stating.**  It
    # pinned that the paycheck engine and ``investment_projection`` rounded a
    # percentage deduction's gross identically -- driven at the owner's own
    # 2027 salary of $96,785.88, where until plan step balance:X-aw the two
    # answered $3,722.54 and $3,722.53 on 5 of his 63 saved periods, so a
    # percentage was taken against a gross a cent below the one the paycheck
    # subtracted it from.  X-aw made both ask
    # ``payroll_basis.gross_per_paycheck``, and this case held them there.
    #
    # It has no successor because it has no SUBJECT: the second producer is
    # gone.  The contribution feed reads this engine's own breakdown now
    # (ruling **R-SAL2**), so the agreement the case enforced is structural
    # rather than asserted -- and the caveat it carried is settled with it.
    # Its own docstring recorded that it graded the rounding rule and NOT the
    # wiring, because the two sides' INPUTS still differed by any applicable
    # raise (finding **D45**), and that "a case asserting these two equal on a
    # raise-BEARING profile would fail, and should".  There is one input now,
    # and ``test_income_service.TestThePerPeriodGrossIsTheENGINES`` grades a
    # raise-bearing profile end to end.


class TestGrossPerPaycheck:
    """The per-paycheck gross's rounding contract, graded directly rather than through the engine.

    Plan step **balance:X-aw** added ``gross_per_paycheck`` as a public
    function with two callers, and an adversarial review noted it had no case
    of its own.  **Plan step salary:X-av-3a deleted it**: the per-paycheck
    pay is stored now (ruling R-SAL59), so the yearly division it performed
    has no successor -- its float refusal and its divisor-at-every-cadence
    cases went with it -- and the migration's one conversion is graded in
    ``test_pay_entries_migration``.  What survives is its rounding MODE, which
    the walk's every raise step now owns (ruling R-SAL60).
    """

    def test_it_rounds_half_up_at_an_exact_half_cent(self):
        """The rounding MODE, at the one input where the modes disagree.

        None of the salaries the engine tests land on a half cent, so the mode
        is inherited from ``round_money`` and graded nowhere in this file.
        Re-stated on the pay list's walk at plan step salary:X-av-3a, where
        the rounding is a raise step's: $2,000.10 a paycheck under a 5% raise
        is 2000.10 x 1.05 = $2,100.105 exactly -- ROUND_HALF_UP gives
        $2,100.11, Python's default ROUND_HALF_EVEN (banker's) gives
        $2,100.10.  A money figure must never reach the even-rounding default
        implicitly.
        """
        profile = FakeProfile(
            pay=Decimal("2000.10"), pay_from=_WALK_OPENS,
            raises=[FakeRaise(percentage="0.05", effective_month=1,
                              effective_year=2026)],
        )
        assert _walked_pay(profile, date(2026, 1, 2)) == Decimal("2100.11")


class TestTheGrossContractIsDocumented:
    """The per-paycheck gross contract is stated where a reader will find it.

    Replaces ``TestBiweeklyResidueDocstring``, which pinned MED-05 / PA-07's
    wording so a revert to F-127's could not pass silently.  Ruling
    **balance:R-HW** superseded MED-05 / PA-07, so these pin the NEW contract
    for the same reason: the residue-distribution wording must not creep back
    in and read as though it still applied.

    The fourth arm that lived here -- the ids these docstrings cite have a
    row in the registry they name -- left this suite on 2026-09-11 and went
    with the planning registries at the tracker switch: each id now titles a
    card in the tracker, which no test reads.
    """

    def test_module_docstring_names_the_rate_contract(self):
        """Module docstring states the rule, what it replaced, and its cost."""
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel

        doc = paycheck_calculator.__doc__ or ""
        assert "RATE" in doc
        assert "X-aw" in doc
        assert "R-HW" in doc
        # The supersession trail stays legible in both directions.
        assert "MED-05" in doc and "PA-07" in doc
        # Both spellings appear and both are load-bearing: this contract
        # SUPERSEDED MED-05 / PA-07, which had itself superseded F-127.
        assert "superseding" in doc and "superseded" in doc
        assert "N-239" in doc
        # The cost is stated rather than quietly dropped.
        assert "no longer sum to the annual salary exactly" in doc

    def test_calculate_paycheck_docstring_points_at_the_one_producer(self):
        """The function docstring names the producer and denies the list.

        A caller reading only the signature in an IDE tooltip has to learn
        that the owner's payday SET does NOT reach the gross -- that is the
        whole content of finding N-239.  The set is no longer an argument at
        all: plan step **balance:X-bh-1** moved the four judgements that DO
        read it onto the calendar the basis carries.  The producer it names
        is the pay list's walk since plan step salary:X-av-3a deleted
        ``gross_per_paycheck``.
        """
        doc = calculate_paycheck.__doc__ or ""
        assert "base_pay_on" in doc
        assert "RATE" in doc
        assert "the payday SET does not reach it" in doc

    def test_the_producer_states_what_it_gave_up(self):
        """The producer carries the rulings that cost it cents.

        ``gross_per_paycheck`` carried R-HW, N-239 and MED-05 until plan step
        salary:X-av-3a deleted it; the package docstring keeps that trail
        (the case above).  The producer now is the pay list's walk,
        ``PayrollBasis.base_pay_on``, and what it gives up is ruled twice:
        the yearly figure is pay x paychecks a year rather than the salary
        typed (R-SAL59), and each raise step rounds to the cent (R-SAL60).
        """
        from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
            BasePay,
            PayrollBasis,
        )

        doc = PayrollBasis.base_pay_on.__doc__ or ""
        assert "R-SAL59" in doc
        assert "R-SAL60" in doc
        assert "R-SAL59" in (BasePay.__doc__ or "")


# ── Earning lines (plan step salary:R18-b, ruling R-SAL38) ──────────────


class TestEarningLines:
    """A paycheck is base pay plus a list of lines: the two EARNING kinds, hand-computed.

    Every figure here is derived by hand from the same rules the deduction
    cases above state ($60k over 26; 10% to $50k, 22% above, $15k standard
    deduction; NC 4.5%; FICA 6.2% / 1.45%) and asserted as a constant, so the
    engine is graded against arithmetic done outside it.  Base pay is
    ``60000 / 26 = 2307.69``; the base case with no line nets ``1854.22``
    (``test_basic_paycheck_no_deductions``).
    """

    @staticmethod
    def _one(period_date=date(2026, 1, 16)):
        return _period(start_date=period_date, period_id=1)

    def test_a_taxable_earning_joins_gross_fica_and_withholding(self, simple_tax_configs):
        """+$100 taxable every paycheck: gross 2407.69, every tax line moves, net 1932.07.

          gross    = 2307.69 + 100.00 = 2407.69     (base stays 2307.69)
          federal  : annual 62599.94 - 15000 = 47599.94; 10% -> 4759.99; /26 = 183.08
          state    : 62599.94 * 0.045 = 2817.00; /26 = 108.35
          SS       : 2407.69 * 0.062 = 149.28
          Medicare : 2407.69 * 0.0145 = 34.91
          net      = 2407.69 - 183.08 - 108.35 - 149.28 - 34.91 = 1932.07
        """
        line = FakeDeduction(name="Phone Allowance", amount="100", paycheck_line_kind="taxable_earning")
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.earnings.base_biweekly == Decimal("2307.69")
        assert result.earnings.gross_biweekly == Decimal("2407.69")
        assert result.earnings.total_taxable == Decimal("100.00")
        assert [(l.name, l.amount, l.target_account_id) for l in result.earnings.taxable] == [
            ("Phone Allowance", Decimal("100.00"), None),
        ]
        assert result.earnings.after_tax == []
        assert result.earnings.taxable_income == Decimal("2407.69")
        assert result.taxes.federal == Decimal("183.08")
        assert result.taxes.state == Decimal("108.35")
        assert result.taxes.social_security == Decimal("149.28")
        assert result.taxes.medicare == Decimal("34.91")
        assert result.earnings.net_pay == Decimal("1932.07")
        # The pass shares the deduction lists' shape and leaves them empty.
        assert result.deductions.pre_tax == [] and result.deductions.post_tax == []

    def test_an_after_tax_earning_joins_net_and_nothing_else(self, simple_tax_configs):
        """+$100 after tax: gross, taxable income and every tax line as the base case; net + 100."""
        line = FakeDeduction(name="Reimbursement", amount="100", paycheck_line_kind="after_tax_earning")
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.earnings.base_biweekly == Decimal("2307.69")
        assert result.earnings.gross_biweekly == Decimal("2307.69")
        assert result.earnings.taxable_income == Decimal("2307.69")
        assert result.earnings.taxable == []
        assert [(l.name, l.amount) for l in result.earnings.after_tax] == [
            ("Reimbursement", Decimal("100.00")),
        ]
        assert result.earnings.total_after_tax == Decimal("100.00")
        assert (result.taxes.federal, result.taxes.state,
                result.taxes.social_security, result.taxes.medicare) == (
            Decimal("173.08"), Decimal("103.85"), Decimal("143.08"), Decimal("33.46"),
        )
        assert result.earnings.net_pay == Decimal("1954.22")

    def test_a_percentage_deduction_is_a_percentage_of_base_not_of_gross(self, simple_tax_configs):
        """6% pre-tax beside a $100 taxable earning: 6% x 2307.69 = 138.46, not 6% x 2407.69 = 144.46.

        Ruling R-SAL38 (3).  With the line taken from base:
          taxable income = 2407.69 - 138.46 = 2269.23
          federal : annual 62599.94 - 3599.96 (138.46 x 26) - 15000 = 43999.98; 10% -> 4400.00; /26 = 169.23
          state   : 2269.23 x 26 = 58999.98 x 0.045 = 2655.00; /26 = 102.12
          SS / Medicare on gross: 149.28 / 34.91
          net     = 2407.69 - 138.46 - 169.23 - 102.12 - 149.28 - 34.91 = 1813.69
        """
        lines = [
            FakeDeduction(name="Phone Allowance", amount="100", paycheck_line_kind="taxable_earning"),
            FakeDeduction(name="State Retirement", amount="0.06", calc_method="percentage",
                          paycheck_line_kind="pre_tax_deduction"),
        ]
        profile = FakeProfile(
            pay=Decimal("2307.69"), pay_from=date(2026, 1, 16),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=lines,
        )
        period = self._one()

        result = calculate_paycheck(payroll_basis(profile, [period]), period, simple_tax_configs)

        assert result.deductions.pre_tax[0].amount == Decimal("138.46")
        assert result.earnings.gross_biweekly == Decimal("2407.69")
        assert result.earnings.taxable_income == Decimal("2269.23")
        assert result.taxes.federal == Decimal("169.23")
        assert result.taxes.state == Decimal("102.12")
        assert result.earnings.net_pay == Decimal("1813.69")

    def test_a_taxable_earnings_cadence_places_it_on_the_first_paycheck_of_the_month(
        self, simple_tax_configs,
    ):
        """The developer's phone allowance: $45 taxable on the month's first paycheck only.

        Jan 2 is January's first paycheck (gross 2352.69); Jan 16 its second
        (gross 2307.69, no line); the year-to-date wages before Jan 16 carry
        the $45 -- the one gross producer, replayed.
        """
        line = FakeDeduction(
            name="Phone Allowance", amount="45", paycheck_line_kind="taxable_earning",
            recurrence_rule=twelve_rule(date(2026, 1, 1)),
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        jan2 = _period(start_date=date(2026, 1, 2), period_id=1)
        jan16 = _period(start_date=date(2026, 1, 16), period_id=2)
        basis = payroll_basis(profile, [jan2, jan16])

        first = calculate_paycheck(basis, jan2, simple_tax_configs)
        second = calculate_paycheck(basis, jan16, simple_tax_configs)

        assert first.earnings.gross_biweekly == Decimal("2352.69")
        assert [l.amount for l in first.earnings.taxable] == [Decimal("45.00")]
        assert second.earnings.gross_biweekly == Decimal("2307.69")
        assert second.earnings.taxable == []
        assert _get_cumulative_wages(basis, jan16.start_date) == Decimal("2352.69")

    def test_the_year_to_date_wages_carry_the_taxable_earning_into_the_ss_cap(self):
        """A $100 taxable line reaches a $4,700 wage base one paycheck earlier.

        statutory max = 0.062 x 4700 = 291.40.  Without the line the third
        paycheck (cumulative 4615.38) owes the remainder, 5.25; with it the
        second paycheck (cumulative 2407.69) owes 142.12 and the third
        (cumulative 4815.38) owes nothing -- which is what a cumulative that
        left the earning out would get wrong.
        """
        configs = {
            "bracket_set": None,
            "state_config": None,
            "fica_config": FakeFicaConfig(ss_wage_base="4700"),
        }
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        plain = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1),
        )
        with_line = FakeProfile(
            pay=Decimal("2307.69"), created_at=date(2026, 1, 1),  # $60,000.00 a year / 26
            lines=[FakeDeduction(name="Bonus", amount="100", paycheck_line_kind="taxable_earning")],
        )

        plain_ss = [
            calculate_paycheck(payroll_basis(plain, periods), p, configs).taxes.social_security
            for p in periods
        ]
        line_ss = [
            calculate_paycheck(payroll_basis(with_line, periods), p, configs).taxes.social_security
            for p in periods
        ]

        assert plain_ss == [Decimal("143.08"), Decimal("143.08"), Decimal("5.25")]
        assert line_ss == [Decimal("149.28"), Decimal("142.12"), Decimal("0.00")]
        assert _get_cumulative_wages(payroll_basis(with_line, periods), periods[2].start_date) == Decimal("4815.38")

    def test_a_lines_span_bounds_the_paychecks_it_is_taken_on(self, simple_tax_configs):
        """A rule from the second paycheck for two occurrences is taken on the 2nd and 3rd only.

        Plan step salary:R18-c (ruling **R-SAL38** (2)): a line's span is its
        rule's own ``starts_on`` and closing bound, which the occurrence walk
        the engine already reads has honoured for every definition; this
        pins that a payroll line's earning rides only the paydays inside it.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
        rule = FakeRule(
            unit=RecurrenceUnitEnum.PERIOD,
            placement=PeriodPlacementEnum.CONTAINING_DATE,
            starts_on=date(2026, 1, 16),
        )
        rule.max_occurrences = 2
        line = FakeDeduction(
            name="Stipend", amount="100", paycheck_line_kind="after_tax_earning",
            recurrence_rule=rule,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[line],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
            _period(start_date=date(2026, 2, 13), period_id=4),
        ]
        basis = payroll_basis(profile, periods)

        results = [calculate_paycheck(basis, p, simple_tax_configs) for p in periods]

        assert [r.earnings.total_after_tax for r in results] == [
            Decimal("0.00"), Decimal("100.00"), Decimal("100.00"), Decimal("0.00"),
        ]

    def test_an_earning_is_capped_and_deactivated_like_any_line(self, simple_tax_configs):
        """The shared pass: a $150 annual cap on a $100 after-tax line pays 100, 50, 0; an inactive line pays nothing."""
        capped = FakeDeduction(
            name="Stipend", amount="100", annual_cap="150", paycheck_line_kind="after_tax_earning",
        )
        inactive = FakeDeduction(
            name="Old Allowance", amount="500", paycheck_line_kind="taxable_earning", is_active=False,
        )
        profile = FakeProfile(
            pay=Decimal("2307.69"),  # $60,000.00 a year / 26
            created_at=date(2026, 1, 1), lines=[capped, inactive],
        )
        periods = [
            _period(start_date=date(2026, 1, 2), period_id=1),
            _period(start_date=date(2026, 1, 16), period_id=2),
            _period(start_date=date(2026, 1, 30), period_id=3),
        ]
        basis = payroll_basis(profile, periods)

        results = [calculate_paycheck(basis, p, simple_tax_configs) for p in periods]

        assert [r.earnings.total_after_tax for r in results] == [
            Decimal("100.00"), Decimal("50.00"), Decimal("0.00"),
        ]
        assert [r.earnings.net_pay for r in results] == [
            Decimal("1954.22"), Decimal("1904.22"), Decimal("1854.22"),
        ]
        assert all(r.earnings.gross_biweekly == Decimal("2307.69") for r in results)
