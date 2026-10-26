> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel UI/UX Remediation Plan

## Overview

This plan addresses all findings from the Shekel UI/UX audit (`docs/ui_ux_audit.md`) across 6 phases of work. The audit identified 7 critical issues, 18 major issues, and ~25 minor issues spanning navigation architecture, nomenclature, settings sprawl, visual consistency, and Phase 7 readiness.

**Scope:** 6 phases, approximately 50-60 files modified across templates, routes, CSS, and tests. No database model or schema changes. All phases are independently shippable.

**Key risks:** Flash message text changes affect 7 test assertions. Settings dashboard consolidation is the largest new feature (new route, templates, redirects). Navigation restructure touches the globally-included `base.html`. Nomenclature changes span ~30 files but are text-only.

---

## Guiding Principles

1. **No model changes.** This is a UI/UX cleanup. No database models, column names, table names, or migrations will be modified.

2. **url_for names are a contract.** No `url_for` endpoint name (e.g., `templates.list_templates`) will be renamed. These names are referenced across templates, route redirects, and test files. See Appendix for the full reference map.

3. **Old URLs redirect.** When a page is absorbed into the settings dashboard, its old URL redirects (302) to the corresponding settings section. Test updates for redirected URLs are deferred to a follow-up phase after the new settings page is confirmed functional.

4. **Test gates are mandatory.** Every phase ends with `pytest` -- all 770+ existing tests must pass. If a phase changes flash message text or page content that tests assert on, those test updates are part of the same phase.

5. **Lower risk first.** Phases are ordered from lowest to highest risk. Early phases touch only templates and CSS; later phases restructure navigation and consolidate settings.

6. **Respect existing architecture.** Flask blueprints, Jinja2 templates, HTMX partials, Bootstrap 5, server-rendered HTML. No new frameworks, build pipelines, or SPAs.

7. **Each phase is atomic.** Every phase produces a working, shippable app. No phase leaves the app in a broken intermediate state.

---

## Phase 1: Visual Consistency & Polish

### Addresses

1.3, 1.6, 2.4, 3.9, 3.10, 7.1, 7.2, 7.7, and minor page-level findings from Section 5 (Charts heading, grid quick-select labels, table classes).

### Depends On

None.

### Risk Level

**Low.** All changes are in templates and CSS. No route logic, url_for names, or URL paths change. No flash message text changes. Zero test assertions affected.

### Changes

1. **Fix duplicate `bi-wallet2` icon (1.3).** Change the Salary navbar icon from `bi-wallet2` to `bi-cash-coin`.
   - `app/templates/base.html` (line 58): change `bi-wallet2` to `bi-cash-coin`

2. **Fix duplicate `bi-wallet2` on charts dashboard (1.3).** Change the Net Pay Trajectory chart header icon.
   - `app/templates/charts/dashboard.html` (line 103): change `bi-wallet2` to `bi-cash-coin`

3. **Add navbar active state (1.6).** Add conditional `active` class to nav-link items based on `request.path`. Use Jinja2 `startswith` checks to highlight the current section.
   - `app/templates/base.html` (lines 46-100): add `{% if request.path == '/' %}active{% endif %}` (and similar) to each `nav-link`

4. **Fix Settings "Phase 7" help text (2.4).** Replace developer jargon with user-facing text.
   - `app/templates/settings/settings.html` (line 31): change `"Used for Phase 7 expense inflation adjustments."` to `"Annual inflation rate used for long-term expense projections."`

5. **Add tooltip to "P&I" (3.9).** Expand the abbreviation or add a `title` attribute.
   - `app/templates/mortgage/dashboard.html` (line 51): change `Monthly P&I` to `Monthly P&I` with `title="Principal & Interest"`, or expand to `Monthly Principal & Interest`

6. **Fix account type display formatting (3.10).** Create a consistent Jinja2 filter or macro for formatting account type names (e.g., `auto_loan` → `Auto Loan`, `roth_401k` → `Roth 401(k)`, `traditional_ira` → `Traditional IRA`). The cleanest approach is a Python template filter registered in `app/__init__.py` or a shared macro.
   - `app/__init__.py`: register a `format_account_type` template filter
   - `app/templates/accounts/list.html` (line 30): replace `name|capitalize` with `name|format_account_type`
   - `app/templates/savings/dashboard.html` (line 58): replace existing type formatting
   - `app/templates/investment/dashboard.html` (line 9): replace existing type formatting
   - `app/templates/auto_loan/dashboard.html`: replace any type formatting
   - `app/templates/mortgage/dashboard.html`: replace any type formatting
   - `app/templates/retirement/dashboard.html`: replace any type formatting in accounts table

7. **Standardize page headings to `<h4>` (7.1).** The charts dashboard uses `<h1>`; all other pages use `<h4>`.
   - `app/templates/charts/dashboard.html` (line 5): change `<h1 class="mb-4">` to `<h4>`

8. **Add breadcrumbs to pages missing them (7.2).** Currently, breadcrumbs exist on: Settings, Templates form, Transfers form, Pay Periods, Salary form/breakdown/projection/tax-config. Add breadcrumbs to the pages listed below. Note: `categories/list.html` is excluded because it will be absorbed into the settings dashboard in Phase 3.
   - `app/templates/templates/list.html`: add breadcrumb block (Home → Recurring Transactions)
   - `app/templates/transfers/list.html`: add breadcrumb block (Home → Transfers)
   - `app/templates/accounts/list.html`: add breadcrumb block (Home → Manage Accounts)
   - `app/templates/savings/dashboard.html`: add breadcrumb block (Home → Accounts)
   - `app/templates/accounts/hysa_detail.html`: add breadcrumb block (Home → Accounts → HYSA Detail)
   - `app/templates/mortgage/dashboard.html`: replace "Back to Accounts" button with breadcrumb (Home → Accounts → Mortgage)
   - `app/templates/auto_loan/dashboard.html`: replace "Back to Accounts" button with breadcrumb (Home → Accounts → Auto Loan)
   - `app/templates/investment/dashboard.html`: replace "Back to Accounts" button with breadcrumb (Home → Accounts → Investment)
   - `app/templates/retirement/dashboard.html`: add breadcrumb block (Home → Retirement)
   - `app/templates/retirement/pension_form.html`: add breadcrumb block (Home → Retirement → Pensions)
   - `app/templates/charts/dashboard.html`: add breadcrumb block (Home → Charts)
   - `app/templates/salary/list.html`: add breadcrumb block (Home → Salary)

9. **Standardize table classes (7.7).** Ensure all non-grid tables use `table table-hover table-sm` with `thead class="table-light"` consistently.
   - `app/templates/salary/form.html` (\_deductions_section and \_raises_section includes): add `table-hover` and `table-light` to thead where missing
   - `app/templates/mortgage/dashboard.html`: add `table-hover` and `table-light` to thead on loan summary table
   - `app/templates/retirement/dashboard.html`: add `table-hover` and `table-light` to thead on accounts table

10. **Add tooltips to grid quick-select buttons (Section 5, grid).** The labels "3P", "6P", "6M", "1Y", "2Y" are compact but not self-explanatory.
    - `app/templates/grid/grid.html`: add `title` attributes to quick-select buttons (e.g., `title="3 pay periods"`, `title="6 months"`, `title="1 year"`, `title="2 years"`)

### Test Gate

- [ ] `pytest` passes (all 770+ existing tests)
- [ ] Manual verification: navbar icons are unique (Salary = `bi-cash-coin`, Accounts & Savings = `bi-wallet2`)
- [ ] Manual verification: active navbar item is highlighted on each page
- [ ] Manual verification: breadcrumbs appear on all list/dashboard pages
- [ ] Manual verification: account type names display correctly (Roth 401(k), Traditional IRA, Auto Loan)
- [ ] Manual verification: Charts page heading matches other pages

---

## Phase 2: Nomenclature & Labels

### Addresses

3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, and workflow friction points from 4A (step 1 discoverability), 4B (heading jargon), 4F ("Accounts & Savings" confusion), 5.x (Templates list/form page titles, "Effective from" help text).

### Depends On

Phase 1 (breadcrumbs reference the new labels).

### Risk Level

**Medium.** Text changes across ~30 files. Seven test assertions reference flash message strings that will change. No url_for names or URL paths change.

### Changes

#### "Templates" → "Recurring Transactions" (3.1)

1. **Navbar label.**
   - `app/templates/base.html` (line 53): change `Templates` to `Recurring`
   - `app/templates/base.html` (line 52): change icon from `bi-file-earmark-ruled` to `bi-arrow-repeat`

2. **List page heading and title.**
   - `app/templates/templates/list.html` (line ~6): change `{% block title %}` from `Transaction Templates` to `Recurring Transactions`
   - `app/templates/templates/list.html` (line ~23): change heading from `Transaction Templates` to `Recurring Transactions`
   - `app/templates/templates/list.html`: change "New Template" button text to `New Recurring Transaction`
   - `app/templates/templates/list.html`: change empty-state alert text from "No templates yet. Create one to start auto-generating transactions." to "No recurring transactions yet. Create one to auto-generate budget entries each pay period."

3. **Form page heading, title, and breadcrumbs.**
   - `app/templates/templates/form.html` (line ~35): change `New Transaction Template` / `Edit Transaction Template` to `New Recurring Transaction` / `Edit Recurring Transaction`
   - `app/templates/templates/form.html`: update breadcrumb text from `Templates` to `Recurring Transactions`
   - `app/templates/templates/form.html` (title block): update to `New Recurring Transaction` / `Edit Recurring Transaction`

4. **"Default Amount" → "Amount" (3.4).**
   - `app/templates/templates/form.html` (line ~50): change label from `Default Amount` to `Amount`
   - `app/templates/templates/list.html` (line ~37): change column header from `Default Amount` to `Amount`

5. **"Effective from" help text (Section 5).**
   - `app/templates/templates/form.html`: change help text from "Leave blank to use today's date" to "Changes take effect from this date. Future budget entries will be regenerated. Leave blank to apply immediately."

6. **Flash messages in routes.**
   - `app/routes/templates.py` (line 165): change `f"Template '{template.name}' created."` to `f"Recurring transaction '{template.name}' created."`
   - `app/routes/templates.py` (line 175, 213, 307, 336): change `"Template not found."` to `"Recurring transaction not found."`
   - `app/routes/templates.py` (line 297): change `f"Template '{template.name}' updated."` to `f"Recurring transaction '{template.name}' updated."`
   - `app/routes/templates.py` (lines 322, 364): update deactivation/reactivation flash messages similarly

7. **Update test assertions for changed flash messages.**
   - `tests/test_routes/test_templates.py` (lines 322, 338, 419, 433, 487): change `b"Template not found"` to `b"Recurring transaction not found"`

8. **Onboarding banner.**
   - `app/templates/base.html` (lines 174-176): change "Create transaction templates" to "Set up recurring transactions"
   - `app/templates/base.html` (line 175): change icon from `bi-file-earmark-ruled` to `bi-arrow-repeat`

#### "Transfer Template" → "Recurring Transfer" (3.2)

9. **Transfer list page.**
   - `app/templates/transfers/list.html` (line ~23): change heading from `Transfer Templates` to `Recurring Transfers`
   - `app/templates/transfers/list.html` (title block): change from `Transfer Templates` to `Recurring Transfers`

10. **Transfer form page.**
    - `app/templates/transfers/form.html` (line ~35): change `New Transfer Template` / `Edit Transfer Template` to `New Recurring Transfer` / `Edit Recurring Transfer`
    - `app/templates/transfers/form.html`: update breadcrumb text from `Transfer Templates` to `Recurring Transfers`

11. **Transfer flash messages.**
    - `app/routes/transfers.py` (line 164): change `f"Transfer template '{template.name}' created."` to `f"Recurring transfer '{template.name}' created."`
    - `app/routes/transfers.py` (lines 174, 201, 290, 318): change `"Transfer template not found."` to `"Recurring transfer not found."`
    - `app/routes/transfers.py` (line 280): change `f"Transfer template '{template.name}' updated."` to `f"Recurring transfer '{template.name}' updated."`
    - `app/routes/transfers.py` (lines 304, 343): update deactivation/reactivation flash messages similarly

12. **Update test assertions for changed transfer flash messages.**
    - `tests/test_routes/test_transfers.py` (lines 352, 367): change `b"Transfer template not found."` to `b"Recurring transfer not found."`

#### "Anchor Balance" → "Current Balance" (3.3)

13. **Accounts list.**
    - `app/templates/accounts/list.html` (line 22): change `Anchor Balance` column header to `Current Balance`

14. **Grid anchor display.**
    - `app/templates/grid/grid.html` (line ~9): if "Anchor Balance" text appears, change to "Current Balance" or "Balance"
    - `app/templates/grid/_anchor_edit.html`: update any "anchor" labels visible to the user

#### "Accounts & Savings" → "Accounts Dashboard" (3.5)

15. **Navbar label.**
    - `app/templates/base.html` (line 73): change `Accounts & Savings` to `Accounts Dashboard`

16. **Savings dashboard heading.**
    - `app/templates/savings/dashboard.html` (line 6): change `Accounts & Savings` to `Accounts Dashboard`
    - `app/templates/savings/dashboard.html` (title block): update similarly

#### Mortgage rate input format (3.6)

17. **Change mortgage rate input to accept percentage values** (e.g., user enters `6.5` for 6.5% instead of `0.065`).
    - `app/templates/mortgage/dashboard.html` (lines 92-94): change input to accept percentage (value displayed as `params.interest_rate * 100`, step="0.001", min="0", max="100"), add `%` suffix in input group
    - `app/routes/mortgage.py` (in `update_params` and `create_params`): divide the submitted interest_rate by 100 before saving
    - `app/templates/mortgage/setup.html`: same input format change
    - `app/templates/mortgage/_rate_history.html`: same format change for rate input in ARM rate change form

18. **Write new test for mortgage rate percentage conversion.**
    - `tests/test_routes/test_mortgage.py`: add test that submitting `6.5` stores `0.065` in the database

#### DRY recurrence pattern labels (3.7)

19. **Create a shared Jinja2 macro or context processor for recurrence pattern labels.** The `pattern_labels` dict is defined independently in 4 files.
    - Create `app/templates/_recurrence_labels.html` as a Jinja2 include with the shared dict, OR register a context processor in `app/__init__.py` that injects `recurrence_pattern_labels`
    - `app/templates/templates/list.html`: remove local `pattern_labels` dict, use shared source
    - `app/templates/templates/form.html`: same
    - `app/templates/transfers/list.html`: same
    - `app/templates/transfers/form.html`: same

#### Flash message cleanup (3.8)

20. **Sanitize validation error flash messages.** Replace raw Marshmallow error dicts with user-friendly messages.
    - `app/routes/templates.py` (line 87): change `f"Validation error: {errors}"` to a user-friendly message (e.g., "Please correct the errors below." with field-level display)
    - `app/routes/transfers.py` (line 90): same pattern
    - `app/routes/accounts.py` (lines 103-106): same pattern
    - `app/routes/salary.py` (line 149): same pattern
    - `app/routes/savings.py` (line 486): same pattern
    - `app/routes/retirement.py` (line 380): same pattern
    - `app/routes/investment.py` (line 359): same pattern
    - `app/routes/mortgage.py` (line 160): same pattern
    - `app/routes/auto_loan.py` (line 122): same pattern
    - `app/routes/categories.py` (line 50): same pattern
    - `app/routes/retirement.py` (line 523): same pattern

### Test Gate

- [ ] `pytest` passes (all 770+ existing tests, with the 7 updated assertions)
- [ ] New test: mortgage rate percentage conversion stores correct decimal
- [ ] Manual verification: all "Template" references replaced with "Recurring Transaction" in user-facing text
- [ ] Manual verification: all "Transfer Template" references replaced with "Recurring Transfer"
- [ ] Manual verification: "Anchor Balance" column header now says "Current Balance"
- [ ] Manual verification: savings dashboard heading says "Accounts Dashboard"
- [ ] Manual verification: mortgage rate input accepts percentage (e.g., 6.5 not 0.065)
- [ ] Manual verification: flash messages show user-friendly validation errors

---

## Phase 3: Settings Dashboard

### Addresses

2.1, 2.2, 2.3, 2.5, 2.6, and partially 1.4 (setup pages become discoverable through the settings dashboard before they are removed from the navbar in Phase 4).

### Depends On

Phase 2 (nomenclature applied -- labels should be correct before building the settings dashboard content).

### Risk Level

**Medium.** This phase creates a new route and template structure but does not modify the navbar. The existing standalone pages still work and are still linked from the navbar -- the settings dashboard is an additional path to reach them. Redirects from old URLs are added, but existing tests continue to pass because GET requests to redirected URLs receive 302 responses (tests that don't follow redirects will still get a valid HTTP status).

### Changes

#### Settings dashboard layout

1. **Create the settings dashboard page.** A two-column layout: persistent left sidebar with navigation + right content area that renders the active section. URL pattern: `/settings?section=general` (default when no section specified).
   - `app/templates/settings/dashboard.html`: new template extending `base.html` with a Bootstrap two-column layout (`col-md-3` sidebar + `col-md-9` content area). The sidebar lists all sections with the active one highlighted based on the `section` query parameter.

2. **Sidebar sections (flat list):**
   - **General** (`?section=general`, default) -- the current 4 settings fields (grid periods, inflation rate, low balance threshold, default grid account)
   - **Categories** (`?section=categories`) -- category management (add/delete, grouped by group name)
   - **Pay Periods** (`?section=pay-periods`) -- period generation form
   - **Tax Config** (`?section=tax`) -- federal tax brackets, FICA rates, state tax configuration
   - **Account Types** (`?section=account-types`) -- account type CRUD (currently a sidebar card on `/accounts`)
   - **Retirement** (`?section=retirement`) -- SWR, planned retirement date, estimated retirement tax rate

#### Section content templates

3. **Extract each section's content into a partial template** that the settings dashboard includes based on the active section.
   - `app/templates/settings/_general.html`: the current settings form fields (extracted from `settings/settings.html`)
   - `app/templates/settings/_categories.html`: category list and add form (extracted from `categories/list.html`)
   - `app/templates/settings/_pay_periods.html`: pay period generation form (extracted from `pay_periods/generate.html`)
   - `app/templates/settings/_tax_config.html`: tax brackets, FICA, state tax forms (extracted from `salary/tax_config.html`)
   - `app/templates/settings/_account_types.html`: account type management (extracted from the sidebar card in `accounts/list.html`)
   - `app/templates/settings/_retirement.html`: retirement settings fields (extracted from the bottom card in `retirement/dashboard.html`)

4. **Note on configuration patterns (2.3).** Account-specific settings (HYSA APY, mortgage params, investment params, etc.) remain on each account's detail page. These are not consolidated into the settings dashboard because they are tied to individual accounts and have different complexity. The settings dashboard sidebar includes a brief note: "Account-specific settings (interest rates, loan terms, investment parameters) are configured on each account's detail page."

#### Route changes

5. **Expand the `settings.show` route** to accept a `section` query parameter and render the dashboard with the appropriate section content.
   - `app/routes/settings.py`: modify `show()` to read `request.args.get("section", "general")`, gather section-specific context data, and render `settings/dashboard.html` with the active section's partial. Each section requires its own data queries (e.g., categories section needs all categories, tax section needs bracket sets and FICA config). Reference the existing route handlers (`categories.list_categories`, `salary.tax_config`, etc.) as patterns for which data to load.

6. **Update form POST redirect targets.** When a form within a settings section is submitted, the POST handler should redirect back to the settings dashboard with the correct section, not to the old standalone URL.
   - `app/routes/categories.py`: change redirects from `url_for("categories.list_categories")` to `url_for("settings.show", section="categories")`
   - `app/routes/salary.py` (tax config routes): change redirects from `url_for("salary.tax_config")` to `url_for("settings.show", section="tax")`
   - `app/routes/accounts.py` (account type routes): change redirects from `url_for("accounts.list_accounts")` to `url_for("settings.show", section="account-types")`
   - `app/routes/retirement.py` (`update_settings`): change redirect from `url_for("retirement.dashboard")` to `url_for("settings.show", section="retirement")`
   - `app/routes/settings.py` (`update`): change redirect from `url_for("settings.show")` to `url_for("settings.show", section="general")`
   - Note: `pay_periods.generate` currently redirects to `url_for("grid.index")` after generating periods -- this is correct behavior (user wants to see their grid), so it stays as-is.

7. **Add 302 redirects from old standalone GET URLs** to the corresponding settings section.
   - `app/routes/categories.py`: `list_categories()` (GET handler) returns `redirect(url_for("settings.show", section="categories"))`
   - `app/routes/pay_periods.py`: `generate_form()` (GET handler) returns `redirect(url_for("settings.show", section="pay-periods"))`
   - `app/routes/salary.py`: `tax_config()` (GET handler) returns `redirect(url_for("settings.show", section="tax"))`
   - Note: POST endpoints are unaffected by these redirects -- they are separate route registrations and continue to process form submissions normally.

8. **Remove retirement settings card from retirement dashboard.** The SWR, planned retirement date, and estimated tax rate fields move to the settings dashboard. The retirement dashboard keeps its sensitivity sliders (those are interactive analysis tools, not persistent settings).
   - `app/templates/retirement/dashboard.html`: remove the "Retirement Settings" card (lines ~162-196)
   - The retirement dashboard should add a small link or note: "Retirement settings are in Settings > Retirement" with a link to `url_for("settings.show", section="retirement")`

9. **Remove account types sidebar from accounts list page.** Account type management moves to the settings dashboard.
   - `app/templates/accounts/list.html`: remove the account types sidebar card, allow the accounts table to use the full width

#### Test considerations

10. **Existing tests that hit redirected GET URLs** (e.g., `client.get("/categories")`) will receive 302 responses. Tests that check for `200` status codes on these URLs will fail. These test updates are **deferred to a follow-up phase** after the settings dashboard is confirmed functional. In the interim, tests still pass if they check for redirect status (302) or use `follow_redirects=True`.

    Affected test files (to be updated in follow-up):
    - `tests/test_routes/test_categories.py`: hits `GET /categories`
    - `tests/test_routes/test_pay_periods.py`: hits `GET /pay-periods/generate`
    - `tests/test_routes/test_salary.py`: hits `GET /salary/tax-config`
    - `tests/test_routes/test_settings.py`: hits `GET /settings` (this one still works since `/settings` is the dashboard)

    Note: `GET /settings` is NOT redirected -- it IS the dashboard. Only the absorbed standalone pages redirect.

### Test Gate

- [ ] `pytest` passes (all existing tests -- tests hitting redirected URLs get 302 which is still a valid response; update any that strictly assert `200` on now-redirected GET endpoints)
- [ ] New tests: `GET /settings` renders the dashboard with General section by default
- [ ] New tests: `GET /settings?section=categories` renders category management content
- [ ] New tests: `GET /settings?section=tax` renders tax configuration content
- [ ] New tests: `GET /settings?section=retirement` renders retirement settings
- [ ] New tests: `GET /categories` returns 302 redirect to `/settings?section=categories`
- [ ] New tests: `POST /categories` still processes form submission (not redirected)
- [ ] Manual verification: settings dashboard renders correctly with sidebar navigation
- [ ] Manual verification: each sidebar section displays correct content
- [ ] Manual verification: form submissions within each section save correctly and return to the right section
- [ ] Manual verification: retirement dashboard no longer shows settings card, shows link to settings instead
- [ ] Manual verification: accounts list page no longer shows account types sidebar

---

## Phase 4: Navbar Restructure

### Addresses

1.1, 1.2, 1.4, 1.5, 1.7, 6.1, and workflow friction from 4B (transfers disconnected from accounts), 4F (which "Accounts" to click), Section 5 (savings dashboard "New Account" link).

### Depends On

Phase 3 (settings dashboard must exist before removing setup pages from the navbar -- otherwise Categories, Pay Periods, and Tax Config become undiscoverable).

### Risk Level

**Medium.** The navbar in `base.html` is included on every authenticated page. However, this is a straightforward reduction from 11 items to 8 flat links -- no dropdowns, no complex restructuring. All url_for endpoint names and URL paths remain unchanged.

### Changes

#### Navbar restructure (1.1, 1.2, 1.4, 1.7, 6.1)

1. **Replace the 11-item flat navbar with 8 flat top-level links.** The reduced set covers all daily-use and weekly-use pages. Setup/config pages are now accessible through the Settings dashboard (Phase 3).

   New navbar items (in order):
   - **Budget** (`grid.index`, `/`) -- `bi-grid-3x3`
   - **Recurring** (`templates.list_templates`, `/templates`) -- `bi-arrow-repeat`
   - **Accounts** (`savings.dashboard`, `/savings`) -- `bi-wallet2`
   - **Salary** (`salary.list_profiles`, `/salary`) -- `bi-cash-coin`
   - **Transfers** (`transfers.list_transfer_templates`, `/transfers`) -- `bi-arrow-left-right`
   - **Retirement** (`retirement.dashboard`, `/retirement`) -- `bi-briefcase`
   - **Charts** (`charts.dashboard`, `/charts`) -- `bi-bar-chart-line`
   - **Settings** (`settings.show`, `/settings`) -- `bi-gear`

   Removed from navbar (now accessible via Settings dashboard):
   - ~~Accounts~~ (`accounts.list_accounts`) -- replaced by "Accounts" pointing to `savings.dashboard`
   - ~~Categories~~ (`categories.list_categories`) -- in Settings > Categories
   - ~~Pay Periods~~ (`pay_periods.generate_form`) -- in Settings > Pay Periods

   File: `app/templates/base.html` (lines 44-101): rewrite the `<ul class="navbar-nav me-auto">` section.

2. **Resolve the two "Accounts" navbar items (1.1).** The savings dashboard (`savings.dashboard`) becomes the sole "Accounts" link in the navbar. The CRUD accounts page (`accounts.list_accounts`) is accessible from the savings dashboard via a "Manage Accounts" link and from Settings > Account Types for type management.

3. **Update navbar active state logic (from Phase 1).** Adjust the active state checks to match the new 8-item navbar. The `/settings` path now covers settings and all its sections. The `/savings` path is the "Accounts" active state.
   - `app/templates/base.html`: update the `request.path.startswith()` conditions

4. **Add "Manage Accounts" link to savings dashboard.** Users need a path from the unified accounts view to the CRUD page for creating/editing/deactivating accounts.
   - `app/templates/savings/dashboard.html`: add a "Manage Accounts" button or link (`url_for('accounts.list_accounts')`) in the page header area, alongside the existing "New Goal" button

5. **Add "New Account" link to savings dashboard (Section 5, savings dashboard).** Users should be able to create accounts from the unified accounts view.
   - `app/templates/savings/dashboard.html`: add a "New Account" button (`url_for('accounts.new_account')`) in the page header area

6. **Add "New Transfer" quick-action to savings dashboard account cards (1.5, 4B).** Each account card should have a link to create a transfer with the account pre-selected (via query parameter).
   - `app/templates/savings/dashboard.html`: add transfer icon link to account cards that links to `url_for('transfers.new_transfer_template')` with `?from_account=<id>` or `?to_account=<id>`
   - `app/routes/transfers.py` (in `new_transfer_template`): read optional `from_account` / `to_account` query params to pre-select dropdowns
   - `app/templates/transfers/form.html`: pre-select account dropdowns when query params are provided

7. **Update onboarding banner.** The onboarding banner references `templates.list_templates` and `pay_periods.generate_form` -- both url_for names still work. The `pay_periods.generate_form` link will now redirect to settings, which is fine for onboarding. Verify it still works; no code change expected.

### Test Gate

- [ ] `pytest` passes (all 770+ existing tests)
- [ ] New test: `GET /transfers/new?from_account=<id>` pre-selects the source account dropdown
- [ ] Manual verification: navbar displays 8 items correctly at desktop width (>992px)
- [ ] Manual verification: navbar displays correctly at tablet width (768-992px)
- [ ] Manual verification: navbar hamburger menu works at mobile width (<768px)
- [ ] Manual verification: all 8 navbar items link to correct pages
- [ ] Manual verification: active state highlights correctly for each page
- [ ] Manual verification: "Manage Accounts" and "New Account" buttons appear on savings dashboard
- [ ] Manual verification: transfer quick-action on account cards links to pre-filled form
- [ ] Manual verification: onboarding banner links still function correctly

---

## Phase 5: Workflow & Interaction Refinements

### Addresses

Workflow friction points from 4A (category filtering), 4C (deduction target account prominence, help text), 4D (empty cell discoverability), 4E (carry-forward button visibility), Section 5 (investment employer fields conditional visibility, deduction form density, HYSA projection length, salary list net pay explanation).

### Depends On

Phase 2 (nomenclature fixes applied before workflow text changes).

### Risk Level

**Medium.** Changes touch form layouts and conditional display logic. Some require minor JavaScript additions. No url_for names or URL paths change.

### Changes

1. **Improve target account field prominence in deduction form (4C).** Move the "Target Account" field higher in the deduction form layout and improve help text.
   - `app/templates/salary/_deductions_section.html` (lines 148-158): move "Target Account" dropdown to appear after the "Name" and "Timing" fields (before amount/method), and change help text from "Credits deduction to a retirement/investment account" to "Select the retirement or investment account this deduction contributes to. The deducted amount will appear as a contribution in that account's growth projection."

2. **Conditional visibility for investment employer fields (Section 5, investment).** Hide employer contribution fields when employer type is "None."
   - `app/templates/investment/dashboard.html` (lines ~170-208): wrap employer contribution fields in a container that is shown/hidden based on the employer type dropdown value
   - `app/static/js/app.js` or inline `<script>` in `investment/dashboard.html`: add JS to toggle visibility when employer type changes

3. **Add tooltip/hint to empty grid cells (4D).** Make the `--` dash cells more obviously clickable.
   - `app/templates/grid/_transaction_empty_cell.html`: add `title="Click to add a transaction"` to the cell content
   - `app/static/css/app.css`: add a subtle hover cursor change for empty cells (e.g., `cursor: cell` or increase the hover background opacity)

4. **Improve carry-forward button visibility (4E).** The `btn-xs btn-warning` button is small.
   - `app/templates/grid/grid.html`: change carry-forward button from `btn-xs` to `btn-sm` for better visibility, or add a tooltip

5. **Add net pay explanation to salary list (Section 5, salary list).**
   - `app/templates/salary/list.html`: add a small `text-muted` note below the net pay column header or as a tooltip: "Estimated take-home pay after taxes and deductions"

6. **Improve deduction form density (Section 5, salary form).** The 8+ field form is dense on smaller screens.
   - `app/templates/salary/_deductions_section.html`: reorganize the multi-row form layout to use clearer visual grouping (e.g., group amount-related fields together, group schedule fields together) with Bootstrap row/col classes

7. **Add "View on Grid" link after template creation (4A).** After creating a recurring transaction, flash message or redirect includes a way to see it on the grid.
   - `app/routes/templates.py` (around line 165): change the flash message to include guidance, e.g., `f"Recurring transaction '{template.name}' created. View it on the Budget grid."`
   - (Optional: redirect to grid instead of template list, but this changes existing behavior -- note as optional)

### Deferred / No Change Required

The following workflow friction points are noted but do not require changes:

- **4A: Category dropdown shows all categories for expense creation.** The dropdown shows income and expense categories together. Filtering by type would require knowing the transaction type before displaying categories. Current behavior is acceptable -- the category list is typically short.

- **4C: Deduction linked to savings-type accounts.** The audit notes that only retirement/investment accounts appear in the target account dropdown. This is by design -- deductions model paycheck withholdings that flow to employer-sponsored accounts. Linking to savings accounts would be a feature addition, not a UX fix.

- **4D: Quick-create auto-names from category.** Already good UX, no change needed.

- **4E: Batch "mark all as done."** This would be a feature addition (Phase 7+ scope), not a UX fix.

- **4G: Payoff calculator buried below other sections.** The mortgage dashboard is information-dense but functionally correct. Payoff is a secondary action. Adding anchor links or tabs would increase complexity. No change.

- **4H: Retirement dashboard calculation explainers.** Adding "how is this calculated?" tooltips would be valuable but is a documentation enhancement. Deferred.

- **Section 5: HYSA projection table length.** The period-by-period table is long but functional. Adding pagination would increase complexity. No change.

- **Section 5: Categories cannot be renamed.** This is a feature addition. Deferred.

- **Section 5: Pay periods cannot be viewed or extended.** The current generate-only workflow works. Viewing existing periods is a feature addition. Deferred.

### Test Gate

- [ ] `pytest` passes (all 770+ existing tests)
- [ ] Manual verification: target account field is more prominent in deduction form
- [ ] Manual verification: employer fields hide when employer type is "None" on investment dashboard
- [ ] Manual verification: empty grid cells show tooltip on hover
- [ ] Manual verification: carry-forward button is easier to spot
- [ ] Manual verification: deduction form layout is readable on mobile width

---

## Phase 6: Phase 7 (Scenarios) Readiness

### Addresses

6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7.

### Depends On

Phase 4 (navbar restructure must be complete before adding scenario UI elements).

### Risk Level

**Low.** This phase is primarily planning and minimal preparatory changes. The actual scenario implementation is Phase 7 work. This phase ensures the UI can accommodate scenarios without another restructure.

### Changes

1. **Reserve space for scenario indicator in navbar (6.2).** Add a placeholder/slot in the navbar structure for a future scenario selector. This is a structural change only -- no functionality yet.
   - `app/templates/base.html`: add a commented-out or `d-none` element in the navbar between the brand and main nav items where the scenario selector will go. Include a comment: `{# Phase 7: scenario selector goes here #}`

2. **Reserve space for scenario controls in grid header (6.3).** Add a structural slot in the grid header for future scenario controls.
   - `app/templates/grid/grid.html` (lines 6-56): add a commented-out row or `d-none` container for the scenario selector, separate from the period navigation controls

3. **Document scenario UI requirements.** Create a brief specification of what the scenario UI will need:
   - Global scenario indicator (navbar or sub-navbar bar)
   - Scenario selector dropdown
   - "Compare" mode toggle
   - Per-chart scenario overlay controls

   This documentation is informational, not code.

### Findings Already Resolved by Earlier Phases

- **6.1 (Navbar cannot absorb 12th item):** Resolved by Phase 4's reduction to 8 items. "Scenarios" can be added as a 9th top-level link with room to spare.
- **6.5 (Nomenclature compounds with scenarios):** Resolved by Phase 2. "Recurring Transactions" is clear; "Scenario Recurring Transactions" or "Recurring Transactions (Scenario: What-If)" would be understandable.
- **6.6 (Charts can accommodate scenario overlays):** Already compatible. The HTMX fragment pattern supports adding scenario parameters to chart requests.
- **6.7 (Retirement gap analysis is scenario-ready):** Already compatible. The sensitivity slider pattern extends naturally to scenario parameters.

### Findings Deferred to Phase 7 Implementation

- **6.2 (Global scenario indicator):** The actual indicator implementation depends on the Phase 7 data model. Phase 6 only reserves the UI slot.
- **6.4 (Side-by-side comparison layout):** Requires Phase 7 implementation to determine comparison mode (dual grid vs. merged grid vs. diff overlay). No preparatory UI change is possible without the feature design.

### Test Gate

- [ ] `pytest` passes (all 770+ existing tests)
- [ ] Manual verification: navbar still renders correctly with reserved slots
- [ ] Manual verification: grid header still renders correctly with reserved slots

---

## Dependency Graph

```
Phase 1: Visual Consistency & Polish
   │
   ▼
Phase 2: Nomenclature & Labels
   │
   ├──────────────────────────┐
   ▼                          ▼
Phase 3: Settings Dashboard   Phase 5: Workflow Improvements
   │                          (independent of Phase 3/4)
   ▼
Phase 4: Navbar Restructure
   │
   ▼
Phase 6: Phase 7 Readiness
```

**Critical path:** Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 6

**Independent after Phase 2:** Phase 5 can be done in parallel with Phases 3, 4, and 6.

---

## Risk Register

| #   | Risk                                                      | Likelihood | Impact   | Mitigation                                                                                                                                 |
| --- | --------------------------------------------------------- | ---------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| R1  | Flash message text changes break test assertions          | **High**   | Low      | 7 known assertions identified. Update in same commit as flash changes (Phase 2).                                                           |
| R2  | Navbar restructure breaks layout on mobile                | Medium     | **High** | Test at 3 breakpoints (mobile, tablet, desktop) before committing. 8 flat links is simpler than the current 11.                            |
| R3  | Nomenclature changes miss some occurrences                | Medium     | Low      | Run `grep -r "Template" --include="*.html" --include="*.py"` before and after Phase 2.                                                     |
| R4  | Active state logic incorrect for nested routes            | Medium     | Low      | Use `request.path.startswith()` for sections with sub-routes (e.g., `/salary` matches `/salary/tax-config`). Test each page.               |
| R5  | Mortgage rate format change introduces calculation errors | Low        | **High** | Add unit test for percentage → decimal conversion. Test boundary values (0%, 100%, 6.5%).                                                  |
| R6  | Recurrence label DRY-up breaks template rendering         | Low        | Medium   | Test all 4 views (template list, template form, transfer list, transfer form) that display recurrence patterns.                            |
| R7  | Breadcrumb additions reference wrong url_for endpoint     | Low        | Low      | Each breadcrumb uses existing, proven url_for endpoints. Verify each link manually.                                                        |
| R8  | `format_account_type` filter misses edge cases            | Low        | Low      | Test with all existing account types: checking, savings, hysa, mortgage, auto_loan, 401k, roth_401k, traditional_ira, roth_ira, brokerage. |
| R9  | Settings dashboard section data loading misses context    | Medium     | Medium   | Each section's data requirements are modeled on existing route handlers. Test each section renders its full content.                        |
| R10 | Redirected POST handlers double-redirect                  | Low        | Medium   | Only GET handlers redirect. POST handlers are separate route registrations. Verify POST flows for each absorbed page.                      |
| R11 | Transfer pre-fill query params ignored or mishandled      | Low        | Medium   | Add test for `GET /transfers/new?from_account=<id>` returning form with pre-selected account.                                              |

---

## Appendix: url_for Reference Map

Every `url_for` endpoint name used across templates, routes, and tests. This serves as the safety reference -- any phase that modifies navigation must verify these remain intact.

### auth blueprint

| Endpoint      | Function | URL Path  | Method    | Referenced In                                                            |
| ------------- | -------- | --------- | --------- | ------------------------------------------------------------------------ |
| `auth.login`  | `login`  | `/login`  | GET, POST | `base.html` (login_manager redirect), `errors/429.html`, route redirects |
| `auth.logout` | `logout` | `/logout` | GET       | `base.html` navbar                                                       |

### grid blueprint

| Endpoint           | Function      | URL Path            | Method | Referenced In                                                                                                                                         |
| ------------------ | ------------- | ------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `grid.index`       | `index`       | `/`                 | GET    | `base.html` navbar+brand, `errors/404.html`, `errors/500.html`, `pay_periods/generate.html` breadcrumb, route redirects (`auth.py`, `pay_periods.py`) |
| `grid.balance_row` | `balance_row` | `/grid/balance-row` | GET    | `grid/grid.html` HTMX target                                                                                                                          |

### templates blueprint

| Endpoint                        | Function              | URL Path                        | Method | Referenced In                                                                                   |
| ------------------------------- | --------------------- | ------------------------------- | ------ | ----------------------------------------------------------------------------------------------- |
| `templates.list_templates`      | `list_templates`      | `/templates`                    | GET    | `base.html` navbar, `templates/form.html` breadcrumb+cancel, route redirects, onboarding banner |
| `templates.new_template`        | `new_template`        | `/templates/new`                | GET    | `templates/list.html` button, route redirects                                                   |
| `templates.create_template`     | `create_template`     | `/templates`                    | POST   | `templates/form.html` action                                                                    |
| `templates.edit_template`       | `edit_template`       | `/templates/<id>/edit`          | GET    | `templates/list.html` links, route redirects                                                    |
| `templates.update_template`     | `update_template`     | `/templates/<id>`               | POST   | `templates/form.html` action                                                                    |
| `templates.delete_template`     | `delete_template`     | `/templates/<id>/delete`        | POST   | `templates/list.html` action                                                                    |
| `templates.reactivate_template` | `reactivate_template` | `/templates/<id>/reactivate`    | POST   | `templates/list.html` action                                                                    |
| `templates.preview_recurrence`  | `preview_recurrence`  | `/templates/preview-recurrence` | GET    | `templates/form.html` HTMX                                                                      |

### transactions blueprint

| Endpoint                          | Function             | URL Path                           | Method | Referenced In                                                                |
| --------------------------------- | -------------------- | ---------------------------------- | ------ | ---------------------------------------------------------------------------- |
| `transactions.create_transaction` | `create_transaction` | `/transactions`                    | POST   | `grid/grid.html` modal form                                                  |
| `transactions.update_transaction` | `update_transaction` | `/transactions/<id>`               | PATCH  | `grid/_transaction_quick_edit.html` HTMX                                     |
| `transactions.mark_done`          | `mark_done`          | `/transactions/<id>/mark-done`     | POST   | `grid/_transaction_full_edit.html`                                           |
| `transactions.mark_credit`        | `mark_credit`        | `/transactions/<id>/mark-credit`   | POST   | `grid/_transaction_full_edit.html`                                           |
| `transactions.unmark_credit`      | `unmark_credit`      | `/transactions/<id>/unmark-credit` | DELETE | `grid/_transaction_full_edit.html`                                           |
| `transactions.cancel_transaction` | `cancel_transaction` | `/transactions/<id>/cancel`        | POST   | `grid/_transaction_full_edit.html`                                           |
| `transactions.get_cell`           | `get_cell`           | `/transactions/<id>/cell`          | GET    | `grid/_transaction_quick_edit.html` HTMX, `grid/_transaction_full_edit.html` |
| `transactions.get_quick_edit`     | `get_quick_edit`     | `/transactions/<id>/quick-edit`    | GET    | `grid/_transaction_cell.html` HTMX                                           |
| `transactions.get_full_edit`      | `get_full_edit`      | `/transactions/<id>/full-edit`     | GET    | `grid/_transaction_quick_edit.html` HTMX                                     |
| `transactions.get_quick_create`   | `get_quick_create`   | `/transactions/new/quick`          | GET    | `grid/_transaction_empty_cell.html` HTMX                                     |
| `transactions.get_full_create`    | `get_full_create`    | `/transactions/new/full`           | GET    | `grid/_transaction_quick_create.html` HTMX                                   |
| `transactions.get_empty_cell`     | `get_empty_cell`     | `/transactions/empty-cell`         | GET    | grid HTMX                                                                    |
| `transactions.create_inline`      | `create_inline`      | `/transactions/inline`             | POST   | `grid/_transaction_quick_create.html` HTMX                                   |
| `transactions.delete_transaction` | `delete_transaction` | `/transactions/<id>`               | DELETE | `grid/_transaction_full_edit.html`                                           |
| `transactions.carry_forward`      | `carry_forward`      | `/pay-periods/<id>/carry-forward`  | POST   | `grid/grid.html`                                                             |

### pay_periods blueprint

| Endpoint                    | Function        | URL Path                | Method | Referenced In                                                                    |
| --------------------------- | --------------- | ----------------------- | ------ | -------------------------------------------------------------------------------- |
| `pay_periods.generate_form` | `generate_form` | `/pay-periods/generate` | GET    | `base.html` navbar (until Phase 4), onboarding banner. Redirects to settings (Phase 3). |
| `pay_periods.generate`      | `generate`      | `/pay-periods/generate` | POST   | `pay_periods/generate.html` form, settings dashboard form                        |

### accounts blueprint

| Endpoint                         | Function                | URL Path                               | Method | Referenced In                                                                                              |
| -------------------------------- | ----------------------- | -------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| `accounts.list_accounts`         | `list_accounts`         | `/accounts`                            | GET    | `base.html` navbar (until Phase 4), `accounts/form.html` breadcrumb+cancel, savings dashboard, route redirects |
| `accounts.new_account`           | `new_account`           | `/accounts/new`                        | GET    | `accounts/list.html` button, savings dashboard (Phase 4), route redirects                                  |
| `accounts.create_account`        | `create_account`        | `/accounts`                            | POST   | `accounts/form.html` action                                                                                |
| `accounts.edit_account`          | `edit_account`          | `/accounts/<id>/edit`                  | GET    | `accounts/list.html` links, route redirects                                                                |
| `accounts.update_account`        | `update_account`        | `/accounts/<id>`                       | POST   | `accounts/form.html` action                                                                                |
| `accounts.deactivate_account`    | `deactivate_account`    | `/accounts/<id>/delete`                | POST   | `accounts/list.html` action                                                                                |
| `accounts.reactivate_account`    | `reactivate_account`    | `/accounts/<id>/reactivate`            | POST   | `accounts/list.html` action                                                                                |
| `accounts.inline_anchor_form`    | `inline_anchor_form`    | `/accounts/<id>/inline-anchor-form`    | GET    | `accounts/_anchor_cell.html` HTMX                                                                          |
| `accounts.inline_anchor_update`  | `inline_anchor_update`  | `/accounts/<id>/inline-anchor`         | PATCH  | `accounts/_anchor_cell.html` HTMX                                                                          |
| `accounts.inline_anchor_display` | `inline_anchor_display` | `/accounts/<id>/inline-anchor-display` | GET    | `accounts/_anchor_cell.html` HTMX                                                                          |
| `accounts.true_up`               | `true_up`               | `/accounts/<id>/true-up`               | PATCH  | `grid/_anchor_edit.html` HTMX                                                                              |
| `accounts.anchor_form`           | `anchor_form`           | `/accounts/<id>/anchor-form`           | GET    | `grid/grid.html` HTMX                                                                                     |
| `accounts.anchor_display`        | `anchor_display`        | `/accounts/<id>/anchor-display`        | GET    | `grid/_anchor_edit.html` HTMX                                                                              |
| `accounts.create_account_type`   | `create_account_type`   | `/accounts/types`                      | POST   | `accounts/list.html` form, settings dashboard form (Phase 3)                                               |
| `accounts.update_account_type`   | `update_account_type`   | `/accounts/types/<id>`                 | POST   | `accounts/list.html` form, settings dashboard form (Phase 3)                                               |
| `accounts.delete_account_type`   | `delete_account_type`   | `/accounts/types/<id>/delete`          | POST   | `accounts/list.html` action, settings dashboard action (Phase 3)                                           |
| `accounts.hysa_detail`           | `hysa_detail`           | `/accounts/<id>/hysa`                  | GET    | `savings/dashboard.html` link, route redirects                                                             |
| `accounts.update_hysa_params`    | `update_hysa_params`    | `/accounts/<id>/hysa/params`           | POST   | `accounts/hysa_detail.html` form                                                                           |

### categories blueprint

| Endpoint                     | Function          | URL Path                  | Method | Referenced In                                                                               |
| ---------------------------- | ----------------- | ------------------------- | ------ | ------------------------------------------------------------------------------------------- |
| `categories.list_categories` | `list_categories` | `/categories`             | GET    | `base.html` navbar (until Phase 4), route redirects. Redirects to settings (Phase 3).       |
| `categories.create_category` | `create_category` | `/categories`             | POST   | `categories/list.html` form, settings dashboard form (Phase 3)                              |
| `categories.delete_category` | `delete_category` | `/categories/<id>/delete` | POST   | `categories/list.html` action, settings dashboard action (Phase 3)                          |

### settings blueprint

| Endpoint          | Function | URL Path    | Method | Referenced In                                                               |
| ----------------- | -------- | ----------- | ------ | --------------------------------------------------------------------------- |
| `settings.show`   | `show`   | `/settings` | GET    | `base.html` navbar, route redirects. Becomes settings dashboard (Phase 3).  |
| `settings.update` | `update` | `/settings` | POST   | `settings/settings.html` form, settings dashboard General section (Phase 3) |

### salary blueprint

| Endpoint                    | Function             | URL Path                             | Method | Referenced In                                                                                                                                                                                                   |
| --------------------------- | -------------------- | ------------------------------------ | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `salary.list_profiles`      | `list_profiles`      | `/salary`                            | GET    | `base.html` navbar, `salary/form.html` breadcrumb+cancel, `salary/breakdown.html` breadcrumb, `salary/projection.html` breadcrumb, `salary/tax_config.html` breadcrumb+back, route redirects, onboarding banner |
| `salary.new_profile`        | `new_profile`        | `/salary/new`                        | GET    | `salary/list.html` button, route redirects                                                                                                                                                                      |
| `salary.create_profile`     | `create_profile`     | `/salary`                            | POST   | `salary/form.html` action                                                                                                                                                                                       |
| `salary.edit_profile`       | `edit_profile`       | `/salary/<id>/edit`                  | GET    | `salary/list.html` links, `salary/breakdown.html` breadcrumb+back, `salary/projection.html` breadcrumb, `salary/form.html` quick links, route redirects                                                         |
| `salary.update_profile`     | `update_profile`     | `/salary/<id>`                       | POST   | `salary/form.html` action                                                                                                                                                                                       |
| `salary.delete_profile`     | `delete_profile`     | `/salary/<id>/delete`                | POST   | `salary/list.html` action                                                                                                                                                                                       |
| `salary.add_raise`          | `add_raise`          | `/salary/<id>/raises`                | POST   | `salary/_raises_section.html` HTMX                                                                                                                                                                              |
| `salary.delete_raise`       | `delete_raise`       | `/salary/raises/<id>/delete`         | POST   | `salary/_raises_section.html` HTMX                                                                                                                                                                              |
| `salary.add_deduction`      | `add_deduction`      | `/salary/<id>/deductions`            | POST   | `salary/_deductions_section.html` HTMX                                                                                                                                                                          |
| `salary.delete_deduction`   | `delete_deduction`   | `/salary/deductions/<id>/delete`     | POST   | `salary/_deductions_section.html` HTMX                                                                                                                                                                          |
| `salary.breakdown`          | `breakdown`          | `/salary/<id>/breakdown/<period_id>` | GET    | `salary/breakdown.html` period dropdown, `salary/projection.html` links                                                                                                                                         |
| `salary.breakdown_current`  | `breakdown_current`  | `/salary/<id>/breakdown`             | GET    | `salary/list.html` links, `salary/form.html` quick links                                                                                                                                                        |
| `salary.projection`         | `projection`         | `/salary/<id>/projection`            | GET    | `salary/list.html` links, `salary/form.html` quick links                                                                                                                                                        |
| `salary.tax_config`         | `tax_config`         | `/salary/tax-config`                 | GET    | `salary/list.html` link, route redirects. Redirects to settings (Phase 3).                                                                                                                                      |
| `salary.update_tax_config`  | `update_tax_config`  | `/salary/tax-config`                 | POST   | `salary/tax_config.html` form, settings dashboard form (Phase 3)                                                                                                                                                |
| `salary.update_fica_config` | `update_fica_config` | `/salary/fica-config`                | POST   | `salary/tax_config.html` form, settings dashboard form (Phase 3)                                                                                                                                                |

### transfers blueprint

| Endpoint                                 | Function                       | URL Path                             | Method | Referenced In                                                                                                                    |
| ---------------------------------------- | ------------------------------ | ------------------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `transfers.list_transfer_templates`      | `list_transfer_templates`      | `/transfers`                         | GET    | `base.html` navbar, `transfers/form.html` breadcrumb+cancel, route redirects                                                     |
| `transfers.new_transfer_template`        | `new_transfer_template`        | `/transfers/new`                     | GET    | `transfers/list.html` button, `grid/grid.html` "Add Transfer" link, `savings/dashboard.html` goal transfer link, route redirects |
| `transfers.create_transfer_template`     | `create_transfer_template`     | `/transfers`                         | POST   | `transfers/form.html` action                                                                                                     |
| `transfers.edit_transfer_template`       | `edit_transfer_template`       | `/transfers/<id>/edit`               | GET    | `transfers/list.html` links, route redirects                                                                                     |
| `transfers.update_transfer_template`     | `update_transfer_template`     | `/transfers/<id>`                    | POST   | `transfers/form.html` action                                                                                                     |
| `transfers.delete_transfer_template`     | `delete_transfer_template`     | `/transfers/<id>/delete`             | POST   | `transfers/list.html` action                                                                                                     |
| `transfers.reactivate_transfer_template` | `reactivate_transfer_template` | `/transfers/<id>/reactivate`         | POST   | `transfers/list.html` action                                                                                                     |
| `transfers.get_cell`                     | `get_cell`                     | `/transfers/cell/<id>`               | GET    | `transfers/_transfer_quick_edit.html` HTMX, `transfers/_transfer_full_edit.html`                                                 |
| `transfers.get_quick_edit`               | `get_quick_edit`               | `/transfers/quick-edit/<id>`         | GET    | `transfers/_transfer_cell.html` HTMX                                                                                             |
| `transfers.get_full_edit`                | `get_full_edit`                | `/transfers/<id>/full-edit`          | GET    | `transfers/_transfer_quick_edit.html` HTMX                                                                                       |
| `transfers.update_transfer`              | `update_transfer`              | `/transfers/instance/<id>`           | PATCH  | `transfers/_transfer_quick_edit.html` HTMX                                                                                       |
| `transfers.create_ad_hoc`                | `create_ad_hoc`                | `/transfers/ad-hoc`                  | POST   | `grid/grid.html` or transfer cells                                                                                               |
| `transfers.delete_transfer`              | `delete_transfer`              | `/transfers/instance/<id>`           | DELETE | `transfers/_transfer_full_edit.html`                                                                                             |
| `transfers.mark_done`                    | `mark_done`                    | `/transfers/instance/<id>/mark-done` | POST   | `transfers/_transfer_full_edit.html`                                                                                             |
| `transfers.cancel_transfer`              | `cancel_transfer`              | `/transfers/instance/<id>/cancel`    | POST   | `transfers/_transfer_full_edit.html`                                                                                             |

### savings blueprint

| Endpoint              | Function      | URL Path                     | Method | Referenced In                                                                      |
| --------------------- | ------------- | ---------------------------- | ------ | ---------------------------------------------------------------------------------- |
| `savings.dashboard`   | `dashboard`   | `/savings`                   | GET    | `base.html` navbar, mortgage/auto_loan/investment/hysa back links, route redirects |
| `savings.new_goal`    | `new_goal`    | `/savings/goals/new`         | GET    | `savings/dashboard.html` button, route redirects                                   |
| `savings.create_goal` | `create_goal` | `/savings/goals`             | POST   | `savings/goal_form.html` action                                                    |
| `savings.edit_goal`   | `edit_goal`   | `/savings/goals/<id>/edit`   | GET    | `savings/dashboard.html` links, route redirects                                    |
| `savings.update_goal` | `update_goal` | `/savings/goals/<id>`        | POST   | `savings/goal_form.html` action                                                    |
| `savings.delete_goal` | `delete_goal` | `/savings/goals/<id>/delete` | POST   | `savings/goal_form.html` action                                                    |

### mortgage blueprint

| Endpoint                    | Function           | URL Path                                      | Method | Referenced In                                                                 |
| --------------------------- | ------------------ | --------------------------------------------- | ------ | ----------------------------------------------------------------------------- |
| `mortgage.dashboard`        | `dashboard`        | `/accounts/<id>/mortgage`                     | GET    | `savings/dashboard.html` link, route redirects (`accounts.py`, `mortgage.py`) |
| `mortgage.create_params`    | `create_params`    | `/accounts/<id>/mortgage/setup`               | POST   | `mortgage/setup.html` form                                                    |
| `mortgage.update_params`    | `update_params`    | `/accounts/<id>/mortgage/params`              | POST   | `mortgage/dashboard.html` form                                                |
| `mortgage.add_rate_change`  | `add_rate_change`  | `/accounts/<id>/mortgage/rate`                | POST   | `mortgage/_rate_history.html` HTMX                                            |
| `mortgage.add_escrow`       | `add_escrow`       | `/accounts/<id>/mortgage/escrow`              | POST   | `mortgage/_escrow_list.html` HTMX                                             |
| `mortgage.delete_escrow`    | `delete_escrow`    | `/accounts/<id>/mortgage/escrow/<cid>/delete` | POST   | `mortgage/_escrow_list.html` HTMX                                             |
| `mortgage.payoff_calculate` | `payoff_calculate` | `/accounts/<id>/mortgage/payoff`              | POST   | `mortgage/dashboard.html` form                                                |

### auto_loan blueprint

| Endpoint                  | Function        | URL Path                          | Method | Referenced In                                                                  |
| ------------------------- | --------------- | --------------------------------- | ------ | ------------------------------------------------------------------------------ |
| `auto_loan.dashboard`     | `dashboard`     | `/accounts/<id>/auto-loan`        | GET    | `savings/dashboard.html` link, route redirects (`accounts.py`, `auto_loan.py`) |
| `auto_loan.create_params` | `create_params` | `/accounts/<id>/auto-loan/setup`  | POST   | `auto_loan/setup.html` form                                                    |
| `auto_loan.update_params` | `update_params` | `/accounts/<id>/auto-loan/params` | POST   | `auto_loan/dashboard.html` form                                                |

### investment blueprint

| Endpoint                   | Function        | URL Path                                 | Method | Referenced In                                                                    |
| -------------------------- | --------------- | ---------------------------------------- | ------ | -------------------------------------------------------------------------------- |
| `investment.dashboard`     | `dashboard`     | `/accounts/<id>/investment`              | GET    | `savings/dashboard.html` link, `retirement/dashboard.html` link, route redirects |
| `investment.growth_chart`  | `growth_chart`  | `/accounts/<id>/investment/growth-chart` | GET    | `investment/dashboard.html` HTMX slider                                          |
| `investment.update_params` | `update_params` | `/accounts/<id>/investment/params`       | POST   | `investment/dashboard.html` form                                                 |

### retirement blueprint

| Endpoint                     | Function          | URL Path                          | Method | Referenced In                                                                                                 |
| ---------------------------- | ----------------- | --------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------- |
| `retirement.dashboard`       | `dashboard`       | `/retirement`                     | GET    | `base.html` navbar, `retirement/pension_form.html` back, route redirects                                      |
| `retirement.pension_list`    | `pension_list`    | `/retirement/pension`             | GET    | `retirement/dashboard.html` button, route redirects                                                           |
| `retirement.create_pension`  | `create_pension`  | `/retirement/pension`             | POST   | `retirement/pension_form.html` form                                                                           |
| `retirement.edit_pension`    | `edit_pension`    | `/retirement/pension/<id>/edit`   | GET    | `retirement/pension_form.html` links, route redirects                                                         |
| `retirement.update_pension`  | `update_pension`  | `/retirement/pension/<id>`        | POST   | `retirement/pension_form.html` form                                                                           |
| `retirement.delete_pension`  | `delete_pension`  | `/retirement/pension/<id>/delete` | POST   | `retirement/pension_form.html` action                                                                         |
| `retirement.gap_analysis`    | `gap_analysis`    | `/retirement/gap`                 | GET    | `retirement/dashboard.html` HTMX                                                                              |
| `retirement.update_settings` | `update_settings` | `/retirement/settings`            | POST   | `retirement/dashboard.html` form (until Phase 3), settings dashboard form (Phase 3). Redirects to settings.   |

### charts blueprint

| Endpoint                      | Function               | URL Path                       | Method | Referenced In                       |
| ----------------------------- | ---------------------- | ------------------------------ | ------ | ----------------------------------- |
| `charts.dashboard`            | `dashboard`            | `/charts`                      | GET    | `base.html` navbar, route redirects |
| `charts.balance_over_time`    | `balance_over_time`    | `/charts/balance-over-time`    | GET    | `charts/dashboard.html` HTMX        |
| `charts.spending_by_category` | `spending_by_category` | `/charts/spending-by-category` | GET    | `charts/dashboard.html` HTMX        |
| `charts.budget_vs_actuals`    | `budget_vs_actuals`    | `/charts/budget-vs-actuals`    | GET    | `charts/dashboard.html` HTMX        |
| `charts.amortization`         | `amortization`         | `/charts/amortization`         | GET    | `charts/dashboard.html` HTMX        |
| `charts.net_worth`            | `net_worth`            | `/charts/net-worth`            | GET    | `charts/dashboard.html` HTMX        |
| `charts.net_pay`              | `net_pay`              | `/charts/net-pay`              | GET    | `charts/dashboard.html` HTMX        |

### Test Files With Route Assertions

All route tests use hardcoded URL paths (e.g., `client.get("/templates")`) rather than `url_for()`. This means URL path changes would break tests, but url_for endpoint name changes would not. Since this plan does not change any URL paths (only adds redirects), test path references are safe -- tests will receive 302 instead of 200 for redirected GET endpoints.

**Flash message assertions that reference "Template" (must be updated in Phase 2):**

| File                                  | Line | Assertion                                                 |
| ------------------------------------- | ---- | --------------------------------------------------------- |
| `tests/test_routes/test_templates.py` | 322  | `assert b"Template not found" in resp.data`               |
| `tests/test_routes/test_templates.py` | 338  | `assert b"Template not found" in resp.data`               |
| `tests/test_routes/test_templates.py` | 419  | `assert b"Template not found" in resp.data`               |
| `tests/test_routes/test_templates.py` | 433  | `assert b"Template not found" in resp.data`               |
| `tests/test_routes/test_templates.py` | 487  | `assert b"Template not found" in resp.data`               |
| `tests/test_routes/test_transfers.py` | 352  | `assert b"Transfer template not found." in response.data` |
| `tests/test_routes/test_transfers.py` | 367  | `assert b"Transfer template not found." in response.data` |

**Tests hitting GET endpoints that will redirect in Phase 3 (deferred updates):**

| File                                     | Endpoint Hit             | New Behavior |
| ---------------------------------------- | ------------------------ | ------------ |
| `tests/test_routes/test_categories.py`   | `GET /categories`        | 302 → `/settings?section=categories` |
| `tests/test_routes/test_pay_periods.py`  | `GET /pay-periods/generate` | 302 → `/settings?section=pay-periods` |
| `tests/test_routes/test_salary.py`       | `GET /salary/tax-config` | 302 → `/settings?section=tax` |

---

## Audit Finding Cross-Reference

Every finding from the audit mapped to its resolution phase.

### Section 1: Navigation and Information Architecture

| Finding                                  | Severity | Resolution                                                                        |
| ---------------------------------------- | -------- | --------------------------------------------------------------------------------- |
| 1.1 Two "Accounts" navbar items          | Critical | Phase 4: make savings dashboard the sole "Accounts" navbar link                   |
| 1.2 Navbar has 11 items, cannot scale    | Critical | Phase 4: reduce to 8 flat items                                                   |
| 1.3 Duplicate bi-wallet2 icon            | Major    | Phase 1: change Salary icon to bi-cash-coin                                       |
| 1.4 Setup pages in primary nav           | Major    | Phase 3 + 4: absorb into settings dashboard, remove from navbar                   |
| 1.5 Transfers disconnected from accounts | Major    | Phase 4: add transfer links on savings dashboard account cards                    |
| 1.6 No navbar active state               | Minor    | Phase 1: add conditional active class                                             |
| 1.7 No mobile nav ordering strategy      | Minor    | Phase 4: 8 flat items is manageable on mobile; daily-use items listed first       |

### Section 2: Settings and Configuration Sprawl

| Finding                                           | Severity | Resolution                                                                                 |
| ------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| 2.1 Retirement settings not on Settings page      | Major    | Phase 3: retirement settings section in settings dashboard                                 |
| 2.2 Tax config buried under Salary                | Major    | Phase 3: tax config section in settings dashboard                                          |
| 2.3 Inconsistent config patterns per account type | Major    | Phase 3: documented as intentional; account-specific configs stay on detail pages           |
| 2.4 "Phase 7" help text in Settings               | Minor    | Phase 1: replace with user-facing text                                                     |
| 2.5 Settings page is sparse                       | Minor    | Phase 3: settings dashboard with 6 sections replaces sparse page                           |
| 2.6 Configuration inventory                       | Minor    | Phase 3: settings sidebar serves as the configuration inventory                            |

### Section 3: Nomenclature and Labeling

| Finding                                        | Severity | Resolution                                             |
| ---------------------------------------------- | -------- | ------------------------------------------------------ |
| 3.1 "Templates" is jargon                      | Critical | Phase 2: rename to "Recurring Transactions" everywhere |
| 3.2 "Transfer Template" compounds jargon       | Major    | Phase 2: rename to "Recurring Transfer"                |
| 3.3 "Anchor Balance" not intuitive             | Major    | Phase 2: rename to "Current Balance"                   |
| 3.4 Inconsistent amount terminology            | Major    | Phase 2: rename "Default Amount" to "Amount"           |
| 3.5 "Accounts & Savings" misleading            | Major    | Phase 2: rename to "Accounts Dashboard"                |
| 3.6 Mortgage rate decimal vs percentage        | Minor    | Phase 2: change input to accept percentage             |
| 3.7 Recurrence labels duplicated in 4 files    | Minor    | Phase 2: DRY into shared macro or context processor    |
| 3.8 Flash messages expose validation internals | Minor    | Phase 2: user-friendly error messages                  |
| 3.9 "P&I" used without explanation             | Minor    | Phase 1: add tooltip or expand abbreviation            |
| 3.10 Account type names shown raw              | Minor    | Phase 1: format_account_type filter                    |

### Section 4: Workflow Analysis

| Finding                                | Severity                   | Resolution                                                                      |
| -------------------------------------- | -------------------------- | ------------------------------------------------------------------------------- |
| 4A Recurring expense creation friction | Critical (discoverability) | Phase 2: rename fixes discoverability; Phase 5: "View on Grid" in flash message |
| 4B Transfer setup friction             | Major                      | Phase 4: transfer quick-action on account cards with pre-fill                   |
| 4C Deduction target account buried     | Major                      | Phase 5: move field higher, improve help text                                   |
| 4D Ad-hoc expense grid friction        | Minor                      | Phase 5: tooltip on empty cells, cursor hint                                    |
| 4E Payday workflow friction            | Minor                      | Phase 5: larger carry-forward button                                            |
| 4F Unified account view confusion      | Critical                   | Phase 2 (rename) + Phase 4 (single nav entry)                                   |
| 4G Mortgage payoff buried              | Minor                      | No change: payoff is secondary action, page is functional                       |
| 4H Retirement calculation opacity      | Minor                      | Deferred: calculation tooltips are documentation enhancement                    |

### Section 5: Page-Level UI Review

| Page                 | Issues Found                   | Resolution                                      |
| -------------------- | ------------------------------ | ----------------------------------------------- |
| Budget Grid          | Quick-select labels unclear    | Phase 1: tooltips                               |
| Templates List       | Jargon naming                  | Phase 2                                         |
| Templates Form       | "Effective from" confusing     | Phase 2: improve help text                      |
| Salary List          | Net pay not explained          | Phase 5: add explanation                        |
| Salary Form          | Dense deduction form           | Phase 5: layout improvement                     |
| Accounts List        | Overlaps dashboard             | Phase 4: remove from navbar, accessible from savings dashboard |
| Savings Dashboard    | No "New Account"               | Phase 4: add button                             |
| HYSA Detail          | No breadcrumbs                 | Phase 1: add breadcrumb                         |
| Mortgage Dashboard   | Decimal rate, long page        | Phase 2: rate format; no change for page length |
| Investment Dashboard | Employer fields always visible | Phase 5: conditional visibility                 |
| Retirement Dashboard | No breadcrumbs                 | Phase 1: add breadcrumb                         |
| Charts Dashboard     | h1 heading                     | Phase 1: change to h4                           |
| Categories           | No rename capability           | Deferred: feature addition                      |
| Pay Periods          | No view/extend                 | Deferred: feature addition                      |
| Settings             | Sparse                         | Phase 3: settings dashboard                     |

### Section 6: Phase 7 Readiness

| Finding                                       | Severity | Resolution                                                   |
| --------------------------------------------- | -------- | ------------------------------------------------------------ |
| 6.1 Navbar cannot absorb 12th item            | Critical | Phase 4: 8 items leaves room for Scenarios as 9th            |
| 6.2 No global scenario indicator              | Critical | Phase 6: reserve UI slot; implementation deferred to Phase 7 |
| 6.3 Grid has no room for scenario controls    | Major    | Phase 6: reserve UI slot                                     |
| 6.4 Side-by-side comparison needs layout work | Major    | Deferred to Phase 7 implementation                           |
| 6.5 Nomenclature compounds with scenarios     | Major    | Phase 2: resolved by renaming "Templates"                    |
| 6.6 Charts can accommodate scenario overlays  | Minor    | No change needed: already compatible                         |
| 6.7 Retirement gap analysis is scenario-ready | Minor    | No change needed: already compatible                         |

### Section 7: Visual and Interaction Consistency

| Finding                              | Severity | Resolution                                 |
| ------------------------------------ | -------- | ------------------------------------------ |
| 7.1 Heading level inconsistency      | Major    | Phase 1: standardize to h4                 |
| 7.2 Inconsistent breadcrumb presence | Major    | Phase 1: add breadcrumbs to all pages      |
| 7.3 Button style conventions         | Minor    | No change needed: already consistent       |
| 7.4 Card structure consistency       | Minor    | No change needed: already consistent       |
| 7.5 Status colors consistent         | Minor    | No change needed: already consistent       |
| 7.6 Chart styling consistent         | Minor    | No change needed: already consistent       |
| 7.7 Table class usage varies         | Minor    | Phase 1: standardize classes               |
| 7.8 Dark mode complete               | Minor    | No change needed: already complete         |
| 7.9 Accessibility focus indicators   | Minor    | No change needed: already well-implemented |
| 7.10 HTMX loading states             | Minor    | No change needed: both patterns functional |
