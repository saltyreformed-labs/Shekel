"""Single source of truth for the constants Jinja reads.

TWO groups, registered separately because they have different
preconditions.  The first and larger is the ID-derived set: every
constant the templates consume by integer ID rather than by ref-table
``name`` string (the project's "IDs for logic, strings for display
only" invariant -- see CLAUDE.md "Reference tables: IDs for logic,
strings for display only.").  The second is the pay-calendar INPUT
BOUNDS, added at plan step X-ad-a; it needs no database and is
registered unconditionally -- see
:func:`register_pay_calendar_bound_globals`.

Called from two places:

  1. ``app.create_app()`` once per app construction, after
     ``ref_cache.init(...)`` succeeds, so templates rendered by a
     normal request can read the constants without a database hit.
  2. ``tests/conftest.py::_refresh_ref_cache_and_jinja_globals``
     once per test, after the per-test drop+reclone has truncated
     ``ref.account_types`` (Phase 3b / C-28 / F-044) and the seed
     has been re-run with fresh sequence IDs.  Without this re-seat
     the previously-registered globals would point at IDs that no
     longer exist and every template referencing one would raise
     ``UndefinedError``.

The audit follow-up Commit 6 (F-7) extracted this helper after
verification showed the conftest copy of the registration list was
missing eight entries (``LINE_KIND_PRE_TAX_DEDUCTION``, ``LINE_KIND_POST_TAX_DEDUCTION``,
``CALC_PERCENTAGE``, ``CALC_FLAT``, ``GOAL_MODE_FIXED``,
``GOAL_MODE_INCOME_RELATIVE``, ``INCOME_UNIT_PAYCHECKS``,
``INCOME_UNIT_MONTHS``).  Folding both call sites through one
function makes future drift impossible: adding a new constant
requires editing exactly one list -- ``_REF_ID_GLOBALS`` below.
"""

from collections.abc import Callable
from enum import Enum

from flask import Flask

from app import ref_cache
from app.enums import (
    AcctCategoryEnum,
    AcctTypeEnum,
    BusinessDayShiftEnum,
    CalcMethodEnum,
    EmployerContributionTypeEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.pay_era import (
    CADENCE_DAYS_MAX,
    CADENCE_DAYS_MIN,
    DAY_OF_MONTH_MAX,
    DAY_OF_MONTH_MIN,
)
from app.schemas.validation import cadence_kind_token
from app.services.pay_rhythm import FixedDays, Monthly, SemiMonthly
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN
from app.services.pay_period_batch import PERIOD_BATCH_MAX, PERIOD_BATCH_MIN

# Every ID-derived Jinja global, grouped by the ``ref_cache`` accessor
# that resolves it.  Each group pairs one accessor with the
# ``{global_name: enum_member}`` map it applies; ``register_ref_id_globals``
# folds the whole table into ``app.jinja_env.globals``.  Adding a constant
# is one row here -- the single source of truth the F-7 extraction
# established (see the module docstring).
_REF_ID_GLOBALS: tuple[tuple[Callable[[Enum], int], dict[str, Enum]], ...] = (
    (ref_cache.status_id, {
        "STATUS_PROJECTED": StatusEnum.PROJECTED,
        "STATUS_DONE": StatusEnum.DONE,
        "STATUS_RECEIVED": StatusEnum.RECEIVED,
        "STATUS_CREDIT": StatusEnum.CREDIT,
        "STATUS_CANCELLED": StatusEnum.CANCELLED,
    }),
    (ref_cache.txn_type_id, {
        "TXN_TYPE_INCOME": TxnTypeEnum.INCOME,
        "TXN_TYPE_EXPENSE": TxnTypeEnum.EXPENSE,
    }),
    # Account type IDs -- all types registered so templates can use
    # integer comparisons instead of string-based name checks.
    (ref_cache.acct_type_id, {
        "ACCT_TYPE_CHECKING": AcctTypeEnum.CHECKING,
        "ACCT_TYPE_SAVINGS": AcctTypeEnum.SAVINGS,
        "ACCT_TYPE_HYSA": AcctTypeEnum.HYSA,
        "ACCT_TYPE_MONEY_MARKET": AcctTypeEnum.MONEY_MARKET,
        "ACCT_TYPE_CD": AcctTypeEnum.CD,
        "ACCT_TYPE_HSA": AcctTypeEnum.HSA,
        "ACCT_TYPE_CREDIT_CARD": AcctTypeEnum.CREDIT_CARD,
        "ACCT_TYPE_MORTGAGE": AcctTypeEnum.MORTGAGE,
        "ACCT_TYPE_AUTO_LOAN": AcctTypeEnum.AUTO_LOAN,
        "ACCT_TYPE_STUDENT_LOAN": AcctTypeEnum.STUDENT_LOAN,
        "ACCT_TYPE_PERSONAL_LOAN": AcctTypeEnum.PERSONAL_LOAN,
        "ACCT_TYPE_HELOC": AcctTypeEnum.HELOC,
        "ACCT_TYPE_401K": AcctTypeEnum.K401,
        "ACCT_TYPE_ROTH_401K": AcctTypeEnum.ROTH_401K,
        "ACCT_TYPE_TRADITIONAL_IRA": AcctTypeEnum.TRADITIONAL_IRA,
        "ACCT_TYPE_ROTH_IRA": AcctTypeEnum.ROTH_IRA,
        "ACCT_TYPE_BROKERAGE": AcctTypeEnum.BROKERAGE,
        "ACCT_TYPE_529": AcctTypeEnum.PLAN_529,
    }),
    # No ``REC_*`` group: plan step R7b-2 took the last five with the pattern
    # ``<select>`` whose ``data-*`` attributes read them.  The form authors
    # ``(interval_n, unit, placement)`` now and the closed pattern set is a
    # storage encoding the write door chooses, so no template names a pattern
    # id at all -- and a registered global no template references is a constant
    # defending nothing (the reason ``REC_EVERY_PERIOD`` and
    # ``REC_MONTHLY_FIRST`` left at plan step R7a).  The picker's ids reach
    # ``_recurrence_fields.html`` on the
    # :class:`~app.services.recurrence.PickerModel` the route resolves, which
    # is per-request data rather than a process-wide constant.
    # ``tests/test_jinja_globals.py`` pins the registered set against the
    # templates themselves, so neither a dropped global nor a dead one can pass
    # unnoticed -- it is what caught these five.
    (ref_cache.acct_category_id, {
        "ACCT_CAT_ASSET": AcctCategoryEnum.ASSET,
        "ACCT_CAT_LIABILITY": AcctCategoryEnum.LIABILITY,
        "ACCT_CAT_RETIREMENT": AcctCategoryEnum.RETIREMENT,
        "ACCT_CAT_INVESTMENT": AcctCategoryEnum.INVESTMENT,
    }),
    # No ``LINE_KIND_*`` group since task plan#2715 (plan step salary:S11-e).
    # The salary page's kind chip was the only template that read one, and it
    # now reads which kinds are pre-tax from the kind vocabulary
    # (``pre_tax_kind_ids``, on the lines section's context), so the template
    # does not list the kinds itself.  ``LINE_KIND_POST_TAX_DEDUCTION`` never
    # had a reader.  Both were deleted on the developer's word (2026-11-02).
    (ref_cache.calc_method_id, {
        "CALC_PERCENTAGE": CalcMethodEnum.PERCENTAGE,
        "CALC_FLAT": CalcMethodEnum.FLAT,
    }),
    (ref_cache.goal_mode_id, {
        "GOAL_MODE_FIXED": GoalModeEnum.FIXED,
        "GOAL_MODE_INCOME_RELATIVE": GoalModeEnum.INCOME_RELATIVE,
    }),
    (ref_cache.income_unit_id, {
        "INCOME_UNIT_PAYCHECKS": IncomeUnitEnum.PAYCHECKS,
        "INCOME_UNIT_MONTHS": IncomeUnitEnum.MONTHS,
    }),
    (ref_cache.employer_contribution_type_id, {
        "EMPLOYER_TYPE_NONE": EmployerContributionTypeEnum.NONE,
        "EMPLOYER_TYPE_FLAT_PERCENTAGE": EmployerContributionTypeEnum.FLAT_PERCENTAGE,
        "EMPLOYER_TYPE_MATCH": EmployerContributionTypeEnum.MATCH,
    }),
)


#: What each payday convention is CALLED on a form, in the order the four
#: schedule forms offer them (plan step ``pay_calendar:C14-b``, ruling
#: **R-PC56**: the question is asked wherever a cadence is).  Declared once
#: here rather than spelled in each template, because four copies of an option
#: list are four places for the wording to drift -- and the wording is the
#: whole of what an owner has to go on.
#:
#: **No option is described as usual or recommended**, and that is the
#: ruling's own wording rather than terseness: R-PC56 REFUSED defaulting to
#: ``prior`` on the reasoning that real payroll usually pays early, because
#: that states as fact what no owner was asked.  A help text saying the same
#: thing would reintroduce the claim one layer up.
_PAYDAY_SHIFT_LABELS: dict[Enum, str] = {
    BusinessDayShiftEnum.NONE: "Pay on that day anyway",
    BusinessDayShiftEnum.PRIOR: "Pay the business day before",
    BusinessDayShiftEnum.NEXT: "Pay the business day after",
}

#: The QUESTION those options answer, and what it tells an owner.
#:
#: **Here for the same reason the options are, and an adversarial review of
#: 2026-09-05 is why they are here too.**  The first form of this step
#: centralised the three option labels and left the question spelled in FOUR
#: templates and the help text in TWO -- absent from regenerate and reset,
#: which are precisely the doors where an owner CHANGES a stored convention
#: and most needs to know what the choices mean.  A wording that drifts
#: between the door that sets a value and the door that corrects it is worse
#: than one that is merely repeated.
PAYDAY_SHIFT_LABEL = "If a payday falls on a weekend or holiday"
PAYDAY_SHIFT_HELP = (
    "Your employer's rule. Shekel keeps every payday on its scheduled day "
    "until you say otherwise."
)

#: What the four doors that ask for a PAYDAY tell the owner about which day
#: to type (plan step ``pay_calendar:C17-c-2a``, closing ledger row
#: **PC-504**).  The writer reads the day as a point on the owner's NOMINAL
#: grid -- it is what a batch is spaced from and, when the batch mints an
#: era, that era's ``effective_from`` and so its phase -- while every form
#: asked for the day the owner was PAID.  Under a convention that moves a
#: payday the two differ: an owner really paid 2025-12-31 because payroll
#: moved the 2026-01-01 nominal day back typed 2025-12-31, and every later
#: payday derived a day early, permanently.  So the question names the
#: SCHEDULED day, in the same word the convention control uses, and this
#: sentence is declared once for the reason the option list is.
PAYDAY_NOMINAL_HELP = (
    "The date on your pay schedule. If payroll paid that paycheck early or "
    "late because the day fell on a weekend or holiday, enter the scheduled "
    "day, not the day the money arrived."
)


def register_ref_id_globals(app: Flask) -> None:
    """Register every ID-derived Jinja global on the given Flask app.

    Idempotent: each call overwrites the same set of keys with the
    same values resolved from ``ref_cache``, so repeated invocation
    (e.g. once per test after a ref-cache reseat) is safe and has
    no externally observable side effect beyond the final dict
    state.

    Pre-condition: ``ref_cache.init(...)`` has completed successfully
    against the current session.  If the caller observed any
    unavailable ref tables (the bootstrap-window branch in
    ``create_app``), it MUST skip this call -- the accessors in
    ``_REF_ID_GLOBALS`` would raise ``KeyError`` for missing enum
    members.

    Args:
        app: The Flask application instance whose
            ``jinja_env.globals`` map will be populated.
    """
    globals_map = app.jinja_env.globals
    for accessor, members in _REF_ID_GLOBALS:
        for name, member in members.items():
            globals_map[name] = accessor(member)
    # The payday-convention picker's options, as ``(id, label)`` pairs the
    # ``render_select`` macro consumes directly.  Built here rather than in
    # ``_REF_ID_GLOBALS`` because that table maps ONE name to ONE id and this
    # is an ordered list; built at all because four forms render the same
    # three choices (**R-PC56**).  ``SHIFT_NONE`` rides along as the selected
    # value a form falls back to when it has no schedule row to read a stored
    # answer off -- registration, and the standalone generate page.
    globals_map["PAYDAY_SHIFT_OPTIONS"] = tuple(
        (ref_cache.business_day_shift_id(member), label)
        for member, label in _PAYDAY_SHIFT_LABELS.items()
    )
    globals_map["SHIFT_NONE"] = ref_cache.business_day_shift_id(
        BusinessDayShiftEnum.NONE,
    )


def register_pay_calendar_bound_globals(app: Flask) -> None:
    """Register the pay-calendar input bounds every schedule form renders.

    **Plan step X-ad-a**, and it exists so a template hint cannot disagree
    with the rule behind it.  Three forms carry a cadence and a period count
    -- the settings generate card, its standalone page, and now registration
    -- and each rendered its own ``min`` / ``max`` literals.  A literal in a
    template is the copy nobody re-reads, which the cadence floor proved
    twice: X-ad-a moved it UP to the writer's, because a one-day cadence could
    not be materialised into an authored ``end_date`` and three hardcoded
    ``min="1"`` attributes went on inviting the value that reached the CHECK
    as a 500 -- and plan step ``pay_calendar:C4-c`` moved it back DOWN to the
    column's when that column was dropped.  Neither move touched a template.

    Registered UNCONDITIONALLY, unlike :func:`register_ref_id_globals`: these
    are module constants, so there is no ref-cache precondition and no
    bootstrap window in which they would raise.  That also means the per-test
    ref-cache reseat does not have to re-run this one -- nothing invalidates
    a constant.

    The names are the browser's half of a three-layer refusal and never the
    guard: the Marshmallow field and the service refuse the same set whatever
    was rendered.

    Args:
        app: The Flask application whose ``jinja_env.globals`` to populate.
    """
    app.jinja_env.globals.update({
        # The payday-convention control's wording (plan step
        # pay_calendar:C14-b).  Registered UNCONDITIONALLY beside the bounds
        # rather than with the OPTIONS, because these two are module constants
        # with no ``ref_cache`` precondition -- the same split this function's
        # docstring draws.
        "PAYDAY_SHIFT_LABEL": PAYDAY_SHIFT_LABEL,
        "PAYDAY_SHIFT_HELP": PAYDAY_SHIFT_HELP,
        "PAYDAY_NOMINAL_HELP": PAYDAY_NOMINAL_HELP,
        "CADENCE_DAYS_MIN": CADENCE_DAYS_MIN,
        "CADENCE_DAYS_MAX": CADENCE_DAYS_MAX,
        # The day-of-month arms' bounds (plan step pay_calendar:C17-d-3),
        # the model's 1..31, for the same reason the cadence pair is here.
        "DAY_OF_MONTH_MIN": DAY_OF_MONTH_MIN,
        "DAY_OF_MONTH_MAX": DAY_OF_MONTH_MAX,
        # What each radio arm of the cadence-kind control POSTS (ruling
        # R-PC84), read from the schema's own wire table rather than
        # spelled in the macro: the ``value=`` a browser sends and the token
        # ``CadenceKindField`` maps to a type are one spelling this way.
        # Module constants with no ref-cache precondition, like the bounds,
        # because R-PC80 dropped the kind's ref table -- the kind is the
        # value's TYPE, and its token is the schema's.
        "CADENCE_KIND_FIXED_DAYS": cadence_kind_token(FixedDays),
        "CADENCE_KIND_MONTHLY": cadence_kind_token(Monthly),
        "CADENCE_KIND_SEMI_MONTHLY": cadence_kind_token(SemiMonthly),
        "PERIOD_BATCH_MIN": PERIOD_BATCH_MIN,
        "PERIOD_BATCH_MAX": PERIOD_BATCH_MAX,
        # How far this application's calendar reaches (plan step
        # balance:X-bh-2), which is what bounds the two pay-history date
        # inputs.  Named for the FACT rather than for the column that happens
        # to be bounded by it: one number with a second name is how
        # ``EFFECTIVE_DATE_*`` and ``_STARTS_ON_*`` already came to exist.
        # ISO strings rather than ``date`` objects because both are rendered
        # straight into an ``<input type="date">``'s ``min`` / ``max``, and a
        # template calling ``.isoformat()`` at two sites is two places for the
        # format to drift from the one the browser parses.
        "CALENDAR_DATE_MIN": CALENDAR_DATE_MIN.isoformat(),
        "CALENDAR_DATE_MAX": CALENDAR_DATE_MAX.isoformat(),
    })
