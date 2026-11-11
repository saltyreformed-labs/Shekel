"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables (all but :class:`TaxTypeEnum`).  The *value* of each member
is the database ``name`` column after all migrations have run.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.

**A PACKAGE since plan step recurrence:R16-d, split by a gate rather than for
tidiness** (ruling **R-IR**): the flat module stood at exactly 1,000 lines,
pylint's ceiling, and that step's new enum would have broken it.  The four
provenance catalogues moved verbatim to :mod:`._provenance`, a subject of
their own, and are re-exported below, so every ``from app.enums import ...``
is unchanged and this package stays the one place an enum is imported from.
"""

import enum

# The provenance catalogues live in a module of their own (plan step
# recurrence:R16-d, ruling R-IR) and are re-exported here, so this package
# stays the one place every enum is imported from.
from app.enums._provenance import (
    MovementFigureSourceEnum,
    SettledDayBasisEnum,
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)


class StatusEnum(enum.Enum):
    """Transaction status values.

    Values match ``ref.statuses.name`` after the Commit #1 migration
    renames the display names.

    **The SETTLED BAND HAS TWO MEMBERS -- Paid and Received -- and a THIRD is
    not how a row is frozen** (plan step **X-am**, ruling **balance:R-HA**,
    closing finding **N-177**).  A sixth member ``SETTLED`` sat here as the
    ARCHIVE: a terminal state the state machine gave no outgoing edge but
    itself, reachable only from the full-edit popover's Status ``<select>``.
    Nothing has ever carried it: 1,591 ``system.audit_log`` rows for the two
    status-bearing tables since 2026-05-07, including 229 DELETEs and 208
    status changes, name it NOWHERE -- which is what rules out a row that was
    archived and hard-deleted between snapshots -- and every snapshot that
    exists reads zero besides.  Its
    only content beyond ``DONE`` was *and you may never revert*, since
    ``is_immutable`` already locks a Paid row's fields.  It refused every act
    that CORRECTS a row (revert, re-price, add or remove a purchase, match a
    bank line) and permitted the one that DESTROYS it, because
    ``transaction_service.deletion_refusal`` never named it.

    **How firmly a settled row is known is PROVENANCE, and it already ships**:
    the covering movement's ``figure_source_id`` (who wrote the FIGURE),
    ``settled_day_basis_id`` (how the DAY is known) and ``reconciled_by_id``
    (which statement was seen to show it) -- three columns answering three
    different questions, each correctable without destroying the row.  A
    status member is a cruder fourth answer to the same question and the only
    one with no way back.

    ``pay_period_locks`` answers the neighbouring question -- whether a SPAN may
    still be rewritten -- but it is a read-only CLASSIFIER rather than a second
    place to store the fact, and it is not the row-level answer: no row-edit
    door consults it, and its ``SETTLED_TXN`` reason is itself DERIVED from
    ``settled_status_ids()``.  So the honest statement is narrower than *neither
    is a status*: the settled BAND is load-bearing everywhere, and what X-am
    refuses is a member INSIDE it whose distinct meaning is a lock.
    """

    PROJECTED = "Projected"
    DONE = "Paid"          # Renamed from "done" -- expense has been paid
    RECEIVED = "Received"  # Income has been deposited
    CREDIT = "Credit"      # Paid via credit card, not checking
    CANCELLED = "Cancelled"


class TxnTypeEnum(enum.Enum):
    """Transaction type values.

    Values match ``ref.transaction_types.name`` after the Commit #2
    migration capitalizes the display names.
    """

    INCOME = "Income"
    EXPENSE = "Expense"


class AcctCategoryEnum(enum.Enum):
    """Account type category values.

    Groups account types into high-level buckets for dashboard layout
    and chart axis assignment.  Values match
    ``ref.account_type_categories.name``.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    RETIREMENT = "Retirement"
    INVESTMENT = "Investment"


class AcctTypeEnum(enum.Enum):
    """Account type values.

    Values match ``ref.account_types.name`` after the Commit #2
    migration capitalizes the display names.  Each member maps 1:1
    to a row in the account_types table.
    """

    CHECKING = "Checking"
    SAVINGS = "Savings"
    HYSA = "HYSA"
    MONEY_MARKET = "Money Market"
    CD = "CD"
    HSA = "HSA"
    CREDIT_CARD = "Credit Card"
    MORTGAGE = "Mortgage"
    AUTO_LOAN = "Auto Loan"
    STUDENT_LOAN = "Student Loan"
    PERSONAL_LOAN = "Personal Loan"
    HELOC = "HELOC"
    K401 = "401(k)"
    ROTH_401K = "Roth 401(k)"
    TRADITIONAL_IRA = "Traditional IRA"
    ROTH_IRA = "Roth IRA"
    BROKERAGE = "Brokerage"
    PLAN_529 = "529 Plan"
    PROPERTY = "Property"


class PaycheckLineKindEnum(enum.Enum):
    """The KIND of a payroll line: its position in the paycheck's waterfall.

    Values match ``ref.paycheck_line_kinds.name`` in the database, and the
    members are in WATERFALL order (ruling **R-SAL38**, plan steps
    **salary:R18-a** and **R18-b**): a paycheck is base pay plus a list of
    lines, and a line's kind is where it sits between base pay and net pay::

        base pay
        + TAXABLE_EARNING       -> gross (withholding annualises it)
        - PRE_FICA_DEDUCTION    -> Social Security and Medicare wages
        - PRE_TAX_DEDUCTION     -> taxable income (the income-tax base)
        - withholding
        - POST_TAX_DEDUCTION
        + AFTER_TAX_EARNING     -> net pay (the deposit)

    Sign and stage DERIVE from the kind, so a position no payroll has (a
    post-tax line that skips Social Security) is not storable.  The two
    pre-tax kinds are plan step **salary:S11-e** (ruling **plan#2639**,
    **SAL-566**): "Pre-tax, all payroll taxes" (a Section 125 premium, a
    flexible spending account) and "Pre-tax, income tax only" (a retirement
    deferral, the one kind before it).  ``DeductionTimingEnum`` until R18-a.
    """

    TAXABLE_EARNING = "taxable_earning"
    PRE_FICA_DEDUCTION = "pre_fica_deduction"
    PRE_TAX_DEDUCTION = "pre_tax_deduction"
    POST_TAX_DEDUCTION = "post_tax_deduction"
    AFTER_TAX_EARNING = "after_tax_earning"


class WithholdingKindEnum(enum.Enum):
    """WHICH TAX a pay stub's withholding figure is (plan step **salary:S11-a**).

    Values match ``ref.withholding_kinds.name``, the catalogue behind
    ``salary.pay_stub_withholdings.withholding_kind_id`` (ruling **R-SAL42**,
    round 4, fork 11: "a table per kind of line").  A transcribed stub
    carries one amount per TAX it prints, and this says which tax each
    amount is::

        FEDERAL_INCOME   -- federal income tax withheld
        STATE_INCOME     -- state income tax withheld
        SOCIAL_SECURITY  -- the employee's Social Security (OASDI) share
        MEDICARE         -- the employee's Medicare share

    **A reference list and not four columns, and the difference is the
    document's.**  A real stub can lack one of these (a state with no income
    tax) or carry one more (a city income tax, a state disability line), so
    "exactly these four" is the app's paycheck model and not a fact of the
    stub.  A new tax is a new member and a new row here, never a table
    change.  The four members are the four withholding lines the paycheck
    engine computes today (``paycheck_calculator._breakdown.TaxLines``).
    """

    FEDERAL_INCOME = "federal_income"
    STATE_INCOME = "state_income"
    SOCIAL_SECURITY = "social_security"
    MEDICARE = "medicare"


class CalcMethodEnum(enum.Enum):
    """Calculation method values.

    Values match ``ref.calc_methods.name`` in the database.
    """

    FLAT = "flat"
    PERCENTAGE = "percentage"


class TaxTypeEnum(enum.Enum):
    """How a state taxes income, as the tax law states it; no ``ref`` table.

    Compared as members; plan step salary:X-at-2 dropped ``ref.tax_types`` (plan#3211).
    """

    FLAT = "flat"
    NONE = "none"
    BRACKET = "bracket"


class FilingStatusEnum(enum.Enum):
    """Federal filing status values.

    Values match ``ref.filing_statuses.name`` in the database.  The tax law
    (:mod:`app.tax_law`) is keyed on these members, so a profile's
    ``filing_status_id`` reaches its rules through
    ``ref_cache.filing_status_member`` and never through a name.
    """

    SINGLE = "single"
    MARRIED_JOINTLY = "married_jointly"
    MARRIED_SEPARATELY = "married_separately"
    HEAD_OF_HOUSEHOLD = "head_of_household"


class RaiseTypeEnum(enum.Enum):
    """Salary-raise type values.

    **Display-only TO THE PROJECTIONS since plan step salary:S3-c**, which
    is narrower than "display-only" and an adversarial review of that step is
    why the sentence says so.  No projection branches on it -- every engine
    applies every raise uniformly on the pay list's walk
    (``salary_raises.applications_between``, read by the basis since plan
    step salary:X-av-3a, through ``PayrollBasis.pay_rate_on`` since task
    plan#3100) and stops each one at its own stored ``terminal_year``.
    It is still LOGIC to the storage tier: ``raise_type_id`` is a member of
    ``uq_salary_raises_profile_type_date``, so the type decides whether
    a second raise on the same profile and start date can be written at all,
    and ``salary.add_raise`` has a whole duplicate-handling branch keyed on
    that constraint.

    It selected how the retirement salary projection treated a raise until
    then (Gate A ruling 3 / fork F4): a ``cola`` recurring raise
    extrapolated to the retirement date while ``merit`` and ``custom``
    raises stopped at ``auth.user_settings.merit_raise_horizon_years``.
    Ruling **R-SAL11** (developer, 2026-09-05) deleted that setting -- the
    horizon a recurring raise decays over is a fact on the RAISE -- and with
    it the last reader of this discrimination.  Values match
    ``ref.raise_types.name``; resolved to IDs via
    ``ref_cache.raise_type_id`` and compared by ID, never by name.
    """

    MERIT = "merit"
    COLA = "cola"
    CUSTOM = "custom"


class RecurrenceUnitEnum(enum.Enum):
    """Recurrence cadence-unit values (recurrence redesign, step R2).

    The first of the two axes that replaced the closed pattern set's
    eight-name enum, ``RecurrencePatternEnum``, deleted with
    ``ref.recurrence_patterns`` at plan step **R9**: a recurrence is
    ``every <interval_n> <unit>``.
    Four of the old names -- Monthly, Quarterly, Semi-Annual, Annual -- were
    the same idea with a different integer baked into the NAME (every 1, 3, 6
    or 12 months), which is why "every other month" and "every two years" had
    nowhere to live.  With the interval in a column, they do.

        period -- the user's own pay-period cadence (the paycheck-space
                  family: today's Every Period / Every N Periods).
        week   -- calendar weeks; weekly and biweekly-by-date bills, which
                  the old set could not express at all.
        month  -- calendar months, month-end clamped (``interval_n`` 1 =
                  Monthly, 3 = Quarterly, 6 = Semi-Annual).
        year   -- calendar years (``interval_n`` 1 = Annual).

    Values match ``ref.recurrence_units.name``; application code resolves
    them via ``ref_cache.recurrence_unit_id`` and compares the integer ID,
    never the string ``name`` -- the project-wide IDs-for-logic invariant.
    """

    PERIOD = "period"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class PeriodPlacementEnum(enum.Enum):
    """How an occurrence date maps onto a pay period (redesign step R2).

    An occurrence is a calendar DATE; a Shekel row lives in a pay PERIOD.
    This is the rule that carries one to the other, and it is a real user
    choice rather than a derived detail: it is the axis today's Monthly and
    Monthly First patterns differ on.  (They differ on the anchor day too --
    Monthly anchors on its ``day_of_month``, Monthly First on the 1st -- so
    placement alone does not carry one to the other: Monthly on the 15th with
    the placement swapped is "the first paycheck starting on or after the
    15th", i.e. the SECOND of the month, not the first.)

        containing_date            -- the period whose date range contains
                                      the occurrence (today's Monthly,
                                      Quarterly, Semi-Annual, Annual).
        period_starting_on_or_after -- the first period whose start_date is
                                      on or after the occurrence (today's
                                      Monthly First: the first paycheck of
                                      each month).

    Well-defined for every unit rather than inert for some: under the
    ``period`` unit an occurrence date IS a period start, so both values
    resolve to the same period.

    Values match ``ref.period_placements.name``; resolved via
    ``ref_cache.period_placement_id`` and compared by integer ID.
    """

    CONTAINING_DATE = "containing_date"
    PERIOD_STARTING_ON_OR_AFTER = "period_starting_on_or_after"


class BusinessDayShiftEnum(enum.Enum):
    """Weekend/holiday adjustment for an occurrence date (redesign step R2).

    Seeded by step R2 with every rule set to ``none``; step R8 is the first
    step that lets a user choose another value, so the column turns
    behaviour ON rather than being added later.

        none  -- take the occurrence date as computed.
        prior -- move a non-business-day occurrence BACKWARD to the previous
                 business day (the usual choice for a bill: pay early rather
                 than late).
        next  -- move it FORWARD to the following business day.

    The shift applies to the CASH date only.  A bill contractually due Aug 1
    and paid on Friday July 30 because Aug 1 is a Sunday still satisfies the
    Aug 1 installment, so the due-date side of a rule is never shifted.

    Values match ``ref.business_day_shifts.name``; resolved via
    ``ref_cache.business_day_shift_id`` and compared by integer ID.
    """

    NONE = "none"
    PRIOR = "prior"
    NEXT = "next"


class GoalModeEnum(enum.Enum):
    """Savings goal amount mode values.

    Values match ``ref.goal_modes.name`` in the database.
    """

    FIXED = "Fixed"
    INCOME_RELATIVE = "Income-Relative"


class IncomeUnitEnum(enum.Enum):
    """Income multiplier unit values.

    Values match ``ref.income_units.name`` in the database.
    """

    PAYCHECKS = "Paychecks"
    MONTHS = "Months"


class RoleEnum(enum.Enum):
    """User role values.

    Values match ``ref.user_roles.name`` in the database.
    """

    OWNER = "owner"
    COMPANION = "companion"


class LoanAnchorSourceEnum(enum.Enum):
    """Loan anchor event source values (CRIT-02 / E-18).

    Distinguishes the origination event that every loan carries from
    user-initiated balance true-ups appended through the dashboard
    edit flow, and from the tracking-start assertion a mid-life-imported
    loan records so its ledger has a recent verified balance to reset
    at.  Values match ``ref.loan_anchor_sources.name``.
    """

    ORIGINATION = "origination"
    USER_TRUEUP = "user_trueup"
    # A mid-life loan's tracked balance: the operator started tracking an
    # already-amortizing loan and recorded its real balance as of a date
    # -- at setup, the balance the setup door asks for (plan step
    # recurrence:R20), or after the fact through the dashboard's
    # tracking-start form.  It is an ordinary balance ASSERTION
    # (is_opening=False) that RESETS the running balance at its own
    # date, exactly like a user true-up, whatever payments are recorded
    # around it.
    # It does NOT open the ledger: since plan step C1 the loan's ONE
    # opening is ALWAYS the synthesized ORIGINATION, because opening at
    # a mid-life tracking start read the loan out of existence for its
    # whole pre-tracking window (finding B-11, a false $0.00 that made
    # the year-end summary report negative principal paid on real data).
    # See ``app.services.loan_loaders.load_loan_anchor_facts``.
    TRACKING_START = "tracking_start"


class AccountOpeningSourceEnum(enum.Enum):
    """Where an ``account_openings`` row's figure CAME FROM (plan step X-f3c-2a).

    An account's opening equity is what it held before its records begin, and
    the two members answer the one question a reader of that figure has: did a
    HUMAN state it, or did the app compute it?  The distinction is financial
    rather than clerical.  A ``MIGRATION_DERIVED`` figure is the pre-X-f3c-2a
    inferred rule frozen -- the earliest assertion minus the movements it
    already contained -- and finding **N-275** measures one of them wrong by
    ``$436.05`` (account 1's opening asserts ``$2,746.58`` for 2026-03-27 where
    the bank's own closing that day is ``$3,182.63``).  A ``USER_DECLARED``
    figure is one somebody actually asserted.  Reading which is which off the
    row is what lets a later surface say "this opening has never been
    confirmed" instead of presenting a guess and a fact identically.

    Values match ``ref.account_opening_sources.name``.  The loan twin is
    :class:`LoanAnchorSourceEnum`, and the shape is deliberately the same: a
    typed provenance column on an append-only balance record, resolved through
    ``ref_cache`` and compared by ID.
    """

    # The figure a human stated: an account's declared opening balance at
    # creation -- which IS its opening equity, because a just-created account
    # has no records for the assertion to already contain -- or an owner
    # correcting that figure later (plan step X-f3c-2b-2a).  Both reach
    # ``opening_service.stage_account_opening``, the table's ONE writer.
    USER_DECLARED = "user_declared"
    # The figure the X-f3c-2a migration computed for an account that already
    # existed, from the posted ledger's own ``account_opening`` entry.  It
    # reproduces what every balance already rested on, so the migration moves
    # ``$0.00`` -- but it is a DERIVATION preserved, not an observation, which
    # is exactly what N-275 is about.
    MIGRATION_DERIVED = "migration_derived"


class EmployerContributionTypeEnum(enum.Enum):
    """Employer retirement-contribution type values (#38).

    Selects the employer-contribution formula the growth engine
    applies to an investment/retirement account: no employer
    contribution, a flat percentage of gross pay, or a match of the
    employee's contribution up to a cap.  Values match
    ``ref.employer_contribution_types.name``.
    """

    NONE = "none"
    FLAT_PERCENTAGE = "flat_percentage"
    MATCH = "match"


class CompoundingFrequencyEnum(enum.Enum):
    """Interest compounding frequency values (#38).

    Selects the per-period compounding formula the interest
    projection engine applies to an interest-bearing account.  Values
    match ``ref.compounding_frequencies.name``.
    """

    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class InterestAccrualConventionEnum(enum.Enum):
    """HOW interest accrues on a balance over a window (plan step **recurrence:R16-d**).

    A loan's convention is a fact of its NOTE, so it is a value on the loan
    (``budget.loan_params.interest_accrual_convention_id``, finding **D52**)
    rather than a formula the app assumes.  The one producer,
    :func:`app.utils.money.accrued_interest`, prices a window under a member::

        monthly_1_12 -- one twelfth of the annual rate for each calendar month
                        the window spans, whatever its days: the US
                        fixed-rate mortgage's convention, and every loan's
                        until its note says otherwise.

    **One member, and that is a ruling** (developer, plan#3354): a member joins
    with the first thing that uses it, so no loan can name a convention the app
    cannot honour.  ``daily_actual_365`` arrives with the card's finance charge
    (``credit_card:CC-3i``) or a loan's daily accrual (plan#3355), and the
    savings account's compounding rules join at plan#3356 (plan#3348).  Values
    match ``ref.interest_accrual_conventions.name``; resolved through
    ``ref_cache`` and compared as members or ids, never by name.
    """

    MONTHLY_1_12 = "monthly_1_12"


class LedgerAccountClassEnum(enum.Enum):
    """Ledger account class values for the double-entry posting ledger.

    The five fundamental accounting classes (Build-Order Step 2) plus the
    reporting class ruling **R-FO** added (plan step X-f3d).  Values match
    ``ref.ledger_account_classes.name``.  Asset and Expense are debit-normal;
    Liability, Income, Equity and Unrealized are credit-normal -- the
    natural-balance side is stored as the ``is_debit_normal`` boolean on
    each row and read via ``ref_cache.ledger_class_is_debit_normal``,
    never inferred from these member names.

    ``UNREALIZED`` is OTHER COMPREHENSIVE INCOME: a price movement the owner
    has not sold into cash.  It is a class of its own rather than more Income
    because ``net_income = income - expense``, so a ``$40,000`` house
    revaluation booked as Income would read as ``$40,000`` earned.  The income
    statement reports it BELOW the net-income line and the balance sheet folds
    it into Equity as one derived accumulated line, exactly as Income and
    Expense are folded into Retained Earnings -- so the trial balance closes
    with it, not despite it.  Its accounts are per-account
    (``LedgerAccountKindEnum.UNREALIZED_CHANGE``) and are booked by an
    ``INVESTMENT`` / ``APPRECIATING`` account's balance-assertion true-up.
    """

    ASSET = "Asset"
    LIABILITY = "Liability"
    INCOME = "Income"
    EXPENSE = "Expense"
    EQUITY = "Equity"
    # ``String(12)`` on the ref column, which is why the member value is the
    # bare adjective rather than "Change in Value": the class NAME is an
    # identifier (matched to the enum by ``ref_cache.init``), and the reader's
    # section headings carry the prose.
    UNREALIZED = "Unrealized"


class PostingKindEnum(enum.Enum):
    """Posting-leg kind values for ``budget.account_postings``.

    ``transfer`` is a transfer's two balanced legs (Build-Order Step 2);
    ``income`` / ``expense`` are an ordinary settled transaction's cash and
    category legs (Build-Order Step 3); ``principal`` / ``interest`` /
    ``escrow`` / ``refund`` are the four legs of a confirmed loan payment's
    real-split correction (Build-Order Step 4) -- the loan principal
    adjustment, the accrued interest expense, the configured escrow expense,
    and the payoff-overpayment refund receivable.  ``opening`` / ``trueup``
    are the two-leg kinds the loan read switch adds (Build-Order Step 4,
    second half): ``opening`` books a loan's origination balance once as a
    balanced opening-equity entry so the ledger is authoritative for the
    confirmed balance, and ``trueup`` books an append-only dated correction
    that drives the ledger balance to a user-verified value without rewriting
    the prior payment postings.  Later Build-Order steps add further kinds
    via data migrations.  Values match ``ref.posting_kinds.name``.
    """

    TRANSFER = "transfer"
    INCOME = "income"
    EXPENSE = "expense"
    PRINCIPAL = "principal"
    INTEREST = "interest"
    ESCROW = "escrow"
    REFUND = "refund"
    OPENING = "opening"
    TRUEUP = "trueup"


class PostingSourceEnum(enum.Enum):
    """Journal-entry source-event values for ``budget.journal_entries``.

    ``loan_payment`` is the real-split correction appended to a confirmed
    loan-payment transfer (Build-Order Step 4).  ``loan_opening`` /
    ``loan_trueup`` are the source events the loan read switch adds
    (Build-Order Step 4, second half): ``loan_opening`` tags the once-per-loan
    opening-equity entry booked at origination, and ``loan_trueup`` tags an
    append-only loan-balance correction entry.  ``account_opening`` /
    ``account_trueup`` are their NON-loan siblings (Build-Order Step 5):
    ``account_opening`` tags the once-per-account entry booking a non-loan
    account's earliest anchor assertion, and ``account_trueup`` tags the
    balanced correction appended for each later anchor true-up -- together
    they make every non-loan linked ledger sum to an ABSOLUTE balance, closing
    the app-wide trial balance.  None of the five corrections links a row (the
    source FK is nullable); the source kind is what disambiguates them.
    ``purchase`` is a single PURCHASE recorded against an envelope whose bank
    posting day the owner has recorded (plan step X-f3b, ruling **R-FM**): it
    links ``transaction_entry_id``, and it books its own cash leg on its own
    day so its envelope's close books only the remainder.
    ``transfer_movement`` is ONE side of a settled transfer -- a side's
    covering movement, booked on its own bank day against the owner's
    Transfers-in-transit clearing account (plan step ``balance:X-bi-6-3``,
    rulings **R-BAL45** and **R-BAL101**): it links ``transaction_entry_id``
    exactly as ``purchase`` does.  Two older values are gone: ``transfer``,
    the one-entry-per-transfer shape (Build-Order Step 2), and
    ``transaction``, a settled row's own cash leg (Step 3).  After plan steps
    ``balance:X-bi-6-3`` and ``balance:X-bi-4a`` each was written only to
    reverse what it had posted, the deploy resync bringing every entry of
    both to zero, and plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted the reversed
    pairs, both kinds and both links (ruling **plan#1867**).  Later steps add
    ``paycheck`` and ``credit_payback`` via data migrations.  Values match
    ``ref.posting_sources.name``.
    """

    PURCHASE = "purchase"
    TRANSFER_MOVEMENT = "transfer_movement"
    LOAN_PAYMENT = "loan_payment"
    LOAN_OPENING = "loan_opening"
    LOAN_TRUEUP = "loan_trueup"
    ACCOUNT_OPENING = "account_opening"
    ACCOUNT_TRUEUP = "account_trueup"


class LedgerAccountKindEnum(enum.Enum):
    """Row-kind discriminator for ``budget.ledger_accounts`` (Build-Order Step 4).

    The explicit, positive discriminator that replaces inferring a ledger
    account's kind from the NULL-pattern of its ``account_id`` /
    ``category_id`` / ``is_owner_bucket`` columns (see
    :class:`app.models.ledger_account.LedgerAccount`).  Every row carries a
    ``kind_id`` FK to one of these values; readers branch on the integer ID,
    never on which FKs happen to be NULL.

    The first four enumerate the kinds Steps 2-3 already create:

        linked    -- one per real ``budget.accounts`` row (Asset/Liability).
        category  -- one per budget category per Income/Expense class.
        fallback  -- the per-(owner, class) Uncategorized bucket, an
                     OWNER-BUCKET kind (``is_owner_bucket``; see ``transit``).
        orphan    -- a former category row whose category was deleted.

    The next three are the per-loan ledger accounts Step 4's loan-payment
    correction books into:

        loan_interest -- the loan's accrued-interest Expense account.
        loan_escrow   -- the loan's configured-escrow Expense account.
        loan_refund   -- the loan's payoff-overpayment refund Asset account.

    The loan read switch (Build-Order Step 4, second half) adds one more
    per-loan account:

        equity_opening -- the loan's opening-balance Equity account, the
                          credit counter-leg of the once-per-loan
                          opening-equity entry that books the origination
                          balance so the ledger is authoritative for the
                          loan's confirmed balance.

    Build-Order Step 5 adds the non-loan sibling:

        anchor_equity -- a non-loan account's opening/true-up Equity account
                         (``account_id`` set, like ``linked``), the counter-leg
                         of its ``account_opening`` / ``account_trueup``
                         corrections.  One per real account, coexisting with
                         the linked row under the ``(account_id, kind_id)``
                         partial unique that Step 5's chart migration re-keys
                         ``uq_ledger_accounts_account`` into.

    Ruling **R-FO** (plan step X-f3d) adds the two kinds that say what a
    TRUE-UP's difference WAS, dispatched over the account's projection kind:

        interest_income  -- an ``INTEREST`` account's per-account Interest
                            Income account (Income class).
        unrealized_change  -- an ``INVESTMENT`` or ``APPRECIATING`` account's
                            per-account Change in Value account
                            (``LedgerAccountClassEnum.UNREALIZED``).

    Both carry ``account_id`` and share the ``anchor_equity`` shape and its
    ``(account_id, kind_id)`` unique exactly, so no new index exists for them.
    An account's OPENING keeps booking to ``anchor_equity`` whatever its kind:
    an opening is capital brought onto the books, not something earned (a
    Property's ``$350,000.00`` opening is not a gain).

    Plan step ``balance:X-bi-6-3`` (rulings **R-BAL45**, **R-BAL99**) adds
    the second OWNER-BUCKET kind beside ``fallback``:

        transit -- the owner's Transfers-in-transit clearing account (Asset
                   class): the counter leg of every settled transfer's TWO
                   per-movement entries, so each side posts on its own bank
                   day and the account nets to zero once both have cleared.
                   No ``account_id`` / ``category_id`` / ``loan_account_id``;
                   held to one per owner by the owner-bucket key
                   ``(user_id, class_id, kind_id) WHERE is_owner_bucket``,
                   the same key that holds the ``fallback`` rows.

    Application code resolves these via ``ref_cache.ledger_account_kind_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant.
    """

    LINKED = "linked"
    CATEGORY = "category"
    FALLBACK = "fallback"
    ORPHAN = "orphan"
    LOAN_INTEREST = "loan_interest"
    LOAN_ESCROW = "loan_escrow"
    LOAN_REFUND = "loan_refund"
    EQUITY_OPENING = "equity_opening"
    ANCHOR_EQUITY = "anchor_equity"
    INTEREST_INCOME = "interest_income"
    UNREALIZED_CHANGE = "unrealized_change"
    TRANSIT = "transit"


class AmountSourceEnum(enum.Enum):
    """WHICH RELATION states a row's amount, when the row does not state it itself.

    Ruling **R-FI**'s discriminator (plan step **X-au-c1**): a row's amount is
    either its OWN -- a human authored the figure, or the money moved -- or it
    is DERIVED, and a derived amount is not stored at all.  A row that owns its
    amount carries ``amount_source_id IS NULL`` and a figure; a derived row
    names one of these and carries no figure.  The pairing is the CHECK
    ``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``, so
    a stale derived figure is unrepresentable rather than merely unlikely.

        template -- the recurring DEFINITION that generated this row states
                    its price: ``Transaction.template_id`` ->
                    ``budget.transaction_templates``, or
                    ``Transfer.transfer_template_id`` ->
                    ``budget.transfer_templates``.

    ``parent_transfer`` named a transfer SHADOW's parent, the shadow worth
    what its transfer was (amount rule 5); plan step ``balance:X-bi-6-4d-2``
    deleted the shadows, and ``balance:X-bi-6-5`` the member, its reference
    row and the rule (migration ``c956c1d83108``).

    **These name the RELATION that prices the row, not the RULE that computes
    the figure, and the difference is a developer ruling (2026-08-12) amending
    R-FI's own enumeration.**  R-FI listed five values naming the five
    producers -- own / salary / template / loan payment / transfer -- and two of
    those are refinements a DEFINITION carries, not facts about a row: a
    template is salary-linked when an active
    :class:`~app.models.salary_profile.SalaryProfile` names it
    (``template_amount_service.is_salary_linked_template``), and a transfer
    template is a loan payment when it holds a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row
    (``recurring_transfer_query.loan_payment_config``).  Storing the refinement on
    every generated row copies a definition-level fact onto each of its
    instances, and two LIVE routes then falsify the copy:

    * ``routes/loan/payment_transfer.py`` ``track_payment`` -- the loan
      dashboard's one-click "auto-track the contract" sets
      ``derive_from_loan = True``.  A legacy manual payment has no settings row,
      so its shadows would have been stamped with the plain-transfer rule; after
      the flip the definition's price series is dormant
      (``template_amount_service.owns_its_amount`` is False) and every stamped
      shadow becomes unpriceable.
    * ``routes/salary/profiles.py`` ``delete_profile`` -- archiving a profile is
      the moment a template stops being salary-linked and starts owning its own
      amount, which X-au-a already handles by stating a price through the write
      door in the same unit of work (58 production rows).  Rows stamped with the
      salary rule would keep naming a producer that no longer answers.

    Both are repairable only by a writer that rewrites every affected row -- a
    second maintainer of a derived value, which is the shape this arc exists to
    delete.  Naming the relation instead leaves the refinement where it is a
    fact: the row says *my definition prices me*, and the definition says how.
    ``credit_card:CC4b``'s card payment needs no new value here -- it prices
    through its transfer template's satellite, the same shape as a loan payment
    -- and neither does ``CC5a``'s rewards accrual.  **``CC4c``'s projected
    finance charge DOES**, and a first draft of this paragraph claimed the card
    arc needed none: that row carries no pricing link at all and its producer is
    reached from the row's ACCOUNT, which is finding **N-264**.

    Application code resolves these via ``ref_cache.amount_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  The OWN state is deliberately NOT a member: it is the ABSENCE of
    a source, which is what lets the ownership CHECK be written as
    ``(amount_source_id IS NULL) = (estimated_amount IS NOT NULL)`` -- a
    constraint over two NULL-nesses, with no ref-table id literal frozen into
    the schema.
    """

    TEMPLATE = "template"
