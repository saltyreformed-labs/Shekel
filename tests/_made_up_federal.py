"""The federal side of a test's made-up tax year: one filing status's rules, and the credits.

A made-up year (:func:`tests._test_helpers.made_up_year`) carries one set of
:class:`~app.tax_law.FederalRules` for every filing status and states its
per-child credits once (:class:`~app.tax_law.FederalCredits`), as the law
states them (ruling plan#3340).  These builders lived in
``tests/_test_helpers.py`` until plan step plan#3206 took the credits off the
per-status rules; that module was over the 1,000-line ceiling (ruling
R-BAL301), so the federal builders moved here rather than add lines to it.
"""

from decimal import Decimal

from app.tax_law import FederalCredits, FederalRules, ladder


def zero_federal():
    """Return federal rules that price $0.00 at every income.

    What a made-up year states for its federal rules when the test's rows held
    no bracket set: a year cannot lack federal rules, and a single 0% rung
    prices what no row priced on a paycheck.  The two differ in two places no
    test installing this reaches: the withholding adds the W-4's extra
    withholding to a bracket figure, and the annual liability REFUSES a missing
    bracket set where it prices these rules at $0.00.

    Returns:
        FederalRules: Zero deduction, one 0% rung from $0.
    """
    return FederalRules(
        standard_deduction=Decimal("0.00"),
        brackets=ladder(("0.00", None, "0.0000")),
    )


def made_up_federal():
    """Return the made-up federal rules ``seed_tax_bracket_set`` wrote as rows.

    The same deduction and two rungs; the row's credits are
    :func:`made_up_credits`.  The rows' ladder STOPPED at $47,150, which a law
    refuses (income above a closed top would be taxed by nothing), so an open
    rung carries it on, and since a ladder's rates climb (finding plan#3208)
    it taxes 22%, the rate of the rung above these two in the single filer's
    2024 table they match.  The tax on taxable income up to $47,150 is the
    rows' tax; above it these rules tax 22% where the rows taxed nothing, and
    :func:`app.services.tax_calculator.marginal_rate_for` reads 22% where the
    rows read 12%.

    Returns:
        FederalRules: The made-up single filer's rules.
    """
    return FederalRules(
        standard_deduction=Decimal("14600.00"),
        brackets=ladder(
            ("0.00", "11600.00", "0.1000"),
            ("11600.00", "47150.00", "0.1200"),
            ("47150.00", None, "0.2200"),
        ),
    )


def zero_credits():
    """Return per-child credits of $0.00, a made-up year's unless a test states others.

    What a made-up year states beside :func:`zero_federal`: the rows that held
    no bracket set held no credit either.

    Returns:
        FederalCredits: Every credit and the refundable cap $0.00.
    """
    return FederalCredits(
        child_credit_amount=Decimal("0.00"),
        other_dependent_credit_amount=Decimal("0.00"),
        child_credit_refundable_cap=Decimal("0.00"),
    )


def made_up_credits():
    """Return the made-up per-child credits ``seed_tax_bracket_set`` wrote on its row.

    $2,000 a child and $500 an other dependent, beside :func:`made_up_federal`.
    The refundable credit cap is the column default the rows took, $0.00.

    Returns:
        FederalCredits: The made-up year's credits.
    """
    return FederalCredits(
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("0.00"),
    )
