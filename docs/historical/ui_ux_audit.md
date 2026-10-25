> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel UI/UX Audit

## Executive Summary

The Shekel budget app has grown from a focused biweekly paycheck tool to a comprehensive personal finance application across six development phases, but its navigation and information architecture have not been restructured to accommodate this growth. The most critical issues are: (1) the navbar has 11 items with confusing overlap ("Accounts" vs "Accounts & Savings"), duplicate icons, and no grouping -- adding Phase 7 Scenarios will make it unworkable; (2) recurring transactions are called "Templates" throughout the UI, which is developer jargon invisible to users; (3) configuration is scattered across seven different pages with no consistent pattern; and (4) the two parallel account views (Accounts at `/accounts` and Accounts & Savings at `/savings`) create confusion about which page serves what purpose. The core budget grid and payday workflow are well-designed, but the surrounding navigation and feature discoverability degrade significantly as users move beyond the grid.

---

## 1. Navigation and Information Architecture

### Findings

**1.1 [Critical] Two confusingly similar "Accounts" navbar items**

The navbar contains both "Accounts" (`bi-bank`) and "Accounts & Savings" (`bi-wallet2`). A user looking at their mortgage, savings balance, or checking account has no way to know which link to click.

- "Accounts" (`/accounts`, `accounts/list.html`) is a CRUD management page: a flat table of accounts with anchor balance editing, plus account type management in a sidebar card.
- "Accounts & Savings" (`/savings`, `savings/dashboard.html`) is a dashboard: account cards grouped by category (Asset/Liability/Retirement/Investment), emergency fund metrics, and savings goals.

The v3 addendum section 8.1 specifies a unified accounts dashboard organized by category. The `savings/dashboard.html` template implements this, but the original `accounts/list.html` was never removed or merged. Both are accessible from the navbar.

Affected files: `app/templates/base.html:46-75`, `app/templates/accounts/list.html`, `app/templates/savings/dashboard.html`

```html
<!-- base.html lines 62-75 -- both items exist side by side -->
<li class="nav-item">
  <a class="nav-link" href="{{ url_for('accounts.list_accounts') }}">
    <i class="bi bi-bank"></i> Accounts
  </a>
</li>
...
<li class="nav-item">
  <a class="nav-link" href="{{ url_for('savings.dashboard') }}">
    <i class="bi bi-wallet2"></i> Accounts & Savings
  </a>
</li>
```

**1.2 [Critical] Navbar has 11 items and cannot scale**

Current items: Budget, Templates, Salary, Accounts, Transfers, Accounts & Savings, Retirement, Charts, Categories, Pay Periods, Settings. Phase 7 adds Scenarios (12th item). Phases 8-10 could add more. The flat list has no grouping, no dropdowns, and no visual hierarchy. On mobile, the hamburger menu would contain 12+ undifferentiated items.

Affected file: `app/templates/base.html:45-101`

**1.3 [Major] Duplicate icon usage: bi-wallet2**

The `bi-wallet2` icon is used for both "Salary" and "Accounts & Savings" in the navbar. These are unrelated features. A user scanning the navbar by icon cannot distinguish them.

```html
<!-- base.html line 58 -->
<i class="bi bi-wallet2"></i> Salary
<!-- base.html line 73 -->
<i class="bi bi-wallet2"></i> Accounts & Savings
```

Additionally, on the charts dashboard, the Net Pay Trajectory chart header uses `bi-wallet2`:

```html
<!-- charts/dashboard.html line 103 -->
<i class="bi bi-wallet2"></i> Net Pay Trajectory
```

Affected files: `app/templates/base.html:58,73`, `app/templates/charts/dashboard.html:103`

**1.4 [Major] Setup-only pages occupy primary navigation**

Categories and Pay Periods are setup pages visited rarely after initial configuration (once for pay periods, a few times for categories during onboarding, then almost never). They occupy the same visual weight as the budget grid, which is used multiple times per week.

- Categories (`/categories`): Set up expense groups once, occasionally add a new one.
- Pay Periods (`/pay-periods/generate`): Run once at setup, extend every 1-2 years.

Affected files: `app/templates/base.html:86-95`, `app/routes/categories.py`, `app/routes/pay_periods.py`

**1.5 [Major] Transfers page is disconnected from accounts**

Transfers (`/transfers`) are movements between accounts, but they exist as a separate top-level page with no navigational connection to the accounts they affect. A user looking at their checking account on the savings dashboard has no path to "set up a recurring transfer from this account" without navigating to a separate top-level page.

Affected files: `app/templates/base.html:66-70`, `app/routes/transfers.py`

**1.6 [Minor] Navbar active state not indicated**

The navbar does not highlight the currently active page. All `nav-link` elements have the same appearance regardless of which page the user is on. Bootstrap supports this via the `active` class on `nav-link`, but it is not applied conditionally.

Affected file: `app/templates/base.html:46-100`

**1.7 [Minor] No navbar collapse ordering strategy**

On screens narrower than the `md` breakpoint, all 11 items collapse into the hamburger menu in the same flat order. There is no prioritization -- the rarely-used "Pay Periods" item is as prominent as the daily-use "Budget" item.

Affected file: `app/templates/base.html:40-101`

---

## 2. Settings and Configuration Sprawl

### Findings

**2.1 [Major] Retirement planning settings live on the retirement dashboard, not on Settings**

The retirement dashboard (`/retirement`) contains a "Retirement Settings" card at the bottom with three fields: Safe Withdrawal Rate, Planned Retirement Date, and Estimated Retirement Tax Rate. These are stored in `UserSettings` but are not on the `/settings` page.

A user going to Settings to configure their planned retirement date will not find it there.

```html
<!-- retirement/dashboard.html lines 162-196 -->
<div class="card mb-4">
  <div class="card-header">
    <h6 class="mb-0"><i class="bi bi-gear"></i> Retirement Settings</h6>
  </div>
  <div class="card-body">
    <form method="POST" action="{{ url_for('retirement.update_settings') }}">
      ...
      <label class="form-label">Safe Withdrawal Rate (%)</label>
      <label class="form-label">Planned Retirement Date</label>
      <label class="form-label">Est. Retirement Tax Rate (%)</label>
      ...
    </form>
  </div>
</div>
```

Affected files: `app/templates/retirement/dashboard.html:162-196`, `app/routes/retirement.py:508-548`

**2.2 [Major] Tax configuration buried under Salary with no cross-link from Settings**

Federal tax brackets, FICA rates, and state tax configuration live at `/salary/tax-config`, accessible only from the salary section. The Settings page does not mention or link to tax configuration.

Affected files: `app/routes/salary.py:575-680`, `app/templates/salary/tax_config.html`

**2.3 [Major] HYSA, mortgage, auto loan, and investment parameters all use different configuration patterns**

- HYSA: Separate detail page (`/accounts/<id>/hysa`) with a form at the bottom.
- Mortgage: Dashboard page (`/accounts/<id>/mortgage`) with parameter editing in a side-by-side card.
- Auto loan: Dashboard page (`/accounts/<id>/auto_loan`) with the same side-by-side pattern.
- Investment: Dashboard page (`/accounts/<id>/investment`) with parameters in a card at the bottom.
- Pension: Separate form page (`/retirement/pension`) with a list-and-form pattern.

There is no consistent convention for "configure the parameters of an account type." Some use inline editing on a dashboard, some have separate form pages, and the patterns vary by account type.

Affected files: `app/templates/accounts/hysa_detail.html`, `app/templates/mortgage/dashboard.html:75-119`, `app/templates/auto_loan/dashboard.html`, `app/templates/investment/dashboard.html:139-208`, `app/templates/retirement/pension_form.html`

**2.4 [Minor] Settings page help text references Phase 7**

The "Default Inflation Rate" field has help text: "Used for Phase 7 expense inflation adjustments." This is developer language that should not be in the UI.

```html
<!-- settings/settings.html line 31 -->
help_text="Used for Phase 7 expense inflation adjustments."
```

Affected file: `app/templates/settings/settings.html:28-31`

**2.5 [Minor] Settings page is very sparse**

The settings page contains only four fields (grid periods, inflation rate, low balance threshold, default grid account) in a narrow `col-lg-4` column. Given how much configuration exists across the app, this page feels incomplete -- a user would expect more here.

Affected file: `app/templates/settings/settings.html`

**2.6 [Minor] Configuration inventory**

The following configuration items exist across the app:

| Configuration            | Location                                           | Frequency of Change             |
| ------------------------ | -------------------------------------------------- | ------------------------------- |
| Grid default periods     | Settings page                                      | Rarely                          |
| Default inflation rate   | Settings page                                      | Rarely                          |
| Low balance threshold    | Settings page                                      | Rarely                          |
| Default grid account     | Settings page                                      | Rarely                          |
| Categories               | Categories page (`/categories`)                    | Rarely after setup              |
| Pay periods              | Pay Periods page (`/pay-periods/generate`)         | Once at setup, yearly extension |
| Tax brackets (federal)   | Salary > Tax Config (`/salary/tax-config`)         | Yearly                          |
| FICA rates               | Salary > Tax Config (`/salary/tax-config`)         | Yearly                          |
| State tax rate           | Salary > Tax Config (`/salary/tax-config`)         | Rarely                          |
| HYSA APY and compounding | HYSA detail page (`/accounts/<id>/hysa`)           | Occasionally                    |
| Mortgage params          | Mortgage dashboard (`/accounts/<id>/mortgage`)     | Rarely                          |
| Auto loan params         | Auto loan dashboard (`/accounts/<id>/auto_loan`)   | Rarely                          |
| Investment params        | Investment dashboard (`/accounts/<id>/investment`) | Occasionally                    |
| Pension profile          | Retirement > Pension (`/retirement/pension`)       | Rarely                          |
| Safe withdrawal rate     | Retirement dashboard bottom                        | Rarely                          |
| Planned retirement date  | Retirement dashboard bottom                        | Rarely                          |
| Est. retirement tax rate | Retirement dashboard bottom                        | Rarely                          |
| Account types            | Accounts page sidebar (`/accounts`)                | Rarely                          |

---

## 3. Nomenclature and Labeling

### Findings

**3.1 [Critical] "Templates" is developer jargon for recurring transactions**

Throughout the entire app, recurring transactions are called "Templates." The navbar says "Templates," the page titles say "Transaction Templates," the form says "New Template" / "Edit Template," the flash messages say "Template created," and URLs use `/templates`. A typical user thinks in terms of "recurring bills," "recurring income," or "scheduled transactions" -- not "templates."

Affected files (non-exhaustive):

- `app/templates/base.html:51-54` (navbar: `<i class="bi bi-file-earmark-ruled"></i> Templates`)
- `app/templates/templates/list.html:23` (heading: `Transaction Templates`)
- `app/templates/templates/form.html:35` (heading: `New Transaction Template` / `Edit Transaction Template`)
- `app/routes/templates.py:165` (flash: `Template '{template.name}' created.`)
- Onboarding banner in `base.html:174-176` ("Create transaction templates")

```html
<!-- base.html lines 51-54 -->
<li class="nav-item">
  <a class="nav-link" href="{{ url_for('templates.list_templates') }}">
    <i class="bi bi-file-earmark-ruled"></i> Templates
  </a>
</li>
```

**3.2 [Major] "Transfer Template" compounds the jargon**

Transfer definitions (recurring transfers) are called "Transfer Templates" in page titles, form headings, flash messages, and the list view. The navbar simply says "Transfers," but the pages themselves say "Transfer Templates."

```html
<!-- transfers/form.html line 35 -->
<h4>
  <i class="bi bi-arrow-left-right"></i>
  {{ 'Edit' if template else 'New' }} Transfer Template
</h4>
```

Affected files: `app/templates/transfers/list.html:23`, `app/templates/transfers/form.html:35-36`

**3.3 [Major] "Anchor Balance" is not intuitive**

The budget grid and accounts page use "Anchor Balance" as the term for the actual, verified checking account balance. This is an internal modeling concept. A user would understand "Current Balance," "Verified Balance," or "True-Up Balance" more readily.

```html
<!-- accounts/list.html line 22 -->
<th scope="col" class="text-end">Anchor Balance</th>
```

The grid uses the phrase "as of [date]" next to the balance, which helps contextually, but the accounts list table header says "Anchor Balance" without any tooltip or explanation.

Affected files: `app/templates/accounts/list.html:22`, `app/templates/grid/_anchor_edit.html`, `app/templates/grid/grid.html:9`

**3.4 [Major] Inconsistent amount terminology**

- Transaction templates use "Default Amount" (`app/templates/templates/form.html:50`, `app/templates/templates/list.html:37`)
- Transactions use "Amount" in the quick edit and "estimated_amount" in the data model
- Transfers use "Amount" consistently
- The grid add-transaction modal uses "Amount" (`grid/grid.html:305`)
- Salary uses "Annual Salary"
- The form label says "Default Amount" but the column header on the grid says nothing -- amounts are just numbers

A user editing a recurring bill would not understand why the amount is called "default" (it is the amount used when generating future instances, which can be overridden per-period -- but this is never explained).

**3.5 [Major] "Accounts & Savings" label is misleading**

The page at `/savings` is titled "Accounts & Savings" in the heading but its purpose is the unified accounts dashboard (all account types grouped by category). The "& Savings" suffix implies it only covers savings accounts, when in fact it shows checking, HYSA, mortgage, auto loan, retirement, and investment accounts. The page also contains savings goals and emergency fund metrics, which may be why "Savings" was added, but the current name conflates two different concepts.

```html
<!-- savings/dashboard.html lines 5-6 -->
<h4><i class="bi bi-wallet2"></i> Accounts & Savings</h4>
```

Affected file: `app/templates/savings/dashboard.html:6`

**3.6 [Minor] Mortgage interest rate input shows decimal, display shows percentage**

On the mortgage dashboard, the interest rate input accepts a decimal value (e.g., `0.06500`) while the adjacent display shows the percentage (e.g., `6.500%`). This is inconsistent with the investment params page, which accepts the percentage directly (e.g., `7` for 7%).

```html
<!-- mortgage/dashboard.html lines 92-94 -->
<input type="number" class="form-control" id="interest_rate" name="interest_rate"
       value="{{ "%.5f"|format(params.interest_rate|float) }}" step="0.00001" min="0" max="1">
<span class="input-group-text">{{ "%.3f"|format(params.interest_rate|float * 100) }}%</span>
```

Affected file: `app/templates/mortgage/dashboard.html:92-94`

**3.7 [Minor] Recurrence pattern labels inconsistently duplicated**

The `pattern_labels` dictionary is defined independently at the top of four different template files (`templates/list.html`, `templates/form.html`, `transfers/list.html`, `transfers/form.html`). If a pattern name changes, it must be updated in four places.

**3.8 [Minor] Flash messages expose internal schema validation details**

Multiple routes send validation errors directly to the user: `flash(f"Validation error: {errors}", "danger")`. The `errors` dict from Marshmallow contains field names like `default_amount`, `recurrence_pattern`, etc., which are developer-facing. A user would see something like `Validation error: {'default_amount': ['Missing data for required field.']}`.

Affected files: `app/routes/templates.py:87-88`, `app/routes/transfers.py:90-91`, `app/routes/accounts.py:103-106`, and many others.

**3.9 [Minor] "P&I" used without explanation**

The mortgage dashboard displays "Monthly P&I" (Principal & Interest) without explaining the abbreviation. A user unfamiliar with mortgage terminology might not know what P&I means.

```html
<!-- mortgage/dashboard.html line 51 -->
<span class="text-muted">Monthly P&I</span>
```

Affected file: `app/templates/mortgage/dashboard.html:51`

**3.10 [Minor] Account type names shown raw from database**

Account type names are displayed using `name|capitalize` or `name|replace('_', ' ')|title`, which means database values like `auto_loan`, `roth_401k`, `traditional_ira` are shown as "Auto*loan", "Roth_401k", "Traditional_ira" -- with underscores visible. The `|replace('*', ' ')` filter is used inconsistently.

Affected files: `app/templates/accounts/list.html:30`, `app/templates/savings/dashboard.html:58`, `app/templates/investment/dashboard.html:9`

---

## 4. Workflow Analysis

### 4A. Create Recurring Expense

**Steps:**

1. User clicks "Templates" in navbar (must know that "Templates" means recurring transactions).
2. Lands on `/templates` -- list of all templates.
3. Clicks "New Template" button (top right).
4. Fills out form: Name, Default Amount, Type (defaults to Expense), Account, Category, Recurrence Pattern, and pattern-specific fields.
5. Clicks "Create."
6. Redirected to templates list with success flash.
7. To verify on the grid, user navigates to "Budget" in navbar.

**Friction points:**

- **[Critical]** Step 1 requires knowing "Templates" means "recurring transactions." No tooltip, no helper text, no alias. The onboarding checklist also says "Create transaction templates."
- **[Major]** The form heading says "New Transaction Template" -- still jargon.
- **[Minor]** After creation, the user is redirected to the templates list, not the budget grid where they could immediately see the generated transactions. A "View on Grid" link would help.
- **[Minor]** The "Category" dropdown shows all categories including income categories when creating an expense. No filtering by type.
- **Total clicks:** 4 (navbar > New Template > fill form > Create). Acceptable count, but discoverability is poor.

### 4B. Set Up Transfer Between Accounts

**Steps:**

1. User clicks "Transfers" in navbar.
2. Lands on `/transfers` -- list of transfer templates.
3. Clicks "New Transfer" button.
4. Fills out form: Name, Amount, From Account, To Account, Recurrence Pattern.
5. Clicks "Create."
6. Transfer appears in the TRANSFERS section of the budget grid.

**Friction points:**

- **[Major]** There is no way to initiate a transfer from the savings dashboard account cards. Users seeing their checking and savings accounts side by side cannot click "Transfer to this account" -- they must navigate away to `/transfers`.
- **[Major]** The form heading says "New Transfer Template" -- the user just wants to set up a transfer, not create a "template."
- **[Minor]** The form does not prevent setting From and To to the same account. There is no client-side validation for this.
- **[Minor]** On the savings dashboard, savings goal cards have a small transfer icon button that links to the general "New Transfer" page, but it does not pre-fill the target account.

### 4C. Deduction to Account Linking

**Steps:**

1. User navigates to Salary > clicks profile > Edit.
2. Scrolls down to Deductions section.
3. Clicks "Add Deduction" to expand the form.
4. Fills out deduction details (name, timing, method, amount, per year).
5. At the bottom of the deduction form, finds "Target Account" dropdown.
6. Selects a retirement/investment account.
7. Clicks "Add."

**Friction points:**

- **[Major]** The "Target Account" field is at the very bottom of a dense, multi-row form with 8+ fields. A user adding a 401(k) deduction could easily miss it, especially on first setup.
- **[Major]** The help text says "Credits deduction to a retirement/investment account" -- technically accurate but does not explain the practical effect (that the deduction amount will be reflected as a contribution in the target account's growth projection).
- **[Minor]** There is no visual feedback when a paycheck is received confirming that the deduction was credited to the target account. The user must navigate to the investment dashboard separately to verify.
- **[Minor]** Only retirement/investment accounts appear in the dropdown. If a user wants to link a deduction to a savings account (e.g., HSA contribution to a savings-type account), they cannot.

```html
<!-- salary/_deductions_section.html lines 148-158 -->
<label class="form-label">Target Account</label>
<select name="target_account_id" class="form-select form-select-sm">
  <option value="">-- None --</option>
  {% for acct in investment_accounts %}
  <option value="{{ acct.id }}">{{ acct.name }}</option>
  {% endfor %}
</select>
<small class="text-muted"
  >Credits deduction to a retirement/investment account</small
>
```

### 4D. Ad-Hoc Expense from Budget Grid

**Steps:**

1. User is on the budget grid (`/`).
2. Clicks on an empty cell (the `--` dash) in the expense row for the desired category and period.
3. An inline quick-create input appears (HTMX-loaded).
4. Types the amount and presses Enter.
5. A new transaction is created with status "projected," named after the category.

Alternative path via modal:

1. User clicks "Add Transaction" button in the grid header area.
2. A modal appears with fields: Name, Amount, Type, Category, Pay Period.
3. Fills out and clicks "Add."

**Friction points:**

- **[Minor]** The click-to-create on empty cells is powerful but not discoverable. The `--` dash does not look clickable. There is no tooltip or hover indicator beyond a subtle background change.
- **[Minor]** The full edit popover (tier 2) requires clicking a small `>` expand button on the quick edit form. The button is 20px wide and uses muted text color -- easy to miss.
- **[Minor]** An ad-hoc transaction created via the grid has no category selection in the quick-create mode -- it uses the row's category automatically. This is good UX. The modal path does require category selection, which is appropriate for less constrained creation.
- Overall, this workflow is well-designed for the power user. The grid is the strongest part of the UI.

### 4E. Payday Workflow

**Steps (per project_requirements_v2.md section 3):**

1. **True up checking balance:** Click the anchor balance on the grid, type actual bank balance, press Enter. (HTMX inline edit.)
2. **Mark paycheck as received:** Click the paycheck cell in the INCOME section, expand to full edit, click "Received" button. (HTMX quick action.)
3. **Carry forward unpaid items:** If past periods have unpaid items, a "Carry Fwd" button appears in the period header. Click it.
4. **Mark cleared expenses:** Click each cleared expense cell, expand, click "Done."
5. **Mark credit card expenses:** Click expense cell, click "Credit" to mark it and auto-generate a payback entry in the next period.
6. **Check projections:** Look at the balance row in the tfoot, which updates live via HTMX.

**Friction points:**

- **[Minor]** Steps 2 and 4 each require two interactions per item (click cell to show quick edit, then click expand `>` button, then click the status button). For a user with 15-20 line items, this is repetitive. A batch "mark all as done" feature is not available.
- **[Minor]** The "Carry Fwd" button only appears for past periods, which is correct, but its small `btn-xs` size and position inside the period header cell may be overlooked.
- Overall, the payday workflow is functional and achievable from the grid without leaving the page. The grid is designed well for this use case.

### 4F. Unified Account View

**Steps:**

1. User wants to see all account balances.
2. Clicks "Accounts & Savings" (or "Accounts"?).
3. If they clicked "Accounts" (`/accounts`), they see a flat CRUD table with anchor balances. No category grouping, no projections, no cards.
4. If they clicked "Accounts & Savings" (`/savings`), they see the category-grouped dashboard with balance cards, projections, emergency fund metrics, and savings goals.
5. From the savings dashboard, they can click detail buttons to navigate to HYSA, mortgage, auto loan, or investment dashboards.

**Friction points:**

- **[Critical]** The two-page split means a user must guess which "Accounts" link to click. The wrong choice lands them on a CRUD page when they wanted a dashboard (or vice versa).
- **[Major]** The savings dashboard is the unified view described in v3 addendum 8.1, but it is not called "Accounts" -- it is called "Accounts & Savings" and linked from a separate navbar item.
- **[Minor]** The savings dashboard "Back to Accounts" link on mortgage, auto loan, and investment dashboards all link to `/savings` (the dashboard), not `/accounts` (the CRUD page). This is correct behavior but the label "Back to Accounts" is confusing when the page is called "Accounts & Savings."

```html
<!-- mortgage/dashboard.html line 7-9 -->
<a
  href="{{ url_for('savings.dashboard') }}"
  class="btn btn-outline-secondary btn-sm"
>
  <i class="bi bi-arrow-left"></i> Back to Accounts
</a>
```

### 4G. Mortgage Payoff Scenarios

**Steps:**

1. User navigates to "Accounts & Savings" dashboard.
2. Finds the mortgage account card in the "Liability" section.
3. Clicks the house icon button to go to the mortgage dashboard.
4. Scrolls down past the loan summary, parameters form, escrow section, and (if ARM) rate history.
5. Reaches the "Payoff Calculator" card.
6. Selects a tab: "Extra Payment" or "Target Date."
7. Enters values and clicks "Calculate."
8. Results appear via HTMX below the form.

**Friction points:**

- **[Minor]** The payoff calculator is buried below 3-4 other sections on a long page. A user specifically looking for payoff scenarios must scroll significantly.
- **[Minor]** The payoff chart at the bottom of the page shows the standard balance-over-time curve, but the comparison chart (standard vs. accelerated) only appears in the HTMX results after calculating -- not in the main chart area. These are separate charts.
- **[Minor]** The navigation path (navbar > Accounts & Savings > mortgage card > scroll to payoff) is 3 clicks + scrolling. Discoverable but could be more direct.
- The payoff calculator itself is well-designed with tabs for two modes and a slider for the extra payment amount.

### 4H. Retirement Readiness Review

**Steps:**

1. User clicks "Retirement" in navbar.
2. Lands on the retirement dashboard (`/retirement`).
3. Sees: Sensitivity sliders (SWR, Return Rate), Gap Analysis, Pension Details, Retirement Accounts table, Retirement Settings.
4. Can adjust sliders to see how changes affect the gap analysis (HTMX live update).
5. Can click individual account names in the table to go to their investment dashboards.
6. Can click "Manage Pensions" to add/edit pension profiles.

**Friction points:**

- **[Major]** The retirement dashboard ties together data from multiple sources (salary profiles, paycheck deductions, pension profiles, account balances, user settings), but the relationships are not explained. A user does not see where the "Pre-retirement Net Monthly" number comes from (it is derived from the salary profile's net pay). There is no "how is this calculated?" link or tooltip.
- **[Minor]** Retirement settings (SWR, planned date, tax rate) are at the bottom of the page. A user might expect these at the top since they fundamentally change all the projections displayed above.
- **[Minor]** The pension management is on a separate page (`/retirement/pension`) accessed via a button in the header. After creating a pension, the user is redirected back to the retirement dashboard. This is a reasonable flow.
- Overall, the retirement dashboard is information-dense but functional.

---

## 5. Page-Level UI Review

### Budget Grid (`/`, `grid/grid.html`)

- **Clear title/purpose:** Yes -- shows the account name and balance prominently at the top.
- **Primary action:** The grid itself is the primary interaction surface. "Add Transaction" button is visible. "Add Transfer" button is also present.
- **Empty state:** Two handled: `no_setup.html` (no scenario) and `no_periods.html` (no pay periods). Both provide guidance.
- **Navigation:** Period navigation arrows and quick-select buttons (3P, 6P, 6M, 1Y, 2Y) are clear and functional. No breadcrumbs (this is the home page).
- **HTMX interactions:** Inline editing, quick-create, balance row refresh all work with loading states. The popover edit form appears via HTMX.
- **Loading states:** `td.htmx-loading` CSS spinner exists.
- **Consistency:** This is the best-designed page in the app.
- **Issue [Minor]:** The quick-select labels "3P", "6P", "6M", "1Y", "2Y" are compact but not self-explanatory. "3P" means "3 periods" but a user might not know that immediately.

### Templates List (`/templates`, `templates/list.html`)

- **Page title:** "Transaction Templates" -- jargon issue (see 3.1).
- **Primary action:** "New Template" button, top right.
- **Empty state:** Alert box saying "No templates yet. Create one to start auto-generating transactions." Uses the word "templates" again.
- **Navigation:** No breadcrumbs on the list page.
- **Issue [Major]:** Page title in `<title>` tag says "Transaction Templates" and the heading says "Transaction Templates."

### Templates Form (`/templates/new`, `/templates/<id>/edit`, `templates/form.html`)

- **Breadcrumbs:** Present, linking back to "Templates" (jargon).
- **Form layout:** Well-organized with basic fields, then recurrence rule section separated by `<hr>`.
- **Issue [Minor]:** The "Effective from" field on edit appears below the recurrence rule section with help text "Leave blank to use today's date." This is a technical concept (when to start regenerating future transactions) that could be confusing.
- **Recurrence preview:** Dynamically shows next 5 occurrences -- good UX.

### Salary List (`/salary`, `salary/list.html`)

- **Clear title/purpose:** "Salary" with estimated net pay shown per profile.
- **Primary action:** Links to edit each profile, view breakdown, view projection.
- **Issue [Minor]:** The list shows net pay but does not explain that this is the estimated take-home after taxes and deductions.

### Salary Form (`/salary/<id>/edit`, `salary/form.html`)

- **Complex form:** Salary details, W-4 settings, raises (collapsible), deductions (collapsible), quick links to breakdown and projection.
- **W-4 section:** Has excellent helper text explaining W-4 steps (3, 4a, 4b, 4c).
- **Issue [Minor]:** The raises and deductions sections use HTMX collapsible forms. The "Add Deduction" toggle button uses `data-toggle-target` which appears to be custom JS, not standard Bootstrap collapse. If the JS fails to load, the form would be hidden.
- **Issue [Minor]:** The deduction form has 8+ fields in a dense multi-row layout. On smaller screens this would be difficult to use.

### Accounts List (`/accounts`, `accounts/list.html`)

- **Page title:** "Accounts."
- **Primary action:** "New Account" button.
- **Two-column layout:** Accounts table on the left, Account Types card on the right.
- **Issue [Major]:** This page shows a CRUD table while the savings dashboard shows a richer card-based view. Both are accessible from the navbar. The CRUD page should be either merged into the dashboard or made accessible only from the dashboard.
- **Issue [Minor]:** The Account Types sidebar allows inline editing of type names, but the save button appears only when the input changes. This is a nice interaction but could confuse users who don't notice the button.

### Savings Dashboard (`/savings`, `savings/dashboard.html`)

- **Clear title/purpose:** "Accounts & Savings" -- slightly misleading (see 3.5).
- **Layout:** Category-grouped account cards, emergency fund card, savings goals. Well-structured.
- **Primary action:** "New Goal" button in the header.
- **Issue [Minor]:** No way to add a new account from this dashboard. The "New Account" action is only on the separate Accounts page.
- **Detail navigation:** Each account card has a small icon button (house for mortgage, graph for investment, etc.) that navigates to the type-specific dashboard. Functional but small.

### HYSA Detail (`/accounts/<id>/hysa`, `accounts/hysa_detail.html`)

- **Page:** Shows APY, projected balances, and a period-by-period interest projection table.
- **Issue [Minor]:** No breadcrumbs to navigate back. The back button would need to be added.
- **Issue [Minor]:** The projection table can be very long (one row per pay period across 2 years). No pagination or accordion.

### Mortgage Dashboard (`/accounts/<id>/mortgage`, `mortgage/dashboard.html`)

- **Layout:** Two-column summary and parameter editing, escrow section, rate history (ARM), payoff calculator, balance chart.
- **Breadcrumb-like:** "Back to Accounts" button in header area.
- **Issue [Minor]:** The interest rate input accepts raw decimal values (`0.06500`) while investment pages accept percentages (`7` for 7%). This inconsistency could cause data entry errors.
- **Issue [Minor]:** The page is long. Five distinct sections stacked vertically with no anchor links or tabs.

### Auto Loan Dashboard (`/accounts/<id>/auto_loan`, `auto_loan/dashboard.html`)

- **Layout:** Similar to mortgage but simpler (no escrow, no ARM, no payoff calculator).
- **Consistency:** Follows the same card-based pattern as mortgage. Good.

### Investment Dashboard (`/accounts/<id>/investment`, `investment/dashboard.html`)

- **Layout:** Summary card, YTD contribution progress, employer contribution details, growth projection chart with horizon slider, parameters form.
- **Issue [Minor]:** The parameters form at the bottom has employer contribution fields (Flat %, Match %, Match Cap %) that are always visible even when the employer type is set to "None." These should be conditionally shown.
- **Good UX:** The horizon slider for the growth chart updates via HTMX with debounce. Smooth interaction.

### Retirement Dashboard (`/retirement`, `retirement/dashboard.html`)

- **Layout:** Sensitivity sliders, gap analysis, pension details, accounts table, settings.
- **Good UX:** The sensitivity sliders for SWR and Return Rate update the gap analysis in real-time via HTMX.
- **Issue [Major]:** No breadcrumbs. No indication of current location in the app hierarchy.
- **Issue [Minor]:** The gap analysis disclaimer at the bottom is an `alert-secondary` that is easy to overlook.

### Charts Dashboard (`/charts`, `charts/dashboard.html`)

- **Layout:** 6 chart cards loaded progressively via HTMX with skeleton loading states.
- **Good UX:** Progressive loading prevents a single slow query from blocking the entire page. Each chart has its own loading spinner.
- **Good UX:** Chart cards have interactive controls (account filters, period range selectors, profile selectors) loaded inline.
- **Issue [Minor]:** The page heading is `<h1>` while all other pages use `<h4>`. This is a visual inconsistency.

```html
<!-- charts/dashboard.html line 5 -->
<h1 class="mb-4"><i class="bi bi-bar-chart-line"></i> Charts</h1>
```

### Categories (`/categories`, `categories/list.html`)

- **Layout:** Two-column: grouped categories on left, add form on right.
- **Empty state:** Alert saying "No categories yet."
- **Issue [Minor]:** Categories can only be deleted, not renamed. The form is add-only.

### Pay Periods (`/pay-periods/generate`, `pay_periods/generate.html`)

- **Layout:** Simple form with three fields.
- **Breadcrumbs:** Present, linking back to Budget.
- **Issue [Minor]:** There is no way to view existing pay periods or extend the schedule. The page only shows the generation form. After generating, the user is redirected to the grid. To extend periods, they must come back and re-enter the next start date.

### Settings (`/settings`, `settings/settings.html`)

- **Layout:** Simple form with four fields in a narrow column.
- **Breadcrumbs:** Present.
- **Issue [Minor]:** Very sparse for a "Settings" page. See finding 2.5.

---

## 6. Future-Proofing for Phase 7 (Scenarios)

### Findings

**6.1 [Critical] Navbar cannot absorb a 12th item**

Adding "Scenarios" as a 12th navbar item would make the navigation bar unusable, especially on medium-width screens where items start to wrap before the hamburger breakpoint. The nav must be restructured before Phase 7 is implemented. A dropdown/grouping approach is necessary.

**6.2 [Critical] No global "current scenario" indicator**

Phase 7 requires scenario-scoped views. Currently, the grid and all pages implicitly use the baseline scenario (hardcoded query: `filter_by(is_baseline=True)`). When scenarios exist, the user needs to know which scenario they are viewing/editing at all times. There is no UI element for this.

The navbar or a global status bar would need a scenario selector/indicator. This is not a minor addition -- it affects every page that queries scenario-scoped data (grid, salary, transfers, accounts, retirement, charts).

Affected files: Every route file that queries by `scenario_id` (grid.py, templates.py, transfers.py, salary.py, savings.py, retirement.py, charts.py).

**6.3 [Major] Grid template has no room for scenario controls**

The grid header bar (`grid/grid.html:6-56`) currently contains: account name + balance, period navigation arrows, quick-select buttons, "Add Transaction" button, and "Add Transfer" link. Adding a scenario selector/dropdown would crowd this already busy header area.

```html
<!-- grid/grid.html lines 6-56 -- the header area is already dense -->
<div
  class="d-flex align-items-center justify-content-between flex-wrap gap-2 mb-3"
>
  <div class="d-flex align-items-center gap-3">
    <!-- anchor balance display -->
  </div>
  <div class="d-flex align-items-center gap-2">
    <!-- period nav + add transaction + add transfer -->
  </div>
</div>
```

**6.4 [Major] Side-by-side scenario comparison requires layout changes**

The current page layout uses `container-fluid` with full-width content. For side-by-side comparison of two scenarios, the grid would need to either:

- Display two grids side by side (requiring responsive handling for the already-wide grid with sticky columns), or
- Show a single merged grid with comparison columns interleaved.

The current grid CSS (`grid-wrapper` with `max-height: calc(100vh - 160px)` and sticky columns/headers) would need significant reworking for a two-grid view.

**6.5 [Major] Nomenclature issues compound with scenarios**

If "Templates" is confusing now, "Scenario Templates" or "Cloned Templates" in Phase 7 will be worse. The jargon problem (finding 3.1) should be resolved before Phase 7 adds scenario-scoped variants of existing pages.

**6.6 [Minor] Charts page can accommodate scenario overlays**

The charts dashboard already uses HTMX-loaded fragments with interactive controls. Adding a scenario selector to each chart card is architecturally feasible. The chart rendering JS (`chart_theme.js`, `chart_balance.js`, etc.) would need to support multi-dataset overlays, but the template structure can handle it.

**6.7 [Minor] Retirement gap analysis is scenario-ready**

The gap analysis already supports parameter overrides via the sensitivity sliders. Scenario-scoped what-ifs ("what if I increase my 401k contribution") would follow the same pattern -- change input parameters and recalculate.

---

## 7. Visual and Interaction Consistency

### Findings

**7.1 [Major] Heading level inconsistency across pages**

- Charts dashboard uses `<h1>` for the page heading.
- All other pages use `<h4>` for the page heading.
- Some pages use `<h5>` for sub-section headings, others use `<h6>` inside card headers.

```html
<!-- charts/dashboard.html line 5 -->
<h1 class="mb-4"><i class="bi bi-bar-chart-line"></i> Charts</h1>

<!-- Every other page: -->
<h4><i class="..."></i> Page Title</h4>
```

Affected files: `app/templates/charts/dashboard.html:5` vs. all other page templates.

**7.2 [Major] Inconsistent breadcrumb presence**

Breadcrumbs are present on: Settings, Templates form, Transfers form, Pay Periods, Salary form. Breadcrumbs are absent on: Templates list, Transfers list, Accounts list, Savings dashboard, Retirement dashboard, Charts dashboard, Categories, Mortgage dashboard, Auto loan dashboard, Investment dashboard, HYSA detail.

There is no consistent rule for which pages get breadcrumbs and which do not. Detail pages (mortgage, investment, auto loan) have a "Back to Accounts" button instead of breadcrumbs, which serves a similar purpose but looks different.

**7.3 [Minor] Button style conventions**

- Primary actions generally use `btn-primary` or `btn btn-primary btn-sm`. Consistent.
- Cancel/back actions use `btn-outline-secondary`. Consistent.
- Destructive actions use `btn-outline-danger`. Consistent.
- The investment dashboard uses `btn-primary btn-sm` for "Save Parameters" while the mortgage dashboard uses the same pattern. Consistent.
- The grid uses `btn-warning btn-xs` for "Carry Fwd" buttons. The `btn-xs` class is custom (defined in app.css) and only used here.

**7.4 [Minor] Card structure consistency**

All Phase 3-6 dashboards use Bootstrap cards with `card-header` containing `<h6>` and `card-body`. This pattern is consistent across:

- Savings dashboard (emergency fund card)
- Mortgage dashboard (loan summary, parameters, escrow, rate history, payoff, chart)
- Investment dashboard (summary, contribution progress, employer details, growth chart, parameters)
- Retirement dashboard (sensitivity, gap analysis, pension, accounts, settings)
- Charts dashboard (6 chart cards)

Good consistency here.

**7.5 [Minor] Status colors are consistent**

- `--shekel-done: #2ECC71` (green) -- used for "done" and "received" badges.
- `--shekel-credit: #E67E22` (orange) -- used for "credit" badges.
- `--shekel-danger: #E74C3C` (red) -- used for negative balances and cancellation.
- These are defined in CSS custom properties and used consistently across the grid and transfer cells.

**7.6 [Minor] Chart styling is consistent**

All Chart.js charts use `chart_theme.js` for consistent theming. The payoff chart, growth chart, gap analysis chart, and charts dashboard charts all load this shared theme file. Good consistency.

**7.7 [Minor] Table class usage varies**

- Templates list: `table table-hover table-sm` with `thead class="table-light"`.
- Transfers list: Same pattern.
- Accounts list: Same pattern.
- Salary deductions table: `table table-sm` (no `table-hover`, no `table-light` thead).
- Mortgage loan summary: `table table-sm` (no hover, no thead styling).
- Retirement accounts table: `table table-sm` with plain `<thead>`.

The grid table uses its own class (`grid-table`) and is separate from standard tables. The non-grid tables have minor class inconsistencies.

**7.8 [Minor] Dark mode coverage is complete**

All pages respect the dark/light theme toggle. CSS custom properties are defined for both themes. Cards, tables, forms, modals, and charts all adapt. No pages appear broken in light mode based on template review.

**7.9 [Minor] Accessibility: focus indicators are well-implemented**

Custom `:focus-visible` styles are defined in app.css for buttons, form controls, select elements, and nav links. All use the accent color (`--shekel-accent`) with 2px offset. Skip link and visually-hidden content are present.

**7.10 [Minor] Loading state for HTMX**

The CSS defines `td.htmx-loading` with a spinner animation, but this only applies to `<td>` elements. HTMX loading states in card bodies (like the gap analysis container or chart containers) use a different pattern -- the card body starts with a spinner skeleton that is replaced on load. These two approaches are not unified but both work.

---

## Appendix: Full Inventory

### A. Navbar Items

| Label              | Route                                                 | Template                    | Icon                    | Visit Frequency                  | Issues                                                               |
| ------------------ | ----------------------------------------------------- | --------------------------- | ----------------------- | -------------------------------- | -------------------------------------------------------------------- |
| Budget             | `grid.index` (`/`)                                    | `grid/grid.html`            | `bi-grid-3x3`           | Every session                    | None                                                                 |
| Templates          | `templates.list_templates` (`/templates`)             | `templates/list.html`       | `bi-file-earmark-ruled` | Weekly during setup, then rarely | Jargon name (3.1)                                                    |
| Salary             | `salary.list_profiles` (`/salary`)                    | `salary/list.html`          | `bi-wallet2`            | Monthly or less                  | Duplicate icon (1.3)                                                 |
| Accounts           | `accounts.list_accounts` (`/accounts`)                | `accounts/list.html`        | `bi-bank`               | Occasionally                     | Overlaps with Accounts & Savings (1.1)                               |
| Transfers          | `transfers.list_transfer_templates` (`/transfers`)    | `transfers/list.html`       | `bi-arrow-left-right`   | Monthly                          | Disconnected from accounts (1.5)                                     |
| Accounts & Savings | `savings.dashboard` (`/savings`)                      | `savings/dashboard.html`    | `bi-wallet2`            | Weekly                           | Duplicate icon (1.3), overlaps Accounts (1.1), misleading name (3.5) |
| Retirement         | `retirement.dashboard` (`/retirement`)                | `retirement/dashboard.html` | `bi-briefcase`          | Monthly or less                  | None                                                                 |
| Charts             | `charts.dashboard` (`/charts`)                        | `charts/dashboard.html`     | `bi-bar-chart-line`     | Weekly                           | None                                                                 |
| Categories         | `categories.list_categories` (`/categories`)          | `categories/list.html`      | `bi-tags`               | Rarely after setup               | Setup page in primary nav (1.4)                                      |
| Pay Periods        | `pay_periods.generate_form` (`/pay-periods/generate`) | `pay_periods/generate.html` | `bi-calendar-range`     | Rarely (once/year)               | Setup page in primary nav (1.4)                                      |
| Settings           | `settings.show` (`/settings`)                         | `settings/settings.html`    | `bi-gear`               | Rarely                           | Sparse content (2.5)                                                 |

### B. All User-Facing Pages

| URL                             | Page Title            | Purpose                                  | Primary Action               | Issues                              |
| ------------------------------- | --------------------- | ---------------------------------------- | ---------------------------- | ----------------------------------- |
| `/`                             | Budget Grid           | Main budget view, biweekly paycheck grid | Inline edit, add transaction | Core page, well-designed            |
| `/templates`                    | Transaction Templates | List recurring transaction definitions   | New Template                 | Jargon naming                       |
| `/templates/new`                | New Template          | Create recurring transaction             | Create                       | Jargon naming                       |
| `/templates/<id>/edit`          | Edit Template         | Modify recurring transaction             | Update                       | Jargon naming                       |
| `/salary`                       | Salary                | List salary profiles                     | New Profile                  | None                                |
| `/salary/new`                   | New Salary Profile    | Create salary profile                    | Create Profile               | None                                |
| `/salary/<id>/edit`             | Edit Salary Profile   | Edit profile with raises/deductions      | Update Profile               | Dense deduction form                |
| `/salary/<id>/breakdown/<pid>`  | Paycheck Breakdown    | Detailed pay stub view                   | Navigation to other periods  | None                                |
| `/salary/<id>/projection`       | Salary Projection     | Salary over time table                   | None (read-only)             | None                                |
| `/salary/tax-config`            | Tax Configuration     | Federal/state/FICA setup                 | Update configs               | Buried under Salary                 |
| `/accounts`                     | Accounts              | CRUD table of accounts + types           | New Account                  | Overlaps with /savings              |
| `/accounts/new`                 | New Account           | Create account                           | Create                       | None                                |
| `/accounts/<id>/edit`           | Edit Account          | Modify account                           | Update                       | None                                |
| `/accounts/<id>/hysa`           | HYSA Detail           | Interest projection                      | Update HYSA Params           | No breadcrumbs                      |
| `/accounts/<id>/mortgage`       | Mortgage Dashboard    | Loan summary, escrow, payoff             | Update Parameters            | Long page, decimal rate input       |
| `/accounts/<id>/auto_loan`      | Auto Loan Dashboard   | Loan summary                             | Update Parameters            | Simpler than mortgage               |
| `/accounts/<id>/investment`     | Investment Dashboard  | Growth projection                        | Save Parameters              | Employer fields always visible      |
| `/transfers`                    | Transfer Templates    | List recurring transfers                 | New Transfer                 | "Transfer Templates" jargon         |
| `/transfers/new`                | New Transfer          | Create recurring transfer                | Create                       | "Transfer Template" heading         |
| `/transfers/<id>/edit`          | Edit Transfer         | Modify recurring transfer                | Update                       | "Transfer Template" heading         |
| `/savings`                      | Accounts & Savings    | Unified account dashboard                | New Goal                     | Misleading name, overlaps /accounts |
| `/savings/goals/new`            | New Goal              | Create savings goal                      | Create                       | None                                |
| `/savings/goals/<id>/edit`      | Edit Goal             | Modify savings goal                      | Update                       | None                                |
| `/retirement`                   | Retirement Planning   | Gap analysis, pension, accounts          | Manage Pensions              | Settings at bottom                  |
| `/retirement/pension`           | Pension               | Create/edit pension profiles             | Create                       | None                                |
| `/retirement/pension/<id>/edit` | Edit Pension          | Modify pension profile                   | Update                       | None                                |
| `/charts`                       | Charts                | Visualization dashboard                  | None (interactive charts)    | h1 heading inconsistency            |
| `/categories`                   | Categories            | Manage expense categories                | Add Category                 | Setup page in primary nav           |
| `/pay-periods/generate`         | Pay Periods           | Generate biweekly schedule               | Generate                     | Setup page in primary nav           |
| `/settings`                     | Settings              | User preferences                         | Save Settings                | Very sparse                         |

### C. Nomenclature Map

| Current Term                    | Where Used                                       | Suggested User-Friendly Term                                                       |
| ------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------- | -------------- | --------- |
| Template / Transaction Template | Navbar, page titles, forms, flash messages, URLs | Recurring Transaction / Recurring Bill                                             |
| Transfer Template               | Page titles, forms, flash messages               | Recurring Transfer                                                                 |
| Default Amount                  | Template form, template list column header       | Amount (per occurrence)                                                            |
| Anchor Balance                  | Accounts list column header, grid internal       | Current Balance / Verified Balance                                                 |
| Accounts & Savings              | Navbar label, savings dashboard heading          | Accounts (as unified dashboard)                                                    |
| P&I                             | Mortgage dashboard                               | Principal & Interest                                                               |
| SWR                             | Retirement sliders, settings                     | Safe Withdrawal Rate (already spelled out in label, abbreviation used in code/IDs) |
| every_period                    | Recurrence pattern option                        | Every paycheck (already translated in UI)                                          |
| every_n_periods                 | Recurrence pattern option                        | Every N paychecks (already translated in UI)                                       |
| pre_tax / post_tax              | Deduction timing badges                          | Pre-Tax / Post-Tax (already formatted but uses replace('\_', '-'))                 |
| auto_loan                       | Account type display                             | Auto Loan (inconsistent formatting with `| capitalize`vs`| replace`) |
| roth_401k                       | Account type display                             | Roth 401(k)                                                                        |
| traditional_ira                 | Account type display                             | Traditional IRA                                                                    |
| Phase 7                         | Settings help text                               | (remove developer reference)                                                       |
| Effective from                  | Template edit form                               | Apply changes starting from                                                        |
| is_arm                          | Mortgage form checkbox                           | Adjustable Rate (ARM) (already labeled correctly)                                  |
| Inflation Mo.                   | Deduction form                                   | Inflation Start Month                                                              |
