"""The salary schema's lookups: paycheck line kinds, withholding taxes, tax and deduction methods.

Split out of the flat ``app/enums.py`` at plan step **credit_card:CC-6** (see
:mod:`app.enums` for why).  Every class below stands exactly as it stood there.
"""

import enum


class PaycheckLineKindEnum(enum.Enum):
    """The KIND of a payroll line: its position in the paycheck's waterfall.

    Values match ``ref.paycheck_line_kinds.name`` in the database, and the
    members are in WATERFALL order (ruling **R-SAL38**, plan steps
    **salary:R18-a** and **R18-b**): a paycheck is base pay plus a list of
    lines, and a line's kind is where it sits between base pay and net pay::

        base pay
        + TAXABLE_EARNING       -> gross (withholding annualises it)
        - PRE_FICA_DEDUCTION    -> Social Security and Medicare wages
        - PRE_TAX_DEDUCTION     -> taxable income (the income-tax base)
        - withholding
        - POST_TAX_DEDUCTION
        + AFTER_TAX_EARNING     -> net pay (the deposit)

    Sign and stage DERIVE from the kind, so a position no payroll has (a
    post-tax line that skips Social Security) is not storable.  The two
    pre-tax kinds are plan step **salary:S11-e** (ruling **plan#2639**,
    **SAL-566**): "Pre-tax, all payroll taxes" (a Section 125 premium, a
    flexible spending account) and "Pre-tax, income tax only" (a retirement
    deferral, the one kind before it).  ``DeductionTimingEnum`` until R18-a.
    """

    TAXABLE_EARNING = "taxable_earning"
    PRE_FICA_DEDUCTION = "pre_fica_deduction"
    PRE_TAX_DEDUCTION = "pre_tax_deduction"
    POST_TAX_DEDUCTION = "post_tax_deduction"
    AFTER_TAX_EARNING = "after_tax_earning"


class WithholdingKindEnum(enum.Enum):
    """WHICH TAX a pay stub's withholding figure is (plan step **salary:S11-a**).

    Values match ``ref.withholding_kinds.name``, the catalogue behind
    ``salary.pay_stub_withholdings.withholding_kind_id`` (ruling **R-SAL42**,
    round 4, fork 11: "a table per kind of line").  A transcribed stub
    carries one amount per TAX it prints, and this says which tax each
    amount is::

        FEDERAL_INCOME   -- federal income tax withheld
        STATE_INCOME     -- state income tax withheld
        SOCIAL_SECURITY  -- the employee's Social Security (OASDI) share
        MEDICARE         -- the employee's Medicare share

    **A reference list and not four columns, and the difference is the
    document's.**  A real stub can lack one of these (a state with no income
    tax) or carry one more (a city income tax, a state disability line), so
    "exactly these four" is the app's paycheck model and not a fact of the
    stub.  A new tax is a new member and a new row here, never a table
    change.  The four members are the four withholding lines the paycheck
    engine computes today (``paycheck_calculator._breakdown.TaxLines``).
    """

    FEDERAL_INCOME = "federal_income"
    STATE_INCOME = "state_income"
    SOCIAL_SECURITY = "social_security"
    MEDICARE = "medicare"


class CalcMethodEnum(enum.Enum):
    """Calculation method values.

    Values match ``ref.calc_methods.name`` in the database.
    """

    FLAT = "flat"
    PERCENTAGE = "percentage"


class TaxTypeEnum(enum.Enum):
    """Tax type values.

    Values match ``ref.tax_types.name`` in the database.
    """

    FLAT = "flat"
    NONE = "none"
    BRACKET = "bracket"


class FilingStatusEnum(enum.Enum):
    """Federal filing status values.

    Values match ``ref.filing_statuses.name`` in the database.  The tax law
    (:mod:`app.tax_law`) is keyed on these members, so a profile's
    ``filing_status_id`` reaches its rules through
    ``ref_cache.filing_status_member`` and never through a name.
    """

    SINGLE = "single"
    MARRIED_JOINTLY = "married_jointly"
    MARRIED_SEPARATELY = "married_separately"
    HEAD_OF_HOUSEHOLD = "head_of_household"


class RaiseTypeEnum(enum.Enum):
    """Salary-raise type values.

    **Display-only TO THE PROJECTIONS since plan step salary:S3-c**, which
    is narrower than "display-only" and an adversarial review of that step is
    why the sentence says so.  No projection branches on it -- every engine
    applies every raise uniformly on the pay list's walk
    (``salary_raises.applications_between``, read by
    ``PayrollBasis.base_pay_on`` since plan step salary:X-av-3a) and stops
    each one at its own stored ``salary.salary_raises.terminal_year``.
    It is still LOGIC to the storage tier: ``raise_type_id`` is a member of
    ``uq_salary_raises_profile_type_year_month``, so the type decides whether
    a second raise on the same (profile, year, month) can be written at all,
    and ``salary.add_raise`` has a whole duplicate-handling branch keyed on
    that constraint.

    It selected how the retirement salary projection treated a raise until
    then (Gate A ruling 3 / fork F4): a ``cola`` recurring raise
    extrapolated to the retirement date while ``merit`` and ``custom``
    raises stopped at ``auth.user_settings.merit_raise_horizon_years``.
    Ruling **R-SAL11** (developer, 2026-09-05) deleted that setting -- the
    horizon a recurring raise decays over is a fact on the RAISE -- and with
    it the last reader of this discrimination.  Values match
    ``ref.raise_types.name``; resolved to IDs via
    ``ref_cache.raise_type_id`` and compared by ID, never by name.
    """

    MERIT = "merit"
    COLA = "cola"
    CUSTOM = "custom"
