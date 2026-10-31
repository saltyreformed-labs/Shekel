"""
Shekel Budget App -- Model Registry

All models are imported here so that Alembic's autogenerate can
discover them.  Import order matters: ref tables first, then auth,
then budget (to satisfy foreign key dependencies).
"""

# Reference / lookup tables
from app.models.ref import (
    AccountType,
    AmountSource,
    BusinessDayShift,
    CalcMethod,
    PaycheckLineKind,
    FilingStatus,
    GoalMode,
    IncomeUnit,
    LedgerAccountClass,
    LedgerAccountKind,
    LoanAnchorSource,
    PeriodPlacement,
    PostingKind,
    PostingSource,
    RaiseType,
    RecurrenceUnit,
    StatementSource,
    Status,
    TaxType,
    TransactionType,
    UserRole,
    WithholdingKind,
)

# Authentication
from app.models.user import MfaConfig, User, UserSettings
from app.models.trusted_device import TrustedDevice

# Budget domain
from app.models.pay_period import PayPeriod
from app.models.pay_schedule import PaySchedule
from app.models.pay_era import PayEra
from app.models.account import (
    Account,
    AccountAnchorHistory,
    AccountAnchorHistoryImmutableError,
)
from app.models.account_opening import (
    AccountOpening,
    AccountOpeningImmutableError,
)
from app.models.anchor_release import (
    AnchorRelease,
    AnchorReleaseImmutableError,
)
from app.models.category import Category
from app.models.recurrence_rule import RecurrenceRule
from app.models.recurrence_anchors import (
    RecurrenceWeekdayAnchor,
)
from app.models.scenario import Scenario
from app.models.transaction_template import TransactionTemplate
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.template_amount_version import TemplateAmountVersion
from app.models.loan_payment_settings import LoanPaymentSettings
from app.models.ledger_account import LedgerAccount
from app.models.journal_entry import JournalEntry, Posting
from app.models.savings_goal import SavingsGoal
from app.models.interest_params import InterestParams
from app.models.loan_params import LoanParams
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_anchor_withdrawal import (
    LoanAnchorWithdrawal,
    LoanAnchorWithdrawalImmutableError,
)
from app.models.loan_features import RateHistory
from app.models.escrow_line import EscrowLine, EscrowComponentVersion
from app.models.investment_params import InvestmentParams
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.credit_card_params import CreditCardParams
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
)
from app.models.statement_match import (
    StatementMatch,
    StatementMatchMember,
)
from app.models.statement_line_skip import StatementLineSkip

# Salary domain
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.salary_pay_entry import SalaryPayEntry
from app.models.paycheck_line import PaycheckLine
from app.models.pension_profile import PensionProfile
from app.models.calibration_override import CalibrationOverride
from app.models.pay_stub import (
    PayStub,
    PayStubLineAmount,
    PayStubOneOff,
    PayStubWithholding,
)
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from app.models.tax_config import (
    FicaConfig,
    StateChildDeduction,
    StateTaxConfig,
    TaxBracket,
    TaxBracketSet,
)

# Public API.  Every model class imported above is re-exported here.  Declaring
# __all__ marks these otherwise side-effect-only imports (needed so Alembic
# autogenerate can discover every model) as exported rather than unused, which
# removes the need for a blanket `disable=unused-import` while still flagging any
# genuinely accidental unused import added later.  Keep in sync with the imports.
__all__ = [
    # Reference / lookup tables
    "AccountType",
    "AmountSource",
    "BusinessDayShift",
    "CalcMethod",
    "PaycheckLineKind",
    "FilingStatus",
    "GoalMode",
    "IncomeUnit",
    "LedgerAccountClass",
    "LedgerAccountKind",
    "LoanAnchorSource",
    "PeriodPlacement",
    "PostingKind",
    "PostingSource",
    "RaiseType",
    "RecurrenceUnit",
    "StatementSource",
    "Status",
    "TaxType",
    "TransactionType",
    "UserRole",
    "WithholdingKind",
    # Authentication
    "MfaConfig",
    "TrustedDevice",
    "User",
    "UserSettings",
    # Budget domain
    "PayEra",
    "PayPeriod",
    "PaySchedule",
    "Account",
    "AccountAnchorHistory",
    "AccountAnchorHistoryImmutableError",
    "AccountOpening",
    "AccountOpeningImmutableError",
    "AnchorRelease",
    "AnchorReleaseImmutableError",
    "Category",
    "RecurrenceRule",
    "RecurrenceWeekdayAnchor",
    "Scenario",
    "TransactionTemplate",
    "Transaction",
    "TransactionEntry",
    "TransferTemplate",
    "Transfer",
    "TemplateAmountVersion",
    "LoanPaymentSettings",
    "LedgerAccount",
    "JournalEntry",
    "Posting",
    "SavingsGoal",
    "InterestParams",
    "LoanParams",
    "LoanAnchorEvent",
    "LoanAnchorWithdrawal",
    "LoanAnchorWithdrawalImmutableError",
    "RateHistory",
    "EscrowLine",
    "EscrowComponentVersion",
    "InvestmentParams",
    "AssetAppreciationParams",
    "CreditCardParams",
    "Merchant",
    "MerchantRule",
    "AccountExternalIdentity",
    "BankStatementLine",
    "StatementImport",
    "StatementMatch",
    "StatementMatchMember",
    "StatementLineSkip",
    # Salary domain
    "SalaryProfile",
    "SalaryRaise",
    "SalaryPayEntry",
    "PaycheckLine",
    "PensionProfile",
    "CalibrationOverride",
    "PayStub",
    "PayStubLineAmount",
    "PayStubOneOff",
    "PayStubWithholding",
    "YtdTaxCheckpoint",
    "FicaConfig",
    "StateChildDeduction",
    "StateTaxConfig",
    "TaxBracket",
    "TaxBracketSet",
]
