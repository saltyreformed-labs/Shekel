"""One function per "what id is this enum member", and nothing else.

Split out of the flat ``app/ref_cache.py`` at plan step
**bank_import:X-f6e-1** (see :mod:`._state` for why).  **The move is pure**:
every function below stands exactly as it stood, comments and pragmas
included.  The one addition is that step's own
:func:`statement_balance_evidence_id`, the twenty-sixth of them.
:mod:`app.ref_cache` re-exports all of them, so no caller changed.

**Every one of them returns an ID and never a ``name``**, which is the
project-wide invariant these exist to serve: reference tables drive logic by
their integer key, and their strings are for display.  The custom checker
``shekel-refname-compare`` is the gate; this module is the door it points at.

**The cache is read through :func:`._state.cache` rather than bound by value**,
which is the one thing the split had to be careful about and is argued where
that function is defined.  It is the single mechanical difference between these
bodies and the ones the flat module carried: ``_cache.X`` became ``cache().X``
and ``_require_init()`` became ``require_init()``, uniformly, thirty-one times.
"""

from app.enums import (
    AccountOpeningSourceEnum,
    AcctCategoryEnum,
    AcctTypeEnum,
    AmountSourceEnum,
    BusinessDayShiftEnum,
    CalcMethodEnum,
    CompoundingFrequencyEnum,
    FilingStatusEnum,
    PaycheckLineKindEnum,
    EmployerContributionTypeEnum,
    GoalModeEnum,
    IncomeUnitEnum,
    LedgerAccountClassEnum,
    LedgerAccountKindEnum,
    LoanAnchorSourceEnum,
    MovementFigureSourceEnum,
    PeriodPlacementEnum,
    PostingKindEnum,
    PostingSourceEnum,
    RaiseTypeEnum,
    RecurrenceUnitEnum,
    RoleEnum,
    SettledDayBasisEnum,
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
    StatusEnum,
    TaxTypeEnum,
    TxnTypeEnum,
    WithholdingKindEnum,
)

from ._state import cache, require_init


def status_id(member):
    """Return the integer primary key for a StatusEnum member.

    Args:
        member: A ``StatusEnum`` member (e.g. ``StatusEnum.PROJECTED``).

    Returns:
        int -- the ``ref.statuses.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid StatusEnum member.
    """
    require_init()
    return cache().enum_ids[StatusEnum][member]


def txn_type_id(member):
    """Return the integer primary key for a TxnTypeEnum member.

    Args:
        member: A ``TxnTypeEnum`` member (e.g. ``TxnTypeEnum.INCOME``).

    Returns:
        int -- the ``ref.transaction_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid TxnTypeEnum member.
    """
    require_init()
    return cache().enum_ids[TxnTypeEnum][member]


def transaction_type_is_income(transaction_type_id):
    """Return True if *transaction_type_id* refers to the Income type row.

    Thin convenience over the cached map so cross-field validators and
    other call sites can ask "is this an income type?" without importing
    ``TxnTypeEnum`` themselves.  Used by the template Marshmallow schemas
    to enforce that ``is_envelope`` (envelope rollover semantics) is
    only set on expense templates.

    Args:
        transaction_type_id: Integer primary key of a
            ``ref.transaction_types`` row.

    Returns:
        bool -- True iff *transaction_type_id* equals the cached Income
        type ID; False for the Expense type or any unrecognised value.
        Callers that need to validate the FK itself must do so
        separately (this accessor never raises for unknown IDs).

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return transaction_type_id == cache().enum_ids[TxnTypeEnum][TxnTypeEnum.INCOME]


def acct_type_id(member):
    """Return the integer primary key for an AcctTypeEnum member.

    Args:
        member: An ``AcctTypeEnum`` member (e.g. ``AcctTypeEnum.CHECKING``).

    Returns:
        int -- the ``ref.account_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AcctTypeEnum member.
    """
    require_init()
    return cache().enum_ids[AcctTypeEnum][member]


def acct_category_id(member):
    """Return the integer primary key for an AcctCategoryEnum member.

    Args:
        member: An ``AcctCategoryEnum`` member (e.g. ``AcctCategoryEnum.ASSET``).

    Returns:
        int -- the ``ref.account_type_categories.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AcctCategoryEnum member.
    """
    require_init()
    return cache().enum_ids[AcctCategoryEnum][member]


def acct_category_member(category_id):
    """Return the ``AcctCategoryEnum`` member a category PK names, or ``None``.

    The inverse of :func:`acct_category_id`, and the reverse lookup the
    canonical classifier
    (:func:`app.services.account_category.account_category`) resolves every
    account's category through -- so classifying an account is one dict read
    rather than a scan asking this cache once per enum member.  Added at plan
    step X-z6 (ruling R-CV), where that scan measured 2.3x-4.5x the cost of the
    single comparison it replaced.

    **It answers ``None`` rather than raising, and that is the difference from
    :func:`ledger_class_is_debit_normal` beside it.**  That accessor is
    logic-bearing over a CLOSED set -- every ``budget.ledger_accounts.class_id``
    was written by this application from its own enum, so an unknown one is a
    genuine data error.  This one is read against
    ``ref.account_type_categories``, where ``init`` requires the four
    :class:`~app.enums.AcctCategoryEnum` rows to exist and does NOT forbid
    others; a fifth row is a state the schema permits, and the application's
    answer for it is "no category I model" (bucketed ``other``, not a
    liability), never a 500 on every page that classifies an account.

    Args:
        category_id: The integer primary key of a
            ``ref.account_type_categories`` row.

    Returns:
        The :class:`~app.enums.AcctCategoryEnum` member for *category_id*, or
        ``None`` when no member names it.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return cache().enum_members[AcctCategoryEnum].get(category_id)


def recurrence_unit_id(member):
    """Return the integer primary key for a RecurrenceUnitEnum member.

    The first axis of the two-axis recurrence model (redesign step R2): a
    rule recurs every ``budget.recurrence_rules.interval_n`` units of this
    kind.  Read by the occurrence engine to branch on the cadence unit via
    the integer ID, never the string ``name`` -- the project-wide
    IDs-for-logic invariant.

    Args:
        member: A ``RecurrenceUnitEnum`` member
                (e.g. ``RecurrenceUnitEnum.MONTH``).

    Returns:
        int -- the ``ref.recurrence_units.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RecurrenceUnitEnum member.
    """
    require_init()
    return cache().enum_ids[RecurrenceUnitEnum][member]


def period_placement_id(member):
    """Return the integer primary key for a PeriodPlacementEnum member.

    The rule that carries an occurrence DATE onto the pay PERIOD a Shekel
    row lives in (redesign step R2) -- the axis today's ``Monthly`` and
    ``Monthly First`` patterns differ on (they differ on the anchor day as
    well; see :class:`~app.enums.PeriodPlacementEnum`).  Compared by integer
    ID, never by the string ``name``.

    Args:
        member: A ``PeriodPlacementEnum`` member
                (e.g. ``PeriodPlacementEnum.CONTAINING_DATE``).

    Returns:
        int -- the ``ref.period_placements.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid PeriodPlacementEnum member.
    """
    require_init()
    return cache().enum_ids[PeriodPlacementEnum][member]


def business_day_shift_id(member):
    """Return the integer primary key for a BusinessDayShiftEnum member.

    Whether an occurrence landing on a non-business day moves backward,
    forward, or stays put (redesign step R2 creates the vocabulary; step R8
    is the first step a user can choose a value other than ``none``).
    Compared by integer ID, never by the string ``name``.

    Args:
        member: A ``BusinessDayShiftEnum`` member
                (e.g. ``BusinessDayShiftEnum.PRIOR``).

    Returns:
        int -- the ``ref.business_day_shifts.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid BusinessDayShiftEnum member.
    """
    require_init()
    return cache().enum_ids[BusinessDayShiftEnum][member]


def business_day_shift_member(shift_id):
    """Return the BusinessDayShiftEnum member an id names, or ``None``.

    :func:`business_day_shift_id`'s INVERSE, and the only inverse this module
    carries.  It is here rather than in a domain module because this
    vocabulary is the one two arcs share (``pay_calendar:R-PC47``): the pay
    schedule's payday convention and a recurrence rule's cash-date shift are
    one question of one table, so a second implementation of "which member is
    this id" would be the drift that ruling exists to prevent.  The recurrence
    axes that are NOT shared keep their own lookups in
    ``app.services.recurrence._vocabulary``.

    A linear scan of three members rather than a stored inverse map, for
    ``modelled_unit``'s reason: the value is resolved when a schedule form is
    read or written, never per row of a grid.

    ``None`` rather than a raise, because both askers are reading something a
    user may repair -- a submitted id the form can refuse with a field error,
    and a stored id an owner can re-answer -- rather than asserting an
    invariant.

    Args:
        shift_id: A stored or submitted ``ref.business_day_shifts.id``.

    Returns:
        The matching ``BusinessDayShiftEnum`` member, or ``None`` -- either
        because the id is not a row at all, or because it is a row this
        application does not model.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    ids = cache().enum_ids[BusinessDayShiftEnum]
    for member in BusinessDayShiftEnum:
        if ids[member] == shift_id:
            return member
    return None


def acct_type_icon(type_id):
    """Return the Bootstrap icon class for an account type, or a default.

    Args:
        type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        str -- the ``icon_class`` value, or ``'bi-bank'`` if unset.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    meta = cache().acct_type_meta.get(type_id, {})
    return meta.get("icon_class") or "bi-bank"


def acct_type_max_term(type_id):
    """Return the max term months for an account type, or None if no limit.

    Args:
        type_id: The integer primary key of a ``ref.account_types`` row.

    Returns:
        int or None -- the ``max_term_months`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    meta = cache().acct_type_meta.get(type_id, {})
    return meta.get("max_term_months")


def paycheck_line_kind_id(member):
    """Return the integer primary key for a PaycheckLineKindEnum member."""
    require_init()
    return cache().enum_ids[PaycheckLineKindEnum][member]


def paycheck_line_kind_member(kind_id):
    """Return the PaycheckLineKindEnum member a stored ``paycheck_line_kind_id`` names.

    :func:`paycheck_line_kind_id`'s inverse (plan step salary:R18-b): the
    salary page and the cockpit read a stored line's kind back to word it and
    to tell the deduction side from the earning side, and the schema refuses
    a target account on an earning kind -- IDs for logic, strings for display.

    Args:
        kind_id: The ``ref.paycheck_line_kinds`` primary key.

    Returns:
        The member, or ``None`` for an id the cache does not hold.
    """
    require_init()
    return cache().enum_members[PaycheckLineKindEnum].get(kind_id)


def withholding_kind_id(member):
    """Return the integer primary key for a WithholdingKindEnum member.

    WHICH TAX a transcribed pay stub's withholding figure is (plan step
    **salary:S11-a**, ruling **R-SAL42**), stamped on
    ``salary.pay_stub_withholdings.withholding_kind_id`` -- always via the
    integer ID, never the string ``name``.  What the members mean, and why they
    are a list rather than four columns, is
    :class:`app.enums.WithholdingKindEnum`'s to say.

    Args:
        member: A ``WithholdingKindEnum`` member
                (e.g. ``WithholdingKindEnum.FEDERAL_INCOME``).

    Returns:
        int -- the ``ref.withholding_kinds.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid WithholdingKindEnum member.
    """
    require_init()
    return cache().enum_ids[WithholdingKindEnum][member]


def calc_method_id(member):
    """Return the integer primary key for a CalcMethodEnum member."""
    require_init()
    return cache().enum_ids[CalcMethodEnum][member]


def tax_type_id(member):
    """Return the integer primary key for a TaxTypeEnum member."""
    require_init()
    return cache().enum_ids[TaxTypeEnum][member]


def filing_status_member(filing_status_id):
    """Return the FilingStatusEnum member a stored ``filing_status_id`` names.

    The tax law (:mod:`app.tax_law`, ruling **salary:R-SAL74**) is keyed on
    :class:`~app.enums.FilingStatusEnum` members, and a salary profile stores
    ``salary.salary_profiles.filing_status_id``; this is the one step between
    them, so no reader builds a member out of the ref row's ``name``.

    It answers ``None`` rather than raising, for the reason
    :func:`acct_category_member` gives: ``init`` requires the four member rows
    and does not forbid others, so an id this application does not model is a
    state the schema permits, and the caller decides what that means.

    Args:
        filing_status_id: A ``ref.filing_statuses`` primary key.

    Returns:
        The member, or ``None`` for an id the cache does not hold.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return cache().enum_members[FilingStatusEnum].get(filing_status_id)


def raise_type_id(member):
    """Return the integer primary key for a RaiseTypeEnum member.

    The retirement salary projection (``pension_calculator
    .project_salaries_by_year``, deleted at plan step salary:X-av-3a) used
    this to branch its merit horizon on ``salary.salary_raises
    .raise_type_id`` without ever reading the string ``name`` (Gate A ruling
    3 / fork F4); plan step **salary:S3-c** deleted that branch with the
    setting behind it (ruling **R-SAL11**).  **It has NO production caller
    now** -- only tests -- and an adversarial review of that step named the
    earlier wording here ("what any future reader must go through") as
    exactly the speculative justification CLAUDE.md rule 13 forbids.  It is
    retained rather than deleted because it is one member of a uniform
    per-enum accessor set that ``tests/test_ref_cache.py`` grades as a set;
    deleting one member alone is its own change, and it is named here so the
    next reader does not have to rediscover that it is unused.

    Args:
        member: A ``RaiseTypeEnum`` member (e.g. ``RaiseTypeEnum.COLA``).

    Returns:
        int -- the ``ref.raise_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RaiseTypeEnum member.
    """
    require_init()
    return cache().enum_ids[RaiseTypeEnum][member]


def raise_type_member(type_id):
    """Return the ``RaiseTypeEnum`` member a raise-type PK names, or ``None``.

    The inverse of :func:`raise_type_id`, added at plan step salary:S3-f-1 for
    :attr:`app.models.salary_raise.SalaryRaise.raise_type_name`: the display
    name of a raise's type resolved from the FACT the row carries
    (``raise_type_id``) rather than from the ``raise_type`` relationship,
    which is ``None`` on a row that has never been flushed -- SQLAlchemy does
    not lazy-load a relationship on a pending instance -- and an adversarial
    review of that step measured the X-bl invariance control
    (``tests/manual/verify_amount_resolver.py``) pricing exactly such a row.
    The enum's values match ``ref.raise_types.name`` by construction (see
    :class:`~app.enums.RaiseTypeEnum`), so ``member.value`` IS the name.

    **It answers ``None`` rather than raising**, for the reason
    :func:`acct_category_member` does: ``init`` requires the three enum rows
    to exist and does not forbid others, so a hand-inserted fourth row is a
    state the schema permits, and the caller reads that row's own ``name``.

    Args:
        type_id: The integer primary key of a ``ref.raise_types`` row.

    Returns:
        The :class:`~app.enums.RaiseTypeEnum` member for *type_id*, or ``None``
        when no member names it.

    Raises:
        RuntimeError: If the cache has not been initialized.
    """
    require_init()
    return cache().enum_members[RaiseTypeEnum].get(type_id)


def goal_mode_id(member):
    """Return the integer primary key for a GoalModeEnum member.

    Args:
        member: A ``GoalModeEnum`` member (e.g. ``GoalModeEnum.FIXED``).

    Returns:
        int -- the ``ref.goal_modes.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid GoalModeEnum member.
    """
    require_init()
    return cache().enum_ids[GoalModeEnum][member]


def income_unit_id(member):
    """Return the integer primary key for an IncomeUnitEnum member.

    Args:
        member: An ``IncomeUnitEnum`` member (e.g. ``IncomeUnitEnum.PAYCHECKS``).

    Returns:
        int -- the ``ref.income_units.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid IncomeUnitEnum member.
    """
    require_init()
    return cache().enum_ids[IncomeUnitEnum][member]


def role_id(member):
    """Return the integer primary key for a RoleEnum member.

    Args:
        member: A ``RoleEnum`` member (e.g. ``RoleEnum.OWNER``).

    Returns:
        int -- the ``ref.user_roles.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid RoleEnum member.
    """
    require_init()
    return cache().enum_ids[RoleEnum][member]


def loan_anchor_source_id(member):
    """Return the integer primary key for a LoanAnchorSourceEnum member.

    Used by the loan-anchor-event writer (Commit 12 backfill, Commit 16
    true-up flow) and the loan resolver (Commit 13) to compare against
    ``budget.loan_anchor_events.source_id`` without ever reading the
    string ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``LoanAnchorSourceEnum`` member
                (e.g. ``LoanAnchorSourceEnum.ORIGINATION``).

    Returns:
        int -- the ``ref.loan_anchor_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid LoanAnchorSourceEnum member.
    """
    require_init()
    return cache().enum_ids[LoanAnchorSourceEnum][member]


def account_opening_source_id(member):
    """Return the integer primary key for an AccountOpeningSourceEnum member.

    Used by the opening-equity writer (``account_service.create_account``) and
    by any reader telling a DECLARED opening from a MIGRATION-DERIVED one, to
    compare against ``budget.account_openings.source_id`` without ever reading
    the string ``name``.  Matches the project-wide IDs-for-logic invariant, and
    is the exact twin of :func:`loan_anchor_source_id` one account kind over.

    Args:
        member: An ``AccountOpeningSourceEnum`` member
                (e.g. ``AccountOpeningSourceEnum.USER_DECLARED``).

    Returns:
        int -- the ``ref.account_opening_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AccountOpeningSourceEnum member.
    """
    require_init()
    return cache().enum_ids[AccountOpeningSourceEnum][member]


def employer_contribution_type_id(member):
    """Return the integer primary key for an EmployerContributionTypeEnum member.

    Used by the growth engine and the investment-projection
    employer-params builder (#38) to branch on the employer
    contribution formula via ``budget.investment_params
    .employer_contribution_type_id`` without ever reading the string
    ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: An ``EmployerContributionTypeEnum`` member
                (e.g. ``EmployerContributionTypeEnum.MATCH``).

    Returns:
        int -- the ``ref.employer_contribution_types.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid EmployerContributionTypeEnum member.
    """
    require_init()
    return cache().enum_ids[EmployerContributionTypeEnum][member]


def compounding_frequency_id(member):
    """Return the integer primary key for a CompoundingFrequencyEnum member.

    Used by the interest projection engine (#38) to branch on the
    compounding formula via ``budget.interest_params
    .compounding_frequency_id`` without ever reading the string
    ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``CompoundingFrequencyEnum`` member
                (e.g. ``CompoundingFrequencyEnum.DAILY``).

    Returns:
        int -- the ``ref.compounding_frequencies.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid CompoundingFrequencyEnum member.
    """
    require_init()
    return cache().enum_ids[CompoundingFrequencyEnum][member]


def ledger_account_class_id(member):
    """Return the integer primary key for a LedgerAccountClassEnum member.

    Used by the chart-of-accounts sync hook (Commit 2) to derive a real
    account's ledger class from its account-type category, and by later
    Build-Order steps that read postings to compare against
    ``budget.ledger_accounts.class_id`` without ever reading the string
    ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``LedgerAccountClassEnum`` member
                (e.g. ``LedgerAccountClassEnum.ASSET``).

    Returns:
        int -- the ``ref.ledger_account_classes.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid LedgerAccountClassEnum member.
    """
    require_init()
    return cache().enum_ids[LedgerAccountClassEnum][member]


def ledger_class_is_debit_normal(class_id):
    """Return whether a ledger account class is debit-normal.

    The natural-balance side of a ledger account class, read from the
    cached ``is_debit_normal`` flag by a reader holding a
    ``budget.ledger_accounts.class_id``.  A debit-normal class (Asset,
    Expense) accumulates a balance that increases on a debit; a
    credit-normal class (Liability, Income, Equity) increases on a
    credit, so a reader negates that account's debit-positive posting sum
    to present its natural balance.

    Logic-bearing, so an unknown ``class_id`` raises rather than
    defaulting -- a missing class is a genuine data/seed error, not a
    benign lookup miss (contrast ``acct_type_icon``, a display accessor
    that defaults).

    Args:
        class_id: The integer primary key of a
            ``ref.ledger_account_classes`` row.

    Returns:
        bool -- TRUE if the class is debit-normal (Asset, Expense),
        FALSE if credit-normal (Liability, Income, Equity).

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *class_id* is not a known ledger-account-class PK.
    """
    require_init()
    return cache().ledger_class_debit_normal[class_id]


def ledger_account_kind_id(member):
    """Return the integer primary key for a LedgerAccountKindEnum member.

    Used by the chart-of-accounts resolver and the loan-payment posting
    service (Build-Order Step 4) to stamp and read a ledger account's
    explicit row-kind discriminator via ``budget.ledger_accounts.kind_id``,
    and by chart readers that enumerate by kind -- always via the integer
    ID, never the string ``name``.  Matches the project-wide IDs-for-logic
    invariant.

    Args:
        member: A ``LedgerAccountKindEnum`` member
                (e.g. ``LedgerAccountKindEnum.LINKED``).

    Returns:
        int -- the ``ref.ledger_account_kinds.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid LedgerAccountKindEnum member.
    """
    require_init()
    return cache().enum_ids[LedgerAccountKindEnum][member]


def posting_kind_id(member):
    """Return the integer primary key for a PostingKindEnum member.

    Used by ``posting_service`` (Commit 4) to tag each
    ``budget.account_postings`` leg with its kind, and by later readers
    that branch on the leg kind -- always via the integer ID, never the
    string ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``PostingKindEnum`` member
                (e.g. ``PostingKindEnum.TRANSFER``).

    Returns:
        int -- the ``ref.posting_kinds.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid PostingKindEnum member.
    """
    require_init()
    return cache().enum_ids[PostingKindEnum][member]


def posting_source_id(member):
    """Return the integer primary key for a PostingSourceEnum member.

    Used by ``posting_service`` (Commit 4) to tag each
    ``budget.journal_entries`` row with its source-event kind, and by
    later readers -- always via the integer ID, never the string
    ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``PostingSourceEnum`` member
                (e.g. ``PostingSourceEnum.TRANSFER_MOVEMENT``).

    Returns:
        int -- the ``ref.posting_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid PostingSourceEnum member.
    """
    require_init()
    return cache().enum_ids[PostingSourceEnum][member]


def amount_source_id(member):
    """Return the integer primary key for an AmountSourceEnum member.

    The amount model's discriminator (ruling **R-FI**, plan step X-au-c1): which
    RELATION states a row's amount, stamped on
    ``budget.transactions.amount_source_id`` and
    ``budget.transfers.amount_source_id`` by the writers that stop pricing a row
    and read by the resolver that prices it -- always via the integer ID, never
    the string ``name``.  Matches the project-wide IDs-for-logic invariant.

    **There is deliberately no accessor for the OWN state**, because it is not a
    member: a row that owns its amount carries ``amount_source_id IS NULL``, so
    the question "does this row own its figure" is a NULL test on the column and
    needs no cache read at all.  That is what lets the ownership CHECK be a
    constraint over two NULL-nesses rather than one carrying a frozen id
    literal.

    Args:
        member: An ``AmountSourceEnum`` member
                (e.g. ``AmountSourceEnum.TEMPLATE``).

    Returns:
        int -- the ``ref.amount_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid AmountSourceEnum member.
    """
    require_init()
    return cache().enum_ids[AmountSourceEnum][member]


def statement_source_id(member):
    """Return the integer primary key for a StatementSourceEnum member.

    WHERE a recorded statement line came from (ruling **R-FP**, plan step
    ``bank_import:X-f6a``), stamped on ``budget.statement_imports.source_id``
    and ``budget.account_external_identities.source_id`` by the import door and
    read by the adapter registry -- always via the integer ID, never the string
    ``name``.  Matches the project-wide IDs-for-logic invariant.

    Args:
        member: A ``StatementSourceEnum`` member
                (e.g. ``StatementSourceEnum.SECU_CHECKING_CSV``).

    Returns:
        int -- the ``ref.statement_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid StatementSourceEnum member.
    """
    require_init()
    return cache().enum_ids[StatementSourceEnum][member]


def settled_day_basis_id(member):
    """Return the integer primary key for a SettledDayBasisEnum member.

    The settle DAY's discriminator (plan step **X-az**): HOW a settled row's
    ``settled_on`` is known -- a bank observation, a balance assertion's upper
    bound, or the owner's own entry.  Stamped on
    ``budget.transactions.settled_day_basis_id`` and
    ``budget.transaction_entries.settled_day_basis_id`` by the settle doors and
    read by the statement matcher's window rule
    (``statement_match._subjects.CandidateRow.expected_window``) -- always via the
    integer ID, never the string ``name``.  Matches the project-wide
    IDs-for-logic invariant.

    **There is deliberately no accessor for the NOT-SETTLED state**, for the
    reason :func:`amount_source_id` states about OWN: it is not a member but
    the ABSENCE of one, so "does this row carry a settle day" is a NULL test
    on ``settled_on`` and needs no cache read -- and no ref id is frozen into
    the schema.  The two NULL-nesses are welded by a BICONDITIONAL check
    constraint on each table, so the absence is exact rather than conventional.

    Args:
        member: A ``SettledDayBasisEnum`` member
                (e.g. ``SettledDayBasisEnum.OBSERVED``).

    Returns:
        int -- the ``ref.settled_day_bases.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid SettledDayBasisEnum member.
    """
    require_init()
    return cache().enum_ids[SettledDayBasisEnum][member]


def movement_figure_source_id(member):
    """Return the integer primary key for a MovementFigureSourceEnum member.

    WHO WROTE a movement's FIGURE (plan step **X-bi-3a**, ruling **R-BAL39**):
    the settle pricing it from the plan (``resolved``), a person (``typed``) or
    the bank's own line (``observed``).  Stamped on
    ``budget.transaction_entries.figure_source_id`` by the two writers of a
    movement -- ``entry_service``'s purchase doors and the status seam's
    covering-movement writer -- and read by the seam when a settle is reverted
    or repeated: a ``resolved`` figure is re-priced, a stated one is honoured.
    Always via the integer ID, never the string ``name``.

    There is no accessor for an ABSENT source because there is no absent state:
    the column is NOT NULL with no default, so a movement that states no source
    is unstorable rather than conventionally "typed".

    Args:
        member: A ``MovementFigureSourceEnum`` member
                (e.g. ``MovementFigureSourceEnum.RESOLVED``).

    Returns:
        int -- the ``ref.movement_figure_sources.id`` value.

    Raises:
        RuntimeError: If the cache has not been initialized.
        KeyError: If *member* is not a valid MovementFigureSourceEnum member.
    """
    require_init()
    return cache().enum_ids[MovementFigureSourceEnum][member]


def statement_balance_evidence_member(evidence_id):
    """Return the StatementBalanceEvidenceEnum member for a stored id, or None.

    :func:`statement_balance_evidence_id`'s inverse, and the reason it exists
    is the project's own rule rather than convenience: a reader holding
    ``budget.account_anchor_history.evidence_id`` needs the MEMBER to
    dispatch on, and the only other way to get one is
    ``StatementBalanceEvidenceEnum(<ref row>.name)`` -- constructing
    logic out of a column whose strings are for display.  That is the subtler
    half of the IDs-for-logic rule, the half ``shekel-refname-compare`` cannot
    see because it is a constructor rather than a comparison, and it moves
    reference drift from a ``ValueError`` on every render of the statements
    page to the ``RuntimeError`` at boot where this project makes it fail.

    Args:
        evidence_id: A ``ref.statement_balance_evidence`` primary key.

    Returns:
        The member, or ``None`` when no row carries that id -- the answer
        :func:`acct_category_member` gives for the same shape.
    """
    require_init()
    return cache().enum_members[StatementBalanceEvidenceEnum].get(evidence_id)


def statement_balance_evidence_id(member):
    """Return the integer primary key for a StatementBalanceEvidenceEnum member.

    The level relation's evidence rank (plan step **X-f6e-1**, ruling
    **R-GF**; the column moved to ``budget.account_anchor_history.evidence_id``
    at ``balance:X-bj-1``).  Stamped by the import door for a bank level and
    by the column's own default for an owner's, via the integer ID and never
    the string ``name``.  What the three members MEAN is
    :class:`app.enums.StatementBalanceEvidenceEnum`'s to say and is not
    restated here.
    """
    require_init()
    return cache().enum_ids[StatementBalanceEvidenceEnum][member]
