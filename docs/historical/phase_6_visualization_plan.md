> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel -- Phase 6 Visualization Plan

**Version:** 1.1
**Date:** March 5, 2026
**Prerequisite Phases:** 1 ✅, 2 ✅, 4 (v2) ✅, 3 (v3 HYSA) ✅, 4 (v3 Debt) ✅, 5 (v3 Investments) ✅
**Stack:** Flask · Jinja2 · HTMX · Chart.js · Bootstrap 5 · PostgreSQL

---

## 1. Phase Context

Phase 6 is the dedicated visualization phase. It builds a centralized **Charts page** (`/charts`) with cross-cutting financial charts that span multiple account types and data domains. This is distinct from the basic inline charts already built alongside individual features (the mortgage payoff chart, the auto loan balance chart, the investment growth chart, and the retirement gap waterfall). Phase 6 also upgrades existing inline charts with interactive controls (sliders, live-updating inputs) where the original implementation was form-only.

### What Already Exists Before Phase 6

| Chart | Location | Built In | Type |
|-------|----------|----------|------|
| Payoff chart (standard vs. accelerated) | Mortgage dashboard | Phase 4 | Chart.js line (`payoff_chart.js`) |
| Auto loan balance-over-time | Auto loan dashboard | Phase 4 | Chart.js line |
| Investment growth projection | Investment dashboard | Phase 5 | Chart.js line/stacked area |
| Retirement gap waterfall | Retirement dashboard | Phase 5 | Chart.js bar/waterfall |
| Salary net pay trajectory | Salary projection view | Phase 2 | Chart.js line |

### What Phase 6 Adds

1. A unified **Charts page** with all cross-cutting visualizations.
2. New chart types not tied to a single dashboard.
3. Interactive upgrades to existing inline charts.
4. A shared Chart.js configuration layer for consistent theming.

**Build principle:** No placeholders. Charts are added to the page as they are ready. The Charts
page starts with whatever charts are built first and grows incrementally. Scenario Comparison will
be added to the Charts page when Phase 7 (Scenarios) is built -- it is not part of Phase 6.

---

## 2. Chart Inventory

### 2.1 New Charts (Charts Page)

| # | Chart Name | Type | Data Source(s) | Priority |
|---|-----------|------|---------------|----------|
| C1 | Balance Over Time (All Accounts) | Multi-line | `balance_calculator`, all account types | P0 |
| C2 | Spending by Category | Horizontal bar | `transactions` (expenses, status = done) | P0 |
| C3 | Budget vs. Actuals | Grouped bar | `transactions` (estimated vs. actual by category) | P0 |
| C4 | Amortization Breakdown | Stacked area | `amortization_engine` (principal vs. interest per payment) | P1 |
| C5 | Net Worth Over Time | Line | All account balances (assets − liabilities) | P1 |
| C6 | Net Pay Trajectory | Step line | `paycheck_calculator`, `salary_raises` | P1 |

### 2.2 Interactive Upgrades to Existing Charts

| # | Upgrade | Current State | Target State | Location |
|---|---------|--------------|-------------|----------|
| U1 | Payoff calculator slider | Form-based `hx-post` | Range slider with live-updating results | Mortgage dashboard |
| U2 | Investment growth time horizon | Static projection to retirement | Adjustable horizon slider | Investment dashboard |
| U3 | Retirement gap sensitivity | Fixed 4% withdrawal rate | Adjustable rate slider + return rate slider | Retirement dashboard |

---

## 3. Architecture

### 3.1 Route & URL Structure

| Method | URL | Returns | Description |
|--------|-----|---------|-------------|
| `GET` | `/charts` | Page | Charts dashboard (all visualizations) |
| `GET` | `/charts/balance-over-time` | Fragment | Balance chart data + canvas (HTMX) |
| `GET` | `/charts/spending-by-category` | Fragment | Spending chart data + canvas (HTMX) |
| `GET` | `/charts/budget-vs-actuals` | Fragment | Budget vs. actuals data + canvas (HTMX) |
| `GET` | `/charts/amortization` | Fragment | Amortization breakdown data + canvas (HTMX) |
| `GET` | `/charts/net-worth` | Fragment | Net worth over time data + canvas (HTMX) |
| `GET` | `/charts/net-pay` | Fragment | Net pay trajectory data + canvas (HTMX) |

Each fragment endpoint accepts query parameters for filtering (date range, account selection, category). The Charts page loads chart containers on initial page load, then populates each chart via HTMX `hx-get` with `hx-trigger="load"` so the page renders immediately and charts fill in progressively.

### 3.2 Blueprint Registration

```
app/routes/charts.py  →  Blueprint('charts', __name__, url_prefix='/charts')
```

Register in `app/__init__.py` alongside existing blueprints.

### 3.3 Service Layer

Create a new `chart_data_service.py` that orchestrates data from existing services. This service does **not** duplicate business logic -- it calls `balance_calculator`, `amortization_engine`, `growth_engine`, etc. and reshapes their output into chart-ready structures (labels + datasets).

```
app/services/chart_data_service.py
```

**Key methods:**

| Method | Calls | Returns |
|--------|-------|---------|
| `get_balance_over_time(user_id, account_ids, start, end)` | `BalanceCalculator`, account queries | `{labels: [...], datasets: [{label, data, color}, ...]}` |
| `get_spending_by_category(user_id, start, end)` | Transaction queries grouped by category | `{labels: [...], data: [...], colors: [...]}` |
| `get_budget_vs_actuals(user_id, period_ids)` | Transaction queries (estimated vs. actual) | `{labels: [...], estimated: [...], actual: [...]}` |
| `get_amortization_breakdown(account_id)` | `AmortizationEngine` | `{labels: [...], principal: [...], interest: [...]}` |
| `get_net_worth_over_time(user_id, start, end)` | All account balances, asset/liability classification | `{labels: [...], data: [...]}` |
| `get_net_pay_trajectory(user_id)` | `PaycheckCalculator`, salary projections | `{labels: [...], data: [...]}` |

All methods return plain Python dicts. The route layer converts them to JSON for Chart.js via `tojson` in Jinja2 templates.

### 3.4 File Structure (New Files)

```
app/
├── routes/
│   └── charts.py                    # Charts blueprint
├── services/
│   └── chart_data_service.py        # Data orchestration for charts
├── templates/
│   └── charts/
│       ├── dashboard.html           # Main charts page (full page)
│       ├── _balance_over_time.html  # HTMX fragment
│       ├── _spending_category.html  # HTMX fragment
│       ├── _budget_vs_actuals.html  # HTMX fragment
│       ├── _amortization.html       # HTMX fragment
│       ├── _net_worth.html          # HTMX fragment
│       └── _net_pay.html            # HTMX fragment
└── static/
    └── js/
        ├── chart_theme.js           # Shared Chart.js defaults + theme integration
        ├── chart_balance.js         # Balance over time chart
        ├── chart_spending.js        # Spending by category chart
        ├── chart_budget.js          # Budget vs. actuals chart
        ├── chart_amortization.js    # Amortization breakdown chart
        ├── chart_net_worth.js       # Net worth over time chart
        ├── chart_net_pay.js         # Net pay trajectory chart
        └── chart_slider.js          # Reusable slider + live-update logic
```

---

## 4. Chart.js Theme Layer

### 4.1 Problem

The existing `payoff_chart.js` hardcodes colors like `#6c757d` and `#adb5bd`. These work in dark mode but break in light mode. Phase 6 introduces a shared theme configuration that reads CSS custom properties so charts respond to theme toggles automatically.

### 4.2 Implementation -- `chart_theme.js`

This file sets Chart.js global defaults and exposes a `ShekelChart` namespace.

**Responsibilities:**

- Read `--shekel-text-primary`, `--shekel-text-secondary`, `--shekel-border-subtle`, `--shekel-accent`, and semantic colors from CSS custom properties at render time.
- Set `Chart.defaults.color`, `Chart.defaults.borderColor`, `Chart.defaults.font.family` globally.
- Expose a palette of 8 distinct series colors that maintain sufficient contrast in both dark and light themes.
- Provide a `ShekelChart.create(canvasId, config)` wrapper that merges theme defaults with chart-specific options.
- Listen for the theme toggle event and re-render all active charts.

**Palette (8-color series):**

| Index | Name | Dark Hex | Light Hex | Usage |
|-------|------|----------|-----------|-------|
| 0 | Accent (steel blue) | `#4A9ECC` | `#2878A8` | Primary series / checking |
| 1 | Green | `#2ECC71` | `#1A9B50` | Savings / income |
| 2 | Amber | `#E67E22` | `#C96B15` | Credit / escrow |
| 3 | Rose | `#D97BA0` | `#B05A80` | Expenses |
| 4 | Teal | `#1ABC9C` | `#148F77` | HYSA / investments |
| 5 | Purple | `#9B59B6` | `#7D3C98` | Retirement accounts |
| 6 | Coral | `#E74C3C` | `#C0392B` | Liabilities / danger |
| 7 | Slate | `#95A5A6` | `#707B7C` | Secondary / baseline |

### 4.3 Theme Toggle Re-render

When the user toggles dark/light mode, the theme toggle handler (already in `app.js`) dispatches a `shekel:theme-changed` custom event. `chart_theme.js` listens for this event and calls `update()` on every tracked chart instance with new colors derived from the updated CSS variables.

### 4.4 Retrofitting Existing Charts

After `chart_theme.js` is built, refactor `payoff_chart.js` and any other inline chart scripts to use `ShekelChart.create()` instead of raw `new Chart()`. This ensures all charts--inline and Charts page--share consistent theming.

---

## 5. Detailed Chart Specifications

### 5.1 C1 -- Balance Over Time (All Accounts)

**Purpose:** Show projected balances for all accounts on a single timeline, giving the user a holistic view of their financial position over the 2-year projection horizon.

**Chart type:** Multi-line (one line per account).

**X-axis:** Pay period end dates (biweekly intervals, matching the grid).

**Y-axis:** Dollar amount. Dual Y-axis with toggle: left axis for checking/savings (smaller values), right axis for mortgage/retirement (larger values). Toggle via a checkbox, default on. Mortgage principal will dwarf checking balance, so dual-axis is the default view to keep smaller accounts readable.

**Data source:** `BalanceCalculator` for checking/savings. `AmortizationEngine` for mortgage/auto loan remaining principal. `GrowthEngine` for investment/retirement projected balances. `InterestProjection` for HYSA.

**Interactivity:**

- Account selector: checkboxes to show/hide individual account lines. Default: checking + savings visible, others off. Implemented as an HTMX form that re-requests the fragment with updated `account_ids` query params.
- Date range: inherits the grid's date range controls (3 Periods, 6 Periods, 3 Months, etc.) via shared query params.
- Hover tooltip: shows all visible account balances at that date.

**Chart.js config notes:** Use `tension: 0.3` for smooth curves. `pointRadius: 0` with `pointHitRadius: 10` for clean lines with hover activation. Use `fill: false` to avoid visual clutter with multiple lines.

### 5.2 C2 -- Spending by Category

**Purpose:** Show where money goes, grouped by category, for a selected time range.

**Chart type:** Horizontal bar chart (categories on Y-axis, dollars on X-axis). Horizontal orientation is better for long category names.

**Data source:** Sum of `actual_amount` (or `estimated_amount` if no actual) for all expense transactions with status `done` in the selected period range, grouped by `category.group_name`.

**Interactivity:**

- Period range selector: dropdown or quick-select buttons (Current Period, Last 3 Periods, Last 6 Periods, Last 12 Periods, Year-to-Date).
- Click a bar to drill down to individual items within that category (loads a secondary fragment below the chart showing the line items).

**Colors:** Use the rose/expense palette for bars. Each category group gets a shade from a gradient scale.

### 5.3 C3 -- Budget vs. Actuals

**Purpose:** Compare budgeted (estimated) amounts to actual amounts per category, highlighting where the user is over or under budget.

**Chart type:** Grouped bar chart. Each category has two bars side by side -- estimated (muted color) and actual (vivid color). Overspend highlighted in red.

**Data source:** Estimated vs. actual amounts from transactions, grouped by category, for selected periods.

**Interactivity:**

- Period selector (same as C2).
- Toggle between "by category" (grouped) and "by period" (time series comparison).

**Visual treatment:** If actual exceeds estimated, the overspend portion of the actual bar is filled in danger red. If actual is under estimated, the bar is green-tinted.

### 5.4 C4 -- Amortization Breakdown

**Purpose:** Show the principal vs. interest composition of each loan payment over the full life of the loan.

**Chart type:** Stacked area chart. Principal area on bottom (green), interest area on top (amber/coral). The total height at each point equals the fixed monthly payment.

**Data source:** `AmortizationEngine.generate_schedule()` for the selected loan account.

**Interactivity:**

- Loan selector: dropdown to switch between mortgage and auto loan (if multiple).
- Hover tooltip: shows payment number, date, principal portion, interest portion, remaining balance.

### 5.5 C5 -- Net Worth Over Time

**Purpose:** A single-line summary of total assets minus total liabilities projected over time.

**Chart type:** Line chart with area fill. Fill is green-tinted when positive, red-tinted when negative (use a gradient or the `fill` plugin with threshold at zero).

**Data source:** Aggregation of all account projected balances. Assets (checking + savings + HYSA + retirement + brokerage) minus liabilities (mortgage remaining principal + auto loan remaining principal).

**Interactivity:** Same date range controls as C1.

### 5.6 C6 -- Net Pay Trajectory

**Purpose:** Show how net biweekly pay changes over time due to scheduled raises (merit, COLA, custom), visualizing the impact of salary growth.

**Chart type:** Step line chart (since pay changes are discrete events, not gradual).

**Data source:** `PaycheckCalculator` run forward through all salary raise events. Each raise produces a new net pay amount that stays flat until the next raise.

**Interactivity:** Salary profile selector (if multiple profiles exist). Hover shows gross, deductions, and net at each point.

**Note:** This chart already exists in a basic form on the Salary Projection view (Phase 2). Phase 6 adds it to the Charts page with the shared theme layer and enhanced tooltips. The inline version on the Salary page remains as-is.

---

## 6. Interactive Upgrade Specifications

### 6.1 U1 -- Payoff Calculator Slider (Mortgage Dashboard)

**Current:** User types an extra payment amount into a form field, clicks Calculate, HTMX posts to `/accounts/<id>/mortgage/payoff`, results fragment renders below.

**Upgrade:** Add an HTML range slider (`<input type="range">`) alongside the text input. The slider and text input are synced bidirectionally via JavaScript. On slider `input` event (not `change`), debounce 250ms, then trigger the same HTMX `hx-post` to fetch updated results. The text input retains its existing behavior for precise values.

**Slider range:** $0 to $2,000 (configurable via `data-max` attribute), step $25.

**Implementation:**

- Add `chart_slider.js` -- a reusable module that binds a range input to a text input and triggers an HTMX request on debounced change.
- Update `mortgage/_payoff_form.html` to include the range slider.
- The existing `hx-post` endpoint and `_payoff_results.html` fragment require no changes.

### 6.2 U2 -- Investment Growth Horizon Slider

**Current:** Investment dashboard shows growth projection to the planned retirement date.

**Upgrade:** Add a year slider (current year to retirement year + 10) that adjusts the projection horizon. The chart re-renders via HTMX fragment swap when the slider value changes.

### 6.3 U3 -- Retirement Gap Sensitivity Controls

**Current:** Gap analysis uses the fixed 4% safe withdrawal rate from `user_settings`.

**Upgrade:** Add two sliders on the retirement dashboard:

- Safe withdrawal rate: 2.0% to 6.0%, step 0.25%.
- Assumed annual return: 3.0% to 12.0%, step 0.5%.

Moving either slider triggers an HTMX request to `/retirement/gap` with the slider values as query params. The endpoint recalculates the gap with the provided rates (without saving them to `user_settings` -- these are exploratory, not persistent changes).

---

## 7. Charts Page Layout

### 7.1 Page Structure

The Charts page (`/charts`) uses a card-based layout. Each chart occupies a Bootstrap card within a responsive grid. The layout prioritizes the most useful charts at the top.

```
┌─────────────────────────────────────────────────────────────┐
│  Charts                                           [filters] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Balance Over Time (All Accounts)             [C1]  │    │
│  │  [account checkboxes]  [date range]  [dual-axis ☑]  │    │
│  │  ┌───────────────────────────────────────────────┐  │    │
│  │  │              (multi-line chart)                │  │    │
│  │  └───────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌──────────────────────┐  ┌───────────────────────────┐    │
│  │  Spending by         │  │  Budget vs. Actuals  [C3] │    │
│  │  Category       [C2] │  │  [period selector]        │    │
│  │  [period selector]   │  │  ┌─────────────────────┐  │    │
│  │  ┌────────────────┐  │  │  │  (grouped bar)      │  │    │
│  │  │ (horiz bar)    │  │  │  └─────────────────────┘  │    │
│  │  └────────────────┘  │  └───────────────────────────┘    │
│  └──────────────────────┘                                   │
│                                                             │
│  ┌──────────────────────┐  ┌───────────────────────────┐    │
│  │  Amortization   [C4] │  │  Net Worth Over Time [C5] │    │
│  │  [loan selector]     │  │  [date range controls]    │    │
│  │  ┌────────────────┐  │  │  ┌─────────────────────┐  │    │
│  │  │ (stacked area) │  │  │  │  (line + area fill) │  │    │
│  │  └────────────────┘  │  │  └─────────────────────┘  │    │
│  └──────────────────────┘  └───────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Net Pay Trajectory                           [C6]  │    │
│  │  [profile selector]                                 │    │
│  │  ┌───────────────────────────────────────────────┐  │    │
│  │  │              (step line chart)                 │  │    │
│  │  └───────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Responsive Behavior

- Full-width charts (C1, C6) span `col-12`.
- Paired charts (C2+C3, C4+C5) use `col-lg-6` and stack on smaller viewports.
- Each card has a minimum height of 350px to ensure charts are readable.
- When Scenario Comparison is added in Phase 7, it pairs with C6 and both drop to `col-lg-6`.

### 7.3 Progressive Loading (HTMX)

Each chart card renders immediately with a loading skeleton (a muted placeholder with a subtle pulse animation). The chart content is loaded via `hx-get="/charts/<name>"` with `hx-trigger="load"`. This keeps the initial page load fast and avoids blocking on data-heavy queries.

```html
<div class="card">
  <div class="card-header">Balance Over Time</div>
  <div class="card-body"
       hx-get="/charts/balance-over-time"
       hx-trigger="load"
       hx-swap="innerHTML">
    <div class="chart-skeleton"></div>
  </div>
</div>
```

---

## 8. Navigation Update

Add "Charts" to the navbar. Per the UI/UX design doc §3.1, this was already planned:

> **Navigation items (added in later phases):** ... Charts (`/charts`) -- Phase 5.

(Phase 5 in v2 numbering = Phase 6 in v3 numbering.)

Position the Charts link after Accounts in the nav order: Budget Grid · Templates · Categories · Salary · Accounts · **Charts** · Settings.

---

## 9. Testing Strategy

### 9.1 Service Tests (`tests/test_services/test_chart_data_service.py`)

| Test | Validates |
|------|-----------|
| `test_balance_over_time_single_checking` | Basic case: one checking account, correct labels and data points |
| `test_balance_over_time_multi_account` | Multiple account types produce separate datasets |
| `test_balance_over_time_date_range_filter` | Start/end params correctly bound the data |
| `test_spending_by_category_groups_correctly` | Expense transactions summed by category group |
| `test_spending_by_category_only_done` | Only transactions with status `done` are included |
| `test_budget_vs_actuals_estimated_and_actual` | Both estimated and actual amounts returned per category |
| `test_amortization_breakdown_matches_engine` | Output matches `amortization_engine.generate_schedule()` |
| `test_net_worth_assets_minus_liabilities` | Correct aggregation across account types |
| `test_net_pay_trajectory_step_changes` | Net pay changes at raise dates, flat between raises |
| `test_empty_data_returns_empty_structure` | No crash on zero accounts/transactions |

### 9.2 Route Tests (`tests/test_routes/test_charts.py`)

| Test | Validates |
|------|-----------|
| `test_charts_page_requires_auth` | Unauthenticated requests redirect to login |
| `test_charts_page_renders` | GET `/charts` returns 200 with expected card containers |
| `test_balance_fragment_returns_canvas` | GET `/charts/balance-over-time` returns HTML with canvas element |
| `test_spending_fragment_accepts_period_params` | Query params filter data correctly |
| `test_htmx_header_present` | Fragments return correctly when `HX-Request` header is present |
| `test_non_htmx_redirects` | Fragment URLs without HTMX header redirect to full charts page |

### 9.3 JavaScript Testing (Manual / Visual)

Since the project does not use a JS test framework, chart rendering is validated manually:

- Each chart renders in both dark and light themes without color contrast issues.
- Theme toggle re-renders charts with correct colors.
- Slider controls debounce correctly and trigger HTMX requests.
- Tooltips show formatted dollar amounts and correct labels.
- Charts render gracefully with zero data (empty state message, not a broken chart).

---

## 10. Implementation Order

### Sprint 1 -- Foundation (Week 1)

- [ ] Create `chart_theme.js` with shared Chart.js defaults, CSS variable reading, palette, and `ShekelChart.create()` wrapper.
- [ ] Create `chart_theme.js` theme toggle listener and re-render logic.
- [ ] Retrofit `payoff_chart.js` to use `ShekelChart.create()`.
- [ ] Create `charts` Blueprint with `GET /charts` returning the dashboard page.
- [ ] Create `charts/dashboard.html` with card-based layout (cards added per chart as built).
- [ ] Add "Charts" to navbar.
- [ ] Write route auth tests.

### Sprint 2 -- Core Charts (Weeks 2-3)

- [ ] Build `chart_data_service.py` scaffolding with `get_balance_over_time()`.
- [ ] Build C1 (Balance Over Time) -- route, fragment template, JS, account selector.
- [ ] Build C2 (Spending by Category) -- route, fragment, JS, period selector.
- [ ] Build C3 (Budget vs. Actuals) -- route, fragment, JS, period selector.
- [ ] Write service tests for C1, C2, C3.
- [ ] Write route tests for C1, C2, C3 fragments.

### Sprint 3 -- Extended Charts (Week 4)

- [ ] Build C4 (Amortization Breakdown) -- route, fragment, JS, loan selector.
- [ ] Build C5 (Net Worth Over Time) -- route, fragment, JS.
- [ ] Build C6 (Net Pay Trajectory) -- route, fragment, JS, profile selector.
- [ ] Write service tests for C4, C5, C6.
- [ ] Write route tests for C4, C5, C6 fragments.

### Sprint 4 -- Interactive Upgrades + Polish (Week 5)

- [ ] Build `chart_slider.js` reusable module.
- [ ] U1: Add payoff calculator slider to mortgage dashboard.
- [ ] U2: Add investment growth horizon slider.
- [ ] U3: Add retirement gap sensitivity sliders.
- [ ] Manual visual QA: both themes, all charts, empty states, tooltips.
- [ ] Retrofit any remaining inline charts to `ShekelChart.create()`.

### Sprint 5 -- Hardening (Week 6)

- [ ] Performance: ensure chart data queries use appropriate indexes (check `EXPLAIN ANALYZE`).
- [ ] Add loading error states (if HTMX fragment fetch fails, show a retry message).
- [ ] Empty state handling: each chart shows a helpful message when there is no data.
- [ ] Accessibility: ensure chart cards have proper `aria-label` attributes and that keyboard users can navigate between chart controls.
- [ ] Final test pass -- run full test suite, fix any regressions.

---

## 11. Dependencies & Assumptions

| Dependency | Status | Notes |
|-----------|--------|-------|
| Chart.js already in project | ✅ | Used by `payoff_chart.js`; no new CDN dependency needed |
| Phases 1-4 (v3) complete | ✅ | All prerequisite engines and services are built |
| Phase 5 (v3) finishing | 🔧 | Growth engine, pension calculator, retirement gap needed for C5 and Charts page |
| No new Python packages | ✅ | All data reshaping is plain Python dicts; no pandas or plotting libs |
| No new JS build pipeline | ✅ | All JS is vanilla, loaded via `<script>` tags; consistent with project philosophy |

---

## 12. Resolved Design Decisions (Phase 6)

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| Q1 | Filter persistence across navigation | **URL params** | Bookmarkable, shareable, no server state; consistent with grid date range pattern |
| Q2 | Chart data endpoint format | **HTML fragments with `data-*` attributes** | Consistent with HTMX patterns and existing payoff chart approach; no separate JSON API layer |
| Q3 | Dual Y-axis for Balance Over Time | **Dual axis toggle, default on** | Mortgage principal will dwarf checking balance; dual-axis keeps smaller accounts readable |
| Q4 | Charts page auto-refresh | **No** | Data changes only when the user edits transactions; manual refresh is sufficient |
| Q5 | Chart export (PNG/CSV) | **Defer to Phase 8** | CSV export is already planned for Phase 8 (Hardening); chart PNG export can be added alongside it |
