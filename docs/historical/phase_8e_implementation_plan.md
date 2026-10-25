> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8E: Multi-User Groundwork -- Implementation Plan

## 1. Overview

This plan implements Sub-Phase 8E from the Phase 8 Hardening & Ops Plan. It covers user registration, a comprehensive user_id query audit across all routes and services, route-level authorization hardening, and data isolation integration tests. The goal is to verify and enforce that every user sees only their own data -- no role system, no admin features, no kid accounts.

**Pre-existing infrastructure discovered during planning:**

- The seed script (`scripts/seed_user.py`) is **already idempotent** -- it checks for an existing user by email at line 66 and skips creation if found. No changes needed for item 4 of the master plan.
- The `_get_owned_transaction()` helper already exists in `app/routes/transactions.py:39-53`. It loads a transaction by ID, verifies ownership via `txn.pay_period.user_id != current_user.id`, and returns None if unauthorized. This is the exact pattern needed for other indirectly scoped models.
- The codebase already has **strong user_id discipline**. The vast majority of route-level queries already filter by `user_id=current_user.id` (direct) or verify ownership after loading by PK (indirect). The audit found no critical unscoped queries at the route level -- only defense-in-depth gaps in service-layer functions and one minor edge case in reference table management.
- The `auth_service.py` module has `hash_password()`, `verify_password()`, `authenticate()`, and `change_password()` functions. No `create_user()` function exists -- user creation is handled directly in the seed script. Registration will need a new service function.
- Password validation enforces a 12-character minimum in `auth_service.change_password()` (line 79). Registration must use the same validation.
- The `conftest.py` test fixtures create a single user with checking account, baseline scenario, and 5 categories. The isolation tests need a second user factory that follows this same pattern.
- Tax configuration tables (`salary.tax_bracket_sets`, `salary.fica_configs`, `salary.state_tax_configs`) have `user_id` columns -- they are per-user configurations, not shared reference data as suggested in the master plan. Existing queries already filter by `user_id`. New users will need to set up their own tax configs via the UI (the app handles missing configs gracefully -- paycheck calculations return zero tax when no config exists).

**New dependencies required:** None. All required packages are already installed.

**Alembic migration required:** None. All relevant tables already have `user_id` columns.

---

## 2. Pre-Existing Infrastructure

### 2.1 Authentication Service (`app/services/auth_service.py`)

| Function | Line | Purpose | Relevant to 8E |
|----------|------|---------|-----------------|
| `hash_password(plain_password)` | 15-26 | Bcrypt hash generation | Used by registration |
| `verify_password(plain_password, password_hash)` | 29-42 | Bcrypt verification | Not directly |
| `authenticate(email, password)` | 45-63 | Email/password login | Not directly |
| `change_password(user, current_password, new_password)` | 66-85 | Password change with 12-char min | Registration reuses the 12-char validation |

**No `create_user()` function exists.** The seed script creates users directly via ORM. Registration needs a new `register_user()` function in `auth_service.py` that handles: email uniqueness check, password validation, bcrypt hashing, User creation, UserSettings creation, and baseline Scenario creation.

### 2.2 Auth Routes (`app/routes/auth.py`)

The auth blueprint has 10 route handlers: login, logout, change-password, invalidate-sessions, mfa/verify, mfa/setup, mfa/confirm, mfa/regenerate-backup-codes, mfa/disable (GET), mfa/disable (POST). Registration routes will be added after the login route (GET + POST at `/register`).

### 2.3 Login Template (`app/templates/auth/login.html`)

A centered card layout with the Shekel logo, email/password fields, "Remember me" checkbox, and "Sign In" button. The registration page must follow this exact visual pattern and include a "Create an account" link. The login page needs a reciprocal "Already have an account?" link.

### 2.4 Seed Script (`scripts/seed_user.py`)

**Already idempotent.** Line 66-69 checks for existing user by email and returns early:

```python
existing = db.session.query(User).filter_by(email=email).first()
if existing:
    print(f"User '{email}' already exists (id={existing.id}).  Skipping.")
    return existing
```

Creates: User, UserSettings, Checking Account (anchor balance 0), Baseline Scenario, 22 default categories. Environment variables control email/password/display_name.

### 2.5 Test Fixtures (`tests/conftest.py`)

- **`seed_user` (lines 150-213):** Creates test user (`test@shekel.local` / `testpass`), UserSettings, Checking account (balance 1000.00), Baseline scenario, 5 categories (Salary, Rent, Car Payment, Groceries, Payback). Returns dict with all objects.
- **`seed_periods` (lines 216-240):** Generates 10 pay periods starting 2026-01-02 (14-day cadence). Sets anchor period.
- **`auth_client` (lines 243-253):** Logs in via POST to `/login` and returns authenticated client.

The isolation tests need a `seed_second_user` fixture and a `second_auth_client` fixture that create an independent dataset.

### 2.6 Ownership Verification Patterns

Two patterns are used throughout the codebase:

**Pattern A -- Direct filter:** Used when loading lists. Queries include `user_id=current_user.id` in the filter.
```python
accounts = db.session.query(Account).filter_by(user_id=current_user.id).all()
```

**Pattern B -- Load-then-check:** Used when loading by PK from a URL parameter. Loads the object, then verifies ownership.
```python
account = db.session.get(Account, account_id)
if account is None or account.user_id != current_user.id:
    flash("Account not found.", "danger")
    return redirect(url_for("accounts.list_accounts"))
```

**Pattern C -- Helper function:** Used for indirectly scoped objects in `transactions.py:39-53`:
```python
def _get_owned_transaction(txn_id):
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        return None
    if txn.pay_period.user_id != current_user.id:
        return None
    return txn
```

For 8E, Pattern B and C should be consolidated into reusable helper functions to eliminate repetition and reduce the risk of missed checks.

### 2.7 Application Configuration (`app/config.py`)

- No configurable minimum password length -- the 12-character minimum is hardcoded in `auth_service.change_password()` line 79. Registration will use the same hardcoded value for consistency.
- `WTF_CSRF_ENABLED = False` in TestConfig (line 48) -- test forms don't need CSRF tokens.
- `RATELIMIT_ENABLED = False` in TestConfig -- rate limiting is disabled in tests.

### 2.8 Exceptions (`app/exceptions.py`)

Existing exception hierarchy:
- `ShekelError` (base)
- `AuthError` -- authentication/authorization failures
- `ValidationError` -- input validation failures
- `NotFoundError` -- resource not found
- `ConflictError` -- duplicate or conflicting state

Registration will use `AuthError` for duplicate email and `ValidationError` for invalid input.

---

## 3. Table Inventory

### 3.1 Reference Tables (Shared -- No user_id filter needed)

| Schema | Table | Has user_id | Scoping | Notes |
|--------|-------|-------------|---------|-------|
| ref | account_types | No | Shared | Account type lookup (checking, savings, etc.) |
| ref | transaction_types | No | Shared | income, expense |
| ref | statuses | No | Shared | projected, done, received, credit, cancelled |
| ref | recurrence_patterns | No | Shared | every_period, monthly, annual, etc. |
| ref | filing_statuses | No | Shared | single, married_jointly, etc. |
| ref | deduction_timings | No | Shared | pre_tax, post_tax |
| ref | calc_methods | No | Shared | flat, percentage |
| ref | tax_types | No | Shared | flat, none, bracket |
| ref | raise_types | No | Shared | merit, cola, custom |

**9 tables.** All read-only lookup data. Any user can access. Excluded from audit.

### 3.2 Authentication Tables (Direct user_id)

| Schema | Table | Has user_id | Scoping | Notes |
|--------|-------|-------------|---------|-------|
| auth | users | N/A | IS the user table | PK is user_id |
| auth | user_settings | Yes | Direct | FK to auth.users.id, unique per user |
| auth | mfa_configs | Yes | Direct | FK to auth.users.id, unique per user |

**3 tables.** All have direct user_id or are the user table itself.

### 3.3 Budget Tables (Direct user_id)

| Schema | Table | Has user_id | Scoping | Notes |
|--------|-------|-------------|---------|-------|
| budget | pay_periods | Yes | Direct | FK to auth.users.id |
| budget | accounts | Yes | Direct | FK to auth.users.id |
| budget | categories | Yes | Direct | FK to auth.users.id |
| budget | scenarios | Yes | Direct | FK to auth.users.id |
| budget | recurrence_rules | Yes | Direct | FK to auth.users.id |
| budget | transaction_templates | Yes | Direct | FK to auth.users.id |
| budget | transfer_templates | Yes | Direct | FK to auth.users.id |
| budget | transfers | Yes | Direct | FK to auth.users.id |
| budget | savings_goals | Yes | Direct | FK to auth.users.id |

**9 tables** with direct user_id columns. Queries must filter by `user_id`.

### 3.4 Budget Tables (Indirect via FK chain)

| Schema | Table | Has user_id | Scoping | FK Chain to user_id |
|--------|-------|-------------|---------|---------------------|
| budget | transactions | No | Indirect via pay_period | `transactions.pay_period_id` → `pay_periods.user_id` |
| budget | account_anchor_history | No | Indirect via account | `account_anchor_history.account_id` → `accounts.user_id` |
| budget | hysa_params | No | Indirect via account | `hysa_params.account_id` → `accounts.user_id` |
| budget | mortgage_params | No | Indirect via account | `mortgage_params.account_id` → `accounts.user_id` |
| budget | mortgage_rate_history | No | Indirect via account | `mortgage_rate_history.account_id` → `accounts.user_id` |
| budget | escrow_components | No | Indirect via account | `escrow_components.account_id` → `accounts.user_id` |
| budget | auto_loan_params | No | Indirect via account | `auto_loan_params.account_id` → `accounts.user_id` |
| budget | investment_params | No | Indirect via account | `investment_params.account_id` → `accounts.user_id` |

**8 tables** without direct user_id. Every route that loads these must either: (a) load the parent first and verify ownership, then load the child by parent FK, or (b) join to the parent and filter by user_id.

### 3.5 Salary Tables (Direct user_id)

| Schema | Table | Has user_id | Scoping | Notes |
|--------|-------|-------------|---------|-------|
| salary | salary_profiles | Yes | Direct | FK to auth.users.id |
| salary | pension_profiles | Yes | Direct | FK to auth.users.id |
| salary | tax_bracket_sets | Yes | Direct | Per-user tax config (NOT shared ref data) |
| salary | state_tax_configs | Yes | Direct | Per-user state tax config |
| salary | fica_configs | Yes | Direct | Per-user FICA config |

**5 tables** with direct user_id.

### 3.6 Salary Tables (Indirect via FK chain)

| Schema | Table | Has user_id | Scoping | FK Chain to user_id |
|--------|-------|-------------|---------|---------------------|
| salary | salary_raises | No | Indirect via salary_profile | `salary_raises.salary_profile_id` → `salary_profiles.user_id` |
| salary | paycheck_deductions | No | Indirect via salary_profile | `paycheck_deductions.salary_profile_id` → `salary_profiles.user_id` |
| salary | tax_brackets | No | Indirect via bracket_set | `tax_brackets.bracket_set_id` → `tax_bracket_sets.user_id` |

**3 tables** without direct user_id. Routes access these through their parent records.

### 3.7 System Tables

| Schema | Table | Has user_id | Scoping | Notes |
|--------|-------|-------------|---------|-------|
| system | audit_log | Yes | System | Already has user_id column. Excluded from audit. |

**1 table.** System-level, not user-facing.

### 3.8 Tax Configuration Classification Correction

The master plan states "salary.tax_bracket_sets, salary.tax_brackets, salary.fica_configs, salary.state_tax_configs are reference data." This is **incorrect**. These tables have `user_id` columns and contain per-user tax configurations:

- `tax_bracket_sets`: Has `user_id` (FK to auth.users.id). Unique constraint on `(user_id, tax_year, filing_status_id)`.
- `fica_configs`: Has `user_id` (FK to auth.users.id). Unique constraint on `(user_id, tax_year)`.
- `state_tax_configs`: Has `user_id` (FK to auth.users.id). Unique constraint on `(user_id, state_code)`.
- `tax_brackets`: No `user_id` but indirectly scoped via `bracket_set_id` → `tax_bracket_sets.user_id`.

These are seeded by `scripts/seed_tax_brackets.py` for a specific user. They function as user-specific reference data -- each user needs their own copy. Existing routes already filter by `user_id`. These tables are **included** in the audit scope.

---

## 4. Query Audit Results

This section documents every database query in the application, organized by blueprint/service module. For each module:
- **Confirmed Safe** queries are listed briefly with their filtering method.
- **Needs Fix** queries include current code, required fix, and risk level.
- **Safe Despite No user_id** queries explain why they're safe (e.g., reference table lookup, pre-filtered parent).

### 4.1 auth -- Login, Registration, Settings, MFA

**File: `app/routes/auth.py`**

| Line | Query | Status |
|------|-------|--------|
| ~34 | `User` via `auth_service.authenticate(email, password)` | Safe -- authenticates by email/password |
| ~48 | `MfaConfig.filter_by(user_id=user.id)` | Safe -- filters by authenticated user |
| ~90 | `current_user` object access | Safe -- Flask-Login session |
| ~162 | `User` via `db.session.get(User, pending_user_id)` | Safe -- pending_user_id from server-side session |
| ~168 | `MfaConfig.filter_by(user_id=user.id)` | Safe -- user from session |
| ~215 | `MfaConfig.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| ~244 | `MfaConfig` create/update for `current_user.id` | Safe -- scoped to current user |
| ~285 | `MfaConfig.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| ~310 | `MfaConfig.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| ~330 | `MfaConfig.filter_by(user_id=current_user.id)` | Safe -- direct filter |

**Verdict: All queries safe. No fixes needed.**

**File: `app/services/auth_service.py`**

| Line | Query | Status |
|------|-------|--------|
| 52 | `User.query.filter_by(email=email).first()` | Safe -- email lookup for login |

**Verdict: All queries safe.**

---

### 4.2 grid -- Budget Grid, Transactions

**File: `app/routes/grid.py`**

| Line | Query | Status |
|------|-------|--------|
| 44-48 | `Scenario.filter_by(user_id=user_id, is_baseline=True)` | Safe -- direct user_id filter |
| 83-91 | `Transaction.filter(pay_period_id.in_(period_ids), scenario_id)` | Safe -- period_ids and scenario from user-scoped queries |
| 94-102 | `Transfer.filter(pay_period_id.in_(period_ids), scenario_id)` | Safe -- same as above |
| 130-135 | `Category.filter_by(user_id=user_id)` | Safe -- direct filter |
| 138 | `Status.query()` | Safe -- ref table |
| 139 | `TransactionType.query()` | Safe -- ref table |
| 183-187 | `Scenario.filter_by(user_id=user_id, is_baseline=True)` | Safe -- direct filter |
| 205-223 | Transaction + Transfer queries (balance_row) | Safe -- same pattern as index |

All queries are user-scoped via `user_id = current_user.id` (set at line ~38) or derived from user-scoped objects.

**Verdict: All queries safe. No fixes needed.**

**File: `app/routes/transactions.py`**

| Line | Query | Status |
|------|-------|--------|
| 39-53 | `_get_owned_transaction()` helper | Safe -- verifies `txn.pay_period.user_id == current_user.id` |
| 220-231 | Category, PayPeriod, Scenario lookups for create forms | Safe -- ownership validated |
| 323-334 | Category, PayPeriod, Status for inline create | Safe -- ownership validated (lines 324, 329) |
| 366-372 | PayPeriod for full create | Safe -- ownership validated (line 367) |
| 409-410 | PayPeriod for carry-forward | Safe -- ownership validated (line 410) |

Every transaction route that takes an ID uses `_get_owned_transaction()` which returns None (→ 404) for unauthorized access. Create routes validate ownership of referenced categories and pay periods.

**Verdict: All queries safe. No fixes needed.**

---

### 4.3 accounts -- Accounts Dashboard, HYSA, Anchor

**File: `app/routes/accounts.py`**

| Line | Query | Status |
|------|-------|--------|
| 54-59 | `Account.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| 60-64 | `AccountType.query()` | Safe -- ref table |
| 111-115 | `Account.filter_by(user_id=current_user.id, name=...)` | Safe -- duplicate check with user_id |
| 162-166 | `AccountType.query()` | Safe -- ref table |
| 192-199 | `Account.filter_by(user_id=current_user.id, name=...)` | Safe -- duplicate check with user_id |
| 240-251 | `TransferTemplate.filter(user_id == current_user.id)` | Safe -- direct filter |
| 554-558 | `HysaParams.filter_by(account_id=account.id)` | Safe -- account ownership verified at line ~536 |
| 569-573 | `Scenario.filter_by(user_id=user_id, is_baseline=True)` | Safe -- direct filter |
| 577-595 | Transaction/Transfer queries by period_ids + scenario | Safe -- derived from user-scoped objects |
| 598-602 | `TransactionTemplate.query(user_id=user_id)` | Safe -- direct filter |
| 679-683 | `HysaParams.filter_by(account_id=account.id)` | Safe -- account ownership verified first |

Every route that takes `account_id` loads the account, then checks `account.user_id != current_user.id`. Child records (HysaParams, AccountAnchorHistory) are loaded by the verified account's ID.

**Edge case -- `delete_account_type` (line 428):**
```python
in_use = db.session.query(Account).filter_by(account_type_id=type_id).first()
```
This checks if **any** account (across all users) uses the type. This is **correct behavior** for shared reference data -- account types are in the `ref` schema conceptually (though stored with the `ref.account_types` model), and deleting one that any user relies on would break their data. This is excluded from the 8E audit scope per the constraints.

**Verdict: All queries safe. No fixes needed.**

---

### 4.4 templates -- Transaction Templates

**File: `app/routes/templates.py`**

| Line | Query | Status |
|------|-------|--------|
| 40-45 | `TransactionTemplate.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| 53-65 | Category, Account filter by user_id; RecurrencePattern, TransactionType (ref) | Safe |
| 93-100 | Account, Category `.session.get()` + ownership check | Safe -- validated |
| 109-113 | `RecurrencePattern.filter_by(name=...)` | Safe -- ref table |
| 120-123 | `PayPeriod.session.get()` + ownership check | Safe -- validated (line 124) |
| 153-157 | `Scenario.filter_by(user_id=current_user.id, is_baseline=True)` | Safe -- direct filter |
| 173-176 | `TransactionTemplate.session.get()` + ownership check | Safe -- validated |
| 313-318 | Transaction soft-delete by template_id + status | Safe -- template ownership verified first |
| 342-360 | Transaction/Scenario queries for reactivation | Safe -- template and scenario user-scoped |

Every route that takes `template_id` verifies `template.user_id != current_user.id`. All list queries filter by `user_id=current_user.id`.

**Verdict: All queries safe. No fixes needed.**

---

### 4.5 transfers -- Transfer Templates and Instances

**File: `app/routes/transfers.py`**

| Line | Query | Status |
|------|-------|--------|
| 51-56 | `TransferTemplate.filter_by(user_id=current_user.id)` | Safe -- direct filter |
| 64-69 | `Account.filter_by(user_id=current_user.id, is_active=True)` | Safe |
| 102-109 | Account `.session.get()` + ownership check (from and to) | Safe |
| 158-162 | `Scenario.filter_by(user_id=current_user.id, is_baseline=True)` | Safe |
| 178-181 | `TransferTemplate.session.get()` + ownership check | Safe |
| 301-306 | Transfer soft-delete by template_id + status | Safe -- template verified first |
| 329-341 | Transfer/Scenario for reactivation | Safe -- template and scenario user-scoped |
| 438-449 | PayPeriod, Account for ad-hoc creation | Safe -- all validated (lines 439-447) |
| 535-538 | `Transfer.session.get()` + `xfer.user_id != current_user.id` | Safe -- direct user_id check |

Transfer instances have a direct `user_id` column. Every transfer route checks `transfer.user_id != current_user.id`.

**Verdict: All queries safe. No fixes needed.**

---

### 4.6 salary -- Salary Profiles, Raises, Deductions, Tax Config

**File: `app/routes/salary.py`**

| Line | Query | Status |
|------|-------|--------|
| 68-88 | `_load_tax_configs()` -- TaxBracketSet, StateTaxConfig, FicaConfig | Safe -- all filter by `user_id=user_id` |
| 104-109 | `SalaryProfile.filter_by(user_id=current_user.id)` | Safe |
| 155-159 | `Scenario.filter_by(user_id=current_user.id, is_baseline=True)` | Safe |
| 165-169 | `Category.filter_by(user_id=current_user.id, ...)` | Safe |
| 181 | `TransactionType.filter_by(name="income")` | Safe -- ref table |
| 184-188 | `Account.filter_by(user_id=current_user.id, is_active=True)` | Safe |
| 195-199 | `RecurrencePattern.filter_by(name="every_period")` | Safe -- ref table |
| 260-264 | `SalaryProfile.session.get()` + ownership check | Safe |
| 356 | `SalaryProfile.session.get()` + ownership check | Safe |
| 394-400 | `SalaryRaise.session.get()` → profile ownership check | Safe -- checks `profile.user_id` |
| 474-480 | `PaycheckDeduction.session.get()` → profile ownership check | Safe -- checks `profile.user_id` |
| 509-514 | SalaryProfile, PayPeriod `.session.get()` + ownership checks | Safe |
| 553 | `SalaryProfile.session.get()` + ownership check | Safe |
| 593-636 | StateTaxConfig, FicaConfig update queries | Safe -- all filter by `user_id=current_user.id` |
| 660-664 | `Scenario.filter_by(user_id=current_user.id, is_baseline=True)` | Safe |
| 721-738 | Account queries for deduction target | Safe -- filter by `user_id` |

Raises and deductions are accessed through their parent salary profile. Routes load the raise/deduction by PK, then verify the parent profile's `user_id`. Tax config queries all include `user_id=current_user.id`.

**Verdict: All queries safe. No fixes needed.**

---

### 4.7 retirement -- Pension Profiles, Retirement Dashboard

**File: `app/routes/retirement.py`**

| Line | Query | Status |
|------|-------|--------|
| 63-65 | `UserSettings.filter_by(user_id=user_id)` | Safe |
| 67-76 | PensionProfile, SalaryProfile by `user_id` | Safe |
| 118-122 | `AccountType.filter(category.in_(...))` | Safe -- ref table |
| 126-134 | `Account.filter(user_id == user_id, ...)` | Safe |
| 145-155 | PaycheckDeduction join SalaryProfile filter by `user_id` | Safe |
| 162-170 | Transfer filter by account_ids + period_ids | Safe -- accounts pre-filtered |
| 188-192 | `InvestmentParams.filter_by(account_id=acct.id)` | Safe -- account verified |
| 356-365 | PensionProfile, SalaryProfile by `user_id` | Safe |
| 402-411 | PensionProfile `.session.get()` + ownership check | Safe |
| 424 | PensionProfile `.session.get()` + ownership check | Safe |
| 459 | PensionProfile `.session.get()` + ownership check | Safe |
| 528-532 | `UserSettings.filter_by(user_id=current_user.id)` | Safe |

All pension routes verify `pension.user_id != current_user.id`. Dashboard queries all scope by `user_id`.

**Verdict: All queries safe. No fixes needed.**

---

### 4.8 savings -- Savings Goals Dashboard

**File: `app/routes/savings.py`**

| Line | Query | Status |
|------|-------|--------|
| 50-55 | `Account.filter_by(user_id=user_id, is_active=True)` | Safe |
| 57-61 | `Scenario.filter_by(user_id=user_id, is_baseline=True)` | Safe |
| 69-87 | Transaction, Transfer by period_ids + scenario | Safe |
| 91-95 | `TransactionTemplate.query(user_id=user_id)` | Safe |
| 104-182 | Various param queries (HysaParams, MortgageParams, etc.) | Safe -- all via user-scoped accounts |
| 336-340 | `SavingsGoal.filter_by(user_id=user_id, is_active=True)` | Safe |
| 471-476 | `Account.filter_by(user_id=current_user.id, is_active=True)` | Safe |
| 510, 528, 562 | SavingsGoal `.session.get()` + ownership check | Safe |
| 542 | Account `.session.get()` + ownership check | Safe |

**Verdict: All queries safe. No fixes needed.**

---

### 4.9 categories -- Category Management

**File: `app/routes/categories.py`**

| Line | Query | Status |
|------|-------|--------|
| 44-52 | `Category.filter_by(user_id=current_user.id, ...)` | Safe -- direct filter |
| 57 | `Category(user_id=current_user.id, ...)` create | Safe |
| 74-75 | `Category.session.get()` + `category.user_id != current_user.id` | Safe |
| 83-91 | TransactionTemplate, Transaction `.filter_by(category_id=...)` | Safe -- see note |

**Note on lines 83-91:** The "in use" check queries templates and transactions by `category_id` without a user_id filter. This is safe because: (a) the category was already verified to belong to `current_user` at line 75, and (b) categories have a unique user_id FK, so no other user's templates/transactions can reference this user's category (FK integrity).

**Verdict: All queries safe. No fixes needed.**

---

### 4.10 pay_periods -- Pay Period Generation

**File: `app/routes/pay_periods.py`**

No direct database queries. All logic delegated to `pay_period_service.generate_pay_periods(user_id=current_user.id)`.

**Verdict: Safe.**

---

### 4.11 charts -- Visualization Endpoints

**File: `app/routes/charts.py`**

No direct database queries. All logic delegated to `chart_data_service.*()` functions, every one called with `user_id=current_user.id`.

**Verdict: Safe (see chart_data_service audit in section 4.15).**

---

### 4.12 mortgage -- Mortgage Dashboard

**File: `app/routes/mortgage.py`**

| Line | Query | Status |
|------|-------|--------|
| 43-55 | `_load_mortgage_account()` -- Account by PK + ownership check + MortgageParams by account_id | Safe |
| 96-101 | `EscrowComponent.filter_by(account_id=account.id, is_active=True)` | Safe -- account verified |
| 110-115 | `MortgageRateHistory.filter_by(account_id=account.id)` | Safe -- account verified |
| 144-153 | Account by PK + ownership check + MortgageParams duplicate check | Safe |
| 241-246 | MortgageRateHistory by account_id | Safe -- account verified |
| 269-288 | EscrowComponent create/query by account_id | Safe -- account verified |
| 307-320 | EscrowComponent by PK + `comp.account_id != account.id` check | Safe |

All mortgage routes use `_load_mortgage_account()` which verifies `account.user_id != current_user.id`. Child records are accessed via the verified account's ID.

**Verdict: All queries safe. No fixes needed.**

---

### 4.13 auto_loan -- Auto Loan Dashboard

**File: `app/routes/auto_loan.py`**

| Line | Query | Status |
|------|-------|--------|
| 33-45 | `_load_auto_loan_account()` -- Account by PK + ownership check + AutoLoanParams by account_id | Safe |
| 106-115 | Account by PK + ownership check + AutoLoanParams by account_id | Safe |

All routes verify account ownership before accessing loan parameters.

**Verdict: All queries safe. No fixes needed.**

---

### 4.14 investment -- Investment Dashboard

**File: `app/routes/investment.py`**

| Line | Query | Status |
|------|-------|--------|
| 45-54 | Account by PK + `account.user_id != current_user.id` + InvestmentParams | Safe |
| 64-68 | `SalaryProfile.filter_by(user_id=current_user.id, is_active=True)` | Safe |
| 76-86 | PaycheckDeduction join SalaryProfile filter by `user_id` | Safe |
| 101-109 | Transfer filter by account_id + period_ids | Safe -- account pre-verified |
| 179-183 | `UserSettings.filter_by(user_id=current_user.id)` | Safe |
| 216-292 | Growth chart queries -- same patterns as dashboard | Safe |
| 342-351 | Update params -- account verified first | Safe |

**Verdict: All queries safe. No fixes needed.**

---

### 4.15 settings -- User Settings Dashboard

**File: `app/routes/settings.py`**

| Line | Query | Status |
|------|-------|--------|
| 56-61 | `Account.filter_by(user_id=current_user.id, is_active=True)` | Safe |
| 63-68 | `Category.filter_by(user_id=current_user.id)` | Safe |
| 74-75 | FilingStatus, TaxType `.query()` | Safe -- ref tables |
| 76-92 | TaxBracketSet, FicaConfig, StateTaxConfig by `user_id=current_user.id` | Safe |
| 94-98 | `AccountType.query()` | Safe -- ref table |
| 99-105 | `Account.query(user_id=current_user.id)` | Safe |
| 109-113 | `MfaConfig.filter_by(user_id=current_user.id)` | Safe |
| 177-178 | Account `.session.get()` + ownership check | Safe |

**Verdict: All queries safe. No fixes needed.**

---

### 4.16 Service-Level Query Audit

#### `app/services/pay_period_service.py`

All 5 query functions take `user_id` as a parameter and filter by it. **All safe.**

#### `app/services/account_resolver.py`

All queries either filter by `user_id` or load by PK then immediately check `acct.user_id == user_id`. **All safe.**

#### `app/services/chart_data_service.py`

All 10+ public functions take `user_id` and filter accordingly. Internal helper functions (`_get_baseline_scenario`, `_get_periods`, `_get_period_range`) all filter by `user_id`. Sub-queries that filter by `scenario_id` or `period_ids` are safe because those objects were loaded with user_id filters. **All safe.**

#### `app/services/credit_workflow.py`

`mark_as_credit(transaction_id)` and `unmark_credit(transaction_id)` load transactions by PK without user_id verification. **Safe in practice** because the calling routes (`transactions.py` mark_credit/unmark_credit) verify ownership via `_get_owned_transaction()` before calling the service. The `_get_or_create_cc_category(user_id)` function filters by user_id.

**Recommendation:** Add `user_id` parameter and ownership check to `mark_as_credit()` and `unmark_credit()` for defense-in-depth. **Risk if unfixed: Low** (route-level protection exists).

#### `app/services/carry_forward_service.py`

`carry_forward_unpaid(source_period_id, target_period_id)` loads PayPeriod objects by PK without user_id verification. **Safe in practice** because the calling route (`transactions.py:carry_forward`, line 409-410) verifies period ownership.

**Recommendation:** Add `user_id` parameter and ownership check. **Risk if unfixed: Low** (route-level protection exists).

#### `app/services/recurrence_engine.py`

- `generate_for_template()`, `regenerate_for_template()`: Take a template object (pre-verified by caller) and scenario_id. Internal queries filter by template_id and scenario_id, both user-scoped. **Safe.**
- `resolve_conflicts()` (line 244): Takes transaction IDs and modifies them without user verification. **Not directly exposed to routes** -- only called internally by generate/regenerate functions with IDs from pre-filtered queries. **Safe in practice.** For defense-in-depth, adding a user_id check would be ideal but is low priority.
- `_get_salary_profile(template_id)` (line 449): Filters by template_id, not user_id. **Safe** because templates are user-scoped.
- Tax config queries in `_get_transaction_amount()` (lines 471-491): Filter by `user_id` (derived from `salary_profile.user_id`). **Safe.**

#### `app/services/transfer_recurrence.py`

Same pattern as `recurrence_engine.py`. `resolve_conflicts()` is internal-only. **Safe in practice.**

---

### 4.17 Query Audit Summary

| Module | Total Queries | Safe | Needs Fix | Notes |
|--------|---------------|------|-----------|-------|
| auth routes | 10 | 10 | 0 | |
| grid routes | 9 | 9 | 0 | |
| transaction routes | 15 | 15 | 0 | `_get_owned_transaction()` pattern |
| account routes | 18 | 18 | 0 | Consistent ownership checks |
| template routes | 12 | 12 | 0 | |
| transfer routes | 16 | 16 | 0 | Direct user_id on transfers |
| salary routes | 20+ | 20+ | 0 | |
| retirement routes | 12 | 12 | 0 | |
| savings routes | 12 | 12 | 0 | |
| categories routes | 4 | 4 | 0 | |
| pay_periods routes | 0 | 0 | 0 | Delegated to service |
| charts routes | 0 | 0 | 0 | Delegated to service |
| mortgage routes | 10 | 10 | 0 | |
| auto_loan routes | 3 | 3 | 0 | |
| investment routes | 8 | 8 | 0 | |
| settings routes | 10 | 10 | 0 | |
| **Services** | 30+ | 30+ | **0** | Defense-in-depth recommendations noted |

**Result: No critical or high-risk unscoped queries found at the route level.** The codebase has consistent user_id discipline. Service-level defense-in-depth improvements are recommended but represent low risk since all services are called from routes that verify ownership first.

---

## 5. Route Authorization Audit

Every route that takes an ID parameter is listed below with its authorization status.

### 5.1 accounts Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_account` | `/accounts/<account_id>/edit` | account_id | Yes | `account.user_id != current_user.id` → redirect |
| `update_account` | `/accounts/<account_id>` | account_id | Yes | Same |
| `deactivate_account` | `/accounts/<account_id>/delete` | account_id | Yes | Same |
| `reactivate_account` | `/accounts/<account_id>/reactivate` | account_id | Yes | Same |
| `inline_anchor_update` | `/accounts/<account_id>/inline-anchor` | account_id | Yes | Same |
| `inline_anchor_form` | `/accounts/<account_id>/inline-anchor-form` | account_id | Yes | Same |
| `inline_anchor_display` | `/accounts/<account_id>/inline-anchor-display` | account_id | Yes | Same |
| `true_up` | `/accounts/<account_id>/true-up` | account_id | Yes | Same |
| `anchor_form` | `/accounts/<account_id>/anchor-form` | account_id | Yes | Same |
| `anchor_display` | `/accounts/<account_id>/anchor-display` | account_id | Yes | Same |
| `hysa_detail` | `/accounts/<account_id>/hysa` | account_id | Yes | Same |
| `update_hysa_params` | `/accounts/<account_id>/hysa/params` | account_id | Yes | Same |
| `update_account_type` | `/accounts/types/<type_id>` | type_id | N/A | Ref table -- shared data |
| `delete_account_type` | `/accounts/types/<type_id>/delete` | type_id | N/A | Ref table -- shared data |

### 5.2 templates Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_template` | `/templates/<template_id>/edit` | template_id | Yes | `template.user_id != current_user.id` → redirect |
| `update_template` | `/templates/<template_id>` | template_id | Yes | Same |
| `delete_template` | `/templates/<template_id>/delete` | template_id | Yes | Same |
| `reactivate_template` | `/templates/<template_id>/reactivate` | template_id | Yes | Same |

### 5.3 transfers Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_transfer_template` | `/transfers/<template_id>/edit` | template_id | Yes | `template.user_id != current_user.id` |
| `update_transfer_template` | `/transfers/<template_id>` | template_id | Yes | Same |
| `delete_transfer_template` | `/transfers/<template_id>/delete` | template_id | Yes | Same |
| `reactivate_transfer_template` | `/transfers/<template_id>/reactivate` | template_id | Yes | Same |
| `get_cell` | `/transfers/cell/<xfer_id>` | xfer_id | Yes | `xfer.user_id != current_user.id` |
| `get_quick_edit` | `/transfers/quick-edit/<xfer_id>` | xfer_id | Yes | Same |
| `get_full_edit` | `/transfers/<xfer_id>/full-edit` | xfer_id | Yes | Same |
| `update_transfer` | `/transfers/instance/<xfer_id>` | xfer_id | Yes | Same |
| `delete_transfer` | `/transfers/instance/<xfer_id>` | xfer_id | Yes | Same |
| `mark_done` | `/transfers/instance/<xfer_id>/mark-done` | xfer_id | Yes | Same |
| `cancel_transfer` | `/transfers/instance/<xfer_id>/cancel` | xfer_id | Yes | Same |

### 5.4 transactions Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `get_cell` | `/transactions/<txn_id>/cell` | txn_id | Yes | `_get_owned_transaction()` or `pay_period.user_id` |
| `get_quick_edit` | `/transactions/<txn_id>/quick-edit` | txn_id | Yes | `_get_owned_transaction()` |
| `get_full_edit` | `/transactions/<txn_id>/full-edit` | txn_id | Yes | Same |
| `update_transaction` | `/transactions/<txn_id>` | txn_id | Yes | Same |
| `mark_done` | `/transactions/<txn_id>/mark-done` | txn_id | Yes | Same |
| `mark_credit` | `/transactions/<txn_id>/mark-credit` | txn_id | Yes | Same |
| `unmark_credit` | `/transactions/<txn_id>/unmark-credit` | txn_id | Yes | Same |
| `cancel_transaction` | `/transactions/<txn_id>/cancel` | txn_id | Yes | Same |
| `delete_transaction` | `/transactions/<txn_id>` | txn_id | Yes | Same |
| `carry_forward` | `/pay-periods/<period_id>/carry-forward` | period_id | Yes | `period.user_id != current_user.id` |

### 5.5 salary Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_profile` | `/salary/<profile_id>/edit` | profile_id | Yes | `profile.user_id != current_user.id` |
| `update_profile` | `/salary/<profile_id>` | profile_id | Yes | Same |
| `delete_profile` | `/salary/<profile_id>/delete` | profile_id | Yes | Same |
| `add_raise` | `/salary/<profile_id>/raises` | profile_id | Yes | Same |
| `delete_raise` | `/salary/raises/<raise_id>/delete` | raise_id | Yes | Via `raise.salary_profile.user_id` |
| `add_deduction` | `/salary/<profile_id>/deductions` | profile_id | Yes | `profile.user_id` |
| `delete_deduction` | `/salary/deductions/<ded_id>/delete` | ded_id | Yes | Via `deduction.salary_profile.user_id` |
| `breakdown` | `/salary/<profile_id>/breakdown/<period_id>` | profile_id, period_id | Yes | Both checked |
| `breakdown_current` | `/salary/<profile_id>/breakdown` | profile_id | Yes | Redirects to breakdown |
| `projection` | `/salary/<profile_id>/projection` | profile_id | Yes | `profile.user_id` |

### 5.6 retirement Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_pension` | `/retirement/pension/<pension_id>/edit` | pension_id | Yes | `pension.user_id != current_user.id` |
| `update_pension` | `/retirement/pension/<pension_id>` | pension_id | Yes | Same |
| `delete_pension` | `/retirement/pension/<pension_id>/delete` | pension_id | Yes | Same |

### 5.7 savings Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `edit_goal` | `/savings/goals/<goal_id>/edit` | goal_id | Yes | `goal.user_id != current_user.id` |
| `update_goal` | `/savings/goals/<goal_id>` | goal_id | Yes | Same |
| `delete_goal` | `/savings/goals/<goal_id>/delete` | goal_id | Yes | Same |

### 5.8 categories Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `delete_category` | `/categories/<category_id>/delete` | category_id | Yes | `category.user_id != current_user.id` |

### 5.9 mortgage Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `dashboard` | `/accounts/<account_id>/mortgage` | account_id | Yes | Via `_load_mortgage_account()` |
| `create_params` | `/accounts/<account_id>/mortgage/setup` | account_id | Yes | `account.user_id != current_user.id` |
| `update_params` | `/accounts/<account_id>/mortgage/params` | account_id | Yes | Via helper |
| `add_rate_change` | `/accounts/<account_id>/mortgage/rate` | account_id | Yes | Via helper |
| `add_escrow` | `/accounts/<account_id>/mortgage/escrow` | account_id | Yes | Via helper |
| `delete_escrow` | `/accounts/<account_id>/mortgage/escrow/<component_id>/delete` | account_id, component_id | Yes | Account verified + `comp.account_id == account.id` |
| `payoff_calculate` | `/accounts/<account_id>/mortgage/payoff` | account_id | Yes | Via helper |

### 5.10 auto_loan Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `dashboard` | `/accounts/<account_id>/auto-loan` | account_id | Yes | Via `_load_auto_loan_account()` |
| `create_params` | `/accounts/<account_id>/auto-loan/setup` | account_id | Yes | `account.user_id` |
| `update_params` | `/accounts/<account_id>/auto-loan/params` | account_id | Yes | Via helper |

### 5.11 investment Blueprint

| Route | URL | ID Params | Ownership Check | Method |
|-------|-----|-----------|-----------------|--------|
| `dashboard` | `/accounts/<account_id>/investment` | account_id | Yes | `account.user_id != current_user.id` |
| `growth_chart` | `/accounts/<account_id>/investment/growth-chart` | account_id | Yes | Same |
| `update_params` | `/accounts/<account_id>/investment/params` | account_id | Yes | Same |

### 5.12 Authorization Audit Summary

**All 60+ routes that accept ID parameters verify ownership.** No missing authorization checks were found. The codebase consistently uses either:
1. Direct `object.user_id != current_user.id` checks for directly scoped objects.
2. Parent ownership verification for indirectly scoped objects (e.g., load account first, then access child params).
3. The `_get_owned_transaction()` helper for transactions (indirect via pay_period).

**Current behavior for unauthorized access: 404 (via redirect with flash) or empty return.** The codebase already follows the "return 404, don't confirm resource exists" pattern. This is the recommended approach (see Decision section 6.2).

---

## 6. Decisions and Recommendations

### 6.1 Open vs. Invite-Only Registration

**Decision: Open registration.**

Rationale:
- Cloudflare Access (completed in 8D) restricts who can reach the application. Only email addresses added to the Cloudflare Access policy can load any page, including the registration page.
- This creates a two-layer authentication model:
  - **Outer layer (Cloudflare Access):** Controls who can reach the app. Managed via Cloudflare dashboard.
  - **Inner layer (app authentication):** Controls who has an account and can see their data. Managed by registration + login.
- Adding invite tokens would require: a token generation CLI script, a token model/table, token validation in the registration flow, and token cleanup. This is unnecessary complexity when Cloudflare Access already gates access.
- To add a new user: (1) Add their email to the Cloudflare Access policy, (2) they visit the app and register. Two steps, no token management.

### 6.2 403 vs. 404 for Unauthorized Access

**Decision: Return 404 Not Found.**

Rationale:
- The codebase already uses this pattern universally. Routes return "Not found" with 404 status (or redirect with a "not found" flash message) when a user attempts to access a resource they don't own.
- 404 is more secure: it does not confirm whether the resource exists. An attacker guessing IDs cannot distinguish "this resource belongs to another user" from "this resource does not exist."
- For debugging: the audit log (8B) captures user_id on every request, so unauthorized access attempts can be traced there without exposing information in the HTTP response.
- The `_get_owned_transaction()` helper already returns None (triggering 404) without distinguishing "not found" from "not yours."

Apply this consistently: every ownership check that fails should redirect with a generic "not found" message or return `("Not found", 404)`.

### 6.3 Default Data for New Users

**Decision: Create minimal required data -- UserSettings + Baseline Scenario.**

When a new user registers, the `register_user()` service function creates:

1. **User record** -- email, hashed password, display name.
2. **UserSettings row** -- uses model defaults (inflation 3%, grid 6 periods, low balance threshold 500, SWR 4%).
3. **Baseline Scenario** -- `is_baseline=True`. Required by the grid and many query functions that filter `Scenario.filter_by(user_id=..., is_baseline=True)`.

**Not created automatically:**
- **Checking account:** The grid handles missing accounts gracefully via `resolve_grid_account()` returning None. The user creates their first account through the existing UI.
- **Categories:** The templates and categories pages show empty lists. The user creates categories through the existing settings UI.
- **Pay periods:** The pay periods settings section has a generation form. The user generates periods before using the grid.
- **Default categories from seed script:** The seed script creates 22 default categories for the development user. For production registration, users start with an empty state and customize their own categories. This avoids opinionated defaults that may not match their needs.
- **Tax configurations:** Created manually through the salary tax config UI. The paycheck calculator handles missing configs gracefully (returns zero tax).

This approach ensures the app works correctly for new users (no null pointer errors or missing baseline scenarios) while giving them full control over their setup.

### 6.4 Test Data Factory Pattern

**Decision: Factory function in `conftest.py`.**

Rationale:
- The existing `seed_user` fixture follows a simple pattern: create objects, flush, return a dict. The second user factory should follow the exact same pattern.
- A dedicated builder module would be over-engineered for this use case. We need exactly two users with predictable data, not a generic factory.
- The factory function will be called `seed_second_user` and create a complete independent dataset: user, settings, checking account, baseline scenario, 5 categories, and optionally pay periods.
- Tests that need both users will depend on both `seed_user` and `seed_second_user` fixtures.
- A `second_auth_client` fixture will log in as the second user.

---

## 7. Work Units

### Work Unit Dependency Graph

```
WU-1: Registration Service & Route
  |
  v
WU-2: Ownership Helpers & Service Defense-in-Depth
  |
  v
WU-3: Data Isolation Test Fixtures
  |
  v
WU-4: Data Isolation Integration Tests (Page Visibility)
  |
  v
WU-5: Direct Object Access Tests (ID Guessing)
```

WU-1 is independent. WU-2 is independent of WU-1. WU-3 depends on WU-1 (needs registration to test). WU-4 and WU-5 depend on WU-3 (need two-user fixtures).

---

### WU-1: Registration Service and Route

**Goal:** Add user registration with email/password form, validation, default data creation, and links between login and registration pages.

**Depends on:** Nothing.

#### Files to Create

**`app/templates/auth/register.html`** -- Registration page template.

Extends `base.html`. Structure follows `login.html` (centered card, Shekel logo):

```
{% extends "base.html" %}
{% block title %}Register -- Shekel{% endblock %}
{% block content %}
<div class="row justify-content-center mt-5">
  <div class="col-md-4 col-lg-3">
    <div class="card shadow-sm">
      <div class="card-body">
        <div class="text-center mb-4">
          <img src="{{ url_for('static', filename='img/shekel_logo.png') }}"
               alt="Shekel" height="40" style="width: auto;">
        </div>
        <h5 class="text-center mb-3">Create Account</h5>
        <form method="POST" action="{{ url_for('auth.register') }}">
          <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
          <div class="mb-3">
            <label for="email" class="form-label">Email</label>
            <input type="email" class="form-control" id="email" name="email"
                   required autocomplete="email" autofocus
                   value="{{ request.form.get('email', '') }}">
          </div>
          <div class="mb-3">
            <label for="display_name" class="form-label">Display Name</label>
            <input type="text" class="form-control" id="display_name" name="display_name"
                   required maxlength="100"
                   value="{{ request.form.get('display_name', '') }}">
          </div>
          <div class="mb-3">
            <label for="password" class="form-label">Password</label>
            <input type="password" class="form-control" id="password" name="password"
                   required minlength="12" autocomplete="new-password">
            <div class="form-text">Minimum 12 characters.</div>
          </div>
          <div class="mb-3">
            <label for="confirm_password" class="form-label">Confirm Password</label>
            <input type="password" class="form-control" id="confirm_password"
                   name="confirm_password" required minlength="12"
                   autocomplete="new-password">
          </div>
          <button type="submit" class="btn btn-primary w-100">Create Account</button>
        </form>
        <div class="text-center mt-3">
          <a href="{{ url_for('auth.login') }}">Already have an account? Sign in</a>
        </div>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

#### Files to Modify

**`app/services/auth_service.py`** -- Add `register_user()` function after `change_password()` (after line 85):

```python
def register_user(email, password, display_name):
    """Register a new user with default settings and baseline scenario.

    Creates the user record, a UserSettings row with defaults, and a
    baseline Scenario.  Does NOT commit -- the caller handles the commit.

    Args:
        email: The user's email address.
        password: The plaintext password (must be >= 12 chars).
        display_name: The user's display name.

    Returns:
        The newly created User object.

    Raises:
        ConflictError: If the email is already registered.
        ValidationError: If the password is shorter than 12 characters.
        ValidationError: If the email format is invalid.
    """
```

Implementation:
1. Validate email format with a simple regex (`r"^[^@]+@[^@]+\.[^@]+$"`). If invalid, raise `ValidationError("Invalid email format.")`.
2. Check uniqueness: `User.query.filter_by(email=email).first()`. If exists, raise `ConflictError("An account with this email already exists.")`.
3. If `len(password) < 12`, raise `ValidationError("Password must be at least 12 characters.")`.
4. Create user: `User(email=email, password_hash=hash_password(password), display_name=display_name)`.
5. `db.session.add(user)` and `db.session.flush()` (to get user.id).
6. Create settings: `UserSettings(user_id=user.id)`.
7. Create baseline scenario: `Scenario(user_id=user.id, name="Baseline", is_baseline=True)`.
8. `db.session.add()` both.
9. Return user.

Note: This function imports `db` from `app.extensions` -- this is the one exception to the "no Flask imports in services" rule, matching the pattern used by `authenticate()` at line 52 which already imports `db` to query the User model.

**`app/routes/auth.py`** -- Add registration routes after `login()` (after line ~69):

```python
@auth_bp.route("/register", methods=["GET"])
def register_form():
    """Display the registration form."""

@auth_bp.route("/register", methods=["POST"])
def register():
    """Process a registration submission."""
```

`register_form()` implementation:
1. If `current_user.is_authenticated`, redirect to grid.
2. Render `auth/register.html`.

`register()` implementation:
1. If `current_user.is_authenticated`, redirect to grid.
2. Extract `email`, `display_name`, `password`, `confirm_password` from `request.form`.
3. If `password != confirm_password`, flash "Password and confirmation do not match." (danger), redirect to `auth.register_form`.
4. Call `auth_service.register_user(email, password, display_name)`.
5. On success: `db.session.commit()`, log the event (`logger.info("action=user_registered email=%s", email)`), flash "Account created. Please sign in." (success), redirect to `auth.login`.
6. Catch `ConflictError`: flash the error message (danger), redirect to `auth.register_form`.
7. Catch `ValidationError`: flash the error message (danger), redirect to `auth.register_form`.

**`app/templates/auth/login.html`** -- Add registration link. After the `</form>` tag and before the closing card body, add:

```html
<div class="text-center mt-3">
  <a href="{{ url_for('auth.register_form') }}">Create an account</a>
</div>
```

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see below)
- [ ] GET `/register` renders the registration form
- [ ] POST `/register` with valid data creates user + settings + scenario, redirects to login
- [ ] POST `/register` with duplicate email shows error
- [ ] POST `/register` with short password shows error
- [ ] POST `/register` with mismatched passwords shows error
- [ ] Login page shows "Create an account" link
- [ ] Registration page shows "Already have an account?" link

#### New Tests

**`tests/test_routes/test_auth.py`** -- Add `TestRegistration` class:

```python
class TestRegistration:
    """Tests for the registration flow (GET/POST /register)."""

    def test_register_page_renders(self, app, client):
        """GET /register returns the registration form."""

    def test_register_success(self, app, client, db):
        """POST /register with valid data creates user, settings, and scenario."""

    def test_register_duplicate_email(self, app, client, seed_user):
        """POST /register with existing email shows conflict error."""

    def test_register_password_too_short(self, app, client):
        """POST /register with password < 12 chars shows validation error."""

    def test_register_password_mismatch(self, app, client):
        """POST /register with mismatched passwords shows error."""

    def test_register_invalid_email(self, app, client):
        """POST /register with invalid email format shows error."""

    def test_register_redirects_if_authenticated(self, app, auth_client, seed_user):
        """GET /register while logged in redirects to grid."""

    def test_new_user_can_login(self, app, client, db):
        """A registered user can log in with their credentials."""

    def test_new_user_has_baseline_scenario(self, app, client, db):
        """A registered user has a baseline scenario created."""

    def test_new_user_sees_empty_grid(self, app, client, db):
        """A registered user who logs in sees an empty budget grid."""
```

**`tests/test_services/test_auth_service.py`** -- Add `TestRegisterUser` class:

```python
class TestRegisterUser:
    """Tests for auth_service.register_user()."""

    def test_register_user_success(self, app, db):
        """register_user() creates a user, settings, and baseline scenario."""

    def test_register_user_duplicate_email(self, app, db, seed_user):
        """register_user() raises ConflictError for duplicate email."""

    def test_register_user_short_password(self, app, db):
        """register_user() raises ValidationError for short password."""

    def test_register_user_invalid_email(self, app, db):
        """register_user() raises ValidationError for invalid email."""

    def test_register_user_password_hashed(self, app, db):
        """register_user() stores a bcrypt hash, not plaintext."""
```

#### Impact on Existing Tests

None. The registration routes are new and do not modify any existing routes or models. The login template gets a link added, but no existing test checks for the absence of that link.

---

### WU-2: Ownership Helpers and Service Defense-in-Depth

**Goal:** Extract reusable ownership verification helpers and add user_id validation to service functions that currently rely on route-level checks.

**Depends on:** Nothing.

#### Files to Create

**`app/utils/auth_helpers.py`** -- Reusable ownership verification helpers:

```python
"""
Shekel Budget App -- Authorization Helpers

Reusable functions for verifying resource ownership. Used by route
handlers to ensure the current user can only access their own data.
"""

from flask_login import current_user

from app.extensions import db


def get_or_404(model, pk, user_id_field="user_id"):
    """Load a record by PK and verify it belongs to the current user.

    For models with a direct user_id column. Returns the record if
    found and owned, otherwise returns None (caller should return 404).

    Args:
        model: The SQLAlchemy model class.
        pk: The primary key value.
        user_id_field: The name of the user_id column (default "user_id").

    Returns:
        The model instance if found and owned, else None.
    """
    record = db.session.get(model, pk)
    if record is None:
        return None
    if getattr(record, user_id_field, None) != current_user.id:
        return None
    return record


def get_owned_via_parent(model, pk, parent_attr, parent_user_id_attr="user_id"):
    """Load a record by PK and verify ownership via a parent relationship.

    For models without a direct user_id column that are scoped through
    a parent FK (e.g., Transaction via PayPeriod, SalaryRaise via
    SalaryProfile, EscrowComponent via Account).

    Args:
        model: The SQLAlchemy model class.
        pk: The primary key value.
        parent_attr: The relationship attribute name on the model
                     (e.g., "pay_period", "salary_profile", "account").
        parent_user_id_attr: The user_id attribute on the parent
                             (default "user_id").

    Returns:
        The model instance if found and owned via parent, else None.
    """
    record = db.session.get(model, pk)
    if record is None:
        return None
    parent = getattr(record, parent_attr, None)
    if parent is None:
        return None
    if getattr(parent, parent_user_id_attr, None) != current_user.id:
        return None
    return record
```

These helpers consolidate the ownership verification patterns found throughout the codebase. Routes can adopt them incrementally without changing behavior.

#### Files to Modify

**`app/services/credit_workflow.py`** -- Add user_id parameter to `mark_as_credit()` and `unmark_credit()`:

Current `mark_as_credit()` signature (line ~38):
```python
def mark_as_credit(transaction_id):
```

New signature:
```python
def mark_as_credit(transaction_id, user_id):
```

Add after loading the transaction (line ~44):
```python
txn = db.session.get(Transaction, transaction_id)
if txn is None:
    raise NotFoundError("Transaction not found.")
# Defense-in-depth: verify ownership via pay_period.
if txn.pay_period.user_id != user_id:
    raise NotFoundError("Transaction not found.")
```

Same pattern for `unmark_credit(transaction_id, user_id)`.

**`app/routes/transactions.py`** -- Update calls to credit_workflow:

Current (line ~160):
```python
credit_workflow.mark_as_credit(txn.id)
```

New:
```python
credit_workflow.mark_as_credit(txn.id, current_user.id)
```

Same for `unmark_credit`.

**`app/services/carry_forward_service.py`** -- Add user_id parameter:

Current signature (line ~35):
```python
def carry_forward_unpaid(source_period_id, target_period_id):
```

New signature:
```python
def carry_forward_unpaid(source_period_id, target_period_id, user_id):
```

Add after loading periods (lines ~42-46):
```python
source = db.session.get(PayPeriod, source_period_id)
if source is None or source.user_id != user_id:
    raise NotFoundError("Source pay period not found.")
target = db.session.get(PayPeriod, target_period_id)
if target is None or target.user_id != user_id:
    raise NotFoundError("Target pay period not found.")
```

**`app/routes/transactions.py`** -- Update call to carry_forward_service:

Current (line ~413):
```python
carry_forward_service.carry_forward_unpaid(period.id, current_period.id)
```

New:
```python
carry_forward_service.carry_forward_unpaid(period.id, current_period.id, current_user.id)
```

#### Test Gate

- [ ] `pytest` passes (all existing tests still pass with updated signatures)
- [ ] New tests pass

#### New Tests

**`tests/test_services/test_credit_workflow.py`** -- Update existing tests to pass `user_id` parameter. Add:

```python
def test_mark_as_credit_wrong_user_raises(self, app, db, seed_user, seed_periods):
    """mark_as_credit() raises NotFoundError for wrong user_id."""

def test_unmark_credit_wrong_user_raises(self, app, db, seed_user, seed_periods):
    """unmark_credit() raises NotFoundError for wrong user_id."""
```

**`tests/test_utils/test_auth_helpers.py`** -- New test file:

```python
class TestGetOr404:
    """Tests for auth_helpers.get_or_404()."""

    def test_returns_owned_record(self, app, db, seed_user, auth_client):
        """get_or_404() returns a record owned by current_user."""

    def test_returns_none_for_other_user(self, app, db, seed_user, seed_second_user, auth_client):
        """get_or_404() returns None for a record owned by another user."""

    def test_returns_none_for_missing(self, app, db, auth_client):
        """get_or_404() returns None for nonexistent PK."""


class TestGetOwnedViaParent:
    """Tests for auth_helpers.get_owned_via_parent()."""

    def test_returns_owned_child(self, app, db, seed_user, seed_periods, auth_client):
        """get_owned_via_parent() returns a child record owned via parent."""

    def test_returns_none_for_other_user(self, app, db, seed_user, seed_second_user, seed_periods, auth_client):
        """get_owned_via_parent() returns None for other user's child."""
```

#### Impact on Existing Tests

- **`tests/test_services/test_credit_workflow.py`:** Existing tests call `mark_as_credit(txn_id)` and `unmark_credit(txn_id)`. These must be updated to pass `user_id=seed_user["user"].id`. The behavior is identical; only the signature changes.
- **`tests/test_integration/test_workflows.py`:** If carry_forward_unpaid is called, the call must be updated. Check for direct calls.
- No other test files are affected.

---

### WU-3: Data Isolation Test Fixtures

**Goal:** Create test infrastructure for two-user isolation testing: a second user fixture, a second auth client, and a complete data factory for each user.

**Depends on:** WU-1 (registration creates users).

#### Files to Modify

**`tests/conftest.py`** -- Add second user fixtures after `auth_client` (after line 253):

```python
@pytest.fixture()
def seed_second_user(app, db):
    """Create a second test user with independent data.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="second@shekel.local",
        password_hash=hash_password("secondpass12"),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    checking_type = (
        db.session.query(AccountType).filter_by(name="checking").one()
    )
    account = Account(
        user_id=user.id,
        account_type_id=checking_type.id,
        name="Checking",
        current_anchor_balance=Decimal("2000.00"),
    )
    db.session.add(account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
    }


@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
    """Generate 10 pay periods for the second user.

    Returns:
        List of PayPeriod objects.
    """
    from app.services import pay_period_service

    periods = pay_period_service.generate_pay_periods(
        user_id=seed_second_user["user"].id,
        start_date=date(2026, 1, 2),
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()

    account = seed_second_user["account"]
    account.current_anchor_period_id = periods[0].id
    db.session.commit()

    return periods


@pytest.fixture()
def second_auth_client(app, db, client, seed_second_user):
    """Provide an authenticated test client for the second user.

    IMPORTANT: Uses a separate client instance to avoid session conflicts
    with auth_client.
    """
    second_client = app.test_client()
    second_client.post("/login", data={
        "email": "second@shekel.local",
        "password": "secondpass12",
    })
    return second_client
```

Also add these imports at the top of conftest.py (add to existing import block):

```python
from app.models.transfer_template import TransferTemplate
from app.models.transfer import Transfer
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
```

Add a comprehensive data factory fixture:

```python
@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
    """Create a complete dataset for the primary test user.

    Adds templates, transactions, transfers, and a salary profile to
    the primary test user's data.  Used by isolation tests to verify
    that user B cannot see user A's data.

    Returns:
        dict with all created objects.
    """
    user = seed_user["user"]
    account = seed_user["account"]
    scenario = seed_user["scenario"]
    categories = seed_user["categories"]
    periods = seed_periods

    # Create a transaction template.
    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()
    expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=categories["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Rent Payment",
        default_amount=Decimal("1200.00"),
    )
    db.session.add(template)
    db.session.flush()

    # Create a transaction.
    projected_status = db.session.query(Status).filter_by(name="projected").one()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        status_id=projected_status.id,
        name="Rent Payment",
        category_id=categories["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("1200.00"),
    )
    db.session.add(txn)
    db.session.flush()

    # Create a savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)

    db.session.commit()

    return {
        **seed_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
    }


@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
    """Create a complete dataset for the second test user.

    Mirrors seed_full_user_data but for the second user.
    """
    user = seed_second_user["user"]
    account = seed_second_user["account"]
    scenario = seed_second_user["scenario"]
    categories = seed_second_user["categories"]
    periods = seed_second_periods

    every_period = db.session.query(RecurrencePattern).filter_by(name="every_period").one()
    expense_type = db.session.query(TransactionType).filter_by(name="expense").one()

    rule = RecurrenceRule(
        user_id=user.id,
        pattern_id=every_period.id,
    )
    db.session.add(rule)
    db.session.flush()

    template = TransactionTemplate(
        user_id=user.id,
        account_id=account.id,
        category_id=categories["Rent"].id,
        recurrence_rule_id=rule.id,
        transaction_type_id=expense_type.id,
        name="Second User Rent",
        default_amount=Decimal("900.00"),
    )
    db.session.add(template)
    db.session.flush()

    projected_status = db.session.query(Status).filter_by(name="projected").one()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=periods[0].id,
        scenario_id=scenario.id,
        status_id=projected_status.id,
        name="Second User Rent",
        category_id=categories["Rent"].id,
        transaction_type_id=expense_type.id,
        estimated_amount=Decimal("900.00"),
    )
    db.session.add(txn)
    db.session.flush()

    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Vacation Fund",
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)

    db.session.commit()

    return {
        **seed_second_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
    }
```

#### Test Gate

- [ ] `pytest` passes (all existing tests unaffected)
- [ ] The new fixtures can be used together without FK conflicts

#### New Tests

No new test file for this WU -- the fixtures are tested implicitly by WU-4 and WU-5.

#### Impact on Existing Tests

None. New fixtures are added alongside existing ones. No existing fixture is modified.

---

### WU-4: Data Isolation Integration Tests (Page Visibility)

**Goal:** Verify that when user A logs in, they see only their own data on every page. When user B logs in, they see only their own data.

**Depends on:** WU-3 (two-user fixtures).

#### Files to Create

**`tests/test_integration/test_data_isolation.py`** -- Data isolation test suite:

```python
"""
Shekel Budget App -- Data Isolation Tests

Verifies that each user sees only their own data across all pages
and endpoints.  Creates two users with separate datasets and checks
that neither user can see the other's data.
"""

import pytest
from decimal import Decimal


class TestGridIsolation:
    """Budget grid shows only the logged-in user's data."""

    def test_user_a_sees_own_transactions(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's grid shows their transaction, not user B's."""

    def test_user_b_sees_own_transactions(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's grid shows their transaction, not user A's."""


class TestAccountsIsolation:
    """Accounts dashboard shows only the logged-in user's accounts."""

    def test_user_a_sees_own_accounts(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's accounts page lists only their account."""

    def test_user_b_sees_own_accounts(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's accounts page lists only their account."""


class TestTemplatesIsolation:
    """Templates page shows only the logged-in user's templates."""

    def test_user_a_sees_own_templates(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's templates page lists only their template."""

    def test_user_b_sees_own_templates(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's templates page lists only their template."""


class TestTransfersIsolation:
    """Transfers page shows only the logged-in user's transfers."""

    def test_user_a_sees_own_transfers(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's transfers page shows only their data."""

    def test_user_b_sees_own_transfers(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's transfers page shows only their data."""


class TestSalaryIsolation:
    """Salary page shows only the logged-in user's salary profiles."""

    def test_user_a_sees_own_profiles(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's salary page lists only their profiles."""

    def test_user_b_sees_own_profiles(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's salary page lists only their profiles."""


class TestSavingsIsolation:
    """Savings page shows only the logged-in user's data."""

    def test_user_a_sees_own_savings(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's savings page shows only their goals and accounts."""

    def test_user_b_sees_own_savings(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's savings page shows only their goals and accounts."""


class TestCategoriesIsolation:
    """Settings categories show only the logged-in user's categories."""

    def test_user_a_sees_own_categories(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's settings page lists only their categories."""

    def test_user_b_sees_own_categories(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's settings page lists only their categories."""


class TestChartsIsolation:
    """Chart endpoints return only the logged-in user's data."""

    def test_balance_chart_user_a(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """Balance chart for user A shows only their account data."""

    def test_balance_chart_user_b(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """Balance chart for user B shows only their account data."""


class TestSettingsIsolation:
    """Settings page shows only the logged-in user's settings."""

    def test_user_a_sees_own_settings(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's settings reflect their own configuration."""

    def test_user_b_sees_own_settings(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's settings reflect their own configuration."""


class TestRetirementIsolation:
    """Retirement page shows only the logged-in user's data."""

    def test_user_a_sees_own_retirement(
        self, app, auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User A's retirement page shows only their pension profiles."""

    def test_user_b_sees_own_retirement(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """User B's retirement page shows only their pension profiles."""
```

#### Test Gate

- [ ] All isolation tests pass
- [ ] Each test verifies the user's own data IS present in the response
- [ ] Each test verifies the other user's data IS NOT present in the response

#### Impact on Existing Tests

None. This is a new test file.

---

### WU-5: Direct Object Access Tests (ID Guessing)

**Goal:** Verify that user B cannot access user A's resources by guessing IDs. Every route that takes an ID parameter should return 404 when user B provides user A's resource ID.

**Depends on:** WU-3 (two-user fixtures).

#### Files to Create

**`tests/test_integration/test_access_control.py`** -- Direct object access tests:

```python
"""
Shekel Budget App -- Access Control Tests

Verifies that users cannot access other users' resources by guessing IDs.
Every route that accepts an ID parameter is tested.
"""

import pytest


class TestAccountAccessControl:
    """User B cannot access user A's accounts by ID."""

    def test_edit_account_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /accounts/<user_a_account_id>/edit as user B returns redirect/404."""

    def test_update_account_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /accounts/<user_a_account_id> as user B returns redirect/404."""

    def test_deactivate_account_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /accounts/<user_a_account_id>/delete as user B is blocked."""

    def test_hysa_detail_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /accounts/<user_a_account_id>/hysa as user B returns redirect/404."""

    def test_inline_anchor_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """PATCH /accounts/<user_a_account_id>/inline-anchor as user B is blocked."""


class TestTemplateAccessControl:
    """User B cannot access user A's templates by ID."""

    def test_edit_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /templates/<user_a_template_id>/edit as user B returns redirect/404."""

    def test_update_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /templates/<user_a_template_id> as user B returns redirect/404."""

    def test_delete_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /templates/<user_a_template_id>/delete as user B is blocked."""


class TestTransactionAccessControl:
    """User B cannot access user A's transactions by ID."""

    def test_get_cell_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /transactions/<user_a_txn_id>/cell as user B returns 404."""

    def test_quick_edit_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /transactions/<user_a_txn_id>/quick-edit as user B returns 404."""

    def test_update_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """PATCH /transactions/<user_a_txn_id> as user B returns 404."""

    def test_mark_done_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /transactions/<user_a_txn_id>/mark-done as user B returns 404."""

    def test_mark_credit_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /transactions/<user_a_txn_id>/mark-credit as user B returns 404."""

    def test_cancel_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /transactions/<user_a_txn_id>/cancel as user B returns 404."""

    def test_delete_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """DELETE /transactions/<user_a_txn_id> as user B returns 404."""

    def test_carry_forward_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /pay-periods/<user_a_period_id>/carry-forward as user B returns 404."""


class TestTransferAccessControl:
    """User B cannot access user A's transfers by ID."""

    def test_edit_transfer_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /transfers/<user_a_template_id>/edit as user B returns redirect/404."""

    def test_update_transfer_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /transfers/<user_a_template_id> as user B is blocked."""

    def test_delete_transfer_template_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /transfers/<user_a_template_id>/delete as user B is blocked."""


class TestSalaryAccessControl:
    """User B cannot access user A's salary profiles by ID."""

    def test_edit_profile_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /salary/<user_a_profile_id>/edit as user B returns redirect/404."""

    def test_update_profile_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /salary/<user_a_profile_id> as user B is blocked."""

    def test_delete_profile_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /salary/<user_a_profile_id>/delete as user B is blocked."""

    def test_breakdown_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /salary/<user_a_profile_id>/breakdown as user B is blocked."""

    def test_projection_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /salary/<user_a_profile_id>/projection as user B is blocked."""


class TestSavingsAccessControl:
    """User B cannot access user A's savings goals by ID."""

    def test_edit_goal_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /savings/goals/<user_a_goal_id>/edit as user B returns redirect/404."""

    def test_update_goal_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /savings/goals/<user_a_goal_id> as user B is blocked."""

    def test_delete_goal_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /savings/goals/<user_a_goal_id>/delete as user B is blocked."""


class TestCategoryAccessControl:
    """User B cannot delete user A's categories by ID."""

    def test_delete_category_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /categories/<user_a_category_id>/delete as user B is blocked."""


class TestRetirementAccessControl:
    """User B cannot access user A's pension profiles by ID."""

    def test_edit_pension_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /retirement/pension/<user_a_pension_id>/edit as user B is blocked."""

    def test_update_pension_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /retirement/pension/<user_a_pension_id> as user B is blocked."""

    def test_delete_pension_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /retirement/pension/<user_a_pension_id>/delete as user B is blocked."""


class TestMortgageAccessControl:
    """User B cannot access user A's mortgage dashboard by account ID."""

    def test_mortgage_dashboard_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /accounts/<user_a_account_id>/mortgage as user B is blocked."""

    def test_mortgage_setup_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /accounts/<user_a_account_id>/mortgage/setup as user B is blocked."""


class TestAutoLoanAccessControl:
    """User B cannot access user A's auto loan dashboard by account ID."""

    def test_auto_loan_dashboard_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /accounts/<user_a_account_id>/auto-loan as user B is blocked."""


class TestInvestmentAccessControl:
    """User B cannot access user A's investment dashboard by account ID."""

    def test_investment_dashboard_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """GET /accounts/<user_a_account_id>/investment as user B is blocked."""

    def test_investment_update_returns_404(
        self, app, second_auth_client, seed_full_user_data, seed_full_second_user_data
    ):
        """POST /accounts/<user_a_account_id>/investment/params as user B is blocked."""
```

#### Test Gate

- [ ] All access control tests pass (expected: 302 redirect with "not found" flash, or 404 status)
- [ ] Tests cover every blueprint listed in the master plan: budget grid, accounts, transfers, salary, templates, mortgage, auto loan, investment, retirement, charts, settings

#### Impact on Existing Tests

None. This is a new test file.

---

## 8. Work Unit Dependency Graph

```
WU-1: Registration           WU-2: Ownership Helpers
(auth_service.register_user,  (auth_helpers.py, service
 register routes, template)    defense-in-depth fixes)
         \                    /
          \                  /
           v                v
      WU-3: Data Isolation Fixtures
      (seed_second_user, seed_full_user_data,
       second_auth_client in conftest.py)
                    |
          +---------+---------+
          |                   |
          v                   v
   WU-4: Page Visibility    WU-5: ID Guessing
   Tests (isolation per     Tests (access control
    page/endpoint)           per ID route)
```

WU-1 and WU-2 are **independent** and can be done in parallel.

WU-3 depends on WU-1 (second user creation follows the registration service pattern).

WU-4 and WU-5 depend on WU-3 (need two-user fixtures) and are **independent** of each other.

---

## 9. Complete Test Plan Table

| # | File | Class | Method | WU | Verifies |
|---|------|-------|--------|----|----------|
| 1 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_page_renders` | 1 | GET /register renders form |
| 2 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_success` | 1 | Valid registration creates user+settings+scenario |
| 3 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_duplicate_email` | 1 | Duplicate email rejected |
| 4 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_password_too_short` | 1 | Password < 12 chars rejected |
| 5 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_password_mismatch` | 1 | Mismatched passwords rejected |
| 6 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_invalid_email` | 1 | Invalid email format rejected |
| 7 | `test_routes/test_auth.py` | `TestRegistration` | `test_register_redirects_if_authenticated` | 1 | Logged-in user redirected from register |
| 8 | `test_routes/test_auth.py` | `TestRegistration` | `test_new_user_can_login` | 1 | Registered user can authenticate |
| 9 | `test_routes/test_auth.py` | `TestRegistration` | `test_new_user_has_baseline_scenario` | 1 | Registration creates baseline scenario |
| 10 | `test_routes/test_auth.py` | `TestRegistration` | `test_new_user_sees_empty_grid` | 1 | New user grid has no transactions |
| 11 | `test_services/test_auth_service.py` | `TestRegisterUser` | `test_register_user_success` | 1 | Service creates user+settings+scenario |
| 12 | `test_services/test_auth_service.py` | `TestRegisterUser` | `test_register_user_duplicate_email` | 1 | Service raises ConflictError |
| 13 | `test_services/test_auth_service.py` | `TestRegisterUser` | `test_register_user_short_password` | 1 | Service raises ValidationError |
| 14 | `test_services/test_auth_service.py` | `TestRegisterUser` | `test_register_user_invalid_email` | 1 | Service raises ValidationError |
| 15 | `test_services/test_auth_service.py` | `TestRegisterUser` | `test_register_user_password_hashed` | 1 | Password stored as bcrypt hash |
| 16 | `test_services/test_credit_workflow.py` | (existing) | `test_mark_as_credit_wrong_user_raises` | 2 | Wrong user_id raises NotFoundError |
| 17 | `test_services/test_credit_workflow.py` | (existing) | `test_unmark_credit_wrong_user_raises` | 2 | Wrong user_id raises NotFoundError |
| 18 | `test_utils/test_auth_helpers.py` | `TestGetOr404` | `test_returns_owned_record` | 2 | Helper returns owned record |
| 19 | `test_utils/test_auth_helpers.py` | `TestGetOr404` | `test_returns_none_for_other_user` | 2 | Helper returns None for other user |
| 20 | `test_utils/test_auth_helpers.py` | `TestGetOr404` | `test_returns_none_for_missing` | 2 | Helper returns None for missing PK |
| 21 | `test_utils/test_auth_helpers.py` | `TestGetOwnedViaParent` | `test_returns_owned_child` | 2 | Helper returns child via parent |
| 22 | `test_utils/test_auth_helpers.py` | `TestGetOwnedViaParent` | `test_returns_none_for_other_user` | 2 | Helper blocks other user's child |
| 23 | `test_integration/test_data_isolation.py` | `TestGridIsolation` | `test_user_a_sees_own_transactions` | 4 | Grid scoped to user A |
| 24 | `test_integration/test_data_isolation.py` | `TestGridIsolation` | `test_user_b_sees_own_transactions` | 4 | Grid scoped to user B |
| 25 | `test_integration/test_data_isolation.py` | `TestAccountsIsolation` | `test_user_a_sees_own_accounts` | 4 | Accounts scoped to user A |
| 26 | `test_integration/test_data_isolation.py` | `TestAccountsIsolation` | `test_user_b_sees_own_accounts` | 4 | Accounts scoped to user B |
| 27 | `test_integration/test_data_isolation.py` | `TestTemplatesIsolation` | `test_user_a_sees_own_templates` | 4 | Templates scoped to user A |
| 28 | `test_integration/test_data_isolation.py` | `TestTemplatesIsolation` | `test_user_b_sees_own_templates` | 4 | Templates scoped to user B |
| 29 | `test_integration/test_data_isolation.py` | `TestTransfersIsolation` | `test_user_a_sees_own_transfers` | 4 | Transfers scoped to user A |
| 30 | `test_integration/test_data_isolation.py` | `TestTransfersIsolation` | `test_user_b_sees_own_transfers` | 4 | Transfers scoped to user B |
| 31 | `test_integration/test_data_isolation.py` | `TestSalaryIsolation` | `test_user_a_sees_own_profiles` | 4 | Salary scoped to user A |
| 32 | `test_integration/test_data_isolation.py` | `TestSalaryIsolation` | `test_user_b_sees_own_profiles` | 4 | Salary scoped to user B |
| 33 | `test_integration/test_data_isolation.py` | `TestSavingsIsolation` | `test_user_a_sees_own_savings` | 4 | Savings scoped to user A |
| 34 | `test_integration/test_data_isolation.py` | `TestSavingsIsolation` | `test_user_b_sees_own_savings` | 4 | Savings scoped to user B |
| 35 | `test_integration/test_data_isolation.py` | `TestCategoriesIsolation` | `test_user_a_sees_own_categories` | 4 | Categories scoped to user A |
| 36 | `test_integration/test_data_isolation.py` | `TestCategoriesIsolation` | `test_user_b_sees_own_categories` | 4 | Categories scoped to user B |
| 37 | `test_integration/test_data_isolation.py` | `TestChartsIsolation` | `test_balance_chart_user_a` | 4 | Charts scoped to user A |
| 38 | `test_integration/test_data_isolation.py` | `TestChartsIsolation` | `test_balance_chart_user_b` | 4 | Charts scoped to user B |
| 39 | `test_integration/test_data_isolation.py` | `TestSettingsIsolation` | `test_user_a_sees_own_settings` | 4 | Settings scoped to user A |
| 40 | `test_integration/test_data_isolation.py` | `TestSettingsIsolation` | `test_user_b_sees_own_settings` | 4 | Settings scoped to user B |
| 41 | `test_integration/test_data_isolation.py` | `TestRetirementIsolation` | `test_user_a_sees_own_retirement` | 4 | Retirement scoped to user A |
| 42 | `test_integration/test_data_isolation.py` | `TestRetirementIsolation` | `test_user_b_sees_own_retirement` | 4 | Retirement scoped to user B |
| 43 | `test_integration/test_access_control.py` | `TestAccountAccessControl` | `test_edit_account_returns_404` | 5 | User B blocked from A's account edit |
| 44 | `test_integration/test_access_control.py` | `TestAccountAccessControl` | `test_update_account_returns_404` | 5 | User B blocked from A's account update |
| 45 | `test_integration/test_access_control.py` | `TestAccountAccessControl` | `test_deactivate_account_returns_404` | 5 | User B blocked from deactivating A's account |
| 46 | `test_integration/test_access_control.py` | `TestAccountAccessControl` | `test_hysa_detail_returns_404` | 5 | User B blocked from A's HYSA |
| 47 | `test_integration/test_access_control.py` | `TestAccountAccessControl` | `test_inline_anchor_returns_404` | 5 | User B blocked from A's anchor |
| 48 | `test_integration/test_access_control.py` | `TestTemplateAccessControl` | `test_edit_template_returns_404` | 5 | User B blocked from A's template edit |
| 49 | `test_integration/test_access_control.py` | `TestTemplateAccessControl` | `test_update_template_returns_404` | 5 | User B blocked from A's template update |
| 50 | `test_integration/test_access_control.py` | `TestTemplateAccessControl` | `test_delete_template_returns_404` | 5 | User B blocked from A's template delete |
| 51 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_get_cell_returns_404` | 5 | User B blocked from A's transaction cell |
| 52 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_quick_edit_returns_404` | 5 | User B blocked from A's transaction edit |
| 53 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_update_returns_404` | 5 | User B blocked from A's transaction update |
| 54 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_mark_done_returns_404` | 5 | User B blocked from marking A's txn done |
| 55 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_mark_credit_returns_404` | 5 | User B blocked from marking A's txn credit |
| 56 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_cancel_returns_404` | 5 | User B blocked from cancelling A's txn |
| 57 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_delete_returns_404` | 5 | User B blocked from deleting A's txn |
| 58 | `test_integration/test_access_control.py` | `TestTransactionAccessControl` | `test_carry_forward_returns_404` | 5 | User B blocked from A's carry-forward |
| 59 | `test_integration/test_access_control.py` | `TestTransferAccessControl` | `test_edit_transfer_template_returns_404` | 5 | User B blocked from A's transfer edit |
| 60 | `test_integration/test_access_control.py` | `TestTransferAccessControl` | `test_update_transfer_template_returns_404` | 5 | User B blocked from A's transfer update |
| 61 | `test_integration/test_access_control.py` | `TestTransferAccessControl` | `test_delete_transfer_template_returns_404` | 5 | User B blocked from A's transfer delete |
| 62 | `test_integration/test_access_control.py` | `TestSalaryAccessControl` | `test_edit_profile_returns_404` | 5 | User B blocked from A's salary edit |
| 63 | `test_integration/test_access_control.py` | `TestSalaryAccessControl` | `test_update_profile_returns_404` | 5 | User B blocked from A's salary update |
| 64 | `test_integration/test_access_control.py` | `TestSalaryAccessControl` | `test_delete_profile_returns_404` | 5 | User B blocked from A's salary delete |
| 65 | `test_integration/test_access_control.py` | `TestSalaryAccessControl` | `test_breakdown_returns_404` | 5 | User B blocked from A's paycheck breakdown |
| 66 | `test_integration/test_access_control.py` | `TestSalaryAccessControl` | `test_projection_returns_404` | 5 | User B blocked from A's salary projection |
| 67 | `test_integration/test_access_control.py` | `TestSavingsAccessControl` | `test_edit_goal_returns_404` | 5 | User B blocked from A's savings goal edit |
| 68 | `test_integration/test_access_control.py` | `TestSavingsAccessControl` | `test_update_goal_returns_404` | 5 | User B blocked from A's savings goal update |
| 69 | `test_integration/test_access_control.py` | `TestSavingsAccessControl` | `test_delete_goal_returns_404` | 5 | User B blocked from A's savings goal delete |
| 70 | `test_integration/test_access_control.py` | `TestCategoryAccessControl` | `test_delete_category_returns_404` | 5 | User B blocked from A's category delete |
| 71 | `test_integration/test_access_control.py` | `TestRetirementAccessControl` | `test_edit_pension_returns_404` | 5 | User B blocked from A's pension edit |
| 72 | `test_integration/test_access_control.py` | `TestRetirementAccessControl` | `test_update_pension_returns_404` | 5 | User B blocked from A's pension update |
| 73 | `test_integration/test_access_control.py` | `TestRetirementAccessControl` | `test_delete_pension_returns_404` | 5 | User B blocked from A's pension delete |
| 74 | `test_integration/test_access_control.py` | `TestMortgageAccessControl` | `test_mortgage_dashboard_returns_404` | 5 | User B blocked from A's mortgage |
| 75 | `test_integration/test_access_control.py` | `TestMortgageAccessControl` | `test_mortgage_setup_returns_404` | 5 | User B blocked from A's mortgage setup |
| 76 | `test_integration/test_access_control.py` | `TestAutoLoanAccessControl` | `test_auto_loan_dashboard_returns_404` | 5 | User B blocked from A's auto loan |
| 77 | `test_integration/test_access_control.py` | `TestInvestmentAccessControl` | `test_investment_dashboard_returns_404` | 5 | User B blocked from A's investment |
| 78 | `test_integration/test_access_control.py` | `TestInvestmentAccessControl` | `test_investment_update_returns_404` | 5 | User B blocked from A's investment update |

**Total new tests: 78**

---

## 10. Phase 8E Test Gate Checklist (Expanded)

- [ ] **`pytest` passes** (all existing tests plus new isolation tests)
  - Verified by: running `pytest` after all WUs complete
  - Expected: all existing ~900+ tests pass; all 78 new tests pass

- [ ] **Registration creates a new user with default settings**
  - Verified by: tests #2, #11 (`test_register_success`, `test_register_user_success`)
  - Checks: User record exists, UserSettings row exists, Baseline scenario exists

- [ ] **New user can log in and sees an empty budget (no data from the seeded user)**
  - Verified by: tests #8, #10 (`test_new_user_can_login`, `test_new_user_sees_empty_grid`)
  - Checks: Login succeeds, grid page renders with no transaction data

- [ ] **Data isolation tests pass: user A cannot see user B's data on any endpoint**
  - Verified by: tests #23-#42 (TestGridIsolation through TestRetirementIsolation)
  - Coverage: budget grid, accounts, templates, transfers, salary, savings, categories, charts, settings, retirement

- [ ] **Direct object access by ID returns 404 for unauthorized users**
  - Verified by: tests #43-#78 (all TestAccessControl classes)
  - Coverage: accounts, templates, transactions, transfers, salary, savings, categories, retirement, mortgage, auto loan, investment

- [ ] **user_id audit checklist is complete with all queries reviewed**
  - Verified by: Section 4 of this document (Query Audit Results)
  - All ~150+ queries across 16 route files and 7 service files documented
  - Result: No critical unscoped queries found at route level; defense-in-depth improvements applied in WU-2

---

## 11. File Summary

### New Files

| File | WU | Purpose |
|------|----|---------|
| `app/templates/auth/register.html` | 1 | Registration form template |
| `app/utils/auth_helpers.py` | 2 | Reusable ownership verification helpers |
| `tests/test_utils/test_auth_helpers.py` | 2 | Tests for auth helpers |
| `tests/test_integration/test_data_isolation.py` | 4 | Page visibility isolation tests |
| `tests/test_integration/test_access_control.py` | 5 | Direct object access tests |

### Modified Files

| File | WU | Changes |
|------|----|---------|
| `app/services/auth_service.py` | 1 | Add `register_user()` function |
| `app/routes/auth.py` | 1 | Add `register_form()` and `register()` routes |
| `app/templates/auth/login.html` | 1 | Add "Create an account" link |
| `app/services/credit_workflow.py` | 2 | Add `user_id` parameter to `mark_as_credit()` and `unmark_credit()` |
| `app/services/carry_forward_service.py` | 2 | Add `user_id` parameter to `carry_forward_unpaid()` |
| `app/routes/transactions.py` | 2 | Update calls to credit_workflow and carry_forward_service |
| `tests/test_services/test_credit_workflow.py` | 2 | Update existing tests for new signature; add wrong-user tests |
| `tests/conftest.py` | 3 | Add `seed_second_user`, `seed_second_periods`, `second_auth_client`, `seed_full_user_data`, `seed_full_second_user_data` fixtures |
