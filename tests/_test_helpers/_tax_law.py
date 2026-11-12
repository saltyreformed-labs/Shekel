"""The tax law a test prices under (plan step salary:X-at-1): zero and made-up federal, FICA and
state rules, and the laws the seed helpers wrote.
"""

from decimal import Decimal

from app.enums import FilingStatusEnum, TaxTypeEnum
from app.tax_law import FederalRules, FicaRules, StateYearLaw, TaxLaw, TaxYearLaw, ladder


# --- The tax law a test prices under (plan step salary:X-at-1) --------------
#
# The law lives once, in :mod:`app.tax_law`, and every test prices under it
# unless the test installs another through the ``tax_law`` fixture in
# ``tests/conftest.py`` (ruling salary:R-SAL80, "Real law, tests may swap").
# Until X-at-1 a test wrote its law as per-user tax ROWS through three seeders
# here; the builders below make the same figures as a law, so a test that
# seeded those rows installs exactly what it priced before and no expected
# figure moves.  A law must pass :mod:`app.tax_law._types`' checks, which the
# rows never had to, and each builder's docstring says where that shaped it.

#: A made-up law's one source line: a test's figures cite no document.
MADE_UP_SOURCES = ("A test's made-up figures, not a published law",)


#: No year at all: every kind resolves no rules.  It lists no state either,
#: so since plan step salary:X-at-3 it prices no PROFILE -- the engine refuses
#: a state the law does not list (ruling salary:R-SAL78) -- and is what the
#: tax-law alarms' tests judge.  A test pricing a profile with no tax installs
#: :data:`NO_TAX_LAW`.
EMPTY_TAX_LAW = TaxLaw(years=())


def zero_federal():
    """Return federal rules that price $0.00 at every income, with no credits.

    What a made-up year states for its federal rules when the test's rows held
    no bracket set: a year cannot lack federal rules, and a single 0% rung
    prices what no row priced on a paycheck.  The two differ in two places no
    test installing this reaches: the withholding adds the W-4's extra
    withholding to a bracket figure, and the annual liability REFUSES a missing
    bracket set where it prices these rules at $0.00.

    Returns:
        FederalRules: Zero deduction, zero credits, one 0% rung from $0.
    """
    return FederalRules(
        standard_deduction=Decimal("0.00"),
        child_credit_amount=Decimal("0.00"),
        other_dependent_credit_amount=Decimal("0.00"),
        child_credit_refundable_cap=Decimal("0.00"),
        brackets=ladder(("0.00", None, "0.0000")),
    )


def made_up_federal():
    """Return the made-up federal rules ``seed_tax_bracket_set`` wrote as rows.

    The same deduction, credits and two rungs.  The rows' ladder STOPPED at
    $47,150, which a law refuses (income above a closed top would be taxed by
    nothing), so a 0% open rung carries it on: the tax on every income is the
    rows' tax, and only :func:`app.services.tax_calculator.marginal_rate_for`
    above $47,150 reads 0% where the rows read 12%.  The refundable credit cap
    is the column default the rows took, $0.00.

    Returns:
        FederalRules: The made-up single filer's rules.
    """
    return FederalRules(
        standard_deduction=Decimal("14600.00"),
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("0.00"),
        brackets=ladder(
            ("0.00", "11600.00", "0.1000"),
            ("11600.00", "47150.00", "0.1200"),
            ("47150.00", None, "0.0000"),
        ),
    )


def zero_fica():
    """Return FICA rules that price $0.00 Social Security and Medicare.

    What a made-up year states when the test's rows held no FICA row: a year
    cannot lack FICA, and zero rates price what no row priced on a paycheck.
    The wage base and threshold must be above zero, so they carry
    ``made_up_fica``'s -- which caps the Taxes tab's W-2 preview of Social
    Security wages at that base where no row left it uncapped; no test
    installing this renders that preview.

    Returns:
        FicaRules: Every rate 0.
    """
    return FicaRules(
        ss_rate=Decimal("0.0000"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0000"),
        medicare_surtax_rate=Decimal("0.0000"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )


def made_up_fica():
    """Return the made-up FICA rules ``seed_fica_config`` wrote as a row.

    Returns:
        FicaRules: 6.2% on a $176,100 base, 1.45%, 0.9% over $200,000.
    """
    return FicaRules(
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )


def made_up_state(rate, standard_deduction=Decimal("25500.00")):
    """Return a made-up flat-rate state, as ``seed_state_tax_config`` wrote it.

    The row carried ONE filing status (single); a law states every status, so
    each carries the row's deduction.  The row held no child deduction tiers.

    Args:
        rate: The flat rate as a fraction (``Decimal("0.0399")``).
        standard_deduction: Every status's standard deduction.

    Returns:
        StateYearLaw: The made-up state.
    """
    return StateYearLaw(
        tax_type=TaxTypeEnum.FLAT,
        flat_rate=rate,
        standard_deduction={status: standard_deduction for status in FilingStatusEnum},
        child_deduction_tiers={status: () for status in FilingStatusEnum},
    )


def no_income_tax_state():
    """Return a state with no income tax: the explicit ``$0.00`` entry (ruling salary:R-SAL78).

    Tax type ``NONE``, no rate, a ``$0.00`` standard deduction and no child
    deduction for every filing status.  Since plan step salary:X-at-3 the law
    must LIST a profile's state or the engine refuses the profile, so a test
    whose figures carry no state tax lists the profile's state this way rather
    than leaving it out.

    Returns:
        StateYearLaw: The made-up untaxed state.
    """
    return StateYearLaw(
        tax_type=TaxTypeEnum.NONE,
        flat_rate=None,
        standard_deduction={status: Decimal("0.00") for status in FilingStatusEnum},
        child_deduction_tiers={status: () for status in FilingStatusEnum},
    )


def made_up_year(tax_year=2026, *, federal=None, fica=None, states=None):
    """Return one made-up tax year, one set of federal rules for every status.

    The rows these years replace carried ONE status (single); every status
    carries the same rules here, which a single-filer test cannot see.

    Args:
        tax_year: The year.
        federal: The federal rules every filing status carries
            (default :func:`zero_federal`).
        fica: The year's FICA rules (default :func:`zero_fica`).
        states: ``{state_code: StateYearLaw}`` (default none).

    Returns:
        TaxYearLaw: The made-up year.
    """
    rules = federal if federal is not None else zero_federal()
    return TaxYearLaw(
        tax_year=tax_year,
        sources=MADE_UP_SOURCES,
        federal={status: rules for status in FilingStatusEnum},
        fica=fica if fica is not None else zero_fica(),
        states=states if states is not None else {},
    )


def bracket_set_law(*tax_years):
    """Return the law ``seed_tax_bracket_set`` wrote, once per year asked for.

    The rows held a bracket set and nothing else, so each year carries
    :func:`made_up_federal` for every status, zero FICA and no state.

    Args:
        *tax_years: The years the rows were written for (default 2026 alone).

    Returns:
        TaxLaw: Those years, oldest first.
    """
    return TaxLaw(years=tuple(
        made_up_year(year, federal=made_up_federal())
        for year in sorted(tax_years or (2026,))
    ))


#: No tax on any line, and North Carolina listed: one made-up year whose
#: federal rules and FICA price $0.00 (:func:`zero_federal`,
#: :func:`zero_fica`) and whose one state has no income tax
#: (:func:`no_income_tax_state`).  What a test pricing a profile with no tax
#: installs since plan step salary:X-at-3, where it installed
#: :data:`EMPTY_TAX_LAW` until the engine began refusing a state the law does
#: not list.  It prices every paycheck line at $0.00 as the empty law did,
#: with the two differences :func:`zero_federal` names -- a W-4's extra
#: withholding is added to federal, and the annual liability prices $0.00
#: where it refused -- and a third: the tax-law banner, which the empty law
#: shows on every page, shows under this one only from November 1, 2026,
#: when 2027 falls due.  It also lists NC on the Settings tax-law card where
#: the empty law listed nothing.  None of the tests that moved to it asserts
#: on any of these (each passed unchanged on it, 2026-10-08).
NO_TAX_LAW = TaxLaw(years=(made_up_year(2026, states={"NC": no_income_tax_state()}),))


def fica_only_law(tax_year=2026):
    """Return the law ``seed_fica_config`` alone wrote: made-up FICA, and NC untaxed.

    Federal prices $0.00 on the zero rules a year cannot lack.  The row held
    no state, which priced no state tax; since plan step salary:X-at-3 the
    engine refuses a state the law does not list (ruling salary:R-SAL78), so
    North Carolina -- every caller's profile's state -- is listed with no
    income tax (:func:`no_income_tax_state`), which prices the same $0.00.

    Args:
        tax_year: The one year the row was written for.

    Returns:
        TaxLaw: That one year.
    """
    return TaxLaw(years=(
        made_up_year(
            tax_year, fica=made_up_fica(), states={"NC": no_income_tax_state()},
        ),
    ))


def state_and_fica_law(rate=Decimal("0.0399"), tax_year=2026):
    """Return the law the ``seed_state_tax_config`` + ``seed_fica_config`` pair wrote.

    The commonest setup the rows had: a North Carolina flat rate and the
    made-up FICA, and no bracket set, so federal prices $0.00.

    Args:
        rate: The North Carolina flat rate.
        tax_year: The one year the rows were written for.

    Returns:
        TaxLaw: That one year.
    """
    return TaxLaw(years=(
        made_up_year(
            tax_year, fica=made_up_fica(), states={"NC": made_up_state(rate)},
        ),
    ))


def made_up_law(*years):
    """Return a law of consecutive made-up years, each ``(tax_year, {state: rate})``.

    The tax-law alarms' tests (plan step salary:X-at-4) vary WHICH years and
    states the law lists, never the figures, so each year states
    :func:`made_up_year`'s defaults (:func:`zero_federal` and
    :func:`zero_fica`) and a made-up flat rate per state it lists (``{}`` for
    a year that lists none).

    Args:
        *years: ``(tax_year, {state_code: rate_string})`` per year, oldest
            first and consecutive (:class:`~app.tax_law.TaxLaw` refuses a
            skipped year).

    Returns:
        TaxLaw: Those years.
    """
    return TaxLaw(years=tuple(
        made_up_year(tax_year, states={
            state: made_up_state(Decimal(rate)) for state, rate in states.items()
        })
        for tax_year, states in years
    ))
