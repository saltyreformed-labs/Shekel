"""The shapes the tax law is written in, and the checks that make a malformed year unwritable.

Every value is a frozen dataclass over ``Decimal`` literals: the law is data the
release carries, never a row a user or a deploy can write (ruling
**salary:R-SAL74**).  A year is checked when its module is imported, so a
transcription slip in next year's figures -- a bracket ladder with a gap, a
filing status left out, a rate written as a percent -- stops the application
from starting rather than pricing a paycheck against it.

**The checks replace the per-user tables' constraints, one for one, and are
never narrower.**  Until plan step salary:X-at-1 each user's copy of the law sat
in five ``salary`` tables (unread since, and dropped at plan step X-at-2) whose
columns refused a malformed row: amounts were ``Numeric(12, 2)`` and rates
``Numeric(5, 4)``, both ``NOT NULL`` where the law requires a figure, every
amount at least zero, the Social Security wage base and the Additional Medicare
threshold above zero, every rate between 0 and 1, a bracket's upper bound at or
above its lower one and a child deduction tier's above it, a state code of two
characters, and a tax year between 2000 and 2100.  Each of those is a check
below, on the dataclass that carries the figure, so no year can hold what no
table could.

**Several are stricter than the tables, because a per-row CHECK cannot state
them or the column chose otherwise:** every filing status present in each year
and each state, and FICA in every year; a ladder's rungs ordered by position
and climbing from zero without a gap to one open top (a child deduction table
likewise, when a state has one); no empty bracket (its table allowed an upper
bound EQUAL to the lower one); a state code of two capital letters; a state's
standard deduction always stated (``$0.00`` where it has none; the column
allowed ``NULL``); a state's tax type agreeing with its rate -- a flat-rate
state states one, a state with no income tax none -- and never a type the app
has no formula for (plan step salary:X-at-3); a figure with more decimal
places than its column refused where the column silently rounded it, because
a transcription that disagrees with its scale is a slip to fix, not a figure
to reinterpret; a law that cannot change once it loads, because every mapping
in it is a read-only view; no year skipped between two the law carries
(ruling salary:R-SAL86); and every state listed from the law's first year
(ruling salary:R-SAL129).

**The attribute names are the calculator's.**  :mod:`app.services.tax_calculator`
reads ``standard_deduction``, ``brackets`` (each ``min_income`` / ``max_income``
/ ``rate`` / ``sort_order``), the three credit amounts and the five FICA
figures off whatever it is handed -- the withholding formula off the federal
rules' ``withholding_rules`` -- and these carry exactly those names, so the
calculator prices the law without knowing where it came from.  The federal
figures reach it as one filing status's slice
(:class:`app.services.tax_config_service.FederalTaxRules`): that status's
:class:`FederalRules` with the year's :class:`FederalCredits` beside them,
because the law states the credits once for every filer (ruling plan#3340).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from app.enums import FilingStatusEnum, TaxTypeEnum

_ZERO = Decimal("0")
_ONE = Decimal("1")

# The per-user tables' column types, which the checks keep: amounts were
# ``Numeric(12, 2)`` (ten digits before the point, two after) and rates
# ``Numeric(5, 4)`` (one and four).
_AMOUNT_DIGITS = (10, 2)
_RATE_DIGITS = (1, 4)

# The range those tables' ``ck_*_valid_tax_year`` CHECKs held.
_FIRST_TAX_YEAR = 2000
_LAST_TAX_YEAR = 2100


@dataclass(frozen=True)
class Bracket:
    """One rung of a federal bracket ladder: ``rate`` on income in ``(min_income, max_income]``.

    ``max_income`` is ``None`` on the top rung only.  ``sort_order`` is the
    rung's position in its ladder, set by :func:`ladder` and never written by
    hand, because the calculator orders rungs by it; :class:`FederalRules`
    refuses a ladder whose rungs are not ordered by their position.
    """

    min_income: Decimal
    max_income: Decimal | None
    rate: Decimal
    sort_order: int

    def __post_init__(self):
        """Refuse a rung no ``salary.tax_brackets`` row could have held, or an empty one."""
        _require_amount(self.min_income, "bracket min_income")
        _require_upper_bound(self.min_income, self.max_income, "bracket")
        _require_rate(self.rate, "bracket rate")


def ladder(*rungs: tuple[str, str | None, str]) -> tuple[Bracket, ...]:
    """Build a bracket ladder from ``(min_income, max_income, rate)`` strings.

    The one place a rung's ``sort_order`` is derived (its position).  Each
    rung checks itself as it is built; whether the rungs form one ladder is
    :class:`FederalRules`' check, because a ladder is only ever used there.

    Args:
        *rungs: The rungs from the bottom up, each as decimal strings exactly
            as the source prints them (``max_income`` ``None`` on the top rung).

    Returns:
        The ladder as a tuple of :class:`Bracket`.

    Raises:
        ValueError: If a rung is malformed (:class:`Bracket`).
    """
    return tuple(
        Bracket(
            min_income=Decimal(low),
            max_income=None if high is None else Decimal(high),
            rate=Decimal(rate),
            sort_order=position,
        )
        for position, (low, high, rate) in enumerate(rungs)
    )


@dataclass(frozen=True)
class FederalRules:
    """One filing status's federal income-tax rules for one tax year.

    What the law states PER FILING STATUS: the tax RETURN's standard
    deduction and bracket ladder.  The per-child credits the law states once
    for every filer are the year's (:class:`FederalCredits`, ruling
    plan#3340), and :class:`app.services.tax_config_service.FederalTaxRules`
    sets them beside one status's rules for the calculator.

    **A paycheck withholds on the year's withholding documents, which can
    print other figures than the return's** (ruling plan#3213, plan step
    salary:X-at-7).  The employer withholds on IRS Publication 15-T and the
    employee's Form W-4, both printed before the year starts.  P.L. 119-21
    (signed 2025-07-04) raised 2025's standard deduction and child credit, and
    the IRS left that year's withholding tables and W-4 on the old figures
    (IR-2025-82), so 2025's paychecks withheld on a smaller standard
    deduction and child credit than its return allows, and the difference
    came back as a refund.
    ``withholding_standard_deduction`` here and the year's
    :attr:`FederalCredits.withholding_child_credit_amount` state a
    withholding figure where it DIFFERS from the return's, and are ``None``
    where the withholding documents print the return's figure.
    ``None`` is the one way to write a figure the two share: stating it again
    would be a second spelling that a later edit could part from the first, so
    a stated figure equal to the return's is refused.  The law states no
    withholding copy of the other two figures the formula reads.  The
    other-dependent credit has not differed.  The ladder has, for one filing
    status: Pub 15-T has no married-filing-separately table and withholds on
    the single filer's ladder, whose 35% rung runs past the return's 37%
    threshold for married filing separately, and the formula withholds on
    the return's ladder (finding plan#3243, step plan#3241).
    """

    standard_deduction: Decimal
    brackets: tuple[Bracket, ...]
    withholding_standard_deduction: Decimal | None = None

    def __post_init__(self):
        """Refuse a negative amount, a broken ladder, or a withholding figure as the return's."""
        _require_amount(self.standard_deduction, "federal standard_deduction")
        _require_rungs(self.brackets, Bracket, "a bracket ladder")
        if [rung.sort_order for rung in self.brackets] != list(range(len(self.brackets))):
            raise ValueError("a bracket ladder's rungs are ordered by their position")
        _require_climb(
            [(rung.min_income, rung.max_income) for rung in self.brackets],
            "a bracket ladder",
        )
        _require_withholding_figure(
            self.withholding_standard_deduction, self.standard_deduction,
            "federal withholding_standard_deduction",
        )


@dataclass(frozen=True)
class FederalCredits:
    """One tax year's per-child federal credits, stated once for every filer.

    The child tax credit and the refundable part of it per qualifying child
    (IRC sec. 24(a) and (h)), and the credit per other dependent (sec.
    24(h)(4)): the law states each once for every filing status, so a year
    states each once (ruling plan#3340).  Until plan step plan#3206 every
    filing status stated its own copy -- four a year -- and nothing refused
    copies that disagreed.

    ``withholding_child_credit_amount`` is the per-child credit Form W-4's
    Step 3 claims where it DIFFERS from the return's, and ``None`` where the
    W-4 prints the return's (:class:`FederalRules` says why a year can state
    one).  The W-4 states it once for everyone too, so it lives here with the
    others (ruling plan#3342).
    """

    child_credit_amount: Decimal
    other_dependent_credit_amount: Decimal
    child_credit_refundable_cap: Decimal
    withholding_child_credit_amount: Decimal | None = None

    def __post_init__(self):
        """Refuse a negative amount, or a withholding child credit that is the return's."""
        _require_amount(self.child_credit_amount, "federal child_credit_amount")
        _require_amount(
            self.other_dependent_credit_amount, "federal other_dependent_credit_amount",
        )
        _require_amount(
            self.child_credit_refundable_cap, "federal child_credit_refundable_cap",
        )
        _require_withholding_figure(
            self.withholding_child_credit_amount, self.child_credit_amount,
            "federal withholding_child_credit_amount",
        )


@dataclass(frozen=True)
class FicaRules:
    """One tax year's Social Security and Medicare rules (they carry no filing status)."""

    ss_rate: Decimal
    ss_wage_base: Decimal
    medicare_rate: Decimal
    medicare_surtax_rate: Decimal
    medicare_surtax_threshold: Decimal

    def __post_init__(self):
        """Refuse what the ``ck_fica_configs_*`` CHECKs refused."""
        _require_rate(self.ss_rate, "FICA ss_rate")
        _require_amount(self.ss_wage_base, "FICA ss_wage_base", above_zero=True)
        _require_rate(self.medicare_rate, "FICA medicare_rate")
        _require_rate(self.medicare_surtax_rate, "FICA medicare_surtax_rate")
        _require_amount(
            self.medicare_surtax_threshold, "FICA medicare_surtax_threshold",
            above_zero=True,
        )


@dataclass(frozen=True)
class ChildDeductionTier:
    """One AGI tier of a state's per-child deduction.

    ``deduction_per_child`` applies to AGI in ``(agi_min, agi_max]``, and
    ``agi_max`` is ``None`` on the open-ended top tier.  A threshold dollar
    belongs to the LOWER tier, as the statute reads ("Up to $X", then "Over $X");
    :func:`app.services.tax_calculator.resolve_child_deduction_per_child` keys
    on ``agi_max`` alone.
    """

    agi_min: Decimal
    agi_max: Decimal | None
    deduction_per_child: Decimal

    def __post_init__(self):
        """Refuse a tier no ``salary.state_child_deductions`` row could have held."""
        _require_amount(self.agi_min, "child deduction agi_min")
        _require_upper_bound(self.agi_min, self.agi_max, "child deduction tier")
        _require_amount(self.deduction_per_child, "child deduction_per_child")


@dataclass(frozen=True)
class StateYearLaw:
    """One state's income-tax law for one tax year.

    The rate is the STATE's, stated once; the standard deduction and the child
    deduction tiers are per filing status, because the statute states them per
    status.  Every filing status is present in both, or the year does not load.
    A status's tiers are empty for a state with no child deduction; otherwise
    they climb from zero without a gap, lowest AGI first.

    **The tax type and the figures agree, and the type is one the app prices**
    (plan step salary:X-at-3; the developer, 2026-10-08, "Strict").  A
    ``FLAT`` state states a rate above zero.  A state with no income tax
    (``NONE``) states no rate, a ``$0.00`` standard deduction for every status
    and no child deduction, and prices ``$0.00``: the explicit entry ruling
    **salary:R-SAL78** asks for, and the ONE way to write it -- a flat 0% or
    an untaxed state carrying deductions would be a second spelling of the
    same ``$0.00``.  A ``BRACKET`` state is refused: no formula in the app
    prices a state's bracket ladder
    (:func:`app.services.tax_calculator.calculate_state_tax`), so it would
    price ``$0.00`` without a word -- finding **SAL-575**'s defect by another
    door.
    """

    tax_type: TaxTypeEnum
    flat_rate: Decimal | None
    standard_deduction: Mapping[FilingStatusEnum, Decimal]
    child_deduction_tiers: Mapping[FilingStatusEnum, tuple[ChildDeductionTier, ...]]

    def __post_init__(self):
        """Refuse a state year that leaves a filing status out or states a malformed figure."""
        _freeze(self, "standard_deduction", "child_deduction_tiers")
        if not isinstance(self.tax_type, TaxTypeEnum):
            raise ValueError(f"a state's tax_type is a TaxTypeEnum, not {self.tax_type!r}")
        if self.tax_type is TaxTypeEnum.BRACKET:
            raise ValueError(
                "a state taxed on a bracket ladder cannot be written: no formula "
                "in the app prices one, so it would price $0.00"
            )
        if self.tax_type is TaxTypeEnum.FLAT and self.flat_rate is None:
            raise ValueError("a flat-rate state states its rate")
        if self.tax_type is TaxTypeEnum.NONE and self.flat_rate is not None:
            raise ValueError(
                f"a state with no income tax states no rate, not {self.flat_rate}"
            )
        if self.flat_rate is not None:
            _require_rate(self.flat_rate, "state flat_rate")
            if self.flat_rate == _ZERO:
                raise ValueError(
                    "a flat-rate state's rate is above zero: a state with no "
                    "income tax is written as one, tax type NONE"
                )
        _require_every_status(self.standard_deduction, "state standard deduction")
        for status, deduction in self.standard_deduction.items():
            _require_amount(deduction, f"{status.value} state standard deduction")
        _require_every_status(self.child_deduction_tiers, "state child deduction")
        for status, tiers in self.child_deduction_tiers.items():
            what = f"the {status.value} child deduction tiers"
            _require_rungs(tiers, ChildDeductionTier, what)
            if tiers:
                _require_climb([(tier.agi_min, tier.agi_max) for tier in tiers], what)
        if self.tax_type is TaxTypeEnum.NONE:
            _require_nothing_to_deduct(self)


@dataclass(frozen=True)
class TaxYearLaw:
    """Everything the app knows of one tax year's law, and where it came from.

    ``sources`` names each published document the year's figures were
    transcribed from, so a reader checking a figure knows where to look and a
    reader adding next year knows what to look for.  The federal rules cover
    every filing status, the per-child credits are stated once for all of them
    (ruling plan#3340), and FICA is always present, or the year does not load;
    a state appears only where the app supports it, and a state the app
    supports is one the law lists (:attr:`TaxLaw.supported_states`).
    """

    tax_year: int
    sources: tuple[str, ...]
    federal: Mapping[FilingStatusEnum, FederalRules]
    federal_credits: FederalCredits
    fica: FicaRules
    states: Mapping[str, StateYearLaw]

    def __post_init__(self):
        """Refuse a year out of range, lacking a status, its credits or FICA, or a source."""
        _freeze(self, "federal", "states")
        if (
            not isinstance(self.tax_year, int)
            or not _FIRST_TAX_YEAR <= self.tax_year <= _LAST_TAX_YEAR
        ):
            raise ValueError(
                f"a tax year is a year from {_FIRST_TAX_YEAR} to {_LAST_TAX_YEAR}, "
                f"not {self.tax_year!r}"
            )
        _require_every_status(self.federal, f"{self.tax_year} federal rules")
        for status, rules in self.federal.items():
            if not isinstance(rules, FederalRules):
                raise ValueError(
                    f"{self.tax_year} {status.value} federal rules are FederalRules, "
                    f"not {rules!r}"
                )
        if not isinstance(self.federal_credits, FederalCredits):
            raise ValueError(
                f"{self.tax_year} federal credits are FederalCredits, "
                f"not {self.federal_credits!r}"
            )
        if not isinstance(self.fica, FicaRules):
            raise ValueError(f"{self.tax_year} FICA rules are FicaRules, not {self.fica!r}")
        for state_code, state in self.states.items():
            if not (
                isinstance(state_code, str) and len(state_code) == 2
                and state_code.isascii() and state_code.isalpha()
                and state_code.isupper()
            ):
                raise ValueError(
                    f"{self.tax_year} state {state_code!r} is not a two-letter "
                    "capital state code"
                )
            if not isinstance(state, StateYearLaw):
                raise ValueError(
                    f"{self.tax_year} {state_code} law is a StateYearLaw, not {state!r}"
                )
        if not isinstance(self.sources, tuple):
            raise ValueError(f"the {self.tax_year} tax law's sources are a tuple")
        if not self.sources or not all(
            isinstance(source, str) and source.strip() for source in self.sources
        ):
            raise ValueError(f"the {self.tax_year} tax law cites no source")


@dataclass(frozen=True)
class TaxLaw:
    """Every tax year the app carries, oldest first, each year once, none skipped.

    **No skipped year (ruling salary:R-SAL86).**  A year between two the law
    carries would be priced on the earlier one's rules
    (:func:`app.services.tax_config_service.resolve_tax_year`) with nothing to
    say it is missing, because the tax-law alarms ask only whether the law
    reaches the year a date calls for (:mod:`app.services.tax_law_alarm`).
    Refusing the gap here is what lets "the law carries the due year" mean
    "and every year before it back to the first".

    **Every state is listed from the law's first year (ruling
    salary:R-SAL129, plan step salary:X-at-3).**  A state the law listed only
    from a later year would be priced, for the years before it, on that later
    year's rules -- the resolver reaches forward when nothing precedes -- and
    nothing would say so.  So a state missing from the first year does not
    load.  A later year may still lack one (ruling R-SAL86: a new year ships
    before its state); the tax-law alarms name that state until it lands
    (:mod:`app.services.tax_law_alarm`), so a state added to the first year
    alone loads and is named for every later year.
    """

    years: tuple[TaxYearLaw, ...]

    def __post_init__(self):
        """Refuse a non-year, years misordered, repeated or skipped, or a late state."""
        if not isinstance(self.years, tuple):
            raise ValueError("the tax law's years are a tuple")
        for year in self.years:
            if not isinstance(year, TaxYearLaw):
                raise ValueError(f"a tax law year is a TaxYearLaw, not {year!r}")
        numbers = [year.tax_year for year in self.years]
        if numbers != sorted(set(numbers)):
            raise ValueError(f"tax years must be distinct and ascending: {numbers}")
        if numbers and numbers != list(range(numbers[0], numbers[-1] + 1)):
            raise ValueError(f"the tax law skips a year: {numbers}")
        for year in self.years[1:]:
            late = sorted(set(year.states) - set(self.years[0].states))
            if late:
                raise ValueError(
                    f"the tax law lists {name_states(tuple(late))} from "
                    f"{year.tax_year} but not in its first year, "
                    f"{self.years[0].tax_year}: a state is listed from the law's "
                    f"first year"
                )

    @property
    def supported_states(self) -> tuple[str, ...]:
        """Every state the law lists, alphabetically: the states the app supports.

        The ONE spelling of that set (ruling **salary:R-SAL78**, plan step
        salary:X-at-3): the salary profile form offers exactly these, and
        :meth:`supports` -- which both profile doors and the paycheck engine
        ask -- is membership in it.  Empty for a law with no year, which
        supports no state.

        Returns:
            The two-letter state codes.
        """
        return tuple(sorted({code for year in self.years for code in year.states}))

    def supports(self, state_code: str) -> bool:
        """Whether the app supports *state_code*: the law lists it.

        The one question both profile doors
        (:mod:`app.schemas.validation.salary`) and the paycheck engine
        (:func:`app.services.tax_config_service.profile_tax_series`) ask,
        answered from :attr:`supported_states`.  Exact: ``nc`` is not ``NC``.

        Args:
            state_code: The state as a profile stores or a form submits it.

        Returns:
            ``True`` when the law lists *state_code*.
        """
        return state_code in self.supported_states

    def years_listing(self, state_code: str) -> tuple[TaxYearLaw, ...]:
        """Return the years whose law lists *state_code*, oldest first.

        The ONE spelling of a state's series: the paycheck's state line is
        resolved from it (:func:`app.services.tax_config_service.profile_tax_series`)
        and so is the year the tax-law alarms name for a state
        (:mod:`app.services.tax_law_alarm`), so the two cannot part.

        Args:
            state_code: A two-letter state code.

        Returns:
            The :class:`TaxYearLaw` of each year listing the state; empty when
            no year does.
        """
        return tuple(year for year in self.years if state_code in year.states)


def name_states(state_codes: Sequence[str]) -> str:
    """Name *state_codes* for a sentence: ``"NC"``, ``"NC and SC"``, ``"NC, SC and VA"``.

    The one wording of a list of state codes in a sentence (ruling plan#3202,
    the developer 2026-11-10): the refusals' -- the law's own, the profile
    doors' and the engine's (:class:`app.exceptions.UnsupportedStateError`)
    -- and the tax-law banner's (``templates/_tax_law_notice.html``, through
    the template filter of the same name).  ``"no state"`` when empty.  The
    weekly tax-law email lists phrases, not codes ("NC on 2026's and SC on
    2026's", ``scripts/check_tax_law.py``), in wording of its own that ruling
    plan#3198 keeps.

    Args:
        state_codes: Two-letter state codes, in the order to name them.

    Returns:
        The phrase.
    """
    if not state_codes:
        return "no state"
    if len(state_codes) == 1:
        return state_codes[0]
    return f"{', '.join(state_codes[:-1])} and {state_codes[-1]}"


def _require_number(value, what: str) -> None:
    """Raise unless *value* is a finite ``Decimal``.

    The first half of a ``Numeric`` column's type: it holds only a number.  A
    ``float`` is refused with the rest, because it cannot carry an exact figure.

    Args:
        value: The figure to check.
        what: What the figure is, for the error message.

    Raises:
        ValueError: If *value* is not a finite ``Decimal``.
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{what} must be a Decimal figure, not {value!r}")


def _require_fit(value: Decimal, what: str, digits: tuple[int, int]) -> None:
    """Raise unless *value* fits its column's *digits*.

    The second half of a ``Numeric`` column's type: at most so many digits
    before the point and so many after.  The places are the VALUE's, not its
    spelling: ``0.03990`` is four places.

    Args:
        value: The figure to check, already a finite ``Decimal``.
        what: What the figure is, for the error message.
        digits: ``(before, after)`` -- the most digits before the decimal
            point and after it.

    Raises:
        ValueError: If *value* is too large for the digits before the point,
            or has more places than after it.
    """
    before, after = digits
    if value.adjusted() >= before:
        raise ValueError(f"{what} {value} has more than {before} digits before the point")
    if value != value.quantize(Decimal(1).scaleb(-after)):
        raise ValueError(f"{what} {value} has more than {after} decimal places")


def _require_amount(value, what: str, *, above_zero: bool = False) -> None:
    """Raise unless *value* is a dollar amount to the cent, at least zero.

    Args:
        value: The amount to check.
        what: What the amount is, for the error message.
        above_zero: Refuse zero too, as the ``ss_wage_base > 0`` and
            ``medicare_surtax_threshold > 0`` CHECKs did.

    Raises:
        ValueError: If *value* is not a ``Decimal`` to the cent under
            $10,000,000,000, is negative, or is zero when *above_zero*.
    """
    _require_number(value, what)
    _require_fit(value, what, _AMOUNT_DIGITS)
    if value < _ZERO or (above_zero and value == _ZERO):
        bound = "above" if above_zero else "at least"
        raise ValueError(f"{what} {value} must be {bound} zero")


def _require_rate(value, what: str) -> None:
    """Raise unless *value* is a fraction from 0 to 1, to at most four places.

    A percent written where the fraction belongs -- ``3.99`` for 3.99% -- is the
    slip this refuses: it would tax at 399%.

    Args:
        value: The rate to check.
        what: What the rate is, for the error message.

    Raises:
        ValueError: If *value* is not a ``Decimal`` to four places, or lies
            outside ``[0, 1]``.
    """
    _require_number(value, what)
    if not _ZERO <= value <= _ONE:
        raise ValueError(f"{what} {value} is not a fraction between 0 and 1")
    _require_fit(value, what, _RATE_DIGITS)


def _require_withholding_figure(
    value, returns: Decimal, what: str,
) -> None:
    """Raise unless *value* is ``None`` or an amount other than the return's figure.

    A withholding figure is stated only where the year's withholding documents
    part from the return (ruling plan#3213; :class:`FederalRules`), so one
    equal to *returns* is a second spelling of the return's figure and is
    refused, as a flat 0% state is refused beside the one spelling of no
    income tax (:class:`StateYearLaw`).

    Args:
        value: The stated withholding figure, or ``None`` where withholding
            uses the return's.
        returns: The return's figure it stands beside, already checked.
        what: What the figure is, for the error message.

    Raises:
        ValueError: If *value* is not an amount to the cent at least zero, or
            equals *returns*.
    """
    if value is None:
        return
    _require_amount(value, what)
    if value == returns:
        raise ValueError(
            f"{what} {value} is the return's figure: a withholding figure is "
            "stated only where it differs, and is None where the withholding "
            "documents print the return's"
        )


def _require_upper_bound(low: Decimal, high: Decimal | None, what: str) -> None:
    """Raise unless *high* is ``None`` (an open top) or an amount above *low*.

    Args:
        low: The rung's lower bound, already checked as an amount.
        high: The rung's upper bound, or ``None`` on an open-ended top rung.
        what: What the rung is, for the error message.

    Raises:
        ValueError: If *high* is not an amount or does not exceed *low*.
    """
    if high is None:
        return
    _require_amount(high, f"{what} upper bound")
    if high <= low:
        raise ValueError(f"a {what} ends at {high}, not above where it starts ({low})")


def _require_rungs(rungs, kind: type, what: str) -> None:
    """Raise unless *rungs* is a tuple of *kind*, so each rung has checked itself.

    Args:
        rungs: The rungs to check.
        kind: The dataclass every rung must be.
        what: What the rungs are, for the error message.

    Raises:
        ValueError: If *rungs* is not a tuple or holds anything but *kind*.
    """
    if not isinstance(rungs, tuple) or not all(isinstance(r, kind) for r in rungs):
        raise ValueError(f"{what} must be a tuple of {kind.__name__}")


def _require_climb(bounds: list[tuple[Decimal, Decimal | None]], what: str) -> None:
    """Raise unless *bounds* climb from zero without a gap to one open-ended top.

    The one shape check for a table of rungs -- a federal bracket ladder and a
    state's child deduction tiers alike: the bottom rung starts at zero, each
    rung starts where the one below it ends, and only the top rung is
    open-ended.  A gap would leave income no rung covers; a closed top, income
    above it.

    Args:
        bounds: ``(low, high)`` per rung, bottom first; ``high`` is ``None``
            on an open-ended rung.
        what: What the rungs are, for the error message.

    Raises:
        ValueError: If *bounds* is empty or does not climb as above.
    """
    if not bounds or bounds[0][0] != _ZERO:
        raise ValueError(f"{what} starts at zero")
    for position, ((_, below_high), (above_low, _)) in enumerate(
        zip(bounds, bounds[1:]), start=1,
    ):
        if below_high is None or below_high != above_low:
            raise ValueError(
                f"{what}: rung {position} starts at {above_low}, "
                f"not where rung {position - 1} ends ({below_high})"
            )
    if bounds[-1][1] is not None:
        raise ValueError(f"{what}: the top rung is open-ended")


def _require_nothing_to_deduct(state: StateYearLaw) -> None:
    """Raise unless a state with no income tax deducts nothing from anyone.

    The explicit ``$0.00`` entry (ruling salary:R-SAL78) states no rate, and a
    deduction or a child deduction beside it would be a figure that prices
    nothing: the Taxes tab would show it and no tax would move.

    Args:
        state: A :class:`StateYearLaw` of tax type ``NONE``, its other checks
            already passed.

    Raises:
        ValueError: A filing status states a standard deduction above
            ``$0.00``, or any child deduction tiers.
    """
    for status, deduction in state.standard_deduction.items():
        if deduction != _ZERO:
            raise ValueError(
                f"a state with no income tax deducts nothing, not {deduction} "
                f"for {status.value}"
            )
    for status, tiers in state.child_deduction_tiers.items():
        if tiers:
            raise ValueError(
                f"a state with no income tax has no child deduction, not tiers "
                f"for {status.value}"
            )


def _freeze(law, *names: str) -> None:
    """Replace each named mapping field of *law* with a read-only copy.

    A frozen dataclass stops a field being REASSIGNED but not a dict in it
    being changed, which would get past every check above after the year
    loaded.  The copy also cuts the law off from the caller's dict.

    Args:
        law: The dataclass instance being initialised.
        *names: Its mapping fields.
    """
    for name in names:
        object.__setattr__(law, name, MappingProxyType(dict(getattr(law, name))))


def _require_every_status(by_status: Mapping, what: str) -> None:
    """Raise unless *by_status* holds exactly one entry per filing status.

    Args:
        by_status: A mapping keyed on :class:`~app.enums.FilingStatusEnum`.
        what: What the mapping is, for the error message.

    Raises:
        ValueError: If a status is missing or a key is not a status.
    """
    if set(by_status) != set(FilingStatusEnum):
        missing = sorted(m.value for m in set(FilingStatusEnum) - set(by_status))
        raise ValueError(f"{what} must cover every filing status; missing {missing}")
