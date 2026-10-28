"""
Shekel Budget App -- Reference Table Seed Data

Single source of truth for reference-table seed data and the
idempotent seeding function that materialises it.

Two exports:

* ``ACCT_TYPE_SEEDS`` -- the canonical account-type tuple list used
  to populate ``ref.account_types``.
* ``seed_reference_data(session, *, verbose=False)`` -- the idempotent
  upsert routine that populates every ref-schema table to a
  byte-identical state on every call.  Used by the application
  factory's dev/test convenience seed (``app/__init__.py``), the
  deploy (``scripts/init_database.py``, inside entrypoint step 3's one
  transaction), the manual repair script (``scripts/seed_ref_tables.py``),
  the pytest fixture stack (``tests/conftest.py``), and the test
  template builder (``scripts/build_test_template.py``).

Three call sites previously duplicated this logic with subtle
divergence (one used dict-based ``REF_DATA``, another a list-based
``REF_DATA``, the third inlined the table list); a single function
eliminates the drift risk that a future migration adding a new ref
row could be applied in two call sites but not the third.

Each ``ACCT_TYPE_SEEDS`` entry: (name, category_name, has_parameters,
has_amortization, has_interest, is_pretax, is_liquid, has_appreciation,
has_revolving_credit, icon_class, max_term_months)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Typing-only imports: keep this module side-effect free at import
    # time (see the deferred ``app.models.ref`` import inside
    # ``seed_reference_data``).  ``from __future__ import annotations``
    # makes every annotation a lazy string, so neither the SQLAlchemy
    # ORM nor the model layer is imported when ``app.ref_seeds`` loads.
    from types import ModuleType

    from sqlalchemy.orm import Session

# fmt: off
# Pylint: ``line-too-long`` -- columnar alignment is intentional for
# readability: each row is one account type and the columns correspond
# to the tuple docstring above.  Wrapping individual rows harms
# scannability.
# pylint: disable=line-too-long

ACCT_TYPE_SEEDS = [
    # name              category      params amort  interest pretax liquid appr   revolv icon               max_term
    ("Checking",        "Asset",      False, False, False, False, True,  False, False, "bi-wallet2",        None),
    ("Savings",         "Asset",      False, False, False, False, True,  False, False, "bi-piggy-bank",     None),
    ("HYSA",            "Asset",      True,  False, True,  False, True,  False, False, "bi-piggy-bank",     None),
    ("Money Market",    "Asset",      True,  False, True,  False, True,  False, False, "bi-cash-stack",     None),
    ("CD",              "Asset",      True,  False, True,  False, False, False, False, "bi-safe",           None),
    ("HSA",             "Asset",      True,  False, True,  False, False, False, False, "bi-heart-pulse",    None),
    ("Credit Card",     "Liability",  False, False, False, False, False, False, True,  "bi-credit-card",    None),
    ("Mortgage",        "Liability",  True,  True,  False, False, False, False, False, "bi-house",          600),
    ("Auto Loan",       "Liability",  True,  True,  False, False, False, False, False, "bi-car-front",      120),
    ("Student Loan",    "Liability",  True,  True,  False, False, False, False, False, "bi-mortarboard",    300),
    ("Personal Loan",   "Liability",  True,  True,  False, False, False, False, False, "bi-cash-coin",      120),
    ("HELOC",           "Liability",  True,  True,  False, False, False, False, False, "bi-bank",           360),
    ("401(k)",          "Retirement", True,  False, False, True,  False, False, False, "bi-graph-up-arrow", None),
    ("Roth 401(k)",     "Retirement", True,  False, False, False, False, False, False, "bi-graph-up-arrow", None),
    ("Traditional IRA", "Retirement", True,  False, False, True,  False, False, False, "bi-graph-up-arrow", None),
    ("Roth IRA",        "Retirement", True,  False, False, False, False, False, False, "bi-graph-up-arrow", None),
    ("Brokerage",       "Investment", True,  False, False, False, False, False, False, "bi-bar-chart-line", None),
    ("529 Plan",        "Investment", True,  False, False, False, False, False, False, "bi-mortarboard",    None),
    ("Property",        "Asset",      True,  False, False, False, False, True,  False, "bi-houses",         None),
]
# pylint: enable=line-too-long
# fmt: on

# The ``AccountType`` metadata columns each ``ACCT_TYPE_SEEDS`` row carries
# after its (name, category_name) head, IN THE ROW'S ORDER -- the one place
# that order is stated in code, read by ``_seed_account_types``.
_ACCT_TYPE_METADATA_COLUMNS = (
    "has_parameters",
    "has_amortization",
    "has_interest",
    "is_pretax",
    "is_liquid",
    "has_appreciation",
    "has_revolving_credit",
    "icon_class",
    "max_term_months",
)


# Per-table seed data for the non-AccountType ref tables.  Entries are
# either bare strings (used as ``name``) or dicts (full row spec for
# tables with non-name columns such as ``Status``'s booleans).  The
# tuple ordering matches the existing conftest seeding order so that
# any test that asserts on stable ID assignment (none currently do,
# but the property is preserved for future safety) continues to see
# the same IDs.
#
# ``Status`` carries three boolean columns that drive runtime logic
# (settled / immutable / excludes_from_balance); copying the structure
# wholesale from the previous three call sites' dict literals is the
# canonical form -- changing any column here must be matched by a
# database migration.
# fmt: off
# Pylint: ``line-too-long`` -- the ``Status`` rows are columnar-aligned
# dict literals (one row per status, columns aligned to the boolean
# flags) for the same scannability reason as ``ACCT_TYPE_SEEDS`` above;
# wrapping individual rows harms readability.
# pylint: disable=line-too-long
_REF_TABLE_SEEDS = (
    # (model_attr_name, list of entries)
    ("TransactionType", ["Income", "Expense"]),
    ("Status", [
        {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
        {"name": "Paid",      "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
        {"name": "Received",  "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
        {"name": "Credit",    "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
        {"name": "Cancelled", "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
    ]),
    ("FilingStatus", [
        "single", "married_jointly", "married_separately",
        "head_of_household",
    ]),
    # ``PaycheckLineKind`` -- where a payroll line sits in the paycheck's
    # waterfall (ruling R-SAL38), in waterfall order.  ``ref.deduction_
    # timings`` (``pre_tax`` / ``post_tax``) until plan step salary:R18-a,
    # whose migration renamed the table and those two rows; R18-b's
    # migration inline-seeds the two earning kinds (the dual-seed pattern),
    # so a freshly upgraded database resolves the enum before this
    # idempotent reseed runs.
    ("PaycheckLineKind", [
        "taxable_earning", "pre_tax_deduction", "post_tax_deduction",
        "after_tax_earning",
    ]),
    ("CalcMethod", ["flat", "percentage"]),
    ("TaxType", ["flat", "none", "bracket"]),
    ("RaiseType", ["merit", "cola", "custom"]),
    ("GoalMode", ["Fixed", "Income-Relative"]),
    ("IncomeUnit", ["Paychecks", "Months"]),
    ("UserRole", ["owner", "companion"]),
    # ``LoanAnchorSource`` -- the provenance tag carried by every row
    # in ``budget.loan_anchor_events`` (CRIT-02 / E-18 / Commit 12).
    # ``origination`` is materialised once per loan from the immutable
    # LoanParams fields; ``user_trueup`` is appended by the dashboard
    # balance-edit flow (Commit 16); ``tracking_start`` is the
    # mid-life-import opening (its own migration inline-seeds it, the
    # same dual-seed pattern as the posting refs, so a freshly upgraded
    # DB resolves the enum before this idempotent reseed runs).  Same
    # idempotent upsert semantics as the other reference tables.
    ("LoanAnchorSource", ["origination", "user_trueup", "tracking_start"]),
    # ``AccountOpeningSource`` -- the provenance tag carried by every row in
    # ``budget.account_openings`` (plan step X-f3c-2a, ruling R-HE).
    # ``user_declared`` is what ``account_service.create_account`` writes from
    # the balance its owner typed; ``migration_derived`` marks the rows the
    # X-f3c-2a migration computed for accounts that already existed, and two of
    # those are already known wrong (N-275, N-379) -- which is why the split is
    # a financial statement rather than a label.  Seeded HERE as well as in the
    # migration for the reason the twin above is: the create_all path never
    # runs a migration, and ``ref_cache.init()`` treats a missing row in an
    # existing table as fatal.
    ("AccountOpeningSource", ["user_declared", "migration_derived"]),
    # ``EmployerContributionType`` / ``CompoundingFrequency`` (#38) --
    # the two logic-bearing enums promoted off free-string columns to
    # ref tables so the growth/interest engines branch on IDs.  Names
    # match the enum values and the prior column literals exactly, so
    # the promotion migration backfills name->id 1:1.
    ("EmployerContributionType", ["none", "flat_percentage", "match"]),
    ("CompoundingFrequency", ["daily", "monthly", "quarterly"]),
    # Posting-ledger ref tables (Build-Order Step 2, Commit 1; extended in
    # Step 3, Commit 1 and Step 4, Commit 1).  ``LedgerAccountClass`` carries
    # the logic-bearing ``is_debit_normal`` boolean (TRUE for Asset/Expense,
    # FALSE for the credit-normal classes), so its entries are dicts like
    # ``Status`` above; the migration ``f5037400dc5e`` inline-seeds the
    # identical rows so a freshly upgraded DB resolves those enums before
    # this idempotent reseed runs.  ``PostingKind`` / ``PostingSource``
    # seeded only ``transfer`` in Step 2; Step 3 added the ``income`` /
    # ``expense`` kinds and the ``transaction`` source; Step 4 added the
    # ``principal`` / ``interest`` / ``escrow`` / ``refund`` loan-correction
    # kinds and the ``loan_payment`` source; the loan read switch (Step 4,
    # second half) adds the ``opening`` / ``trueup`` kinds and the
    # ``loan_opening`` / ``loan_trueup`` sources (each inline-seeded by its
    # own migration, the same dual-seed pattern); Step 5 adds the
    # ``account_opening`` / ``account_trueup`` sources (the non-loan anchor
    # corrections -- the ``opening`` / ``trueup`` KINDS are reused, the source
    # disambiguates); plan step X-f3b adds the ``purchase`` source (the
    # ``expense`` KIND is reused), and later steps INSERT more.  Plan step
    # ``balance:X-bi-6-5``'s second leaf (card plan#1845, migration
    # ``1712c506ee99``) deleted the ``transfer`` and ``transaction`` SOURCES;
    # the ``transfer`` KIND stays, carried by a transfer movement's legs.
    # ``LedgerAccountKind`` (Step 4) is the explicit row-kind discriminator
    # for ``budget.ledger_accounts``: the four kinds the chart already uses,
    # the three per-loan accounts the loan-payment correction books into, the
    # ``equity_opening`` per-loan Equity account the read switch adds, the
    # ``anchor_equity`` per-NON-loan-account Equity account Step 5 adds, and
    # the ``interest_income`` / ``unrealized_change`` per-account accounts a
    # modelled account's true-up books into (ruling R-FO, plan step X-f3d;
    # migration ``e6b4a2d8c713`` inline-seeds all three of that step's rows).
    # Names match the enum ``.value`` strings in ``app/enums.py`` exactly.
    ("LedgerAccountClass", [
        {"name": "Asset",      "is_debit_normal": True},
        {"name": "Liability",  "is_debit_normal": False},
        {"name": "Income",     "is_debit_normal": False},
        {"name": "Expense",    "is_debit_normal": True},
        {"name": "Equity",     "is_debit_normal": False},
        # Other comprehensive income (ruling R-FO, plan step X-f3d).  A gain is
        # a CREDIT, so credit-normal like Income -- and a class of its OWN so
        # ``net_income = income - expense`` can never count it.
        {"name": "Unrealized", "is_debit_normal": False},
    ]),
    ("PostingKind", [
        "transfer", "income", "expense",
        "principal", "interest", "escrow", "refund",
        "opening", "trueup",
    ]),
    ("PostingSource", [
        "loan_payment",
        "loan_opening", "loan_trueup",
        "account_opening", "account_trueup",
        # A single PURCHASE whose bank posting day the owner recorded (ruling
        # **R-FM**, plan step X-f3b): it links
        # ``journal_entries.transaction_entry_id`` and books its own cash leg
        # on its own day, so its envelope's close books only the remainder.
        # Migration ``b7c3d9e1f204`` inline-seeds it.
        "purchase",
        # ONE side of a settled transfer -- a shadow's covering movement on
        # its own bank day against the owner's Transfers-in-transit account
        # (ruling **R-BAL45**'s shape C, built at plan step
        # ``balance:X-bi-6-3`` under **R-BAL101**); it links
        # ``transaction_entry_id`` exactly as ``purchase`` does.  Migration
        # ``c7d1e9a4b2f8`` inline-seeds it.
        "transfer_movement",
    ]),
    ("LedgerAccountKind", [
        "linked", "category", "fallback", "orphan",
        "loan_interest", "loan_escrow", "loan_refund",
        "equity_opening", "anchor_equity",
        # The two kinds ruling R-FO adds (plan step X-f3d): what a modelled
        # account's TRUE-UP difference WAS.  Both share the ``anchor_equity``
        # column shape and its ``(account_id, kind_id)`` unique.
        "interest_income", "unrealized_change",
        # The owner's Transfers-in-transit clearing account (Asset), the
        # second OWNER-BUCKET kind beside ``fallback`` (ruling **R-BAL99**,
        # plan step ``balance:X-bi-6-3``; migration ``c7d1e9a4b2f8``
        # inline-seeds it).
        "transit",
    ]),
    # Two-axis recurrence vocabulary (recurrence redesign, step R2; plan
    # ``docs/plans/implementation_plan_recurrence_redesign.md``).  A rule
    # recurs every ``interval_n`` ``RecurrenceUnit``s; ``PeriodPlacement``
    # carries the resulting occurrence DATE onto the pay PERIOD a row lives
    # in; ``BusinessDayShift`` is the weekend/holiday adjustment step R8
    # turns on (every rule is seeded at ``none``).  The migration
    # ``e7a4d95c2b18`` inline-seeds the identical rows so a freshly upgraded
    # DB resolves those enums before this idempotent reseed runs -- the same
    # dual-seed pattern the posting refs use.  Names match the enum
    # ``.value`` strings in ``app/enums.py`` exactly.
    ("RecurrenceUnit", ["period", "week", "month", "year"]),
    ("PeriodPlacement", ["containing_date", "period_starting_on_or_after"]),
    ("BusinessDayShift", ["none", "prior", "next"]),
    # The amount model's discriminator (balance arc, plan step X-au-c1; ruling
    # **R-FI**).  WHICH RELATION states a row's amount when the row does not
    # state it itself: ``template`` is the recurring definition that generated
    # it.  A row that OWNS its amount carries ``amount_source_id IS NULL``, so
    # there is deliberately no ``own`` row here -- see
    # :class:`app.enums.AmountSourceEnum` for why the OWN state is an absence.
    # The migration ``b3f7c2a9d514`` inline-seeds this row so a freshly
    # upgraded DB resolves the enum before this idempotent reseed runs -- the
    # same dual-seed pattern the posting and recurrence refs use -- with
    # ``parent_transfer``, the transfer a shadow belonged to, which migration
    # ``c956c1d83108`` deletes (plan step ``balance:X-bi-6-5``).  Names match
    # the enum ``.value`` strings in ``app/enums.py`` exactly.
    ("AmountSource", ["template"]),
    # WHERE a recorded statement line came from (bank-import arc, plan step
    # X-f6a-1; ruling **R-FP**).  One row per source ADAPTER -- a FORMAT at an
    # institution, because one bank publishes one statement several ways and
    # the ways do not carry the same facts.  Migration ``3f408018a71c``
    # inline-seeds the identical row so a freshly upgraded DB resolves the enum
    # before this idempotent reseed runs -- the same dual-seed pattern the
    # posting, recurrence and amount refs use.
    #
    # **This entry is leg 3, and omitting it is invisible to the test suite.**
    # Every test database is migration-built and then reseeded, so a value
    # present in the enum and the migration but missing HERE fails only on the
    # ``create_all`` + ``seed_reference_data`` bootstrap (dev / test first-run,
    # and the production deploy reseed) -- where ``ref_cache.init`` finds the
    # table present and the row absent and raises, refusing to boot.  A dict
    # entry rather than a bare name because this table carries a display label
    # the upload form reads.
    # **The label names the FORMAT and not a column it may not have** (plan
    # step ``bank_import:X-gc``).  It read "CSV with running balance" until
    # then, on the one control that chooses a parser -- while the help text
    # directly beneath it says the column is optional, and while SECU had
    # stopped offering it altogether.  Migration ``a1f4c7e0b839`` updates the
    # row an existing database already carries, because this seeder INSERTS
    # missing rows and leaves present ones alone.
    ("StatementSource", [
        {"name": "secu_checking_csv",
         "display_name": "SECU checking -- CSV export"},
    ]),
    # The settle DAY's discriminator (balance arc, plan step X-az).  HOW a
    # settled row's ``settled_on`` is known: ``observed`` is a day a bank
    # statement showed the money posting on, ``asserted`` is the day the owner
    # asserted a BALANCE for -- an UPPER BOUND on the true posting day, not a
    # point -- ``entered`` is the owner's own word with no bank document behind
    # it, and ``borrowed`` (plan step X-bi-6-4c-3) is a transfer side's day with
    # no evidence of its own: its other side's, or the day Paid was pressed.
    # Migration ``d3b8f5a1c7e2`` inline-seeds that fourth row the same way.
    # A row that carries no settle day carries no basis, so there is
    # deliberately no ``not_settled`` row here; each table's pairing CHECK is a
    # BICONDITIONAL over the two NULL-nesses -- see
    # :class:`app.enums.SettledDayBasisEnum`.  The migration ``c7d31f9a45e8``
    # inline-seeds the identical rows so a freshly upgraded DB resolves the enum
    # before this idempotent reseed runs -- the same dual-seed pattern the
    # settlement-record, amount-model, posting and recurrence refs use.  Names
    # match the enum ``.value`` strings in ``app/enums.py`` exactly.
    ("SettledDayBasis", ["observed", "asserted", "entered", "borrowed"]),
    # WHO WROTE a movement's FIGURE (balance arc, plan step X-bi-3a, ruling
    # R-BAL39): ``resolved`` is the settle pricing it from the plan, ``typed``
    # a person stating it, ``observed`` the bank's own line stating it.  The
    # figure's twin of the settle-day basis above, on the one table that holds
    # movements.  Every movement carries one (NOT NULL, no default), so there is
    # no absent state to leave unseeded.  The migration ``b5c7e9a1d2f4``
    # inline-seeds the identical rows for the same reason its siblings do.
    # Names match the enum ``.value`` strings in ``app/enums.py`` exactly.
    ("MovementFigureSource", ["resolved", "typed", "observed"]),
    # How strongly an imported statement's balance is EVIDENCED (bank_import
    # arc, plan step X-f6e-1, ruling R-GF): ``file_chain`` is a file stating a
    # balance beside every line, so it proves itself; ``corroborated`` is that
    # figure agreeing with a balance the app already holds which is itself
    # evidenced; ``uncorroborated`` is nothing confirming it.  It is the
    # WEAKEST LINK in the chain behind the figure -- a day solved against an
    # uncorroborated opening is uncorroborated -- which is what stops a
    # re-upload of one file from checking an assumption against itself.
    # An import that placed no figure on a day carries no evidence, so there
    # is deliberately no ``unknown`` row here; the pairing CHECK is a
    # BICONDITIONAL over the two NULL-nesses -- see
    # :class:`app.enums.StatementBalanceEvidenceEnum`.  **Their ORDER here
    # carries no meaning**: the ladder is stated once, on the enum, and an
    # early draft that read it off these row ids was measured backwards.  The
    # migration ``4c1f8b7e2a90`` inline-seeds the identical rows so a freshly
    # upgraded DB resolves the enum before this idempotent reseed runs -- the
    # same dual-seed pattern every ref above uses.  Names match the enum
    # ``.value`` strings in ``app/enums.py`` exactly.
    (
        "StatementBalanceEvidence",
        ["file_chain", "corroborated", "uncorroborated"],
    ),
    # WHICH TAX a transcribed pay stub's withholding figure is (salary arc,
    # plan step S11-a, ruling R-SAL42): the four the paycheck engine computes.
    # A tax a stub prints and this list lacks (a city income tax) is a new row
    # and enum member, never a table change -- see
    # :class:`app.enums.WithholdingKindEnum`.  The migration that creates
    # ``ref.withholding_kinds`` inline-seeds the identical rows so a freshly
    # upgraded DB resolves the enum before this idempotent reseed runs -- the
    # same dual-seed pattern every ref above uses.  Names match the enum
    # ``.value`` strings in ``app/enums.py`` exactly.
    (
        "WithholdingKind",
        ["federal_income", "state_income", "social_security", "medicare"],
    ),
)
# pylint: enable=line-too-long
# fmt: on


_ACCT_TYPE_CATEGORY_SEEDS = ("Asset", "Liability", "Retirement", "Investment")


def seed_reference_data(session: Session, *, verbose: bool = False) -> None:
    """Idempotently populate every ref-schema lookup table.

    Runs the three-step seed:

    1. ``ref.account_type_categories`` (4 fixed rows: Asset, Liability,
       Retirement, Investment).  Must precede the AccountType seed
       because each AccountType row carries a category FK.
    2. ``ref.account_types`` from ``ACCT_TYPE_SEEDS`` (19 rows).
       Existing rows have their metadata columns UPDATED in place so
       a column-shape change in a future migration propagates
       correctly on next seed; missing rows are INSERTed.
    3. The non-AccountType ref tables from ``_REF_TABLE_SEEDS``.
       Existing rows are left alone; only missing rows are INSERTed.
       Status carries non-name columns (booleans) -- those entries
       are dicts; everything else is name-only.

    Idempotent by design: calling this twice in a row produces no
    duplicates and (modulo metadata refreshes on existing
    AccountType rows) no changes on the second call.  Safe to run
    against a fresh empty DB or one that already contains a partial
    or complete seed.

    Args:
        session: SQLAlchemy session bound to the target database.
            The function flushes between steps 1 and 2 so the
            category PKs are visible to the AccountType inserts;
            it does NOT commit -- callers own the transaction
            boundary so they can wrap the seed in their own
            commit / rollback contract (the deploy commits it with
            the rest of its one transaction; the repair script
            commits; conftest commits inside ``_seed_ref_tables``;
            the template builder commits after seeding).
        verbose: When True, prints one line per inserted row.  Used
            by the deploy and ``scripts/seed_ref_tables.py`` to give
            the operator an audit trail.  Default False so test
            paths run silently.

    Returns:
        None.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred import:
    # ``app.models.ref`` imports ``app.extensions`` which constructs the
    # SQLAlchemy() singleton -- importing at module load would force
    # ``app`` initialisation as a side-effect of ``import
    # app.ref_seeds``, which the test bootstrap (which needs to set
    # environment variables before app import) cannot tolerate.  The
    # deferred import keeps this module side-effect free at import time.
    # ``ref_models`` is threaded into the per-step helpers so they
    # inherit the same deferral (no module-level import).
    # pylint: disable=import-outside-toplevel
    from app.models import ref as ref_models

    _seed_account_type_categories(session, ref_models, verbose=verbose)
    # Flush so the category PKs are visible to the AccountType FK in
    # step 2.  Without this, those INSERTs would either fail with NOT
    # NULL on ``category_id`` or pick up stale IDs from a prior session.
    session.flush()
    _seed_account_types(session, ref_models, verbose=verbose)
    _seed_other_ref_tables(session, ref_models, verbose=verbose)


def _seed_account_type_categories(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Insert the 4 fixed ``AccountTypeCategory`` rows (idempotent).

    Asset / Liability / Retirement / Investment.  Existing rows are
    left untouched; only missing rows are INSERTed.  The caller must
    ``flush`` after this step so the category PKs are visible to the
    AccountType FK in :func:`_seed_account_types`.

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module (passed in to keep
            the deferred-import discipline of the public entry point).
        verbose: When True, prints one line per inserted row.
    """
    for cat_name in _ACCT_TYPE_CATEGORY_SEEDS:
        existing = (
            session.query(ref_models.AccountTypeCategory)
            .filter_by(name=cat_name)
            .first()
        )
        if existing is None:
            session.add(ref_models.AccountTypeCategory(name=cat_name))
            if verbose:
                print(f"  + account_type_categories: {cat_name}")


def _seed_account_types(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Upsert the 19 ``AccountType`` rows from ``ACCT_TYPE_SEEDS``.

    Missing rows are INSERTed; existing rows have their metadata
    columns refreshed in place so a column-shape change in a future
    migration propagates correctly on the next seed (the canonical
    behaviour shared by the conftest, ``app/__init__.py``, deploy and
    ``scripts/seed_ref_tables.py`` seed paths).  Requires the
    ``AccountTypeCategory`` rows to already be flushed -- their PKs back
    the ``category_id`` FK.

    The metadata columns are named ONCE, in :data:`_ACCT_TYPE_METADATA_COLUMNS`,
    and zipped against each row's trailing fields, so the INSERT arm and the
    refresh arm cannot disagree about which columns a seed row carries (they
    were two hand-written lists of the same names until plan step
    credit_card:CC-1 added ``has_revolving_credit``).  ``strict=True`` makes a
    row with a missing or extra field fail here, loudly, rather than seed a
    column with its neighbour's value.  ``category_id`` is written on INSERT
    only: an existing row keeps its category, as it always has.

    **The lookup is scoped to the BUILT-IN row** (``user_id IS NULL``), which
    is what the partial index ``uq_account_types_seeded_name`` keys.  An owner
    may name a custom type after a seed ("HYSA" is the model docstring's own
    example), and until plan step credit_card:CC-1 this looked the row up by
    name alone with no ORDER BY -- so a reseed could find the OWNER's row first
    and overwrite its flags with the seed's.  With ``has_revolving_credit``
    gating every card feature that would have made a user's "Credit Card" type
    revolving at the next container start; the seed now writes only the row it
    owns (the CC-1 adversarial review's finding 2).

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module.
        verbose: When True, prints one line per inserted row.
    """
    cat_lookup = {
        c.name: c.id
        for c in session.query(ref_models.AccountTypeCategory).all()
    }
    for name, cat_name, *metadata in ACCT_TYPE_SEEDS:
        values = dict(zip(_ACCT_TYPE_METADATA_COLUMNS, metadata, strict=True))
        existing = (
            session.query(ref_models.AccountType)
            .filter_by(name=name, user_id=None)
            .one_or_none()
        )
        if existing is None:
            session.add(ref_models.AccountType(
                name=name,
                category_id=cat_lookup[cat_name],
                **values,
            ))
            if verbose:
                print(f"  + account_types: {name}")
        else:
            for column, value in values.items():
                setattr(existing, column, value)


def _seed_other_ref_tables(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Insert any missing rows in the non-AccountType ref tables.

    Driven by ``_REF_TABLE_SEEDS``.  Existing rows are left untouched, so
    there is no in-place metadata refresh as in step 2.  Dict entries
    carry the non-name columns -- ``Status``'s three migration-managed
    runtime booleans, and ``StatementSource``'s ``display_name``, which
    the statement upload form renders; every other entry is name-only.

    **The consequence of "left untouched" is a real seam and it is
    graded rather than trusted**: editing a non-name value here changes
    what a FRESH bootstrap says and NOTHING about a database that
    already holds the row, so the two halves are changed together (the
    dual-seed pattern -- a migration for the databases that exist, this
    file for the ones yet to be born) and
    ``tests/test_services/test_statement_import/test_reads.py::
    test_the_SEEDER_and_the_DATABASE_agree_about_that_label`` compares
    them.  Migration ``a1f4c7e0b839`` is the worked example.

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module.
        verbose: When True, prints one line per inserted row.
    """
    for model_attr_name, entries in _REF_TABLE_SEEDS:
        model = getattr(ref_models, model_attr_name)
        for entry in entries:
            if isinstance(entry, dict):
                row_name = entry["name"]
                existing = session.query(model).filter_by(name=row_name).first()
                if existing is None:
                    session.add(model(**entry))
                    if verbose:
                        print(f"  + {model.__tablename__}: {row_name}")
            else:
                existing = session.query(model).filter_by(name=entry).first()
                if existing is None:
                    session.add(model(name=entry))
                    if verbose:
                        print(f"  + {model.__tablename__}: {entry}")
