> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan -- Account Parameter Architecture (DRY/SOLID Compliance)

**Date:** March 29, 2026
**Status:** Plan complete, awaiting implementation
**Branch:** `dev`

**Prerequisite documents read:**

- `CLAUDE.md` -- Project rules, architecture, conventions
- `docs/account_parameter_architecture.md` -- Primary analysis (Options A-G)
- `docs/adversarial_audit.md` -- Security/quality findings (H-04, L-06, L-07, L-14, OCP assessment)
- `docs/project_roadmap_v4-1.md` -- Overall roadmap, Section 5 context
- `docs/project_requirements_v3_addendum.md` -- Section 4.22 (Extended Account Types)
- `docs/implementation_plan_section4.md` -- Completed Section 4 patterns
- `docs/fixes_improvements.md` -- Open items audit
- `docs/implementation_plan_section5.md` -- Now defunct (see Section 2.9)

**Executive summary:** This plan eliminates all hardcoded account type ID dispatch in the Shekel
codebase, replacing it with metadata flags on `ref.account_types`. After implementation, an end
user can create any account type through the settings UI -- selecting a category and toggling
behavior flags -- and the application handles it correctly: parameter tables auto-created,
dashboard routing correct, chart projections correct, retirement gap analysis correct. Zero
developer intervention required. The plan consists of 8 atomic commits organized in three phases.

---

## 1. Current State Assessment

Verified against the codebase on March 29, 2026. All file paths, function names, and line
references confirmed by reading the actual code.

### 1.1 Liability Category (Gold Standard)

The liability category is fully DRY and OCP-compliant. It is the pattern to replicate.

**What makes it gold standard:**

- **Single unified `LoanParams` table** (`app/models/loan_params.py`) serves all five loan
  types: Mortgage, Auto Loan, Student Loan, Personal Loan, HELOC.
- **Single route file** (`app/routes/loan.py`) with a single `_load_loan_account()` helper that
  verifies `account_type.has_amortization` -- a boolean metadata flag, not a type ID check.
- **Single template set** (`app/templates/loan/`) with type-specific behavior driven entirely by
  `account_type.max_term_months` and `account_type.icon_class`.
- **Type-agnostic services:** `amortization_engine.py` accesses 6 attributes via duck-typing.
  `balance_calculator.calculate_balances_with_amortization()` uses `hasattr()` checks, not type
  imports.
- **Adding a new loan type** requires only a seed data entry with `has_amortization=True`. No
  code changes needed.

**Exception:** Credit Card (`has_parameters=False`, `has_amortization=False`). Revolving credit
is fundamentally different from installment debt and correctly excluded.

### 1.2 Asset Category

**Current state: partially hardcoded.**

Only HYSA has parameters (`has_parameters=True`). The `HysaParams` model stores `apy` and
`compounding_frequency`. Money Market, CD, and HSA have `has_parameters=False` despite Money
Market, CD, and HSA being structurally identical to HYSA (interest-bearing accounts with APY
and compounding frequency).

**Hardcoded references found (5 locations):**

| File | Line | Code | Purpose |
| --- | --- | --- | --- |
| `app/routes/accounts.py` | 137 | `acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA)` | Auto-create HysaParams |
| `app/routes/accounts.py` | 159 | Same check | Redirect to hysa_detail |
| `app/routes/accounts.py` | 576 | Same check | Verify HYSA type on detail page |
| `app/routes/savings.py` | 101 | Same check | Load HYSA params for dashboard |
| `app/services/chart_data_service.py` | 224 | Same check | Dispatch to interest calculator |

**DRY violation:** Adding Money Market or CD interest support requires copying the HYSA pattern
into all 5 locations. This is the exact anti-pattern the liability category already solved.

**`has_parameters` gap:** The `has_parameters` flag is stored on `ref.account_types` but never
queried for behavioral dispatch anywhere in the codebase. It exists in seed data and the model
but does not drive any logic.

### 1.3 Retirement Category

**Current state: mostly metadata-driven with one hardcoded constant.**

All four types (401(k), Roth 401(k), Traditional IRA, Roth IRA) share `InvestmentParams`.
`growth_engine.py` is fully type-agnostic. `retirement_gap_calculator.py` operates on projected
balances with an `is_traditional` boolean -- good metadata pattern.

**However**, `app/routes/retirement.py:51` defines:

```python
TRADITIONAL_TYPE_ENUMS = frozenset({AcctTypeEnum.K401, AcctTypeEnum.TRADITIONAL_IRA})
```

This frozenset is used at line 131-132 to build `traditional_type_ids` for flagging pre-tax
accounts in gap analysis (line 332). If a user creates a 403(b), Solo 401(k), or SEP IRA
through the settings UI, the app will not recognize it as pre-tax. The frozenset defeats the
goal of user-configurable account types.

**Positive:** `retirement.py` lines 123-130 already use category-based queries for loading
retirement accounts:

```python
retirement_types = db.session.query(AccountType).filter(
    AccountType.category_id.in_([retirement_cat_id, investment_cat_id])
).all()
```

This is the correct pattern. Only the pre-tax distinction is hardcoded.

### 1.4 Investment Category

**Current state: hardcoded type ID sets.**

Brokerage shares `InvestmentParams` with retirement types. The dispatch works, but relies on
hardcoded frozensets in two locations:

| File | Lines | Code | Purpose |
| --- | --- | --- | --- |
| `app/routes/accounts.py` | 142-148 | `investment_type_ids = {K401, ROTH_401K, TRAD_IRA, ROTH_IRA, BROKERAGE}` | Auto-create InvestmentParams + redirect |
| `app/routes/savings.py` | 117-122 | `retirement_type_ids = {K401, ROTH_401K, TRAD_IRA, ROTH_IRA, BROKERAGE}` | Load InvestmentParams for dashboard |

**529 Plan gap:** 529 Plan has `has_parameters=False` in seed data despite being structurally
identical to Brokerage (same `InvestmentParams` schema applies). It cannot have investment
parameters until the seed is updated AND the hardcoded sets are replaced with category queries.

### 1.5 Cross-Cutting Issues

**Savings dashboard (`savings.py:295-305`)** -- The `needs_setup` logic uses three different
dispatch strategies in sequence: HYSA type ID check, then retirement frozenset check, then
`has_amortization` metadata check. This should be a single metadata-driven block.

**Emergency fund calculation (`savings.py:433-436`)** -- Uses `savings_type_ids = {SAVINGS, HYSA}`
to determine which accounts count toward emergency fund. This is a hardcoded type ID set that
should be replaced with a metadata flag. Not all asset types are liquid emergency reserves (CDs
have penalties, retirement accounts have withdrawal restrictions), so a dedicated `is_liquid`
boolean on `ref.account_types` is the correct approach. This also eliminates the last remaining
hardcoded type ID set in the savings dashboard after Commits 3 and 4.

**Settings UI (`accounts.py:380-408`)** -- `create_account_type()` hardcodes
`category_id=ref_cache.acct_category_id(AcctCategoryEnum.ASSET)`. `update_account_type()` only
updates the `name` field. No UI exists for `category_id`, `has_parameters`, `has_amortization`,
`icon_class`, or `max_term_months`. User-created types are functionally dead-ends.

### 1.6 Discrepancies Between Architecture Doc and Current Code

Two discrepancies found between `docs/account_parameter_architecture.md` and the current code:

1. **Architecture doc references `MortgageParams` and `AutoLoanParams` as separate models.**
   The current code has a unified `LoanParams` model (`app/models/loan_params.py`) serving all
   loan types. The loan unification was completed in commit `5742c51`.

2. **Architecture doc references `mortgage.py` and `auto_loan.py` as separate route files.**
   The current code has a unified `loan.py` route (`app/routes/loan.py`) serving all loan types.

Both discrepancies reflect the loan unification work that occurred after the architecture doc was
written. The architecture doc's analysis and option proposals remain valid; only the file
references are stale.

---

## 2. Design Decisions

### 2.1 Asset Category: Option A -- Rename HysaParams to InterestParams + has_interest Flag

**Chosen approach:** Rename the `HysaParams` model/table to `InterestParams` and add a
`has_interest` boolean column to `ref.account_types`.

**Justification:** This exactly mirrors the liability pattern. `has_amortization=True` tells the
app "use LoanParams and the amortization engine." `has_interest=True` tells the app "use
InterestParams and the interest projection service." The interest projection service
(`app/services/interest_projection.py`) is already fully type-agnostic -- it takes scalar values
(`balance`, `apy`, `compounding_frequency`), not model objects. The rename is safe.

**Why not Option B (keep table name):** A table named `hysa_params` that stores parameters for
Money Market and CD accounts is misleading. The rename is mechanical and one-time. Do it right.

**Why not Option C (per-type tables):** Rejected for the same reason per-type loan tables were
rejected. DRY violation. Already proven wrong by the liability category.

### 2.2 Retirement/Investment: Option D -- Category-Based Queries

**Chosen approach:** Replace hardcoded `investment_type_ids` and `retirement_type_ids` frozensets
with queries filtering by `AccountType.category_id` and `AccountType.has_parameters`.

**Justification:** `retirement.py` already does this correctly (lines 123-130). The savings
dashboard and account creation routes should follow the same pattern.

### 2.3 TRADITIONAL_TYPE_ENUMS: is_pretax Metadata Flag

**Chosen approach:** Add `is_pretax` boolean column to `ref.account_types`. Set `True` for
401(k) and Traditional IRA. Eliminate the `TRADITIONAL_TYPE_ENUMS` frozenset.

**Justification:** The entire point of this plan is that a user can create a new retirement
account type (403(b), Solo 401(k), SEP IRA) and the app handles it correctly without code
changes. That includes knowing whether it is pre-tax or post-tax for the retirement gap
analysis. A frozenset that must be manually updated by a developer defeats this goal.

The `is_pretax` flag is only meaningful for Retirement category types. The settings UI will
only show this option when category=Retirement.

### 2.4 529 Plan: Option E -- Enable Parameters

**Chosen approach:** Set `has_parameters=True` for 529 Plan in seed data. Once category-based
queries are in place (Commit 4), this works automatically with zero code changes.

### 2.5 Settings UI: Option F -- Enhanced Form

**Chosen approach:** Extend the account type create/update forms to expose: category (dropdown),
has_parameters (checkbox), has_amortization (checkbox, Liability only), has_interest (checkbox,
Asset only), is_pretax (checkbox, Retirement only), is_liquid (checkbox, Asset only), icon_class
(dropdown), max_term_months (number input, shown when has_amortization=True).

**Validation rules:** has_amortization requires Liability category. has_interest requires Asset
category. is_pretax requires Retirement category. is_liquid requires Asset category.
max_term_months requires has_amortization. These prevent nonsensical flag combinations.

### 2.6 Unified Dispatch: Option G -- Falls Out of A + D

The unified dispatch chain after all commits:

```
Account creation auto-params:
  if has_interest         → create InterestParams with defaults
  if has_amortization     → skip (user fills loan setup form)
  if has_parameters       → create InvestmentParams with defaults

Account creation redirect:
  if has_interest         → redirect to interest_detail (setup=1)
  if has_amortization     → redirect to loan.dashboard (setup=1)
  if has_parameters       → redirect to investment.dashboard (setup=1)
  else                    → redirect to accounts list

Chart service dispatch:
  if has_interest + params exist  → calculate_balances_with_interest()
  if has_amortization             → calculate_balances_with_amortization()
  else                            → calculate_balances()
```

No type IDs in any dispatch path.

---

## 3. Commit Sequence

### Phase I: Foundation

---

#### Commit 1

**A. Commit message:**
`feat(account-types): add has_interest, is_pretax, and is_liquid metadata columns`

**B. Problem statement:**
The application has no metadata mechanism to identify interest-bearing accounts, pre-tax
retirement accounts, or liquid emergency-fund-eligible accounts. HYSA dispatch is hardcoded
by type ID. Pre-tax vs post-tax distinction is hardcoded via the `TRADITIONAL_TYPE_ENUMS`
frozenset. Emergency fund eligibility is hardcoded via `savings_type_ids = {SAVINGS, HYSA}`.
All three prevent user-created account types from working correctly.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `migrations/versions/<hash>_add_has_interest_is_pretax_is_liquid.py` | Created | Alembic migration adding three boolean columns |
| `app/models/ref.py` | Modified | `AccountType` class gains `has_interest`, `is_pretax`, and `is_liquid` columns |
| `app/ref_seeds.py` | Modified | Seed tuples gain two new positional values |
| `app/__init__.py` | Modified | Application factory seeding logic handles new tuple positions |
| `scripts/seed_ref_tables.py` | Modified | Standalone seed script handles new tuple positions |

**D. Implementation approach:**

*Migration:*

Add three columns to `ref.account_types`:
- `has_interest` Boolean, NOT NULL, server_default='false'
- `is_pretax` Boolean, NOT NULL, server_default='false'
- `is_liquid` Boolean, NOT NULL, server_default='false'

Data migration within the same Alembic file:
- `UPDATE ref.account_types SET has_interest = true WHERE name IN ('HYSA', 'HSA')`
- `UPDATE ref.account_types SET has_parameters = true WHERE name = 'HSA'`
- `UPDATE ref.account_types SET is_pretax = true WHERE name IN ('401(k)', 'Traditional IRA')`
- `UPDATE ref.account_types SET is_liquid = true WHERE name IN ('Checking', 'Savings', 'HYSA', 'Money Market')`

Note: HSA gains `has_parameters=True` alongside `has_interest=True` because the dispatch
logic uses `has_parameters` as the outer guard for `needs_setup` detection. An interest-bearing
type must also have `has_parameters=True` to function correctly.

*Model (`app/models/ref.py`):*

Add to `AccountType` class after `has_amortization`:
```python
has_interest = db.Column(db.Boolean, nullable=False, default=False)
is_pretax = db.Column(db.Boolean, nullable=False, default=False)
is_liquid = db.Column(db.Boolean, nullable=False, default=False)
```

Update the docstring to document all behavioral flags. `is_liquid` indicates the account type
holds liquid funds that count toward emergency fund calculations and savings goal eligibility.

*Seed data (`app/ref_seeds.py`):*

Expand tuple format to 9 values:
`(name, category_name, has_parameters, has_amortization, has_interest, is_pretax, is_liquid, icon_class, max_term_months)`

Updated values per type:
- HYSA: `has_interest=True`, `is_liquid=True`
- HSA: `has_parameters=True`, `has_interest=True` (was both False)
- Checking: `is_liquid=True`
- Savings: `is_liquid=True`
- Money Market: `is_liquid=True`
- 401(k): `is_pretax=True`
- Traditional IRA: `is_pretax=True`
- All other types: `False` for all new columns.

*Application factory (`app/__init__.py`) and seed script (`scripts/seed_ref_tables.py`):*

Update the unpacking of seed tuples to include the three new positional values. Set
`existing.has_interest`, `existing.is_pretax`, and `existing.is_liquid` on upsert. Also
update `existing.has_parameters` for HSA (changing from False to True).

**E. Test cases:**

- **New:** `test_account_type_has_interest_column` -- Verify HYSA and HSA have
  `has_interest=True` after seed. Verify Checking has `has_interest=False`. Assert column
  is non-nullable.
- **New:** `test_account_type_is_pretax_column` -- Verify 401(k) and Traditional IRA have
  `is_pretax=True`. Verify Roth 401(k), Roth IRA, Brokerage have `is_pretax=False`.
- **New:** `test_account_type_is_liquid_column` -- Verify Checking, Savings, HYSA, Money Market
  have `is_liquid=True`. Verify CD, HSA, Credit Card, all loan types, all retirement types
  have `is_liquid=False`.
- **New:** `test_hsa_has_parameters_true` -- Verify HSA now has `has_parameters=True` (changed
  from False).
- **Regression:** All existing account type tests pass unchanged.

**F. Manual verification steps:**

1. Run `flask db upgrade`. Verify migration completes without errors.
2. Run `python scripts/seed_ref_tables.py`. Verify seed completes.
3. Query `SELECT name, has_interest, is_pretax, is_liquid, has_parameters FROM ref.account_types ORDER BY id`.
   Verify: HYSA and HSA have `has_interest=true`; HSA has `has_parameters=true`; 401(k) and
   Traditional IRA have `is_pretax=true`; Checking, Savings, HYSA, Money Market have
   `is_liquid=true`.

**G. Downstream effects:**

Minimal. This commit adds columns, sets values, and updates HSA from `has_parameters=False` to
`has_parameters=True`. The HSA change means a newly created HSA account will now auto-create
`InterestParams` and redirect to the interest detail page (after Commit 3). No existing HSA
accounts are affected -- they simply gain the ability to have interest parameters configured.
All other existing code continues to work because the new columns are not yet read by any
dispatch path.

**H. Rollback notes:**

Revert the commit, then run `flask db downgrade` to remove the three columns and revert HSA's
`has_parameters` to False. The downgrade drops `has_interest`, `is_pretax`, and `is_liquid`.
No data loss -- these columns contain only derived metadata that can be re-added by re-running
the migration.

---

#### Commit 2

**A. Commit message:**
`refactor(accounts): rename HysaParams to InterestParams and hysa_detail to interest_detail`

**B. Problem statement:**
The `HysaParams` model, `hysa_params` table, `hysa_detail` route, and `hysa_detail.html`
template are all named for a single account type. After this plan, they serve all
interest-bearing types (HYSA, Money Market, CD). HYSA-specific naming prevents developers and
users from understanding their generic purpose.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `migrations/versions/<hash>_rename_hysa_params_to_interest_params.py` | Created | ALTER TABLE RENAME |
| `app/models/hysa_params.py` → `app/models/interest_params.py` | Renamed | File rename, class rename `HysaParams` → `InterestParams`, table rename, backref rename |
| `app/models/__init__.py` | Modified | Import path updated |
| `app/schemas/validation.py` | Modified | `HysaParamsCreateSchema` → `InterestParamsCreateSchema`, `HysaParamsUpdateSchema` → `InterestParamsUpdateSchema` |
| `app/routes/accounts.py` | Modified | Import, variable names, route function names (`hysa_detail` → `interest_detail`, `update_hysa_params` → `update_interest_params`), URL patterns (`/hysa` → `/interest`), flash messages |
| `app/routes/savings.py` | Modified | Import, variable names (`hysa_params_map` → `interest_params_map`, `hysa_type_id` → remains temporarily as dispatch changes in Commit 3) |
| `app/services/chart_data_service.py` | Modified | Comment updates referencing the old name |
| `app/templates/accounts/hysa_detail.html` → `app/templates/accounts/interest_detail.html` | Renamed | Template file rename, page title generalized |
| `tests/test_routes/test_hysa.py` | Modified | Import and class/fixture renames |
| `tests/test_routes/test_accounts.py` | Modified | Import rename, url_for references |
| `tests/test_services/test_balance_calculator_hysa.py` | Modified | Namedtuple mock rename |
| `tests/test_routes/test_savings.py` | Modified | Variable name updates |

**D. Implementation approach:**

This is a purely mechanical rename with no behavioral changes.

*Migration:*
```python
op.rename_table("hysa_params", "interest_params", schema="budget")
op.execute("ALTER INDEX budget.ix_hysa_params_account_id RENAME TO ix_interest_params_account_id")
```

*Model file:*
Rename `app/models/hysa_params.py` to `app/models/interest_params.py`. Inside:
- Class `HysaParams` → `InterestParams`
- `__tablename__` = `"interest_params"`
- Backref: `backref=db.backref("interest_params", uselist=False, lazy="joined")`
- `__repr__` updated

*Routes:*
In `accounts.py`:
- `from app.models.interest_params import InterestParams`
- Route `/accounts/<int:account_id>/hysa` → `/accounts/<int:account_id>/interest`
- Function `hysa_detail()` → `interest_detail()`
- Route `/accounts/<int:account_id>/hysa/params` → `/accounts/<int:account_id>/interest/params`
- Function `update_hysa_params()` → `update_interest_params()`
- Schema instances: `_hysa_params_schema` → `_interest_params_schema`
- Flash message: "This account is not a HYSA." → "This account type does not support interest parameters."
- All `url_for("accounts.hysa_detail")` → `url_for("accounts.interest_detail")`

In `savings.py`:
- `from app.models.interest_params import InterestParams`
- `hysa_params_map` → `interest_params_map`
- Other `hysa_params` variable references → `interest_params`

*Schemas (`validation.py`):*
- `HysaParamsCreateSchema` → `InterestParamsCreateSchema`
- `HysaParamsUpdateSchema` → `InterestParamsUpdateSchema`

**E. Test cases:**

- **Updated:** Every test file that imports `HysaParams` updated to import `InterestParams`.
  Every variable named `hysa_params` updated. Every `url_for("accounts.hysa_detail")` updated.
  Every assertion checking the `hysa_params` backref updated to `interest_params`.
- **Regression:** Run the full test suite. All tests must pass with only naming changes.
- **New:** No new tests needed -- this is a pure rename.

**F. Manual verification steps:**

1. Run `flask db upgrade`. Verify table renamed in database: `\dt budget.*` should show
   `interest_params`, not `hysa_params`.
2. Navigate to an existing HYSA account detail page. Verify URL is now `/accounts/<id>/interest`.
3. Edit APY on the interest detail page. Verify the form submits to `/accounts/<id>/interest/params`
   and saves correctly.
4. Create a new HYSA account. Verify redirect goes to `/accounts/<id>/interest?setup=1`.

**G. Downstream effects:**

All backref access changes from `account.hysa_params` to `account.interest_params`. Any
external bookmarks to `/accounts/<id>/hysa` will 404. This is acceptable -- there are no
external links to these URLs.

**H. Rollback notes:**

Revert the commit, then `flask db downgrade` to rename the table back. The downgrade renames
`interest_params` back to `hysa_params` and restores the index name. All data preserved.

---

### Phase II: Dispatch Unification

---

#### Commit 3

**A. Commit message:**
`refactor(dispatch): replace hardcoded HYSA type ID checks with has_interest metadata flag`

**B. Problem statement:**
Five locations in the codebase check `acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA)`
to dispatch interest-related behavior. This means a Money Market or CD account with identical
financial characteristics cannot use interest projection without code changes. The `has_interest`
flag (added in Commit 1) should drive this dispatch instead.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `app/routes/accounts.py` | Modified | `create_account()`: auto-creation and redirect use `has_interest`. `interest_detail()`: type verify uses `has_interest`. `update_interest_params()`: type verify uses `has_interest`. |
| `app/services/chart_data_service.py` | Modified | `_calculate_account_balances()`: dispatch uses `has_interest` + `interest_params` |
| `app/routes/savings.py` | Modified | Interest params loading block uses `has_interest` flag. First `needs_setup` branch uses `has_interest`. |
| `tests/test_routes/test_accounts.py` | Modified | Tests for auto-creation and redirect updated |
| `tests/test_routes/test_hysa.py` | Modified | Detail page tests updated for generic interest type verification |
| `tests/test_services/test_chart_data_service.py` | Modified | Dispatch test updated |
| `tests/test_routes/test_savings.py` | Modified | Dashboard loading tests updated |

**D. Implementation approach:**

*`accounts.py` -- `create_account()` (current lines 132-173):*

Replace the auto-creation block:
```python
# Before (hardcoded):
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA):
    if not db.session.query(InterestParams).filter_by(account_id=account.id).first():
        db.session.add(InterestParams(account_id=account.id))

# After (metadata-driven):
if account_type and account_type.has_interest:
    if not db.session.query(InterestParams).filter_by(account_id=account.id).first():
        db.session.add(InterestParams(account_id=account.id))
```

Replace the redirect block:
```python
# Before:
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA):
    return redirect(url_for("accounts.interest_detail", account_id=account.id, setup=1))

# After:
if account_type and account_type.has_interest:
    return redirect(url_for("accounts.interest_detail", account_id=account.id, setup=1))
```

*`accounts.py` -- `interest_detail()` (current line 576):*

Replace:
```python
# Before:
if not account.account_type or account.account_type_id != ref_cache.acct_type_id(AcctTypeEnum.HYSA):

# After:
if not account.account_type or not account.account_type.has_interest:
```

*`accounts.py` -- `update_interest_params()` (current line 677):*

Same pattern -- replace HYSA type ID check with `account.account_type.has_interest`.

*`chart_data_service.py` -- `_calculate_account_balances()` (current line 224):*

Replace:
```python
# Before:
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA) and account.interest_params:

# After:
if account.account_type and account.account_type.has_interest and account.interest_params:
```

*`savings.py` -- Interest params loading (current lines 100-108):*

Replace:
```python
# Before:
hysa_type_id = ref_cache.acct_type_id(AcctTypeEnum.HYSA)
interest_params_map = {}
hysa_account_ids = [a.id for a in accounts if a.account_type_id == hysa_type_id]

# After:
interest_params_map = {}
interest_account_ids = [
    a.id for a in accounts
    if a.account_type and a.account_type.has_interest
]
```

Update the query to use `interest_account_ids` instead of `hysa_account_ids`.

*`savings.py` -- `needs_setup` first branch (current line 300):*

Replace:
```python
# Before:
if acct.account_type_id == hysa_type_id:
    needs_setup = acct_interest_params is None

# After:
if acct.account_type and acct.account_type.has_interest:
    needs_setup = acct_interest_params is None
```

Remove the `hysa_type_id` variable entirely.

*`savings.py` -- Emergency fund calculation (current lines 432-451):*

Replace the hardcoded `savings_type_ids` set with the `is_liquid` metadata flag:

```python
# Before:
savings_type_ids = {
    ref_cache.acct_type_id(AcctTypeEnum.SAVINGS),
    hysa_type_id,
}
total_savings = Decimal("0.00")
for ad in account_data:
    if ad["account"].account_type_id in savings_type_ids:
        total_savings += ad["current_balance"] or Decimal("0.00")
...
savings_accounts = [
    ad["account"] for ad in account_data
    if ad["account"].account_type_id in savings_type_ids
]

# After:
total_savings = Decimal("0.00")
for ad in account_data:
    if ad["account"].account_type and ad["account"].account_type.is_liquid:
        total_savings += ad["current_balance"] or Decimal("0.00")
...
savings_accounts = [
    ad["account"] for ad in account_data
    if ad["account"].account_type and ad["account"].account_type.is_liquid
]
```

This eliminates the `savings_type_ids` frozenset and the `AcctTypeEnum.SAVINGS` import usage
in this section. Any account type with `is_liquid=True` now counts toward emergency fund and
appears in the savings goal form dropdown.

**E. Test cases:**

- **New:** `test_create_account_money_market_auto_creates_interest_params` -- Create an account
  with a Money Market type that has `has_interest=True`. Assert `InterestParams` row is created.
  Assert redirect to `/accounts/<id>/interest?setup=1`. (Requires test fixture that sets
  `has_interest=True` on Money Market type.)
- **New:** `test_create_account_hsa_auto_creates_interest_params` -- Create an HSA account.
  Assert `InterestParams` row is created (HSA now has `has_interest=True`, `has_parameters=True`).
- **New:** `test_interest_detail_accepts_any_interest_type` -- Access interest detail page with a
  Money Market account that has `has_interest=True`. Assert 200 response, not a redirect/error.
- **New:** `test_chart_dispatch_interest_type` -- Verify `_calculate_account_balances()` calls
  `calculate_balances_with_interest()` for any account with `has_interest=True` and an
  `InterestParams` row.
- **New:** `test_emergency_fund_uses_is_liquid` -- Verify that accounts with `is_liquid=True`
  (Checking, Savings, HYSA, Money Market) contribute to emergency fund total. Verify accounts
  with `is_liquid=False` (CD, loans, retirement) do not.
- **New:** `test_savings_goal_dropdown_uses_is_liquid` -- Verify the savings goal form dropdown
  lists all `is_liquid=True` accounts, not just Savings and HYSA.
- **Updated:** Existing HYSA tests continue to pass (HYSA still has `has_interest=True`).
- **Edge case:** `test_interest_detail_rejects_non_interest_type` -- Access interest detail
  with a Checking account (`has_interest=False`). Assert redirect with warning flash.
- **Edge case:** `test_has_interest_true_but_no_params_row` -- Account type has
  `has_interest=True` but no `InterestParams` row exists. Verify auto-creation on detail page
  (existing safety fallback at line 585-589).
- **Edge case:** `test_user_created_liquid_type_in_emergency_fund` -- Create a user-defined
  asset type with `is_liquid=True`. Create an account of that type with a balance. Verify it
  contributes to emergency fund calculation.

**F. Manual verification steps:**

1. Create a new HYSA account. Verify it still redirects to interest detail and auto-creates
   params. Edit APY. Verify interest projections display correctly.
2. In the database, temporarily set `has_interest=True` for Money Market:
   `UPDATE ref.account_types SET has_interest = true WHERE name = 'Money Market'`
3. Create a Money Market account. Verify it redirects to interest detail with `?setup=1`.
   Verify `InterestParams` row was auto-created. Edit APY and verify projections.
4. Navigate to the savings dashboard. Verify Money Market account shows APY display.
5. Check the balance-over-time chart. Verify Money Market uses interest projection.
6. Revert the temporary Money Market change (the proper seed update comes in a later commit
   or remains a user decision via settings UI).

**G. Downstream effects:**

- Any account type with `has_interest=True` now automatically gets interest projection support.
- Any account type with `is_liquid=True` now contributes to emergency fund and appears in
  the savings goal form dropdown.
- No changes to interest calculation logic -- `interest_projection.py` is untouched.
- No changes to `balance_calculator.py` -- it still receives params the same way.
- The savings dashboard now displays APY for any `has_interest` account, not just HYSA.
- Emergency fund calculation now includes Money Market and Checking balances (both
  `is_liquid=True` in seed data). Previously only Savings and HYSA were included.

**H. Rollback notes:**

Revert the commit. All dispatch reverts to HYSA type ID checks. No migration involved -- purely
code logic changes. Existing data unaffected.

---

#### Commit 4

**A. Commit message:**
`refactor(dispatch): replace hardcoded investment type ID sets with category-based queries`

**B. Problem statement:**
`accounts.py` and `savings.py` maintain hardcoded frozensets of 5 investment/retirement type
IDs (`K401`, `ROTH_401K`, `TRADITIONAL_IRA`, `ROTH_IRA`, `BROKERAGE`). These sets must be
manually updated by a developer to support new types. The 529 Plan is excluded despite being
structurally identical to Brokerage.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `app/routes/accounts.py` | Modified | `create_account()`: `investment_type_ids` frozenset replaced with category query |
| `app/routes/savings.py` | Modified | `retirement_type_ids` frozenset replaced with category query. `needs_setup` block fully rewritten to be metadata-driven. |
| `tests/test_routes/test_accounts.py` | Modified | Investment auto-creation tests updated |
| `tests/test_routes/test_savings.py` | Modified | Dashboard tests updated |

**D. Implementation approach:**

*`accounts.py` -- `create_account()` (current lines 141-151, 168-171):*

Replace the hardcoded frozenset and its two uses:

```python
# Before:
investment_type_ids = {
    ref_cache.acct_type_id(AcctTypeEnum.K401),
    ref_cache.acct_type_id(AcctTypeEnum.ROTH_401K),
    ref_cache.acct_type_id(AcctTypeEnum.TRADITIONAL_IRA),
    ref_cache.acct_type_id(AcctTypeEnum.ROTH_IRA),
    ref_cache.acct_type_id(AcctTypeEnum.BROKERAGE),
}
if acct_type_id in investment_type_ids:
    ...

# After:
# Auto-create InvestmentParams for retirement/investment types with parameters.
if (account_type
    and account_type.has_parameters
    and not account_type.has_interest
    and not account_type.has_amortization):
    if not db.session.query(InvestmentParams).filter_by(account_id=account.id).first():
        db.session.add(InvestmentParams(account_id=account.id))
```

The redirect block uses the same predicate:
```python
if (account_type
    and account_type.has_parameters
    and not account_type.has_interest
    and not account_type.has_amortization):
    return redirect(url_for("investment.dashboard", account_id=account.id, setup=1))
```

This predicate means: "parameterized types that are not interest-bearing and not amortizing."
By elimination, these are investment/retirement types. This is more robust than a category
check because it handles edge cases where a type's category might not match expectations.

*`savings.py` -- Investment params loading (current lines 116-132):*

Replace:
```python
# Before:
retirement_type_ids = {
    ref_cache.acct_type_id(AcctTypeEnum.K401),
    ...
}
inv_account_ids = [a.id for a in accounts if a.account_type_id in retirement_type_ids]

# After:
inv_account_ids = [
    a.id for a in accounts
    if a.account_type
    and a.account_type.has_parameters
    and not a.account_type.has_interest
    and not a.account_type.has_amortization
]
```

*`savings.py` -- Rewrite entire `needs_setup` block (current lines 299-305):*

```python
# After:
needs_setup = False
if acct.account_type and acct.account_type.has_parameters:
    if acct.account_type.has_interest:
        needs_setup = interest_params_map.get(acct.id) is None
    elif acct.account_type.has_amortization:
        needs_setup = loan_params_map.get(acct.id) is None
    else:
        needs_setup = investment_params_map.get(acct.id) is None
```

This is fully metadata-driven. The `has_parameters` outer guard ensures we only check
types that are expected to have params. The inner branches use the specific behavioral flags.

**E. Test cases:**

- **New:** `test_create_account_529_auto_creates_investment_params` -- With 529 Plan set to
  `has_parameters=True` (done in Commit 6, but can be set up in test fixture), verify
  `InvestmentParams` auto-creation and redirect.
- **New:** `test_create_account_user_type_retirement_auto_creates_params` -- Create a
  user-defined account type with `category_id=Retirement`, `has_parameters=True`. Create an
  account of that type. Assert `InvestmentParams` created and redirect to investment dashboard.
- **New:** `test_needs_setup_metadata_driven` -- For each param type (interest, loan,
  investment), verify `needs_setup=True` when params are missing and `False` when present.
- **Updated:** Existing investment account creation tests pass unchanged.
- **Edge case:** `test_has_parameters_false_no_auto_create` -- Create an account type with
  `has_parameters=False`. Verify no params created, redirect to accounts list.

**F. Manual verification steps:**

1. Create a 401(k) account. Verify `InvestmentParams` auto-created and redirect to investment
   dashboard with `?setup=1`.
2. Create a Brokerage account. Same verification.
3. On savings dashboard, verify retirement/investment accounts show return rate and "needs
   setup" badge when params are missing.

**G. Downstream effects:**

- Any retirement/investment type with `has_parameters=True` now automatically gets
  `InvestmentParams` auto-creation and investment dashboard routing.
- The `needs_setup` logic is now uniform across all three param types.
- No changes to `growth_engine.py` or `retirement_gap_calculator.py`.

**H. Rollback notes:**

Revert the commit. Dispatch reverts to hardcoded frozensets. No migration. Data unaffected.

---

#### Commit 5

**A. Commit message:**
`refactor(retirement): replace TRADITIONAL_TYPE_ENUMS frozenset with is_pretax metadata flag`

**B. Problem statement:**
`app/routes/retirement.py` defines `TRADITIONAL_TYPE_ENUMS = frozenset({AcctTypeEnum.K401,
AcctTypeEnum.TRADITIONAL_IRA})` at module level (line 51). This is used to flag accounts as
pre-tax in the retirement gap analysis (line 332). A user-created 403(b) or SEP IRA would
not be recognized as pre-tax, producing incorrect tax calculations in the gap analysis.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `app/routes/retirement.py` | Modified | Remove `TRADITIONAL_TYPE_ENUMS`. `_compute_gap_data()` queries `AccountType.is_pretax` instead. |
| `tests/test_routes/test_retirement.py` | Modified | Tests for gap analysis updated |

**D. Implementation approach:**

*`retirement.py` -- Remove module-level constant (line 51):*

Delete:
```python
TRADITIONAL_TYPE_ENUMS = frozenset({AcctTypeEnum.K401, AcctTypeEnum.TRADITIONAL_IRA})
```

*`retirement.py` -- `_compute_gap_data()` (current lines 131-132):*

Replace:
```python
# Before:
traditional_type_ids = frozenset(
    ref_cache.acct_type_id(m) for m in TRADITIONAL_TYPE_ENUMS
)

# After:
traditional_type_ids = frozenset(
    rt.id for rt in retirement_types if rt.is_pretax
)
```

Note: `retirement_types` is already loaded by the category-based query at lines 125-129.
The `is_pretax` attribute is available on those `AccountType` objects. No additional query
needed.

The usage at line 332 remains unchanged:
```python
"is_traditional": acct.account_type_id in traditional_type_ids,
```

*Remove unused imports:*

`AcctTypeEnum` may still be needed elsewhere in the file. Check. If no other references exist
in `retirement.py`, remove the import.

**E. Test cases:**

- **New:** `test_gap_analysis_user_created_pretax_type` -- Create a user-defined account type
  with `category_id=Retirement`, `has_parameters=True`, `is_pretax=True`. Create an account
  with `InvestmentParams`. Run gap analysis. Verify the account is flagged as `is_traditional`
  in the projections.
- **New:** `test_gap_analysis_posttax_type` -- Same setup but `is_pretax=False`. Verify account
  is NOT flagged as `is_traditional`.
- **Regression:** Existing 401(k) gap analysis tests pass unchanged (401(k) has
  `is_pretax=True` from seed data).
- **Edge case:** `test_gap_analysis_no_retirement_accounts` -- Verify empty projections list
  when no retirement accounts exist.

**F. Manual verification steps:**

1. Navigate to the retirement dashboard. Verify gap analysis displays correctly for existing
   401(k) and Traditional IRA accounts (unchanged behavior).
2. In the database, create a test account type: `INSERT INTO ref.account_types (name,
   category_id, has_parameters, has_amortization, has_interest, is_pretax, icon_class) VALUES
   ('403(b)', <retirement_cat_id>, true, false, false, true, 'bi-graph-up-arrow')`.
3. Create an account of that type. Set up `InvestmentParams`. Add a few transactions.
4. Navigate to retirement dashboard. Verify the 403(b) appears in the gap analysis and is
   treated as pre-tax.
5. Clean up the test data.

**G. Downstream effects:**

- `retirement_gap_calculator.py` is unaffected -- it already receives `is_traditional` as a
  boolean in the projections dict. The source of that boolean changed, but the calculator
  does not care.
- Gap analysis weighted return rate calculation is unaffected.
- Pension calculator is unaffected.

**H. Rollback notes:**

Revert the commit. The `TRADITIONAL_TYPE_ENUMS` frozenset is restored. No migration.

---

#### Commit 6

**A. Commit message:**
`feat(account-types): enable 529 Plan parameters in seed data`

**B. Problem statement:**
529 Plan has `has_parameters=False` in seed data despite being structurally identical to
Brokerage. A user who creates a 529 Plan account gets no investment parameter setup, no growth
projections, and no contribution tracking. With category-based dispatch now in place (Commit 4),
simply setting `has_parameters=True` makes 529 Plan fully functional.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `migrations/versions/<hash>_enable_529_plan_parameters.py` | Created | Data migration setting `has_parameters=True` |
| `app/ref_seeds.py` | Modified | 529 Plan tuple updated: `has_parameters` changed from `False` to `True` |

**D. Implementation approach:**

*Migration:*

Data-only migration (no schema change):
```python
def upgrade():
    op.execute(
        "UPDATE ref.account_types SET has_parameters = true WHERE name = '529 Plan'"
    )

def downgrade():
    op.execute(
        "UPDATE ref.account_types SET has_parameters = false WHERE name = '529 Plan'"
    )
```

*Seed data (`ref_seeds.py`):*

Change 529 Plan entry from `has_parameters=False` to `has_parameters=True`.

**E. Test cases:**

- **New:** `test_529_plan_auto_creates_investment_params` -- Create a 529 Plan account. Assert
  `InvestmentParams` row auto-created. Assert redirect to investment dashboard.
- **New:** `test_529_plan_growth_projection` -- Create a 529 Plan account with params. Add
  transactions. Verify growth projections appear on investment dashboard.
- **Regression:** All other account type tests unaffected.

**F. Manual verification steps:**

1. Run `flask db upgrade`. Verify migration completes.
2. Create a 529 Plan account. Verify redirect to investment dashboard with `?setup=1`.
3. Set assumed annual return to 7%. Verify growth projections display on the dashboard.
4. Navigate to savings dashboard. Verify 529 Plan appears in the Investment category with
   return rate displayed.

**G. Downstream effects:**

- 529 Plan accounts now have investment params, growth projections, and contribution tracking.
- Savings dashboard shows 529 Plan with return rate.
- Chart service includes 529 Plan in growth-based projections.
- Retirement gap analysis includes 529 Plan if category is Investment (already handled by
  category-based query in `retirement.py`).

**H. Rollback notes:**

Revert the commit, run `flask db downgrade`. 529 Plan reverts to `has_parameters=False`.
Existing 529 Plan accounts with `InvestmentParams` rows will have orphaned params -- the rows
are harmless but unused. They can be cleaned up manually if needed.

---

### Phase III: Settings UI Enhancement

---

#### Commit 7

**A. Commit message:**
`feat(settings): extend account type schemas and routes to accept metadata fields`

**B. Problem statement:**
`create_account_type()` hardcodes `category_id=AcctCategoryEnum.ASSET` for all new types.
`update_account_type()` only updates the `name` field. A user cannot configure category,
has_parameters, has_amortization, has_interest, is_pretax, icon_class, or max_term_months
through the UI. User-created account types are functionally dead-ends.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `app/schemas/validation.py` | Modified | `AccountTypeCreateSchema` and `AccountTypeUpdateSchema` gain metadata fields with validation |
| `app/routes/accounts.py` | Modified | `create_account_type()` accepts all metadata fields. `update_account_type()` accepts all metadata fields. |
| `tests/test_routes/test_accounts.py` | Modified | Account type CRUD tests updated for new fields |

**D. Implementation approach:**

*Schemas (`validation.py`):*

`AccountTypeCreateSchema`:
```python
name = fields.String(required=True, validate=validate.Length(min=1, max=30))
category_id = fields.Integer(required=True)
has_parameters = fields.Boolean(load_default=False)
has_amortization = fields.Boolean(load_default=False)
has_interest = fields.Boolean(load_default=False)
is_pretax = fields.Boolean(load_default=False)
is_liquid = fields.Boolean(load_default=False)
icon_class = fields.String(
    load_default="bi-bank",
    validate=validate.Length(max=30),
)
max_term_months = fields.Integer(
    load_default=None, allow_none=True,
    validate=validate.Range(min=1, max=600),
)
```

Add `@validates_schema` for cross-field validation:
- If `has_amortization=True`, require `category_id` to match Liability category ID.
- If `has_interest=True`, require `category_id` to match Asset category ID.
- If `is_pretax=True`, require `category_id` to match Retirement category ID.
- If `is_liquid=True`, require `category_id` to match Asset category ID.
- If `max_term_months` is set, require `has_amortization=True`.
- `has_amortization` and `has_interest` are mutually exclusive.

`AccountTypeUpdateSchema`: same fields but all optional (for partial updates).

*Routes (`accounts.py`):*

`create_account_type()`: Remove hardcoded `category_id=ASSET`. Use `data["category_id"]`
from validated schema. Pass all fields to `AccountType()` constructor:

```python
account_type = AccountType(**data)
```

`update_account_type()`: Apply all provided fields:

```python
for field in ("name", "category_id", "has_parameters", "has_amortization",
              "has_interest", "is_pretax", "is_liquid", "icon_class", "max_term_months"):
    if field in data:
        setattr(account_type, field, data[field])
```

**E. Test cases:**

- **New:** `test_create_account_type_with_category` -- POST with `category_id=Liability`,
  `has_parameters=True`, `has_amortization=True`. Assert account type created with correct flags.
- **New:** `test_create_account_type_invalid_flag_combo` -- POST with `has_amortization=True`,
  `category_id=Asset`. Assert validation error (has_amortization requires Liability).
- **New:** `test_update_account_type_metadata` -- Update an existing type to change
  `has_parameters` from False to True. Assert persisted.
- **New:** `test_create_account_type_mutual_exclusion` -- POST with both `has_interest=True`
  and `has_amortization=True`. Assert validation error.
- **Updated:** Existing create/update tests must now provide `category_id` (required field).
- **Edge case:** `test_max_term_without_amortization` -- POST with `max_term_months=120` but
  `has_amortization=False`. Assert validation error.

**F. Manual verification steps:**

1. In the browser, navigate to Settings > Account Types.
2. Attempt to create a new account type (the form will still only show the name field until
   Commit 8 adds the template fields). Use developer tools or curl to POST with all fields.
3. Verify the new type is created with the correct category and flags.
4. Verify validation errors are returned for invalid flag combinations.

**G. Downstream effects:**

- The backend now accepts all metadata fields. Combined with the dispatch unification from
  Phase II, a user-created type with the correct flags works end-to-end.
- No existing types are affected. Existing create/update form submissions continue to work
  because all new fields have defaults (except `category_id`, which is now required --
  the template must be updated in Commit 8 to include it).

**H. Rollback notes:**

Revert the commit. Schemas revert to name-only. Routes revert to hardcoded ASSET category.
No migration. No data changes.

---

#### Commit 8

**A. Commit message:**
`feat(settings): update account types template with metadata form fields`

**B. Problem statement:**
The settings UI (`_account_types.html`) only exposes the `name` field for account types. After
Commit 7, the backend accepts all metadata fields, but the UI does not surface them. Users
cannot create functional custom account types without direct database access.

**C. Files modified:**

| File | Action | What changes |
| --- | --- | --- |
| `app/templates/settings/_account_types.html` | Modified | Create and edit forms gain metadata fields with conditional visibility |
| `app/routes/accounts.py` | Modified | Pass category list and icon options to template context |
| `tests/test_routes/test_accounts.py` | Modified | Tests for settings UI rendering |

**D. Implementation approach:**

*Route context (`accounts.py`):*

In the settings routes (or wherever `_account_types.html` is rendered), pass additional
context:
```python
categories = db.session.query(AccountTypeCategory).order_by(AccountTypeCategory.id).all()
```

Also pass an `ICON_CHOICES` list of available Bootstrap icon classes (sourced from the existing
seed data icons plus a few generic options).

*Template (`_account_types.html`):*

Add to the create form (and similar structure for edit forms):

```html
<!-- Category (required dropdown) -->
<select name="category_id" id="category_id" class="form-select" required>
    {% for cat in categories %}
    <option value="{{ cat.id }}">{{ cat.name }}</option>
    {% endfor %}
</select>

<!-- Behavioral flags (checkboxes with conditional visibility) -->
<div class="form-check">
    <input type="checkbox" name="has_parameters" value="true" class="form-check-input">
    <label class="form-check-label">Has parameters</label>
</div>

<!-- Only visible when category is Liability -->
<div class="form-check" data-show-for-category="Liability">
    <input type="checkbox" name="has_amortization" value="true" class="form-check-input">
    <label class="form-check-label">Uses amortization</label>
</div>

<!-- Only visible when category is Asset -->
<div class="form-check" data-show-for-category="Asset">
    <input type="checkbox" name="has_interest" value="true" class="form-check-input">
    <label class="form-check-label">Has interest</label>
</div>

<!-- Only visible when category is Retirement -->
<div class="form-check" data-show-for-category="Retirement">
    <input type="checkbox" name="is_pretax" value="true" class="form-check-input">
    <label class="form-check-label">Pre-tax contributions</label>
</div>

<!-- Only visible when category is Asset -->
<div class="form-check" data-show-for-category="Asset">
    <input type="checkbox" name="is_liquid" value="true" class="form-check-input">
    <label class="form-check-label">Liquid (counts toward emergency fund)</label>
</div>

<!-- Only visible when has_amortization is checked -->
<div data-show-for-flag="has_amortization">
    <label>Max term (months)</label>
    <input type="number" name="max_term_months" min="1" max="600" class="form-control">
</div>

<!-- Icon selector -->
<select name="icon_class" class="form-select">
    {% for icon in icon_choices %}
    <option value="{{ icon }}">{{ icon }}</option>
    {% endfor %}
</select>
```

Add a small inline `<script>` block for conditional field visibility. On category dropdown
change: show/hide the category-specific checkboxes. On amortization checkbox change: show/hide
max_term_months. This uses standard DOM manipulation, no new JS dependencies.

For the edit form in the existing account type rows: add the same fields, pre-populated with
current values. Use hidden inputs for boolean fields when unchecked (HTML checkbox behavior --
unchecked checkboxes are not submitted).

**E. Test cases:**

- **New:** `test_settings_account_types_shows_categories` -- GET settings page. Assert category
  dropdown is present with all four categories.
- **New:** `test_settings_account_types_shows_flags` -- Assert has_parameters, has_amortization,
  has_interest, is_pretax, is_liquid checkboxes are present.
- **New:** `test_settings_create_full_type_via_form` -- POST with all fields via the form.
  Assert account type created with correct metadata.
- **New:** `test_settings_edit_type_metadata` -- POST update with changed metadata. Assert
  changes persisted.
- **Regression:** Existing settings tests pass. Existing account types display correctly.

**F. Manual verification steps:**

1. Navigate to Settings > Account Types.
2. Verify the create form now shows: Name, Category dropdown, Has Parameters checkbox,
   conditional checkboxes for Amortization/Interest/Pre-tax, Icon dropdown, Max Term field.
3. Select "Liability" category. Verify "Uses amortization" checkbox appears. Verify "Has
   interest" checkbox is hidden.
4. Select "Asset" category. Verify "Has interest" appears. Verify "Uses amortization" hidden.
5. Select "Retirement" category. Verify "Pre-tax contributions" appears.
6. Create a new type: "Money Market" (if not already existing), Asset, has_parameters=True,
   has_interest=True, icon=bi-cash-stack. Save.
7. Create a Money Market account. Verify redirect to interest detail. Set APY. Verify
   projections work.
8. Create a new type: "Solo 401(k)", Retirement, has_parameters=True, is_pretax=True,
   icon=bi-graph-up-arrow. Save.
9. Create a Solo 401(k) account. Verify redirect to investment dashboard. Set return rate.
   Navigate to retirement dashboard. Verify it appears in gap analysis as pre-tax.

**G. Downstream effects:**

This is the final commit. After this, the full end-to-end workflow works:

1. User creates account type in Settings with correct category and flags.
2. User creates an account of that type.
3. App auto-creates the correct params (or redirects to setup for loans).
4. Dashboard, charts, and gap analysis handle the type correctly.
5. No code changes were required at any step.

**H. Rollback notes:**

Revert the commit. Template reverts to name-only form. Backend (Commit 7) still accepts
metadata fields but they are not surfaced in the UI. No migration. No data changes.

---

## 4. Dependency Graph

```
Commit 1 (add columns)          -- no dependencies
Commit 2 (rename model/route)   -- no dependencies
Commit 3 (has_interest dispatch) -- depends on Commit 1 AND Commit 2
Commit 4 (category queries)     -- depends on Commit 1 (uses has_interest in predicate)
Commit 5 (is_pretax dispatch)   -- depends on Commit 1
Commit 6 (529 Plan)             -- depends on Commit 4 (category-based dispatch must be in place)
Commit 7 (schemas/routes)       -- depends on Commit 1 (new columns must exist in model)
Commit 8 (template)             -- depends on Commit 7
```

Commits 1 and 2 can be implemented in either order but both must precede Commit 3.
Commits 3, 4, and 5 are independent of each other (they touch different files/functions) but
all depend on Commit 1. Commit 6 depends on Commit 4. Commits 7 and 8 are sequential.

---

## 5. Migration Strategy

### Migration 1: Add has_interest, is_pretax, and is_liquid (Commit 1)

**Tables affected:** `ref.account_types`

**Upgrade:**
- `ALTER TABLE ref.account_types ADD COLUMN has_interest BOOLEAN NOT NULL DEFAULT false`
- `ALTER TABLE ref.account_types ADD COLUMN is_pretax BOOLEAN NOT NULL DEFAULT false`
- `ALTER TABLE ref.account_types ADD COLUMN is_liquid BOOLEAN NOT NULL DEFAULT false`
- `UPDATE ref.account_types SET has_interest = true WHERE name IN ('HYSA', 'HSA')`
- `UPDATE ref.account_types SET has_parameters = true WHERE name = 'HSA'`
- `UPDATE ref.account_types SET is_pretax = true WHERE name IN ('401(k)', 'Traditional IRA')`
- `UPDATE ref.account_types SET is_liquid = true WHERE name IN ('Checking', 'Savings', 'HYSA', 'Money Market')`

**Downgrade:**
- `ALTER TABLE ref.account_types DROP COLUMN is_liquid`
- `ALTER TABLE ref.account_types DROP COLUMN is_pretax`
- `ALTER TABLE ref.account_types DROP COLUMN has_interest`
- `UPDATE ref.account_types SET has_parameters = false WHERE name = 'HSA'`

**Data migration:** Yes -- multiple UPDATE statements set initial flag values and correct HSA's
`has_parameters`. These are also reflected in the seed script for fresh deployments.

### Migration 2: Rename hysa_params to interest_params (Commit 2)

**Tables affected:** `budget.hysa_params` (renamed to `budget.interest_params`)

**Upgrade:**
- `ALTER TABLE budget.hysa_params RENAME TO budget.interest_params`
- Rename any indexes (the unique index on `account_id` may have an auto-generated name
  containing `hysa_params` -- check and rename if so).

**Downgrade:**
- `ALTER TABLE budget.interest_params RENAME TO budget.hysa_params`
- Restore original index names.

**Data migration:** No -- table rename preserves all data.

### Migration 3: Enable 529 Plan parameters (Commit 6)

**Tables affected:** `ref.account_types` (data only)

**Upgrade:**
- `UPDATE ref.account_types SET has_parameters = true WHERE name = '529 Plan'`

**Downgrade:**
- `UPDATE ref.account_types SET has_parameters = false WHERE name = '529 Plan'`

**Data migration:** Yes -- single row update.

### Seed Script Convention

This project uses both Alembic migrations and the seed script (`scripts/seed_ref_tables.py`)
for reference data. The seed script performs upserts and is the single source of truth for fresh
deployments. Alembic migrations handle data changes for existing deployments. Both must agree.

Each commit that changes seed data also includes a data migration in Alembic. The seed script
is updated in the same commit.

---

## 6. Risk Assessment

| Commit | Risk | Blast Radius | Worst Case | Detection |
| --- | --- | --- | --- | --- |
| 1 (add columns) | **Low** | Minimal (additive + HSA has_parameters change) | Columns added with wrong defaults; HSA gains unexpected behavior | `SELECT` verification; tests |
| 2 (rename) | **Medium** | All HYSA functionality | Missed rename causes ImportError or broken backref | App fails to start; tests fail immediately |
| 3 (has_interest + is_liquid dispatch) | **Medium** | Interest projections, chart dispatch, savings dashboard, emergency fund | Wrong flag check causes HYSA to lose interest projections or emergency fund to include wrong accounts | Zero interest shown for HYSA; emergency fund total wrong; chart shows flat line |
| 4 (category queries) | **Medium** | Investment auto-creation, savings dashboard, needs_setup | Wrong predicate excludes existing investment types | 401(k) creation doesn't auto-create params; savings dashboard missing return rates |
| 5 (is_pretax dispatch) | **Low** | Retirement gap analysis tax calculation | Wrong is_pretax flag → incorrect tax treatment in gap | Gap analysis shows wrong after-tax income for traditional accounts |
| 6 (529 Plan) | **Low** | 529 Plan accounts only | 529 Plan gets investment params it didn't before | No negative impact -- purely additive functionality |
| 7 (schemas/routes) | **Low** | Account type creation via API/form | Invalid flag combinations allowed through | Bad types created via settings; caught by edge case tests |
| 8 (template) | **Low** | Settings UI only | Form fields don't match backend expectations | Visual inspection; form submission tests |

**Financial calculation safety:** No commit in this plan changes any calculation logic.
`interest_projection.py`, `amortization_engine.py`, `growth_engine.py`, `balance_calculator.py`,
`retirement_gap_calculator.py`, and `pension_calculator.py` are all untouched. Only the dispatch
paths that route accounts TO those calculators are changed. The risk is "wrong calculator for
wrong account" -- never "calculator produces wrong numbers."

---

## 7. Full Test Suite Verification Points

Run `timeout 660 pytest -v --tb=short` at these points:

| After Commit | Reason |
| --- | --- |
| 1 | Migration commit -- verify no regressions from new columns |
| 2 | Migration commit + rename touches many files -- high rename-breakage risk |
| 3 | Dispatch logic change -- core behavioral change |
| 4 | Dispatch logic change -- core behavioral change |
| 6 | Migration commit -- verify 529 Plan enablement doesn't break other types |
| 8 | Final commit -- comprehensive final gate |

Commits 5 and 7 are lower risk (isolated changes to retirement.py and schemas). Targeted test
runs are acceptable during development:
- Commit 5: `pytest tests/test_routes/test_retirement.py -v`
- Commit 7: `pytest tests/test_routes/test_accounts.py -v`

---

## 8. Relationship to Existing Plans

### Section 4 (UX/Grid Overhaul) -- Complete

Section 4 is fully complete. Task 4.4c (ref table ID-based lookups) established the
`AcctTypeEnum`/`ref_cache` pattern used throughout the codebase. Task 4.7/4.8 (Account
Parameter Setup UX) introduced the `has_parameters` and `has_amortization` metadata columns,
the post-creation redirect chain, and the auto-creation of `HysaParams` and `InvestmentParams`.

**Section 4 artifacts that this plan refactors:**

The post-creation dispatch chain in `accounts.py:create_account()` was written during Section 4
with hardcoded type ID checks for HYSA and investment types. The `has_amortization` flag was
used correctly for loan dispatch (the gold standard), but HYSA and investment types were
hardcoded. This plan replaces those hardcoded checks with metadata flags, completing the pattern
that Section 4 started.

### Section 5 (Debt and Account Improvements) -- Defunct

`docs/implementation_plan_section5.md` is now **defunct**. It was written before the loan
unification (unified `LoanParams` and `loan.py`) and references stale model names
(`MortgageParams`, `AutoLoanParams`) and stale route files (`mortgage.py`, `auto_loan.py`).
Its inventory section (3.1) lists Student Loan and Personal Loan as having no implementation,
but the unified `LoanParams` table and `loan.py` route already serve them -- they only need a
`LoanParams` row created for each specific account.

**This plan must be completed before Section 5 is rewritten.** The structural changes in this
plan (metadata-driven dispatch, InterestParams rename, is_pretax flag) change the foundation
that Section 5 builds on. A new Section 5 implementation plan should be written after this
plan is implemented.

**Section 5 tasks this plan enables:**

- **5.1 (Debt Payment Linkage):** Unaffected by this plan. The unified loan infrastructure
  already supports all debt types.
- **5.4 (Income-Relative Savings Goals):** Unaffected. Savings goals are independent of account
  type dispatch.
- **5.5 (Payoff Calculator):** Unaffected. The payoff calculator already works for any
  `has_amortization=True` account.

### Project Roadmap v4.1

The roadmap lists "Account parameter architecture refactor" as preparatory work for Section 5.
This plan delivers that prerequisite. No conflicts with the roadmap.

### Adversarial Audit Findings

| Finding | Status |
| --- | --- |
| H-04 (AccountType CRUD no user scoping) | **Not addressed.** AccountType is a shared ref table. In single-user deployment, this is not exploitable. Multi-user scoping is a larger architectural decision outside this plan's scope. |
| L-06, L-07, L-14 (DRY violations) | **Addressed.** All hardcoded type ID dispatch is eliminated. |
| OCP assessment | **Addressed.** After this plan, adding a new account type requires only a seed data entry (or settings UI creation). No code changes needed. |

---

## 9. What This Plan Does NOT Cover

**Credit Card (revolving credit):**
Excluded. Revolving credit is fundamentally different from installment debt and interest-bearing
savings. It would need its own parameter model (credit limit, APR, minimum payment percentage,
grace period) and its own balance projection logic (statement cycles, minimum payments,
utilization). This is a separate feature. Credit Card has `has_parameters=False` and
`has_amortization=False` and should remain so until a dedicated revolving credit feature is
planned.

**Checking account detail route:**
`accounts.py:checking_detail()` checks for `AcctTypeEnum.CHECKING` specifically. This is a
type-specific detail page with specific semantics (primary transaction account, no params). It
is not a DRY violation -- it is intentional single-purpose routing. Not changed.

**Multi-user AccountType scoping (H-04):**
The adversarial audit flagged that AccountType CRUD has no user scoping. This is a multi-user
concern and is out of scope for this architecture plan. Single-user deployment is not affected.

---

## 10. Self-Review Checklist

- [x] Every hardcoded type ID reference found in the grep analysis has a corresponding commit
  that eliminates it.
  - HYSA type ID checks (5 locations) → Commit 3
  - savings_type_ids frozenset (savings.py emergency fund) → Commit 3
  - investment_type_ids frozenset (accounts.py) → Commit 4
  - retirement_type_ids frozenset (savings.py) → Commit 4
  - TRADITIONAL_TYPE_ENUMS frozenset (retirement.py) → Commit 5
- [x] Every commit has all eight sections (A through H) fully completed.
- [x] No commit leaves the codebase in a broken state.
- [x] The settings UI, after all commits, allows a user to create a new account type with the
  correct category and metadata flags, and the app handles it automatically.
- [x] The plan addresses the `has_parameters` flag that is currently stored but never queried.
  After Commit 4, `has_parameters` is the outer guard in the `needs_setup` logic and the
  investment auto-creation predicate.
- [x] Every model rename (HysaParams → InterestParams) is traced through every import, backref,
  template reference, and test file (Commit 2, Section C).
- [x] Every new metadata flag has corresponding validation in the schema (Commit 7), seed data
  updates (Commits 1, 6), and migration (Commits 1, 6).
- [x] Edge cases are documented and tested: empty states, missing params rows, mismatched flags,
  user-created types with no accounts yet.
- [x] The dependency graph is consistent with the commit descriptions.
- [x] The plan does not introduce any new DRY violations, hardcoded type checks, or SOLID
  violations.
- [x] Every frozenset or hardcoded set of type IDs used for behavioral dispatch has been replaced
  with a metadata flag. The `TRADITIONAL_TYPE_ENUMS` frozenset is eliminated (Commit 5). The
  `investment_type_ids` and `retirement_type_ids` frozensets are eliminated (Commit 4). The
  `savings_type_ids` set is eliminated (Commit 3, replaced by `is_liquid`). No code-level
  constants remain for type-based dispatch.
- [x] The plan accounts for audit findings H-04 (documented as out of scope), L-06/L-07/L-14
  (addressed), and the OCP assessment (addressed).
- [x] The plan preserves all existing financial calculation correctness. No calculation service
  is modified. Only dispatch logic changes.
