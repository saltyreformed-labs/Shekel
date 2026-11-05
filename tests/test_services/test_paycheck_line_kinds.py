"""The kind vocabulary (:mod:`app.services.paycheck_line_kinds`) covers every kind.

Plan step **salary:S11-e** (ruling **plan#2639**) split the pre-tax kind in
two.  Each kind needs a label and a group heading, or a page that words it
raises ``KeyError``, and the new kind is a DEDUCTION, so it may fund an
account and the contribution feed reads it.
"""

import pytest

from app import ref_cache
from app.enums import PaycheckLineKindEnum
from app.services import paycheck_line_kinds


class TestEveryKindIsWorded:
    """Labels, group headings and the select's offer set, kind by kind."""

    def test_every_kind_has_a_label_and_a_group_heading(self):
        """Both maps are keyed by exactly the enum's members."""
        assert set(paycheck_line_kinds.LABELS) == set(PaycheckLineKindEnum)
        assert set(paycheck_line_kinds.GROUP_LABELS) == set(PaycheckLineKindEnum)

    def test_the_two_pre_tax_kinds_carry_the_rulings_words(self):
        """Ruling plan#2639's labels, word for word, as both label and heading."""
        for member, words in (
            (PaycheckLineKindEnum.PRE_FICA_DEDUCTION, "Pre-tax, all payroll taxes"),
            (PaycheckLineKindEnum.PRE_TAX_DEDUCTION, "Pre-tax, income tax only"),
        ):
            assert paycheck_line_kinds.LABELS[member] == words
            assert paycheck_line_kinds.GROUP_LABELS[member] == words

    def test_the_select_offers_the_five_kinds_in_waterfall_order(self):
        """The all-payroll-taxes kind sits between the taxable earnings and the other pre-tax kind."""
        assert [label for _id, label in paycheck_line_kinds.kind_options()] == [
            "Taxable earning", "Pre-tax, all payroll taxes", "Pre-tax, income tax only",
            "Post-tax deduction", "After-tax earning",
        ]


class TestTheSideOfEachKind:
    """Which kinds are deductions, read by id."""

    @pytest.mark.parametrize("member, deduction", [
        (PaycheckLineKindEnum.TAXABLE_EARNING, False),
        (PaycheckLineKindEnum.PRE_FICA_DEDUCTION, True),
        (PaycheckLineKindEnum.PRE_TAX_DEDUCTION, True),
        (PaycheckLineKindEnum.POST_TAX_DEDUCTION, True),
        (PaycheckLineKindEnum.AFTER_TAX_EARNING, False),
    ])
    def test_the_three_deduction_kinds_are_deductions(self, member, deduction):
        """Both pre-tax kinds and the post-tax kind leave the paycheck; the earnings do not."""
        assert paycheck_line_kinds.is_deduction(ref_cache.paycheck_line_kind_id(member)) is deduction


class TestTheTwoPreTaxKinds:
    """The one statement of which kinds are pre-tax, and its order (task plan#2715)."""

    def test_the_all_payroll_taxes_kind_comes_first(self):
        """The engine prices a paycheck's pre-tax lines in this order, so it is pinned.

        Waterfall order: the all-payroll-taxes kind leaves the Social
        Security and Medicare wages before the income-tax-only kind leaves
        the taxable wage.
        """
        assert paycheck_line_kinds.PRE_TAX_KINDS == (
            PaycheckLineKindEnum.PRE_FICA_DEDUCTION, PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        )

    def test_the_ids_are_the_two_kinds_ids(self):
        """What the salary page's chip compares a stored id against."""
        assert paycheck_line_kinds.pre_tax_kind_ids() == frozenset({
            ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_FICA_DEDUCTION),
            ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION),
        })
