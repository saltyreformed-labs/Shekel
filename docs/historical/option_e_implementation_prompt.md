> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Option E Implementation: Unified Loan Parameters Architecture

## Preamble -- Read This First

You are implementing a major architectural refactor of the Shekel budget app's
loan parameter system. This document is the result of an extensive
architectural investigation that evaluated five options (A through E) for
handling loan parameters. Option E was selected as the best approach for
correctness, scalability, and maintainability.

**Do not skip any section of this prompt.** Every instruction exists for a
reason. Every file listed for reading must be read in full before any code is
written. Every constraint must be honored. This is a financial application
where bugs have real-world consequences -- missed bill payments, wrong
projections, and financial decisions made on bad data. The developer is a solo
operator with no QA team. You are the only safeguard between this code and
production.

**No shortcuts. No workarounds. No "good enough for now." Do it the right way
the first time.**

---

## Context -- What Option E Is and Why

### The Problem

The current architecture creates a separate database table, SQLAlchemy model,
route file, template directory, Marshmallow schema pair, and test file for
each loan type. Today that means `auto_loan_params` and `mortgage_params` as
separate tables with nearly identical columns, `auto_loan.py` and
`mortgage.py` as separate route files with ~70% identical logic, and separate
template directories with copy-pasted HTML.

Two loan types (Student Loan, Personal Loan) are seeded in `ref.account_types`
with `has_parameters=True` and `has_amortization=True` but are dead ends -- no
model, no routes, no templates exist for them. HELOC is flagged
`has_amortization=True` but has no params table at all. Adding any new loan
type under the current architecture requires creating 5+ new files that are
nearly identical copies of existing ones.

This violates DRY (massive code duplication), the Open/Closed Principle (every
new type requires modifying multiple files), and the project's own design
principle that "new types [are] added via INSERT, not schema migration."

### The Solution -- Option E

**One parameter table per calculation engine, not per account type.**

The amortization engine is already loan-type-agnostic -- it takes raw scalars
(`principal`, `rate`, `term`) and returns a schedule. It has zero awareness of
what type of loan it's computing. The balance calculator accesses only three
attributes on a loan params object (`.interest_rate`, `.term_months`,
`.current_principal`). The architecture should match this reality.

Option E creates a single `budget.loan_params` table that serves the
amortization engine for ALL installment loan types. One model, one route file,
one template set, one schema pair. Mortgage-specific features (escrow, ARM rate
history) are account-level concerns that already FK to `account_id`, not to any
params table -- they work identically regardless of where the loan params live.

### Key Architectural Decisions (Already Made -- Do Not Revisit)

These decisions were made during the investigation phase after reading all
project documentation and all relevant code. They are final.

1. **One `budget.loan_params` table** replaces both `budget.auto_loan_params`
   and `budget.mortgage_params`. The table includes the three ARM columns
   (`is_arm`, `arm_first_adjustment_months`, `arm_adjustment_interval_months`)
   as nullable fields. These cost nothing when unused (PostgreSQL NULL bitmap
   storage) and eliminate the need for a separate mortgage extensions table.

2. **`budget.mortgage_rate_history` is renamed to `budget.rate_history`.** The
   table FKs to `account_id`, not to any params table. Variable-rate tracking
   is not inherently mortgage-specific -- a HELOC or future ARM product could
   use the same table.

3. **`budget.escrow_components` is unchanged.** It already FKs to `account_id`
   and its name describes what it stores, not what loan type it serves.

4. **Feature availability is data-driven, not type-driven.** The escrow tab
   shows if the account has escrow components (or if the user adds one). The
   rate history tab shows if `params.is_arm` is True. The payoff calculator is
   available to all loan types. No type-checking conditionals like
   "if this is a mortgage, show escrow." Any loan account can have these
   features if the data supports it.

5. **`icon_class` and `max_term_months` are added to `ref.account_types`** so
   that display and validation configuration is data-driven. Adding a new loan
   type with a custom icon and term limit is a database INSERT, not a code
   change.

6. **HELOC's `has_parameters` flag changes from False to True.** It was False
   only because no params table existed for it. With `loan_params`, it has a
   table.

7. **The amortization engine, balance calculator, and escrow calculator are
   NOT modified.** They are already correct and type-agnostic. The refactor
   changes only the plumbing layers (models, routes, templates, schemas) that
   sit between the user and the engines.

8. **All existing mortgage and auto loan functionality must survive.** Escrow
   CRUD, ARM rate history, payoff calculator (both modes), balance charts,
   parameter editing -- all features must work through the new unified
   routes and templates. Nothing is dropped.

---

## Phase 0 -- Read Everything Before Writing Anything

**This phase is mandatory. Do not write any code until every file listed below
has been read in full and you have confirmed you understand the current
implementation.**

### Project Documentation (read in this order)

1. **`CLAUDE.md`** -- Project rules, architecture, patterns, quality standards.
   This is the law. Everything you write must comply.

2. **`docs/project_requirements_v2.md`** -- Original requirements. Focus on
   data model principles (3NF, referential integrity, ref tables for enums),
   PostgreSQL schema organization, and the `ref.account_types` design.

3. **`docs/project_requirements_v3_addendum.md`** -- Extended requirements.
   Focus on how parameter tables attach to accounts, the amortization engine
   service definition (note it is described as "loan-type-agnostic"), and the
   project structure showing per-type files.

4. **`docs/project_roadmap_v4-1.md`** -- Current roadmap. Focus on Section 5
   scope, the `ref.account_types.category` prerequisite, the `has_parameters`
   and `has_amortization` discussion, and the stated goal of "a single source
   of truth for which account types support payoff calculations."

5. **`docs/implementation_plan_section5.md`** -- The per-type plan that this
   refactor replaces. Read it to understand what was planned, but **do not
   implement it**. Option E supersedes this plan entirely.

### Application Code (read every file listed, in full)

**Models -- understand the current table structures:**
- `app/models/auto_loan_params.py` -- The model being replaced. Note every
  column, relationship, constraint, and backref name.
- `app/models/mortgage_params.py` -- The model being replaced. Note every
  column (especially the three ARM columns), plus `MortgageRateHistory` and
  `EscrowComponent` models defined in this file.
- `app/models/account.py` -- The Account model. Understand how it relates to
  params via backrefs (`auto_loan_params`, `mortgage_params`, `rate_history`,
  `escrow_components`). These backref names will change.
- `app/models/ref.py` -- The `AccountType` and `AccountTypeCategory` models.
  Understand `has_parameters`, `has_amortization`, `category_id`.
- `app/models/__init__.py` -- How models are registered. You'll need to update
  imports here.

**Routes -- understand every code path:**
- `app/routes/auto_loan.py` -- Read every function. Note:
  - `_load_auto_loan_account()` helper pattern
  - `_build_chart_data()` helper (identical to mortgage's version)
  - `dashboard()` -- how it calls amortization_engine, what it passes to template
  - `create_params()` -- validation flow, percentage conversion, model creation
  - `update_params()` -- which fields are updatable, the `_PARAM_FIELDS` set
- `app/routes/mortgage.py` -- Read every function. Note:
  - `_load_mortgage_account()` helper pattern (identical structure to auto loan)
  - `_build_chart_data()` helper (identical to auto loan)
  - `_compute_total_payment()` helper (escrow integration)
  - `dashboard()` -- additional escrow and rate history loading
  - `create_params()` -- same pattern as auto loan
  - `update_params()` -- different `_PARAM_FIELDS` set (includes ARM fields)
  - `add_rate_change()` -- HTMX endpoint for ARM rate history
  - `add_escrow()` / `delete_escrow()` -- HTMX endpoints for escrow CRUD
  - `payoff_calculate()` -- HTMX endpoint with two modes (extra_payment, target_date)
- `app/routes/accounts.py` -- Read the `create_account()` function. Trace the
  entire redirect chain. Note the comment about Student Loan / Personal Loan
  falling through. Find every reference to `auto_loan` or `mortgage` blueprints.
- `app/routes/savings.py` -- Read the dashboard function. Find where it loads
  `loan_params_map` with separate queries for `MortgageParams` and
  `AutoLoanParams`. Note how the map is keyed by `account_id`.
- `app/routes/settings.py` -- Read the account type CRUD. Note that users can
  only set `name` -- `category_id` is hardcoded to ASSET, `has_parameters` and
  `has_amortization` are not exposed.

**Services -- confirm they are type-agnostic (DO NOT MODIFY THESE):**
- `app/services/amortization_engine.py` -- Read every function signature. Confirm
  that no function takes a params object -- they all take raw scalars. Confirm
  there is zero type-specific branching. Note the `AmortizationRow` and
  `AmortizationSummary` dataclasses.
- `app/services/balance_calculator.py` -- Read `calculate_balances_with_amortization()`.
  Confirm it accesses only `.interest_rate`, `.term_months`, `.current_principal`
  on the `loan_params` object. Confirm there is zero type-specific branching.
- `app/services/escrow_calculator.py` -- Read every function. Confirm it operates
  on `EscrowComponent` objects and has no awareness of loan type.

**Schemas -- understand current validation:**
- `app/schemas/validation.py` -- Find and read:
  - `AutoLoanParamsCreateSchema` and `AutoLoanParamsUpdateSchema`
  - `MortgageParamsCreateSchema` and `MortgageParamsUpdateSchema`
  - `PayoffCalculatorSchema`
  - `EscrowComponentSchema`
  - `MortgageRateChangeSchema`
  - `AccountTypeCreateSchema` and `AccountTypeUpdateSchema`
  Note the differences: `interest_rate` decimal places (3 vs 5),
  `term_months` max (120 vs 600), ARM fields on mortgage only.

**Templates -- understand current UI:**
- `app/templates/auto_loan/dashboard.html` -- Every section, every variable,
  every form field, every script include.
- `app/templates/auto_loan/setup.html` -- Every form field, validation attrs.
- `app/templates/mortgage/dashboard.html` -- Every tab, every section, every
  HTMX endpoint, every conditional block.
- `app/templates/mortgage/setup.html` -- Every form field including `is_arm`.
- `app/templates/mortgage/_escrow_list.html` -- HTMX partial, escrow CRUD.
- `app/templates/mortgage/_rate_history.html` -- HTMX partial, rate history.
- `app/templates/mortgage/_payoff_results.html` -- HTMX partial, both modes.

**Enums and ref cache:**
- `app/enums.py` -- `AcctTypeEnum` members, `AcctCategoryEnum` members.
- `app/ref_cache.py` -- How enum members map to database IDs at startup.

**Seed script:**
- `scripts/seed_ref_tables.py` -- The `ACCT_TYPE_SEEDS` list. Note every
  account type's `(name, category, has_parameters, has_amortization)` tuple.

**Tests -- understand current coverage:**
- `tests/test_routes/test_auto_loan.py` -- Every test method, what it verifies.
- `tests/test_routes/test_mortgage.py` -- Every test method, what it verifies.
- `tests/conftest.py` -- Available fixtures, especially account and user setup.
- `tests/test_services/test_amortization_engine.py` -- Confirm these tests do
  not reference any params model directly.
- `tests/test_services/test_balance_calculator.py` -- Check if it references
  `AutoLoanParams` or `MortgageParams` directly.

**Application factory and blueprint registration:**
- `app/__init__.py` -- Find where `auto_loan_bp` and `mortgage_bp` are
  imported and registered. You'll need to replace these with `loan_bp`.

**Static assets:**
- `app/static/js/payoff_chart.js` -- Read to understand how chart data is
  consumed. Confirm it has no type-specific logic.
- `app/static/js/chart_slider.js` -- Read to understand the slider sync
  mechanism used by the payoff calculator.
- `app/static/js/chart_theme.js` -- Read for context.

### After Reading -- Verify Understanding

Before proceeding to Phase 1, confirm the following by checking the actual
code (do not rely on this document's assertions):

- [ ] `AutoLoanParams` columns are an exact subset of `MortgageParams` columns
- [ ] The amortization engine has zero type-specific branching
- [ ] The balance calculator accesses only `.interest_rate`, `.term_months`,
      `.current_principal` on loan params
- [ ] `EscrowComponent` and `MortgageRateHistory` FK to `account_id`, not to
      `mortgage_params.id`
- [ ] `_build_chart_data()` is character-for-character identical in both route files
- [ ] The payoff calculator logic in `mortgage.py` has no mortgage-specific
      assumptions (it uses the generic amortization engine)
- [ ] The account creation redirect chain in `accounts.py` has hardcoded
      type-specific if/elif branches
- [ ] `savings.py` has separate per-type queries for loading loan params

If any of these assertions are wrong, stop and reassess before proceeding.
The architecture assumes these are true.

---

## Phase 1 -- Database Migration

**Create a single Alembic migration** with a descriptive message. The migration
must be reversible (implement both `upgrade()` and `downgrade()`).

### Step 1: Add columns to `ref.account_types`

Add two nullable columns:
- `icon_class` VARCHAR(30), DEFAULT NULL
- `max_term_months` INTEGER, DEFAULT NULL

Then backfill with UPDATE statements:

| Account Type | icon_class | max_term_months |
|---|---|---|
| Checking | `bi-wallet2` | NULL |
| Savings | `bi-piggy-bank` | NULL |
| HYSA | `bi-piggy-bank` | NULL |
| Money Market | `bi-cash-stack` | NULL |
| CD | `bi-safe` | NULL |
| HSA | `bi-heart-pulse` | NULL |
| Credit Card | `bi-credit-card` | NULL |
| Mortgage | `bi-house` | 600 |
| Auto Loan | `bi-car-front` | 120 |
| Student Loan | `bi-mortarboard` | 300 |
| Personal Loan | `bi-cash-coin` | 120 |
| HELOC | `bi-bank` | 360 |
| 401(k) | `bi-graph-up-arrow` | NULL |
| Roth 401(k) | `bi-graph-up-arrow` | NULL |
| Traditional IRA | `bi-graph-up-arrow` | NULL |
| Roth IRA | `bi-graph-up-arrow` | NULL |
| Brokerage | `bi-bar-chart-line` | NULL |
| 529 Plan | `bi-mortarboard` | NULL |

Update HELOC's `has_parameters` from FALSE to TRUE.

### Step 2: Create `budget.loan_params`

```sql
CREATE TABLE budget.loan_params (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL UNIQUE
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    original_principal NUMERIC(12, 2) NOT NULL,
    current_principal NUMERIC(12, 2) NOT NULL,
    interest_rate NUMERIC(7, 5) NOT NULL,
    term_months INTEGER NOT NULL,
    origination_date DATE NOT NULL,
    payment_day INTEGER NOT NULL
        CHECK (payment_day >= 1 AND payment_day <= 31),
    is_arm BOOLEAN NOT NULL DEFAULT FALSE,
    arm_first_adjustment_months INTEGER,
    arm_adjustment_interval_months INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Step 3: Create `budget.rate_history`

```sql
CREATE TABLE budget.rate_history (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL
        REFERENCES budget.accounts(id) ON DELETE CASCADE,
    effective_date DATE NOT NULL,
    interest_rate NUMERIC(7, 5) NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Step 4: Migrate data

```sql
-- Migrate auto_loan_params → loan_params
INSERT INTO budget.loan_params (
    account_id, original_principal, current_principal, interest_rate,
    term_months, origination_date, payment_day,
    is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
    created_at, updated_at
)
SELECT
    account_id, original_principal, current_principal, interest_rate,
    term_months, origination_date, payment_day,
    FALSE, NULL, NULL,
    created_at, updated_at
FROM budget.auto_loan_params;

-- Migrate mortgage_params → loan_params
INSERT INTO budget.loan_params (
    account_id, original_principal, current_principal, interest_rate,
    term_months, origination_date, payment_day,
    is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
    created_at, updated_at
)
SELECT
    account_id, original_principal, current_principal, interest_rate,
    term_months, origination_date, payment_day,
    is_arm, arm_first_adjustment_months, arm_adjustment_interval_months,
    created_at, updated_at
FROM budget.mortgage_params;

-- Migrate mortgage_rate_history → rate_history
INSERT INTO budget.rate_history (
    account_id, effective_date, interest_rate, notes, created_at
)
SELECT
    account_id, effective_date, interest_rate, notes, created_at
FROM budget.mortgage_rate_history;
```

### Step 5: Drop old tables

```sql
DROP TABLE budget.auto_loan_params;
DROP TABLE budget.mortgage_rate_history;
DROP TABLE budget.mortgage_params;
```

**Important:** The `downgrade()` function must reverse all of this -- recreate
the old tables, migrate data back, drop the new tables, remove the new columns,
and revert the HELOC flag change. Even if we never use downgrade, it must be
correct.

### Step 6: Update the seed script

Update `scripts/seed_ref_tables.py`:
- Change HELOC tuple from `("HELOC", "Liability", False, True)` to
  `("HELOC", "Liability", True, True)`.
- Add `icon_class` and `max_term_months` to the seed data structure and
  seeding logic. Use the values from the table above.
- The seed script uses an upsert-on-name pattern. Ensure the new columns are
  included in both the insert and the "already exists" path. If a row already
  exists, the seed script should update `icon_class` and `max_term_months` to
  the seed values (these are reference data, not user data).

---

## Phase 2 -- Models

### Create `app/models/loan_params.py`

**The `LoanParams` model:**
- `__tablename__ = "loan_params"`
- `__table_args__` with CheckConstraint on `payment_day` and `{"schema": "budget"}`
- Columns matching the migration exactly (id, account_id with FK + unique,
  original_principal, current_principal, interest_rate, term_months,
  origination_date, payment_day, is_arm with server_default false,
  arm_first_adjustment_months nullable, arm_adjustment_interval_months
  nullable, created_at, updated_at)
- Relationship: `account = db.relationship("Account", backref=db.backref("loan_params", uselist=False, lazy="joined"))`
- `__repr__` showing account_id, interest_rate, term_months

Follow the exact patterns established by the existing models (see
`auto_loan_params.py` and `mortgage_params.py` for style). Use the same
import pattern, docstring style, and column definition style.

### Create `app/models/loan_features.py`

Move `EscrowComponent` from `mortgage_params.py` into this file. The model
code is unchanged except:
- The module docstring should reflect that these are loan account features,
  not mortgage-specific models.

Create the `RateHistory` model (replacing `MortgageRateHistory`):
- `__tablename__ = "rate_history"`
- `__table_args__ = {"schema": "budget"}`
- Same columns as current `MortgageRateHistory`
- Relationship: `account = db.relationship("Account", backref=db.backref("rate_history", lazy="select"))`
- `__repr__` showing account_id, effective_date, interest_rate

**Important backref changes:** The current `MortgageRateHistory` uses
`backref="rate_history"` on the Account model. The new `RateHistory` should
use the same backref name so that existing code accessing
`account.rate_history` continues to work. Similarly, `EscrowComponent` uses
`backref="escrow_components"` -- keep this unchanged.

The new `LoanParams` uses `backref="loan_params"`. This is a NEW backref name.
Code that currently accesses `account.auto_loan_params` or
`account.mortgage_params` must be updated to `account.loan_params`.

### Update `app/models/ref.py`

Add the two new columns to the `AccountType` model:
- `icon_class = db.Column(db.String(30), nullable=True)`
- `max_term_months = db.Column(db.Integer, nullable=True)`

Update the class docstring to document these new columns.

### Update `app/models/__init__.py`

- Remove imports of `AutoLoanParams`, `MortgageParams`, `MortgageRateHistory`
- Add imports of `LoanParams`, `RateHistory`
- Keep `EscrowComponent` import (update source to `loan_features`)
- Verify no other model files import from the deleted modules

### Delete old model files

- Delete `app/models/auto_loan_params.py`
- Delete `app/models/mortgage_params.py`

### Grep for stale references

After making these changes, grep the entire codebase for:
- `AutoLoanParams` -- must appear nowhere except migration files
- `MortgageParams` -- must appear nowhere except migration files
- `MortgageRateHistory` -- must appear nowhere except migration files
- `auto_loan_params` (as a string/backref) -- must appear nowhere except
  migration files
- `mortgage_params` (as a string/backref) -- must appear nowhere except
  migration files

Fix every reference found.

---

## Phase 3 -- Schemas

### Update `app/schemas/validation.py`

**Delete these schema classes:**
- `AutoLoanParamsCreateSchema`
- `AutoLoanParamsUpdateSchema`
- `MortgageParamsCreateSchema`
- `MortgageParamsUpdateSchema`
- `MortgageRateChangeSchema`

**Create these schema classes:**

`LoanParamsCreateSchema`:
- All fields from the loan_params table that a user provides during setup
- `original_principal` -- Decimal, required, places=2, as_string=True, min=0
- `current_principal` -- Decimal, required, places=2, as_string=True, min=0
- `interest_rate` -- Decimal, required, places=5, as_string=True, min=0, max=100
  (use 5 decimal places for all types -- higher precision costs nothing)
- `term_months` -- Integer, required, min=1, max=600 (universal max; type-specific
  max enforced by the route using `ref.account_types.max_term_months`)
- `origination_date` -- Date, required
- `payment_day` -- Integer, required, min=1, max=31
- `is_arm` -- Boolean, load_default=False
- `arm_first_adjustment_months` -- Integer, allow_none=True
- `arm_adjustment_interval_months` -- Integer, allow_none=True
- Include the `strip_empty_strings` pre_load method from existing schemas

`LoanParamsUpdateSchema`:
- Same fields as create but none required (partial update pattern)
- Omit `original_principal` and `origination_date` (these are not updatable
  after initial setup -- same as current behavior)
- Include `term_months` as updatable (it is in auto loan but missing from
  mortgage update -- include it for all types as there's no reason to prevent
  term correction)
- Include the `strip_empty_strings` pre_load method

`RateChangeSchema` (renamed from `MortgageRateChangeSchema`):
- Same fields as the current schema. Only the class name changes.
- `effective_date` -- Date, required
- `interest_rate` -- Decimal, required, places=5, as_string=True, min=0, max=100
- `notes` -- String, allow_none=True

**Keep unchanged:**
- `PayoffCalculatorSchema`
- `EscrowComponentSchema`
- `AccountTypeCreateSchema`
- `AccountTypeUpdateSchema`

---

## Phase 4 -- Ref Cache

### Update `app/ref_cache.py`

Add a module-level cache for account type metadata (icon_class,
max_term_months) and a helper to query it.

In `init()`, after loading the existing maps, build an account-type metadata
cache:

```python
_acct_type_meta = {}  # int (acct_type PK) -> dict with icon_class, max_term_months
```

Populated by querying `AccountType` rows and storing their `icon_class` and
`max_term_months` values.

Add accessor functions:

```python
def acct_type_icon(acct_type_id: int) -> str:
    """Return the Bootstrap icon class for an account type, or a default."""

def acct_type_max_term(acct_type_id: int) -> int | None:
    """Return the max term months for an account type, or None if no limit."""
```

These follow the same pattern as existing `status_id()`, `acct_type_id()`,
etc. -- fail loudly if the cache is not initialized.

---

## Phase 5 -- Routes

### Create `app/routes/loan.py`

This is the largest single file to create. It consolidates all logic from
`auto_loan.py` and `mortgage.py` into a single blueprint.

**Blueprint:** `loan_bp = Blueprint("loan", __name__)`

**Schema instances:**
- `_create_schema = LoanParamsCreateSchema()`
- `_update_schema = LoanParamsUpdateSchema()`
- `_rate_schema = RateChangeSchema()`
- `_escrow_schema = EscrowComponentSchema()`
- `_payoff_schema = PayoffCalculatorSchema()`

**Private helpers:**

`_load_loan_account(account_id)`:
- Load account by ID from database
- Verify `current_user.id` ownership (same 404 pattern as existing helpers)
- Verify account type has `has_amortization=True` by loading the `AccountType`
  and checking the flag. Do NOT compare against specific enum IDs. This is the
  key Open/Closed improvement -- any type with `has_amortization=True` is valid.
- Load `LoanParams` by `account_id`
- Return `(account, params, account_type)` or `(None, None, None)` if invalid

`_build_chart_data(schedule)`:
- Identical to current implementation (same in both existing route files)
- Returns `(labels, balances)` tuple

`_compute_total_payment(params, escrow_components)`:
- Moved from mortgage.py
- Computes P&I + escrow total. Returns None if params is None.

**Routes:**

`GET /accounts/<int:account_id>/loan` -- `dashboard`:
- Load account, params, account_type via `_load_loan_account()`
- If not found, flash error and redirect to `savings.dashboard`
- If params is None, render `loan/setup.html` with account and account_type
- Calculate remaining_months, summary, schedule via amortization_engine
- Load escrow_components for this account (query by account_id, filter active)
- Calculate monthly_escrow and total_payment if escrow components exist
- Load rate_history if `params.is_arm` is True
- Build chart data
- Get icon_class from account_type (with fallback default)
- Render `loan/dashboard.html` with all data

`POST /accounts/<int:account_id>/loan/setup` -- `create_params`:
- Load account, verify ownership, verify has_amortization=True
- Check for existing LoanParams (prevent duplicates)
- Validate against `_create_schema`
- **Type-specific validation:** After schema validation, check
  `account_type.max_term_months`. If set and `term_months` exceeds it, flash
  error and re-render setup. This is business rule enforcement beyond schema
  structural validation.
- Convert interest_rate from percentage to decimal (divide by 100)
- Create `LoanParams(account_id=account.id, **data)`
- Commit, log, flash success, redirect to `loan.dashboard`

`POST /accounts/<int:account_id>/loan/params` -- `update_params`:
- Load account + params via `_load_loan_account()`
- Validate against `_update_schema`
- **Type-specific validation:** Same max_term_months check as create
- Convert interest_rate from percentage to decimal
- Update fields on params object. The updatable field set:
  `{"current_principal", "interest_rate", "payment_day", "term_months",
  "is_arm", "arm_first_adjustment_months", "arm_adjustment_interval_months"}`
- Commit, log, flash success, redirect to `loan.dashboard`

`POST /accounts/<int:account_id>/loan/rate` -- `add_rate_change`:
- HTMX endpoint. Same logic as current `mortgage.add_rate_change()`.
- Uses `RateHistory` model instead of `MortgageRateHistory`.
- Renders `loan/_rate_history.html` partial.

`POST /accounts/<int:account_id>/loan/escrow` -- `add_escrow`:
- HTMX endpoint. Same logic as current `mortgage.add_escrow()`.
- Renders `loan/_escrow_list.html` partial.

`POST /accounts/<int:account_id>/loan/escrow/<int:component_id>/delete` -- `delete_escrow`:
- HTMX endpoint. Same logic as current `mortgage.delete_escrow()`.
- Renders `loan/_escrow_list.html` partial.

`POST /accounts/<int:account_id>/loan/payoff` -- `payoff_calculate`:
- HTMX endpoint. Same logic as current `mortgage.payoff_calculate()`.
- Renders `loan/_payoff_results.html` partial.
- This was previously mortgage-only. Now available to all loan types.

**Code quality requirements:**
- Follow the exact import style, logging pattern, and error handling of existing
  route files
- Use `log_event()` from `app/utils/logging_helpers.py` if that pattern is
  established, otherwise match the logging pattern used in the existing route
  files
- Every route must have a docstring
- CSRF protection on all POST routes (already handled by form templates)
- Ownership verification on every route via the helper

### Update `app/routes/accounts.py`

In `create_account()`, replace the mortgage and auto loan redirect branches:

**Before:**
```python
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.MORTGAGE):
    return redirect(url_for("mortgage.dashboard", account_id=account.id, setup=1))
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.AUTO_LOAN):
    return redirect(url_for("auto_loan.dashboard", account_id=account.id, setup=1))
```

**After:**
```python
if account_type.has_amortization:
    return redirect(url_for("loan.dashboard", account_id=account.id, setup=1))
```

This single branch handles Mortgage, Auto Loan, Student Loan, Personal Loan,
HELOC, and any future loan type. Place it at the appropriate position in the
redirect chain (after the HYSA check, before the investment check, or wherever
the flow makes sense).

Also update the fallthrough comment and the `has_parameters` warning -- Student
Loan and Personal Loan are no longer dead ends.

**Grep for all references to `auto_loan.` and `mortgage.` blueprint names**
in this file. Every `url_for("auto_loan.xxx")` and `url_for("mortgage.xxx")`
must be updated or removed.

### Update `app/routes/savings.py`

Replace the per-type loan param loading with a single data-driven query:

```python
# Load loan params for all amortizing accounts in one query.
amort_type_ids = {
    at.id for at in db.session.query(AccountType).filter_by(has_amortization=True).all()
}
loan_account_ids = [a.id for a in accounts if a.account_type_id in amort_type_ids]
if loan_account_ids:
    for lp in db.session.query(LoanParams).filter(
        LoanParams.account_id.in_(loan_account_ids)
    ).all():
        loan_params_map[lp.account_id] = lp
```

Remove the separate `MortgageParams` and `AutoLoanParams` imports and queries.

Also check the savings templates for any references to `auto_loan.dashboard`
or `mortgage.dashboard` URLs. These should become `loan.dashboard`.

### Update `app/__init__.py`

- Remove `auto_loan_bp` import and registration
- Remove `mortgage_bp` import and registration
- Add `loan_bp` import and registration
- Verify no other blueprint references the old names

### Delete old route files

- Delete `app/routes/auto_loan.py`
- Delete `app/routes/mortgage.py`

### Grep for stale blueprint references

After all route changes, grep the entire codebase (including templates) for:
- `auto_loan.` (blueprint references in `url_for()` calls)
- `mortgage.` (blueprint references in `url_for()` calls)
- `auto_loan_bp`
- `mortgage_bp`

Every match must be updated or removed. Missing a `url_for()` reference
results in a runtime error (BuildError) or 404.

---

## Phase 6 -- Templates

### Create `app/templates/loan/` directory

Create the following templates. For each, start from the existing mortgage
template (which is the more feature-complete version) and generalize.

**`loan/dashboard.html`:**
- Extend `base.html`
- Title: `{{ account.name }} -- {{ account_type.name }} -- Shekel`
- Breadcrumbs: link to `savings.dashboard`, current page shows account name +
  type name
- Header: `<i class="bi {{ account_type.icon_class or 'bi-bank' }}"></i> {{ account.name }}`
- Tabs:
  - "Overview" (always shown)
  - "Escrow" (shown if `escrow_components` is not empty OR a template variable
    `show_escrow_tab` is True -- the route can set this based on whether the
    type typically has escrow, or it can always show for all loans and let the
    empty state guide the user)
  - "Rate History" (shown if `params.is_arm`)
  - "Payoff Calculator" (always shown)
- Overview tab content: loan summary card + edit parameters card + balance chart
  (merge the identical content from both existing dashboards)
- Edit parameters form: shared fields (current_principal, interest_rate,
  term_months, payment_day) + conditional ARM fields shown if is_arm is True.
  The `term_months` input should use `account_type.max_term_months` for its
  `max` attribute if available, falling back to 600.
- All HTMX endpoints use `loan.*` blueprint names
- All `url_for()` calls use `loan.dashboard`, `loan.update_params`, etc.

**`loan/setup.html`:**
- Merge of auto_loan/setup.html and mortgage/setup.html
- Icon and heading from account_type
- Shared fields: original_principal, current_principal, interest_rate,
  term_months, origination_date, payment_day
- Conditional: `is_arm` checkbox (can be shown for all loan types or
  conditionally -- showing for all is simpler and lets any loan opt into
  variable rate tracking)
- `term_months` max attribute from account_type.max_term_months
- Setup description can reference the account type name generically
- Pre-fill current_principal from `account.current_anchor_balance` if available
  (auto loan does this, mortgage doesn't -- do it for all types)
- POST to `loan.create_params`

**`loan/_escrow_list.html`:**
- Copy from `mortgage/_escrow_list.html`
- Update all `url_for()` calls from `mortgage.*` to `loan.*`
- No other changes needed

**`loan/_rate_history.html`:**
- Copy from `mortgage/_rate_history.html`
- Update all `url_for()` calls from `mortgage.*` to `loan.*`

**`loan/_payoff_results.html`:**
- Copy from `mortgage/_payoff_results.html`
- Update all `url_for()` calls from `mortgage.*` to `loan.*`

### Update templates that link TO loan dashboards

Grep for `url_for('auto_loan.` and `url_for('mortgage.` across ALL templates.
Common locations:
- `app/templates/savings/dashboard.html` (or wherever the accounts dashboard
  links to individual account dashboards)
- `app/templates/accounts/` templates
- Any navigation or breadcrumb templates

Every reference must be updated to `url_for('loan.dashboard', account_id=...)`.

### Delete old template directories

- Delete `app/templates/auto_loan/` (entire directory)
- Delete `app/templates/mortgage/` (entire directory)

---

## Phase 7 -- Tests

### Read existing tests first

Before writing any tests, read:
- `tests/test_routes/test_auto_loan.py` -- every test method
- `tests/test_routes/test_mortgage.py` -- every test method
- `tests/conftest.py` -- all fixtures

Understand what is currently tested and what coverage exists. The new test
file must provide equivalent or better coverage.

### Create `tests/test_routes/test_loan.py`

**Structure:**

The test file should have parametrized tests for behavior that is identical
across loan types, and dedicated tests for features that only apply to certain
configurations.

**Fixtures needed:**
- A loan account (use an existing Auto Loan type account or create one)
- A mortgage account (use Mortgage type)
- LoanParams instances for each
- The test must create real `ref.account_types` rows or use the seeded ones

**Test categories:**

1. **Dashboard tests** (parametrize across at least two loan types):
   - Dashboard renders for account with params
   - Dashboard renders setup page for account without params
   - Dashboard returns 404 for non-existent account
   - Dashboard returns 404 for account owned by different user
   - Dashboard returns 404 for non-amortizing account type

2. **Setup/create_params tests** (parametrize across loan types):
   - Successful param creation
   - Validation errors (missing fields, out-of-range values)
   - Type-specific term_months validation (auto loan rejects >120, mortgage
     allows 360)
   - Duplicate params prevention
   - Interest rate percentage-to-decimal conversion
   - CSRF protection

3. **Update params tests:**
   - Successful update of each field
   - Partial update (only some fields)
   - ARM fields update (is_arm, adjustment months)
   - Validation errors
   - Ownership verification

4. **Escrow tests** (can use any loan type, not just mortgage):
   - Add escrow component
   - Delete escrow component
   - Duplicate name prevention
   - Inflation rate percentage conversion
   - HTMX response format

5. **Rate history tests** (requires is_arm=True on params):
   - Add rate change
   - Rate change updates params.interest_rate
   - HTMX response format
   - Validation errors

6. **Payoff calculator tests** (parametrize across loan types):
   - Extra payment mode
   - Target date mode
   - Invalid mode handling
   - Missing target_date handling
   - HTMX response format

7. **Adversarial tests:**
   - Access loan dashboard for account belonging to another user
   - Create params for account belonging to another user
   - Attempt operations on non-amortizing account type
   - All must return 404 (not 403 -- per CLAUDE.md security rules)

**Every test must have a docstring** explaining what is verified and why.

### Update other test files

Grep for `AutoLoanParams`, `MortgageParams`, `MortgageRateHistory`,
`auto_loan_bp`, `mortgage_bp`, `auto_loan.`, `mortgage.` across all test
files. Update imports and references.

Specific files likely affected:
- `tests/test_services/test_balance_calculator.py` -- if it creates
  `AutoLoanParams` or `MortgageParams` instances for testing, update to
  `LoanParams`
- `tests/conftest.py` -- if it has fixtures creating loan params
- Any integration tests that create loan accounts

### Delete old test files

- Delete `tests/test_routes/test_auto_loan.py`
- Delete `tests/test_routes/test_mortgage.py`

---

## Phase 8 -- Verification

### Run the full test suite

```bash
timeout 660 pytest -v --tb=short
```

Every test must pass. If any test fails:
1. Read the failure output carefully
2. Determine if it's a stale reference (import, url_for, model name) or a
   logic error
3. Fix the root cause -- do not suppress or skip the test
4. Re-run the failing test file to confirm the fix
5. Re-run the full suite

### Run pylint

```bash
pylint app/ --fail-on=E,F
```

The score must not decrease from the current baseline. Fix any new warnings
introduced by the refactor.

### Manual verification checklist

After all tests pass, verify these flows work by reading the code path
end-to-end (trace from route to template to HTMX endpoint):

- [ ] Creating a new mortgage account redirects to loan setup
- [ ] Creating a new auto loan account redirects to loan setup
- [ ] Loan setup form shows ARM checkbox
- [ ] Loan setup form respects type-specific term_months max
- [ ] Loan setup pre-fills current_principal from anchor balance
- [ ] Loan dashboard shows correct icon from account type
- [ ] Loan dashboard shows escrow tab when escrow components exist
- [ ] Loan dashboard shows rate history tab when is_arm is True
- [ ] Loan dashboard always shows payoff calculator tab
- [ ] Editing loan params works (all fields)
- [ ] Adding escrow component works (HTMX response)
- [ ] Deleting escrow component works (HTMX response)
- [ ] Adding rate change works and updates current interest rate
- [ ] Payoff calculator extra payment mode works with chart
- [ ] Payoff calculator target date mode works
- [ ] Savings dashboard loads loan params for all amortizing accounts
- [ ] Balance calculator works with LoanParams objects
- [ ] Student Loan account type can create an account and set up params
- [ ] Personal Loan account type can create an account and set up params

### Grep for stale references (final pass)

```
AutoLoanParams (outside migrations/)
MortgageParams (outside migrations/)
MortgageRateHistory (outside migrations/)
auto_loan_params (as backref, outside migrations/)
mortgage_params (as backref, outside migrations/)
auto_loan_bp
mortgage_bp
url_for('auto_loan.
url_for('mortgage.
url_for("auto_loan.
url_for("mortgage.
from app.models.auto_loan
from app.models.mortgage
from app.routes.auto_loan
from app.routes.mortgage
templates/auto_loan
templates/mortgage
```

Zero matches outside of migration files and this document.

---

## Constraints and Non-Negotiable Rules

These come from CLAUDE.md and the project's established standards. They
apply to every line of code written during this implementation.

1. **Use `Decimal`, never `float`**, for all monetary amounts.
2. **snake_case** everywhere -- variables, functions, modules, columns.
3. **Alembic migrations** for all schema changes. Never `db.create_all()`.
4. **Descriptive migration messages.** e.g., "unify loan params into single
   table with rate history rename"
5. **All queries must be user-scoped.** Every query touching user data filters
   by `user_id` or verifies ownership.
6. **Reference table IDs for logic, strings for display only.** Do not compare
   against `account_type.name` strings. Use `account_type.has_amortization`,
   `account_type.id`, `ref_cache.acct_type_id()`.
7. **Security response rule:** Resource not found and resource belongs to
   another user must return identical 404 responses.
8. **Docstrings on every module, class, and function.**
9. **No unused imports.** Pylint catches these.
10. **No broad `except Exception` blocks.**
11. **Test everything.** Every code path, every edge case, every error condition.
12. **Commit messages:** `<type>(<scope>): <what changed>`.
    Type: feat, fix, refactor, test, docs, chore.
13. **One concern per commit.** Each commit should be atomic and individually
    revertable.

---

## Commit Strategy

Break the work into atomic commits. Suggested sequence:

1. **Migration:** Create loan_params table, rate_history table, add ref columns,
   migrate data, drop old tables.
2. **Models:** Create LoanParams, RateHistory, move EscrowComponent. Update
   ref.py. Delete old model files. Update __init__.py.
3. **Schemas:** Replace per-type schemas with unified schemas.
4. **Ref cache:** Add type config accessors.
5. **Routes:** Create loan.py. Update accounts.py, savings.py. Register
   blueprint. Delete old route files.
6. **Templates:** Create loan/ templates. Update linking templates. Delete old
   template directories.
7. **Tests:** Create test_loan.py. Update affected test files. Delete old test
   files.
8. **Seed script:** Update seed_ref_tables.py with HELOC flag and icon/term data.
9. **Cleanup:** Final grep for stale references. Pylint pass. Full test suite.

Each commit must leave the codebase in a working state. If that's not possible
for intermediate commits (e.g., deleting old routes before new ones exist),
combine the dependent changes into a single commit.

**After every commit, prompt the developer to review and push.**

---

## What This Refactor Does NOT Do

Do not implement any of these. They are out of scope:

- Extending the settings UI to expose `has_parameters`, `has_amortization`,
  `category_id`, `icon_class`, or `max_term_months` for user-created types.
  That's a future UI task.
- Creating a `revolving_credit_params` table for Credit Card or HELOC draw
  phase. That's a future feature.
- Renaming `hysa_params` to `interest_params`. Correct in principle but out
  of scope.
- Modifying the amortization engine, balance calculator, or escrow calculator.
  They are correct and type-agnostic. Do not touch them.
- Adding new account types. The architecture supports it; the actual types are
  added later.
- Implementing Section 5's payment linkage, payment history tracking, or
  income-relative savings goals. Those are separate features built on top of
  the architecture this refactor establishes.
