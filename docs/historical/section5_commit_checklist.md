> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Section 5 Commit Checklist

All commits from `docs/implementation_plan_section5.md` in prescribed order.

---

## Phase 1 -- Regression Baseline

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 1 | **Commit #0** | `test(section5): add regression baseline for loan dashboard and savings goals` | Add regression tests for the full loan dashboard workflow, amortization engine consistency, savings goal lifecycle, and balance calculator with shadow transactions. Safety net before any Section 5 changes. |

## Phase 2 -- Amortization Engine Foundation (Tasks 5.1, 5.7, 5.8)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 2 | **Commit 5.1-1** | `feat(amortization): extend engine to accept payment history for projection scenarios` | Add `PaymentRecord` dataclass and optional `payments` parameter to `generate_schedule()`, `calculate_summary()`, and `get_loan_projection()`. Add `is_confirmed` field to `AmortizationRow`. When payments are provided, the engine uses actual payment amounts instead of the static monthly P&I for matched months. Backward-compatible -- `payments=None` produces identical output to current behavior. |
| 3 | **Commit 5.1-2** | `feat(loan): pass payment history from shadow transactions to amortization engine` | Query shadow income transactions on debt accounts (transfers received) and pass them to the amortization engine as `PaymentRecord` instances. The loan dashboard and payoff calculator now reflect actual/committed payment data. Derives "real" current principal from confirmed payment replay. |
| 4 | **Commit 5.1-3** | `feat(loan): prompt recurring transfer creation after loan parameter save` | After saving loan parameters, display a prompt on the loan dashboard offering to create a recurring monthly transfer from checking to the debt account for the calculated P&I (+escrow) amount. Adds a new route to create the transfer template and generate transfers for existing periods. |
| 5 | **Commit 5.7-1** | `feat(amortization): incorporate ARM rate change history into schedule projections` | Add `RateChangeRecord` dataclass and optional `rate_changes` parameter to the amortization engine. When rate changes are provided, the engine re-amortizes at each rate adjustment (standard ARM behavior). Add `interest_rate` field to `AmortizationRow`. Loan dashboard passes `RateHistory` entries for ARM loans. |
| 6 | **Commit 5.8-1** | `fix(amortization): handle overpayment and zero-balance termination edge cases` | Add two guards to `generate_schedule()`: (1) cap principal payment so balance never goes negative on overpayment, and (2) stop generating schedule rows once the balance reaches zero. Payments after payoff are ignored. |

## Phase 3 -- Loan Visualization (Tasks 5.5, 5.14, 5.13)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 7 | **Commit 5.5-1** | `feat(loan): add multi-scenario visualization to payoff calculator and balance chart` | Extend `get_amortization_breakdown()` to return three projection lines (original schedule, committed schedule, what-if schedule) plus a floor marker (confirmed payments only). Update the loan dashboard chart to render multiple datasets with distinct styles. Update payoff results partial to show committed vs. floor side by side. |
| 8 | **Commit 5.14-1** | `feat(loan): add payment allocation breakdown showing principal/interest/escrow split` | Add a payment breakdown card to the loan dashboard Overview tab showing how the current payment is split between principal, interest, and escrow with percentages and a stacked progress bar. |
| 9 | **Commit 5.13-1** | `feat(loan): add full month-by-month amortization schedule view` | Add a new "Amortization Schedule" tab to the loan dashboard with a collapsible table showing every month's payment, principal, interest, extra payment, remaining balance, and confirmed/projected status. |

## Phase 4 -- Loan Lifecycle (Task 5.9)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 10 | **Commit 5.9-1** | `feat(loan): auto-set recurring transfer end date to projected payoff date` | On loan dashboard load, compare the projected payoff date to the recurring transfer's recurrence rule `end_date` and update it if different. Prevents shadow transaction generation beyond payoff. Handles edge cases where payoff moves later or negative amortization prevents payoff. |
| 11 | **Commit 5.9-2** | `feat(accounts): display paid-off badge on debt accounts with zero principal` | Add `is_paid_off` flag to loan account data in the savings dashboard service by replaying confirmed payments and checking if the remaining balance is zero. Display a green "Paid Off" badge on the accounts dashboard. |
| 12 | **Commit 5.9-3** | `feat(accounts): add account archival for paid-off loans and inactive accounts` | Add `is_archived` boolean column to accounts (migration). Add archive/unarchive routes with guard against archiving accounts with active transfer templates. Filter archived accounts from the main dashboard, grid selector, and other dropdowns. Add a collapsed "Archived Accounts" section on the dashboard. |

## Phase 5 -- Savings Goals (Tasks 5.4, 5.15)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 13 | **Commit 5.4-1** | `feat(savings): add ref tables for goal modes and income units` | Create `ref.goal_modes` (Fixed, Income-Relative) and `ref.income_units` (Paychecks, Months) reference tables with corresponding enum members, ref_cache entries, seed data, and Jinja globals. Migration creates both tables. |
| 14 | **Commit 5.4-2** | `feat(savings): add goal_mode_id, income_unit_id, income_multiplier to savings_goals` | Add three columns to the `savings_goals` table (migration): `goal_mode_id` (FK, defaults to Fixed), `income_unit_id` (FK, nullable), and `income_multiplier` (Numeric, nullable). Update Marshmallow schemas with cross-field validation (income-relative mode requires unit and multiplier; fixed mode rejects them). |
| 15 | **Commit 5.4-3** | `feat(savings): resolve income-relative goal targets from paycheck calculator` | Add `resolve_goal_target()` to savings goal service that computes the dollar target for income-relative goals using `multiplier * net_pay_per_unit`. Update savings dashboard service to load paycheck data and resolve targets for all goals. |
| 16 | **Commit 5.4-4** | `feat(savings): add income-relative mode toggle to savings goal form` | Add a mode selector (Fixed / Income-Relative) to the goal form with conditional field visibility. Pass goal modes and income units to the template. Display resolved targets with mode indicators (e.g., "3 months of salary") on the dashboard. |
| 17 | **Commit 5.15-1** | `feat(savings): add goal completion trajectory and pace indicator` | Add `calculate_trajectory()` to savings goal service that computes months to goal, projected completion date, and pace indicator (on_track/behind/ahead) from current balance, target, and monthly contribution. Display trajectory on goal cards with actionable messaging. |

## Phase 6 -- Aggregate Metrics (Task 5.12)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 18 | **Commit 5.12-1** | `feat(accounts): add debt summary metrics and debt-to-income ratio` | Add `_compute_debt_summary()` to savings dashboard service that aggregates total debt, total monthly payments, weighted average rate, and projected debt-free date across all loan accounts. Compute DTI ratio from total monthly debt payments divided by gross monthly income with color-coded thresholds (green < 36%, yellow 36-43%, red > 43%). |

## Phase 7 -- Advanced Calculators (Tasks 5.10, 5.11)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 19 | **Commit 5.10-1** | `feat(loan): add refinance what-if calculator with side-by-side comparison` | Add a "Refinance" tab to the loan dashboard with a form for new rate, new term, and closing costs. Compute and display a side-by-side comparison of current vs. refinanced schedule including monthly payment change, total interest change, and break-even point. |
| 20 | **Commit 5.11-1** | `feat(debt): add snowball/avalanche/custom strategy service` | Create `debt_strategy_service.py` -- a pure function service that computes cross-account debt payoff strategies. Supports avalanche (highest rate first), snowball (smallest balance first), and custom ordering. Tracks freed payment rollover, per-account payoff timelines, and aggregate metrics. |
| 21 | **Commit 5.11-2** | `feat(debt): add snowball/avalanche strategy page with comparison table` | Create `debt_strategy` blueprint with a dashboard page. Loads all active non-archived debt accounts, renders a form for extra monthly amount and strategy selection, and displays a comparison table of avalanche vs. snowball vs. no-extra scenarios with per-account payoff timelines. |
| 22 | **Commit 5.11-3** | `feat(debt): add multi-line balance chart to debt strategy visualization` | Add a Chart.js chart to the debt strategy page showing one line per debt account with balances converging to zero over time under the selected strategy. Uses CSP-compliant `data-*` attribute pattern. |

## Phase 8 -- Standalone Views (Task 5.16)

| # | Commit | Message | Description |
|---|--------|---------|-------------|
| 23 | **Commit 5.16-1** | `feat(obligations): add recurring obligation summary page` | Create `obligations` blueprint with a summary page that aggregates all active recurring transaction templates and transfer templates. Groups by type (recurring expenses, transfers out, income), computes monthly equivalents, and displays summary metrics (total outflows, total income, net committed cash flow). |

## Opportunistic Improvements (Require Developer Approval)

| # | ID | Description |
|---|----|-------------|
| 24 | **O-1** | Extract the shadow income transaction query (used by both the loan route in 5.1-2 and chart_data_service in 5.5-1) into a shared helper to avoid duplication. ~30 min. |
| 25 | **O-2** | Add a UI tooltip on the `payment_day` field noting that days 29-31 are clamped to the last day of shorter months. Template-only. ~5 min. |
| 26 | **O-3** | Extend the payment breakdown card (5.14) to show projected escrow changes if escrow components have inflation rates (e.g., "Escrow will increase to $X next year"). ~20 min. |
