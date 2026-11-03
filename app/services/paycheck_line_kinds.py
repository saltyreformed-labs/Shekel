"""Shekel Budget App -- What a payroll line's KIND means, stated once.

Plan step **salary:R18-b** (ruling **R-SAL38**; ledger row **D59**): a
paycheck is base pay plus a list of lines, and a line's kind is its position
in the waterfall.  The engine reads the kind as an id
(:mod:`app.services.paycheck_calculator._lines`), and reads which kinds are
pre-tax here; everything that WORDS a kind or asks which SIDE it is on
reads it here too, so the labels, the two sides, the pre-tax kinds and the
select's offer set have one home rather than a spelling per template.

* :data:`LABELS` -- the label each kind renders as ("Taxable earning",
  "Pre-tax, all payroll taxes", ...), the one spelling the salary page's
  chip, its kind select (through :func:`kind_options`) and the pay stub
  door read.  Until R18-b the surfaces derived a label from the ref row's
  name (``pre_tax_deduction`` -> "Pre-Tax-Deduction"), the second of R18-a's
  three accepted display changes; this is where R18-b re-words them.  The
  two pre-tax labels are ruling **plan#2639**'s own words (plan step
  salary:S11-e), which split the one pre-tax kind in two.
* :data:`GROUP_LABELS` -- the heading over a GROUP of one kind's lines, the
  cockpit's anatomy card's.  Its own map since S11-e: the card made a
  heading by appending "s" to the label, which the two pre-tax labels do
  not survive ("Pre-tax, all payroll taxess").
* :data:`PRE_TAX_KINDS` -- the two pre-tax kinds in waterfall order, the
  all-payroll-taxes kind first (ruling **plan#2639**, plan step
  salary:S11-e).  The one spelling of "both pre-tax kinds": the engine
  prices a paycheck's pre-tax lines in this order, a pay stub's pre-tax
  total sums them, the stub picker's taxed kinds hold them, and the salary
  page's chip marks them (through :func:`pre_tax_kind_ids`).
* :func:`is_deduction` -- which side a kind is on.  A DEDUCTION may name the
  account it funds (``target_account_id``, the contribution feed's hook);
  an earning is money IN and funds nothing, so the schema refuses a target
  on an earning kind through this predicate.
* :func:`kind_options` -- the select's ``(id, label)`` pairs in waterfall
  order, the enum's own order.

Flask-free: reads the ref cache and returns plain values.
"""

from app import ref_cache
from app.enums import PaycheckLineKindEnum

#: The label each kind renders as, keyed by member.  Spelled once; the
#: waterfall order is the enum's.
LABELS: dict[PaycheckLineKindEnum, str] = {
    PaycheckLineKindEnum.TAXABLE_EARNING: "Taxable earning",
    PaycheckLineKindEnum.PRE_FICA_DEDUCTION: "Pre-tax, all payroll taxes",
    PaycheckLineKindEnum.PRE_TAX_DEDUCTION: "Pre-tax, income tax only",
    PaycheckLineKindEnum.POST_TAX_DEDUCTION: "Post-tax deduction",
    PaycheckLineKindEnum.AFTER_TAX_EARNING: "After-tax earning",
}

#: The heading over a group of one kind's lines, keyed by member.  The two
#: pre-tax labels already name a group, so they stand as they are.
GROUP_LABELS: dict[PaycheckLineKindEnum, str] = {
    PaycheckLineKindEnum.TAXABLE_EARNING: "Taxable earnings",
    PaycheckLineKindEnum.PRE_FICA_DEDUCTION: "Pre-tax, all payroll taxes",
    PaycheckLineKindEnum.PRE_TAX_DEDUCTION: "Pre-tax, income tax only",
    PaycheckLineKindEnum.POST_TAX_DEDUCTION: "Post-tax deductions",
    PaycheckLineKindEnum.AFTER_TAX_EARNING: "After-tax earnings",
}

#: The two pre-tax kinds, in waterfall order: the "Pre-tax, all payroll
#: taxes" kind leaves the Social Security and Medicare wages and the taxable
#: wage, then the "Pre-tax, income tax only" kind leaves the taxable wage
#: alone.  Every reader of "both pre-tax kinds" in the code and the templates
#: reads this tuple.  The stylesheet is the one exception
#: (``salary.css``): it gives the cockpit's bars a class suffix per kind key,
#: so it names both pre-tax kinds in two selectors, as a display rule.  The
#: order is taken from the enum, which is the waterfall order, so it is not
#: stated a second time here.
PRE_TAX_KINDS: tuple[PaycheckLineKindEnum, ...] = tuple(
    member for member in PaycheckLineKindEnum
    if member in {PaycheckLineKindEnum.PRE_FICA_DEDUCTION, PaycheckLineKindEnum.PRE_TAX_DEDUCTION}
)

#: The deduction side: the kinds that LEAVE the paycheck and may fund an account.
DEDUCTION_KINDS: frozenset[PaycheckLineKindEnum] = frozenset({
    *PRE_TAX_KINDS,
    PaycheckLineKindEnum.POST_TAX_DEDUCTION,
})

#: The one refusal an earning kind has: a target account.  Spelled once for
#: the two doors that state it -- the schema over a POSTED pair, the update
#: route over the EFFECTIVE pair (a posted kind beside a STORED target the
#: payload left alone).
EARNING_TARGET_REFUSAL = (
    "Only a deduction can fund an account; an earning line is paid to you "
    "and has no target account."
)


def is_deduction(kind_id: int) -> bool:
    """Whether *kind_id* is one of the three deduction kinds.

    Args:
        kind_id: A stored or submitted ``paycheck_line_kind_id``.

    Returns:
        ``True`` for either pre-tax kind or a post-tax deduction; ``False``
        for an earning kind, and for an id the cache does not hold (which the
        FK refuses).
    """
    return ref_cache.paycheck_line_kind_member(kind_id) in DEDUCTION_KINDS


def pre_tax_kind_ids() -> frozenset[int]:
    """Return the ids of :data:`PRE_TAX_KINDS`, for a reader holding a stored id.

    The salary page's kind chip reads it to mark both pre-tax kinds, so the
    template compares a line's id against this set rather than spelling the
    two kinds itself.

    Returns:
        The two ``ref.paycheck_line_kinds`` ids.
    """
    return frozenset(ref_cache.paycheck_line_kind_id(member) for member in PRE_TAX_KINDS)


def kind_options() -> list[tuple[int, str]]:
    """Return every kind as ``(id, label)``, in waterfall order, for a select.

    Returns:
        The five pairs, taxable earning first and after-tax earning last.
    """
    return [
        (ref_cache.paycheck_line_kind_id(member), LABELS[member])
        for member in PaycheckLineKindEnum
    ]


__all__ = [
    "DEDUCTION_KINDS", "EARNING_TARGET_REFUSAL", "GROUP_LABELS", "LABELS", "PRE_TAX_KINDS",
    "is_deduction", "kind_options", "pre_tax_kind_ids",
]
