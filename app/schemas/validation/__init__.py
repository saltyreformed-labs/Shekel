"""
Shekel Budget App -- Marshmallow Validation Schemas

Validates and deserializes incoming request data.  Used by routes
to keep controllers thin and push validation logic out of Flask.

Percent / decimal-rate convention (E-28 / HIGH-06, Commit 24)
-------------------------------------------------------------

Percentage rate fields (inflation, APY,
trend alert threshold, etc.) are stored as decimal fractions in the
database -- a 6.2% rate is persisted as ``Decimal("0.0620")`` in a
``Numeric(5, 4)`` or ``Numeric(7, 5)`` column with a database
CHECK constraint pinning the value to ``[0, 1]`` (and similar
ranges for the few "match-multiplier" cases).  The forms that
collect these values render the user-facing percent
(``0.0620 * 100 == 6.20``).

E-28 collapses the storage / schema / form domain split into one
consistent rule: **schemas validate the stored fraction**, and the
percent-to-fraction conversion happens in the schema's own
``@pre_load`` hook so the storage tier (DB CHECK) and the
validation tier (Marshmallow ``Range``) accept and reject exactly
the same set of values.  The shared helper
:func:`_normalize_percent_fields` divides each declared percent
field by 100 before the field's ``Range`` validator runs, and the
``_PERCENT_FIELDS`` class attribute on each schema enumerates the
fields that need conversion (an explicit declaration, not an
implicit name pattern, so a future renamer cannot silently break
the conversion).  Routes that consume one of these schemas no
longer divide -- the loaded value is already the fraction the DB
expects.

The DB CHECK constraints added by commit C-24 of the 2026-04-15
security remediation plan are the storage-tier counterpart to these
validators; they are deliberately not redundant because they catch
raw-SQL bypasses of the route layer.  See
``migrations/versions/<C-24>_marshmallow_range_check_sweep.py`` for
the storage-tier bounds and keep both sides in sync when adding a
new column.

Monetary range validators
-------------------------

Pure monetary fields (deduction amount, SalaryProfile W-4 fields,
YTD tax checkpoint figures, etc.) get ``Range(min=...)`` validators per
commit C-24.  The minimum mirrors the database CHECK (``>= 0`` or
``> 0`` per column); the maximum is set well below the column's
storage limit but above any plausible real-world value, so a typo
that injects an extra digit is rejected with a clean field-level
400 instead of being silently committed.

Package layout
--------------

The historical 2,937-line ``validation.py`` module is split (Phase-3
pylint cleanup) into per-domain sub-modules, mirroring the route
packages that consume them.  Every public schema is re-exported here so
``from app.schemas.validation import XSchema`` keeps working unchanged.
Shared primitives (base schema, range validators, percent-conversion
helper, envelope-on-income rule) live in :mod:`._helpers`.
"""

from app.schemas.validation._helpers import (
    EFFECTIVE_DATE_MAX,
    EFFECTIVE_DATE_MIN,
    ShownLinesSchema,
    form_payload,
)
from app.schemas.validation._recurrence import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_MAX_PER_MONTH_KEY,
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_REPEATS_ON_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.schemas.validation.accounts import (
    AccountCreateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AccountUpdateSchema,
    AnchorUpdateSchema,
    OpeningRestatementSchema,
    AppreciationParamsUpdateSchema,
    InterestParamsCreateSchema,
    InterestParamsUpdateSchema,
)
from app.schemas.validation.auth import (
    ChangePasswordSchema,
    CompanionCreateSchema,
    CompanionEditSchema,
    LoginSchema,
    MfaConfirmSchema,
    MfaDisableSchema,
    MfaVerifySchema,
    ReauthSchema,
    RegisterSchema,
)
from app.schemas.validation.cards import (
    CardAprSchema,
    CardPaymentSchema,
    CreditCardTermsSchema,
)
from app.schemas.validation.categories import (
    CategoryCreateSchema,
    CategoryEditSchema,
)
from app.schemas.validation.debt_strategy import DebtStrategyCalculateSchema
from app.schemas.validation.entries import (
    EntryCreateSchema,
    EntryUpdateSchema,
)
from app.schemas.validation.investments import (
    InvestmentContributionTransferSchema,
    InvestmentParamsCreateSchema,
    InvestmentParamsUpdateSchema,
)
from app.schemas.validation.loans import (
    EscrowComponentSchema,
    EscrowLineMergeSchema,
    EscrowLineRenameSchema,
    EscrowVersionSchema,
    LoanAnchorTrueupSchema,
    LoanParamsCreateSchema,
    LoanParamsUpdateSchema,
    LoanPaymentExtraSchema,
    LoanPaymentTransferSchema,
    PayoffCalculatorSchema,
    RateChangeSchema,
    RefinanceSchema,
)
from app.schemas.validation._pay_rhythm import (
    cadence_kind_token,
    rhythm_to_wire,
)
from app.schemas.validation.pay_periods import (
    PayHistorySchema,
    PayPeriodExtendSchema,
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    PayPeriodRemoveEarlierSchema,
    PayPeriodResetSchema,
    PayPeriodTruncateSchema,
    PayScheduleSchema,
)
from app.schemas.validation.pay_stubs import (
    PayStubFigureSchema,
    PayStubLineSchema,
    PayStubOneOffSchema,
    PayStubPaydaySchema,
    PayStubSchema,
)
from app.schemas.validation.retirement import (
    PensionProfileCreateSchema,
    PensionProfileUpdateSchema,
    RetirementReadinessQuerySchema,
    RetirementSettingsSchema,
)
from app.schemas.validation.salary import (
    PaycheckLineCreateSchema,
    PaycheckLineUpdateSchema,
    RaiseCreateSchema,
    RaiseUpdateSchema,
    SalaryPayEntryFixSchema,
    SalaryProfileCreateSchema,
    SalaryProfileUpdateSchema,
    YtdTaxCheckpointSchema,
)
from app.schemas.validation.savings import (
    SavingsGoalCreateSchema,
    SavingsGoalUpdateSchema,
)
from app.schemas.validation.settings import UserSettingsSchema
from app.schemas.validation.templates import (
    TemplateCreateSchema,
    TemplateUpdateSchema,
)
from app.schemas.validation.transactions import (
    CreditPressSchema,
    InlineTransactionCreateSchema,
    MarkDoneSchema,
    TransactionCreateSchema,
    TransactionDeleteSchema,
    TransactionItemUpdateSchema,
    TransactionUpdateSchema,
)
from app.schemas.validation.transfers import (
    SAME_ACCOUNT_TRANSFER_MESSAGE,
    TransferCreateSchema,
    TransferTemplateCreateSchema,
    TransferTemplateUpdateSchema,
    TransferUpdateSchema,
)

__all__ = [
    "EFFECTIVE_DATE_MAX",
    "EFFECTIVE_DATE_MIN",
    "RECURRENCE_END_BOUND_KEY",
    "RECURRENCE_MAX_PER_MONTH_KEY",
    "RECURRENCE_NEEDS_A_START",
    "RECURRENCE_REPEATS_ON_KEY",
    "RECURRENCE_STARTS_ON_KEY",
    "end_bound_before_start_message",
    "form_payload",
    "AccountCreateSchema",
    "AccountTypeCreateSchema",
    "AccountTypeUpdateSchema",
    "AccountUpdateSchema",
    "AnchorUpdateSchema",
    "OpeningRestatementSchema",
    "AppreciationParamsUpdateSchema",
    "CardAprSchema",
    "CardPaymentSchema",
    "CategoryCreateSchema",
    "CategoryEditSchema",
    "ChangePasswordSchema",
    "CompanionCreateSchema",
    "CompanionEditSchema",
    "CreditCardTermsSchema",
    "CreditPressSchema",
    "DebtStrategyCalculateSchema",
    "PaycheckLineCreateSchema",
    "PaycheckLineUpdateSchema",
    "PayStubFigureSchema",
    "PayStubLineSchema",
    "PayStubOneOffSchema",
    "PayStubPaydaySchema",
    "PayStubSchema",
    "EntryCreateSchema",
    "EntryUpdateSchema",
    "EscrowComponentSchema",
    "EscrowLineMergeSchema",
    "EscrowLineRenameSchema",
    "EscrowVersionSchema",
    "InlineTransactionCreateSchema",
    "InterestParamsCreateSchema",
    "InterestParamsUpdateSchema",
    "InvestmentContributionTransferSchema",
    "InvestmentParamsCreateSchema",
    "InvestmentParamsUpdateSchema",
    "LoanAnchorTrueupSchema",
    "LoanParamsCreateSchema",
    "LoanParamsUpdateSchema",
    "LoanPaymentExtraSchema",
    "LoanPaymentTransferSchema",
    "LoginSchema",
    "MarkDoneSchema",
    "MfaConfirmSchema",
    "MfaDisableSchema",
    "MfaVerifySchema",
    "PayHistorySchema",
    "PayPeriodExtendSchema",
    "PayPeriodGenerateSchema",
    "PayPeriodRegenerateSchema",
    "PayPeriodRemoveEarlierSchema",
    "PayPeriodResetSchema",
    "PayPeriodTruncateSchema",
    "PayScheduleSchema",
    "PayoffCalculatorSchema",
    "PensionProfileCreateSchema",
    "PensionProfileUpdateSchema",
    "RaiseCreateSchema",
    "RaiseUpdateSchema",
    "RateChangeSchema",
    "ReauthSchema",
    "RefinanceSchema",
    "RegisterSchema",
    "RetirementReadinessQuerySchema",
    "RetirementSettingsSchema",
    "SalaryPayEntryFixSchema",
    "SalaryProfileCreateSchema",
    "SalaryProfileUpdateSchema",
    "SavingsGoalCreateSchema",
    "SavingsGoalUpdateSchema",
    "ShownLinesSchema",
    "TemplateCreateSchema",
    "TemplateUpdateSchema",
    "TransactionCreateSchema",
    "TransactionDeleteSchema",
    "TransactionItemUpdateSchema",
    "TransactionUpdateSchema",
    "TransferCreateSchema",
    "TransferTemplateCreateSchema",
    "TransferTemplateUpdateSchema",
    "SAME_ACCOUNT_TRANSFER_MESSAGE",
    "TransferUpdateSchema",
    "UserSettingsSchema",
    "YtdTaxCheckpointSchema",
    # The rhythm's wire, read from OUTSIDE the package (plan step
    # pay_calendar:C17-d-3): the two routes that render a Rhythm BACK into a
    # form take the schema's own inverse here, and ``jinja_globals`` takes
    # each kind's token for the radio arms' ``value=``.  The module that
    # owns them is private, so this is their one public door.
    "cadence_kind_token",
    "rhythm_to_wire",
]
