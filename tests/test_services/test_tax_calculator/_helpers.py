"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_tax_calculator.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal


# ── Test Fixtures ─────────────────────────────────────────────────


class FakeBracket:
    """Minimal stand-in for a TaxBracket ORM object."""

    def __init__(self, min_income, max_income, rate, sort_order):
        self.min_income = min_income
        self.max_income = max_income
        self.rate = rate
        self.sort_order = sort_order


class _FakeChildTier:
    """Minimal stand-in for a StateChildDeduction tier row.

    ``resolve_child_deduction_per_child`` reads only ``agi_max`` and
    ``deduction_per_child`` (the lookup keys on ``agi_max``); ``agi_min`` is
    carried for parity with the real row.
    """

    def __init__(self, agi_min, agi_max, deduction_per_child):
        self.agi_min = agi_min
        self.agi_max = agi_max
        self.deduction_per_child = deduction_per_child


class FakeBracketSet:
    """Minimal stand-in for a TaxBracketSet ORM object.

    Bracket data below is for testing only and does not represent
    actual IRS bracket values.
    """

    def __init__(
        self,
        standard_deduction=Decimal("15000"),
        child_credit_amount=Decimal("2000"),
        other_dependent_credit_amount=Decimal("500"),
        brackets=None,
        child_credit_refundable_cap=Decimal("1700"),
    ):
        self.standard_deduction = standard_deduction
        self.child_credit_amount = child_credit_amount
        self.other_dependent_credit_amount = other_dependent_credit_amount
        self.child_credit_refundable_cap = child_credit_refundable_cap
        self.brackets = brackets or []

    @property
    def withholding_rules(self):
        """The rules withholding prices on: these, as the fake states no withholding figure.

        Mirrors :attr:`app.tax_law.FederalRules.withholding_rules`, which the
        withholding formula reads (ruling plan#3213).
        """
        return self


def _single_brackets():
    """Test fixture: simplified progressive bracket structure."""
    return [
        FakeBracket(Decimal("0"),      Decimal("10000"),  Decimal("0.10"), 0),
        FakeBracket(Decimal("10000"),  Decimal("40000"),  Decimal("0.12"), 1),
        FakeBracket(Decimal("40000"),  Decimal("85000"),  Decimal("0.22"), 2),
        FakeBracket(Decimal("85000"),  Decimal("160000"), Decimal("0.24"), 3),
        FakeBracket(Decimal("160000"), Decimal("210000"), Decimal("0.32"), 4),
        FakeBracket(Decimal("210000"), Decimal("540000"), Decimal("0.35"), 5),
        FakeBracket(Decimal("540000"), None,              Decimal("0.37"), 6),
    ]


# ── Negative-Path and Boundary-Condition Tests ─────────────────────


class _FakeStateTaxConfig:
    """Minimal state tax config for negative path tests."""

    def __init__(self, flat_rate="0.045"):
        self.flat_rate = Decimal(str(flat_rate))
        self.tax_type = type("_TT", (), {"name": "flat"})()
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TaxTypeEnum  # pylint: disable=import-outside-toplevel
        self.tax_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)


class _FakeFicaConfig:
    """Minimal FICA config for negative path tests."""

    def __init__(self):
        self.ss_rate = Decimal("0.062")
        self.ss_wage_base = Decimal("168600")
        self.medicare_rate = Decimal("0.0145")
        self.medicare_surtax_rate = Decimal("0.009")
        self.medicare_surtax_threshold = Decimal("200000")


# ── capped_social_security helper (CRIT-03 / F-037) ───────────────


class _FicaConfig184500:
    """Fixed FicaConfig stub at the 2026-seed wage base.

    capped_social_security only reads `ss_rate` and `ss_wage_base`.
    Medicare attributes are populated because test_calculate_fica_delegates_to_helper
    exercises the full calculate_fica pipeline (which reads them) to prove
    the bracket path also routes through the helper.
    """

    def __init__(self):
        self.ss_rate = Decimal("0.062")
        self.ss_wage_base = Decimal("184500")
        self.medicare_rate = Decimal("0.0145")
        self.medicare_surtax_rate = Decimal("0.009")
        self.medicare_surtax_threshold = Decimal("200000")


# ── Annual Filing-Time Federal Liability (T-P1) ────────────────────


def _federal_2026_single():
    """The 2026 single filer's federal rules, as ``app.tax_law`` states them.

    Mirrors the 2026 law's single-filer rules (``DEFAULT_FEDERAL_BRACKETS
    [2026]["single"]`` in the ``tax_seed_data`` seed until plan step
    salary:X-at-1) so the annual liability arithmetic is hand-confirmed
    against the app's own figures (std deduction 16,100; CTC 2,200 per
    OBBBA; ODC 500; refundable ACTC cap 1,700).
    """
    return FakeBracketSet(
        standard_deduction=Decimal("16100"),
        child_credit_amount=Decimal("2200"),
        other_dependent_credit_amount=Decimal("500"),
        child_credit_refundable_cap=Decimal("1700"),
        brackets=[
            FakeBracket(Decimal("0"),      Decimal("12400"),  Decimal("0.10"), 0),
            FakeBracket(Decimal("12400"),  Decimal("50400"),  Decimal("0.12"), 1),
            FakeBracket(Decimal("50400"),  Decimal("105700"), Decimal("0.22"), 2),
            FakeBracket(Decimal("105700"), Decimal("201775"), Decimal("0.24"), 3),
            FakeBracket(Decimal("201775"), Decimal("256225"), Decimal("0.32"), 4),
            FakeBracket(Decimal("256225"), Decimal("640600"), Decimal("0.35"), 5),
            FakeBracket(Decimal("640600"), None,              Decimal("0.37"), 6),
        ],
    )


class _FakeNCStateConfig:
    """The seeded 2026 NC flat state config (rate 3.99%, std ded 12,750)."""

    def __init__(self, flat_rate="0.0399", standard_deduction="12750"):
        self.flat_rate = Decimal(flat_rate)
        self.standard_deduction = Decimal(standard_deduction)
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import TaxTypeEnum  # pylint: disable=import-outside-toplevel
        self.tax_type_id = ref_cache.tax_type_id(TaxTypeEnum.FLAT)


def _federal_2026_mfj():
    """The 2026 married-filing-jointly federal rules, as ``app.tax_law`` states them.

    Mirrors the 2026 law's married-filing-jointly rules
    (``DEFAULT_FEDERAL_BRACKETS[2026]["married_jointly"]`` in the
    ``tax_seed_data`` seed until plan step salary:X-at-1): std
    deduction 32,200; CTC 2,200; ODC 500; refundable ACTC cap 1,700.
    """
    return FakeBracketSet(
        standard_deduction=Decimal("32200"),
        child_credit_amount=Decimal("2200"),
        other_dependent_credit_amount=Decimal("500"),
        child_credit_refundable_cap=Decimal("1700"),
        brackets=[
            FakeBracket(Decimal("0"),      Decimal("24800"),  Decimal("0.10"), 0),
            FakeBracket(Decimal("24800"),  Decimal("100800"), Decimal("0.12"), 1),
            FakeBracket(Decimal("100800"), Decimal("211400"), Decimal("0.22"), 2),
            FakeBracket(Decimal("211400"), Decimal("403550"), Decimal("0.24"), 3),
            FakeBracket(Decimal("403550"), Decimal("512450"), Decimal("0.32"), 4),
            FakeBracket(Decimal("512450"), Decimal("768700"), Decimal("0.35"), 5),
            FakeBracket(Decimal("768700"), None,              Decimal("0.37"), 6),
        ],
    )
