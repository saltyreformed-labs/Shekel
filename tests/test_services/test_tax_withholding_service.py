"""
Shekel Budget App -- YTD Tax Withholding Service Tests (T-P2)

Hand-confirmed assertions for ``tax_withholding_service``: the checkpoint
CRUD (``latest_checkpoint`` / ``save_checkpoint``) and the
withholding-to-date producer (``compute_withholding_to_date``).

The producer's rule is "measured checkpoint + calibrated projection for the
remainder" (the audit's words; the projection prices each paycheck's taxes
from the profile's pay stubs since plan step salary:S11-c-2c, which deleted
the calibration), computed with FULL-YEAR engine context: the projection runs
over the entire year's period list (so cumulative wages drive the SS
wage-base cap and the Medicare surtax threshold, and month grouping drives
monthly-capped deductions) and only the remainder periods' breakdowns are
summed.  It re-implements no tax math -- the modeled remainder is
delegated to ``paycheck_calculator.project_salary`` -- so the arithmetic
these tests pin is:

* the period PARTITION (which periods are measured vs modeled), verified by
  comparing the producer's projected side against a ``project_salary`` call
  over the hand-determined remainder subset.  **What that oracle is
  independent OF changed at plan step balance:X-bh-1** and this paragraph
  said the opposite until an adversarial review of that step: it used to
  project the remainder ALONE, with its cumulative context restarting at
  zero where the producer carried full-year state, and the two coincided
  only because these scenarios sit far below the 2026 SS wage base
  (184,500) and Medicare surtax threshold (200,000) with no deductions.
  The engine now takes that context off the owner's CALENDAR, which neither
  side can narrow, so the cumulative restarts nowhere and the two agree by
  construction.  What the oracle still checks independently is the SUBSET:
  it is HANDED the periods to sum, where the producer derives its own from
  the checkpoint.  ``TestFullYearCapContext`` pins the above-cap case with
  absolute hand-computed dollars and is what grades the context itself;
* the SPLIT IDENTITY ``total == measured + projected`` (component-wise);
* the measured side taken VERBATIM from the checkpoint;
* the gross figure, which is exactly computable (salary 130,000 / 26 =
  5,000.00 per period, no rounding residue); and
* the four withholding lines priced from a PAY STUB of the paycheck's own
  lines and pay, where each line is the stub's figure and the formulas'
  difference is ``$0.00`` -- hand-computed in the test docstring (the
  CALIBRATION path's ``round(base * rate)`` until plan step
  salary:S11-c-2c deleted it).

Pay periods come from ``seed_periods`` (10 biweekly periods, all in 2026,
starting 2026-01-02 with a 14-day cadence):

    P0 01-02  P1 01-16  P2 01-30  P3 02-13  P4 02-27
    P5 03-13  P6 03-27  P7 04-10  P8 04-24  P9 05-08
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import WithholdingKindEnum
from app.extensions import db as _db
from app.models.pay_period import PayPeriod
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.services import paycheck_calculator
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import calendar_for
from app.services.payroll_basis import PayrollBasis
from tests._test_helpers import add_test_pay_stub, start_test_pay_list

from app.services.tax_withholding_service import (
    CheckpointFigures,
    year_paydays,
    WithholdingToDate,
    compute_withholding_to_date,
    latest_checkpoint,
    save_checkpoint,
)

ZERO = Decimal("0")


def _make_profile(
    seed_user, name="Withholding Test Profile",
    pay="5000.00",  # $130,000.00 a year / 26
):
    """Build and flush an active single/NC SalaryProfile.

    Default salary 130,000 (5,000.00/period at 26, no residue); the
    cap-context test passes 260,000 (10,000.00/period) to cross the 2026
    SS wage base mid-year.
    """
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    _db.session.add(profile)
    # From the 2026 calendar's first payday (P0 above): saved under
    # ``seed_periods``, and a PROJECTED payday off the seed owner's 2024
    # bootstrap (52 x 14 days on) where a case builds its periods after the
    # profile or builds none.
    start_test_pay_list(profile, Decimal(pay), date(2026, 1, 2))
    _db.session.flush()
    return profile


def _committed_profile(seed_user, **kwargs):
    """Build and flush a profile; it prices under the shipped law."""
    profile = _make_profile(seed_user, **kwargs)
    _db.session.flush()
    return profile


def _make_full_year_periods(user, count=26, start=date(2026, 1, 2)):
    """Create *count* biweekly pay periods directly (period_index 1..count).

    26 periods from 2026-01-02 span P0 01-02 .. P25 12-18 -- a full 26-pay
    calendar year (the seed_user bootstrap period sits at index 0 in 2024
    and is never passed to the producer).  Built directly rather than via
    ``seed_periods`` (which creates only 10) because the cap-context test
    needs the SS wage-base crossing at paycheck 19.
    """
    periods = []
    for i in range(count):
        start_date = start + timedelta(days=14 * i)
        period = PayPeriod(user_id=user.id, start_date=start_date)
        _db.session.add(period)
        periods.append(period)
    _db.session.flush()
    return periods



def _derived(user_id, year=2026):
    """The year's pay periods AS THE PRODUCER SEES THEM.

    ``compute_withholding_to_date`` takes
    :class:`~app.services.pay_calendar.DerivedPeriod` values since pay-calendar
    plan step C2-f2d-3, and its production caller
    (``tax_report_service._load_year_periods``) builds them exactly this way --
    the owner's saved calendar filtered to the tax year.  Handing the ORM rows
    the fixtures create would let these cases pass against a shape no caller
    supplies.
    """
    return tuple(
        period for period in calendar_for(user_id).saved()
        if period.start_date.year == year
    )


def _oracle_basis(seed_user, profile):
    """The oracle's :class:`PayrollBasis` -- the owner's REAL calendar.

    The producer counts paydays off ``basis.calendar`` for the FICA wage-base
    cumulative and the deduction cadence since plan step **balance:X-bh-1**, so
    an oracle built on a narrower schedule would differ from it for a reason
    that has nothing to do with the partition these cases grade.  What stays
    independent is the SUBSET each side is asked to price: the oracle is handed
    an explicit period list and sums all of it, where the producer derives its
    own from the checkpoint.
    """
    return PayrollBasis(profile, calendar_for(seed_user["user"].id))


def _add_checkpoint(profile, as_of_date, **figures):
    """Insert a checkpoint directly (independent of ``save_checkpoint``)."""
    defaults = {
        "ytd_gross": Decimal("40000.00"),
        "ytd_federal": Decimal("4000.00"),
        "ytd_state": Decimal("1500.00"),
        "ytd_social_security": Decimal("2480.00"),
        "ytd_medicare": Decimal("580.00"),
    }
    defaults.update(figures)
    cp = YtdTaxCheckpoint(
        salary_profile_id=profile.id, as_of_date=as_of_date, **defaults,
    )
    _db.session.add(cp)
    _db.session.flush()
    return cp


def _expected_projected(basis, year, periods):
    """Sum ``project_salary`` over *periods* -- the independent oracle.

    Same configs SSOT and engine as the producer, over the subset it is
    HANDED -- which is the whole of what it checks independently, and is
    what the producer derives for itself from the checkpoint.  One year's
    law, where the producer resolves it per year (ruling **R-SAL77**, plan
    step salary:S11-c-2c): the two agree on a year's own periods, and only a
    pay stub of another year could part them, which no case using this
    oracle seeds.

    **Its cumulative context no longer restarts at zero**, and this docstring
    claimed it did until an adversarial review of plan step
    **balance:X-bh-1**: the engine reads the year's paydays off
    ``basis.calendar`` rather than off the list it is passed, so a subset
    projection carries the same year-to-date state a full-year one does.
    That removes a divergence rather than hiding one -- the case where the
    two USED to differ (a high earner crossing the SS cap mid-year) is
    pinned with absolute hand-computed dollars in ``TestFullYearCapContext``,
    which is where the context is graded.
    """
    configs = load_tax_configs_for_year(basis.profile, year)
    breakdowns = paycheck_calculator.project_salary(basis, periods, configs)
    return {
        "gross": sum((b.earnings.gross_biweekly for b in breakdowns), ZERO),
        "federal": sum((b.taxes.federal for b in breakdowns), ZERO),
        "state": sum((b.taxes.state for b in breakdowns), ZERO),
        "social_security": sum(
            (b.taxes.social_security for b in breakdowns), ZERO,
        ),
        "medicare": sum((b.taxes.medicare for b in breakdowns), ZERO),
    }


def _assert_projected_equals(projected, expected):
    """Assert a WithholdingComponents matches an oracle dict component-wise."""
    assert projected.gross == expected["gross"]
    assert projected.federal == expected["federal"]
    assert projected.state == expected["state"]
    assert projected.social_security == expected["social_security"]
    assert projected.medicare == expected["medicare"]


class TestLatestCheckpoint:
    """latest_checkpoint returns the max in-year as_of_date, or None."""

    def test_returns_max_as_of_date_in_year(self, app, db, seed_user):
        """Two 2026 checkpoints -> the later-dated one is returned."""
        profile = _committed_profile(seed_user)
        _add_checkpoint(profile, date(2026, 2, 1), ytd_gross=Decimal("10000.00"))
        march = _add_checkpoint(
            profile, date(2026, 3, 1), ytd_gross=Decimal("20000.00"),
        )
        db.session.commit()

        result = latest_checkpoint(profile.id, 2026)
        assert result is not None
        assert result.id == march.id
        assert result.as_of_date == date(2026, 3, 1)

    def test_none_when_no_checkpoint_in_year(self, app, db, seed_user):
        """A 2025-dated checkpoint is not a candidate for year 2026."""
        profile = _committed_profile(seed_user)
        _add_checkpoint(profile, date(2025, 12, 20))
        db.session.commit()
        assert latest_checkpoint(profile.id, 2026) is None

    def test_ignores_other_profiles_checkpoint(self, app, db, seed_user):
        """A different profile's checkpoint is never returned."""
        profile_a = _committed_profile(seed_user, name="Profile A")
        profile_b = _make_profile(seed_user, name="Profile B")
        _add_checkpoint(profile_b, date(2026, 4, 1))
        db.session.commit()
        assert latest_checkpoint(profile_a.id, 2026) is None


class TestSaveCheckpoint:
    """save_checkpoint upserts on (profile, as_of_date)."""

    def test_insert_new_date(self, app, db, seed_user):
        """A new date inserts a row with the given figures."""
        profile = _committed_profile(seed_user)
        db.session.commit()
        figures = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
            notes="mid-year stub",
        )
        cp = save_checkpoint(profile.id, figures)
        db.session.commit()

        assert cp.id is not None
        assert cp.ytd_gross == Decimal("60000.00")
        assert cp.notes == "mid-year stub"
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 1

    def test_resave_same_date_updates_in_place(self, app, db, seed_user):
        """Re-entering the same date REPLACES the row (no second row)."""
        profile = _committed_profile(seed_user)
        db.session.commit()
        base = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        )
        first = save_checkpoint(profile.id, base)
        db.session.commit()
        first_id = first.id

        corrected = CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("61000.00"),
            ytd_federal=Decimal("6100.00"),
            ytd_state=Decimal("2450.00"),
            ytd_social_security=Decimal("3782.00"),
            ytd_medicare=Decimal("884.50"),
            notes="corrected",
        )
        second = save_checkpoint(profile.id, corrected)
        db.session.commit()

        assert second.id == first_id  # same row updated
        assert second.ytd_gross == Decimal("61000.00")
        assert second.notes == "corrected"
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 1

    def test_new_date_inserts_second_row(self, app, db, seed_user):
        """A different date inserts a second row (history-keeping)."""
        profile = _committed_profile(seed_user)
        db.session.commit()
        save_checkpoint(profile.id, CheckpointFigures(
            as_of_date=date(2026, 3, 31),
            ytd_gross=Decimal("30000.00"),
            ytd_federal=Decimal("3000.00"),
            ytd_state=Decimal("1200.00"),
            ytd_social_security=Decimal("1860.00"),
            ytd_medicare=Decimal("435.00"),
        ))
        save_checkpoint(profile.id, CheckpointFigures(
            as_of_date=date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        ))
        db.session.commit()
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter_by(salary_profile_id=profile.id)
            .count()
        )
        assert count == 2


class TestComputeNoCheckpoint:
    """With no checkpoint the whole year is modeled; measured is zero."""

    def test_fully_modeled_sum(self, app, db, seed_user, seed_periods):
        """No checkpoint -> measured zeros, remainder = all 10 periods.

        Gross is exact: 130,000 / 26 = 5,000.00 per period, so
        projected.gross = 5,000.00 * 10 = 50,000.00.  The four withholding
        lines are delegated to project_salary and verified against the
        independent oracle over the same 10 periods.  total == projected
        (measured is zero).
        """
        profile = _committed_profile(seed_user)
        db.session.commit()

        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )
        expected = _expected_projected(
            _oracle_basis(seed_user, profile), 2026,
            _derived(seed_user["user"].id),
        )

        assert isinstance(result, WithholdingToDate)
        assert result.checkpoint is None
        assert result.measured_through is None
        # Measured side is all zeros.
        assert result.measured.gross == ZERO
        assert result.measured.federal == ZERO
        # Projected == oracle over all 10 periods.
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 10
        # total == measured + projected == projected (measured zero).
        assert result.total.gross == result.projected.gross
        assert result.total.federal == result.projected.federal
        assert result.total.state == result.projected.state
        assert result.total.social_security == result.projected.social_security
        assert result.total.medicare == result.projected.medicare


class TestComputeWithCheckpoint:
    """A mid-year checkpoint splits measured (verbatim) from modeled remainder."""

    def test_mid_period_split(self, app, db, seed_user, seed_periods):
        """Checkpoint dated 2026-01-15 (mid-P0, after P0's 01-02 payday).

        P0's payday (01-02) is on/before the stub date, so P0 is MEASURED
        (its paycheck is in the checkpoint, not double-counted).  The
        remainder is P1..P9 (start_date > 2026-01-15), i.e. 9 periods.

        Measured = the checkpoint's five figures verbatim.
        projected.gross = 4,812.34 * 9 = 43,311.06; projected withholding ==
        oracle over P1..P9.  total.<line> = measured + projected.

        Made-up figures, swapped at plan step salary:S11-c-2c (strict ruling
        balance:R-BAL132): this case paid the module's $5,000.00 and measured
        6.2% of it, a production salary amount.  It now pays ``$4,812.34``
        and measures made-up figures that are no tax rate of it (a
        checkpoint's figures are whatever the stub printed; none is 10%, 4%,
        6.2% or 1.45% of the base).
        """
        profile = _committed_profile(seed_user, pay="4812.34")
        cp = _add_checkpoint(
            profile, date(2026, 1, 15),
            ytd_gross=Decimal("4812.34"),
            ytd_federal=Decimal("503.17"),
            ytd_state=Decimal("186.42"),
            ytd_social_security=Decimal("301.66"),
            ytd_medicare=Decimal("71.03"),
        )
        db.session.commit()

        remainder = _derived(seed_user["user"].id)[1:]  # P1..P9
        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )
        expected = _expected_projected(
            _oracle_basis(seed_user, profile), 2026, remainder,
        )

        assert result.checkpoint is not None
        assert result.checkpoint.id == cp.id
        assert result.measured_through == date(2026, 1, 15)
        # Measured taken verbatim from the checkpoint.
        assert result.measured.gross == Decimal("4812.34")
        assert result.measured.federal == Decimal("503.17")
        assert result.measured.state == Decimal("186.42")
        assert result.measured.social_security == Decimal("301.66")
        assert result.measured.medicare == Decimal("71.03")
        # Projected == oracle over P1..P9 (P0 excluded).
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("4812.34") * 9
        # Split identity, component-wise.
        assert result.total.gross == Decimal("4812.34") + expected["gross"]
        assert result.total.federal == Decimal("503.17") + expected["federal"]
        assert result.total.state == Decimal("186.42") + expected["state"]
        assert (
            result.total.social_security
            == Decimal("301.66") + expected["social_security"]
        )
        assert result.total.medicare == Decimal("71.03") + expected["medicare"]

    def test_on_payday_boundary(self, app, db, seed_user, seed_periods):
        """Checkpoint dated exactly ON P1's payday (2026-01-16).

        ``start_date > as_of_date`` is STRICT, so P1 (start 01-16) is NOT in
        the remainder -- its paycheck is covered by the stub.  Remainder is
        P2..P9 (8 periods); projected.gross = 5,000.00 * 8 = 40,000.00.
        """
        profile = _committed_profile(seed_user)
        _add_checkpoint(profile, date(2026, 1, 16))
        db.session.commit()

        remainder = _derived(seed_user["user"].id)[2:]  # P2..P9
        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )
        expected = _expected_projected(
            _oracle_basis(seed_user, profile), 2026, remainder,
        )

        assert result.measured_through == date(2026, 1, 16)
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 8

    def test_different_year_checkpoint_ignored(
        self, app, db, seed_user, seed_periods,
    ):
        """A 2025 checkpoint is ignored for year 2026 -> fully modeled."""
        profile = _committed_profile(seed_user)
        _add_checkpoint(profile, date(2025, 12, 20))
        db.session.commit()

        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )
        expected = _expected_projected(
            _oracle_basis(seed_user, profile), 2026,
            _derived(seed_user["user"].id),
        )

        assert result.checkpoint is None
        assert result.measured_through is None
        assert result.measured.gross == ZERO
        _assert_projected_equals(result.projected, expected)
        assert result.projected.gross == Decimal("5000.00") * 10


class TestComputeEmptyPeriods:
    """A year the schedule holds no payday in yields a zero remainder.

    **The empty case is now a YEAR the calendar does not reach, not an empty
    list a caller passes.**  Plan step **balance:X-bh-1** dropped the
    ``periods`` argument -- it was ``year_paydays(calendar, year)`` at every
    call site, a third argument encoding what ``year`` and ``calendar``
    already said -- so these cases ask about 2099, which the seeded schedule
    does not reach.  That is the state a real owner reaches; an empty list
    beside a populated calendar never was one.
    """

    def test_a_year_with_no_paydays_and_a_checkpoint(self, app, db, seed_user):
        """No paydays -> projected zeros; total == measured (the checkpoint)."""
        profile = _committed_profile(seed_user)
        _add_checkpoint(
            profile, date(2026, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        )
        db.session.commit()

        # The checkpoint is dated in 2026 and the question is asked of 2099,
        # so ``latest_checkpoint`` would scope it out -- it is re-added on the
        # year under test so the MEASURED side is non-zero and the assertion
        # below is about the remainder rather than about an empty profile.
        _add_checkpoint(
            profile, date(2099, 6, 30),
            ytd_gross=Decimal("60000.00"),
            ytd_federal=Decimal("6000.00"),
            ytd_state=Decimal("2400.00"),
            ytd_social_security=Decimal("3720.00"),
            ytd_medicare=Decimal("870.00"),
        )
        db.session.commit()

        result = compute_withholding_to_date(
            profile, 2099,
            BalanceContext.build(seed_user["user"].id),
        )
        assert year_paydays(calendar_for(seed_user["user"].id), 2099) == ()
        # Remainder is empty -> projected all zero.
        assert result.projected.gross == ZERO
        assert result.projected.federal == ZERO
        assert result.projected.state == ZERO
        assert result.projected.social_security == ZERO
        assert result.projected.medicare == ZERO
        # total == measured (checkpoint verbatim).
        assert result.total.gross == Decimal("60000.00")
        assert result.total.federal == Decimal("6000.00")
        assert result.total.medicare == Decimal("870.00")

    def test_a_year_with_no_paydays_and_no_checkpoint(self, app, db, seed_user):
        """No paydays AND no checkpoint -> everything zero."""
        profile = _committed_profile(seed_user)
        db.session.commit()
        assert year_paydays(calendar_for(seed_user["user"].id), 2099) == ()
        result = compute_withholding_to_date(
            profile, 2099,
            BalanceContext.build(seed_user["user"].id),
        )
        assert result.total.gross == ZERO
        assert result.total.federal == ZERO
        assert result.projected.gross == ZERO
        assert result.measured.gross == ZERO
        assert result.checkpoint is None


class TestStubPricedWithholding:
    """A pay stub gives hand-computable per-line withholding (plan step salary:S11-c-2c)."""

    def test_stub_priced_remainder_exact(self, app, db, seed_user, seed_periods):
        """A stub of the paycheck's lines and pay -> its own four taxes, exactly.

        RE-BASED by rulings **R-SAL42** and **R-SAL100** (plan step
        salary:S11-c-2c): this case seeded an ACTIVE calibration (effective
        rates 10% / 5% / 6.2% / 1.45%) until that path was deleted.  Its
        subject -- the remainder's per-line sums and the measured + projected
        identity -- is unchanged; the same dollars now come off a pay stub.

        The profile pays ``$4,812.34`` a paycheck and the stub, dated on the
        2026-04-10 payday, prints base pay 4,812.34 and no paycheck line --
        the profile holds none -- with federal 487.31, state 233.17, Social
        Security 298.43 and Medicare 69.79: made-up figures, no rate of the
        base (a stub's figures are its own), swapped at plan step
        salary:S11-c-2c, with the base, to equal no production salary amount
        (strict ruling balance:R-BAL132; this case paid the module's
        $5,000.00 and printed 6.2% of it, which equals one).
        Both modeled paychecks (04-24, 05-08) pay the same 4,812.34 with the
        same lines, so it prices both, and the formulas' difference between
        its paycheck and each is $0.00 on every line: federal and state
        annualise one paycheck's wages, which are equal, and the year-to-date
        before each (33,686.38 / 38,498.72 / 43,311.06) is far below the
        Social Security wage base and the Medicare surtax threshold.  Per
        paycheck the four taxes are the stub's; over the 2 modeled paychecks:
          gross 9,624.68; federal 974.62; state 466.34; medicare 139.58;
          SS 596.86.
        """
        profile = _committed_profile(seed_user, pay="4812.34")
        add_test_pay_stub(
            profile, date(2026, 4, 10), "4812.34",
            taxes={
                WithholdingKindEnum.FEDERAL_INCOME: "487.31",
                WithholdingKindEnum.STATE_INCOME: "233.17",
                WithholdingKindEnum.SOCIAL_SECURITY: "298.43",
                WithholdingKindEnum.MEDICARE: "69.79",
            },
        )
        db.session.commit()

        # TWO periods are modeled, and a CHECKPOINT is what leaves two --
        # this case sliced ``_derived(...)[:2]`` until plan step
        # **balance:X-bh-1** dropped the ``periods`` argument, which is the
        # right shape for it: in production nothing hands this function a
        # subset, the checkpoint date decides one.  The seeded year runs
        # 2026-01-02 every 14 days to 2026-05-08, so a stub dated 2026-04-15
        # measures through 04-10 and leaves 04-24 and 05-08 modeled.
        _add_checkpoint(
            profile, date(2026, 4, 15),
            ytd_gross=Decimal("38498.72"),
            ytd_federal=Decimal("3851.23"),
            ytd_state=Decimal("1925.61"),
            ytd_social_security=Decimal("2391.47"),
            ytd_medicare=Decimal("561.08"),
        )
        db.session.commit()

        remaining = [
            p for p in _derived(seed_user["user"].id)
            if p.start_date > date(2026, 4, 15)
        ]
        assert len(remaining) == 2, (
            "this case is about TWO modeled paychecks; the seeded schedule "
            f"leaves {len(remaining)} after 2026-04-15"
        )

        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )

        assert result.projected.gross == Decimal("9624.68")
        assert result.projected.federal == Decimal("974.62")
        assert result.projected.state == Decimal("466.34")
        assert result.projected.medicare == Decimal("139.58")
        assert result.projected.social_security == Decimal("596.86")
        # The split identity: total == measured + projected, component-wise
        # (federal 3,851.23 measured + 974.62 modeled; gross 38,498.72 +
        # 9,624.68).
        assert result.total.federal == Decimal("4825.85")
        assert result.total.gross == Decimal("48123.40")
        # Both modeled paychecks were priced from the 04-10 stub (R-SAL100).
        assert result.priced_from == (date(2026, 4, 10), date(2026, 4, 10))


class TestFullYearCapContext:
    """The remainder is projected with full-year cumulative context.

    Pins the case where full-context and remainder-restart projections
    DIVERGE: a high earner crossing the 2026 SS wage base mid-year.  The
    old remainder-only projection restarted cumulative wages at zero and
    would re-charge full SS (and skip the Medicare surtax) across the
    remainder; these absolute hand-computed dollars fail against that code.
    """

    def test_ss_cap_and_surtax_respected_in_remainder(self, app, db, seed_user):
        """260,000 salary, checkpoint 2026-06-30, 26 periods -> capped SS.

        Setup: annual 260,000 / 26 = 10,000.00 gross per period exactly
        (no residue); no deductions, no pay stub; 2026 FICA seeds
        ss_rate 0.0620, ss_wage_base 184,500, medicare_rate 0.0145,
        surtax 0.0090 above cumulative 200,000.

        Periods P0..P25 start 2026-01-02 biweekly; P12 = 06-19,
        P13 = 07-03.  Checkpoint dated 2026-06-30 -> measured covers
        P0..P12 (13 paychecks), remainder = P13..P25 (13 periods).

        Social Security (statutory annual max = 0.0620 x 184,500
        = 11,439.00; cumulative before Pk = 10,000 x k):
          P0..P17  : cumulative <= 170,000 -> full 10,000 x 0.062 = 620.00
          P18 (paycheck 19): cumulative before = 180,000 -> remaining
                     = 11,439.00 - 0.062 x 180,000 = 11,439.00 - 11,160.00
                     = 279.00 -> min(620.00, 279.00) = 279.00
          P19..P25 : cumulative >= 190,000 -> 0.00
        Projected remainder SS = P13..P17 (5 x 620.00 = 3,100.00)
                               + P18 (279.00) + P19..P25 (0)
                               = 3,379.00.
        (Remainder-restart code: 13 x 620.00 = 8,060.00 -- cumulative
        13 x 10,000 = 130,000 never reaches 184,500.)

        Medicare (base 10,000 x 0.0145 = 145.00/period; 0.9% surtax on
        the portion of cumulative gross above 200,000):
          P19 (paycheck 20): before = 190,000, after = 200,000 -- nothing
                     ABOVE the threshold -> 145.00
          P20..P25 : cumulative before >= 200,000 -> full 10,000 surtaxed:
                     145.00 + 10,000 x 0.009 = 145.00 + 90.00 = 235.00
        Projected remainder medicare = P13..P19 (7 x 145.00 = 1,015.00)
                                     + P20..P25 (6 x 235.00 = 1,410.00)
                                     = 2,425.00.
        (Remainder-restart code: 13 x 145.00 = 1,885.00.)

        Gross: 13 x 10,000.00 = 130,000.00.

        The checkpoint's measured figures are set to the modeled elapsed
        values (13 paychecks: SS 8,060.00 = 13 x 620.00 -- all thirteen
        are pre-cap), so total SS = 8,060.00 + 3,379.00 = 11,439.00, the
        statutory annual maximum exactly -- the number the fix exists to
        get right.
        """
        profile = _committed_profile(
            seed_user,
            name="High Earner",
            pay="10000.00",  # $260,000.00 a year / 26
        )
        _make_full_year_periods(seed_user["user"])
        periods = _derived(seed_user["user"].id)
        _add_checkpoint(
            profile, date(2026, 6, 30),
            ytd_gross=Decimal("130000.00"),
            ytd_federal=Decimal("26000.00"),
            ytd_state=Decimal("5500.00"),
            ytd_social_security=Decimal("8060.00"),
            ytd_medicare=Decimal("1885.00"),
        )
        db.session.commit()

        result = compute_withholding_to_date(
            profile, 2026,
            BalanceContext.build(seed_user["user"].id),
        )

        assert result.measured_through == date(2026, 6, 30)
        assert result.projected.gross == Decimal("130000.00")
        # The cap pin: 3,379.00 full-context vs 8,060.00 remainder-restart.
        assert result.projected.social_security == Decimal("3379.00")
        # The surtax pin: 2,425.00 full-context vs 1,885.00 remainder-restart.
        assert result.projected.medicare == Decimal("2425.00")
        # Measured verbatim + split identity -> the statutory annual max.
        assert result.measured.social_security == Decimal("8060.00")
        assert result.total.social_security == Decimal("11439.00")
