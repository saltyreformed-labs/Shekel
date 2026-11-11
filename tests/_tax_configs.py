"""The tax law that applies to one year for one profile, for tests that need it in hand.

The app resolves a profile's law in exactly one place, its paycheck pricer
(:meth:`app.services.income_service.ProfilePaychecks.law`, plan step
salary:X-at-5).  A test that prices a paycheck through the engine directly,
or reasons about the resolver itself, needs the same set without building a
pricer and the pay calendar a pricer is bound to.  This is that set, from
the same two production steps the pricer takes: slice the law to the
profile (:func:`~app.services.tax_config_service.profile_tax_series`) and
pick the year (:func:`~app.services.tax_config_service.configs_by_year`).

It was the app's ``tax_config_service.load_tax_configs_for_year`` until the
Taxes tab's report, its last caller there, began reading its law off the
primary profile's pricer.  A production function with only test callers is
the shape ruling P54 names, so it lives here; its own module because
``tests/_test_helpers.py`` is past the 1,000-line ceiling (ruling R-IR).
"""

from app.services.tax_config_service import configs_by_year, profile_tax_series


def tax_configs_for(profile, tax_year: int) -> dict[str, object]:
    """Return the tax law that applies to *tax_year* for *profile*.

    Args:
        profile: The :class:`~app.models.salary_profile.SalaryProfile`;
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
