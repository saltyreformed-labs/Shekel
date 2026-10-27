"""
Shekel Budget App -- Reference Table Enums

Python Enums whose members correspond 1:1 with rows in the ref schema
lookup tables.  The *value* of each member is the database ``name``
column after all migrations have run.

These enums are the single source of truth for valid reference values.
The ref_cache module maps each member to its database integer ID at
startup, so application code never needs to query by name at runtime.
"""

import enum


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
        + TAXABLE_EARNING       -> gross (the FICA base; withholding annualises it)
        - PRE_TAX_DEDUCTION     -> taxable income (the income-tax base)
        - withholding
        - POST_TAX_DEDUCTION
        + AFTER_TAX_EARNING     -> net pay (the deposit)

    Sign and stage DERIVE from the kind, so the row carries one column for
    the fact.  A pre-tax deduction leaves the income-tax base and not the
    FICA base (a pay stub's Social Security figure carries the employer's
    Section 125 base), while a taxable earning joins BOTH -- which is why
    the two sides are four kinds and not two axes.  Until R18-a this was
    ``DeductionTimingEnum`` over ``ref.deduction_timings`` (``pre_tax`` /
    ``post_tax``), a name that could hold nothing but a deduction.
    """

    TAXABLE_EARNING = "taxable_earning"
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
    """Tax type values.

    Values match ``ref.tax_types.name`` in the database.
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
    (``salary_raises.applications_between``, read by
    ``PayrollBasis.base_pay_on`` since plan step salary:X-av-3a) and stops
    each one at its own stored ``salary.salary_raises.terminal_year``.
    It is still LOGIC to the storage tier: ``raise_type_id`` is a member of
    ``uq_salary_raises_profile_type_year_month``, so the type decides whether
    a second raise on the same (profile, year, month) can be written at all,
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
    ``transaction``, a settled row's own cash leg (Step 3).  Neither was
    written after plan steps ``balance:X-bi-6-3`` and ``balance:X-bi-4a``,
    the deploy resync reversed every entry of both, and plan step
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


class StatementSourceEnum(enum.Enum):
    """WHERE a recorded statement line came from -- ruling **R-FP**'s adapter.

    A statement importer is a SOURCE ADAPTER over one normalized line shape
    (plan step ``bank_import:X-f6a``), so matching, review and fact-writing stay
    source-independent: a new way for a statement to arrive is a new member here
    and a new parser, never a second path through the importer.

        secu_checking_csv -- State Employees' Credit Union's own transaction
                             export, as CSV.

    **A member names a FORMAT at an INSTITUTION, not an institution**, because
    one bank publishes one statement several ways and the ways do not carry the
    same facts.  Measured on the developer's own exports 2026-08-16: SECU's OFX
    -- and its QFX and QBO twins, which are the same statement content plus two
    Intuit routing tags -- carries a ``FITID`` and truncates the description to
    the OFX ``NAME`` limit, 326 of 361 lines landing at exactly 32 characters;
    the CSV carries the merchant, the bank's own category and a per-line running
    balance, and carries no ``FITID`` at all.  The CSV description STARTS WITH
    the OFX name on 306 of 306 shared lines, so the two really are one statement
    with one of them cut short.

    **Losing ``FITID`` costs nothing, and that is measured rather than
    assumed.**  A line's identity is
    ``(account, posted_on, amount, sequence within that group)``
    (:func:`app.services.statement_import.line_identity`), which every source
    can compute including one carrying no id of its own.  Compared across two
    SECU exports twelve days apart, that key reproduced the ``FITID`` key
    exactly: 0 keys present in only one export, 0 lines whose ``FITID``
    disagreed, over 342 shared lines.  So an external id is stored as a
    CORROBORATING fact (``bank_statement_lines.external_id``) rather than as the
    thing identity rests on, and the importer has ONE identity rule instead of
    one per format -- which is what lets ``X-f6b``'s automated source join
    without re-opening the question.

    Application code resolves these via ``ref_cache.statement_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    SECU_CHECKING_CSV = "secu_checking_csv"


class SettledDayBasisEnum(enum.Enum):
    """HOW a settled row's settle DAY is known (plan step **X-az**).

    :class:`MovementFigureSourceEnum`'s twin for the DAY: that one says who
    wrote the FIGURE a settled row's covering movement carries; this one says
    how the day itself is known.  (Its twin was ``SettlementBasisEnum`` on
    the row's own ``settled_basis_id`` until plan step ``balance:X-bi-4b-2``
    deleted that column, migration ``45f10b870c8b``.)  Three writers put three
    different kinds of fact into ``settled_on`` and nothing said which
    (finding **N-332**):

        observed  -- a bank statement showed the money posting on this day.  It
                     is a POINT: the bank named the day, and an observation
                     beats a belief.
        asserted  -- the owner asserted a BALANCE for this day and this money
                     was inside it, so the money moved on or before it.  It is
                     an UPPER BOUND and not a point; the true posting day may
                     be days earlier.
        entered   -- the owner's own word with no bank document behind it:
                     the owner typed the day, or a settle door stamped the day
                     the act happened on a plain row.  A POINT, on the owner's
                     word.
        borrowed  -- NO evidence of its own: one side of a TRANSFER whose day
                     is its other side's, or the day Paid was pressed when
                     neither side has one (plan step ``balance:X-bi-6-4c-3``,
                     rulings **R-BAL142** and **R-BAL143**).  A guess, which
                     follows its lender when the lender's day is corrected.

    **The partition is over EVIDENCE, which is what makes it exhaustive.**  A
    settle day is backed by a bank line, by a balance assertion, by the owner's
    own word, or by nothing of its own; there is no fifth kind, so every writer
    lands in exactly one member.  The first three ARE evidence and ``borrowed``
    is the one member that is not -- the question
    ``settle_day.is_evidence`` answers, and the reason a
    transfer's two sides can hold two different days.  What separates
    ``entered`` from ``observed`` is not confidence but provenance -- both are
    points, and a reader that wants to rank them can, because the column now
    says which is which.  Only a transfer side is ever ``borrowed``: a plain
    row has no other side to borrow from.

    **Its whole reason for existing is that the difference decides a WINDOW.**
    ``statement_match._subjects.CandidateRow.expected_window`` bounds a purchase
    by ``(purchased_on, settled_on)`` when the day is a bound and pins it to a
    point when it is not, and it used to tell the two apart by testing whether
    ``reconciled_by_id`` was populated.  That inference was exact over the three
    writers of the day it happened to meet and blind to the third of them: a day
    the owner typed read as a day the bank had shown.  Reading the bound as an
    observation had already cost **50 duplicate purchases worth `$3,590.00`** on
    the developer's dev database before ``f633d46a``, and inferring a fact from
    another column being populated is the shape finding **N-241** deleted one
    column over -- the row's ``settled_basis_id`` existed (X-au-c3 to
    X-bi-4b-2) precisely so that *"which one a figure is stands in
    ``settled_basis_id`` rather than being inferred from a column being
    populated"*, a question the movement's ``figure_source_id`` answers now.

    **It does NOT replace ``reconciled_by_id``, and the two are not the same
    question.**  That column names WHICH statement was seen to show this money;
    this one says what kind of day the row records.  A row whose asserted day
    the bank later CONFIRMS keeps its link and becomes ``observed`` -- one fact
    changed, the other did not -- which the coupled reading could not express at
    all.

    Application code resolves these via ``ref_cache.settled_day_basis_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  There is deliberately no member meaning *not settled*: a row
    that carries no day carries no basis either, and the pairing CHECK is a
    BICONDITIONAL over the two NULL-nesses, so no ref id is frozen into the
    schema (the reason :class:`AmountSourceEnum` has no ``own`` member).
    """

    OBSERVED = "observed"
    ASSERTED = "asserted"
    ENTERED = "entered"
    BORROWED = "borrowed"


class MovementFigureSourceEnum(enum.Enum):
    """WHO WROTE a movement's FIGURE (plan step **X-bi-3a**, ruling **R-BAL39**).

    A movement is a ``budget.transaction_entries`` row: a purchase recorded
    against an envelope, or -- since X-bi-3a -- the COVERING MOVEMENT a settle
    writes for a bill (and since X-bi-3b for a paycheck), the payment row that
    records the parent's money the way a purchase already records an
    envelope's.  Three writers put a figure on one,
    and this catalogue says which, partitioned by the SOURCE of the figure --
    the argument :class:`SettledDayBasisEnum` makes for the DAY one column
    over, applied to the amount beside it:

        resolved  -- the settle priced it from the PLAN at the moment of the
                     settle (the definition's price series, the salary
                     profile, the loan's schedule).  Not repeatable, which is
                     why it is RECORDED: a re-settle re-prices it, because the
                     app's own inference about a moment that has passed is
                     worth less than a fresh one.
        typed     -- a person stated it: a hand-typed purchase, or a figure
                     read off a statement and typed over the plan's.  A fact
                     somebody stands behind; a re-settle HONOURS it, exactly as
                     ``status_seam.Settlement.from_settle`` honours a retained
                     ``corrected`` record.
        observed  -- the bank's own line stated it: a purchase born from a
                     statement line, or a figure the statement matcher took
                     from a line onto a matched row.  A match that confirms a
                     DAY alone writes no figure and so changes nothing about
                     who wrote it: the label follows the WRITER of the figure,
                     never the evidence beside it (ruling **R-BAL61**, which
                     measured the older "a confirmation raises a typed figure
                     to observed" rule false on every row kind -- every
                     bank-observed day ever written onto a settled row was a
                     day-only confirmation).

    **A reader needs it, which is why it is a column and not a label.**
    ``Settlement.from_settle`` re-prices a ``resolved`` record and honours a
    stated one (``Settlement.stated``); a bill's figure lives on its covering
    movement and nowhere else since plan step ``balance:X-bi-4b-2`` deleted
    ``transactions.settled_basis_id`` (migration ``45f10b870c8b``), so that
    distinction has no other home.  The catalogue was NEW rather than a
    reuse of that column's ``SettlementBasisEnum`` (``derived`` /
    ``corrected`` / ``purchases``, retired with it) because its ``purchases``
    member meant nothing on a movement (a movement IS a purchase) and its
    ``corrected`` member was defined as *a human typed it*, which a bank-born
    purchase would have to claim falsely.

    **It records who last WROTE the figure, and a withdrawal does not
    launder it**: releasing a match leaves a purchase's ``observed`` day in
    place (finding **N-333**, *the days an accepted match wrote are the APP's
    own record*), and the figure's source stays with it.  A day-only edit
    leaves the source alone; the source changes only when someone WRITES the
    figure -- the human PATCH makes it ``typed``, a bank line ``observed``, a
    settle ``resolved``, ``typed`` or (through the matcher's transaction arm)
    ``observed``.  Every writer STATES it with the figure, as one value
    (:class:`app.services.stated_figure.StatedFigure`; plan step
    **X-bi-3e-1**, ruling **R-BAL69**); nothing infers it from the day.

    Application code resolves these via ``ref_cache.movement_figure_source_id``
    and compares against the integer ID -- never the string ``name`` -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  NOT NULL and no default: both writers of a movement state it,
    and a stored guess would be the shape ruling **R-IY** deletes.
    """

    RESOLVED = "resolved"
    TYPED = "typed"
    OBSERVED = "observed"


class StatementBalanceEvidenceEnum(enum.Enum):
    """How strongly an imported statement's balance is EVIDENCED (**X-f6e-1**).

    A statement states a balance (``Balance as of 08/22/2026,2459.600000``) and
    a list of lines.  What the app needs from it is an ANCHOR -- the account
    held this much at the end of this day -- and what this says is how much
    that anchor can be trusted.  Ruling **R-GF**.

        file_chain     -- the file states a balance beside EVERY line, so it
                          proves itself and needs nothing outside.  The only
                          level that rests on this file alone.
        corroborated   -- the figure agrees with a balance the app already
                          holds which is ITSELF evidenced, so two independent
                          statements say the same thing.
        uncorroborated -- nothing confirms it.  The figure is taken at face
                          value, which is what a FIRST import is and what any
                          anchor rooted in one remains.

    **It is the WEAKEST LINK in the chain behind the figure, and that is the
    whole design rather than a caution.**  An anchor's day is often SOLVED
    rather than stated -- ``stated - sum(lines up to d) == opening`` picks the
    day out of the file's own lines -- and it is tempting to record that the
    day was worked out and call the result corroborated.  It is not: a solved
    day is only as good as the opening it was solved against, so an anchor
    solved against an uncorroborated opening is uncorroborated too.  Recording
    the minimum makes that true BY CONSTRUCTION.

    **What it removes is a defect an adversarial review reproduced in two
    clicks** (2026-08-23): re-uploading the identical file made the app walk
    back from its own assumption, find that the file agreed with it, and record
    the result as corroborated -- the assumption checking itself, with the
    receipt turning from a warning to a green tick.  Under a weakest link no
    re-upload can strengthen anything, because the chain still contains the
    assumption.

    **The partition is over EVIDENCE, which is what makes it exhaustive.**  A
    figure is evidenced by its own file, by other files, or by nothing; there
    is no fourth kind, so every level lands in exactly one member -- and since
    plan step ``balance:X-bj-1`` that includes the OWNER's own true-ups, which
    sit in the same relation (``budget.account_anchor_history.evidence_id``)
    at ``uncorroborated``, because a typed figure is confirmed by nothing in
    the app.  A fourth member naming the owner was ruled out 2026-09-16: it
    would be a SOURCE, and the source is the level's ``statement_import_id``.

    **There is deliberately no member meaning "this file states no balance"**,
    for the reason :class:`SettledDayBasisEnum` has none meaning *not settled*:
    that is the ABSENCE of a member rather than one of them.  An import whose
    figure was placed on no day owns no level row, so it carries no evidence
    at all rather than a member saying so, and no ref id is frozen into the
    schema.

    Application code resolves these via ``ref_cache.statement_balance_evidence_id``
    and ``ref_cache.statement_balance_evidence_member`` and compares against the
    integer ID -- never the string ``name`` -- matching the project-wide
    ``ref-table: IDs for logic, strings for display only`` invariant.
    """

    FILE_CHAIN = "file_chain"
    CORROBORATED = "corroborated"
    UNCORROBORATED = "uncorroborated"

    @property
    def strength(self) -> int:
        """Return where this member sits on the evidence ladder, 0 the weakest.

        **Declared once, here, because the ORDER is the meaning.**  A caller
        that compared members by writing its own mapping would be a second
        statement of the ladder, and the two would drift; the weakest-link rule
        (:func:`app.services.statement_import.weaker_of`) is the only consumer
        and it reads this.
        """
        return _EVIDENCE_STRENGTH[self]


#: The evidence ladder, weakest first.  A module-level map rather than a body
#: inside :attr:`StatementBalanceEvidenceEnum.strength` so the ORDER is
#: readable as a list at a glance, which is what a ladder is.
_EVIDENCE_STRENGTH = {
    StatementBalanceEvidenceEnum.UNCORROBORATED: 0,
    StatementBalanceEvidenceEnum.CORROBORATED: 1,
    StatementBalanceEvidenceEnum.FILE_CHAIN: 2,
}
