> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Account Parameter Architecture Investigation

**Date:** 2026-03-29
**Status:** Analysis complete, awaiting decision

---

## Phase 0: Documentation Summary

### Key Architectural Facts by Category

**Asset:**

- HYSA is the only asset type with parameters (`has_parameters=True`).
- `HysaParams` stores `apy` (Numeric 7,5) and `compounding_frequency` (String: daily/monthly/quarterly).
- `interest_projection.py` consumes `balance, apy, compounding_frequency, period_start, period_end`.
- Money Market, CD, and HSA exist in `AcctTypeEnum` but have `has_parameters=False` -- no parameter storage, no interest projection, no dashboard.

**Liability:**

- Loans already use a **unified** `LoanParams` table (one row per amortizing account). The original per-type tables (`auto_loan_params`, `mortgage_params`) described in the v3 addendum and Section 5 plan have been replaced.
- `LoanParams` columns: `original_principal`, `current_principal`, `interest_rate`, `term_months`, `origination_date`, `payment_day`, `is_arm`, `arm_first_adjustment_months`, `arm_adjustment_interval_months`.
- Supporting feature tables (`rate_history`, `escrow_components`) link via `account_id`, not via a params FK.
- `amortization_engine.py` accesses exactly 6 attributes: `origination_date`, `term_months`, `original_principal`, `current_principal`, `interest_rate`, `payment_day`. It ignores ARM fields.
- Single route file (`loan.py`), single template set (`loan/`), single set of schemas.
- Credit Card has `has_parameters=False`, `has_amortization=False` -- no revolving credit support.

**Retirement:**

- All four types (401k, Roth 401k, Traditional IRA, Roth IRA) share a single `InvestmentParams` table.
- Discriminator is `Account.account_type_id` -- the params model has no type column.
- `growth_engine.py` is type-agnostic. Tax treatment (traditional vs. Roth) is applied at the route level via `TRADITIONAL_TYPE_ENUMS` frozenset.
- `pension_calculator.py` and `retirement_gap_calculator.py` do not read `InvestmentParams` directly -- they work with projected balances output by the growth engine.

**Investment:**

- Brokerage shares the same `InvestmentParams` table and `investment.py` routes as retirement types.
- 529 Plan has `has_parameters=False` -- no parameter storage or growth projection.

**Cross-cutting:**

- `ref.account_types` metadata: `category_id` (FK), `has_parameters` (bool), `has_amortization` (bool), `icon_class`, `max_term_months`.
- `has_amortization` is used extensively in dispatch logic (routes, services, templates, chart service).
- `has_parameters` is **not used in any dispatch logic** -- it is stored but never queried for conditional behavior.
- Account creation auto-creates params via hardcoded type ID checks (HYSA specific, investment type ID set), plus a generic `has_amortization` redirect.
- Settings UI only exposes the `name` field. Custom types are always created as `category=Asset`, `has_parameters=False`, `has_amortization=False`.

---

## Phase 2: Per-Category Analysis

### Asset Category

**1. Current parameter tables:**

One table: `budget.hysa_params`

| Column | Type | Nullable |
|---|---|---|
| id | Integer | No (PK) |
| account_id | Integer | No (FK, UNIQUE) |
| apy | Numeric(7,5) | No (default 0.04500) |
| compounding_frequency | String(10) | No (default 'daily') |
| created_at | DateTime(tz) | auto |
| updated_at | DateTime(tz) | auto |

**2. Column overlap:**

N/A -- only one type in this category has parameters. However, comparing what a CD or Money Market would need:

| Column | HYSA | CD | Money Market | HSA |
|---|---|---|---|---|
| apy | Yes | Yes | Yes | Maybe |
| compounding_frequency | Yes | Yes | Yes | Maybe |
| maturity_date | No | Yes | No | No |
| term_months | No | Yes | No | No |
| contribution_limit | No | No | No | Yes |
| contribution_limit_year | No | No | No | Yes |

HYSA and Money Market would share 100% of columns. CD would add maturity fields. HSA is a hybrid -- it earns interest but also has contribution limits like an investment account.

**3. Service interface:**

`interest_projection.calculate_interest(balance, apy, compounding_frequency, period_start, period_end)` -- takes individual values, not the params object. Could trivially work against any object with `.apy` and `.compounding_frequency`.

**4. Route logic:**

HYSA detail view is embedded in `accounts.py` (the `hysa_detail` function). It loads params, calls `calculate_balances_with_interest`, renders a template. The pattern is simple and generic -- the only HYSA-specific part is the template filename.

**5. Template overlap:**

Single template (`accounts/hysa_detail.html`). If CD and Money Market had dashboards, they would be structurally identical -- summary card, params form, projection table. The only differences would be: CD would show maturity date; Money Market would be identical to HYSA.

**6. Schema overlap:**

`HysaParamsCreateSchema` and `HysaParamsUpdateSchema` validate `apy` (Decimal, 0-100) and `compounding_frequency` (OneOf). A generic `InterestParamsSchema` would add optional `maturity_date` and `term_months` for CD.

**7. Unimplemented types:**

- **Money Market** (`has_parameters=False`): Identical to HYSA. Would need: flip `has_parameters=True`, ensure it gets `HysaParams` (or equivalent), and route to a detail view.
- **CD** (`has_parameters=False`): Same as HYSA + maturity date + term. Needs 2 additional nullable columns on the params table.
- **HSA** (`has_parameters=False`): Hybrid type. Earns interest AND has IRS contribution limits. Could use either interest params or investment params, or both.

Under the current architecture, adding Money Market support requires: updating the seed, adding the type to the HYSA-specific branch in `accounts.py`/`chart_data_service.py`, and creating a new template (or parameterizing the existing one). Under a unified architecture, it would require: updating the seed and setting a flag.

**8. DRY assessment:** **Not yet applicable** -- only one implemented type. However, the dispatch logic in `chart_data_service.py` and `accounts.py` is hardcoded to the HYSA type ID rather than using a metadata flag, which means adding any new interest-bearing type requires modifying multiple files.

**9. Open/Closed assessment:** If a user creates "Money Market" through the settings UI:
- It gets `category=Asset`, `has_parameters=False` -- no way to set it otherwise.
- Creating an account of that type works, but there's no params table, no interest projection, no dashboard.
- Functionally identical to a basic savings account.
- To fully support it: need code changes in accounts.py (auto-create params), chart_data_service.py (add to interest calculation dispatch), savings.py (add to HYSA loading block), plus a template. **This violates OCP.**

---

### Liability Category

**1. Current parameter tables:**

One shared table: `budget.loan_params` (serves Mortgage, Auto Loan, Student Loan, Personal Loan, HELOC)

| Column | Type | Nullable | Purpose |
|---|---|---|---|
| id | Integer | No (PK) | |
| account_id | Integer | No (FK, UNIQUE) | One-to-one link |
| original_principal | Numeric(12,2) | No | Contractual payment calc |
| current_principal | Numeric(12,2) | No | Remaining balance |
| interest_rate | Numeric(7,5) | No | Annual rate (decimal) |
| term_months | Integer | No | Original loan term |
| origination_date | Date | No | For remaining months calc |
| payment_day | Integer | No | 1-31, day of month |
| is_arm | Boolean | No (default false) | ARM flag |
| arm_first_adjustment_months | Integer | Yes | ARM only |
| arm_adjustment_interval_months | Integer | Yes | ARM only |
| created_at | DateTime(tz) | auto | |
| updated_at | DateTime(tz) | auto | |

Supporting feature tables (linked via `account_id`, not params FK):
- `budget.rate_history`: `account_id`, `effective_date`, `interest_rate`, `notes`
- `budget.escrow_components`: `account_id`, `name`, `annual_amount`, `inflation_rate`, `is_active`

**2. Column overlap:**

100% shared across all five loan types. ARM columns are nullable and unused for non-ARM loans. Escrow and rate history are available to any loan type via feature tables.

**3. Service interface:**

`amortization_engine.get_loan_projection(params)` expects: `.origination_date`, `.term_months`, `.original_principal`, `.current_principal`, `.interest_rate`, `.payment_day`. Type-agnostic. Works identically for all loan types.

**4. Route logic:**

Single `loan.py` with functions: `dashboard()`, `create_params()`, `update_params()`, `add_rate_change()`, `add_escrow()`, `delete_escrow()`, `payoff_calculate()`. All functions use `account_type.has_amortization` as the access guard. No type-specific branching inside the route logic.

**5. Template overlap:**

One template set (`loan/dashboard.html`, `loan/setup.html`, partials). Dashboard uses tabs: Overview, Escrow (shown for any loan), Rate History (shown if `is_arm`), Payoff Calculator. No per-type templates.

**6. Schema overlap:**

One `LoanParamsCreateSchema`, one `LoanParamsUpdateSchema`. Shared across all loan types.

**7. Unimplemented types:**

- **HELOC** (`has_parameters=True`, `has_amortization=True`): Seeded and ready. A user can create an account of type HELOC, it will redirect to the loan setup flow, and everything works. No additional code needed.
- **Credit Card** (`has_parameters=False`, `has_amortization=False`): Revolving credit -- fundamentally different from installment loans. No parameter support. Not a gap in the current architecture -- it's a genuinely different feature that would need its own parameter model (APR, credit limit, minimum payment formula).

**8. DRY assessment:** **Excellent.** This category is the model for how parameterized account types should work. One table, one route, one template set, one schema set, type-agnostic services. Metadata flags drive behavior.

**9. Open/Closed assessment:** If a user creates "Home Equity Loan" through the settings UI and someone sets `has_amortization=True` and `category=Liability`:
- Account creation redirects to loan setup.
- LoanParams creation works.
- Dashboard, payoff calculator, charts all work.
- **Zero code changes needed** (assuming the metadata flags are set correctly).

The gap is that the settings UI cannot set `has_amortization=True` or `category=Liability`. The UI only accepts a name.

---

### Retirement Category

**1. Current parameter tables:**

Shared `budget.investment_params` (also used by Investment category):

| Column | Type | Nullable | Purpose |
|---|---|---|---|
| id | Integer | No (PK) | |
| account_id | Integer | No (FK, UNIQUE) | One-to-one link |
| assumed_annual_return | Numeric(7,5) | No (default 0.07) | Expected growth rate |
| annual_contribution_limit | Numeric(12,2) | Yes | IRS cap |
| contribution_limit_year | Integer | Yes | Year the limit applies |
| employer_contribution_type | String(20) | No (default 'none') | none/flat_percentage/match |
| employer_flat_percentage | Numeric(5,4) | Yes | |
| employer_match_percentage | Numeric(5,4) | Yes | |
| employer_match_cap_percentage | Numeric(5,4) | Yes | |
| created_at | DateTime(tz) | auto | |
| updated_at | DateTime(tz) | auto | |

**2. Column overlap:**

All four retirement types use 100% of the same columns. The difference between 401k and IRA is not structural -- it's domain-level (401k has employer match; IRA doesn't, but the columns are there for both).

**3. Service interface:**

`growth_engine.project_balance(current_balance, assumed_annual_return, periods, periodic_contribution, employer_params, annual_contribution_limit, ytd_contributions_start)` -- takes individual values extracted from params by the route. Type-agnostic.

`retirement_gap_calculator.calculate_gap(...)` -- works with projected balances from the growth engine and an `is_traditional` flag. Does not read InvestmentParams directly.

**4. Route logic:**

`investment.py`: `dashboard()`, `growth_chart()`, `update_params()` -- identical behavior for all types.

`retirement.py`: `_compute_gap_data()` loads all retirement/investment accounts, projects each forward, passes to gap calculator. Uses `TRADITIONAL_TYPE_ENUMS = frozenset({AcctTypeEnum.K401, AcctTypeEnum.TRADITIONAL_IRA})` for tax treatment -- this is a hardcoded type ID set.

**5. Template overlap:**

`investment/dashboard.html` -- identical for all types.
`retirement/dashboard.html` + partials -- shows all retirement/investment accounts together. Badge shows "Traditional" vs "Roth/Taxable" based on account type.

**6. Schema overlap:**

One `InvestmentParamsCreateSchema`, one `InvestmentParamsUpdateSchema`. Shared.

**7. Unimplemented types:**

None for retirement -- all four enum members are fully supported.

**8. DRY assessment:** **Excellent.** Single table, single route set, single template set, type-agnostic services.

**9. Open/Closed assessment:** If a user creates "403(b)" through the settings UI:
- It gets `category=Asset`, `has_parameters=False` -- wrong category, no params.
- Even if category were correct: the hardcoded `investment_type_ids` set in `accounts.py` (line 142-148) and `savings.py` (line 117-123) would not include it.
- The `TRADITIONAL_TYPE_ENUMS` set in `retirement.py` would not include it.
- **Code changes needed in 3+ files.** This violates OCP.

---

### Investment Category

**1-6:** Same as Retirement -- shares `investment_params` table, routes, templates, schemas.

**7. Unimplemented types:**

- **529 Plan** (`has_parameters=False`): Exists in enum but has no parameter support. A 529 shares the same growth characteristics as other investment accounts (assumed return, contribution limit). It should arguably share `investment_params`.

**8. DRY assessment:** **Excellent** for the implemented type (Brokerage). One gap: 529 Plan is excluded despite being structurally identical.

**9. Open/Closed assessment:** Same issue as Retirement. Adding a new investment type requires modifying the hardcoded type ID set in accounts.py and savings.py.

---

### Cross-Cutting Analysis

**10. Balance calculator coupling:**

The balance calculator itself (`balance_calculator.py`) has three entry points:
- `calculate_balances()` -- generic, no params needed
- `calculate_balances_with_interest()` -- needs object with `.apy` and `.compounding_frequency`
- `calculate_balances_with_amortization()` -- needs object with `.interest_rate`, `.term_months`, `.origination_date`, `.payment_day`, `.current_principal`, `.original_principal`

The dispatch (which entry point to call) happens in two places:

1. **`chart_data_service._calculate_account_balances()`** (lines 224-239):
   - `if acct_type_id == HYSA and account.hysa_params` → interest
   - `if account_type.has_amortization` → amortization
   - else → basic

2. **`savings.py` dashboard** (lines 194-209):
   - `if acct_hysa_params` → interest
   - else → basic (loans get amortization projection from engine directly)

The chart service dispatch is partially generic (`has_amortization` flag) and partially hardcoded (HYSA type ID). The savings dashboard uses a third approach (check if params object exists).

**11. Account creation redirect chain** (`accounts.py` lines 136-173):

```
1. if type_id == HYSA:           → auto-create HysaParams, redirect to hysa_detail
2. if type_id in {K401, ROTH_401K, TRADITIONAL_IRA, ROTH_IRA, BROKERAGE}:
                                  → auto-create InvestmentParams, redirect to investment dashboard
3. if account_type.has_amortization:
                                  → redirect to loan setup (LoanParams created there)
4. else:                          → redirect to accounts list
```

Branch 1 is hardcoded to one type. Branch 2 is hardcoded to 5 specific types. Branch 3 is metadata-driven (generic). Branch 4 is the fallback.

**12. Savings dashboard loading blocks** (`savings.py` lines 100-171):

Three separate loading strategies:
1. **HYSA params** (lines 100-108): Filter accounts by `acct_type_id == HYSA`, then batch-load HysaParams.
2. **Loan params** (lines 110-113, 165-171): Filter by `has_amortization=True` (generic!), then batch-load LoanParams.
3. **Investment params** (lines 115-132): Hardcoded set of 5 type IDs, then batch-load InvestmentParams + PaycheckDeductions.

Block 2 is the gold standard -- metadata-driven, zero hardcoded types. Blocks 1 and 3 require code changes to support new types.

**13. Settings UI gap:**

**What users CAN configure:** Name only.

**What users CANNOT configure:** Category, has_parameters, has_amortization, icon_class, max_term_months.

**Trace: User creates "CD" account type:**
1. Account type created with `name="CD"`, `category_id=Asset`, `has_parameters=False`, `has_amortization=False`.
2. User creates an account of type "CD".
3. `create_account()` runs. No branch matches (not HYSA, not in investment set, not has_amortization). No params created.
4. Redirects to accounts list.
5. Account shows in savings dashboard with basic balance calculation. No interest projection, no dashboard, no setup page.
6. **Dead end.** The account exists but has no financial intelligence.

**Trace: User creates "Business Loan" account type:**
1. Created as `name="Business Loan"`, `category_id=Asset` (wrong!), `has_parameters=False`, `has_amortization=False`.
2. User creates an account. No params, no setup redirect.
3. Savings dashboard shows basic balance. No amortization, no payoff calculator.
4. **Double dead end** -- wrong category AND no parameters.

**Trace: User creates "Crypto Wallet" account type:**
1. Created as `name="Crypto Wallet"`, `category_id=Asset`, `has_parameters=False`.
2. Same dead-end as CD. No growth projection capability.
3. Even if it had InvestmentParams-like data, the savings dashboard would not include it because it is not in the hardcoded investment type ID set.

---

## Phase 3: Options by Category

### Liability Category: No Changes Needed

This category is already the gold standard. Single table, metadata-driven dispatch, type-agnostic services, generic routes and templates. Adding a new installment loan type requires only a seed/enum entry with `has_amortization=True`. Credit Card (revolving) is a genuinely different feature -- not a gap in this architecture.

### Asset Category: Three Options

**Option A: Rename and generalize `hysa_params` to `interest_params`**

Rename the table and model to `InterestParams`. Add nullable columns for CD and Money Market support. Add a `has_interest` boolean flag to `ref.account_types`.

Table changes:
```
budget.interest_params (renamed from hysa_params)
  id, account_id, apy, compounding_frequency   -- existing
  + maturity_date (Date, nullable)              -- for CDs
  + term_months (Integer, nullable)             -- for CDs
```

Code changes:
- Rename model: `HysaParams` -> `InterestParams` (1 model file, all references)
- Add `has_interest` flag to `ref.AccountType` model + migration
- Update seed data: set `has_interest=True` for HYSA, Money Market, CD
- Replace hardcoded HYSA type ID checks with `has_interest` flag in:
  - `accounts.py` create_account (auto-create InterestParams)
  - `chart_data_service.py` _calculate_account_balances
  - `savings.py` loading block
- Rename template: `hysa_detail.html` -> parameterized `interest_detail.html`
- Update schemas: rename `HysaParamsCreateSchema` -> `InterestParamsCreateSchema`
- Alembic migration: rename table, add columns, add flag

Files created: 0
Files modified: ~12 (model, routes, services, schemas, templates, migration, seeds, enums)
Files deleted: 0

Effort: **Medium**

DRY/OCP: Matches the liability pattern. New interest-bearing types need only a seed entry with `has_interest=True`.

**Option B: Keep `hysa_params` table name, add the flag only**

Same as Option A but skip the rename. Add `has_interest` to `ref.account_types`. Replace hardcoded HYSA checks with `has_interest` flag. Keep the table named `hysa_params` even though it now serves multiple types.

Effort: **Low**

DRY/OCP: Same benefits as A. Downside: table name is misleading.

**Option C: Create per-type tables (CD params, Money Market params, etc.)**

Copy the pattern from `hysa_params` for each new type. This is what the original Section 5 plan proposed for loans before the unification.

Effort: **Low per type, high cumulative**

DRY/OCP: Violates DRY. Each new type needs a new model + route + template + schema + migration. Exactly the pattern that was already rejected for loans.

**Recommendation for Asset: Option A.** It follows the proven liability pattern. The rename makes intent clear. The effort is moderate but one-time.

### Retirement/Investment Category: One Improvement Needed

The data model is already unified. The DRY violation is in the **dispatch logic**, not the schema. Hardcoded type ID sets in three files must be replaced with metadata-driven lookups.

**Option D: Replace hardcoded investment type ID sets with `category_id` + `has_parameters` flag**

The `investment_type_ids` set in `accounts.py` and `retirement_type_ids` set in `savings.py` can be replaced with:
```python
# All types in Retirement or Investment category with has_parameters=True
investment_type_ids = {
    at.id for at in db.session.query(AccountType)
    .filter(
        AccountType.category_id.in_([
            ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
            ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT),
        ]),
        AccountType.has_parameters.is_(True),
    ).all()
}
```

The `TRADITIONAL_TYPE_ENUMS` frozenset in `retirement.py` is a domain-level distinction (pre-tax vs post-tax) that could be expressed as a boolean column on `ref.account_types` (e.g., `is_pretax`), but this is a minor point -- there are exactly two traditional types and the set is unlikely to change.

Code changes:
- `accounts.py`: Replace `investment_type_ids` set with category-based query
- `savings.py`: Replace `retirement_type_ids` set with category-based query
- `retirement.py`: Optionally replace `TRADITIONAL_TYPE_ENUMS` with metadata flag

Files modified: 2-3
Effort: **Low**

DRY/OCP: New retirement/investment types work automatically if they have the right `category_id` and `has_parameters=True`.

**Option E: Also add 529 Plan to `has_parameters=True`**

529 Plan shares the same growth characteristics as other investment accounts. Update its seed to `has_parameters=True`. With Option D in place, it would automatically get InvestmentParams auto-creation and growth projection.

Effort: **Trivial** (one seed change)

**Recommendation: Options D + E.** Low effort, high impact.

### Settings UI: Required Improvement

Regardless of which category options are chosen, the settings UI must expose more fields for custom account types. Without this, the promise of user-extensible types is hollow.

**Option F: Enhanced settings UI for account type management**

Add the following fields to the create/edit account type form:
- **Category** (dropdown: Asset, Liability, Retirement, Investment) -- required
- **Has parameters** (checkbox) -- default false
- **Has amortization** (checkbox, shown only if Liability category) -- default false
- **Has interest** (checkbox, shown only if Asset category) -- default false (if Option A is adopted)
- **Max term months** (number input, shown only if has_amortization) -- optional
- **Icon** (dropdown from Bootstrap Icons subset) -- optional

The update form should also expose these fields (with appropriate guards -- e.g., cannot change category if accounts of this type exist).

Code changes:
- `accounts.py`: Update `create_account_type()` and `update_account_type()` to accept new fields
- `validation.py`: Update `AccountTypeCreateSchema` and `AccountTypeUpdateSchema`
- `_account_types.html`: Add form fields with conditional visibility (HTMX or JS)
- Add a migration if any new columns are introduced (e.g., `has_interest`)

Files modified: ~4
Effort: **Medium**

### Cross-Cutting: Dispatch Unification

**Option G: Unify dispatch logic using metadata flags**

After Options A, D, and F are in place, the dispatch logic becomes fully metadata-driven:

Account creation auto-params (`accounts.py`):
```
if has_interest      → create InterestParams with defaults
if has_parameters and category in (Retirement, Investment) → create InvestmentParams with defaults
if has_amortization  → redirect to loan setup
else                 → redirect to accounts list
```

Chart service dispatch (`chart_data_service.py`):
```
if has_interest and interest_params  → calculate_balances_with_interest
if has_amortization and loan_params  → calculate_balances_with_amortization
else                                 → calculate_balances
```

Savings dashboard (`savings.py`):
```
interest accounts:   filter by has_interest flag
loan accounts:       filter by has_amortization flag (already done!)
investment accounts: filter by category + has_parameters flag
```

No hardcoded type IDs anywhere in dispatch logic.

Effort: **Low-Medium** (most of this falls out of Options A and D)

---

## Phase 4: Recommendation

### Per-Category Verdict

| Category | Verdict | Action |
|---|---|---|
| **Liability** | **No changes needed** | Already unified. Single LoanParams table, metadata-driven dispatch, type-agnostic services. Gold standard. |
| **Retirement** | **Minor improvements** | Replace hardcoded type ID sets with category-based queries (Option D). |
| **Investment** | **Minor improvements** | Same as Retirement (Option D). Enable 529 Plan params (Option E). |
| **Asset** | **Significant rework** | Rename and generalize HysaParams to InterestParams (Option A). Add `has_interest` flag. |
| **Cross-cutting** | **Significant rework** | Enhanced settings UI (Option F). Unified dispatch (Option G). |

### Sequencing

**Phase I: Foundation (do first -- unlocks everything else)**

1. Add `has_interest` boolean column to `ref.account_types` model + migration.
2. Update seed data: `has_interest=True` for HYSA, Money Market, CD.
3. Rename `hysa_params` table/model to `interest_params`. Add nullable CD columns (`maturity_date`, `term_months`).
4. Update all references to `HysaParams` -> `InterestParams` (model imports, backrefs, schemas, routes, templates, tests).
5. Replace hardcoded HYSA type ID checks with `has_interest` flag (accounts.py, chart_data_service.py, savings.py).
6. Replace hardcoded investment type ID sets with category-based queries (accounts.py, savings.py).
7. Update 529 Plan seed: `has_parameters=True`.

This phase produces no user-visible changes (existing functionality preserved) but removes all hardcoded type ID dispatch.

**Phase II: Settings UI enhancement**

8. Extend `AccountTypeCreateSchema` and `AccountTypeUpdateSchema` to accept `category_id`, `has_parameters`, `has_amortization`, `has_interest`, `max_term_months`.
9. Update `create_account_type()` and `update_account_type()` to persist new fields.
10. Update `_account_types.html` with conditional form fields.
11. Add appropriate validation guards (e.g., `has_amortization` requires `category=Liability`).

This phase makes user-created account types functional.

**Phase III: Enable unimplemented types (can be done incrementally)**

12. Enable Money Market: set `has_parameters=True`, `has_interest=True` in seed. (No code changes -- auto-creates InterestParams, gets interest projection.)
13. Enable CD: same as Money Market, plus template shows maturity date if present.
14. Generalize HYSA detail template into `interest_detail.html` that conditionally shows maturity fields.

**Dependency order:** Phase I must be complete before Phase II. Phase III can happen incrementally after Phase I.

### Blast Radius Assessment

**What survives intact:**
- Amortization engine -- untouched
- Growth engine -- untouched
- Interest projection service -- untouched (it takes individual params, not the model)
- Balance calculator core logic -- untouched (only the callers change)
- All loan functionality -- untouched
- All investment/retirement functionality -- untouched (just how types are detected changes)
- Transfer service -- untouched

**What changes:**
- `HysaParams` model renamed to `InterestParams` (one model file + all import sites)
- `ref.account_types` gains one column (`has_interest`)
- Dispatch logic in 3 files (accounts.py, chart_data_service.py, savings.py) switches from hardcoded IDs to metadata flags
- Settings UI template and schemas gain new fields
- All existing tests must update `HysaParams` references to `InterestParams`

**What could break:**
- Any test that imports `HysaParams` by name -- mechanical rename fix
- Any template that references `account.hysa_params` -- backref rename in model
- Savings dashboard investment loading -- minor query change

**Risk:** Low. The model rename is the largest change and it is purely mechanical. The dispatch logic changes are straightforward flag checks replacing hardcoded ID sets. No financial calculation logic changes at all.

### Test Impact

- Existing loan tests: **no changes**
- Existing investment/retirement tests: **no changes** (the query that finds investment types changes, but the behavior is identical for seeded types)
- Existing HYSA tests: **mechanical rename** (HysaParams -> InterestParams, hysa_params backref -> interest_params)
- New tests needed:
  - Verify `has_interest` flag drives InterestParams auto-creation
  - Verify category-based investment type detection includes all seeded types
  - Verify user-created account type with proper flags gets full functionality
  - Verify settings UI creates account types with correct metadata

### Summary

The codebase is already 80% of the way to a fully extensible account parameter architecture. The liability category is the proven model. The main gaps are:

1. **HYSA is hardcoded** where it should use a metadata flag (like `has_amortization` does for loans).
2. **Investment types are hardcoded** where they should use category-based queries.
3. **The settings UI is a dead end** for custom types -- it exposes only the name field.

The proposed changes are:
- One new boolean column on `ref.account_types` (`has_interest`)
- One table/model rename (`hysa_params` -> `interest_params`)
- Dispatch logic switches from hardcoded type IDs to metadata flags (3 files)
- Settings UI gains category and flag fields (3-4 files)

After these changes, a user can create a new account type through the settings UI, set its category and flags, and the system automatically provides the correct parameter table, calculation service, and dashboard -- with zero code changes.
