"""The tax law that applies to one year, for tests that need it in hand.

The app resolves a profile's law in exactly one place, its paycheck pricer
(:meth:`app.services.income_service.ProfilePaychecks.law`, plan step
salary:X-at-5).  A test that prices a paycheck through the engine directly,
or reasons about the resolver itself, needs the same set without building a
pricer and the pay calendar a pricer is bound to.  :func:`tax_configs_for` is
that set, from the same two production steps the pricer takes: slice the law
to the profile (:func:`~app.services.tax_config_service.profile_tax_series`)
and pick the year (:func:`~app.services.tax_config_service.configs_by_year`).

It was the app's ``tax_config_service.load_tax_configs_for_year`` until the
Taxes tab's report, its last caller there, began reading its law off the
primary profile's pricer.  A production function with only test callers is
the shape ruling P54 names, so it lives here; its own module because
``tests/_test_helpers.py`` was past the 1,000-line ceiling (ruling R-IR).

**A test that builds a config set or a paycheck's tax lines BY HAND** -- with
fake rules, to price exact figures -- builds it through :func:`hand_built_law`
or names its tax lines' :func:`own_year_rules`.  Since plan step salary:X-at-6
the engine carries each set's ``basis`` onto the paycheck's tax lines as
their ``rules`` (ruling R-SAL75), and both are required, so a hand-built set
is a faithful stand-in for the resolver's only when it carries one.
"""

from app.services.tax_config_service import (
    FederalTaxRules,
    StateTaxRules,
    TaxLawBasis,
    configs_by_year,
    profile_tax_series,
)
from app.tax_law import FicaRules


def tax_configs_for(profile, tax_year: int) -> dict[str, object]:
    """Return the tax law that applies to *tax_year* for *profile*.

    Args:
        profile (SalaryProfile): The
            :class:`~app.models.salary_profile.SalaryProfile`;
            ``filing_status_id`` and ``state_code`` are read, and its ``id``
            and ``name`` for a refusal.
        tax_year: The tax year whose rules are wanted.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config`` and
            ``basis``, as the pricer's ``law`` answers them.

    Raises:
        UnsupportedStateError: The law does not list the profile's state.
    """
    return configs_by_year(profile_tax_series(profile), (tax_year,))[tax_year]


def own_year_rules(tax_year: int) -> TaxLawBasis:
    """Return the basis of tax lines priced wholly on *tax_year*'s own rules.

    Nothing substituted: every part's year is *tax_year*, which is what the
    resolver answers for a year the law carries whole.

    Args:
        tax_year: The year the lines were priced for.

    Returns:
        The :class:`~app.services.tax_config_service.TaxLawBasis`.
    """
    return TaxLawBasis(
        tax_year=tax_year, federal_year=tax_year, state_year=tax_year,
        fica_year=tax_year,
    )


def hand_built_law(
    tax_year: int, *,
    bracket_set: FederalTaxRules | None,
    state_config: StateTaxRules | None,
    fica_config: FicaRules | None,
) -> dict[str, object]:
    """Return a config set built by hand, shaped as the resolver hands one back.

    Each part given rules is on *tax_year*'s own, and each given ``None`` has
    no year: the resolver's own pairing, a part's year being ``None`` exactly
    when its rules are (:class:`~app.services.tax_config_service.TaxLawBasis`).

    Args:
        tax_year: The year the set is for.
        bracket_set: The federal rules, or ``None``; a test's fake of them
            (``FakeFederalRules``) stands in by the attributes the engine reads.
        state_config: The state rules, or ``None``, a fake likewise.
        fica_config: The Social Security and Medicare rules, or ``None``, a
            fake likewise.

    Returns:
        dict: Keys ``bracket_set``, ``state_config``, ``fica_config`` and
            ``basis``.
    """
    return {
        "bracket_set": bracket_set,
        "state_config": state_config,
        "fica_config": fica_config,
        "basis": TaxLawBasis(
            tax_year=tax_year,
            federal_year=None if bracket_set is None else tax_year,
            state_year=None if state_config is None else tax_year,
            fica_year=None if fica_config is None else tax_year,
        ),
    }
