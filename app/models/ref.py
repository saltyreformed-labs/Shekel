"""
Shekel Budget App -- Reference Table Models (ref schema)

Lookup / enum tables that are rarely written and frequently joined.
New values are added via INSERT, never via schema migration.
"""

from sqlalchemy import text

from app.extensions import db


class AccountTypeCategory(db.Model):
    """Account type grouping category (Asset, Liability, Retirement, Investment).

    Groups account types into high-level buckets used for dashboard
    layout ordering and chart axis assignment (assets on left y-axis,
    liabilities on right y-axis).
    """

    __tablename__ = "account_type_categories"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<AccountTypeCategory {self.name}>"


class AccountType(db.Model):
    """Account type reference (Checking, Savings, HYSA, Mortgage, etc.).

    Boolean columns capture behavioural groupings:

        has_parameters    -- This type has a linked *Params table
                             (e.g. HysaParams, LoanParams) that
                             must be created alongside the account.
        has_amortization  -- This type uses the amortization engine
                             for balance projections instead of the
                             generic balance calculator.
        has_interest      -- This type uses the interest projection
                             engine (InterestParams: APY, compounding).
                             Applies to Asset-category types like
                             HYSA, Money Market, CD, HSA.
        is_pretax         -- Contributions to this type are pre-tax
                             (relevant for retirement gap analysis).
                             Applies to Retirement-category types
                             like 401(k), Traditional IRA.
        is_liquid         -- This type holds liquid funds that count
                             toward emergency fund calculations and
                             savings goal eligibility.  Applies to
                             Asset-category types like Checking,
                             Savings, HYSA, Money Market.
        has_appreciation  -- This type's balance is a market value the
                             user sets that appreciates (or depreciates)
                             over time at a configured annual rate
                             (AssetAppreciationParams), projected by the
                             growth engine with contributions zeroed.
                             Checked before has_parameters in
                             ``classify_account`` so a parameterised
                             physical asset (Property) is not mistaken
                             for an investment.  Applies to Asset-category
                             physical assets like Property.
        has_revolving_credit -- This type is a revolving credit line (a
                             Credit Card): an ordinary Liability whose
                             balance is ``opening + SUM(movements)``, folded
                             by the same cash fold every non-loan account
                             rides, with the card's statement cycle, payment
                             rule and refusals layered on top by the
                             credit-card arc.  It is a FLAG and not a
                             projection kind (``classify_account`` never
                             reads it; a card stays PLAIN), read through the
                             ONE predicate
                             :func:`app.services.account_projection.is_revolving`.
                             Seed-only, like ``has_appreciation``: no user
                             door sets it.  ``ck_account_types_revolving_is_plain``
                             makes a revolving type that also carries
                             ``has_amortization`` / ``has_interest`` /
                             ``has_appreciation`` / ``has_parameters``
                             UNREPRESENTABLE rather than resolved by
                             precedence -- the card's params row is optional
                             by design, so the type carries
                             ``has_parameters = FALSE`` (design
                             ``docs/design/credit_card_from_scratch.md``
                             3.1, plan step credit_card:CC-1).

    Display / validation metadata:

        icon_class        -- Bootstrap icon class for UI rendering
                             (e.g. 'bi-house' for Mortgage).
        max_term_months   -- Maximum loan term in months for
                             type-specific validation.  NULL means
                             no type-specific limit.

    Multi-tenant ownership (commit C-28 / F-044):

        user_id           -- Owning user (nullable).  ``NULL`` denotes
                             a seeded built-in type managed by
                             ``app.ref_seeds.seed_reference_data`` and is
                             read-only to every owner.  A non-NULL
                             value means the row was created by that
                             user via the ``/accounts/types`` route;
                             only that owner may rename or delete it.
                             ``ondelete='RESTRICT'`` -- deleting a
                             user with custom types refuses the user
                             delete until those rows are pruned, so
                             we never orphan ``budget.accounts`` rows
                             whose ``account_type_id`` would dangle.

    Uniqueness invariant.  The legacy ``UNIQUE(name)`` constraint
    becomes incompatible with per-user copies of seed names ("Owner A
    can call her custom type 'HYSA' even when a built-in 'HYSA' exists";
    see C-28 acceptance criteria).  It is replaced by two partial
    unique indexes evaluated together:

      ``uq_account_types_seeded_name``   -- ``(name) WHERE user_id IS NULL``,
          guaranteeing one built-in per name (preserves the ref_cache
          enum-to-id contract that maps each ``AcctTypeEnum`` member
          to a single seeded row).
      ``uq_account_types_user_name``     -- ``(user_id, name) WHERE user_id IS NOT NULL``,
          guaranteeing each owner has at most one custom type per
          name.  A user-owned row may share a name with the seeded
          built-in (the WHERE clauses keep the two index domains
          disjoint), and two different owners may both have a custom
          "Crypto" without conflict.
    """

    __tablename__ = "account_types"
    __table_args__ = (
        db.Index(
            "uq_account_types_seeded_name",
            "name",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        db.Index(
            "uq_account_types_user_name",
            "user_id", "name",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        db.Index("ix_account_types_user_id", "user_id"),
        # A revolving type is PLAIN: the "both flags" type the 2026-07-19
        # plan resolved by precedence is refused by the schema instead
        # (design 3.1, plan step credit_card:CC-1).
        db.CheckConstraint(
            "NOT has_revolving_credit OR NOT ("
            "has_amortization OR has_interest OR has_appreciation "
            "OR has_parameters)",
            name="ck_account_types_revolving_is_plain",
        ),
        {"schema": "ref"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), nullable=False)
    # F-073 / C-43: ondelete=RESTRICT closes the audit gap where the
    # nine ref-table FKs default to PostgreSQL's implicit NO ACTION.
    # RESTRICT raises immediately on the offending statement (vs. NO
    # ACTION which defers to commit), giving a clean error message at
    # the point of the violating DELETE; the name follows the
    # SHEKEL_NAMING_CONVENTION ("fk": "fk_<table>_<column_0_name>")
    # so the model rendering and the live-DB rendering converge on
    # the same string.
    category_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "ref.account_type_categories.id",
            name="fk_account_types_category_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    has_parameters = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_amortization = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_interest = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_pretax = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_liquid = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_appreciation = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    has_revolving_credit = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    icon_class = db.Column(db.String(30), nullable=True)
    max_term_months = db.Column(db.Integer, nullable=True)
    # NULL -> seeded built-in row, read-only to every owner.  Non-NULL
    # -> owned by that user; only they may rename or delete it.  See
    # the class docstring for the per-user copy contract and the
    # paired partial unique indexes that enforce it.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    category = db.relationship("AccountTypeCategory")

    def __repr__(self):
        return f"<AccountType {self.name}>"


class TransactionType(db.Model):
    """Transaction type reference: 'Income', 'Expense'."""

    __tablename__ = "transaction_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<TransactionType {self.name}>"


class Status(db.Model):
    """Transaction status reference.

    Values: Projected, Paid, Received, Credit, Cancelled.

    Boolean columns capture logical groupings so that application code
    can branch on a single column instead of comparing against sets of
    status names:

        is_settled          -- The real-world transaction has completed
                               (Paid, Received).  Such a row RECORDS
                               what moved, and the balance counts that record
                               rather than the row's plan (plan step X-au-c3;
                               it read actual_amount until then).
        is_immutable        -- The recurrence engine must not overwrite
                               this transaction (Paid, Received, Credit,
                               Cancelled).
        excludes_from_balance -- This status contributes zero to the
                               projected checking balance (Credit,
                               Cancelled).
    """

    __tablename__ = "statuses"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(15), unique=True, nullable=False)
    is_settled = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_immutable = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    excludes_from_balance = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )

    def __repr__(self):
        return f"<Status {self.name}>"


class RecurrenceUnit(db.Model):
    """Recurrence cadence-unit reference: period, week, month, year.

    The first axis of the two-axis recurrence model (redesign step R2): a
    rule recurs every ``budget.recurrence_rules.interval_n`` units of this
    kind.  Four of the closed pattern set's names were one idea with a
    different integer baked into the name (every 1 / 3 / 6 / 12 months);
    moving that integer into a column is what makes "every other month" and
    "every two years" expressible.  That set lived in ``ref.recurrence_patterns``
    until plan step **R9** dropped the table.

    Application code resolves these via ``ref_cache.recurrence_unit_id`` and
    compares against the integer ID -- never the string ``name`` -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  See :class:`~app.enums.RecurrenceUnitEnum` for the per-value
    semantics.
    """

    __tablename__ = "recurrence_units"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<RecurrenceUnit {self.name}>"


class PeriodPlacement(db.Model):
    """Occurrence-date -> pay-period placement reference (redesign step R2).

    An occurrence is a calendar date; a Shekel row lives in a pay period.
    This table names the rule that carries one to the other -- the axis
    today's ``Monthly`` and ``Monthly First`` patterns differ on (they differ
    on the anchor day as well; see :class:`~app.enums.PeriodPlacementEnum`),
    and therefore a real user choice rather than a derived detail.

    Application code resolves these via ``ref_cache.period_placement_id``
    and compares against the integer ID -- never the string ``name``.  See
    :class:`~app.enums.PeriodPlacementEnum` for the per-value semantics.
    """

    __tablename__ = "period_placements"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)

    def __repr__(self):
        return f"<PeriodPlacement {self.name}>"


class BusinessDayShift(db.Model):
    """Weekend/holiday occurrence-shift reference (redesign step R2).

    Whether an occurrence falling on a non-business day moves backward, moves
    forward, or stays put.  Step R2 seeds the table and defaults every rule
    to ``none``; step R8 is the first step that lets a user pick another
    value, so that step turns behaviour on rather than adding a column.

    Application code resolves these via ``ref_cache.business_day_shift_id``
    and compares against the integer ID -- never the string ``name``.  See
    :class:`~app.enums.BusinessDayShiftEnum` for the per-value semantics and
    for why the shift applies to the cash date only.
    """

    __tablename__ = "business_day_shifts"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<BusinessDayShift {self.name}>"


class FilingStatus(db.Model):
    """Tax filing status reference (Phase 2, but schema created now)."""

    __tablename__ = "filing_statuses"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(25), unique=True, nullable=False)

    def __repr__(self):
        return f"<FilingStatus {self.name}>"


class PaycheckLineKind(db.Model):
    """A payroll line's KIND: its position in the paycheck's waterfall.

    ``pre_tax_deduction`` and ``post_tax_deduction`` since plan step
    salary:R18-a (ruling R-SAL38), which renamed ``ref.deduction_timings``
    (``pre_tax`` / ``post_tax``, Phase 2) because the table it keys,
    ``salary.paycheck_lines``, holds the earning side too from R18-b.  The
    vocabulary is :class:`~app.enums.PaycheckLineKindEnum`.
    """

    __tablename__ = "paycheck_line_kinds"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(25), unique=True, nullable=False)

    def __repr__(self):
        return f"<PaycheckLineKind {self.name}>"


class CalcMethod(db.Model):
    """Calculation method reference: 'flat', 'percentage' (Phase 2)."""

    __tablename__ = "calc_methods"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(12), unique=True, nullable=False)

    def __repr__(self):
        return f"<CalcMethod {self.name}>"


class TaxType(db.Model):
    """Tax type reference: 'flat', 'none', 'bracket' (Phase 2)."""

    __tablename__ = "tax_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<TaxType {self.name}>"


class RaiseType(db.Model):
    """Raise type reference: 'merit', 'cola', 'custom' (Phase 2)."""

    __tablename__ = "raise_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<RaiseType {self.name}>"


class GoalMode(db.Model):
    """Savings goal amount mode reference: 'Fixed', 'Income-Relative'.

    Determines whether a savings goal target is a fixed dollar amount
    or a multiple of the user's income (e.g. 3 months of paychecks).
    """

    __tablename__ = "goal_modes"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<GoalMode {self.name}>"


class IncomeUnit(db.Model):
    """Income multiplier unit reference: 'Paychecks', 'Months'.

    Used with income-relative savings goals to specify whether the
    multiplier is measured in paychecks or calendar months.
    """

    __tablename__ = "income_units"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<IncomeUnit {self.name}>"


class LoanAnchorSource(db.Model):
    """Loan anchor event source reference: 'origination', 'user_trueup'.

    Tags every row in :class:`budget.loan_anchor_events` with the
    provenance of that anchor.  ``origination`` is materialised once
    per loan from the immutable ``LoanParams.origination_date`` /
    ``LoanParams.original_principal`` fields; ``user_trueup`` is
    appended by the loan dashboard's balance edit flow whenever the
    operator asserts a new dated balance (commit C-16 / decision D-C).

    Application code resolves these via ``ref_cache.loan_anchor_source_id``
    and compares against the integer ID -- never the string name --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant.
    """

    __tablename__ = "loan_anchor_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<LoanAnchorSource {self.name}>"


class AccountOpeningSource(db.Model):
    """Account opening source reference: 'user_declared', 'migration_derived'.

    Tags every row in :class:`budget.account_openings` with the provenance of
    that opening equity figure (plan step X-f3c-2a).  ``user_declared`` is
    written by ``account_service.create_account`` from the balance the owner
    typed; ``migration_derived`` marks the seven rows the X-f3c-2a migration
    computed for accounts that already existed, off the posted ledger's own
    ``account_opening`` entry.

    **The split is not clerical.**  A derived figure is the pre-X-f3c-2a
    inference frozen, and finding **N-275** measures account 1's wrong by
    ``$436.05``; a declared one is an observation.  A surface that cannot tell
    them apart presents a guess and a fact identically.

    The loan twin is :class:`LoanAnchorSource`, and the shape is the same by
    design: a typed provenance column on an append-only balance record.
    Application code resolves these via ``ref_cache.account_opening_source_id``
    and compares against the integer ID -- never the string name -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    __tablename__ = "account_opening_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<AccountOpeningSource {self.name}>"


class UserRole(db.Model):
    """User role reference: 'owner', 'companion'.

    Determines route access and data visibility scope.
    Owner accounts have full access.  Companion accounts
    see only transactions from companion-visible templates
    belonging to their linked owner.
    """

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<UserRole {self.name}>"


class EmployerContributionType(db.Model):
    """Employer-contribution type reference: 'none', 'flat_percentage', 'match'.

    Selects the employer retirement-contribution formula the growth
    engine applies to an investment/retirement account.  Application
    code resolves these via ``ref_cache.employer_contribution_type_id``
    and branches on the integer ID -- never the string name --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant (#38; replaced the prior free-string
    ``investment_params.employer_contribution_type`` column).
    """

    __tablename__ = "employer_contribution_types"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<EmployerContributionType {self.name}>"


class CompoundingFrequency(db.Model):
    """Interest compounding frequency reference: 'daily', 'monthly', 'quarterly'.

    Selects the per-period compounding formula the interest projection
    engine applies to an interest-bearing account.  Application code
    resolves these via ``ref_cache.compounding_frequency_id`` and
    branches on the integer ID -- never the string name -- matching
    the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant (#38; replaced the prior free-string
    ``interest_params.compounding_frequency`` column).
    """

    __tablename__ = "compounding_frequencies"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(12), unique=True, nullable=False)

    def __repr__(self):
        return f"<CompoundingFrequency {self.name}>"


class LedgerAccountClass(db.Model):
    """Ledger account class reference: the five accounting classes, plus Unrealized.

    The five fundamental accounting classes for the double-entry posting
    ledger (Build-Order Step 2), and the OTHER COMPREHENSIVE INCOME class
    ruling **R-FO** added (plan step X-f3d).  Every ``budget.ledger_accounts``
    row carries a ``class_id`` FK to one of these rows; the class fixes how a
    reader later interprets that account's accumulated posting balance.

    ``Unrealized`` is a sixth ROW and a sixth reporting class, not a sixth
    fundamental one: an unrealized change in value is still equity on the balance sheet
    (folded into one derived accumulated line, as Income and Expense are folded
    into Retained Earnings), and it is separated from Income only so that
    ``net_income = income - expense`` cannot count a price movement nobody
    sold into cash.  New reference values are data, never schema -- a class row
    is added by a migration's inline seed like any other ref row.

    ``is_debit_normal`` captures the natural-balance side as a boolean so
    application code branches on a single column instead of comparing
    against a set of class names:

        is_debit_normal -- TRUE for classes whose balance increases on a
                           debit (Asset, Expense); FALSE for classes whose
                           balance increases on a credit (Liability,
                           Income, Equity, Unrealized).  A reader presents a
                           credit-normal account's natural balance by
                           negating its accumulated debit-positive posting
                           sum.  No ``server_default``: the value is an
                           intrinsic property of each class, set explicitly
                           by every seed and insert, so a forgotten value
                           must fail loud (NOT NULL) rather than silently
                           default to a wrong-but-valid FALSE.

    Application code resolves these via
    ``ref_cache.ledger_account_class_id`` and reads the natural-balance
    side via the ``ref_cache.ledger_class_is_debit_normal`` meta accessor,
    branching on the integer ID and the cached boolean -- never the string
    ``name`` -- matching the project-wide ``ref-table: IDs for logic,
    strings for display only`` invariant.
    """

    __tablename__ = "ledger_account_classes"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(12), unique=True, nullable=False)
    is_debit_normal = db.Column(db.Boolean, nullable=False)

    def __repr__(self):
        return f"<LedgerAccountClass {self.name}>"


class PostingKind(db.Model):
    """Posting-leg kind reference: the nature of a single ledger leg.

    Tags every ``budget.account_postings`` row with the kind of economic
    event the leg represents.  Step 2 seeds the single value ``transfer``;
    later Build-Order steps INSERT additional kinds (``income``,
    ``expense``, ``principal``, ``interest``, ``contribution``, ``tax``,
    ...) via their own migrations -- new values are data, never schema.

    Application code resolves these via ``ref_cache.posting_kind_id`` and
    compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant.
    """

    __tablename__ = "posting_kinds"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<PostingKind {self.name}>"


class PostingSource(db.Model):
    """Journal-entry source-event reference: the kind of event that posted.

    Tags every ``budget.journal_entries`` row with the kind of source
    event that produced it, independently of the concrete (nullable)
    source FK the entry also carries.  Step 2 seeds the single value
    ``transfer``; later steps INSERT ``transaction``, ``loan_payment``,
    ``paycheck``, ``credit_payback`` via their own migrations -- new
    values are data, never schema.

    Application code resolves these via ``ref_cache.posting_source_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for
    display only`` invariant.
    """

    __tablename__ = "posting_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<PostingSource {self.name}>"


class LedgerAccountKind(db.Model):
    """Row-kind discriminator reference for ``budget.ledger_accounts``.

    The explicit, positive discriminator (Build-Order Step 4) that replaces
    inferring a ledger account's kind from the NULL-pattern of its
    ``account_id`` / ``category_id`` / ``is_owner_bucket`` columns.  Every
    ``budget.ledger_accounts`` row carries a ``kind_id`` FK to one of these
    rows (Commit 2); the kind fixes how a reader enumerates and groups the
    chart of accounts without testing which columns are NULL.

    Step 4 seeds seven kinds: the four the chart already uses (``linked``,
    ``category``, ``fallback``, ``orphan`` -- see
    :class:`app.models.ledger_account.LedgerAccount`) plus the three per-loan
    accounts the loan-payment correction books into (``loan_interest`` and
    ``loan_escrow``, both Expense; ``loan_refund``, an Asset).  Later steps
    INSERT additional kinds via their own migrations -- new values are data,
    never schema: the loan read switch's ``equity_opening``, Step 5's
    ``anchor_equity``, and ruling **R-FO**'s ``interest_income`` /
    ``unrealized_change`` (plan step X-f3d), which say what a modelled account's
    balance-assertion true-up actually WAS.

    Application code resolves these via ``ref_cache.ledger_account_kind_id``
    and compares against the integer ID -- never the string ``name`` --
    matching the project-wide ``ref-table: IDs for logic, strings for display
    only`` invariant.
    """

    __tablename__ = "ledger_account_kinds"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<LedgerAccountKind {self.name}>"


class AmountSource(db.Model):
    """WHICH RELATION states a row's amount (ruling **R-FI**, plan step X-au-c1).

    The catalogue behind ``budget.transactions.amount_source_id`` and
    ``budget.transfers.amount_source_id``.  A row that OWNS its amount carries
    NULL here and a figure in its amount column; a row whose amount is DERIVED
    names the relation that prices it and carries no figure at all.  The pairing
    is a CHECK on each table, so a stale derived figure is unrepresentable
    rather than merely unlikely.

    One value: ``template`` (the recurring definition that generated the row
    states its price).  ``parent_transfer`` (a shadow was worth exactly what
    its parent transfer was) went with the shadows' link at plan step
    ``balance:X-bi-6-5`` (migration ``c956c1d83108``).  Later steps INSERT
    additional relations -- plan step X-au-i's CC payback would add the row
    it repays -- and new values are data, never schema.

    **They name the RELATION, not the RULE**, which is a developer ruling of
    2026-08-12 amending R-FI's own five-value enumeration; the evidence (two
    live routes that falsify a stored rule) is on
    :class:`app.enums.AmountSourceEnum`, together with the reason the OWN state
    is a NULL rather than a row here.

    Application code resolves these via ``ref_cache.amount_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    __tablename__ = "amount_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<AmountSource {self.name}>"


class StatementSource(db.Model):
    """WHERE a recorded statement line came from (ruling **R-FP**, X-f6a).

    The catalogue behind ``budget.statement_imports.source_id``,
    ``budget.bank_statement_lines`` (through its import) and
    ``budget.account_external_identities.source_id``.  One row per source
    ADAPTER: a format at an institution, because one bank publishes one
    statement several ways and the ways carry different facts.

    One value today, ``secu_checking_csv``.  Later adapters -- SECU's OFX, the
    Capital One card, and ``X-f6b``'s automated SimpleFIN feed -- INSERT a row
    here, because a new source is data and never schema.

    Why the member names a format rather than an institution, and why an
    adapter without an external transaction id loses nothing, are both measured
    on :class:`app.enums.StatementSourceEnum`.

    Application code resolves these via ``ref_cache.statement_source_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    __tablename__ = "statement_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    #: What a human sees.  A source is chosen on an upload form, so unlike most
    #: ref rows this one is READ for display as well as joined -- and the label
    #: is a column rather than a template-side mapping for the reason the
    #: project keeps ref labels in ref: a second spelling in Jinja is a second
    #: place for a new adapter's name to be forgotten.
    display_name = db.Column(db.String(80), nullable=False)

    def __repr__(self):
        return f"<StatementSource {self.name}>"


class SettledDayBasis(db.Model):
    """HOW a settled row's settle DAY is known (plan step **X-az**).

    The catalogue behind ``budget.transactions.settled_day_basis_id`` and
    ``budget.transaction_entries.settled_day_basis_id`` -- BOTH tables, because
    both carry ``settled_on`` and all three kinds of day are written to each.
    That is the difference from :class:`MovementFigureSource` below it, which
    needs only the entries table: a figure and who wrote it live on the
    movement, a day on the row it dates as well.  (``SettlementBasis``, the
    row's own figure catalogue behind ``transactions.settled_basis_id``, was
    deleted with that column at plan step ``balance:X-bi-4b-2``, migration
    ``45f10b870c8b``.)

    A row that has not settled carries NULL here and no settle day; a row that
    HAS one carries both, and this column says which of three ways the day was
    arrived at -- ``observed`` (a bank statement showed the money posting),
    ``asserted`` (the owner asserted a balance for that day and this money was
    inside it, so the day is an UPPER BOUND) or ``entered`` (the owner's own
    record, typed or stamped by the door).

    :class:`app.enums.SettledDayBasisEnum` carries what the inference this
    replaces cost and why the difference between a bound and a point is a money
    question rather than a label.

    Application code resolves these via ``ref_cache.settled_day_basis_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.

    ``budget.transfers`` carries no such column and needs none: a transfer's
    money moves on its two shadow ``Transaction`` rows, which each record
    their own day.
    """

    __tablename__ = "settled_day_bases"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<SettledDayBasis {self.name}>"


class MovementFigureSource(db.Model):
    """WHO WROTE a movement's figure (plan step **X-bi-3a**, ruling **R-BAL39**).

    The catalogue behind ``budget.transaction_entries.figure_source_id`` --
    ``resolved`` (the settle priced it from the plan), ``typed`` (a person
    stated it) or ``observed`` (the bank's line stated it).  It is the twin of
    :class:`SettledDayBasis` for the FIGURE beside the day, and it lives on
    the one table that holds movements: a purchase against an envelope has
    always been one, and since X-bi-3a a settle writes one for a bill too
    (since X-bi-3b for a paycheck) -- the covering movement that records the
    parent's money the way a purchase records an envelope's.  It is the ONE
    home of a settled row's figure and its writer since plan step
    ``balance:X-bi-4b-2`` deleted the row's own ``settled_amount`` /
    ``settled_basis_id`` (migration ``45f10b870c8b``).

    :class:`app.enums.MovementFigureSourceEnum` carries the argument: why the
    partition is over the SOURCE, why a reader needs it, and why it is not a
    reuse of the transaction's catalogue.

    Application code resolves these via ``ref_cache.movement_figure_source_id``
    and compares against the integer ID -- never the string ``name`` -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    __tablename__ = "movement_figure_sources"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<MovementFigureSource {self.name}>"


class StatementBalanceEvidence(db.Model):
    """How strongly an imported statement's balance is EVIDENCED (**X-f6e-1**).

    The catalogue behind ``budget.account_anchor_history.evidence_id``, the
    level relation's rank (plan step ``balance:X-bj-1``; it was
    ``budget.statement_imports.balance_evidence_id`` from X-f6e-1 until then).
    Every level carries one, and this says how much it can be trusted --
    ``file_chain`` (the file states a balance beside every line, so it proves
    itself), ``corroborated`` (the figure agrees with a balance the app already
    holds which is itself evidenced) or ``uncorroborated`` (nothing confirms
    it, which is what a first import is and what an owner-typed figure always
    is).  An import that placed no figure on a day owns no level and so
    carries none.

    :class:`app.enums.StatementBalanceEvidenceEnum` carries why it is the
    WEAKEST LINK in the chain rather than a description of how the day was
    worked out: a solved day is only as good as the opening it was solved
    against, so an anchor solved against an uncorroborated one is
    uncorroborated too -- which is what stops a re-upload of the same file from
    checking an assumption against itself and calling the result confirmed.

    **Its row ORDER carries no meaning and no reader may assume it does.**  The
    ladder is stated once, on the enum
    (:attr:`~app.enums.StatementBalanceEvidenceEnum.strength`); an early draft
    ordered a query by this table's id and was measured to return the weakest
    anchor rather than the strongest.

    Application code resolves these via
    ``ref_cache.statement_balance_evidence_id`` and its inverse
    ``ref_cache.statement_balance_evidence_member``, and compares against the
    integer ID -- never the string ``name`` -- matching the project-wide
    ``ref-table: IDs for logic, strings for display only`` invariant.
    """

    __tablename__ = "statement_balance_evidence"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<StatementBalanceEvidence {self.name}>"


class WithholdingKind(db.Model):
    """WHICH TAX a transcribed pay stub's withholding figure is (**salary:S11-a**).

    The catalogue behind ``salary.pay_stub_withholdings.withholding_kind_id``
    (ruling **R-SAL42**): federal income, state income, Social Security and
    Medicare.  A tax a stub may print and this list does not yet name (a city
    income tax) is a new ROW here and a new enum member, never a table change;
    :class:`app.enums.WithholdingKindEnum` carries why it is a list rather than
    four columns.

    Application code resolves these via ``ref_cache.withholding_kind_id`` and
    compares against the integer ID -- never the string ``name`` -- matching the
    project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.
    """

    __tablename__ = "withholding_kinds"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)

    def __repr__(self):
        return f"<WithholdingKind {self.name}>"
