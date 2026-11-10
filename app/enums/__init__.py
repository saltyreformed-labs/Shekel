"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables (all but :class:`TaxTypeEnum`).  The *value* of each member
is the database ``name`` column after all migrations have run.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.

**A package since the flat ``app/enums.py`` reached pylint's 1,000-line
ceiling** (it stood at exactly 1,000 lines; ruling **R-IR**: the step that
breaks a module splits it).  The move is pure: every class stands in one of five
private modules exactly as it stood in the flat one, grouped by the domain
its table serves -- :mod:`._budget`, :mod:`._salary`, :mod:`._recurrence`,
:mod:`._ledger` and :mod:`._provenance` -- and this module re-exports every
one, so ``from app.enums import X`` and ``app.enums.X`` read as they did.
"""

from ._budget import (
    AccountOpeningSourceEnum,
    AcctCategoryEnum,
    AcctTypeEnum,
    CompoundingFrequencyEnum,
    EmployerContributionTypeEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    LoanAnchorSourceEnum,
    RoleEnum,
    StatusEnum,
    TxnTypeEnum,
)
from ._ledger import (
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from ._provenance import (
    AmountSourceEnum,
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from ._recurrence import (
    BusinessDayShiftEnum,
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from ._salary import (
    CalcMethodEnum,
    FilingStatusEnum,
    PaycheckLineKindEnum,
    RaiseTypeEnum,
    TaxTypeEnum,
    WithholdingKindEnum,
)

__all__ = [
    "AccountOpeningSourceEnum",
    "AcctCategoryEnum",
    "AcctTypeEnum",
    "AmountSourceEnum",
    "BusinessDayShiftEnum",
    "CalcMethodEnum",
    "CompoundingFrequencyEnum",
    "EmployerContributionTypeEnum",
    "FilingStatusEnum",
    "GoalModeEnum",
    "IncomeUnitEnum",
    "LedgerAccountClassEnum",
    "LedgerAccountKindEnum",
    "LoanAnchorSourceEnum",
    "MovementFigureSourceEnum",
    "PaycheckLineKindEnum",
    "PeriodPlacementEnum",
    "PostingKindEnum",
    "PostingSourceEnum",
    "RaiseTypeEnum",
    "RecurrenceUnitEnum",
    "RoleEnum",
    "SettledDayBasisEnum",
    "StatementBalanceEvidenceEnum",
    "StatementSourceEnum",
    "StatusEnum",
    "TaxTypeEnum",
    "TxnTypeEnum",
    "WithholdingKindEnum",
]
