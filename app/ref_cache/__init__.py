"""
Shekel Budget App -- Reference Table Cache

Loads reference table IDs once at application startup so that service
and route code can resolve enum members to integer IDs without hitting
the database on every request.

Usage::

    from app import ref_cache
    from app.enums import StatusEnum, AcctTypeEnum

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    checking_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

The cache is initialized by ``create_app()`` after reference tables
are seeded.  If any enum member has no corresponding database row,
``init()`` raises ``RuntimeError`` -- the app refuses to start with
an incomplete reference schema.

Thread safety is NOT provided.  This is a single-user, single-process
Flask application; the cache is written once at startup and read-only
thereafter.

**A PACKAGE since plan step bank_import:X-f6e-1, and the split was forced by a
gate rather than chosen for tidiness**: a twenty-sixth reference table took the
flat module to 1,013 lines against pylint's 1,000-line ceiling, with four lines
of headroom before it.  Two halves, along the seam the module already had --
:mod:`._state` decides what is cached and refuses a reader who asks before it
is, :mod:`._accessors` answers "what id is this enum member" -- and this file
re-exports every name the package DEFINES, so the 98 modules that
``from app import ref_cache`` are untouched.  It is the shape
``app.services.balance_at`` and ``app.services.statement_import`` already use,
which is also what gives ``shekel-private-module-import`` a private surface to
guard.

**What it deliberately does NOT re-export is the flat module's import
leakage**: 26 enum classes and 6 stdlib bindings that were public names only
because they were imported at the top of one file.  Measured across the whole
repository before the split: 0 references to any of them through this
namespace.  ``__all__`` is therefore the surface, and
``tests/test_ref_cache.py`` pins it -- without that, a name dropped from the
re-export block would surface only as an ``AttributeError`` in whatever test
happened to call it, and two of these accessors have no caller at all to raise
one.

**The cap is a forcing function, not a ceiling to raise** (the planning
conventions' rule 4's own argument, applied to code): the real pressure is that these
accessors are twenty-six near-identical two-line bodies -- two of them
(``acct_type_icon``, ``acct_type_max_term``) measurably DEAD, 0 references
anywhere -- and collapsing what survives into one generic lookup is finding
**N-341**, owned by its own step.
"""

from ._accessors import (
    account_opening_source_id,
    acct_category_id,
    acct_category_member,
    acct_type_icon,
    acct_type_id,
    acct_type_max_term,
    amount_source_id,
    business_day_shift_id,
    business_day_shift_member,
    calc_method_id,
    compounding_frequency_id,
    filing_status_member,
    paycheck_line_kind_id,
    paycheck_line_kind_member,
    employer_contribution_type_id,
    goal_mode_id,
    income_unit_id,
    ledger_account_class_id,
    ledger_account_kind_id,
    ledger_class_is_debit_normal,
    loan_anchor_source_id,
    period_placement_id,
    posting_kind_id,
    posting_source_id,
    raise_type_id,
    raise_type_member,
    recurrence_unit_id,
    role_id,
    movement_figure_source_id,
    settled_day_basis_id,
    statement_balance_evidence_id,
    statement_balance_evidence_member,
    statement_source_id,
    status_id,
    tax_type_id,
    transaction_type_is_income,
    txn_type_id,
    withholding_kind_id,
)
from ._state import (
    init,
)

__all__ = [
    "account_opening_source_id",
    "acct_category_id",
    "acct_category_member",
    "acct_type_icon",
    "acct_type_id",
    "acct_type_max_term",
    "amount_source_id",
    "business_day_shift_id",
    "business_day_shift_member",
    "calc_method_id",
    "compounding_frequency_id",
    "filing_status_member",
    "paycheck_line_kind_id",
    "paycheck_line_kind_member",
    "employer_contribution_type_id",
    "goal_mode_id",
    "income_unit_id",
    "init",
    "ledger_account_class_id",
    "ledger_account_kind_id",
    "ledger_class_is_debit_normal",
    "loan_anchor_source_id",
    "period_placement_id",
    "posting_kind_id",
    "posting_source_id",
    "raise_type_id",
    "raise_type_member",
    "recurrence_unit_id",
    "role_id",
    "movement_figure_source_id",
    "settled_day_basis_id",
    "statement_balance_evidence_id",
    "statement_balance_evidence_member",
    "statement_source_id",
    "status_id",
    "tax_type_id",
    "transaction_type_is_income",
    "txn_type_id",
    "withholding_kind_id",
]
