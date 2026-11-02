"""
Shekel Budget App -- Salary Cockpit Producer Tests

Unit tests for :mod:`app.services.salary_cockpit_service`, the pure
producers behind the salary cockpit and projection summary.  The
functions take ``(period, breakdown)`` pairs and return plain Decimal
context, so these tests hand-build breakdowns and hand-compute every
expected figure (no DB, no Flask).

Scenario shared by most tests (7 periods, one merit raise, two third
paychecks) -- salary A = $52,000 for periods 0-3, salary B = $54,000
for periods 4-6:

  idx start        salary  net     kind
  0   2026-05-01   A       1400    regular
  1   2026-05-15   A       1600    third paycheck
  2   2026-06-01   A       1400    regular  (contains today 2026-06-05)
  3   2026-06-15   A       1400    regular
  4   2026-07-01   B       1450    regular  (raise event)
  5   2026-07-15   B       1450    regular
  6   2026-11-01   B       1650    third paycheck
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services import salary_cockpit_service as svc
from app.services.pay_calendar import DerivedPeriod, PayCadence
from app.services.pay_rhythm import FixedDays
from app.services.paycheck_calculator import (
    DeductionBreakdown,
    PricedLine,
    Earnings,
    PaycheckBreakdown,
    PeriodInfo,
    TaxLines,
)

TODAY = date(2026, 6, 5)


def _fake_period(period_id, start_date, end_date):
    """A REAL :class:`DerivedPeriod` (the producers read id/start/end only).

    It was a ``_FakePeriod`` dataclass with an ``id`` field until pay-calendar
    plan step C2-f2d-3 moved these producers onto the derived calendar.  A
    duck type with the wrong field name would have kept passing here while
    every production caller broke, so the production value is built instead.
    """
    return DerivedPeriod(
        period_id=period_id,
        period_index=period_id - 1,
        start_date=start_date,
        end_date=end_date,
        end_is_projected=False,
    )


#: The rhythm a paycheck here is priced at unless a case says otherwise.
_BIWEEKLY = PayCadence(FixedDays(14))


def _priced(lines, kind):
    """Return ``(name, amount_str)`` pairs as the priced lines of *kind*.

    A priced line names the kind it was priced as, required since plan step
    salary:S11-e (ledger row SAL-597), so every fake line here names the kind
    of the list it is built into.
    """
    kind_id = ref_cache.paycheck_line_kind_id(kind)
    return [
        PricedLine(name=name, amount=Decimal(amount), paycheck_line_kind_id=kind_id)
        for name, amount in lines
    ]


def _pair(
    pid, start, end, annual, gross, net, *,
    taxable="0", is_third=False, raise_event="",
    federal="0", state="0", ss="0", medicare="0",
    pre=(), post=(), taxable_lines=(), after_tax=(), cadence=_BIWEEKLY,
    stub_payday=None,
):
    """Build a ``(period, breakdown)`` pair from plain values.

    ``pre`` / ``post`` are iterables of ``(name, amount_str)`` deduction
    lines; ``taxable_lines`` / ``after_tax`` the two earning kinds' (plan
    step salary:R18-b).  ``gross`` is the figure the engine would report --
    base plus the taxable lines -- and ``base_biweekly`` is derived from it
    here so the fake carries the engine's own identity.  Every monetary
    value is constructed from a string.  ``cadence`` is the rhythm the
    paycheck was priced at (ruling R-SAL70); biweekly unless a case crosses
    an era change.  ``stub_payday`` is the payday of the pay stub the
    engine priced the four taxes from, ``None`` for the tax formulas (plan
    step salary:S11-c-2c).
    """
    period = _fake_period(pid, start, end)
    breakdown = PaycheckBreakdown(
        period=PeriodInfo(
            period.start_date, pid,
            is_third_paycheck=is_third, raise_event=raise_event,
            cadence=cadence,
        ),
        earnings=Earnings(
            annual_salary=Decimal(annual),
            base_biweekly=Decimal(gross) - sum(
                (Decimal(a) for _n, a in taxable_lines), Decimal("0"),
            ),
            gross_biweekly=Decimal(gross),
            taxable_income=Decimal(taxable),
            net_pay=Decimal(net),
            taxable=_priced(taxable_lines, PaycheckLineKindEnum.TAXABLE_EARNING),
            after_tax=_priced(after_tax, PaycheckLineKindEnum.AFTER_TAX_EARNING),
        ),
        taxes=TaxLines(
            federal=Decimal(federal), state=Decimal(state),
            social_security=Decimal(ss), medicare=Decimal(medicare),
            stub_payday=stub_payday,
        ),
        deductions=DeductionBreakdown(
            pre_tax=_priced(pre, PaycheckLineKindEnum.PRE_TAX_DEDUCTION),
            post_tax=_priced(post, PaycheckLineKindEnum.POST_TAX_DEDUCTION),
        ),
    )
    return period, breakdown


def _scenario():
    """Return the shared 7-period scenario as an ordered pairs list."""
    return [
        _pair(10, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1400"),
        _pair(11, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1600",
              is_third=True),
        _pair(12, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
              taxable="1800", state="150", ss="124", medicare="26",
              pre=[("401k", "200")], post=[("Roth", "100")]),
        _pair(13, date(2026, 6, 15), date(2026, 6, 28), "52000", "2000", "1400"),
        _pair(14, date(2026, 7, 1), date(2026, 7, 14), "54000", "2076.92", "1450",
              raise_event="MERIT +2.5000%"),
        _pair(15, date(2026, 7, 15), date(2026, 7, 28), "54000", "2076.92", "1450"),
        _pair(16, date(2026, 11, 1), date(2026, 11, 14), "54000", "2076.92", "1650",
              is_third=True),
    ]


class TestCleanRaiseLabel:
    """clean_raise_label: display cleaning of calculator raise_event strings.

    Input shapes are exactly what get_raise_event emits (verified against
    app/services/salary_raises.py, which the engine package's ``_pricing``
    leaf calls): "{TYPE} +{pct}%" for percentage
    raises (pct = stored Numeric(5,4) percentage * 100, e.g. "2.5000"),
    "{TYPE} +${amount:,.2f}" for flat raises, multiple events joined with
    ", ", and "RAISE" as the null-relationship type fallback.
    """

    def test_percentage_trailing_zeros_trimmed(self):
        """Fractional percentage: "MERIT +2.5000%" -> "Merit +2.5%"."""
        assert svc.clean_raise_label("MERIT +2.5000%") == "Merit +2.5%"

    def test_percentage_whole_number(self):
        """Whole percentage drops the dot: "COLA +3.0000%" -> "Cola +3%"."""
        assert svc.clean_raise_label("COLA +3.0000%") == "Cola +3%"

    def test_percentage_shorter_precision(self):
        """A 2dp source Decimal ("+3.00%") trims identically to "+3%"."""
        # A hand-built Decimal("0.03") * 100 = Decimal("3.00") emits
        # "COLA +3.00%"; the trim is precision-agnostic.
        assert svc.clean_raise_label("COLA +3.00%") == "Cola +3%"

    def test_flat_amount_kept_to_the_cent(self):
        """Flat raises keep money formatting: "+$2,000.00" is untouched."""
        assert svc.clean_raise_label("MERIT +$2,000.00") == "Merit +$2,000.00"

    def test_multiple_events_each_cleaned(self):
        """Comma-joined events clean independently; the flat-amount
        thousands comma (",0", no space) does not split the label."""
        raw = "COLA +$2,000.00, MERIT +3.0000%"
        assert svc.clean_raise_label(raw) == "Cola +$2,000.00, Merit +3%"

    def test_fallback_type_title_cased(self):
        """The null-raise_type fallback "RAISE" title-cases like any type."""
        assert svc.clean_raise_label("RAISE +10.0000%") == "Raise +10%"

    def test_empty_string_passthrough(self):
        """No raise event (empty string) stays empty."""
        assert svc.clean_raise_label("") == ""


class TestRaiseRunStarts:
    """raise_run_starts: the shared run-start seam (chip, chart, banner).

    The calculator badges the SAME raise_event on every paycheck of a raise
    month, so this collapses a run to its first period (P-SA1).  A run
    starts when the cleaned label is non-empty AND differs from the
    predecessor's; ``None`` predecessor means no predecessor (first period).
    """

    def test_no_event_is_not_a_start(self):
        """A period with no raise event never starts a run."""
        assert svc.raise_run_starts("", "COLA +3.0000%") is False
        assert svc.raise_run_starts("", None) is False

    def test_first_period_with_event_is_a_start(self):
        """A raise event with no predecessor (idx 0) starts a run."""
        assert svc.raise_run_starts("MERIT +2.5000%", None) is True

    def test_new_label_after_no_event_is_a_start(self):
        """The first raised paycheck after a non-raise period starts a run."""
        assert svc.raise_run_starts("COLA +3.0000%", "") is True

    def test_same_label_as_predecessor_is_not_a_start(self):
        """A later paycheck of the SAME run does not restart it.

        07/16 carries the same "COLA +3.0000%" the calculator badged on
        07/02, so it is inside the run, not a new start -- the exact
        banner-repeat case P-SA1 fixes.
        """
        assert svc.raise_run_starts("COLA +3.0000%", "COLA +3.0000%") is False

    def test_different_label_back_to_back_is_a_start(self):
        """Two different raises in adjacent periods are two runs."""
        assert svc.raise_run_starts("MERIT +2.5000%", "COLA +3.0000%") is True

    def test_same_label_after_a_gap_is_a_fresh_start(self):
        """Next year's COLA (same label, after a no-event gap) is a new run.

        The predecessor of the fresh COLA is the intervening non-raise
        period (empty event), so the label re-appearing starts a new run.
        """
        assert svc.raise_run_starts("COLA +3.0000%", "") is True

    def test_precision_agnostic_match(self):
        """Equal cleaned labels from different source precision are one run.

        "COLA +3.0000%" and "COLA +3.00%" both clean to "Cola +3%", so a
        precision change between adjacent periods is not a new run.
        """
        assert svc.raise_run_starts("COLA +3.0000%", "COLA +3.00%") is False


class TestRaiseRunStartPeriodIds:
    """raise_run_start_period_ids: run-start ids across the full ledger (P-SA1).

    The projection ledger renders every period, so it flags the raise badge
    only on each run's first paycheck -- this returns exactly those period ids.
    """

    def test_shared_scenario_single_raise(self):
        """The shared 7-period scenario has one raise, at period id 14.

        The only raise is the 07/01 merit (idx 4); its neighbours carry no
        event, so it is a one-period run and its id is the only run start.
        """
        assert svc.raise_run_start_period_ids(_scenario()) == {14}

    def test_collapses_multi_period_run_to_its_first(self):
        """A 3-period COLA run + a following merit yields only the two starts.

        The calculator badges "COLA +3.0000%" on three consecutive periods (a
        July COLA hitting 07/02, 07/16, 07/30), then a January merit.  The set
        holds only the first COLA period id (51) and the merit id (55) -- the
        two run STARTS -- not the two COLA continuations (52, 53).
        """
        pairs = [
            _pair(50, date(2026, 6, 1), date(2026, 6, 14), "50000", "1900", "1500"),
            _pair(51, date(2026, 7, 2), date(2026, 7, 15), "51500", "1957", "1545",
                  raise_event="COLA +3.0000%"),
            _pair(52, date(2026, 7, 16), date(2026, 7, 29), "51500", "1957", "1545",
                  raise_event="COLA +3.0000%"),
            _pair(53, date(2026, 7, 30), date(2026, 8, 12), "51500", "1957", "1545",
                  raise_event="COLA +3.0000%"),
            _pair(54, date(2026, 8, 13), date(2026, 8, 26), "51500", "1957", "1545"),
            _pair(55, date(2027, 1, 7), date(2027, 1, 20), "52800", "2007", "1584",
                  raise_event="MERIT +2.5000%"),
        ]
        assert svc.raise_run_start_period_ids(pairs) == {51, 55}

    def test_no_raise_events_is_empty(self):
        """A ledger with no raise events yields the empty set."""
        pairs = [
            _pair(60, date(2026, 5, 1), date(2026, 5, 14), "50000", "1900", "1500"),
            _pair(61, date(2026, 5, 15), date(2026, 5, 28), "50000", "1900", "1500"),
        ]
        assert svc.raise_run_start_period_ids(pairs) == set()


class TestBaseRegularNet:
    """base_regular_net: nearest regular paycheck at the same salary."""

    def test_prefers_previous_regular_same_salary(self):
        """The idx-1 third paycheck resolves to idx-0's regular net (prev)."""
        pairs = _scenario()
        # idx 1 is a third paycheck at salary A; nearest regular same-salary
        # preferring previous is idx 0 => net 1400.00.
        assert svc.base_regular_net(pairs, 1) == Decimal("1400.00")

    def test_falls_back_to_next_when_no_previous(self):
        """A leading third paycheck falls forward to the next regular net."""
        pairs = [
            _pair(1, date(2026, 5, 1), date(2026, 5, 14), "52000", "2000", "1600",
                  is_third=True),
            _pair(2, date(2026, 5, 15), date(2026, 5, 28), "52000", "2000", "1400"),
        ]
        # No earlier period; next regular same salary is idx 1 => 1400.00.
        assert svc.base_regular_net(pairs, 0) == Decimal("1400.00")


class TestNextEvents:
    """next_raise_after / next_third_after strictly after today."""

    def test_next_raise_after_today(self):
        """First raise event after 2026-06-05 is the 2026-07-01 merit step.

        The raw calculator label "MERIT +2.5000%" is display-cleaned:
        "MERIT" title-cases to "Merit"; "2.5000" trims trailing zeros to
        "2.5" -> "Merit +2.5%".
        """
        result = svc.next_raise_after(_scenario(), TODAY)
        assert result == {
            "label": "Merit +2.5%",
            "period_start": date(2026, 7, 1),
        }

    def test_next_third_after_today_delta(self):
        """Next third paycheck (2026-11-01) delta = 1650 - base 1450 = 200."""
        result = svc.next_third_after(_scenario(), TODAY)
        # base regular net at salary B preferring previous is idx 5 (1450);
        # delta = 1650.00 - 1450.00 = 200.00.
        assert result == {
            "period_start": date(2026, 11, 1),
            "net": Decimal("1650.00"),
            "delta": Decimal("200.00"),
        }

    def test_next_raise_none_when_no_future_raise(self):
        """No raise after today returns None."""
        pairs = [_pair(1, date(2026, 1, 1), date(2026, 1, 14), "52000", "2000", "1400")]
        assert svc.next_raise_after(pairs, TODAY) is None

    def test_next_raise_skips_run_started_on_or_before_today(self):
        """Live-data shape: today 07/03, July COLA run 07/02-07/30 -> Jan Merit.

        The calculator badges EVERY period of a raise month, so 07/16 and
        07/30 carry the same "COLA +3.0000%" as 07/02.  That run STARTED
        07/02 (on/before today 07/03), so the raise already landed and the
        whole run is skipped -- its 07/16 / 07/30 tails must NOT surface as
        "the next raise".  The honest next raise is the Jan 2027 Merit
        run's first period.
        """
        live_today = date(2026, 7, 3)
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(3, date(2026, 7, 30), date(2026, 8, 12), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(4, date(2026, 8, 13), date(2026, 8, 26), "94425.25", "3631.74", "2650"),
            _pair(5, date(2027, 1, 7), date(2027, 1, 20), "96786", "3722.54", "2710",
                  raise_event="MERIT +2.5000%"),
        ]
        result = svc.next_raise_after(pairs, live_today)
        assert result == {
            "label": "Merit +2.5%",
            "period_start": date(2027, 1, 7),
        }

    def test_next_raise_future_run_returns_its_start(self):
        """A future run returns its FIRST period even with badged tails."""
        pairs = [
            _pair(1, date(2026, 8, 1), date(2026, 8, 14), "52000", "2000", "1400",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 8, 15), date(2026, 8, 28), "52000", "2000", "1400",
                  raise_event="COLA +3.0000%"),
        ]
        # Both periods start after TODAY (2026-06-05); only the run START
        # (08/01) is a candidate, never the 08/15 tail.
        result = svc.next_raise_after(pairs, TODAY)
        assert result == {
            "label": "Cola +3%",
            "period_start": date(2026, 8, 1),
        }


class TestYearlyNetTotals:
    """yearly_net_totals sums net per calendar year, ordered."""

    def test_single_year_total(self):
        """All 7 nets fall in 2026: 1400+1600+1400+1400+1450+1450+1650 = 10350."""
        result = svc.yearly_net_totals(_scenario())
        assert result == [(2026, Decimal("10350.00"))]

    def test_multi_year_ordered(self):
        """Two years sum independently and sort ascending by year."""
        pairs = [
            _pair(1, date(2026, 12, 20), date(2027, 1, 2), "52000", "2000", "1400"),
            _pair(2, date(2027, 1, 3), date(2027, 1, 16), "52000", "2000", "1450"),
        ]
        # 2026: 1400.00; 2027: 1450.00 (period 1 is keyed by its 2026 start).
        assert svc.yearly_net_totals(pairs) == [
            (2026, Decimal("1400.00")),
            (2027, Decimal("1450.00")),
        ]


class TestBuildChips:
    """build_chips: focused-period hero figures + next-event chips."""

    def test_chips_from_focused_period(self):
        """Chips read gross/annual/take-home off the focused breakdown."""
        pairs = _scenario()
        focused = pairs[2][1]  # idx 2, salary A, gross 2000, net 1400
        chips = svc.build_chips(pairs, focused, TODAY)
        assert chips["gross"] == Decimal("2000")
        assert chips["annual_salary"] == Decimal("52000")
        # take-home = net/gross*100 = 1400/2000*100 = 70.
        assert chips["take_home_rate_pct"] == Decimal("70")
        # Label display-cleaned: "MERIT +2.5000%" -> "Merit +2.5%".
        assert chips["next_raise"] == {
            "label": "Merit +2.5%",
            "period_start": date(2026, 7, 1),
        }
        # third-paycheck chip carries only period_start + delta (200.00).
        assert chips["third_paycheck"] == {
            "period_start": date(2026, 11, 1),
            "delta": Decimal("200.00"),
        }


class TestBuildComposition:
    """build_composition: segment totals + percentages of gross."""

    def test_percentages(self):
        """gross 2000 -> net 70%, pre-tax 10%, taxes 15%, post-tax 5%."""
        breakdown = _scenario()[2][1]  # gross 2000, net 1400, pre 200, tax 300, post 100
        comp = svc.build_composition(breakdown)
        assert comp["gross"] == Decimal("2000")
        assert comp["taxable"] == Decimal("1800")
        assert comp["net"] == Decimal("1400")
        assert comp["pre_tax_total"] == Decimal("200")
        assert comp["taxes_total"] == Decimal("300")  # 0 + 150 + 124 + 26
        assert comp["post_tax_total"] == Decimal("100")
        # of gross 2000: 1400->70.0, 200->10.0, 300->15.0, 100->5.0 (sum 100.0).
        assert comp["pct_net"] == Decimal("70.0")
        assert comp["pct_pre_tax"] == Decimal("10.0")
        assert comp["pct_taxes"] == Decimal("15.0")
        assert comp["pct_post_tax"] == Decimal("5.0")

    def test_names_the_stub_that_priced_the_taxes(self):
        """The card carries the engine's pricing stub, read off the tax lines (R-SAL100)."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            taxable="1800", state="150", ss="124", medicare="26",
            stub_payday=date(2026, 5, 15),
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["tax_stub_payday"] == date(2026, 5, 15)

    def test_names_the_formulas_when_no_stub_priced_the_taxes(self):
        """No stub on or before the payday: the key is None, the formulas' answer."""
        comp = svc.build_composition(_scenario()[2][1])
        assert comp["tax_stub_payday"] is None

    def test_zero_gross_percentages_are_zero(self):
        """A zero-gross period yields 0% segments (no division by zero)."""
        breakdown = _pair(1, date(2026, 1, 1), date(2026, 1, 14), "0", "0", "0")[1]
        comp = svc.build_composition(breakdown)
        assert comp["pct_net"] == Decimal("0")

    def test_no_earning_line_leaves_the_card_as_it_was(self):
        """Without an earning line the added keys are zero or equal net, and pct_net is of net."""
        comp = svc.build_composition(_scenario()[2][1])
        assert comp["base"] == Decimal("2000")
        assert comp["taxable_earnings_total"] == Decimal("0")
        assert comp["after_tax_total"] == Decimal("0")
        assert comp["kept_from_gross"] == comp["net"] == Decimal("1400")
        assert comp["pct_net"] == Decimal("70.0")

    def test_a_taxable_earning_is_inside_gross_and_the_split_is_stated(self):
        """gross 2000 = base 1900 + a $100 taxable line; the spine is unchanged, the footer knows."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            taxable="1800", state="150", ss="124", medicare="26",
            pre=[("401k", "200")], post=[("Roth", "100")],
            taxable_lines=[("Phone Allowance", "100")],
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["gross"] == Decimal("2000")
        assert comp["base"] == Decimal("1900")
        assert comp["taxable_earnings_total"] == Decimal("100")
        assert comp["kept_from_gross"] == comp["net"] == Decimal("1400")
        assert (comp["pct_net"], comp["pct_pre_tax"], comp["pct_taxes"], comp["pct_post_tax"]) == (
            Decimal("70.0"), Decimal("10.0"), Decimal("15.0"), Decimal("5.0"),
        )

    def test_an_after_tax_earning_sits_outside_gross_and_the_segments_still_sum(self):
        """net 1500 = 1400 kept of a 2000 gross + a $100 after-tax line: the net segment is 70.0, not 75.0."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1500",
            taxable="1800", state="150", ss="124", medicare="26",
            pre=[("401k", "200")], post=[("Roth", "100")],
            after_tax=[("Reimbursement", "100")],
        )[1]
        comp = svc.build_composition(breakdown)
        assert comp["net"] == Decimal("1500")
        assert comp["after_tax_total"] == Decimal("100")
        assert comp["kept_from_gross"] == Decimal("1400")
        assert comp["pct_net"] == Decimal("70.0")
        # The four segments of gross still sum to the bar.
        assert (comp["pct_net"] + comp["pct_pre_tax"] + comp["pct_taxes"]
                + comp["pct_post_tax"]) == Decimal("100.0")


class TestBuildLineRows:
    """build_line_rows: proportional bars scaled to the largest line, kind groups in waterfall order.

    Each row's ``group_label`` is the heading over its kind's group (plan step
    salary:S11-e, which split the pre-tax kind and gave the headings a map of
    their own; ruling plan#2639 names the two pre-tax labels).
    """

    def test_rows_scaled_to_largest(self):
        """401k 200 (largest) -> 100.0; Roth 100 -> 50.0."""
        breakdown = _scenario()[2][1]
        rows = svc.build_line_rows(breakdown)
        assert rows == [
            {"name": "401k", "amount": Decimal("200"), "kind": "pre_tax_deduction",
             "group_label": "Pre-tax, income tax only", "bar_pct": Decimal("100.0")},
            {"name": "Roth", "amount": Decimal("100"), "kind": "post_tax_deduction",
             "group_label": "Post-tax deductions", "bar_pct": Decimal("50.0")},
        ]

    def test_empty_when_no_lines(self):
        """A period with no lines returns an empty list."""
        assert svc.build_line_rows(_scenario()[0][1]) == []

    def test_earning_groups_bracket_the_deductions_in_waterfall_order(self):
        """Taxable earnings first, after-tax earnings last; bars scale across every group."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2045", "1500",
            pre=[("401k", "200")], post=[("Roth", "100")],
            taxable_lines=[("Phone Allowance", "45")],
            after_tax=[("Reimbursement", "400")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        assert [(r["name"], r["kind"], r["group_label"], r["bar_pct"]) for r in rows] == [
            ("Phone Allowance", "taxable_earning", "Taxable earnings", Decimal("11.3")),
            ("401k", "pre_tax_deduction", "Pre-tax, income tax only", Decimal("50.0")),
            ("Roth", "post_tax_deduction", "Post-tax deductions", Decimal("25.0")),
            ("Reimbursement", "after_tax_earning", "After-tax earnings", Decimal("100.0")),
        ]

    def test_rows_sorted_desc_within_kind_groups(self):
        """Each kind group sorts amount-descending; pre-tax group first.

        Live-shape input order (calculator order): FSA 50, Vision 10,
        Dental 30, Health 200 (pre-tax); Roth 100, Life 150 (post-tax).
        Expected: Health 200, FSA 50, Dental 30, Vision 10, then Life 150,
        Roth 100.  bar_pct scales to the overall max 200:
          Health 200/200*100 = 100.0   FSA    50/200*100 = 25.0
          Dental  30/200*100 =  15.0   Vision 10/200*100 =  5.0
          Life   150/200*100 =  75.0   Roth  100/200*100 = 50.0
        """
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("FSA", "50"), ("Vision", "10"), ("Dental", "30"), ("Health", "200")],
            post=[("Roth", "100"), ("Life", "150")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        assert [(r["name"], r["amount"], r["kind"], r["bar_pct"]) for r in rows] == [
            ("Health", Decimal("200"), "pre_tax_deduction", Decimal("100.0")),
            ("FSA", Decimal("50"), "pre_tax_deduction", Decimal("25.0")),
            ("Dental", Decimal("30"), "pre_tax_deduction", Decimal("15.0")),
            ("Vision", Decimal("10"), "pre_tax_deduction", Decimal("5.0")),
            ("Life", Decimal("150"), "post_tax_deduction", Decimal("75.0")),
            ("Roth", Decimal("100"), "post_tax_deduction", Decimal("50.0")),
        ]

    def test_equal_amounts_keep_stable_calculator_order(self):
        """Ties within a group keep the calculator's original order."""
        breakdown = _pair(
            1, date(2026, 6, 1), date(2026, 6, 14), "52000", "2000", "1400",
            pre=[("Alpha", "25"), ("Beta", "25"), ("Gamma", "50")],
        )[1]
        rows = svc.build_line_rows(breakdown)
        # Gamma (50) leads; Alpha and Beta tie at 25 and keep their
        # calculator order (sorted() is stable).
        assert [r["name"] for r in rows] == ["Gamma", "Alpha", "Beta"]


class TestBuildChartSeries:
    """build_chart_series: staircase line with the third-paycheck base rule."""

    def test_line_carries_base_thirds_and_raises(self):
        """Third periods carry the base net on the line; spikes go to thirds."""
        series = svc.build_chart_series(_scenario(), TODAY)
        # Window: anchor idx 2 (contains today), lookback 6 -> start 0,
        # horizon +18mo covers all 7 periods.
        starts = [pt["start"] for pt in series["periods"]]
        assert starts == [
            date(2026, 5, 1), date(2026, 5, 15), date(2026, 6, 1),
            date(2026, 6, 15), date(2026, 7, 1), date(2026, 7, 15),
            date(2026, 11, 1),
        ]
        # idx 1 (third) carries base regular net 1400.00, NOT its 1600 spike;
        # idx 6 (third) carries base 1450.00, NOT 1650.
        assert series["periods"][1]["net"] == Decimal("1400.00")
        assert series["periods"][6]["net"] == Decimal("1450.00")
        # The actual third-paycheck nets are the point events.
        assert series["thirds"] == [
            {"start": date(2026, 5, 15), "net": Decimal("1600")},
            {"start": date(2026, 11, 1), "net": Decimal("1650")},
        ]
        # The one raise event, display-cleaned ("MERIT +2.5000%" ->
        # "Merit +2.5%").
        assert series["raises"] == [
            {"start": date(2026, 7, 1), "label": "Merit +2.5%"},
        ]
        assert series["today"] == TODAY

    def test_lookback_clamps_and_horizon_bounds(self):
        """Periods past the 18-month horizon are excluded."""
        pairs = _scenario() + [
            _pair(99, date(2028, 6, 1), date(2028, 6, 14), "54000", "2076.92", "1450"),
        ]
        series = svc.build_chart_series(pairs, TODAY)
        # 2028-06-01 is beyond 2026-06-05 + 18 months (2027-12-05) -> excluded.
        assert all(pt["start"].year < 2028 for pt in series["periods"])

    def test_raise_run_collapsed_to_one_entry(self):
        """Three consecutive periods with one COLA emit ONE raises[] entry.

        The live 07/02-07/30 shape: the calculator badges all three July
        periods with "COLA +3.0000%"; the chart must paint one label at
        the run start (07/02), not three stacked ones.
        """
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(3, date(2026, 7, 30), date(2026, 8, 12), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(4, date(2026, 8, 13), date(2026, 8, 26), "94425.25", "3631.74", "2650"),
        ]
        series = svc.build_chart_series(pairs, date(2026, 7, 3))
        assert series["raises"] == [
            {"start": date(2026, 7, 2), "label": "Cola +3%"},
        ]

    def test_adjacent_different_raises_are_two_runs(self):
        """A label change starts a new run: back-to-back COLA then Merit."""
        pairs = [
            _pair(1, date(2026, 7, 2), date(2026, 7, 15), "94425.25", "3631.74", "2650",
                  raise_event="COLA +3.0000%"),
            _pair(2, date(2026, 7, 16), date(2026, 7, 29), "96786", "3722.54", "2710",
                  raise_event="MERIT +2.5000%"),
        ]
        series = svc.build_chart_series(pairs, date(2026, 7, 3))
        assert series["raises"] == [
            {"start": date(2026, 7, 2), "label": "Cola +3%"},
            {"start": date(2026, 7, 16), "label": "Merit +2.5%"},
        ]


class TestBuildSalaryPath:
    """build_salary_path: forward-only annual-salary staircase + end label."""

    def test_forward_only_points_and_end_label(self):
        """Forward window from the anchor; end label formats the last point."""
        path = svc.build_salary_path(_scenario(), TODAY)
        # lookback 0 -> starts at anchor idx 2 (contains today).
        starts = [pt["start"] for pt in path["points"]]
        assert starts == [
            date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1),
            date(2026, 7, 15), date(2026, 11, 1),
        ]
        assert path["points"][0]["annual"] == Decimal("52000")
        assert path["points"][-1]["annual"] == Decimal("54000")
        # end label = last point: $54,000 at Nov 2026.
        assert path["end_label"] == "$54,000 Nov 2026"

    def test_empty_window_empty_label(self):
        """No pairs -> no points and an empty end label."""
        path = svc.build_salary_path([], TODAY)
        assert path == {"points": [], "end_label": ""}
