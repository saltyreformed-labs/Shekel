"""The budget schema's lookups: a row's status and type, an account's kind, goals and openings.

Split out of the flat ``app/enums.py`` at its 1,000-line ceiling (see
:mod:`app.enums` for why).  Every class below stands exactly as it stood there.
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
