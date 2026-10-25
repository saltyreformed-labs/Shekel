> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 2: Concept Catalog

This document accumulates across sessions P2-a, P2-b, P2-c, and P2-d. It is gated complete only by
the P2-reconcile review session. Each session catalogs one concept family and appends its sections;
no session declares Phase 2 complete.

Schema per concept (audit plan section 2): intended definition (sourced), units/type, producer
sites (1.7.8-filtered), consumer sites, primary path (justified or `PRIMARY PATH: UNKNOWN` + Q-NN),
multi-path flag with exact Phase 3 pairs.

Operational rule applied throughout (inventory 1.7.8): a function is a PRODUCER only if it
computes/derives a value for the concept (arithmetic or aggregation yielding the concept's value).
Compare-only, read-only, guard-only, delegate-only, and assemble-only functions are CONSUMERS even
when the raw 1.7.3 index lists them as producers. The six P1-f relocations
(`dashboard_service._compute_alerts`@252 / `_get_balance_info`@334,
`budget_variance_service._compute_actual`@381, `calendar_service._build_day_entry`@240 /
`_compute_month_end_balance`@435, and `year_end_summary_service.py:1465-1469` ARM anchor) are
CONSUMERS, not producers. Where a producer list disagrees with the raw 1.7.3 index because of a
P1-f correction, the P1-f correction wins and the reconciliation is noted in the concept entry.

---

## Scope of this session (P2-a): balance / cash-flow family

Tokens cataloged here: `checking_balance`, `account_balance`, `projected_end_balance`,
`period_subtotal`, `chart_balance_series`, `net_worth`, `savings_total`, `debt_total`.

Inventory 1.7.2 vocabulary-addition review: the balance/cash-flow additions in 1.7.2 are
`cash_runway_days` (P1-b) and `entry_sum_total`/`entry_remaining` (P1-c). None are folded into this
session: `cash_runway_days` is a runway-days integer (1.7.2 explicitly defers the
fold-into-`emergency_fund_coverage_months` decision; it is a P2 cash-flow-adjacent token but its
producer `_compute_cash_runway`@`dashboard_service.py:375` is a trailing-30-day realised metric, not
a balance projection -- left for the cash-flow/savings session that owns `emergency_fund_*`).
`entry_sum_total`/`entry_remaining` are entry-tracking tokens (Q-08 family), cataloged with the
transaction/entry family, not here. No balance-family token was added beyond Appendix A, so this
session's set equals the eight tokens above.

New question raised by this session: **Q-15** (canonical multi-account aggregate balance owner),
filed in `09_open_questions.md`. Cross-linked from `account_balance`, `debt_total`, `savings_total`.

### QC note (P2-a verification)

Five producer-list entries spot-checked by Read against the cited `file:line`:
1. `calculate_balances`@`balance_calculator.py:35` -- CONFIRMED producer (anchor + income - expenses
   roll-forward loop at 69-86; settled-exclusion at 88-107).
2. `_sum_remaining`@`balance_calculator.py:389` + `_sum_all`@`:422` -- CONFIRMED genuine arithmetic;
   both gate `if txn.status_id != projected_id: continue` (411-412 / 443-444), confirming
   W-091/W-092/A-02 Projected-only exclusion.
3. `_compute_account_projections`@`savings_dashboard_service.py:294` -- CONFIRMED producer/dispatch;
   delegates to `calculate_balances`@343 (checking), `calculate_balances_with_interest`@335 (HYSA),
   `get_loan_projection`@362 (loan, `current_bal = proj.current_balance`@373 per A-04).
4. `get_month_detail`@`calendar_service.py:88` -- delegate (returns `_build_month_summary(...)`@130);
   RECLASSIFIED CONSUMER (reconciled against raw 1.7.3 which listed it as producer).
5. `_compute_net_worth`@`year_end_summary_service.py:689` -- CONFIRMED aggregator/producer;
   delegates per-account to `_build_account_data`@750, sums with liabilities negative, delta
   arithmetic @746.
Additional read: `_compute_debt_summary`@`savings_dashboard_service.py:802` (CONFIRMED producer;
sums `Decimal(str(lp.current_principal))`@840,855 -- the stored column, not the engine-walked
balance) and `accounts.checking_detail`@`accounts.py:1376` (CONFIRMED route consumer; calls
`calculate_balances`@1425 directly -- "the same balance calculator the grid uses", docstring 1383).

---

## Concept: checking_balance

- **Intended definition.** The real checking-account balance projected period-by-period forward
  from the user-entered anchor balance: at the anchor period, `anchor_balance + remaining_income -
  remaining_expenses`; for each post-anchor period, `previous_end_balance + projected_income -
  projected_expenses`. Only Projected-status transactions contribute; done/received/settled/credit/
  cancelled rows are excluded because the anchor already reflects settled activity
  (`balance_calculator.py:36-55` docstring; `:88-107` stale-anchor comment;
  `_sum_remaining`@`:390-398` and `_sum_all`@`:423-431` docstrings). Source: CLAUDE.md domain
  concept "Anchor Balance (real checking balance, projections flow forward from it). Balance
  Calculator (period-by-period from anchor)" (`CLAUDE.md`). A-02/W-091/W-092 confirm settled
  carry-forward source rows are excluded from the projection. Developer expectation E-04
  (`00_priors.md:178-182`): the checking balance for the current pay period is the same number on
  the grid, `/savings`, and `/accounts`.
- **Units / type.** Decimal money (2dp), keyed per `pay_period_id`; producer returns
  `OrderedDict[period_id -> Decimal]` plus a `stale_anchor_warning` bool.
- **Producer sites.**
  - svc:`calculate_balances`@`balance_calculator.py:35` -- canonical engine (no-interest accounts).
  - svc:`calculate_balances_with_interest`@`balance_calculator.py:112` -- same, plus per-period APY
    accrual (interest-bearing accounts).
  - Internal arithmetic helpers: `_sum_remaining`@`balance_calculator.py:389` (anchor period),
    `_sum_all`@`balance_calculator.py:422` (post-anchor); both Projected-only, income via
    `effective_amount`, expense via `_entry_aware_amount`@`balance_calculator.py:292`.
  - Producer (1.5 models): `Account.current_anchor_balance`@`account.py:51` (the stored anchor base
    the engine starts from); `Account.current_anchor_period_id`@`account.py` (anchor period).
  - **P1-f reconciliation.** Raw 1.7.3 also lists `_compute_alerts`@`dashboard_service.py:252`,
    `_get_balance_info`@`dashboard_service.py:334`, and
    `_compute_month_end_balance`@`calendar_service.py:435` as producers. All three are P1-f
    relocations (compare-only / read-and-delegate / delegate-to-`calculate_balances`) -> CONSUMERS.
    `get_month_detail`@`calendar_service.py:88` is a verified delegate (-> `_build_month_summary`)
    -> CONSUMER. Genuine checking_balance producers reduce to the two `balance_calculator`
    functions.
- **Consumer sites.**
  - route:`index`@`grid.py:164`, route:`balance_row`@`grid.py:393` (grid balance row -> calls
    `calculate_balances`).
  - route:`page`@`dashboard.py:40`, route:`balance_section`@`dashboard.py:160` (via
    `dashboard_service._get_balance_info`@334, a P1-f consumer that reads the engine output).
  - route:`checking_detail`@`accounts.py:1376` (calls `calculate_balances`@`accounts.py:1425`
    directly; anchor pattern @1418-1421).
  - svc consumers: `_compute_account_projections`@`savings_dashboard_service.py:294` (delegates),
    `_compute_month_end_balance`@`calendar_service.py:435` (delegates), `_compute_alerts` /
    `_get_balance_info`@`dashboard_service.py:252,334`.
  - templates: `grid/grid.html`, `accounts/checking_detail.html`, `dashboard/_balance_runway.html`,
    `accounts/_anchor_cell.html`, `grid/_anchor_edit.html`.
- **Primary path.** `balance_calculator.calculate_balances`@`balance_calculator.py:35` (and
  `calculate_balances_with_interest`@`:112` for interest-bearing accounts). Justification: it is the
  CLAUDE.md-named domain engine ("Balance Calculator (period-by-period from anchor)"); its docstring
  defines the anchor-forward formula; every other site that surfaces a checking balance delegates to
  it (`accounts.checking_detail`@`accounts.py:1425` calls it directly and its docstring states "the
  same balance calculator the grid uses"; `savings_dashboard._compute_account_projections`@`:343`
  delegates; `calendar._compute_month_end_balance`@`:435` delegates). Roadmap v5 Section 2 Stage A
  ("a single canonical balance computation") names unification as PENDING, which corroborates that
  `calculate_balances` is the intended canonical base even though wrapper inputs are not yet
  unified.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.3 marks `checking_balance` `*`; 1.7.4 lists it as
  single-path with the note "functions as alias for `account_balance` in most contexts; Phase 3
  must verify this aliasing is intentional" -- the 1.7.3-vs-1.7.4 tension is itself a Phase 3 input.
  Audit plan section 3.1 mandates the grid-vs-`/savings`-vs-`/accounts` comparison; symptom #1
  ($160 grid vs $114.29 `/savings`) lives here. Because all three pages route through the SAME
  engine (`calculate_balances`), Phase 3/Phase 5 must compare the engine **inputs**, not the engine:
    - **Pair A (grid vs /accounts checking):** producer `calculate_balances`@`balance_calculator.py:35`
      via route `grid.balance_row`@`grid.py:393` (txn query/anchor in `grid.index`@`grid.py:164`,
      scope @ ~229) **vs** the same producer via route `accounts.checking_detail`@`accounts.py:1376`
      (txn query @`accounts.py:1407-1416`: `account_id` + `pay_period_id in period_ids` +
      `scenario_id == scenario.id` + `is_deleted False`; anchor @`accounts.py:1418-1421`). Compare
      transaction-scope filters, scenario filter, shadow inclusion, and anchor-fallback rule.
    - **Pair B (grid vs /savings):** `calculate_balances`@`balance_calculator.py:35` (grid) **vs**
      `savings_dashboard_service._compute_account_projections`@`savings_dashboard_service.py:294`
      (which delegates to `calculate_balances`@`:343` / `calculate_balances_with_interest`@`:335`
      but feeds it the pre-loaded `all_transactions` set filtered only by `account_id`@`:320-323`;
      anchor @`:325-328`). Compare the pre-load scope vs the grid's per-route query.
    - **Pair C (grid subtotal vs grid balance):** the grid's inline period subtotal
      (`grid.py:263-279`, expense via `effective_amount`) vs the grid balance row's
      `_sum_remaining`/`_sum_all` (expense via `_entry_aware_amount`@`balance_calculator.py:292`).
      Same transactions, different expense formula -> the displayed subtotal row can disagree with
      the balance row on the same page (see `period_subtotal`, Q-10).

---

## Concept: projected_end_balance

- **Intended definition.** The per-account end-of-period balance the engine projects for any
  period at or after the anchor: identical formula to `checking_balance` for checking/HYSA accounts
  (anchor + Projected income - Projected expenses, rolled forward), substituting the amortization
  schedule for loan accounts (A-04 dual policy: ARM uses stored `current_principal`, fixed-rate uses
  engine-walked balance) and the growth engine for investment accounts. Source:
  `balance_calculator.py:36-55` docstring (return value "balances: OrderedDict mapping period_id ->
  Decimal end balance"); `_compute_account_projections`@`savings_dashboard_service.py:298-306`
  docstring ("Dispatches to the appropriate projection engine based on account type"); A-04
  (`09_open_questions.md`) for the loan substitution. Developer expectation E-04 anchor.
- **Units / type.** Decimal money (2dp) per `pay_period_id` (and per `(3,6,12)`-month horizon in the
  detail pages).
- **Producer sites.**
  - svc:`calculate_balances`@`balance_calculator.py:35`; svc:`calculate_balances_with_interest`@
    `balance_calculator.py:112` (checking/HYSA).
  - svc:`_compute_account_projections`@`savings_dashboard_service.py:294` (per-account dispatcher;
    genuine producer of the per-account `projected` dict -- delegates the checking math to
    `calculate_balances`, owns the loan/investment substitution and the 3/6/12-month horizon walk
    @379-383).
  - svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338` (retirement-account
    projected balances).
  - **P1-f reconciliation.** Raw 1.7.3 also lists `_compute_month_end_balance`@
    `calendar_service.py:435` (P1-f relocation -> delegates to `calculate_balances`) and
    `get_month_detail`@`calendar_service.py:88` (verified delegate). Both are CONSUMERS.
- **Consumer sites.** route:`index`@`grid.py:164`; route:`balance_row`@`grid.py:393`;
  route:`dashboard`@`savings.py:107`; route:`checking_detail`@`accounts.py:1376`;
  route:`interest_detail`@`accounts.py:1233`; route:`dashboard`@`investment.py:63`;
  route:`growth_chart`@`investment.py:363`. Templates: `grid/grid.html` (26),
  `grid/_balance_row.html` (26), `savings/dashboard.html` (212),
  `accounts/checking_detail.html` (55), `accounts/interest_detail.html` (64),
  `investment/_growth_chart.html` (16,20), `retirement/_retirement_account_rows.html` (17). JS:
  `growth_chart.js`, `payoff_chart.js`, `retirement_gap_chart.js`.
- **Primary path.** Per account type: `balance_calculator.calculate_balances`@
  `balance_calculator.py:35` / `calculate_balances_with_interest`@`:112` for checking/HYSA;
  `amortization_engine.get_loan_projection`@`amortization_engine.py:864` for loans (A-04);
  `growth_engine.project_balance`@`growth_engine.py:164` for investments. The per-type canonical is
  structurally clear from the dispatch in `_compute_account_projections`@
  `savings_dashboard_service.py:300-373` and the A-04 answer; for the checking flavor it is the same
  canonical engine as `checking_balance`. There is no single cross-account-type producer (the
  dispatch is reimplemented in `year_end_summary_service._build_account_data`@`:750`); see
  `account_balance` and Q-15 for the wrapper-ownership question.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 / E-04 invariant anchor; audit plan 3.1.
  Symptom #1 root concept. Pairs Phase 3 must compare for the same `(user_id, period_id,
  scenario_id, account_id=checking)`:
    - **Pair A:** `calculate_balances`@`balance_calculator.py:35` via `grid.balance_row`@
      `grid.py:393` vs via `accounts.checking_detail`@`accounts.py:1425`.
    - **Pair B:** `calculate_balances`@`balance_calculator.py:35` (grid) vs
      `_compute_account_projections`@`savings_dashboard_service.py:294` (`/savings`,
      `current_bal = balances.get(current_period.id)`@`:352`).
    - **Pair C (loan account):** `get_loan_projection`@`amortization_engine.py:864`
      (`current_bal = proj.current_balance`@`savings_dashboard_service.py:373`) vs the dashboard
      label rendered from `params.current_principal` at `loan/dashboard.html:104` (A-11 / Q-11
      cross-reference -- loan family, P2-b owns the loan-side detail; recorded here because the
      `projected_end_balance` of a loan account is what `/savings` and net worth consume).

---

## Concept: account_balance

- **Intended definition.** The displayed balance of any account (checking, HYSA, loan, investment)
  at a given pay period, type-dispatched: anchor-forward `calculate_balances` for plain/checking,
  `calculate_balances_with_interest` for HYSA, amortization schedule for loans (A-04 dual policy),
  growth engine for investments. Liability accounts are shown as positive balances on their own
  pages but contribute negative to net worth. Source: `_compute_account_projections`@
  `savings_dashboard_service.py:298-306` docstring; `_compute_net_worth`@
  `year_end_summary_service.py:697-721` docstring ("Uses the balance calculator for
  checking/savings, interest calculator for HYSA-type accounts, amortization schedule for loan
  accounts, and growth engine for investment accounts"); A-04. Developer expectation E-04: the same
  account balance is the same number on every page that shows it; an unlabeled difference is a
  finding.
- **Units / type.** Decimal money (2dp) per account per period.
- **Producer sites (aggregated across 1.1 and route layer).**
  - svc:`_compute_account_projections`@`savings_dashboard_service.py:294` -- the `/savings`
    per-account dispatcher (genuine producer of `current_balance` + `projected`).
  - svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338` -- retirement
    accounts.
  - svc:`_compute_net_worth`@`year_end_summary_service.py:689` -- aggregates per-account balances
    (via `_build_account_data`@`:750` -> `_get_account_balance_map`, the SECOND independent
    dispatch implementation; W-151..W-169 net_worth_amort plan).
  - route-resident: `accounts.checking_detail`@`accounts.py:1376` (calls `calculate_balances`@
    `:1425` directly), `accounts.interest_detail`@`accounts.py:1233`, `investment.dashboard`@
    `investment.py:63` -- routes that compute/read a per-account `current_balance` inline rather
    than through `_compute_account_projections`. Aggregated here because the producer set is not
    service-only.
  - Producer (1.5 models): `Account.current_anchor_balance`@`account.py:51`;
    `AccountAnchorHistory.anchor_balance`@`account.py:152` (anchor history).
  - **P1-f reconciliation.** Raw 1.7.3 lists `_get_balance_info`@`dashboard_service.py:334` and
    `_compute_alerts`@`dashboard_service.py:252` as producers -- both P1-f relocations ->
    CONSUMERS. `resolve_grid_account`@`account_resolver.py:36` and `resolve_analytics_account`@
    `account_resolver.py:79` are account-lookup resolvers (delegate-only) -> CONSUMERS, not
    balance producers.
- **Consumer sites.** route:`interest_detail`@`accounts.py:1233`; route:`checking_detail`@
  `accounts.py:1376`; route:`dashboard`@`investment.py:63`; route:`growth_chart`@
  `investment.py:363`; route:`balance_section`@`dashboard.py:160`; route:`page`@`dashboard.py:40`;
  route:`dashboard`@`savings.py:107`. Templates: `grid/grid.html` (17), `savings/dashboard.html`
  (186), `accounts/checking_detail.html` (43), `accounts/interest_detail.html` (52),
  `accounts/_anchor_cell.html` (48), `accounts/list.html` (46,110), `grid/_anchor_edit.html` (53),
  `dashboard/_balance_runway.html` (10), `retirement/_retirement_account_rows.html` (15),
  `investment/_growth_chart.html` (16,20).
- **Primary path.** Per account type the canonical producer is clear and the same as
  `projected_end_balance`: `calculate_balances`/`calculate_balances_with_interest`@
  `balance_calculator.py:35,112` (checking/HYSA), `amortization_engine.get_loan_projection`@
  `amortization_engine.py:864` (loans, A-04), `growth_engine.project_balance`@`growth_engine.py:164`
  (investments). However, the **cross-account multi-account aggregator/dispatcher is not canonical**:
  two independent dispatch implementations exist -- `_compute_account_projections`@
  `savings_dashboard_service.py:294` (drives `/savings`, dashboard) and `_build_account_data`/
  `_get_account_balance_map`@`year_end_summary_service.py:750` (drives net worth). Roadmap v5
  Section 2 Stage A names "a single canonical balance computation" as PENDING (`00_priors.md:152`),
  and net_worth_amort W-152 ("Net worth section and savings progress section must use identical
  calculation paths for all account types") is `planned-per-plan` (NOT done). The codebase does not
  designate which dispatcher is canonical. **See Q-15.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-1 (7 svc producers, 6 routes, 10 templates);
  anchor of symptom #5 (`/accounts` vs other pages). Audit plan section 3.1 mandates the explicit
  grid-vs-`/accounts`-vs-`/savings`-vs-dashboard-vs-investment comparison. Exact pairs:
    - **Checking account, Pair A/B/C:** as enumerated under `checking_balance` (all route through
      `calculate_balances`@`balance_calculator.py:35`; compare inputs, not the engine).
    - **Loan account:** `get_loan_projection`@`amortization_engine.py:864` via
      `_compute_account_projections`@`savings_dashboard_service.py:362-373` **vs** the
      `_get_account_balance_map` dispatch inside `_compute_net_worth`@
      `year_end_summary_service.py:689` (`_build_account_data`@`:750`) **vs** the dashboard label
      `params.current_principal` at `loan/dashboard.html:104` (Q-11). Three bases for one loan's
      balance.
    - **Investment account:** `growth_engine.project_balance` via
      `_compute_account_projections`@`savings_dashboard_service.py:294` **vs** the investment-branch
      of `_get_account_balance_map` (W-163..W-166 / W-151) **vs** route:`investment.dashboard`@
      `investment.py:63`. W-159 requires the Dec 31 net-worth investment balance to equal the Dec 31
      savings-progress balance; verify.

---

## Concept: period_subtotal

- **Intended definition.** Two competing definitions exist; this is a flagged discrepancy, not
  reconciled here.
  - **Definition 1 (grid display detail).** The per-pay-period income / expense / net totals shown
    on the grid subtotal row, computed inline by iterating the period's transactions and
    accumulating `txn.effective_amount` (Projected-only). Source: route `grid.index`@
    `grid.py:263-279`; Q-10 / A-10 (`09_open_questions.md`).
  - **Definition 2 (balance-calculator internal subtotal).** The `(income, expenses)` pair the
    balance engine accumulates per period to roll the running balance: Projected-only, income via
    `effective_amount`, **expense via `_entry_aware_amount`** (subtracts cleared entry debits).
    Source: `_sum_remaining`@`balance_calculator.py:389-419`, `_sum_all`@
    `balance_calculator.py:422-451`.
  - **Definition 3 (per-domain analytics totals).** Variance/calendar/trends/obligations each
    compute a "period total" with their own status and type filters (settled-only for spending
    comparison; expense-only abs for trends; monthly-equivalent for obligations Q-12). Sources:
    `_compute_spending_by_category`@`year_end_summary_service.py:414`, `compute_variance`@
    `budget_variance_service.py:99`, `compute_trends`@`spending_trend_service.py:97`,
    `compute_committed_monthly`@`savings_goal_service.py:287`.
  **Discrepancy (do not reconcile -- Phase 3 input):** A-10 establishes that no service-level
  `period_subtotal` function exists; Definition 1 (grid, `effective_amount` expense) and Definition
  2 (balance calc, `_entry_aware_amount` expense) **disagree by construction** for a Projected
  envelope expense with cleared entries -- the grid subtotal row can differ from a running balance
  derived from the same transactions on the same page. Per A-02/W-091/W-092 the Projected-only
  exclusion IS applied by both Definition 1 and Definition 2 (verified: `grid.py:263-279` filters
  Projected; `_sum_remaining`/`_sum_all` gate `status_id != projected_id`), so the discrepancy is
  the expense formula, not the settled-source exclusion.
- **Units / type.** Decimal money (2dp); a `{income, expense, net}` triple per period.
- **Producer sites.**
  - route-inline: `grid.index`@`grid.py:263-279` (Definition 1; the user-facing subtotal);
    `obligations.summary`@`obligations.py:331-408` (Q-12 monthly-equivalent aggregation).
  - svc internal: `_sum_remaining`@`balance_calculator.py:389`, `_sum_all`@
    `balance_calculator.py:422` (Definition 2; not surfaced as a standalone subtotal -- consumed by
    `calculate_balances`).
  - svc per-domain: `_compute_spending_by_category`@`year_end_summary_service.py:414`;
    `compute_variance`@`budget_variance_service.py:99`; `compute_trends`@
    `spending_trend_service.py:97`; `compute_committed_monthly`@`savings_goal_service.py:287`;
    `_compute_spending_comparison`@`dashboard_service.py` (Q-10, referenced in 1.2.x).
  - **P1-f reconciliation.** Raw 1.7.3 lists `get_month_detail`@`calendar_service.py:88` and
    `get_year_overview`@`calendar_service.py:136` as producers -- both are calendar delegates ->
    CONSUMERS. `budget_variance_service._compute_actual`@`:381` (P1-f relocation) is a CONSUMER;
    the genuine variance producer is `compute_variance`@`:99` (KEEP per 1.7.8).
- **Consumer sites.** route:`index`@`grid.py:164`; route:`page`@`dashboard.py:40`
  (spending_comparison); route:`summary`@`obligations.py:259`; route:`variance_tab`@
  `analytics.py:205`; route:`trends_tab`@`analytics.py:272`; route:`calendar_tab`@
  `analytics.py:107`. Templates: `grid/grid.html` (196,269,280), `obligations/summary.html`
  (multiple), `analytics/_calendar_year.html`, `analytics/_calendar_month.html`,
  `dashboard/_spending_comparison.html` (7,11), `analytics/_variance.html`. JS:
  `chart_variance.js` (diff @69).
- **Primary path.** `PRIMARY PATH: UNKNOWN`. The codebase has no single owner; the intended
  resolution is the open question Q-10 (`09_open_questions.md:371-419`), which already asks exactly
  this ("Subtotal is a display detail of the grid" vs "Subtotal is a shared financial concept").
  No new question is filed -- Q-10 is the governing question; this entry cross-links it. Phase 3
  must record the Definition-1-vs-Definition-2 divergence regardless of the developer's choice
  (A-10).
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-1. Exact pairs:
    - `grid.index`@`grid.py:263-279` (effective_amount expense) **vs** `_sum_remaining`/`_sum_all`@
      `balance_calculator.py:389,422` (`_entry_aware_amount`@`balance_calculator.py:292` expense),
      same `(period, Projected, envelope-with-cleared-entries)` inputs.
    - `grid.index`@`grid.py:263-279` **vs** `_compute_spending_comparison`@`dashboard_service.py`
      (Q-10: opposite status filter -- settled-only -- cannot agree by construction; record as
      DEFINITION_DRIFT candidate).
    - `obligations.summary`@`obligations.py:331-408` monthly-equivalent **vs**
      `compute_committed_monthly`@`savings_goal_service.py:287` (Q-12: expired-template handling
      differs).

---

## Concept: chart_balance_series

- **Intended definition.** The ordered array of per-time-point balances a chart renders on its
  x-axis (parallel to `chart_date_labels`), produced server-side by the domain engine that owns the
  chart: amortization schedule for loan payoff/refinance charts, growth engine for investment/
  retirement charts, balance calculator (with interest) for HYSA charts, debt-strategy projection
  for the debt-strategy chart. Per coding-standards E-17, the series is computed in Python and JS
  treats it as display-only. Source: producer docstrings in `amortization_engine.py`,
  `growth_engine.py`, `debt_strategy_service.py`; `00_priors.md` E-17.
- **Units / type.** Ordered series of Decimal money values (rendered as JSON/data-attr numeric
  arrays); paired with a `chart_date_labels` string series.
- **Producer sites.** svc:`generate_schedule`@`amortization_engine.py:326`;
  svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`project_balance`@
  `growth_engine.py:164`; svc:`reverse_project_balance`@`growth_engine.py:297`;
  svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`calculate_balances_with_interest`@
  `balance_calculator.py:112`. All are LOW-RISK calculation engines (1.7.8 partition); none was a
  P1-f relocation -- producer list matches raw 1.7.3.
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`;
  route:`dashboard`@`investment.py:63`; route:`growth_chart`@`investment.py:363`;
  route:`calculate`@`debt_strategy.py:295`; route:`gap_analysis`@`retirement.py:301`;
  route:`balance_row`@`grid.py:393`. Templates: `loan/_schedule.html`, `loan/dashboard.html`,
  `loan/_payoff_results.html`, `loan/_refinance_results.html`, `investment/dashboard.html`,
  `investment/_growth_chart.html` (3-6), `debt_strategy/_results.html`,
  `retirement/_gap_analysis.html` (80-85). JS: `chart_year_end.js`, `payoff_chart.js`,
  `growth_chart.js`, `retirement_gap_chart.js`, `debt_strategy.js`.
- **Primary path.** Per chart domain (structurally clear, no single cross-domain owner because each
  chart renders a different quantity): loan charts -> `amortization_engine` (`generate_schedule`@
  `:326` / `get_loan_projection`@`:864`); investment/retirement charts -> `growth_engine`
  (`project_balance`@`:164` / `reverse_project_balance`@`:297`); HYSA charts ->
  `calculate_balances_with_interest`@`balance_calculator.py:112`; debt-strategy chart ->
  `calculate_strategy`@`debt_strategy_service.py:521`. The per-domain split is the intended design
  (each engine owns its chart); not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2. Phase 3 must verify (a) each chart's
  rendered series equals the server-computed series at the same time points (no JS recomputation;
  E-17), and (b) where a chart and a non-chart page show the same domain's balance (e.g. loan
  `chart_balance_series` from `get_loan_projection`@`amortization_engine.py:864` vs the loan
  dashboard `loan_principal_*` card), the time-aligned values agree. Specific pair:
  `chart_balance_series` for a checking/HYSA account via `calculate_balances_with_interest`@
  `balance_calculator.py:112` (consumed by `grid.balance_row`@`grid.py:393`) **vs**
  `projected_end_balance` from the same engine consumed by `accounts.checking_detail`@
  `accounts.py:1425` -- same engine, verify same inputs.

---

## Concept: net_worth

- **Intended definition.** Total assets minus total liabilities at 12 monthly endpoints across a
  calendar year: for each month, the last pay period ending on or before the month's last day is
  found and all account balances at that period are summed, with liability (loan) accounts
  contributing negative values. Per-account balances use the balance calculator for
  checking/savings, the interest calculator for HYSA, the amortization schedule for loans, and the
  growth engine for investments (when `ctx` is supplied). Source: `_compute_net_worth`@
  `year_end_summary_service.py:697-721` docstring (read and verified); `00_priors.md` W-151/W-152
  (net_worth_amort plan: investment accounts must include assumed return + employer contributions;
  net worth and savings progress must use identical calculation paths).
- **Units / type.** Decimal money (2dp); a dict `{monthly_values: [12 x {month, month_name,
  balance}], jan1, dec31, delta}`.
- **Producer sites.** svc:`_compute_net_worth`@`year_end_summary_service.py:689` -- sole producer
  (genuine aggregator: `_sum_net_worth_at_period`, `delta = dec31 - jan1`@`:746`). Delegates
  per-account balance derivation to `_build_account_data`@`:750` ->
  `_get_account_balance_map` (W-153/W-154/W-163..W-168 net_worth_amort plan; the SECOND independent
  per-account dispatch -- see `account_balance`). Def-line `:689` verified by Read; P1-b verified
  year_end def-lines (380/414/475/636/824) are exact, and `:689` is consistent.
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Template: `analytics/_year_end.html`
  (`data.net_worth.*` section). Tokens `year_summary_jan1_balance`,
  `year_summary_dec31_balance` are facets of this same producer.
- **Primary path.** `_compute_net_worth`@`year_end_summary_service.py:689` -- it is the sole
  producer of the `net_worth` token, so the token-level primary path is unambiguous. Caveat: its
  *inputs* (`_get_account_balance_map`) constitute a second per-account dispatch that the
  net_worth_amort plan (W-152) intends to make identical to the savings-progress / account-
  projection path; that reconciliation is `planned-per-plan` (NOT done). The token's canonical
  producer is clear; the cross-path consistency of its inputs is the Phase 3 / Q-15 concern.
- **Multi-path flag: PHASE 3 REQUIRED (PLAN_DRIFT axis).** Single producer for the token, but
  net_worth_amort W-152/W-159/W-366 require net worth and savings progress to use identical
  calculation paths and W-159 requires the Dec 31 net-worth investment balance to equal the Dec 31
  savings-progress balance. Pairs:
    - `_get_account_balance_map` (investment branch, W-163..W-166) inside
      `_compute_net_worth`@`year_end_summary_service.py:689` **vs**
      `_compute_savings_progress`@`year_end_summary_service.py:887` (the `savings_total` Dec 31
      figure) -- W-159 equality.
    - `_get_account_balance_map` (loan branch) **vs**
      `_compute_account_projections`@`savings_dashboard_service.py:294` loan branch -- same loan,
      two dispatchers (cross-link Q-15).

---

## Concept: savings_total

- **Intended definition.** The aggregate balance of the user's savings/investment accounts used as
  the "current savings" figure on the savings dashboard, the retirement-gap analysis, and the
  year-end savings-progress section. Per-account balances follow the same type dispatch as
  `account_balance` (balance calculator for cash savings/HYSA, growth engine for investment
  accounts including assumed return and employer contributions). Source:
  `compute_dashboard_data`@`savings_dashboard_service.py:61` (savings dashboard assembler);
  `_compute_savings_progress`@`year_end_summary_service.py:887` (year-end);
  `compute_gap_data`@`retirement_dashboard_service.py:79` (retirement gap); net_worth_amort W-159
  (Dec 31 savings-progress balance must equal Dec 31 net-worth investment balance, both via growth
  engine).
- **Units / type.** Decimal money (2dp); a single aggregate (with a per-account breakdown on the
  savings dashboard).
- **Producer sites.**
  - svc:`_compute_savings_progress`@`year_end_summary_service.py:887` -- genuine year-end producer
    (def-line not in P1-b's explicit verified list 380/414/475/636/824; caveat 1.7.6(2) says
    spot-check before quoting a body citation -- def-line treated as reliable, body cites flagged
    for Phase 3 re-verify).
  - svc:`compute_gap_data`@`retirement_dashboard_service.py:79` -- retirement-gap savings figure.
  - svc:`_compute_account_projections`@`savings_dashboard_service.py:294` -- the per-account
    balances the savings dashboard aggregates (the genuine arithmetic producer for the dashboard
    flavor).
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_dashboard_data`@
    `savings_dashboard_service.py:61` as a producer; it is an **assembler/orchestrator** ("assembles"
    excluded as never-classified-arithmetic per 1.7.8 operational definition) -> CONSUMER-side
    aggregator that delegates to `_compute_account_projections`@`:294` and
    `savings_goal_service`/`emergency_fund` helpers. Recorded as a reconciliation: the genuine
    producer is `_compute_account_projections`@`:294`, not `compute_dashboard_data`@`:61`.
- **Consumer sites.** route:`dashboard`@`savings.py:107`; route:`dashboard`@`retirement.py:46`;
  route:`gap_analysis`@`retirement.py:301`; route:`year_end_tab`@`analytics.py:171`. Templates:
  `savings/dashboard.html` (317), `retirement/_gap_analysis.html` (35,41),
  `analytics/_year_end.html`.
- **Primary path.** Per account type the canonical per-account producer is the same as
  `account_balance` (`calculate_balances_with_interest`/`calculate_balances` for cash savings/HYSA;
  `growth_engine.project_balance`@`growth_engine.py:164` for investment accounts). The
  cross-account `savings_total` aggregate has no single canonical owner -- three independent
  aggregators (`savings_dashboard` via `_compute_account_projections`@`:294`,
  `retirement_dashboard_service.compute_gap_data`@`:79`, `year_end._compute_savings_progress`@
  `:887`). Same root cause as `account_balance` (roadmap v5 Stage A pending). **See Q-15.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2. Pairs:
    - `_compute_account_projections`@`savings_dashboard_service.py:294` (savings dashboard
      aggregate) **vs** `compute_gap_data`@`retirement_dashboard_service.py:79` (retirement-gap
      savings) **vs** `_compute_savings_progress`@`year_end_summary_service.py:887` (year-end Dec
      31), same user, same as-of date.
    - W-159 equality: `_compute_savings_progress`@`year_end_summary_service.py:887` Dec 31 **vs**
      the investment branch of `_compute_net_worth`@`year_end_summary_service.py:689` Dec 31.

---

## Concept: debt_total

- **Intended definition.** The aggregate outstanding debt across the user's active loan accounts,
  shown as the debt-summary widget on the dashboard, the debt card on `/savings`, and the
  debt-strategy page; also the liability contribution to net worth. The per-loan base is **not
  consistently defined**: `_compute_debt_summary`@`savings_dashboard_service.py:802` sums the
  stored `LoanParams.current_principal` (`principal = Decimal(str(lp.current_principal))`@`:840`,
  `total_debt += principal`@`:855`; paid-off and `principal <= 0` loans excluded @`:836-843`),
  whereas `_compute_net_worth`@`year_end_summary_service.py:689` derives loan balances from the
  amortization schedule (negative liabilities). Per A-04 these differ for fixed-rate loans with
  confirmed payments (stored column vs engine-walked). Source: `_compute_debt_summary`@
  `savings_dashboard_service.py:806-824` docstring (read and verified); `_compute_net_worth`@
  `year_end_summary_service.py:697-721` docstring; A-04 (`09_open_questions.md`). This base
  inconsistency is a flagged definition discrepancy (not reconciled here).
- **Units / type.** Decimal money (2dp); `_compute_debt_summary` returns
  `{total_debt, total_monthly_payments, weighted_avg_rate, projected_debt_free_date}`.
- **Producer sites.**
  - svc:`_compute_debt_summary`@`savings_dashboard_service.py:802` -- genuine producer (sums stored
    `current_principal`; `+=`/`*`/`/` @`:855-863`; verified KEEP per 1.7.8).
  - svc:`_compute_debt_progress`@`year_end_summary_service.py:824` -- genuine producer
    (`principal_paid = jan1_bal - dec31_bal`@`:871`; 1.7.8 notes a Signature-column drift, line
    reliable).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` -- debt-strategy aggregate.
  - svc:`_compute_net_worth`@`year_end_summary_service.py:689` -- liability contribution
    (amortization-derived base).
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_dashboard_data`@`dashboard_service.py:40` and
    `compute_dashboard_data`@`savings_dashboard_service.py:61` as producers; both are
    assemblers/orchestrators that delegate to `_compute_debt_summary`@`:802` -> CONSUMER-side
    aggregators, not genuine `debt_total` producers.
- **Consumer sites.** route:`page`@`dashboard.py:40`; route:`dashboard`@`savings.py:107`;
  route:`dashboard`@`debt_strategy.py:275`. Templates: `dashboard/_debt_summary.html` (5),
  `savings/dashboard.html` (54), `debt_strategy/dashboard.html`. (DTI numerator overlaps -- see
  `dti_ratio`, P2-b/-c family; Q-12 mortgage double-count risk cross-reference.)
- **Primary path.** `PRIMARY PATH: UNKNOWN`. The codebase does not designate a canonical base for
  the displayed aggregate debt: `_compute_debt_summary`@`savings_dashboard_service.py:802` uses the
  stored `LoanParams.current_principal`, while `_compute_net_worth`@
  `year_end_summary_service.py:689` uses the amortization-schedule-real balance for the same loan
  accounts; A-04 establishes these differ for fixed-rate loans with confirmed payments. No
  docstring, roadmap statement, or A-0x answer names which base the user-facing `debt_total` should
  use. New question **Q-15** filed (`09_open_questions.md`), cross-linked here.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (6 producers, 3 routes, 3 templates). Pairs:
    - `_compute_debt_summary`@`savings_dashboard_service.py:802` (`total_debt` = sum of stored
      `current_principal`) **vs** the loan-liability sum inside `_compute_net_worth`@
      `year_end_summary_service.py:689` (amortization-derived) -- same loans, different base (A-04).
    - `_compute_debt_summary`@`savings_dashboard_service.py:802` (dashboard + `/savings` card)
      **vs** `calculate_strategy`@`debt_strategy_service.py:521` (`/debt-strategy`) -- compare the
      per-loan principal each uses for the same loan-on-date.
    - Internal-consistency note for Phase 3: `_compute_debt_summary` reads `ad["loan_params"]` and
      `ad["monthly_payment"]` from `_compute_account_projections` output (where `ad`'s
      `current_balance` = `proj.current_balance`@`savings_dashboard_service.py:373`, the A-04
      engine value) but uses `lp.current_principal`@`:840` for `total_debt` -- two different
      principals inside one service for one loan; flag regardless of Q-15's answer.

---

## Scope of this session (P2-b): loan / debt family

Tokens cataloged here: `monthly_payment`, `loan_principal_real`, `loan_principal_stored`,
`loan_principal_displayed`, `principal_paid_per_period`, `interest_paid_per_period`,
`escrow_per_period`, `total_interest`, `interest_saved`, `months_saved`, `payoff_date`,
`loan_remaining_months`, `dti_ratio` (debt side only).

Inventory 1.7.2 vocabulary-addition review: the only P1-b/P1-c addition in the loan/debt family is
`loan_remaining_months` (P1-b, `amortization_engine.py:128-176`). Folded in and cataloged below as
its own concept (it has its own consumer render sites distinct from `payoff_date`, per 1.7.2). The
other 1.7.2 additions are out-of-family and NOT cataloged here: `cash_runway_days` (cash-flow;
P2-a explicitly defers it to the `emergency_fund_*` session), `pension_benefit_annual` /
`pension_benefit_monthly` / `paycheck_breakdown` / `chart_date_labels` /
`transfer_amount_computed` (retirement / income / chart / transfer families -> P2-c/P2-d).
`entry_sum_total` / `entry_remaining` are entry-tracking tokens (Q-08 family), not loan/debt.

Cross-family seam (flagged for P2-reconcile): `dti_ratio` divides a debt-side numerator by an
income-side denominator (`paycheck_gross`-derived `gross_monthly`). P2-b catalogs ONLY the debt
side and the division site; the income producer is P2-c's. The seam is at
`savings_dashboard_service.py:168-176` (`gross_biweekly = params["salary_gross_biweekly"]`).

monthly_payment call-site count (explicit, per session demand): the inventory 1.7.3/1.7.4 states
**14** `calculate_monthly_payment` call sites (A-05's 8 + Q-09's "6"). **P2-b's source-read count
is 16 call sites + 1 definition (17 references), NOT 14.** This is recorded as a finding and
cross-linked to Q-09 (already filed; no duplicate raised). The 16 = 12 in section 1.1
(`amortization_engine.py:436,440,491,512,693,697,952,957`; `balance_calculator.py:225,231`;
`loan_payment_service.py:251,256`) + 4 in section 1.2 (`routes/loan.py:1102,1225,1231`;
`routes/debt_strategy.py:127`). The inventory's "14" undercounts by two: it omits
`routes/debt_strategy.py:127` entirely and Q-09's prose enumerates 7 fallback sites while its
header says "six". The auditor answer **A-09 (proposed, pending developer confirmation, in
`09_open_questions.md`) already establishes 16**; P2-b's count agrees with A-09, not with the
stale "14" crystallized into 1.7.3/1.7.4. Phase 3 must use the 16-site set in the
`monthly_payment` entry below, not the inventory's 14.

### QC note (P2-b verification)

Six producer-list entries spot-checked by Read against the cited `file:line` (>= the required 5):

1. `calculate_monthly_payment`@`amortization_engine.py:178` -- CONFIRMED producer (annuity formula
   `principal * (monthly_rate * factor) / (factor - 1)`@196; quantize TWO_PLACES ROUND_HALF_UP
   @192/197 -- A-01-compliant, W-187/W-203 HOLDS at the producer).
2. `get_loan_projection`@`amortization_engine.py:864` -- CONFIRMED producer of
   `loan_principal_real` / `monthly_payment` / `payoff_date` / `total_interest`; A-04 dual policy
   verified at `:977-985` (ARM `cur_balance = current_principal`@978; fixed walks
   `reversed(schedule)` for last `is_confirmed.remaining_balance`@980-984, fallback
   `current_principal`).
3. `calculate_balances_with_amortization`@`balance_calculator.py:176` -- CONFIRMED producer of
   `principal_paid_per_period` (`principal_by_period`@211); ARM/fixed split @220-235 on
   `getattr(loan_params,"is_arm",False)`.
4. `compute_contractual_pi`@`loan_payment_service.py:233` -- CONFIRMED producer of
   `monthly_payment`; ARM (`params.is_arm`@250) vs fixed ELSE @256.
5. `_compute_debt_summary`@`savings_dashboard_service.py:802` -- CONFIRMED producer of
   `debt_total` (cross-ref) / `dti_ratio` debt-side numerator (`total_monthly`@856 = sum of
   `monthly_pi + monthly_escrow`@851; PITI escrow inclusion W-297/W-246 confirmed @848-853).
6. `_compute_real_principal`@`debt_strategy.py:147` + `minimum_payment`@`debt_strategy.py:127` --
   CONFIRMED producer of `loan_principal_real` / `monthly_payment`; ARM returns stored
   `current_principal`@172-173 (W-060/A-04), fixed replays `get_payment_history` confirmed
   payments@175-184.

Reconciliation (P1-f 1.7.8 operational rule applied): `dashboard_service._get_debt_summary`@`533`
is delegate-only (`return savings_dashboard_service.compute_dashboard_data(...)["debt_summary"]`
@542-543) -> CONSUMER, NOT a `dti_ratio`/`debt_total` producer; the 1.7.3 index attribution of
`compute_dashboard_data@dashboard_service.py:40 (DTI quantize @172,176)` is a citation-drift
(the `@172,176` quantize lines are in `savings_dashboard_service.py`, not `dashboard_service.py`)
and an over-attribution -- corrected in the `dti_ratio` entry below.

---

## Concept: monthly_payment

- **Intended definition.** The fixed monthly principal-and-interest amount that amortizes a loan's
  balance to zero over its remaining term: the standard annuity formula
  `M = P * [r(1+r)^n] / [(1+r)^n - 1]`, `r = annual_rate/12`, `n = remaining_months`; `$0` when
  `principal <= 0` or `n <= 0`; `principal/n` for zero-rate loans. Source: docstring + body
  `amortization_engine.py:178-197` (read and verified). Plan definitions agree: W-203
  (`req_v3_addendum 4.24.3`, same formula), W-187 (`prod_readiness_v1 WU-06`: result must be
  Decimal quantized TWO_PLACES ROUND_HALF_UP -- satisfied at `:192/197`). **Which inputs** the
  formula receives is governed by the resolved answers, not by a single definition: A-05
  (developer + verification, `09_open_questions.md`) establishes the `arm_anchor` anchor-reset
  method is current -- every ARM site computes
  `calculate_monthly_payment(current_principal, current_rate, remaining_months)`, and the
  developer-reported fluctuation ($1911.54 / $1914.34 / $1912.94 / $1910.95, symptoms #2 and #4)
  is an **inconsistent-inputs** problem, not a method conflict. E-02 (`00_priors.md:166-170`):
  the payment must not change inside an ARM's fixed-rate window from any entry point; fluctuation
  by even a few cents is a finding. C-04 (`section5` engine-replay vs `arm_anchor` anchor-reset)
  is adjudicated by A-05: both descriptions are in the code and must produce the same number
  inside an ARM fixed-rate window; any divergence is a finding.
- **Units / type.** Decimal money (2dp), ROUND_HALF_UP. Scalar per loan-on-date (also embedded
  per-row in amortization schedules as the period payment).
- **Producer sites (aggregated across sections 1.1 and 1.2; 1.7.8-filtered).** One canonical
  formula `calculate_monthly_payment`@`amortization_engine.py:178` (definition), invoked from **16
  call sites**. Per-site `(principal, rate, remaining_months)` input provenance -- this table is
  the Phase 5 fluctuation hypothesis substrate:

  | # | Site | Enclosing fn | Branch / discriminator | principal | rate | remaining_months |
  |---|------|--------------|------------------------|-----------|------|------------------|
  | def | `amortization_engine.py:178` | -- | annuity formula; quantize @192/197 | arg | arg | arg |
  | 1 | `amortization_engine.py:436` | `generate_schedule`@326 | `using_contractual` TRUE = `original_principal is not None AND term_months is not None AND not has_rate_changes` (@430-434) | `original_principal` arg | `annual_rate` arg | `term_months` arg |
  | 2 | `amortization_engine.py:440` | `generate_schedule`@326 | ELSE (ARM / re-amort) | `current_principal` arg | `annual_rate` arg | `remaining_months` arg |
  | 3 | `amortization_engine.py:491` | `generate_schedule`@326 | ARM anchor reset (`anchor_balance` set AND `pay_date > anchor_date`, @486-490) | `balance := anchor_balance` | `current_annual_rate` | `months_left = max_months-month_num+1` |
  | 4 | `amortization_engine.py:512` | `generate_schedule`@326 | ARM rate-change re-amort (`period_rate != current_annual_rate`, @502) | `balance` (running) | `current_annual_rate := period_rate` from `rate_schedule` | `months_left` |
  | 5 | `amortization_engine.py:693` | `calculate_summary`@649 | `original_principal is not None AND not has_rate_changes` (@692; note: does NOT also require `term_months is not None`, unlike site 1) | `original_principal` arg | `annual_rate` arg | `term_months` arg |
  | 6 | `amortization_engine.py:697` | `calculate_summary`@649 | ELSE | `current_principal` arg | `annual_rate` arg | `remaining_months` arg |
  | 7 | `amortization_engine.py:952` | `get_loan_projection`@864 | `is_arm AND remaining > 0` (@950) | `Decimal(str(params.current_principal))`@913 (STORED col, A-04) | `Decimal(str(params.interest_rate))`@914 (STORED col, NOT rate_history) | `calculate_remaining_months(params.origination_date, params.term_months)`@908 |
  | 8 | `amortization_engine.py:957` | `get_loan_projection`@864 | ELSE (fixed-rate OR fully-paid ARM `remaining <= 0` -- A-09 corner case) | `Decimal(str(params.original_principal))`@912 | `interest_rate`@914 (STORED) | `params.term_months` |
  | 9 | `balance_calculator.py:225` | `calculate_balances_with_amortization`@176 | `getattr(loan_params,"is_arm",False)` TRUE (@220) | `loan_params.current_principal`@226 (STORED) | `loan_params.interest_rate`@216 (STORED) | `calculate_remaining_months(loan_params.origination_date, loan_params.term_months)`@222 |
  | 10 | `balance_calculator.py:231` | `calculate_balances_with_amortization`@176 | ELSE | `loan_params.original_principal`@232 | `interest_rate`@216 (STORED) | `loan_params.term_months` |
  | 11 | `loan_payment_service.py:251` | `compute_contractual_pi`@233 | `params.is_arm` TRUE (@250) | `Decimal(str(params.current_principal))` (STORED) | `Decimal(str(params.interest_rate))` (STORED) | `calculate_remaining_months(params.origination_date, params.term_months)`@247 |
  | 12 | `loan_payment_service.py:256` | `compute_contractual_pi`@233 | ELSE | `Decimal(str(params.original_principal))` | `Decimal(str(params.interest_rate))` (STORED) | `params.term_months` |
  | 13 | `routes/loan.py:1102` | `refinance_calculate`@1027 | UNCONDITIONAL (refinance preview -- NEW-loan terms by design, A-09) | `refi_principal` = `data["new_principal"]` OR `current_real_principal + closing_costs` (`current_real_principal = proj.current_balance`@1087, A-04 dual) | `pct_to_decimal(data["new_rate"])` (form) | `data["new_term_months"]` (form) |
  | 14 | `routes/loan.py:1225` | `create_payment_transfer`@1170 | `params.is_arm` TRUE (@1221) | `Decimal(str(params.current_principal))` (STORED) | `Decimal(str(params.interest_rate))` (STORED) | `calculate_remaining_months`@1222 |
  | 15 | `routes/loan.py:1231` | `create_payment_transfer`@1170 | ELSE | `Decimal(str(params.original_principal))` | `Decimal(str(params.interest_rate))` (STORED) | `params.term_months` |
  | 16 | `routes/debt_strategy.py:127` | `_build_debt_accounts` helper (-> `dashboard`@275 / `calculate`@295) | UNCONDITIONAL ARM-style formula on EVERY loan, ARM or fixed (no `is_arm` branch) -- **16th site, missed by A-05 and Q-09; see A-09** | `real_principal` from `_compute_real_principal`@147: ARM -> stored `current_principal`@172-173; fixed -> confirmed-payment-replay last `is_confirmed.remaining_balance`@175-184, fallback stored `current_principal` | `Decimal(str(params.interest_rate))`@110 (STORED) | `calculate_remaining_months(params.origination_date, params.term_months)`@111 |

  Provenance synthesis for Phase 5 (symptom #2/#4): `remaining_months` is identical across every
  site for the same loan-on-date by construction -- every ARM site calls
  `calculate_remaining_months(origination_date, term_months)` with default `as_of=today`, a
  calendar-elapsed formula (`amortization_engine.py:128-142`) independent of how many payments
  were confirmed. Therefore the fluctuation is NOT `remaining_months`. The two divergent axes
  are: (a) **principal** -- stored `LoanParams.current_principal` (sites 7,9,11,14) vs
  `proj.current_balance` (site 13 prefill, A-04 dual) vs confirmed-payment-replayed
  `real_principal` (site 16, fixed-rate) vs `original_principal` (sites 1,5,8,10,12,15); (b)
  **rate** -- stored `LoanParams.interest_rate` (sites 7-12,14-16) vs the `rate_schedule`-resolved
  `period_rate` (sites 3,4 inside `generate_schedule`, fire only when `anchor_balance`/
  `rate_changes` are passed). The displayed loan-dashboard "Monthly P&I" is `summary.monthly_payment`
  from site 7 (stored current_principal + stored interest_rate + calendar remaining) -- stable
  across calls -- but a schedule generated WITH `rate_changes` carries per-row payments re-amortized
  at site 4 using the rate-history period rate, so the schedule and the summary disagree for the
  same ARM. The reported $1910.95 is the post-manual-edit value (symptom #2: editing
  `current_principal` on `/accounts/3/loan` changes the input to site 7). This per-site table is
  sufficient for Phase 5 to build the hypothesis tree without re-reading the engine.
- **Consumer sites.** route:`dashboard`@`loan.py:405` (`summary.monthly_payment` via
  `get_loan_projection`); route:`payoff_calculate`@`loan.py:860`;
  route:`refinance_calculate`@`loan.py:1027`; route:`create_payment_transfer`@`loan.py:1170`;
  route:`dashboard`@`debt_strategy.py:275`; route:`calculate`@`debt_strategy.py:295`;
  route:`summary`@`obligations.py:259` (Q-12 monthly-equivalent aggregation);
  route:`dashboard`@`savings.py:107` (via `_compute_debt_summary` `monthly_pi`). Templates:
  `loan/dashboard.html` (129 "Monthly P&I"), `loan/_schedule.html` (55, 94),
  `loan/_payment_breakdown.html` (22), `loan/_escrow_list.html` (8),
  `loan/_payoff_results.html` (72), `loan/_refinance_results.html` (23, 24),
  `debt_strategy/dashboard.html` (minimum_payment), `debt_strategy/_results.html`,
  `dashboard/_debt_summary.html` (9, aggregate), `obligations/summary.html` (51, 159, aggregate).
- **Primary path.** `amortization_engine.calculate_monthly_payment`@`amortization_engine.py:178`
  is the single canonical formula (no competing implementation; W-203 names it). Per the P2-a
  analytical frame this is "one canonical computation consumed with divergent inputs," NOT
  competing engines. For the user-facing per-loan-on-date value the canonical producer is
  `get_loan_projection().summary.monthly_payment`@`amortization_engine.py:864` (site 7/8) -- the
  path the loan dashboard, `/savings` (via `_compute_account_projections` ->
  `_compute_debt_summary`), and the payoff calculator consume. Justification: A-05 (developer,
  resolved) names `calculate_monthly_payment(current_principal, current_rate, remaining_months)`
  per W-048 as the current ARM method and identifies the eight primary call sites as the invariant
  set; `get_loan_projection` is the single entry point that the display layer routes through.
  `routes/debt_strategy.py:127` and the in-`generate_schedule` sites (3,4) are the predicted
  drift sites (aggregation/display layer per the P1-f frame), not the canonical producer.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-1; symptom #2/#4 anchor; E-02 invariant.
  Phase 3 must verify all **16** sites (not the inventory's 14) receive the same
  `(principal, rate, remaining_months)` triple for the same loan-on-date. Exact pairs:
    - **ARM stable-window invariant (E-02):** sites 7 (`amortization_engine.py:952`), 9
      (`balance_calculator.py:225`), 11 (`loan_payment_service.py:251`), 14 (`loan.py:1225`) --
      all four must yield byte-identical Decimal for the same ARM on the same day (all read
      STORED `current_principal` + STORED `interest_rate` + calendar `remaining`; verify no
      `is_arm`-vs-`using_contractual` discriminator mismatch routes one through a fixed branch).
    - **Method-consistency (C-04 / A-05):** site 4 (`amortization_engine.py:512`, rate-history
      period rate inside the schedule) **vs** site 7 (`:952`, stored interest_rate in the
      summary) for the same ARM with `rate_changes` passed -- inside the fixed-rate window these
      must be equal; A-05 says any divergence is a finding.
    - **Fixed-rate partially-paid divergence:** site 16 (`debt_strategy.py:127`,
      `(real_principal_reduced, rate, remaining)`) **vs** site 8 (`:957`,
      `(original_principal, rate, term_months)`) -- for a partially-paid FIXED loan site 16
      produces a strictly lower payment than every contractual site; A-09 flags
      `debt_strategy.py:127` intent as unclear -> cross-link **Q-09** (already filed; no
      duplicate).
    - **Discriminator-type seam:** sites 1-2 / 5-6 (`generate_schedule` /`calculate_summary`,
      caller-state `using_contractual` discriminator) vs sites 7-16 (`is_arm` column
      discriminator). A fixed-rate loan whose caller omits `original_principal`/`term_months`
      silently routes through the ARM branch (A-09 concern 1). Phase 3 audits every direct
      caller of `generate_schedule`/`calculate_summary`.
  Cross-link: Q-09 (`09_open_questions.md`, fallback-branch guarantees + the 16-site count
  finding), C-04 (`00_priors.md:643-646`), A-05.

---

## Concept: loan_principal_real

- **Intended definition.** The true current outstanding balance of a loan as of today, reflecting
  actual confirmed payments. **A-04 dual policy (resolved; the governing definition):** for ARM
  loans the real principal IS the stored `LoanParams.current_principal` directly (the
  user-verified anchor; replaying origination-forward is mathematically wrong without complete
  rate history -- `arm_anchor` 3F / W-060); for fixed-rate loans it is derived by walking the
  amortization schedule generated from origination with confirmed `PaymentRecord`s and taking the
  last `is_confirmed` row's `remaining_balance` (`section5` replay -- W-222). Source:
  `LoanProjection` docstring `amortization_engine.py:848-861` (read); A-04 (developer +
  verification, `09_open_questions.md`); body `amortization_engine.py:977-985` (read: ARM
  `cur_balance = current_principal`@978; fixed loops `reversed(schedule)`, first `is_confirmed`
  -> `cur_balance = row.remaining_balance`@980-984, fallback `current_principal`). E-03
  (`00_priors.md:172-176`): when a transfer to a debt account settles, the real loan principal
  must reflect the principal portion. Documentation-drift note (A-04 verification): the docstring
  at `:855` says fixed-rate balance is "derived from the schedule by walking to today's date" but
  the code walks for the last `is_confirmed` row (inline comment `:971-976` confirms this is
  deliberate); not a logic bug.
- **Units / type.** Decimal money (2dp) per loan, as-of today.
- **Producer sites.**
  - svc:`get_loan_projection`@`amortization_engine.py:864` -- canonical:
    `LoanProjection.current_balance`@990; ARM=stored `current_principal`@978,
    fixed=confirmed-payment-walked@980-984.
  - svc:`_compute_real_principal`@`debt_strategy.py:147` -- the **only producer that explicitly
    replays confirmed/settled shadow income** (`get_payment_history(account_id, scenario_id)`@175
    -> `generate_schedule` from origination@181); ARM short-circuits to stored
    `current_principal`@172-173; fixed fallback to stored when no confirmed payments@176-177.
  - svc:`generate_schedule`@`amortization_engine.py:326` -- produces the per-row
    `remaining_balance` series that `get_loan_projection`/`_compute_real_principal` walk.
  - svc:`_compute_account_projections`@`savings_dashboard_service.py:294` --
    `current_bal = proj.current_balance`@373 (delegates to `get_loan_projection`; genuine
    consumer-of-engine but it is the `/savings` per-account `loan_principal_real` surface).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` -- consumes
    `DebtAccount.current_principal` (= `real_principal` from site above) as the per-loan starting
    balance for the strategy walk.
  - **P1-f reconciliation.** Raw 1.7.3 also lists
    `calculate_balances_with_amortization`@`balance_calculator.py:176` and
    `_compute_debt_progress`@`year_end_summary_service.py:824`. `calculate_balances_with_amortization`
    derives a *per-period* principal trajectory (`principal_by_period`@211), not the scalar
    real-principal-as-of-today -- it is the `principal_paid_per_period` producer; listed here only
    as a consumer of the anchor. `_compute_debt_progress` reads jan1/dec31 schedule balances
    (`_balance_from_schedule_at_date`); it consumes the schedule, does not define real principal.
- **Symptom #3 setup (the update path -- mandatory per session demand).** **No producer
  recomputes the STORED `LoanParams.current_principal` from confirmed/settled shadow transactions.**
  The only writer of the stored column is the manual `update_params`@`loan.py:634` route
  (`setattr(params, field, value)`@674, `"current_principal"` in the allowed-field list @669) --
  verified by `grep`: zero `\.current_principal\s*=` assignments anywhere in `app/`; the only
  occurrences are constructor kwargs for engine inputs and the setattr form-bind. Real-principal
  RECOMPUTATION from confirmed payments exists ONLY for fixed-rate loans (engine schedule walk at
  `amortization_engine.py:980-984`; explicit replay at `debt_strategy.py:175-184`). For ARM loans
  every path returns the stored column unchanged (A-04 / W-060). Consequence Phase 5 must trace:
  for an ARM mortgage (the developer's symptom-#3 account is "the mortgage account"), confirmed
  transfers into the loan account do NOT reduce any displayed principal until the user manually
  edits `current_principal`; for a fixed-rate loan the engine-walked `proj.current_balance`
  reflects payments but is NOT what the loan dashboard renders (see `loan_principal_displayed`).
  The settle path that creates the shadow income (`transaction_service.settle_from_entries` /
  transfer mark-done) writes no principal-update side effect.
- **Consumer sites.** route:`dashboard`@`loan.py:405` (`proj` computed @429 but the card renders
  the stored column -- see `loan_principal_displayed`); route:`payoff_calculate`@`loan.py:860`
  (`real_principal = committed_proj.current_balance`@998); route:`refinance_calculate`@`loan.py:1027`
  (`current_real_principal = proj.current_balance`@1087, Q-11); route:`dashboard`@`debt_strategy.py:275`;
  route:`dashboard`@`savings.py:107` (via `_compute_account_projections`). Templates:
  `loan/_schedule.html` (70, per-row `remaining_balance`); `loan/_refinance_results.html` (69-70);
  `debt_strategy/dashboard.html`; `debt_strategy/_results.html`. (`loan/dashboard.html:104` renders
  the STORED column, cataloged under `loan_principal_displayed`.)
- **Primary path.** `get_loan_projection().current_balance`@`amortization_engine.py:864` (A-04
  dual). Justification: A-04 is the resolved developer answer designating this the canonical
  real-principal surface; `_compute_real_principal`@`debt_strategy.py:147` is a parallel
  reimplementation of the same A-04 policy for the debt-strategy page (predicted drift site per
  the P1-f frame -- it must agree with `get_loan_projection` for the same loan-on-date).
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (6 producers, 5 routes, 5 templates).
  A-04 anchor; symptom #3 + symptom #5 anchor. Exact pairs:
    - `get_loan_projection.current_balance`@`amortization_engine.py:980-984` (fixed-rate
      schedule walk) **vs** `_compute_real_principal`@`debt_strategy.py:175-184` (independent
      confirmed-payment replay) -- same fixed loan, two replay implementations.
    - `get_loan_projection.current_balance`@`amortization_engine.py:978` (ARM = stored) **vs**
      the stored `LoanParams.current_principal` rendered at `loan/dashboard.html:104` -- coincide
      for ARM, diverge for fixed-with-confirmed-payments (Q-11 / `loan_principal_displayed`).
    - cross-link **Q-15** (`09_open_questions.md`): the savings-dashboard
      (`_compute_account_projections` -> `proj.current_balance`) vs year-end
      (`_get_account_balance_map`, amortization-real) per-account dispatch -- which is canonical
      for the aggregate. Already filed by P2-a; cross-linked, not duplicated.

---

## Concept: loan_principal_stored

- **Intended definition.** The persisted `LoanParams.current_principal` column
  (`loan_params.py:54`, `NUMERIC(12,2) NOT NULL`, `CHECK current_principal >= 0`@32) and its
  sibling `LoanParams.original_principal`@53 -- the user-entered loan balances. Per A-04:
  `current_principal` is **AUTHORITATIVE** for ARM loans (the user-verified anchor the engine
  snaps to) and **CACHED-for-display** for fixed-rate loans (the engine-walked balance is the
  real value; the column is a stale-able mirror). Source: model `loan_params.py:53-56` (cited);
  A-04 (`09_open_questions.md`); W-002 (`account_param_arch`: LoanParams must track
  original/current principal). E-14: NUMERIC(12,2) fixes stored precision at 2dp.
- **Units / type.** Decimal money (2dp), stored DB column.
- **Producer sites (1.5 model + the engine that consumes it as authoritative for ARM).**
  - model:`LoanParams.current_principal`@`loan_params.py:54`;
    `LoanParams.original_principal`@`loan_params.py:53` -- the stored values.
  - write path: route:`update_params`@`loan.py:634` (`setattr`@674; only writer -- see
    `loan_principal_real` symptom-#3 note).
  - svc:`get_loan_projection`@`amortization_engine.py:864` -- reads it as the ARM anchor
    (`anchor_bal = current_principal if is_arm`@926; `cur_balance = current_principal`@978) per
    A-04; this is the "stored is authoritative for ARM" producer surface.
  - svc:`load_loan_context`@`loan_payment_service.py:78` and
    `compute_contractual_pi`@`loan_payment_service.py:233` -- read stored
    current_principal/original_principal for ARM P&I.
  - **P1-f reconciliation.** Raw 1.7.3 lists `_compute_interest_for_year`@`year_end:1207` and
    `_balance_from_schedule_at_date`@`year_end:1465-1469` as producers. Per caveat 1.7.6(2) /
    1.7.8 the `:1465-1469` ARM-anchor row is mis-attributed -- the enclosing def is
    `_generate_debt_schedules`@1421, and the row is a *type-normalize + conditional anchor read*
    (`Decimal(str(params.current_principal)) if params.is_arm else None`), i.e. a CONSUMER of the
    stored column per A-04, not a producer. Corrected attribution used here:
    `_generate_debt_schedules`@`year_end_summary_service.py:1421` consumes stored
    `current_principal` as the ARM anchor; it does not derive it.
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`update_params`@`loan.py:631`;
  route:`refinance_calculate`@`loan.py:1027`; route:`create_payment_transfer`@`loan.py:1170`
  (ARM branch @1225-1227). Templates: `loan/dashboard.html` (99 "Original Principal", 104
  "Current Principal" -- the prominent card value); `loan/_refinance_results.html`;
  `loan/_rate_history.html`.
- **Primary path.** model:`LoanParams.current_principal`@`loan_params.py:54` is the stored
  source of truth, authoritative for ARM (A-04). For fixed-rate loans A-04 makes the
  engine-walked value canonical and this column merely CACHED -- so the token-level primary path
  is the column itself, with the A-04 caveat that "stored is authoritative" holds only for ARM.
  Justified by A-04; no UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (6 producers, 4 routes, 3 templates).
  Phase 4 (source-of-truth) owns the AUTHORITATIVE/CACHED classification; Phase 3 pair:
  `LoanParams.current_principal` (stored, rendered `loan/dashboard.html:104`) **vs**
  `get_loan_projection.current_balance`@`amortization_engine.py:980-984` (fixed-rate
  engine-walked) -- the SOURCE_DRIFT candidate for partially-paid fixed loans. Cross-link Q-11,
  Q-15, A-04.

---

## Concept: loan_principal_displayed

- **Intended definition.** The principal figure actually rendered to the user on a
  principal-display page. Audit-plan Appendix A reserves this token for "any third
  principal-display variant"; 1.7.2 records it as an **orphan** (defined at vocab line 33, zero
  body uses) and notes Phase 2 may collapse it into `loan_principal_real` or
  `loan_principal_stored` per the A-04 resolution. **P2-b resolution: `loan_principal_displayed`
  is NOT collapsed; it is materially distinct and load-bearing for symptom #3/#5.** The displayed
  principal is, on the primary loan page, the STORED column, NOT the engine-real value -- a
  divergence the orphan token is the right place to record. Source: A-04; Q-11/A-11
  (`09_open_questions.md`); template read below.
- **Units / type.** Decimal money (2dp) as rendered (Jinja `"{:,.2f}".format(... |float)`).
- **The six principal-display pages (enumerated with file:line per session demand; caveat
  1.7.6#4 / the `loan_principal_*` rows of the 1.7.3 index).** Each is a place a user reads a
  loan principal; the "Source" column states which producer the rendered value comes from:

  | # | Page / template:line | Route | Source of the rendered principal |
  |---|----------------------|-------|----------------------------------|
  | 1 | `loan/dashboard.html:104` ("Current Principal" card, bold accent) | `loan.dashboard`@`loan.py:405` (passes `params=params`@557; `proj` computed @429 but NOT passed) | **STORED** `params.current_principal` (`loan_principal_stored`) -- NOT `proj.current_balance`. Confirmed by read: template line 104 = `params.current_principal|float`; render_template @553-575 passes no `current_balance` var. |
  | 2 | `loan/dashboard.html:99` ("Original Principal") | same | STORED `params.original_principal` |
  | 3 | `loan/_schedule.html:70` (per-row remaining balance) | `loan.dashboard` / `loan.payoff_calculate` | engine schedule rows (`AmortizationRow.remaining_balance` from `generate_schedule`) = `loan_principal_real` trajectory |
  | 4 | `loan/_refinance_results.html:69-70` (current real principal before refi) | `loan.refinance_calculate`@`loan.py:1027` | `current_real_principal = proj.current_balance`@1087 = `loan_principal_real` (A-04 dual) |
  | 5 | `debt_strategy/dashboard.html` (per-account principal) | `debt_strategy.dashboard`@`debt_strategy.py:275` | `DebtAccount.current_principal` = `real_principal` from `_compute_real_principal`@147 = `loan_principal_real` |
  | 6 | `debt_strategy/_results.html` (per-debt starting balance / payoff) | `debt_strategy.calculate`@`debt_strategy.py:295` | `calculate_strategy`@`debt_strategy_service.py:521` starting from `real_principal` = `loan_principal_real` |

  Plus `savings/dashboard.html` (debt card) and `dashboard/_debt_summary.html` render the
  aggregate `debt_total` from `_compute_debt_summary` which sums STORED `current_principal`@840
  (cross-ref P2-a `debt_total`, Q-15) -- a 7th surface where the displayed aggregate uses the
  stored base while page 4/5/6 use the engine-real base.
- **Producer sites.** None of its own (orphan; it is a *render selection* of either
  `loan_principal_stored` or `loan_principal_real`). Recorded as a CONSUMER-side concept whose
  value is whichever producer the page wires in (table above).
- **Consumer sites.** The six pages above + the two aggregate surfaces; all in the table.
- **Primary path.** `PRIMARY PATH: UNKNOWN` -- the codebase does not designate which principal a
  user-facing page MUST display. Page 1 (the most prominent: the bold "Current Principal" card on
  `/accounts/<id>/loan`) shows the STORED column while pages 4-6 show the engine-real value; for a
  partially-paid fixed-rate loan these differ, and A-04 does not state which the *display* layer
  must use (A-04 governs which is *correct* internally, not which the page shows). This is exactly
  the question **Q-11** already asks (refinance prefill vs the dashboard card for the same
  loan-on-date; `09_open_questions.md` Q-11/A-11). No new question filed -- Q-11 is the governing
  question; this entry cross-links it and supplies the six-page map A-11 referenced. Phase 3
  records the page-1-vs-page-4 divergence regardless of Q-11's resolution.
- **Multi-path flag: PHASE 3 REQUIRED.** Exact pairs: `loan/dashboard.html:104`
  (STORED `params.current_principal`, route `loan.dashboard`@`loan.py:557`) **vs**
  `loan/_refinance_results.html:69-70` (`proj.current_balance`@`loan.py:1087`) **vs**
  `debt_strategy/dashboard.html` (`_compute_real_principal`@`debt_strategy.py:147`) -- three
  surfaces, same loan-on-date, must reconcile. Cross-link Q-11, A-04, Q-15.

---

## Concept: principal_paid_per_period

- **Intended definition.** The portion of a period's loan payment that reduces principal (payment
  minus interest, plus any extra), attributed per pay period. In the amortization schedule:
  `principal_portion = monthly_payment - interest + extra_payment`, capped so the balance never
  goes below zero (W-204 `req_v3_addendum 4.24.3`; W-219/W-236 `section5` overpayment cap). In
  the balance calculator's debt path: only the principal portion of a detected shadow-income
  payment reduces the loan balance (`principal_by_period`). Source: `generate_schedule` body
  `amortization_engine.py` (read: per-row `principal` quantized ROUND_HALF_UP @602);
  `calculate_balances_with_amortization` docstring `balance_calculator.py:180-201` (read:
  "Only the principal portion ... reduces the balance"); W-204, W-219, W-295.
- **Units / type.** Decimal money (2dp), ROUND_HALF_UP, per pay period (and per schedule row).
- **Producer sites.**
  - svc:`generate_schedule`@`amortization_engine.py:326` -- per-row `principal`@602 (canonical
    P&I split).
  - svc:`calculate_balances_with_amortization`@`balance_calculator.py:176` -- `principal_by_period`
    dict@211, the per-pay-period principal reduction applied to a debt account's running balance.
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` -- per-debt principal applied per
    period in the snowball/avalanche walk.
  - svc:`_compute_debt_progress`@`year_end_summary_service.py:824` -- `principal_paid = jan1_bal
    - dec31_bal`@871 (W-295/W-363; this is the *year aggregate*, a coarser facet of the same
    concept -- flag the granularity difference for Phase 3, not a separate token).
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`;
  route:`year_end_tab`@`analytics.py:171`. Templates: `loan/_schedule.html` (55, 56);
  `loan/_payment_breakdown.html` (51); `analytics/_year_end.html` (year_summary_principal_paid).
- **Primary path.** `generate_schedule`@`amortization_engine.py:326` per-row `principal` is the
  canonical P&I split; `calculate_balances_with_amortization.principal_by_period` and
  `_compute_debt_progress` are consumers/aggregators of the schedule the engine produces.
  Justified: the engine owns the amortization split; the balance-calculator debt path explicitly
  delegates payment-to-principal attribution to the engine's formula (docstring
  `balance_calculator.py:184-185`). Not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (4 producers, 3 routes, 4 templates).
  Pairs: `generate_schedule`@`amortization_engine.py:326` per-row principal **vs**
  `calculate_balances_with_amortization`@`balance_calculator.py:176` `principal_by_period`
  (same loan -- the balance path uses the ARM/fixed-discriminated monthly payment from sites
  9/10 above; if that payment diverges from the schedule's, the per-period principal diverges)
  **vs** `_compute_debt_progress`@`year_end_summary_service.py:824` jan1-dec31 delta (W-295;
  must equal the sum of per-period principal for the year -- A-06 escrow-preprocessing applies to
  the schedule this delta reads).

---

## Concept: interest_paid_per_period

- **Intended definition.** The interest portion of a period's loan payment:
  `interest = remaining_balance * (annual_rate/12)`, quantized 2dp ROUND_HALF_UP, per amortization
  row / pay period (W-204 `req_v3_addendum 4.24.3`; W-300 `test_remediation`). Source:
  `generate_schedule` body `amortization_engine.py:517` (read:
  `interest = (balance * monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)`); W-204, W-300.
- **Units / type.** Decimal money (2dp), ROUND_HALF_UP, per period / row.
- **Producer sites.**
  - svc:`generate_schedule`@`amortization_engine.py:326` -- per-row `interest`@517 (canonical).
  - svc:`get_loan_projection`@`amortization_engine.py:864` -- exposes the schedule whose rows
    carry interest; `_derive_summary_metrics`@622 sums it (-> `total_interest`).
  - svc:`calculate_balances_with_amortization`@`balance_calculator.py:176` -- splits detected
    payments into interest vs principal using the same engine formula.
  - svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380` -- A-06 calendar-year
    aggregate (`total_interest += row.interest`@406 for rows whose `payment_date.year == year`),
    over a schedule built from **A-06-preprocessed** payments.
  - svc:`_compute_debt_progress`@`year_end_summary_service.py:824` -- year-end debt interest
    facet.
- **A-06 definition discrepancy (flagged, not reconciled).** Per A-06 (`09_open_questions.md`,
  resolved): the year-end mortgage-interest aggregate is correct ONLY when applied to a schedule
  generated from `loan_payment_service.prepare_payments_for_engine`-preprocessed payments
  (escrow subtraction `loan_payment_service.py:305-318` + biweekly-month redistribution
  `:321-351`). A producer that sums schedule `interest` WITHOUT that preprocessing computes a
  DIFFERENT (incomplete) number for biweekly-paid mortgages. `generate_schedule`'s raw per-row
  interest and `_compute_mortgage_interest`'s preprocessed sum are therefore the same formula
  over different payment inputs -- a DEFINITION/SCOPE seam Phase 3 must compare, governed by A-06
  (both layers apply; the simple sum is incomplete on its own). Two paths call `generate_schedule`
  WITHOUT preprocessing (A-06 verification): `savings_dashboard_service.py:471,488` and
  `routes/debt_strategy.py:175,181` -- they do not feed mortgage interest but could produce
  wrong schedules in their own contexts when escrow-inclusive payments are present.
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`;
  route:`year_end_tab`@`analytics.py:171`. Templates: `loan/_schedule.html` (57);
  `loan/_payment_breakdown.html` (56); `analytics/_year_end.html`.
- **Primary path.** `generate_schedule`@`amortization_engine.py:326` per-row `interest` is the
  canonical formula. The user-facing per-period interest is read off the schedule produced by
  `get_loan_projection`@`amortization_engine.py:864`; the year-end calendar aggregate is
  `_compute_mortgage_interest`@`year_end_summary_service.py:380` over A-06-preprocessed payments
  (a different scope of the same formula, governed by A-06 -- not a competing producer). Justified
  by A-06; not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (5 producers, 3 routes, 3 templates).
  A-06 anchor. Pairs: `generate_schedule`@`amortization_engine.py:517` raw per-row interest
  **vs** `_compute_mortgage_interest`@`year_end_summary_service.py:380` (A-06-preprocessed,
  calendar-year filtered) -- same loan-year, the schedule input differs (escrow-subtracted +
  biweekly-redistributed); Phase 3 verifies the dashboard schedule interest reconciles with the
  year-end figure only after the A-06 preprocessing is applied. Cross-link A-06, C-05
  (`00_priors.md:648-651`), W-362.

---

## Concept: escrow_per_period

- **Intended definition.** The monthly escrow amount (property tax + insurance + other components)
  added to P&I to form the full housing payment: `monthly_escrow = sum(component.annual_amount /
  12)` over active escrow components, with each component inflating independently as
  `projected_annual[year_n] = annual_amount * (1 + inflation_rate)^n` (W-201/W-202
  `req_v3_addendum 4.24.2`). Source: `escrow_calculator.py` function docstrings (calculate_monthly_escrow@14,
  calculate_total_payment@60, project_annual_escrow@79 -- per 1.7.3); W-201, W-202; A-06 ties
  escrow into the year-end interest pipeline (escrow must be SUBTRACTED from shadow transaction
  amounts before amortization -- `loan_payment_service.prepare_payments_for_engine` @305-318).
- **Units / type.** Decimal money (2dp) per month; component annual amounts NUMERIC(12,2).
- **Producer sites.**
  - svc:`calculate_monthly_escrow`@`escrow_calculator.py:14` -- canonical sum-of-(annual/12).
  - svc:`calculate_total_payment`@`escrow_calculator.py:60` -- `monthly_pi + monthly_escrow`
    (full payment; verified consumed at `savings_dashboard_service.py:850-851` and
    `loan.py:1241`).
  - svc:`project_annual_escrow`@`escrow_calculator.py:79` -- per-year inflated projection
    (W-202).
  - svc:`load_loan_context`@`loan_payment_service.py:78` -- assembles `monthly_escrow` into the
    loan context.
  - svc:`prepare_payments_for_engine`@`loan_payment_service.py:263` -- A-06 escrow SUBTRACTION
    preprocessing (@305-318); a producer of the *escrow-adjusted payment*, not of escrow itself,
    but the A-06-load-bearing site.
  - **P1-f reconciliation.** Raw 1.7.3 lists `_compute_mortgage_interest`@`year_end:380` as an
    `escrow_per_period` producer -- it CONSUMES preprocessed (escrow-subtracted) payments per
    A-06; reclassified CONSUMER here.
- **Consumer sites.** route:`dashboard`@`loan.py:405` (`monthly_escrow` @433-435);
  route:`add_escrow`@`loan.py:761`; route:`delete_escrow`@`loan.py:815`;
  route:`create_payment_transfer`@`loan.py:1170` (`calculate_total_payment`@1241);
  route:`dashboard`@`savings.py:107` (via `_compute_debt_summary` PITI). Templates:
  `loan/dashboard.html`; `loan/_schedule.html` (55, 59); `loan/_payment_breakdown.html` (62, 70);
  `loan/_escrow_list.html` (8, 16, 36-37); `loan/_refinance_results.html`.
- **Primary path.** `calculate_monthly_escrow`@`escrow_calculator.py:14` is the canonical
  monthly-escrow producer; `calculate_total_payment`@`:60` is the canonical P&I+escrow combiner.
  Single owner (`escrow_calculator`); justified by 1.7.3 single-service attribution + W-201/W-202.
  Not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (6 producers, 4 routes, 5 templates).
  A-06 anchor. Pairs: `calculate_monthly_escrow`@`escrow_calculator.py:14` (dashboard display
  escrow) **vs** the escrow SUBTRACTED inside `prepare_payments_for_engine`@`loan_payment_service.py:305-318`
  (the amount removed before amortization) -- Phase 3 verifies the dashboard's displayed monthly
  escrow equals the per-period escrow the year-end pipeline subtracts (A-06: a mismatch makes
  mortgage interest wrong). Also `loan/_escrow_list.html:37` does Jinja arithmetic on escrow
  (E-16 violation, flagged in 1.3) -- Phase 3 records the template-computation finding. Cross-link
  A-06, W-201, W-202, W-360.

---

## Concept: total_interest

- **Intended definition.** Two definitions exist; flagged discrepancy, governed by A-06, not
  reconciled here.
  - **Definition 1 (life-of-loan).** Sum of the `interest` column over every row of a
    full-life amortization schedule. Source: `_derive_summary_metrics`@`amortization_engine.py:642-644`
    (read: `total_interest = sum(row.interest for row in schedule)`); `calculate_summary`@649
    (`total_interest_standard = sum(r.interest for r in standard)`@715); W-045 (`arm_anchor`:
    derivable from a generated schedule without regenerating).
  - **Definition 2 (calendar-year mortgage interest).** Sum of `row.interest` for schedule rows
    whose `payment_date.year == year`, over a schedule built from A-06-preprocessed payments.
    Source: `_compute_mortgage_interest`@`year_end_summary_service.py:380` (`total_interest +=
    row.interest`@406); W-293 (`section8`); W-362 (`year_end_fixes`: requires escrow subtraction
    + biweekly redistribution). C-05 (`00_priors.md:648-651`) pairs W-293 vs W-362; A-06 resolves
    that both layers apply and the bare `section8` sum is incomplete on its own.
  Discrepancy: the loan dashboard's "Total Interest (life of loan)" (`loan/dashboard.html:139`,
  `summary.total_interest`) and the year-end tab's mortgage-interest figure are DIFFERENT
  quantities (life-of-loan vs one calendar year, raw vs A-06-preprocessed). Phase 3 input, not
  reconciled here.
- **Units / type.** Decimal money (2dp). Definition 1: life-of-loan scalar. Definition 2: per
  calendar year.
- **Producer sites.**
  - svc:`_derive_summary_metrics`@`amortization_engine.py:622` (Definition 1; sum@642-644).
  - svc:`calculate_summary`@`amortization_engine.py:649` (Definition 1; @715, also
    `interest_saved` via standard-vs-accelerated).
  - svc:`get_loan_projection`@`amortization_engine.py:864` (Definition 1; via
    `_derive_summary_metrics`).
  - svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380` (Definition 2; A-06).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` (Definition 1 flavor, summed across
    the strategy walk per debt).
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`;
  route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295`;
  route:`year_end_tab`@`analytics.py:171`. Templates: `loan/dashboard.html` (139);
  `loan/_schedule.html` (96, schedule_totals); `loan/_refinance_results.html` (37, 38);
  `debt_strategy/_results.html` (45-49).
- **Primary path.** Per definition: Definition 1 canonical producer is
  `_derive_summary_metrics`@`amortization_engine.py:622` (consumed via
  `get_loan_projection`/`calculate_summary`); Definition 2 canonical producer is
  `_compute_mortgage_interest`@`year_end_summary_service.py:380` over A-06-preprocessed payments.
  The two are different concepts sharing a token, both governed (A-06 + W-045); not UNKNOWN, but
  Phase 3 must keep them separate.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (5 producers, 5 routes, 4 templates).
  Pairs: `_derive_summary_metrics`@`amortization_engine.py:622` (life-of-loan) **vs**
  `_compute_mortgage_interest`@`year_end_summary_service.py:380` (calendar-year, A-06) -- record
  as DEFINITION_DRIFT-by-design unless a page conflates them; **vs**
  `calculate_strategy`@`debt_strategy_service.py:521` (strategy total) for the same loan. A-05's
  ARM-input divergence propagates: if `monthly_payment` inputs differ, schedule interest differs,
  so `total_interest` inherits the symptom #2/#4 risk. Cross-link A-06, C-05, W-293, W-362.

---

## Concept: interest_saved

- **Intended definition.** The reduction in total interest from an acceleration scenario:
  `interest_saved = total_interest_standard - total_interest_with_extra` (extra monthly principal)
  or the analogous saving from a refinance / debt-strategy. Source: `calculate_summary`@
  `amortization_engine.py:740` (read: `interest_saved = total_interest_standard -
  total_interest_extra`, quantized ROUND_HALF_UP @749); `calculate_strategy`@
  `debt_strategy_service.py:521` (snowball/avalanche interest saved vs minimum-only). Plan: W-243
  through W-245 (`section5` debt strategy); PA-27 (`test_audit`: extra-payment savings precision
  asserted only directionally -- a known test-gap, Phase 7).
- **Units / type.** Decimal money (2dp), ROUND_HALF_UP.
- **Producer sites.**
  - svc:`calculate_summary`@`amortization_engine.py:649` (`interest_saved`@740-749, standard vs
    accelerated schedule).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` (strategy interest saved).
- **Consumer sites.** route:`payoff_calculate`@`loan.py:860`;
  route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295`.
  Templates: `loan/_payoff_results.html` (19); `loan/_refinance_results.html` (41);
  `debt_strategy/_results.html` (80-95).
- **Primary path.** Per domain: amortization payoff/refinance saving ->
  `calculate_summary`@`amortization_engine.py:649`; multi-debt strategy saving ->
  `calculate_strategy`@`debt_strategy_service.py:521`. The two answer different questions
  (single-loan acceleration vs cross-debt strategy ordering); each is canonical for its page.
  Justified by 1.7.3 / W-243-245; not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (2 producers, 3 routes, 3 templates).
  Pair: `calculate_summary`@`amortization_engine.py:649` **vs**
  `calculate_strategy`@`debt_strategy_service.py:521` for the SAME single loan with the SAME
  extra payment (a one-debt strategy run must equal the loan payoff calculator's interest_saved);
  both inherit the `monthly_payment` input-divergence risk (A-05). Cross-link A-05.

---

## Concept: months_saved

- **Intended definition.** The number of months an acceleration scenario shaves off the payoff:
  `months_saved = len(standard_schedule) - len(accelerated_schedule)` (extra-payment case) or
  the time saved by a debt strategy. Source: `calculate_summary`@`amortization_engine.py:739`
  (read: `months_saved = len(standard) - len(accelerated)`); `calculate_strategy`@
  `debt_strategy_service.py:521`. Refinance reuses the slot as `break_even_months`
  (`loan.refinance_calculate`, W-242 `closing_costs / monthly_payment_savings`). Plan: W-242,
  W-243-245.
- **Units / type.** Integer months (non-negative).
- **Producer sites.**
  - svc:`generate_schedule`@`amortization_engine.py:326` (the two schedules whose lengths are
    differenced).
  - svc:`calculate_summary`@`amortization_engine.py:649` (`months_saved`@739).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` (strategy months saved).
- **Consumer sites.** route:`payoff_calculate`@`loan.py:860` (committed_months_saved);
  route:`refinance_calculate`@`loan.py:1027` (break_even_months -- a related but distinct
  derivation, W-242); route:`calculate`@`debt_strategy.py:295`. Templates:
  `loan/_payoff_results.html` (14, 29); `loan/_refinance_results.html` (90);
  `debt_strategy/_results.html`.
- **Primary path.** `calculate_summary`@`amortization_engine.py:649` for single-loan
  acceleration; `calculate_strategy`@`debt_strategy_service.py:521` for strategy. The refinance
  break-even (W-242) is a different formula sharing the render slot -- Phase 3 must not conflate
  it with schedule-length-difference months_saved. Justified by 1.7.3; not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 marks `months_saved` SINGLE-path (1 producer / 1
  consumer in the strict index), but P2-b finds >1 producer (amortization vs debt_strategy) AND a
  definitional fork (schedule-length-diff vs refinance break-even) -> escalated to PHASE 3
  REQUIRED. Pairs: `calculate_summary`@`amortization_engine.py:739` **vs**
  `calculate_strategy`@`debt_strategy_service.py:521` (same loan, same extra) **vs** the
  refinance `break_even_months` derivation @`loan.refinance_calculate`@`loan.py:1027` (W-242 --
  verify it is labeled distinctly from acceleration months_saved so the user is not misled).

---

## Concept: payoff_date

- **Intended definition.** The calendar date the loan reaches a zero balance: the
  `payment_date` of the last row of the (possibly payment-/rate-/anchor-adjusted) amortization
  schedule. Source: `_derive_summary_metrics`@`amortization_engine.py:645` (read:
  `payoff_date = schedule[-1].payment_date`, fallback `fallback_date` when empty@640-641);
  `calculate_summary`@716 (`payoff_date_standard = standard[-1].payment_date`); W-045
  (`arm_anchor`: derivable from generated schedule without regeneration). For debt strategy, the
  date each debt is retired under the chosen ordering (W-243-245).
- **Units / type.** `date` (calendar date), per loan / per scenario.
- **Producer sites.**
  - svc:`_derive_summary_metrics`@`amortization_engine.py:622` (canonical last-row date).
  - svc:`calculate_summary`@`amortization_engine.py:649` (standard + with-extra payoff dates).
  - svc:`get_loan_projection`@`amortization_engine.py:864` (via `_derive_summary_metrics`).
  - svc:`calculate_payoff_by_date`@`amortization_engine.py:753` (inverse: required extra to hit a
    target date -- consumes payoff logic; KEEP as producer of the achievable-date boundary).
  - svc:`calculate_strategy`@`debt_strategy_service.py:521` (per-debt payoff date under
    strategy).
- **Consumer sites.** route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`;
  route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295`.
  Templates: `loan/dashboard.html` (143); `loan/_payoff_results.html` (9);
  `loan/_refinance_results.html` (62-63); `debt_strategy/_results.html` (34-38). Also consumed by
  `section5` W-239 (auto-set recurring transfer template `end_date` to projected payoff) --
  cross-family seam with transfer recurrence (P2-d/transfer family).
- **Primary path.** `_derive_summary_metrics`@`amortization_engine.py:622` (consumed via
  `get_loan_projection`/`calculate_summary`) is canonical; `debt_strategy` produces a
  strategy-specific payoff date for its own page. The schedule the date is read from is built by
  `generate_schedule`, so payoff_date inherits whatever `monthly_payment`/principal/rate inputs
  that schedule used. Justified by 1.7.3 + W-045; not UNKNOWN.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (5 producers, 4 routes, 4 templates).
  Pairs: `_derive_summary_metrics`@`amortization_engine.py:622` (loan dashboard payoff) **vs**
  `calculate_strategy`@`debt_strategy_service.py:521` (debt-strategy payoff) for the same loan
  under the same payments. Inherits symptom #2/#4: divergent `monthly_payment` inputs shift the
  last-row date. W-239 seam: the auto-set transfer-template end_date must equal the displayed
  payoff_date -- Phase 3 verifies. Cross-link A-05, W-239.

---

## Concept: loan_remaining_months

- **Intended definition.** Integer count of payment months still owed on a loan:
  `max(0, term_months - months_elapsed)` where `months_elapsed` is calendar months from
  `origination_date` to `as_of` (default today). Source: `calculate_remaining_months`@
  `amortization_engine.py:128-142` (read and verified). Folded from 1.7.2 (P1-b addition,
  `amortization_engine.py:128-176`): distinct from `payoff_date` because consumers render the
  count separately and it is the `n` input to `calculate_monthly_payment` for every ARM site.
- **Units / type.** Integer months (non-negative).
- **Producer sites.**
  - svc:`calculate_remaining_months`@`amortization_engine.py:128` -- sole canonical producer
    (calendar-elapsed formula, payment-count-independent).
  - svc:`get_loan_projection`@`amortization_engine.py:864` -- exposes `remaining`@908 on
    `LoanProjection.remaining_months`.
  - svc:`compute_contractual_pi`@`loan_payment_service.py:233` -- calls `calculate_remaining_months`@247
    for the ARM P&I `n`.
- **Consumer sites.** route:`refinance_calculate`@`loan.py:1027` (current_remaining_months);
  route:`create_payment_transfer`@`loan.py:1170`. Templates: `loan/_refinance_results.html` (51);
  `debt_strategy/_results.html` (67-70, total_months).
- **Primary path.** `calculate_remaining_months`@`amortization_engine.py:128` -- single canonical
  producer; every other site calls it. Justified by 1.7.3 single-service attribution + the source
  read. Not UNKNOWN.
- **Multi-path flag: not multi-path (1.7.4 single-path).** Recorded for Phase 3 only as the
  shared `remaining_months` input that makes the `monthly_payment` invariant tractable: because
  every ARM site derives `n` from this one calendar formula with `as_of=today`, `n` is identical
  across sites for the same loan-on-date -- so any `monthly_payment` fluctuation is attributable
  to principal/rate, NOT to `n` (see `monthly_payment` provenance synthesis). No PHASE 3 pair of
  its own; cross-link `monthly_payment`.

---

## Concept: dti_ratio (debt side only)

- **Intended definition.** Debt-to-income ratio as a percentage:
  `dti = total_monthly_debt / gross_monthly_income * 100`, where the debt side is the sum across
  active loan accounts of `monthly_pi + monthly_escrow` (PITI), and the income side is
  `gross_biweekly * 26 / 12`. Source: `_compute_debt_summary`@`savings_dashboard_service.py:802-876`
  (read: `monthly_total = (monthly_pi + monthly_escrow).quantize(... ROUND_HALF_UP)`@851,
  `total_monthly += monthly_total`@856) and the division at
  `savings_dashboard_service.py:173-176` (read: `dti_ratio = (total_monthly_payments /
  gross_monthly * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)`@173-176); W-247
  (`section5 5.12-1`: `DTI = total_monthly_debt / gross_monthly_income`, `gross_monthly =
  gross_biweekly*26/12`); W-297 (`section8 OQ-2`: escrow MUST be included for PITI accounts --
  complete-per-plan); W-246 (total monthly debt = monthly P&I + monthly escrow across all loans).
  **Debt side only per session scope.**
- **Units / type.** Decimal percentage, 1dp (`Decimal("0.1")`), ROUND_HALF_UP. Debt-side
  numerator: Decimal money (2dp).
- **Producer sites (debt side).**
  - svc:`_compute_debt_summary`@`savings_dashboard_service.py:802` -- the debt-side numerator
    `total_monthly_payments`@856,873 (sum of per-loan `monthly_pi + monthly_escrow`); also
    `weighted_avg_rate`@857-867.
  - svc:`compute_dashboard_data`@`savings_dashboard_service.py:61` -- performs the DTI division
    @173-176 (the genuine `dti_ratio` scalar producer; debt numerator from `_compute_debt_summary`,
    income denominator from the cross-family seam).
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_dashboard_data@dashboard_service.py:40
    (DTI quantize @172,176)` as a producer. Verified by Read: `dashboard_service._get_debt_summary`@533
    is delegate-only (`return savings_dashboard_service.compute_dashboard_data(user_id)["debt_summary"]`
    @542-543) -> CONSUMER, not a producer (1.7.8 delegate-only rule). Additionally the cited
    `@172,176` quantize lines are physically in `savings_dashboard_service.py` (verified:
    `:172` gross_monthly quantize, `:176` dti_ratio quantize), not `dashboard_service.py:40` --
    a caveat-1.7.6(2)-class citation drift. Corrected: the sole `dti_ratio` producer path is
    `savings_dashboard_service` (`_compute_debt_summary`@802 + division @173-176); the dashboard
    widget consumes it unchanged.
- **Consumer sites.** route:`page`@`dashboard.py:40` (via delegate); route:`dashboard`@`savings.py:107`.
  Templates: `dashboard/_debt_summary.html` (15); `savings/dashboard.html` (54-65, debt summary
  card).
- **Primary path.** `savings_dashboard_service` is the canonical and sole `dti_ratio` path:
  debt-side numerator `_compute_debt_summary`@`savings_dashboard_service.py:802`, division at
  `savings_dashboard_service.py:173-176`. The dashboard widget delegates (no independent
  producer). Justified by the Read-verified delegation; not UNKNOWN. **Caveat (cross-link
  P2-a `debt_total` / Q-15):** `_compute_debt_summary` sums STORED `lp.current_principal`@840 for
  `total_debt` but uses `ad["monthly_payment"]`@846 (engine-derived via
  `_compute_account_projections`) for the DTI numerator -- two principal bases inside one service;
  the DTI numerator itself is monthly-payment-based (consistent), but the co-displayed
  `total_debt` on the same card uses the stored base. Flag regardless of Q-15.
- **Cross-family seam (flagged for P2-reconcile).** The income denominator `gross_monthly =
  gross_biweekly * 26 / 12`@`savings_dashboard_service.py:170-172` derives from
  `params["salary_gross_biweekly"]` -- the `paycheck_gross` concept owned by **P2-c**. P2-b does
  NOT catalog the income producer. P2-reconcile must stitch the `dti_ratio` debt side (here) to
  P2-c's `paycheck_gross` producer and verify the `26/12` biweekly-to-monthly factor (duplicated
  inline here and at `savings_dashboard_service.py:765`, and as named constants in
  `savings_goal_service.py:17-18`, per P2-a Q-12) is numerically consistent across consumers.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (2 producers, 2 routes, 2 templates).
  Pairs: `dashboard/_debt_summary.html` DTI (via `dashboard_service._get_debt_summary`@533
  delegate) **vs** `savings/dashboard.html` DTI (direct
  `compute_dashboard_data`@`savings_dashboard_service.py:61`) for the same user -- must be
  byte-identical since one delegates to the other; Phase 3 confirms the delegation introduces no
  divergence (e.g. differing user-scoping or caching). Also the Q-12 mortgage double-count risk
  (a mortgage counted in both `/obligations` total_expense_monthly AND the DTI numerator) --
  cross-link Q-12 (`09_open_questions.md`, already filed). Cross-link Q-15, Q-12, W-247, W-297.

---

<!-- P2-b (loan/debt) ends here. P2-c (income/tax/paycheck) begins below. -->

## Scope of this session (P2-c): income / paycheck / tax family

Tokens cataloged here (8): `paycheck_gross`, `paycheck_net`, `taxable_income`, `federal_tax`,
`state_tax`, `fica`, `pre_tax_deduction`, `post_tax_deduction`.

Inventory 1.7.2 vocabulary-addition review (fold decision recorded per session demand): the only
1.7.2 addition in the income/tax family is **`paycheck_breakdown`** (P1-c, defined at 1.7.2 as the
`PaycheckBreakdown` dataclass produced by `calculate_paycheck`@`paycheck_calculator.py:92` /
`project_salary`@`:250`, "single-token shorthand for the bundle `paycheck_gross + paycheck_net +
federal_tax + state_tax + fica + pre_tax_deduction + post_tax_deduction +
employer_contribution`"). **Folded, not given a standalone section:** `paycheck_breakdown` is not a
distinct computed quantity -- it is the container dataclass whose fields ARE the eight tokens
cataloged in this session (its `employer_contribution` field is the retirement family and is
deferred to P2-d). Its producer, primary path, and rounding behavior are identical to
`paycheck_net`'s (`calculate_paycheck`@`paycheck_calculator.py:92`); cataloging the eight component
tokens fully covers it. The `paycheck_breakdown` row in 1.7.3 (3 producers, 6 routes, 5 templates)
is therefore the union of the eight per-token producer/consumer lists below; P2-reconcile should
treat it as an alias, not a 9th concept. No other 1.7.2 token is in the income/tax family
(`pension_benefit_*` -> P2-d retirement; `cash_runway_days`/`entry_*` -> other families;
`chart_date_labels`/`transfer_amount_computed` -> chart/transfer).

dti_ratio income-denominator seam (closed here -- see `paycheck_gross` below): P2-b deferred the
`dti_ratio` income denominator to P2-c (`02_concepts.md:1268-1274`, P2-b `dti_ratio` Cross-family
seam). The denominator is `paycheck_gross`; the seam is **closed for P2-reconcile** in the
`paycheck_gross` entry with the precise divergence characterized so reconcile need not re-derive.

Analytical-frame finding (one engine vs aggregation-layer recompute): the income/tax family is
**one canonical engine** (`paycheck_calculator.calculate_paycheck` orchestrating
`tax_calculator`/`calibration_service`) consumed with divergent inputs -- NOT scattered like
`monthly_payment`. The single material aggregation/display-layer recomputation is
`savings_dashboard_service.py:263-266` (`salary_gross_biweekly`), an off-engine `paycheck_gross`
that bypasses raises and uses banker's rounding; it is marked PHASE 3 REQUIRED per the frame even
though no symptom is income/tax. The year-end annual tax/income totals (`analytics.year_end_tab`)
are aggregation-layer consumers of per-period breakdowns and are marked PHASE 3 REQUIRED for the
PA-24 26-period-vs-annual reconciliation.

No new Q-NN raised by P2-c (highest remains Q-15). Existing cross-links: Q-13 (calibrate_preview
inline taxable / pre-tax base), Q-12 (26/12 factor duplication + mortgage double-count touching the
dti seam). Rationale for zero new questions: every income/tax primary path is determined (the
canonical engine is unambiguous); the divergences found (off-engine gross, calibration-path SS-cap
bypass, dead legacy `calculate_federal_tax`) are governed Phase 3/Phase 8 findings with clear
intended behavior, not "what is intended" ambiguities.

### QC note (P2-c verification, 5 producer spot-checks by Read at source)

1. `calculate_paycheck`@`paycheck_calculator.py:92` -- CONFIRMED producer of all 8 tokens: gross
   @133-135, taxable @155-157, bracket federal @184-195, state de-annualize @199-204, FICA @206-214,
   net @223-231.
2. `_apply_raises`@`paycheck_calculator.py:274` -- CONFIRMED `paycheck_gross` sub-producer (sorted
   raise application @294-324, terminal `.quantize(TWO_PLACES, ROUND_HALF_UP)` @326).
3. `calculate_federal_withholding`@`tax_calculator.py:35` -- CONFIRMED `federal_tax` producer (Pub
   15-T pipeline @102-170; pre-tax subtract @112; std-ded @118; per-period @158-164).
4. `calculate_fica`@`tax_calculator.py:274` -- CONFIRMED `fica` producer; SS wage-base cap
   ENFORCED @300-306, Medicare base+surtax @309-318.
5. `apply_calibration`@`calibration_service.py:106` -- CONFIRMED calibrated `federal_tax`/
   `state_tax`/`fica` producer @132-145; NO SS wage-base cap (flat `gross*rate` @139-144).
Reconciliation reads: `derive_effective_rates`@`calibration_service.py:34` (CONSUMER of
`taxable_income` param @40,71; produces effective RATES, not amounts);
`calculate_federal_tax`@`tax_calculator.py:215` (legacy; `grep -rn calculate_federal_tax app/`
returns only the definition -- zero consumers; governed dead-code definition discrepancy);
`salary_gross_biweekly`@`savings_dashboard_service.py:263-266` (off-engine `paycheck_gross`
recompute -- dti seam).

Scope tally for P2-reconcile section-2 completeness gate: **0 of 8** P2-c concepts ended
`PRIMARY PATH: UNKNOWN`. All 8 have a justified canonical path (`calculate_paycheck` /
`tax_calculator` engine functions). 0 new Q-NN. dti_ratio income seam: CLOSED (recorded in
`paycheck_gross`).

---

## Concept: paycheck_gross

- **Intended definition.** Decimal-dollar gross wage for one biweekly pay period after raises and
  before deductions/taxes: `gross_biweekly = (_apply_raises(profile, period) /
  pay_periods_per_year).quantize(Decimal("0.01"), ROUND_HALF_UP)`
  (`paycheck_calculator.py:124-135`; `_apply_raises`@`:274-326`). The biweekly-rounding residue
  (sum of 26 quantized checks need not equal contract annual salary, bounded ~$0.13/yr, signed by
  the salary's fractional cent) is an accepted simplification documented in the module docstring
  (`paycheck_calculator.py:9-38`, F-127/PA-07). Source: inventory vocab line 73; CLAUDE.md domain
  concept "Paycheck Calculator (salary + raises - taxes - deductions)".
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP), per biweekly period. `_apply_raises`
  intermediate is post-raise ANNUAL salary (Decimal, 2dp). `project_salaries_by_year` produces
  annual (not biweekly) salary tuples.
- **Producer sites.**
  - svc:`calculate_paycheck`@`paycheck_calculator.py:92` -- CANONICAL biweekly gross @133-135.
  - svc:`_apply_raises`@`paycheck_calculator.py:274` -- post-raise annual salary @326; the
    raise-sequencing sub-engine (flat-before-percentage within an effective date, recurring
    compounding @303-316).
  - svc:`_apply_single_raise`@`paycheck_calculator.py:329` -- one raise (`salary*(1+pct)` or
    `salary + flat_amount`); genuine arithmetic (1.7.8 LOW-RISK spot-checked, KEEP).
  - svc:`_get_cumulative_wages`@`paycheck_calculator.py:480` -- re-derives per-period gross
    @499-501 (YTD sum for the FICA SS cap); internal `paycheck_gross` re-derivation.
  - svc:`project_salaries_by_year`@`pension_calculator.py:78` -- year-by-year annual salary via
    `_apply_raises`@`:109`; the docstring (`pension_calculator.py:81-82`) explicitly calls it a
    "simplified projection ... For full raise logic, use paycheck_calculator._apply_raises()" --
    a self-declared divergence from the canonical raise path.
  - **OFF-ENGINE producer (aggregation layer):** `savings_dashboard_service.py:263-266` computes
    `salary_gross_biweekly = (Decimal(str(active_profile.annual_salary)) /
    (active_profile.pay_periods_per_year or 26)).quantize(Decimal("0.01"))`. This is an
    independent recomputation that (a) reads RAW `annual_salary` -- NO `_apply_raises`; (b)
    quantizes with DEFAULT rounding (banker's `ROUND_HALF_EVEN`), an A-01 violation (this exact
    line is in the A-01 verification's 24-call violation list, `09_open_questions.md:47`). It is
    the `dti_ratio` income-denominator base and is also passed to
    `calculate_investment_inputs`@`:528-536`.
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_gap_data`@`retirement_dashboard_service.py:79`
    as a `paycheck_gross` producer. Inventory 1.1 row @1173 frames it "delegates" to
    `paycheck_calculator.project_salary` -> CONSUMER under the 1.7.8 delegate-only rule. Genuine
    `paycheck_gross` producers reduce to the `paycheck_calculator` functions + the
    `pension_calculator` annual flavor + the one off-engine `savings_dashboard_service` recompute.
- **Consumer sites.**
  - routes: `list_profiles`@`salary.py:102`, `breakdown`@`salary.py:960`,
    `projection`@`salary.py:1020`, `calibrate_preview`@`salary.py:1064` (Q-13),
    `calibrate_confirm`@`salary.py:1127`, `year_end_tab`@`analytics.py:171`,
    `gap_analysis`@`retirement.py:301`.
  - svc consumers (dti seam): `compute_dashboard_data`@`savings_dashboard_service.py:61`
    (`gross_monthly = gross_biweekly*26/12`@`:170-172`, then `dti_ratio`@`:173-176`);
    `calculate_investment_inputs`@`investment_projection.py:100` (via `salary_gross_biweekly`@`:535`).
  - templates: `salary/list.html` (48), `salary/breakdown.html` (64, 70),
    `salary/projection.html` (50, 56), `salary/calibrate_confirm.html` (35),
    `analytics/_year_end.html` (61).
  - models (input): `SalaryProfile.annual_salary`@`salary_profile.py:72`,
    `SalaryProfile.additional_income`@`salary_profile.py:88`,
    `SalaryRaise.flat_amount`@`salary_raise.py:111`,
    `CalibrationOverride.actual_gross_pay`@`calibration_override.py:80`.
- **Primary path.** `calculate_paycheck`@`paycheck_calculator.py:92` (canonical post-raise biweekly
  gross @133-135), with `_apply_raises`@`:274` as its raise-sequencing sub-engine. Justified: the
  module docstring names this the "Core Phase 2 service"; `PaycheckBreakdown.gross_biweekly` is the
  token every salary route renders; all salary-page routes delegate to it. The
  `savings_dashboard_service.py:263-266` `salary_gross_biweekly` is explicitly NON-canonical (an
  off-engine recompute that drops raises and changes rounding mode) -- it is a Phase 3 DIVERGE, not
  a competing primary. **PRIMARY PATH known (not UNKNOWN).**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (`paycheck_gross` -- 5 producers, 7 routes, 5
  templates; "raise sequencing is the largest variance source"). Exact producer pairs Phase 3 must
  compare for the same profile on the same date:
  - **Pair A (canonical vs off-engine dti base):** `calculate_paycheck`@`paycheck_calculator.py:133-135`
    (post-`_apply_raises`, ROUND_HALF_UP) **vs** `savings_dashboard_service.py:263-266` (raw
    `annual_salary`, NO raises, default banker's rounding). DIVERGES whenever the profile has any
    applicable raise OR the salary's fractional cent rounds differently -- classify
    DEFINITION_DRIFT (raise omission) + ROUNDING_DRIFT (mode). This is the dti_ratio income
    denominator; per the analytical frame the aggregation-layer site is PHASE 3 REQUIRED even if
    it momentarily agrees (no-raise profile).
  - **Pair B (canonical vs FICA-cap re-derivation):** `calculate_paycheck`@`:133-135` **vs**
    `_get_cumulative_wages`@`paycheck_calculator.py:499-501` -- both quantize
    `salary/pay_periods_per_year` with ROUND_HALF_UP; must produce identical per-period gross for
    the same period, else the SS wage-base cap (PA-21) tracks against a drifted YTD base.
  - **Pair C (canonical vs simplified annual projection):** `_apply_raises`@`paycheck_calculator.py:274`
    **vs** `pension_calculator.project_salaries_by_year`@`:78` (`_FakePeriod` at year-end,
    `_apply_raises`@`:109`); the docstring self-declares "simplified" -- verify the year-end
    salary equals the canonical post-raise salary for December periods.
- **dti_ratio seam -- CLOSED for P2-reconcile.** `paycheck_gross` IS the `dti_ratio` income
  denominator: `dti = total_monthly_debt / gross_monthly * 100` with `gross_monthly =
  gross_biweekly*26/12`@`savings_dashboard_service.py:170-172`, `gross_biweekly =
  params["salary_gross_biweekly"]`@`:168`. The producer of that base is the **off-engine**
  `savings_dashboard_service.py:263-266` recompute, **NOT** the canonical
  `calculate_paycheck`. Precise divergence (so P2-reconcile verifies without re-deriving): the dti
  income base (i) omits `_apply_raises` (uses raw `annual_salary`) and (ii) uses default
  `ROUND_HALF_EVEN` instead of `ROUND_HALF_UP`. Cross-link P2-b `dti_ratio` entry
  (`02_concepts.md:1224-1282`, Cross-family seam @`:1268-1274`) and P2-a Q-12 (the `26/12`
  biweekly-to-monthly factor is duplicated inline at `savings_dashboard_service.py:170-172` and
  `:765`, and as named constants `savings_goal_service.py:17-18`; numerically equal, not
  cross-imported). **Seam closed for P2-reconcile.**
- **Prior-audit linkage.** PA-07 (biweekly rounding residue/F-127: the canonical gross @133-135 IS
  the F-127 site; the off-engine @266 compounds PA-07 with an A-01 rounding-MODE divergence).
  PA-20 (no full-year gross/net-pay exact-sum test). PA-22 (`annual_salary=0` / negative /
  `pay_periods_per_year=0` -- `_apply_raises` and the `or 26` guard @`paycheck_calculator.py:132`,
  `:265`,`:489` are the untested edge sites). PA-24 (26-period gross vs annual reconciliation).

---

## Concept: paycheck_net

- **Intended definition.** Decimal-dollar take-home (net) wage for one biweekly pay period after
  all taxes and all deductions. Code decomposition (`paycheck_calculator.py:222-231`):
  `net_pay = (gross_biweekly - total_pre_tax - federal_biweekly - state_biweekly - ss_biweekly -
  medicare_biweekly - total_post_tax).quantize(Decimal("0.01"), ROUND_HALF_UP)`. Source: inventory
  vocab line 75; CLAUDE.md "Paycheck Calculator (salary + raises - taxes - deductions)".
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP), per biweekly period (single terminal
  quantize over already-quantized components).
- **Relationship invariant (verbatim, code's actual form -- Phase 3 and Phase 7 verify this).**
  The code implements, at `paycheck_calculator.py:223-231`:

  `net_pay = gross_biweekly - total_pre_tax - federal_tax - state_tax - social_security - medicare
  - total_post_tax`, then `.quantize(Decimal("0.01"), ROUND_HALF_UP)`.

  The audit-plan section-7 form is `net = gross - (federal + state + fica) - pre_tax - post_tax`.
  With `fica := social_security + medicare`, `taxes := federal + state + fica`, `deductions :=
  pre_tax + post_tax`, the code form is **exactly** the section-7 form -- NO grouping divergence;
  the only nuances are (a) `fica` appears as two line items (`social_security`, `medicare`) rather
  than one, and (b) there is a single terminal `quantize` over per-component values that were each
  already quantized upstream (the PA-07/PA-20 residue surface). Both the audit-plan-section-7 form
  and the code's actual form are recorded; they are semantically identical. Phase 7's
  `net = gross - taxes - deductions` test must expand `fica` into `social_security + medicare` and
  expect the terminal-quantize residue.
- **Producer sites.**
  - svc:`calculate_paycheck`@`paycheck_calculator.py:92` -- CANONICAL, net @223-231.
  - svc:`project_salary`@`paycheck_calculator.py:250` -- per-period list; delegates to
    `calculate_paycheck`@`:263` -> CONSUMER/delegate (not an independent net derivation).
  - **P1-f reconciliation.** Raw 1.7.3 lists `_get_transaction_amount`@`recurrence_engine.py:720`
    (reads salary-linked `breakdown.net_pay`) and `calculate_gap`@`retirement_gap_calculator.py:37`
    (`net_biweekly_pay` is an INPUT parameter @`:37`, not derived) as producers; both CONSUME a
    net value -> CONSUMERS under 1.7.8. `_get_net_biweekly_pay`@`savings_dashboard_service.py:568`
    calls `calculate_paycheck`@`:597` and returns `breakdown.net_pay`@`:600` -> delegate CONSUMER.
    Sole genuine producer: `calculate_paycheck`.
- **Consumer sites.**
  - routes: `list_profiles`@`salary.py:102` (`net_pay`@`:122-125`), `breakdown`@`salary.py:960`,
    `projection`@`salary.py:1020`, `create_profile`@`salary.py:149` (`init_breakdown.net_pay`@`:264`).
  - svc consumers: `retirement_gap_calculator.calculate_gap`@`:37` (net_biweekly input),
    `recurrence_engine._get_transaction_amount`@`:720` (salary-linked transaction amount),
    `savings_dashboard_service._get_net_biweekly_pay`@`:568` (goal-progress input).
  - templates: `salary/list.html` (51), `salary/projection.html` (64), `dashboard/_payday.html`
    (15), `retirement/_gap_analysis.html` (9).
- **Primary path.** `calculate_paycheck`@`paycheck_calculator.py:92` (net @223-231). Sole genuine
  producer; `project_salary` and every svc consumer delegate to or read it. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (`paycheck_net` -- 4 producers, 4 routes, 5
  templates; "end-of-pipeline"). Exact pairs:
  - `calculate_paycheck` net @`paycheck_calculator.py:223-231` **vs** the section-7 invariant
    recomputed from the displayed component tokens (`paycheck_gross` - `pre_tax_deduction` -
    `federal_tax` - `state_tax` - `fica` - `post_tax_deduction`): Phase 7 cross-concept assertion;
    expect equality up to the single-terminal-quantize residue.
  - bracket-tax net (`calibration=None`) **vs** calibrated net (`apply_calibration`@`calibration_service.py:106`
    feeding `federal/state/ss/medicare` into the same @223-231 formula) for the same profile --
    these net values DIFFER by design (calibration's purpose is to reflect the actual stub);
    Phase 3 records this as a labeled intentional difference, not drift.
  - `recurrence_engine._get_transaction_amount`@`:720` salary-linked net **vs** salary-page net
    for the same profile/period (same engine; verify identical inputs/tax_configs).
- **Prior-audit linkage.** PA-07 (single terminal quantize over per-component already-rounded
  values is the residue propagation path). PA-20 (this concept IS the "no full-year net-pay sum
  test" gap -- directly PA-20). PA-22 (zero/negative salary, `pay_periods_per_year=0` edge
  inputs). PA-24 (26-period net + withholding totals vs annual liability).

---

## Concept: taxable_income

- **Intended definition (multiple, by layer -- discrepancy flagged, not synthesized).** Income
  subject to income tax = gross minus pre-tax deductions (minus documented adjustments). The
  codebase has FOUR distinct "taxable" computations under this one token:
  1. **Display taxable (canonical token):** `taxable_biweekly = gross_biweekly - total_pre_tax`,
     floored at 0 (`paycheck_calculator.py:155-157`); surfaced as
     `PaycheckBreakdown.taxable_income`@`:69,238`. Subtracts pre-tax only. Matches the definition.
  2. **Federal-engine internal taxable:** `calculate_federal_withholding` ->
     `adjusted_income = annual_income - pre_tax_deductions - additional_deductions`
     (`tax_calculator.py:112`), then `taxable_income = adjusted_income - standard_deduction`
     (`:118`), annualized, floored 0. Subtracts pre-tax AND standard deduction AND W-4 4(b) -- a
     DIFFERENT quantity, internal to `federal_tax`, NOT the displayed token.
  3. **State-engine internal taxable:** `calculate_state_tax` -> `taxable = annual_gross - std_ded`
     (`tax_calculator.py:263`); caller passes `taxable_biweekly*pp` (`paycheck_calculator.py:200`)
     so pre-tax is already removed upstream, then state std-ded removed.
  4. **Legacy `calculate_federal_tax`@`tax_calculator.py:215`:** `taxable = annual_gross -
     standard_deduction` (`:233`) -- does NOT subtract `pre_tax_deduction`. **DEFINITION
     DISCREPANCY (governed, not reconciled per session demand).** Mitigant: `grep -rn
     calculate_federal_tax app/` returns only the definition -- ZERO consumers; dead code.
  Sources: inventory vocab line 77; the four cited code sites; tax_calculator module docstring
  (`tax_calculator.py:7-14`, Pub 15-T steps).
- **Units / type.** Display token: Decimal money (2dp), per biweekly period, floored at 0.
  Engine-internal flavors: annualized Decimal, floored at 0.
- **Producer sites.**
  - svc:`calculate_paycheck`@`paycheck_calculator.py:92` -- CANONICAL display token @155-157.
  - svc:`calculate_federal_withholding`@`tax_calculator.py:35` -- federal-internal taxable
    @112-120 (annualized, std-ded).
  - svc:`calculate_state_tax`@`tax_calculator.py:240` -- state-internal taxable @263.
  - svc:`calculate_federal_tax`@`tax_calculator.py:215` -- legacy @233 (no pre-tax); dead.
  - **P1-f reconciliation.** Raw 1.7.3 lists `_apply_marginal_brackets`@`tax_calculator.py:173`
    and `derive_effective_rates`@`calibration_service.py:34` as `taxable_income` producers. Both
    RECEIVE `taxable_income` as a parameter (`tax_calculator.py:173`,@`:181`;
    `calibration_service.py:40,71`) and consume it (`_apply_marginal_brackets` walks brackets;
    `derive_effective_rates` divides `federal/taxable`@`:83`). Neither derives taxable income ->
    CONSUMERS under 1.7.8 (`_apply_marginal_brackets` is a `federal_tax`-intermediate producer;
    `derive_effective_rates` is an effective-rate producer). Reconciled: genuine `taxable_income`
    producers are the four listed above.
- **Consumer sites.**
  - routes: `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`,
    `calibrate_preview`@`salary.py:1064` (Q-13: route inline `taxable = gross - total_pre_tax`@`:1095`
    vs `bk.taxable_income`), `calibrate_confirm`@`salary.py:1127`.
  - svc consumers: `_apply_marginal_brackets`@`tax_calculator.py:173`,
    `derive_effective_rates`@`calibration_service.py:34`, `calculate_state_tax`@`tax_calculator.py:240`
    (receives the annualized base).
  - templates: `salary/breakdown.html` (89), `salary/calibrate_confirm.html` (43).
  - models (input): `TaxBracket.min_income`/`max_income`@`tax_config.py:113-114`,
    `SalaryProfile.additional_income`@`salary_profile.py:88`.
- **Primary path.** For the DISPLAYED `taxable_income` token:
  `calculate_paycheck`@`paycheck_calculator.py:155` (`gross_biweekly - total_pre_tax`, floor 0).
  The engine-internal taxable figures (`tax_calculator.py:112-120`, `:263`, `:233`) are NOT the
  same concept -- they are federal/state internal intermediates that additionally subtract a
  standard deduction; they must NOT be conflated with the display token. **PRIMARY PATH known**
  for the display token; the multi-definition split is a Phase 3 labeled-DEFINITION item, not a
  primary-path ambiguity.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (`taxable_income` -- 6 producers, 4 routes,
  2 templates; "Q-13 anchor"). Exact pairs:
  - `calculate_paycheck`@`paycheck_calculator.py:155` (display: gross - pre_tax, floor 0) **vs**
    `salary.calibrate_preview` inline `taxable = data["actual_gross_pay"] - bk.total_pre_tax`@`salary.py:1095`
    -- Q-13: `bk.total_pre_tax` percentage deductions were computed against the PROFILE
    `gross_biweekly` (`paycheck_calculator.py:440`), not `actual_gross_pay`; the two taxable
    values DIVERGE when the stub gross differs from the profile gross (the calibration use case).
    Cross-link Q-13 (`09_open_questions.md:514-559`).
  - `calculate_paycheck`@`:155` display taxable **vs** `calculate_federal_withholding`@`tax_calculator.py:112-120`
    federal-internal taxable -- DIFFERENT layer (internal adds std-ded + annualizes); Phase 3
    records as a labeled definition difference, NOT a drift to "fix".
  - legacy `calculate_federal_tax`@`tax_calculator.py:233` (no pre-tax subtraction) -- GOVERNED
    definition discrepancy; grep-confirmed zero `app/` consumers; recommend Phase 8 dead-code
    finding.
- **Prior-audit linkage.** PA-23 (tax-withholding precision vs IRS Pub 15-T: the federal-internal
  taxable @`tax_calculator.py:112-120` is the Pub 15-T Steps 1-3 base whose exact value the
  PA-23-gap tests do not pin). PA-02 (rate/threshold fields feeding the bracket walk:
  `TaxBracket.rate` `Numeric(5,4)`, std-ded). Q-13 cross-link (calibrate_preview inline taxable).

---

## Concept: federal_tax

- **Intended definition.** Per-period federal income tax withheld via the IRS Publication 15-T
  Percentage Method (annualize -> pre-tax/W-4-4(b) adjust -> standard deduction -> marginal
  brackets -> W-4-3 credits -> de-annualize + extra withholding). Calibrated flavor: `taxable *
  effective_federal_rate`. Sources: inventory vocab line 79; `tax_calculator.py:35-170` +
  module docstring `:7-14`; `calibration_service.py:106-135`.
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP), per biweekly period
  (`calculate_federal_withholding` de-annualizes @`:158-164`; legacy wrapper returns ANNUAL).
- **One engine, two input sources + one aggregation layer (analytical-frame classification).**
  Per-site provenance table (which inputs each site sources and from where):

  | Site | base | rate/bracket source | pre-tax handling | output |
  | ---- | ---- | ------------------- | ---------------- | ------ |
  | `calculate_federal_withholding`@`tax_calculator.py:35` | annualized `gross_pay*pay_periods + additional_income`@`:105` | `bracket_set.brackets` + `standard_deduction` (tax_config) @`:117,128` | subtracts annualized `pre_tax_deductions`@`:112` + W-4 4(b)@`:112` | per-period Decimal @`:158-164` |
  | `calculate_paycheck`@`paycheck_calculator.py:185` (bracket path) | `gross_biweekly`@`:133`; `annual_pre_tax = total_pre_tax*pp`@`:176` | `bracket_set` from `load_tax_configs`@`tax_config_service.py:16` | pre-tax via `_calculate_deductions`@`:149` | `federal_biweekly`@`:185-195`,@`:239` |
  | `apply_calibration`@`calibration_service.py:133` (calibrated path, gated `is_active`@`paycheck_calculator.py:160-163`) | `taxable_biweekly`@`paycheck_calculator.py:155` | `calibration.effective_federal_rate` (stored `CalibrationOverride`) | already in `taxable_biweekly` | `taxable*rate`@`:133-135` |
  | `derive_effective_rates`@`calibration_service.py:83` | `taxable_income` param | `actual_federal_tax / taxable`@`:83` | caller supplies taxable | effective RATE (calibration input), not amount |
  | `calculate_federal_tax`@`tax_calculator.py:215` (legacy) | `annual_gross` param | `bracket_set` + std-ded @`:233` | NONE | ANNUAL tax; **zero `app/` consumers** |
- **Producer sites.** svc:`calculate_federal_withholding`@`tax_calculator.py:35` (canonical
  engine); svc:`_apply_marginal_brackets`@`tax_calculator.py:173` (`federal_tax` intermediate,
  annual tax @`:207`); svc:`calculate_paycheck`@`paycheck_calculator.py:92` (path-selector, emits
  `federal_biweekly`@`:239`); svc:`apply_calibration`@`calibration_service.py:106` (calibrated
  amount @`:133`); svc:`calculate_federal_tax`@`tax_calculator.py:215` (legacy, dead).
  **P1-f reconciliation:** `derive_effective_rates`@`calibration_service.py:34` produces the
  effective federal RATE (a calibration INPUT), not the amount -- retained as a rate-input
  producer, labeled accordingly; `load_tax_configs`@`tax_config_service.py:16` loads `bracket_set`
  with no arithmetic -> input/CONSUMER; `calculate_gap`@`retirement_gap_calculator.py:37` uses an
  `estimated_tax_rate` retirement input -> CONSUMER.
- **Consumer sites.** routes: `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`,
  `calibrate_preview`@`salary.py:1064` (Q-13), `calibrate_confirm`@`salary.py:1127`,
  `year_end_tab`@`analytics.py:171`, `dashboard`@`retirement.py:46`,
  `gap_analysis`@`retirement.py:301`. templates: `salary/breakdown.html` (98),
  `salary/projection.html` (60), `salary/calibrate_confirm.html` (65, 66),
  `analytics/_year_end.html` (68), `retirement/_gap_analysis.html`. models (input):
  `TaxBracketSet.standard_deduction`/`child_credit_amount`/`other_dependent_credit_amount`@`tax_config.py:52,59,63`,
  `TaxBracket.rate`@`tax_config.py:115`, `CalibrationOverride.actual_federal_tax`/`effective_federal_rate`@`calibration_override.py:81,89`,
  `UserSettings.estimated_retirement_tax_rate`@`user.py:242`,
  `SalaryProfile.additional_deductions`/`extra_withholding`@`salary_profile.py:92,96`.
- **Primary path.** Bracket-based `calculate_federal_withholding`@`tax_calculator.py:35` is the
  canonical engine; `calculate_paycheck`@`paycheck_calculator.py:185-195` is the canonical caller
  that selects bracket-vs-calibrated via the `use_calibration` gate (`:160-163`). The calibrated
  path is an intentional, gated override (the pay-stub-calibration use case), not a competing
  canonical. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (`federal_tax` -- 8 producers, 7 routes, 5
  templates; calibration-vs-bracket split, Q-13). Aggregation-layer note (analytical frame): the
  `analytics/_year_end.html` annual federal total (`year_end_tab`@`analytics.py:171`) is an
  aggregation-layer consumer of per-period breakdowns -- PHASE 3 REQUIRED to verify the annual
  federal sum equals the 26-period withholding sum (PA-24). Exact pairs:
  - `calculate_federal_withholding`@`tax_calculator.py:35` (bracket) **vs**
    `apply_calibration`@`calibration_service.py:133` (calibrated) for the same profile when
    calibration is active -- DIFFER by design; Phase 3 labels intentional.
  - `calculate_paycheck` bracket federal @`paycheck_calculator.py:185-195` **vs**
    `salary.calibrate_preview` Q-13 inline taxable derivation @`salary.py:1095` feeding effective
    rates -- cross-link Q-13.
  - legacy `calculate_federal_tax`@`tax_calculator.py:215` (annual, no pre-tax) -- dead;
    Phase 8 dead-code finding.
- **Prior-audit linkage.** PA-23 (PRIMARY: "seven tax tests use range/directional vs exact
  Decimal against IRS Pub 15-T" -- the `:102-170` pipeline is exactly the under-tested method).
  PA-02 (Marshmallow `Range(0,100)` vs DB `CHECK(0..1)` on tax-rate fields -- `TaxBracket.rate`
  `Numeric(5,4)` and the W-4 inputs feed here; entering `6.2` fails opaquely). PA-24 (26-period
  withholding total vs annual liability -- the year-end aggregation path). PA-22
  (`calculate_federal_withholding` validates `gross_pay<0`@`:91-92`, `pay_periods<=0`@`:93-94`,
  negative dependents @`:97-100` -- the negative-path inputs PA-22 says are untested).

---

## Concept: state_tax

- **Intended definition.** Per-period state income tax via the configured flat-rate method
  (`(annual_gross - state_standard_deduction) * flat_rate`), or 0 when the state's `tax_type_id`
  is the `NONE` ref id or `state_config` is absent. Calibrated flavor: `taxable *
  effective_state_rate`. Sources: inventory vocab line 81; `tax_calculator.py:240-268`
  (ID-based NONE check via `ref_cache`@`:257`, NOT a name string -- E-15 compliant);
  `calibration_service.py:136-138`.
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP). `calculate_state_tax` returns ANNUAL;
  `calculate_paycheck` de-annualizes `(state_annual / pp).quantize(...)`@`:202-204`.
- **Per-site provenance table.**

  | Site | base | rate source | NONE handling | output |
  | ---- | ---- | ----------- | ------------- | ------ |
  | `calculate_state_tax`@`tax_calculator.py:240` | `annual_gross` param (paycheck passes `taxable_biweekly*pp`@`paycheck_calculator.py:200`) | `state_config.flat_rate`@`:260` | `tax_type_id == ref_cache.tax_type_id(NONE)` -> 0 @`:257` | ANNUAL `(taxable*rate)`@`:266` |
  | `calculate_paycheck`@`paycheck_calculator.py:199-204` (bracket path) | `taxable_biweekly*pp`@`:200` | via `calculate_state_tax` | inherited | de-annualized `/pp`@`:202-204` ROUND_HALF_UP -> `state_biweekly` |
  | `apply_calibration`@`calibration_service.py:136` (calibrated) | `taxable_biweekly`@`paycheck_calculator.py:155` | `calibration.effective_state_rate` | n/a | `taxable*rate`@`:136-138` |
  | `derive_effective_rates`@`calibration_service.py:86` | `taxable_income` param | `actual_state_tax/taxable`@`:86` | n/a | effective RATE (calibration input) |
- **Producer sites.** svc:`calculate_state_tax`@`tax_calculator.py:240`;
  svc:`calculate_paycheck`@`paycheck_calculator.py:92` (de-annualizes @`:202-204`, emits
  `state_biweekly`@`:240`); svc:`apply_calibration`@`calibration_service.py:106` (@`:136`).
  **P1-f reconciliation:** `derive_effective_rates`@`calibration_service.py:34` produces the
  effective state RATE (calibration input), not the amount -- retained as a rate-input producer.
- **Consumer sites.** routes: `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`,
  `calibrate_preview`@`salary.py:1064`, `update_tax_config`@`salary.py:1251`,
  `year_end_tab`@`analytics.py:171`. templates: `salary/breakdown.html` (102),
  `salary/projection.html` (60), `salary/calibrate_confirm.html` (70, 71),
  `analytics/_year_end.html` (80). models (input): `StateTaxConfig.flat_rate`@`tax_config.py:175`,
  `StateTaxConfig.standard_deduction`@`tax_config.py:176`,
  `CalibrationOverride.actual_state_tax`/`effective_state_rate`@`calibration_override.py:82,90`.
- **Primary path.** `calculate_state_tax`@`tax_calculator.py:240` (canonical engine),
  de-annualized by `calculate_paycheck`@`paycheck_calculator.py:202-204`; calibrated override via
  `apply_calibration` gated on `calibration.is_active`. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED (1.7.4 OVERRIDE).** 1.7.4 lists `state_tax` under
  SINGLE-path tokens (1 producer / 1 consumer). **Source-read OVERRIDES this** (as P2-b did for
  `months_saved`): there are >=3 genuine producers (`calculate_state_tax`, `calculate_paycheck`
  de-annualize, `apply_calibration` calibrated) and 5 routes. Exact pairs:
  - `calculate_state_tax`@`tax_calculator.py:266` (annual quantize) -> `calculate_paycheck`
    `(state_annual/pp).quantize`@`:202-204` (DOUBLE quantize: annual then per-period) **vs**
    `apply_calibration`@`calibration_service.py:136` (SINGLE biweekly quantize) for the same
    profile -- ROUNDING_DRIFT candidate.
  - year-end state total (`analytics.py:171`) **vs** 26-period `state_biweekly` sum (PA-24
    aggregation-layer reconciliation -- PHASE 3 REQUIRED per analytical frame).
- **Prior-audit linkage.** PA-02 (PRIMARY: `StateTaxConfig.flat_rate`@`tax_config.py:175`
  `Numeric(5,4)`; the F-014 finding names "state tax rates" -- Marshmallow `Range(0,100)`
  percentage vs DB `CHECK(0..1)` decimal; entering `6.2` fails opaquely). PA-23 (state-tax
  exact-value precision gap). PA-24 (year-end state reconciliation).

---

## Concept: fica

- **Intended definition.** Per-period FICA = Social Security (capped at `ss_wage_base`) +
  Medicare (base rate on all gross + 0.9% surtax above `medicare_surtax_threshold`), tracked
  across periods via cumulative YTD wages. Calibrated flavor: `gross * effective_ss_rate` and
  `gross * effective_medicare_rate` with **no cap**. Sources: inventory vocab line 83;
  `tax_calculator.py:274-321`; `calibration_service.py:139-144`.
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP); `calculate_fica` returns
  `dict{ss, medicare, total}`. `calculate_paycheck` stores `social_security`/`medicare`
  separately on the breakdown.
- **SS wage-base cap (PA-21) -- explicit yes/no with file:line.**
  - **Bracket/engine path: YES, cap IS enforced.** `tax_calculator.calculate_fica`@`:300-306`:
    `if cumulative >= ss_wage_base: ss_tax = ZERO`@`:300-301`; partial crossing period
    `ss_taxable = ss_wage_base - cumulative`@`:302-304`; else full `gross*ss_rate`@`:305-306`.
    Cap value from `FicaConfig.ss_wage_base`@`tax_config.py:221`. Cross-period YTD tracking is
    threaded by `calculate_paycheck`@`paycheck_calculator.py:206-212` from
    `_get_cumulative_wages`@`:480-504`.
  - **Calibrated path: NO cap.** `apply_calibration`@`calibration_service.py:139-144` computes
    `ss = (gross * effective_ss_rate).quantize(...)` every period with NO `ss_wage_base` ceiling
    and NO cumulative-wage input. A high earner on the calibration path accrues SS past the wage
    base. **DEFINITION DISCREPANCY between the two fica flavors AND a concrete PA-21
    confirmation** (the invariant is unenforced on the calibration path; PA-21 separately notes
    it is untested on both paths).
  - Caller dependency: the cap is correct ONLY when the caller threads `cumulative_wages`
    (`calculate_fica` defaults it to `ZERO`@`:274`). `calculate_paycheck` is the only `app/`
    caller that supplies it (grep-confirmed in verification).
- **Per-site provenance table.**

  | Site | base | rate source | SS cap? | output |
  | ---- | ---- | ----------- | ------- | ------ |
  | `calculate_fica`@`tax_calculator.py:274` | period `gross`@`:291` (not annualized) | `FicaConfig.ss_rate/medicare_rate/surtax_*`@`:293-297` | YES @`:300-306` (needs `cumulative_wages`) | `{ss, medicare, total}` |
  | `calculate_paycheck`@`paycheck_calculator.py:206-214` (bracket) | `gross_biweekly`@`:133` | via `calculate_fica` | YES (threads `_get_cumulative_wages`@`:207`) | `ss_biweekly`/`medicare_biweekly`@`:241-242` |
  | `apply_calibration`@`calibration_service.py:139-144` (calibrated) | `gross_biweekly`@`paycheck_calculator.py:155` | `calibration.effective_ss_rate`/`effective_medicare_rate` | **NO** | `ss`/`medicare` |
  | `derive_effective_rates`@`calibration_service.py:91-95` | `actual_gross_pay` param | `actual_ss/gross`, `actual_medicare/gross` | n/a | effective RATEs (calibration input) |
  | `_get_cumulative_wages`@`paycheck_calculator.py:480` | re-derives per-period gross @`:499-501` | n/a | feeds the cap | YTD cumulative gross |
- **Producer sites.** svc:`calculate_fica`@`tax_calculator.py:274`;
  svc:`calculate_paycheck`@`paycheck_calculator.py:92` (emits `ss_biweekly`/`medicare_biweekly`
  @`:241-242`); svc:`apply_calibration`@`calibration_service.py:106` (@`:139-144`);
  svc:`_get_cumulative_wages`@`paycheck_calculator.py:480` (SS-cap-input producer). **P1-f
  reconciliation:** `derive_effective_rates`@`calibration_service.py:34` produces effective
  SS/Medicare RATES (calibration input), not amounts -- retained as a rate-input producer.
- **Consumer sites.** routes: `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`,
  `calibrate_preview`@`salary.py:1064`, `calibrate_confirm`@`salary.py:1127`,
  `year_end_tab`@`analytics.py:171`, `update_fica_config`@`salary.py:1310`. templates:
  `salary/breakdown.html` (106, 110), `salary/projection.html` (60),
  `salary/calibrate_confirm.html` (75, 76, 80, 81), `analytics/_year_end.html` (72, 76). models
  (input): `FicaConfig.ss_rate/ss_wage_base/medicare_rate/medicare_surtax_rate/medicare_surtax_threshold`@`tax_config.py:217,221,225,229,233`,
  `CalibrationOverride.actual_social_security`/`actual_medicare`/`effective_ss_rate`/`effective_medicare_rate`@`calibration_override.py:83,84,91,92`.
- **Primary path.** `calculate_fica`@`tax_calculator.py:274` (canonical engine, cap-correct when
  fed cumulative wages), driven by `calculate_paycheck`@`paycheck_calculator.py:206-214` (the only
  path supplying cumulative wages). The calibrated `apply_calibration` flavor is an intentional
  gated override that silently drops the SS cap -- Phase 3 DEFINITION_DRIFT, not a competing
  primary. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (`fica` -- 5 producers, 6 routes, 4
  templates; calibration/bracket split). Exact pairs:
  - `calculate_fica`@`tax_calculator.py:300-306` (cap enforced) **vs**
    `apply_calibration`@`calibration_service.py:139-144` (NO cap) for a high earner whose
    cumulative wages exceed `ss_wage_base` -- DIVERGES by construction; PA-21 confirmation.
  - `calculate_paycheck` SS @`tax_calculator.py:302-304` (partial-crossing period
    `ss_wage_base - cumulative`): Phase 3 worked example -- the period that crosses the cap must
    zero subsequent-period SS.
  - year-end FICA total (`analytics.py:171`) **vs** 26-period `ss+medicare` sum incl. the cap
    crossover (PA-24 aggregation-layer reconciliation -- PHASE 3 REQUIRED per analytical frame).
- **Prior-audit linkage.** PA-21 (PRIMARY: SS wage-cap invariant. Code ENFORCES it on the bracket
  path @`tax_calculator.py:300-306` but NOT on the calibration path @`calibration_service.py:139`;
  PA-21 says no test verifies the cap on either path -- this entry confirms AND extends PA-21).
  PA-02 (`FicaConfig.ss_rate`@`tax_config.py:217`, `medicare_rate`@`:225` `Numeric(5,4)` -- the
  named F-014 "FICA tax rates" fields; Marshmallow `Range(0,100)` vs DB `CHECK(0..1)`). PA-23
  (FICA exact-value precision gap). PA-24 (26-period FICA incl. cap crossover vs annual).

---

## Concept: pre_tax_deduction

- **Intended definition.** Decimal per-period pre-tax payroll deduction (401(k), Section-125,
  pre-tax insurance) that REDUCES `taxable_income` and therefore `federal_tax`/`state_tax`.
  Source: inventory vocab line 85; `paycheck_calculator.py:148-157` (computed step 4, subtracted
  into taxable step 5).
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP); flat amount, or
  `(gross_biweekly*pct).quantize(...)`@`:440-442`, with optional compounding inflation
  `amount*(1+rate)**years`@`:451-452`.
- **Ordering-dependency invariant (definitional -- Phase 3 verifies; flag any violator).**
  `pre_tax_deduction` MUST be computed and subtracted from gross BEFORE `taxable_income` and tax
  computation. Verified in the canonical path: `calculate_paycheck` computes pre-tax @`:149-152`
  (step 4) -> `taxable_biweekly = gross_biweekly - total_pre_tax`@`:155` (step 5) -> federal
  annualizes `annual_pre_tax = total_pre_tax*pp`@`:176` and passes it to
  `calculate_federal_withholding` (subtracted @`tax_calculator.py:112`); state receives
  `taxable_biweekly*pp`@`:200`; FICA on full gross @`:210-212`. **Invariant HOLDS** -- no producer
  applies a pre-tax deduction after tax computation.
- **Producer sites.** svc:`_calculate_deductions`@`paycheck_calculator.py:403` (filters by the
  PRE_TAX timing id, flat/pct/inflation @`:438-453`); svc:`calculate_paycheck`@`:92` (invokes it
  @`:149`, totals @`:152`, subtracts @`:155`). **P1-f reconciliation:** raw 1.7.3 lists
  `derive_effective_rates`@`calibration_service.py:34` (input `total_pre_tax`) -- it CONSUMES a
  pre-tax total (the route passes `gross - total_pre_tax` as taxable) -> CONSUMER under 1.7.8.
- **Consumer sites.** routes: `add_deduction`@`salary.py:696`, `update_deduction`@`salary.py:833`,
  `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`, `calibrate_preview`@`salary.py:1064`
  (Q-13 inline `gross - total_pre_tax`@`:1095`). templates: `salary/breakdown.html` (81),
  `salary/projection.html` (58), `salary/calibrate_confirm.html` (39),
  `salary/_deductions_section.html` (38, 40). models (input):
  `PaycheckDeduction.amount`@`paycheck_deduction.py:113` (timing-dependent),
  `CalibrationDeductionOverride.actual_amount`@`calibration_override.py:164`.
- **Primary path.** `_calculate_deductions`@`paycheck_calculator.py:403` invoked with the PRE_TAX
  timing id by `calculate_paycheck`@`:149`. Single parameterized producer. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED (scoped).** 1.7.4 lists `pre_tax_deduction` SINGLE-path;
  source-read AGREES the producer is one parameterized function. Phase 3 is still REQUIRED on two
  axes: (1) the ordering invariant -- pair `paycheck_calculator.py:155` (taxable AFTER pre-tax,
  step 5) vs steps 6-7 @`:159-214`: confirm no producer subtracts pre-tax after tax; (2) the
  percentage-base divergence -- `_calculate_deductions` pct @`paycheck_calculator.py:440`
  (`gross_biweekly*amount`, profile gross) **vs** `salary.calibrate_preview` using that
  profile-gross-based `total_pre_tax` against `actual_gross_pay`@`salary.py:1095` (Q-13
  cross-link, `09_open_questions.md:514-559`).
- **Prior-audit linkage.** PA-07/PA-20 (per-period pct/inflation quantize @`:440-442`,@`:451-452`
  feeds the net-pay terminal-quantize residue). PA-23 (`annual_pre_tax = total_pre_tax*pp`@`:176`
  is the Pub 15-T Step 2 pre-tax-adjustment input whose precision PA-23 leaves unpinned). Q-13
  cross-link.

---

## Concept: post_tax_deduction

- **Intended definition.** Decimal per-period post-tax payroll deduction (Roth 401(k), post-tax
  insurance) that does NOT reduce `taxable_income`; subtracted only from net pay. Source:
  inventory vocab line 87; `paycheck_calculator.py:216-231` (computed step 8 AFTER taxes,
  subtracted only in net step 9).
- **Units / type.** Decimal money (2dp, ROUND_HALF_UP); same flat/pct/inflation shape as
  `pre_tax_deduction` (`_calculate_deductions`@`:438-453`).
- **Ordering-dependency invariant (definitional -- Phase 3 verifies; flag any violator).**
  `post_tax_deduction` MUST be applied AFTER tax computation and MUST NOT reduce
  `taxable_income`. Verified: `calculate_paycheck` computes post-tax @`:217-220` (step 8) AFTER
  federal/state/FICA (steps 6-7 @`:159-214`) and subtracts it only inside `net_pay`@`:230`
  (step 9); it is NEVER subtracted from `taxable_biweekly`@`:155`. **Invariant HOLDS** -- no
  producer applies a post-tax deduction before tax computation.
- **Producer sites.** svc:`_calculate_deductions`@`paycheck_calculator.py:403` (filters by the
  POST_TAX timing id); svc:`calculate_paycheck`@`:92` (invokes it @`:217`, totals @`:220`,
  subtracts @`:230`). Same parameterized producer as `pre_tax_deduction`, distinguished only by
  the `timing_id` argument (DRY-correct; Phase 6 should note the single shared core).
- **Consumer sites.** routes: `add_deduction`@`salary.py:696`, `update_deduction`@`salary.py:833`,
  `breakdown`@`salary.py:960`, `projection`@`salary.py:1020`. templates:
  `salary/breakdown.html` (121), `salary/projection.html` (62),
  `salary/_deductions_section.html`. models (input):
  `PaycheckDeduction.amount`@`paycheck_deduction.py:113` (timing-dependent).
- **Primary path.** `_calculate_deductions`@`paycheck_calculator.py:403` invoked with the
  POST_TAX timing id by `calculate_paycheck`@`:217`. **PRIMARY PATH known.**
- **Multi-path flag: PHASE 3 REQUIRED (scoped).** 1.7.4 lists `post_tax_deduction` SINGLE-path;
  source-read AGREES. Phase 3 REQUIRED only on the ordering invariant: pair
  `paycheck_calculator.py:217-220` (step 8, after steps 6-7) vs `:230` (subtracted only in net) --
  confirm no producer subtracts post-tax from `taxable_biweekly`@`:155`.
- **Prior-audit linkage.** PA-07/PA-20 (per-period pct/inflation quantize @`:440-442`,@`:451-452`
  contributes to the net-pay terminal-quantize residue). PA-22 (deduction input edge cases:
  inactive/zero/percentage-of-zero-gross paths in `_calculate_deductions`@`:422-458`). No other
  PA applies specifically.

<!-- P2-c (income/tax/paycheck) ends here. P2-d appends savings/growth/retirement/transfer/effective_amount/year_summary family below this line. Do not mark Phase 2 complete here; that is P2-reconcile's gate. -->

## Scope of this session (P2-d): savings / growth / retirement / transfer / effective_amount / year_summary family

Tokens cataloged here (18): `apy_interest`, `growth`, `employer_contribution`,
`contribution_limit_remaining`, `ytd_contributions`, `transfer_amount`, `effective_amount`,
`goal_progress`, `emergency_fund_coverage_months`, `cash_runway_days`, `transfer_amount_computed`,
`pension_benefit_annual`, `pension_benefit_monthly`, `year_summary_jan1_balance`,
`year_summary_dec31_balance`, `year_summary_principal_paid`, `year_summary_growth`,
`year_summary_employer_total`.

Inventory 1.7.2 vocabulary-addition review (fold decisions recorded per session demand). P2-d is
the last family session and owns every 1.7.2 addition not absorbed by P2-a/b/c:

- `pension_benefit_annual` / `pension_benefit_monthly` (P1-b) -- **absorbed** (deferred to P2-d by
  P2-c `02_concepts.md:1306`). Cataloged as their own sections below (retirement family).
- `transfer_amount_computed` (P1-c) -- **absorbed** (deferred by P2-b `02_concepts.md:546`).
  Cataloged below; it has consumers but no service producer (route-resident, intentional).
- `cash_runway_days` (P1-b) -- **absorbed** and the deferred fold decision is RESOLVED here:
  **NOT folded into `emergency_fund_coverage_months`.** Read-confirmed at
  `dashboard_service._compute_cash_runway`@`dashboard_service.py:375-417`: it is
  trailing-30-day SETTLED-expense daily-burn against the *checking* `current_balance`, returns
  integer **days**; `emergency_fund_coverage_months`
  (`savings_goal_service.calculate_savings_metrics`@`:139`) is *savings_balance* / average monthly
  expenses, returns Decimal **months**. Different balance base, different lookback, different
  unit -- they answer different questions. Cataloged as a distinct token below.
- `entry_sum_total` / `entry_remaining` (P1-c) -- **NOT a P2-d family token; flagged ORPHAN.**
  These are entry-tracking tokens (Q-08 family). P2-a deferred them to "the transaction/entry
  family" (`02_concepts.md:34`), P2-b confirmed "not loan/debt" (`:547`), P2-c put them in "other
  families" (`:1306`). No P2 session owns an entry/transaction family. They are financial
  concepts a page renders (`grid/_transaction_cell.html`, `dashboard/_bill_row.html`). Per the
  session demand they are NOT forced into a P2-d section; they are listed under "Orphan tokens
  for P2-reconcile" in the completeness checklist with their inventory locations. `goal_progress`
  and `effective_amount` below cross-link them where they intersect (entry-progress flavor of
  `goal_progress`; settle workflow feeding `effective_amount`), but the standalone catalog of
  `entry_sum_total`/`entry_remaining` is an explicit P2-reconcile gap, not a P2-d omission.
- `paycheck_breakdown` / `chart_date_labels` (P1-c) -- out of P2-d family (P2-c folded
  `paycheck_breakdown` as an alias `02_concepts.md:1293-1305`; `chart_date_labels` is P2-a's
  chart family). Not re-cataloged here.

Analytical-frame finding (one engine vs aggregation-layer recompute). The savings/growth/
retirement family is **engine-canonical with aggregation-layer fan-out**, matching P1-f's drift
thesis. The engines (`interest_projection.calculate_interest`, `growth_engine.project_balance`/
`calculate_employer_contribution`, `pension_calculator.calculate_benefit`,
`investment_projection.calculate_investment_inputs`, `savings_goal_service`) each have ONE
producer and all quantize `Decimal("0.01")` `ROUND_HALF_UP` at their boundary (A-01-clean --
verified by Read at `interest_projection.py:114`, `growth_engine.py:114,119-124,243-244,363`,
`pension_calculator.py:63,65-67`, `savings_goal_service.py:135` region). The drift surface is the
aggregation layer (`year_end_summary_service` `_compute_savings_progress`@`:887` /
`_compute_debt_progress`@`:824` / `_compute_interest_for_year`@`:1207`,
`retirement_dashboard_service` `compute_gap_data`@`:79` / `_project_retirement_accounts`@`:338`,
`savings_dashboard_service.compute_dashboard_data`@`:61`). Per the frame, every aggregation-layer
producer of a multi-producer concept is marked PHASE 3 REQUIRED even where it currently appears to
agree. `effective_amount` is the exception to "engine-canonical": it is a model-property canonical
(`Transaction.effective_amount`@`transaction.py:221-245`) with seven hand-rolled mirrors and a
25+-site direct-read bypass surface -- the genuinely scattered concept of this family, handled
with a consolidated bypass table in its entry.

1.7.3 over-attribution corrected by P2-d (1.7.8 operational rule). `grep -n "employer"
app/services/paycheck_calculator.py` returns ZERO matches: `calculate_paycheck`@
`paycheck_calculator.py:92` does **not** compute `employer_contribution`. The 1.7.3 index and
1.7.2 `paycheck_breakdown` bundle text list it as an `employer_contribution` producer; that is a
producer over-attribution. Corrected in the `employer_contribution` entry: the sole producer is
`growth_engine.calculate_employer_contribution`@`growth_engine.py:91`. This also resolves P2-c's
deferral of "the `paycheck_breakdown.employer_contribution` field" to P2-d -- there is no
independently-computed employer field on the breakdown; the retirement dashboard composes
employer contribution via the growth engine.

### QC note (P2-d verification, 5 producer spot-checks by Read at source)

1. `calculate_interest`@`interest_projection.py:49` -- CONFIRMED producer of `apy_interest`:
   daily `balance * ((1+apy/365)**days - 1)`@`:89-90`, monthly/quarterly branches @`:91-110`,
   terminal `.quantize(Decimal("0.01"), ROUND_HALF_UP)`@`:114`. `DAYS_IN_YEAR=Decimal("365")`@
   `:44` confirmed (PA-06 leap-year, documented accepted simplification @ module docstring
   `:7-34`).
2. `calculate_employer_contribution`@`growth_engine.py:91` -- CONFIRMED sole producer of
   `employer_contribution`: flat `(gross*pct).quantize(...HALF_UP)`@`:114`, match
   `min(employee_contribution, (gross*cap_pct))*match_pct`@`:117-124`. Callers
   `project_balance`@`:265`, `reverse_project_balance`@`:339`, `routes/investment.py:187` all
   delegate to it (`grep -rn calculate_employer_contribution app/`).
3. `calculate_benefit`@`pension_calculator.py:31` -- CONFIRMED sole producer of
   `pension_benefit_annual`/`pension_benefit_monthly`: `annual = (benefit_multiplier *
   years_of_service * high_avg).quantize(TWO_PLACES, HALF_UP)`@`:61-63`, `monthly = (annual /
   12).quantize(TWO_PLACES, HALF_UP)`@`:65-67`; empty-salary guard @`:49-55`.
4. `calculate_savings_metrics`@`savings_goal_service.py:139` -- CONFIRMED sole producer of
   `emergency_fund_coverage_months`: `months = (savings_balance /
   avg_expenses).quantize(Decimal("0.1"), HALF_UP)`@`:162-164` (note: 0.1 precision, a
   coverage-duration ratio, not a money value -- A-01 is money-scoped); zero-expense guard
   @`:155-160`; `Decimal("26")/Decimal("12")` factor @`:168` (the Q-12 duplicated factor).
5. `_compute_debt_progress`@`year_end_summary_service.py:824` -- CONFIRMED producer of
   `year_summary_principal_paid`: `principal_paid = jan1_bal - dec31_bal`@`:871` via
   `_balance_from_schedule_at_date(schedule, date(year-1,12,31)/date(year,12,31), original)`
   @`:864-870`; `original = params.original_principal`@`:859`.
Additional Reads: `Transaction.effective_amount`@`transaction.py:221-245` (4-tier:
is_deleted->0, excludes_from_balance->0, actual_amount if not None, else estimated_amount;
docstring `:231` "single source of truth"); `Transfer.effective_amount`@`transfer.py:174-182`
(2-tier: excludes_from_balance->0 else amount); `_compute_transfers_summary`@
`year_end_summary_service.py:636` (queries `Transfer` model `:658`, `total_amount += t.amount`
`:679`); `calculate_investment_inputs`@`investment_projection.py:100` (`ytd_contributions +=
Decimal(str(t.estimated_amount))` -- estimated_amount bypass justified by upstream
`status.excludes_from_balance` filter); `retirement_dashboard_service.compute_slider_defaults`@
`:257` docstring (Decimal-only, zero-SWR-is-explicit -- bears on PA-04 status, see prior-audit
linkage in `growth`).

Scope tally for P2-reconcile section-2 completeness gate: **0 of 18** P2-d concepts ended
`PRIMARY PATH: UNKNOWN`. Every P2-d token has a justified canonical producer, a per-sub-concept
owner (token-overloaded `growth`/`year_summary_growth`/`goal_progress`), or an
intentional-by-design route-resident path (`transfer_amount_computed`,
`contribution_limit_remaining` final subtraction) governed by an already-filed Q (Q-08, Q-12,
Q-15). **0 new Q-NN raised by P2-d; highest remains Q-15.** Rationale for zero new questions:
every divergence found (PA-06 leap-year, off-engine recomputes, the `effective_amount` bypass
surface, the 1.7.3 employer over-attribution, the goal_progress/growth token overloads) has a
clear intended behavior or is already governed by Q-08/Q-12/Q-15 -- none is a "what is intended"
ambiguity.

**Cumulative Phase-2 `PRIMARY PATH: UNKNOWN` tally (all sessions):** P2-a = 2 (`period_subtotal`
@`02_concepts.md:323`, `debt_total` @`:511`); P2-b = 1 (`loan_principal_displayed` @`:882`);
P2-c = 0 (tally @`:1351`); P2-d = 0. **Total = 3 of 47 cataloged concepts** across
P2-a(8)+P2-b(13)+P2-c(8)+P2-d(18). The three UNKNOWNs are all already covered by filed questions
(Q-10, Q-15, Q-11 respectively). P2-reconcile's section-2 gate: no UNKNOWN lacks a governing Q.

---

## Concept: apy_interest

- **Intended definition.** Projected interest earned by an interest-bearing (HYSA / Money Market /
  CD) account during a pay period: balance times the period's accrued rate under the account's
  compounding frequency (daily / monthly / quarterly), using the actual/365 US-retail-bank
  day-count convention. Source: `calculate_interest` docstring + body
  `interest_projection.py:1-114` (read in full); `InterestParams.apy`@`interest_params.py:60`
  (stored rate, `Numeric(7,5)`); `00_priors.md` PA-06/PA-17/PA-18/PA-19.
- **Units / type.** Decimal money (2dp), `ROUND_HALF_UP` -- the per-period interest dollar amount
  (not a rate; the rate input `apy` is `Decimal` fractional, e.g. `0.04500`).
- **Producer sites.**
  - svc:`calculate_interest`@`interest_projection.py:49` -- THE engine; sole arithmetic producer
    (`balance*((1+apy/365)**days-1)` daily @`:90`; monthly @`:96`; quarterly @`:110`; quantize
    @`:114`). LOW-RISK 1.7.8 partition; not a P1-f relocation.
  - svc:`calculate_balances_with_interest`@`balance_calculator.py:112` -- per-period wrapper that
    calls `calculate_interest` and folds the result into the running balance (aggregation-layer
    producer of the interest-inclusive balance series; the interest value itself is the engine's).
  - svc:`_compute_interest_for_year`@`year_end_summary_service.py:1207` -- year-end aggregation
    producer (sums per-period interest for the savings-progress `growth_total`; A-06-adjacent
    pipeline, P1-b verified def-line per 1.7.8). Aggregation-layer.
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_slider_defaults`@
    `retirement_dashboard_service.py:257` under `apy_interest`. It produces a balance-weighted
    average *return rate* and an SWR percentage, NOT a per-period interest dollar amount; it is a
    `growth`-family rate producer (KEEP-as-arithmetic borderline per 1.7.8), cross-linked from
    `growth`, and is a CONSUMER w.r.t. `apy_interest`. Removed from this producer list.
- **Consumer sites.** route:`interest_detail`@`accounts.py:1233` (period_data interest column
  @`:1299`). Templates: `accounts/interest_detail.html` (42, 85), `savings/dashboard.html`
  (137, 140). No JS (E-17: series computed server-side).
- **Primary path.** `calculate_interest`@`interest_projection.py:49` is the single canonical
  engine; `calculate_balances_with_interest`@`balance_calculator.py:112` is the canonical
  per-period consumer that threads it into the balance series, and `_compute_interest_for_year`@
  `year_end_summary_service.py:1207` is the canonical year-end aggregator. The split is layered,
  not competing (engine -> per-period balance -> annual total). Justified by the engine docstring
  and 1.7.3; **not UNKNOWN.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2. Pairs:
  - `accounts/interest_detail.html` (per-period interest column from `calculate_interest` via
    `accounts.interest_detail`@`accounts.py:1299`) **vs** `savings/dashboard.html` (137, 140)
    interest figure via `savings_dashboard_service` -> verify identical for the same
    `(account, period)` (1.7.4-named single-page comparison).
  - Per the analytical frame: `_compute_interest_for_year`@`year_end_summary_service.py:1207`
    (annual `year_summary_growth` interest component) **vs** the sum of per-period
    `calculate_interest` outputs over the same year -- aggregation-layer must equal the engine
    sum (PA-19 26-period compounding-accuracy concern).
- **Prior-audit linkage.** PA-06 (F-126, open): leap-year actual/365-vs-366 overstatement is
  GENUINE and live -- `DAYS_IN_YEAR=Decimal("365")`@`interest_projection.py:44` confirmed by
  Read; documented as an accepted simplification in the module docstring `:7-34` (~$1.23/$100K for
  a 14-day period crossing Feb 29). Phase 3/8 records it as documented-accepted, not silent.
  PA-17 (partially-remediated): no exact-value HYSA interest assertions. PA-18 (open): no tests
  for invalid `compounding_frequency` (the `else: return ZERO`@`:111-112` path) or extreme APY.
  PA-19 (unclear): no 26-period full-year compounding-accuracy test -- the multi-path pair above
  is exactly the uncovered invariant.

---

## Concept: growth

- **Intended definition.** A token-overloaded concept (flagged discrepancy, NOT reconciled here --
  Phase 3 input). The 1.7.3 index attaches one token to four distinct quantities:
  - **G1 investment/account growth** -- the period return added to a balance:
    `current_balance * period_return_rate` from `InvestmentParams.assumed_annual_return`. Source:
    `growth_engine.project_balance` docstring + body `growth_engine.py:164-295` (growth @`:243`).
  - **G2 reverse growth** -- the same applied backward for pre-anchor projection.
    `growth_engine.reverse_project_balance`@`:297`.
  - **G3 spending-trend percentage change** -- `(last - first) / first * 100` over a window.
    `spending_trend_service._compute_trends`@`:97`, `_safe_pct_change`@`:470`.
  - **G4 year-end growth/return totals** -- annual investment return / interest aggregates.
    `year_end_summary_service._compute_savings_progress`@`:887`,
    `_compute_interest_for_year`@`:1207`, `_compute_mortgage_interest`@`:380` (interest as a
    debt-side "growth" of paid interest), `_compute_debt_progress`@`:824`.
  These are different quantities; the inventory 1.7.4 itself instructs "Phase 3 must distinguish
  sub-concepts before comparing." Stored-rate inputs:
  `InvestmentParams.assumed_annual_return`@`investment_params.py:80`,
  `UserSettings.default_inflation_rate`@`user.py:234`,
  `UserSettings.safe_withdrawal_rate`@`user.py:237`, `SalaryRaise.percentage`@`salary_raise.py:110`,
  `UserSettings.trend_alert_threshold`@`user.py:246`.
- **Units / type.** G1/G2/G4-investment/G4-interest: Decimal money (2dp, `ROUND_HALF_UP` --
  `growth_engine.py:243-244,363`). G3: Decimal percentage. The token spans money and rate units --
  part of the discrepancy.
- **Producer sites.** svc:`project_balance`@`growth_engine.py:164` (G1, growth @`:243` quantize
  HALF_UP); svc:`reverse_project_balance`@`growth_engine.py:297` (G2, quantize @`:363`);
  svc:`_compute_trends`@`spending_trend_service.py:97` and `_safe_pct_change`@`:470` (G3);
  svc:`compute_gap_data`@`retirement_dashboard_service.py:79` and
  `_project_retirement_accounts`@`:338` (G1 aggregation-layer for retirement);
  svc:`_compute_savings_progress`@`year_end_summary_service.py:887`,
  `_compute_debt_progress`@`:824`, `_compute_interest_for_year`@`:1207` (G4 aggregation-layer).
  All KEEP per 1.7.8 (none in the six P1-f relocations); `growth_engine` G1/G2 are LOW-RISK
  engines, the `year_end`/`retirement_dashboard` ones are HIGH-RISK aggregation-layer KEEPs.
- **Consumer sites.** route:`dashboard`@`investment.py:63`; route:`growth_chart`@
  `investment.py:363`; route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@
  `retirement.py:301`; route:`trends_tab`@`analytics.py:272`; route:`year_end_tab`@
  `analytics.py:171`. Templates: `investment/dashboard.html`, `investment/_growth_chart.html`,
  `retirement/_gap_analysis.html` (41), `analytics/_trends.html`, `analytics/_year_end.html`.
  JS: charts consume the server series only (E-17).
- **Primary path.** Per sub-concept (the token-overload is the reason there is no single owner;
  this is the same treatment P2-a gave `chart_balance_series` -- per-domain owners, "not
  UNKNOWN"): G1/G2 -> `growth_engine.project_balance`/`reverse_project_balance`@
  `growth_engine.py:164,297` (canonical projection engine; the retirement/year-end sites are
  aggregation-layer consumers of it); G3 -> `spending_trend_service`@`:97,470`; G4-investment ->
  `_project_investment_for_year` (called by `_compute_savings_progress`@`:887`); G4-interest ->
  `_compute_interest_for_year`@`:1207` (which threads `calculate_interest`, see `apy_interest`).
  Each sub-concept has a clear canonical producer; **not UNKNOWN**, but the **token overload is a
  DEFINITION discrepancy Phase 3 must split before any consistency comparison.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (9 producers). Per the analytical frame the
  aggregation-layer producers are PHASE 3 REQUIRED even where they appear to agree. Exact pairs:
  - G1 engine `growth_engine.project_balance`@`growth_engine.py:164` **vs** its
    retirement-aggregation consumer `_project_retirement_accounts`@
    `retirement_dashboard_service.py:338` -- same engine, verify same inputs (assumed return,
    employer, contributions) for the same account-on-date.
  - G4 `_compute_savings_progress`@`year_end_summary_service.py:887` investment growth **vs** the
    `growth_engine` per-period sum over the same year (W-151/W-159 net_worth_amort: Dec 31
    net-worth investment balance must equal Dec 31 savings-progress balance -- cross-link P2-a
    `net_worth`/`account_balance`, Q-15).
  - DEFINITION split: G3 (`spending_trend_service` pct-change) must NOT be compared numerically
    with G1/G2/G4 (money) -- Phase 3 records the token overload itself as a DEFINITION_DRIFT
    candidate.
- **Prior-audit linkage.** PA-04 (F-100, listed "open" in priors 0.6) -- **status-reconciliation
  finding for Phase 3/8:** `retirement_dashboard_service.compute_slider_defaults`@`:257` docstring
  (Read-confirmed) states all arithmetic is now `Decimal` (PA-04's defect was
  `float(... or 0.04)*100`) and that a stored SWR of exactly `Decimal("0")` is treated as an
  explicit zero, only `None` triggers the default -- i.e. PA-04 appears REMEDIATED in current
  code. Priors 0.6 still marks it open. Phase 3 must Read the body (not just the docstring) and
  reconcile PA-04's status; this is the "verify by code, not trust the roadmap" case the plan
  calls for. PA-05 (F-101, open): hardcoded fallback `0.04`/`4.0`/`7.0` magic numbers -- the
  `_DEFAULT_SWR_PCT`/`_DEFAULT_RETURN_PCT` constants named in the `compute_slider_defaults`
  docstring are the candidate remediation; Phase 3 verifies they carry source citations. PA-29
  (partially-remediated): three growth-engine tests directional-only -- the G1/G4 pair above is
  the unverified-precision surface.

---

## Concept: employer_contribution

- **Intended definition.** The employer's retirement contribution for a single biweekly pay
  period: for `flat_percentage` accounts, `gross_biweekly * flat_percentage`; for `match`
  accounts, `min(employee_contribution, gross_biweekly * match_cap_percentage) * match_percentage`;
  `0` for `none`. Source: `calculate_employer_contribution` docstring + body
  `growth_engine.py:91-127` (read in full). Stored inputs:
  `InvestmentParams.employer_flat_percentage`@`investment_params.py:90`,
  `employer_match_percentage`@`:91`, `employer_match_cap_percentage`@`:92`.
- **Units / type.** Decimal money (2dp), `ROUND_HALF_UP` (quantized at every return @
  `growth_engine.py:114,119-120,123-124`).
- **Producer sites.** svc:`calculate_employer_contribution`@`growth_engine.py:91` -- **the sole
  producer.** Confirmed by `grep -rn calculate_employer_contribution app/`: called by
  svc:`project_balance`@`growth_engine.py:265` (within @`:164`),
  svc:`reverse_project_balance`@`growth_engine.py:339` (within @`:297`), and route
  `investment.py:187` (`employer_contribution_per_period`). The growth-engine internal calls and
  the `retirement_dashboard_service` / `year_end` sites are CONSUMERS that delegate to it.
  - **P1-f / 1.7.3 reconciliation (P2-d correction).** Raw 1.7.3 lists
    `calculate_paycheck`@`paycheck_calculator.py:92`,
    `compute_gap_data`@`retirement_dashboard_service.py:79`,
    `_project_retirement_accounts`@`:338`, `_compute_savings_progress`@
    `year_end_summary_service.py:887` as `employer_contribution` producers. `grep -n employer
    app/services/paycheck_calculator.py` returns ZERO -- `calculate_paycheck` does NOT compute
    employer_contribution (producer over-attribution per the 1.7.8 rule; removed). The three
    aggregation-layer functions are CONSUMERS (they call `growth_engine.calculate_employer_
    contribution` or sum its per-period output into `year_summary_employer_total`).
- **Consumer sites.** route:`dashboard`@`investment.py:63` (`employer_contribution_per_period`
  @`:187-189`). Templates: `investment/dashboard.html` (62). Aggregation consumers:
  `retirement_dashboard_service.compute_gap_data`@`:79`, `_compute_savings_progress`@
  `year_end_summary_service.py:887` (-> `year_summary_employer_total`).
- **Primary path.** `growth_engine.calculate_employer_contribution`@`growth_engine.py:91` is the
  single canonical producer; every other site delegates. **Not UNKNOWN.** (The 1.7.4 "7
  producers, Phase 3 checks producer multiplicity" concern is resolved: the multiplicity was an
  over-attribution; there is exactly one producer.)
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2. Although the producer is single, three
  delegating consumers feed user-facing figures and the analytical frame requires verifying they
  pass the same `(employer_params, employee_contribution, gross_biweekly)` triple. Exact pairs:
  - `investment.py:187` (`employer_contribution_per_period`, investment dashboard card) **vs**
    `growth_engine.project_balance`@`growth_engine.py:265` employer line (investment growth
    chart) -- same loan/account-on-date, same engine, verify identical inputs.
  - per-period `growth_engine.calculate_employer_contribution` **vs** the annual
    `year_summary_employer_total` (`_compute_savings_progress`@`year_end_summary_service.py:887`)
    -- annual total must equal the per-period sum over the year.
- **Prior-audit linkage.** PA-04/PA-05 (open): the `employer`/`assumed_return` magic-number
  fallbacks feed the same retirement-projection chain (`compute_gap_data` ->
  `_project_retirement_accounts` -> `calculate_employer_contribution`). PA-29
  (partially-remediated): growth-engine directional-only assertions cover this producer's
  match/flat branches.

---

## Concept: contribution_limit_remaining

- **Intended definition.** The unused portion of an investment account's annual contribution
  limit: `annual_contribution_limit - ytd_contributions` for the limit year. The pieces are
  produced by `calculate_investment_inputs`@`investment_projection.py:100` (which returns
  `annual_contribution_limit` passed through from `InvestmentParams` and the computed
  `ytd_contributions`); the **subtraction itself is route-resident** in `investment.dashboard`@
  `investment.py:173-181` (`limit_info`). Source: `calculate_investment_inputs` body
  `investment_projection.py:100-...` (read: returns `InvestmentInputs(periodic_contribution,
  annual_contribution_limit, ytd_contributions, ...)`); `InvestmentParams.annual_contribution_
  limit`@`investment_params.py:84`, `contribution_limit_year`@`:85`.
- **Units / type.** Decimal money (2dp).
- **Producer sites.** svc:`calculate_investment_inputs`@`investment_projection.py:100` -- produces
  the inputs (`ytd_contributions` arithmetic; `annual_contribution_limit` pass-through). The
  remaining = limit - ytd subtraction is **route-resident** at `investment.py:173-181` (no
  service producer for the final figure -- structurally identical to `transfer_amount_computed`).
  svc:`project_balance`@`growth_engine.py:164` consumes the limit to cap projected contributions
  (a CONSUMER/enforcer of the limit, not a producer of "remaining").
- **Consumer sites.** route:`dashboard`@`investment.py:63` (`limit_info.limit`/`.ytd`/remaining
  @`:173-181`). Templates: `investment/dashboard.html` (76).
- **Primary path.** Inputs canonical at `calculate_investment_inputs`@
  `investment_projection.py:100`; the final subtraction is intentionally route-resident
  (single consumer, the investment dashboard). **Not UNKNOWN.** Flagged as a Phase-6 SRP
  candidate (route-layer financial subtraction with one consumer) -- same class as Q-12's
  obligations inline aggregation; cross-link Q-12, no new question.
- **Multi-path flag.** 1.7.4 single-path (1 producer, 1 consumer). Source-read confirms single
  path; Phase 3 verifies only that `investment.py:173-181` computes `limit - ytd` against the
  same `calculate_investment_inputs` output rendered at `investment/dashboard.html:76` (no
  recomputation drift).
- **Prior-audit linkage.** none.

---

## Concept: ytd_contributions

- **Intended definition.** Year-to-date contributions into an investment account: the sum of
  shadow-income `estimated_amount` for transfer-linked contributions whose pay period falls in
  the contribution-limit year. Source: `calculate_investment_inputs` body
  `investment_projection.py:76-90` (read: `ytd_contributions += Decimal(str(t.estimated_amount))`
  for `t.pay_period_id in ytd_period_ids`). Stored inputs:
  `InvestmentParams.annual_contribution_limit`@`investment_params.py:84`,
  `contribution_limit_year`@`:85`.
- **Units / type.** Decimal money (raw sum; consumed against the limit -- column-precision at
  storage; not separately quantized in the helper, an A-01-acceptable raw-sum-into-display per
  inventory 1.7.6 "missing quantization" note).
- **Producer sites.** svc:`calculate_investment_inputs`@`investment_projection.py:100` -- sole
  producer (`ytd_contributions` accumulation @ the Step-4 block; reads `t.estimated_amount`
  directly -- an `effective_amount` bypass, see the consolidated bypass table under
  `effective_amount`, justified by the upstream `status.excludes_from_balance` filter @ Step 1).
  svc:`project_balance`@`growth_engine.py:164` consumes the limit/ytd to cap contributions
  (CONSUMER, not producer of ytd).
- **Consumer sites.** route:`dashboard`@`investment.py:63` (`limit_info.ytd` @`:173-181`).
  Templates: `investment/dashboard.html` (76).
- **Primary path.** `calculate_investment_inputs`@`investment_projection.py:100`. Single
  canonical producer; **not UNKNOWN.**
- **Multi-path flag.** 1.7.4 single-path. Phase 3 verifies the `estimated_amount` bypass is
  contract-safe (the caller always honors the upstream `status.excludes_from_balance` filter --
  inventory 1.1 bypass row `investment_projection.py:153,187` Phase-3 verdict candidate).
- **Prior-audit linkage.** none directly; cross-link the `effective_amount` bypass table (this
  is one of the 25 service-layer bypass sites).

---

## Concept: transfer_amount

- **Intended definition.** The dollar amount of a transfer between two accounts, stored on the
  `Transfer` row and mirrored onto exactly two shadow `Transaction` rows (one expense, one
  income) per Transfer Invariants 1-3. The displayed/effective transfer amount is
  `Transfer.effective_amount` (`0` for excluded status, else `Transfer.amount`). Source:
  `Transfer.amount`@`transfer.py:142`, `Transfer.effective_amount`@`transfer.py:174-182` (read);
  CLAUDE.md Transfer Invariants (verbatim in `00_priors.md:106-120`);
  `TransferTemplate.default_amount`@`transfer_template.py:60`.
- **Units / type.** Decimal money (2dp), `Numeric(12,2)`.
- **Producer sites.** svc:`create_transfer`@`transfer_service.py:283`;
  svc:`update_transfer`@`transfer_service.py:443`; svc:`restore_transfer`@`transfer_service.py:688`
  -- the ONLY authorized mutators (Invariant 4). svc:`generate_for_template`@
  `transfer_recurrence.py:43`, `regenerate_for_template`@`:141`, `resolve_conflicts`@`:224` --
  recurrence producers, all delegating the actual write to `transfer_service`.
  svc:`carry_forward_unpaid`@`carry_forward_service.py:291` -- moves the transfer (delegates the
  mutation to `transfer_service.update_transfer` per A-07 discrete branch).
  svc:`_compute_transfers_summary`@`year_end_summary_service.py:636` -- year-end display
  aggregation producer (`total_amount += t.amount`@`:679`; KEEP per 1.7.8, genuine `+=`). Stored:
  `Transfer.amount`@`transfer.py:142`.
- **Consumer sites.** route:`update_transfer`@`transfers.py:744`; `create_ad_hoc`@`:850`;
  `mark_done`@`:1055`; `cancel_transfer`@`:1118`; `delete_transfer`@`:1020`;
  `create_transfer_template`@`:127`; route:`year_end_tab`@`analytics.py:171`. Templates:
  `transfers/_transfer_cell.html` (21, 38), `transfers/list.html` (81, 146),
  `analytics/_year_end.html` (transfer_amount per destination).
- **Primary path.** `Transfer.amount` (the stored column) mutated EXCLUSIVELY through
  `transfer_service.create_transfer`/`update_transfer`/`restore_transfer`@
  `transfer_service.py:283,443,688` is the canonical source of truth; `Transfer.effective_amount`@
  `transfer.py:174-182` is the canonical read. Justified by Transfer Invariant 4. **Not UNKNOWN.**
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2 (8 producers). Pairs: stored
  `Transfer.amount` (cell render via `transfers/_transfer_cell.html`) **vs**
  `_compute_transfers_summary`@`year_end_summary_service.py:636` year-end per-destination total
  **vs** the shadow-transaction pair (`Transaction.effective_amount`) that the balance calculator
  actually sums -- Invariant 3 requires shadow amounts == parent; Phase 3 verifies the year-end
  transfers tab total reconciles with the shadow-derived figure for the same periods (a
  double-count risk because net worth is shadow-based and the transfers tab is `Transfer`-based;
  cross-link the budget.transfers classification table below and P2-a `period_subtotal`
  shadow-handling, NOT restated here).
- **Prior-audit linkage.** PA-25 (open): no zero-amount / self-transfer / max-amount boundary
  tests for transfer recurrence -- the recurrence producers (`generate_for_template`@
  `transfer_recurrence.py:43` etc.) feeding `transfer_amount` are the untested boundary surface.
  PA-08 (open): `carry_forward_service` lacks a `scenario_id` filter -- bears on the
  `carry_forward_unpaid`@`carry_forward_service.py:291` transfer-move producer if scenarios are
  enabled.

### Cross-cutting (P2-d demand): every `budget.transfers` table read, classified

CLAUDE.md Transfer Invariant 5 scopes specifically to the **balance calculator**: it must query
ONLY `budget.transactions`, never `budget.transfers`. `grep -rn
"query(Transfer)\|select(Transfer)\|Transfer\.query\|get(Transfer,\|from app.models.transfer
import\|db.session.query(Transfer)" app/` (run 2026-05-15) returns the sites below. Each is
classified LEGITIMATE (CRUD / recurrence template management / authorized mutator / archival /
display aggregation) vs VIOLATION (any balance computation touching `budget.transfers`).

| Site | Function / context | Classification |
| --- | --- | --- |
| `balance_calculator.py` (none; docstring `:17-19` only) | Calculator explicitly does NOT query Transfer | **INVARIANT 5 HOLDS** -- verified by grep: zero `query(Transfer)`/`from app.models.transfer` in the file |
| `transfer_service.py:195, 231, 780-792` | `get(Transfer,...)` / build shadow pair -- THE authorized mutator | LEGITIMATE (Invariant 4) |
| `transfer_recurrence.py:172, 257, 309` | recurrence generate/regenerate/resolve; writes delegated to `transfer_service` | LEGITIMATE (recurrence template management) |
| `carry_forward_service.py` (Transfer partition; move via `transfer_service.update_transfer`) | A-07 discrete-branch move | LEGITIMATE (CRUD via authorized mutator) |
| `routes/transfers.py:491, 546, 636, 668, 985, 1170` | transfer cell render / create / update / mark_done / cancel / delete | LEGITIMATE (CRUD; not balance computation) |
| `routes/transactions.py:282` | `get(Transfer, txn.transfer_id)` -- resolve parent transfer for a shadow cell render | LEGITIMATE (CRUD/display) |
| `routes/accounts.py:691` | account deletion: delete remaining `Transfer` rows via `transfer_service.delete_transfer` | LEGITIMATE (CRUD/cleanup via authorized mutator) |
| `utils/archive_helpers.py:57` | archival/retention sweep | LEGITIMATE (archival) |
| `year_end_summary_service.py:658` (`_compute_transfers_summary`) | year-end transfers-by-destination DISPLAY total (`total_amount += t.amount`@`:679`) | LEGITIMATE as display aggregation -- NOT the balance calculator, NOT a checking/account balance; **Phase 3 must still record + cross-check it does not double-count against the shadow-transaction-based `net_worth`/`account_balance`** |

No VIOLATION found: no balance-computation path queries `budget.transfers`; Invariant 5 is
satisfied. The single money aggregation over `budget.transfers` is the year-end transfers-summary
display, flagged for the Phase 3 double-count cross-check above (this is the Phase-3 input the
audit-plan section 3.1 transfer-read classification demands).

---

## Concept: effective_amount

- **Intended definition.** The single canonical amount a `Transaction` contributes to ANY
  balance/subtotal/aggregation context, defined by the `Transaction.effective_amount` property
  as a strict 4-tier rule: (1) `is_deleted` -> `Decimal("0")`; (2) `status.excludes_from_balance`
  (Credit, Cancelled) -> `Decimal("0")`; (3) `actual_amount` if `actual_amount is not None`;
  (4) else `estimated_amount`. The model docstring `transaction.py:223-236` (read in full)
  states this property "is the single source of truth." `Transfer.effective_amount`@
  `transfer.py:174-182` is the 2-tier transfer analogue (excluded-status -> `0`, else
  `Transfer.amount`). Source: `transaction.py:221-245`, `transfer.py:174-182` (both read);
  audit-plan section 3.1; `00_priors.md` E-rules.
- **Units / type.** Decimal money (2dp); property accessor, not stored.
- **Producer sites.** property:`Transaction.effective_amount`@`transaction.py:221-245` -- THE
  canonical producer. property:`Transfer.effective_amount`@`transfer.py:174-182` -- the transfer
  analogue. The seven hand-rolled mirrors and the entry/settle helpers listed in raw 1.7.3
  (`settle_from_entries`, `_entry_aware_amount`, `_sum_remaining`/`_sum_all`,
  `_compute_actual`/`_build_txn_variance`, `mark_as_credit` `:229`, `_compute_mortgage_interest`/
  `_compute_spending_by_category`/`_compute_entry_breakdowns`, `_create_payback`,
  `_settle_source_and_roll_leftover`) are **bypass sites**, not independent producers of the
  concept -- per 1.7.8 a function that re-selects `actual_amount`/`estimated_amount` without the
  full 4-tier rule is a CONSUMER that reimplements the property, and the genuine concept producer
  remains the property. `budget_variance_service._compute_actual`@`:381` is one of the six P1-f
  relocations (CONSUMER, confirmed).
- **Consumer sites.** Every cell-render route in 1.2 Group A: `get_cell`@`transactions.py:244`;
  `update_transaction`@`:304`; `mark_done`@`:491`; `mark_credit`@`:632`; `unmark_credit`@`:675`;
  `cancel_transaction`@`:713`; `create_inline`@`:909`; `create_transaction`@`:987`;
  `get_cell`@`transfers.py:698`; `update_transfer`@`:744`; `create_ad_hoc`@`:850`;
  `mark_done`@`:1055`; `cancel_transfer`@`:1118`; `mark_paid`@`dashboard.py:54` (Q-14);
  `calendar_tab`@`analytics.py:107`; `variance_tab`@`:205`; `trends_tab`@`:272`;
  `summary`@`obligations.py:259`. Templates: `grid/_transaction_cell.html` (17, 33),
  `grid/_transaction_entries.html` (92), `transfers/_transfer_cell.html` (21),
  `dashboard/_bill_row.html` (33-34, 38), `analytics/_calendar_month.html` (95),
  `obligations/summary.html` (100, 149, 195), `templates/list.html` (99, 182),
  `transfers/list.html` (81, 146). Aggregates: `func.sum(TransactionEntry.amount)`@
  `year_end_summary_service.py:519`; conditional credit-entry `case(...)` sum @`:520-528`.
- **Primary path.** `Transaction.effective_amount`@`transaction.py:221-245` is the explicitly
  designated single source of truth (docstring `:231`); `Transfer.effective_amount`@
  `transfer.py:174-182` for transfers. **Not UNKNOWN.** The Phase-3 concern is not "which is
  canonical" (the property is) but "which direct reads bypass it and can the omitted tiers
  apply" -- the consolidated table below is that Phase-3 input.
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-1. Audit-plan section 3.1 mandates listing
  every direct `actual_amount`/`estimated_amount` read that bypasses the property, across
  services/routes/templates/JS. Consolidated below (exhaustive: inventory 1.1 cross-cutting
  table `01_inventory.md:1595-1620` + a P2-d `grep -rn "\.actual_amount\|\.estimated_amount"
  app/routes app/templates app/static/js` run 2026-05-15). Each row's Phase-3 question is
  whether the omitted tiers (is_deleted, status.excludes_from_balance, the
  `actual_amount is not None` vs truthiness distinction) can apply at that site.

#### Consolidated effective_amount bypass table (Phase-3 input -- exhaustive)

Services (1.1) -- 25 sites across 12 files, from inventory `01_inventory.md:1597-1617`
(reproduced as the Phase-3 input; verdict-candidates are the inventory's):

| file:line | Function | Bypass | Phase-3 verdict candidate |
| --- | --- | --- | --- |
| `balance_calculator.py:292` (374-378,384-385) | `_entry_aware_amount` | reads entries `amount` + `estimated_amount` for cleared-entry checking impact | AGREE (intentional); verify cleared/uncleared partition vs property for non-entry shapes |
| `dashboard_service.py:239` | `_entry_progress_fields` via `compute_remaining` | `estimated_amount` for remaining | **Q-08** (settled txn: actual vs estimated base) |
| `dashboard_service.py:245` | `_entry_progress_fields` | over-budget vs `estimated_amount` | **Q-08** |
| `dashboard_service.py:350` | `_get_balance_info` | reads `Account.current_anchor_balance` | Phase 4 (source-of-truth) |
| `savings_dashboard_service.py:373` | `_compute_account_projections` | `proj.current_balance` (ARM == stored per A-04) | AGREE per A-04 dual; verify all 6 principal pages |
| `retirement_dashboard_service.py:405,441-442` | `_project_retirement_accounts` | `acct.current_anchor_balance` | Phase 4 |
| `year_end_summary_service.py:519,527` | `_compute_entry_breakdowns` SQL | `func.sum(TransactionEntry.amount)` + credit `case` | AGREE (entry-aware envelope sum) |
| `year_end_summary_service.py:1123,1124,1244,1784,1806,1861,2096` | investment/interest/anchor helpers | `account.current_anchor_balance` | Phase 4 |
| `year_end_summary_service.py:1465-1469` | `_balance_from_schedule_at_date` (encl. `_generate_debt_schedules`@`:1421`, P1-f attribution fix) | ARM anchor `LoanParams.current_principal` | AGREE per A-04 (P1-f CONSUMER relocation) |
| `budget_variance_service.py:390-393` | `_compute_actual`/`_build_txn_variance` | hand-rolled `actual if status.is_settled and not None else estimated` | **P1-f CONSUMER relocation**; Phase 3 compare to `transaction.py:238-245` for all is_deleted/excludes_from_balance combos |
| `calendar_service.py:483` | `_compute_month_end_balance` | `account.current_anchor_balance` | Phase 4 |
| `transaction_service.py:153` | `settle_from_entries` | `compute_actual_from_entries(entries)` | AGREE (entries sub-transactional) |
| `carry_forward_service.py:586,878` | `_build_envelope_plan`/`_settle_source_and_roll_leftover` | `compute_actual_from_entries` | AGREE per A-02 |
| `credit_workflow.py:229` | `mark_as_credit` | `actual_amount if not None else estimated_amount` (hand-rolled mirror) | Phase 3 verify omitted tiers (is_deleted, excludes_from_balance) cannot apply here |
| `entry_credit_workflow.py:112-114` | `sync_entry_payback` | `sum(e.amount ...)` | AGREE (entries sub-transactional) |
| `entry_service.py:70` | `_update_actual_if_paid` | `compute_actual_from_entries` | AGREE |
| `entry_service.py:365,367,395,397-399,424,446` | entry-sum helpers | `entry.amount` direct | AGREE (entries sub-transactional) |
| `investment_projection.py:153,187` | `calculate_investment_inputs` | `t.estimated_amount` (shadow income; see `ytd_contributions`) | Phase 3 verify upstream `status.excludes_from_balance` filter always honored by callers |

Routes (1.2) -- direct reads (P2-d grep):

| file:line | Function | Bypass | Phase-3 verdict candidate |
| --- | --- | --- | --- |
| `routes/companion.py:52` | `_build_entry_data` | `compute_remaining(txn.estimated_amount, txn.entries)` | CONSUMER (entry-progress); Q-08 family |
| `routes/companion.py:54-55` | `_build_entry_data` | `float(total / txn.estimated_amount * 100)` inline pct | CONSUMER; also a float-on-money + route-arithmetic concern (cross-link `goal_progress`) |
| `routes/entries.py:105` | `_render_entry_list` | `compute_remaining(txn.estimated_amount, entries)` | CONSUMER (entry-progress); Q-08 family |
| `routes/dashboard.py:128` | `mark_paid` | `txn.actual_amount = actual_amount` (WRITE, not read) | Q-14: `mark_paid` vs `mark_done` actual-write divergence (already in `09_open_questions.md` A-14) |
| `routes/transactions.py:614` | `mark_done` | `txn.actual_amount = actual_amount` (WRITE) | Q-14 counterpart write path |

Templates (1.3) -- hand-rolled effective_amount mirror + Jinja arithmetic (coding-standards
"templates are for display, not computation" violations; bypass tiers 1+2 omitted):

| file:line | Bypass | Phase-3 verdict candidate |
| --- | --- | --- |
| `grid/_transaction_cell.html:17` | `display_amount = t.actual_amount if t.actual_amount is not none else t.estimated_amount` -- 2-tier hand-rolled mirror (omits is_deleted + excludes_from_balance) | Phase 3: cell render can show a non-zero amount for a Credit/Cancelled/soft-deleted txn the property would zero -- verify the cell's status gating compensates |
| `grid/_transaction_cell.html:21` | `remaining = t.estimated_amount - es.total` (Jinja arithmetic) | coding-standards violation; cross-link `entry_remaining` orphan / Q-08 |
| `grid/_transaction_cell.html:33` | aria-label `estimated_amount` reads | display-only; same mirror class |
| `grid/_mobile_grid.html:92,179` | `display_amount = txn.actual_amount if ... is not none else txn.estimated_amount` (mirror x2) | same as `_transaction_cell.html:17` |
| `grid/_mobile_grid.html:96,183` | `remaining = txn.estimated_amount - es.total` (Jinja arithmetic x2) | coding-standards violation |
| `grid/_mobile_grid.html:103,190` | aria-label `estimated_amount` reads | display-only |
| `grid/_carry_forward_preview_modal.html:139,143,147` | `plan.transaction.estimated_amount` display reads | preview display; verify matches the settled `effective_amount` post-carry-forward (A-02) |

(Form-input bindings `name="estimated_amount" value="{{ txn.estimated_amount }}"` in
`_transaction_full_edit.html`/`_transaction_full_create.html`/`_transaction_quick_edit.html`/
`_transaction_quick_create.html`/`grid.html:324` are NOT bypasses -- they edit the raw column
by design, correctly.)

JS (1.4): **NONE.** `grep -rn "actual_amount\|estimated_amount\|effective" app/static/js/`
returns only `effective_month`/`effective_year`/`inflation_effective_month` form-field names
(unrelated to `effective_amount`). Consistent with coding-standards "monetary values in JS are
display-only"; the inventory 1.4 found no `effective_amount` JS recomputation.

**Bypass total: 25 service + 5 route + ~13 template + 0 JS = ~43 sites.** None computes a
checking-account balance from raw transaction data bypassing the property (which would be the
true Invariant violation per inventory `01_inventory.md:1618-1620`); the highest-risk rows are
the hand-rolled mirrors (`budget_variance_service:390-393`, `credit_workflow.py:229`,
`_transaction_cell.html:17`, `_mobile_grid.html:92,179`) that omit tiers 1-2 and the Q-08
entry-progress reads.

- **Prior-audit linkage.** none specific; the bypass surface is the structural substrate Phase 3
  (consistency) and Phase 6 (DRY -- "effective-amount selection reproduced inline") consume.

---

## Concept: goal_progress

- **Intended definition.** Token-overloaded (flagged discrepancy, NOT reconciled -- Phase 3
  input). Two distinct quantities share the token:
  - **GP1 savings-goal completion** -- progress of a `SavingsGoal` toward its resolved dollar
    target: `resolve_goal_target` (fixed `target_amount` or income-relative
    `multiplier * net_biweekly`), `calculate_required_contribution`
    (`(target - current)/remaining_periods`), `calculate_trajectory` (ETA / on-track). Source:
    `savings_goal_service.py:21-40,109-138,331-345` (read).
  - **GP2 entry-tracked spend progress** -- a single transaction's entry total vs its
    `estimated_amount`: `pct = total / estimated_amount * 100`. Source:
    `dashboard_service._entry_progress_fields`@`:203` (genuine `total = debit + credit`@`:238`,
    KEEP per 1.7.8); `routes/companion.py:53-56` inline `float(total / txn.estimated_amount *
    100)`. This is the **Q-08** anchor (estimated vs actual base for settled txns).
- **Units / type.** GP1: Decimal money (target/required-contribution) + a percentage/ETA. GP2:
  percentage (float at `companion.py:54` -- a float-on-money concern). Mixed units = part of the
  discrepancy.
- **Producer sites.** GP1: svc:`resolve_goal_target`@`savings_goal_service.py:21`,
  `calculate_required_contribution`@`:109` (quantize `Decimal("0.01")` HALF_UP @`:135` region),
  `calculate_trajectory`@`:331`. GP2: svc:`_entry_progress_fields`@`dashboard_service.py:203`
  (KEEP), helper:`_build_entry_data`@`companion.py:28-64` (`pct` inline @`:53-56`).
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_dashboard_data`@
    `savings_dashboard_service.py:61` as a `goal_progress` producer; it is an
    assembler/delegator -> CONSUMER (the GP1 arithmetic is `savings_goal_service`'s).
- **Consumer sites.** route:`dashboard`@`savings.py:107`; `gap_analysis`@`retirement.py:301`;
  `index`@`companion.py:80`; `mark_paid`@`dashboard.py:54` (Q-14). Templates:
  `dashboard/_savings_goals.html` (13, 20), `savings/dashboard.html` (374-380, 402),
  `retirement/_gap_analysis.html`, `companion/_transaction_card.html` (33).
- **Primary path.** Per sub-concept (token overload is the reason there is no single owner --
  same treatment as `period_subtotal`/`growth`): GP1 -> `savings_goal_service`
  (`resolve_goal_target`/`calculate_required_contribution`/`calculate_trajectory`); GP2 ->
  `dashboard_service._entry_progress_fields`@`:203` with the estimated-vs-actual base governed by
  **Q-08** (`companion.py:53-56` is a duplicate inline of GP2 -- Phase 6 DRY candidate). Each
  sub-concept has a clear canonical producer; **not UNKNOWN**, but the **token overload + the
  Q-08 base ambiguity is a DEFINITION discrepancy/open question Phase 3 must split.** No new Q
  (Q-08 governs GP2; GP1 is unambiguous).
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.4 Tier-2. Pairs: GP2 `_entry_progress_fields`@
  `dashboard_service.py:203` **vs** `companion.py:53-56` inline pct (same quantity, two
  implementations -- verify identical and resolve Q-08 base consistently); GP1
  `savings_goal_service.calculate_required_contribution`@`:109` **vs** its `/savings` vs
  dashboard-widget vs retirement-gap render (same goal, three pages). DEFINITION split: GP1 and
  GP2 must not be compared numerically.
- **Prior-audit linkage.** none. (Q-08 cross-link is an open question, not a PA-*.)

---

## Concept: emergency_fund_coverage_months

- **Intended definition.** How many months a savings balance would cover average monthly
  expenses: `savings_balance / average_monthly_expenses`, quantized to one decimal; plus derived
  `paychecks_covered` (`months * 26/12`) and `years_covered` (`months / 12`). Zero/None expenses
  -> zeros. Source: `calculate_savings_metrics` docstring + body
  `savings_goal_service.py:139-175` (read in full).
- **Units / type.** Decimal **months** (0.1 precision, `ROUND_HALF_UP`; a coverage-duration
  ratio, NOT a money value -- A-01 is money-scoped, so 0.1 here is not an A-01 violation).
- **Producer sites.** svc:`calculate_savings_metrics`@`savings_goal_service.py:139` -- sole
  producer (`months = (savings_balance/avg_expenses).quantize(Decimal("0.1"),HALF_UP)`@`:162`).
  - **P1-f reconciliation.** Raw 1.7.3 lists `compute_dashboard_data`@
    `savings_dashboard_service.py:61` "(delegates)" -- CONSUMER, not a producer (confirmed by the
    "(delegates)" annotation; the arithmetic is `calculate_savings_metrics`'s).
- **Consumer sites.** route:`dashboard`@`savings.py:107`. Templates: `savings/dashboard.html`
  (298, 304, 310).
- **Primary path.** `calculate_savings_metrics`@`savings_goal_service.py:139`. Single canonical
  producer; **not UNKNOWN.**
- **Multi-path flag.** 1.7.4 single-path. Distinct from `cash_runway_days` (see that entry's
  fold decision); Phase 3 verifies the three derived figures (`months/paychecks/years_covered`)
  are internally consistent and the `26/12` factor @`:168` matches the other two duplications
  (`savings_goal_service.py:17-18`, `savings_dashboard_service.py:170-172,765`; Q-12 / P2-a /
  P2-c cross-link, no restatement).
- **Prior-audit linkage.** none.

---

## Concept: cash_runway_days

- **Intended definition.** Days the checking `current_balance` lasts at the trailing-30-day
  settled-expense burn rate: `current_balance / (sum(abs(effective_amount) of last-30-day
  DONE/RECEIVED/SETTLED expenses) / 30)`. `None` if no recent spending (avoids infinity), `0`
  if balance <= 0. Source: `_compute_cash_runway` docstring + body
  `dashboard_service.py:375-417` (read in full). 1.7.2 P1-b addition; the deferred
  fold-into-`emergency_fund_coverage_months` decision is **RESOLVED here: NOT folded** (different
  balance base [checking vs savings], lookback [trailing-30-day settled vs avg monthly], and unit
  [days vs months]).
- **Units / type.** Integer **days** (`int(runway.quantize(Decimal("1"),HALF_UP))`@`:416`). Uses
  `txn.effective_amount`@`:411` correctly (NOT a bypass -- the property is used).
- **Producer sites.** svc:`_compute_cash_runway`@`dashboard_service.py:375` -- sole producer.
  - **P1-f note.** `_get_balance_info`@`dashboard_service.py:334` (raw 1.7.3 co-listed) is a P1-f
    CONSUMER relocation (delegates to `_compute_cash_runway` + reads balance); not a producer.
- **Consumer sites.** route:`page`@`dashboard.py:40`; route:`balance_section`@`dashboard.py:160`.
  Templates: `dashboard/_balance_runway.html` (21).
- **Primary path.** `_compute_cash_runway`@`dashboard_service.py:375`. Single canonical producer;
  **not UNKNOWN.**
- **Multi-path flag.** 1.7.4 single-path. Phase 3 verifies the `current_balance` input to
  `_compute_cash_runway`@`dashboard_service.py:375` equals the same period's `checking_balance`
  (P2-a; `calculate_balances`@`balance_calculator.py:35`) the rest of the dashboard shows at
  `dashboard/_balance_runway.html` (the runway is only correct if its balance base agrees with
  the balance card on the same page).
- **Prior-audit linkage.** none.

---

## Concept: transfer_amount_computed

- **Intended definition.** A route-resident PRE-FILL transfer amount distinct from the stored
  `transfer_amount`: when the user does not override, the loan-payment dialog defaults it to
  P&I + escrow and the investment-contribution dialog defaults it to
  `annual_contribution_limit/26` with a `$500` fallback. Source: 1.7.2 (P1-c definition);
  `routes/loan.py:1213-1241` (`create_payment_transfer`), `routes/investment.py:668-670`
  (`create_contribution_transfer`). Deferred to P2-d by P2-b (`02_concepts.md:546`).
- **Units / type.** Decimal money (2dp), form pre-fill value (becomes `Transfer.amount` only if
  the user accepts it -- it is NOT a stored column).
- **Producer sites.** NONE in the service layer (by design -- route-resident derivation).
  route-inline: `loan.create_payment_transfer`@`loan.py:1213-1241` (P&I from
  `compute_contractual_pi`/`get_loan_projection` + `escrow_calculator.calculate_total_payment`);
  `investment.create_contribution_transfer`@`investment.py:668-670` (limit/26, $500 fallback).
- **Consumer sites.** Only the two cited routes' Transfer-creation form pre-fill; no template
  renders this token as a distinct displayed value (it populates an input the user can edit).
- **Primary path.** Route-resident BY DESIGN; there is no service producer and the inventory
  1.7.3 explicitly classifies this as the only "consumers but no producers" token, intentional.
  **Not UNKNOWN.** Flagged as the canonical Phase-6 SRP example (route-layer financial
  derivation) -- this is exactly what Q-12 raises (obligations/route-resident aggregation an SRP
  candidate); cross-link Q-12, **no new question.**
- **Multi-path flag.** 1.7.4 single-path (route-resident, no producer). Phase 3 verifies the
  loan pre-fill (`loan.py:1213-1241`) uses the same `monthly_payment`/`escrow_per_period` the
  loan dashboard shows (cross-link P2-b `monthly_payment`, `escrow_per_period`) and the
  investment pre-fill's `limit/26` matches `calculate_investment_inputs`'
  `annual_contribution_limit` (cross-link `contribution_limit_remaining`).
- **Prior-audit linkage.** none.

---

## Concept: pension_benefit_annual

- **Intended definition.** Projected annual defined-benefit pension:
  `benefit_multiplier * years_of_service * high_salary_average`, where the average is over the N
  consecutive highest salary years. `0` when no salary history. Source: `calculate_benefit`
  docstring + body `pension_calculator.py:31-75` (read in full); stored inputs
  `PensionProfile.benefit_multiplier`@`pension_profile.py:78`, `consecutive_high_years`@`:79`.
- **Units / type.** Decimal money (2dp), `ROUND_HALF_UP` (`.quantize(TWO_PLACES, ROUND_HALF_UP)`@
  `pension_calculator.py:63`).
- **Producer sites.** svc:`calculate_benefit`@`pension_calculator.py:31` -- sole producer
  (`annual_benefit`@`:61-63`). 1.7.8 LOW-RISK engine; not a P1-f relocation.
- **Consumer sites.** route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@
  `retirement.py:301`. Templates: `retirement/dashboard.html` (111).
- **Primary path.** `calculate_benefit`@`pension_calculator.py:31`. Single canonical producer;
  **not UNKNOWN.**
- **Multi-path flag.** 1.7.4 single-path. Phase 3 verifies `years_of_service` and the
  high-salary average feed consistently from `project_salaries_by_year`@`pension_calculator.py:78`
  (which itself defers raise logic to `paycheck_calculator._apply_raises` -- cross-link P2-c
  `paycheck_gross` raise-sequencing).
- **Prior-audit linkage.** PA-30 (partially-remediated): two pension-calculator tests use
  directional assertions instead of exact values -- this producer's `annual_benefit` precision
  is the unverified surface.

---

## Concept: pension_benefit_monthly

- **Intended definition.** `pension_benefit_annual / 12`. Source: `calculate_benefit` body
  `pension_calculator.py:65-67` (read: `monthly_benefit = (annual_benefit /
  12).quantize(TWO_PLACES, ROUND_HALF_UP)`).
- **Units / type.** Decimal money (2dp), `ROUND_HALF_UP`.
- **Producer sites.** svc:`calculate_benefit`@`pension_calculator.py:31` -- sole producer
  (`monthly_benefit`@`:65-67`). svc:`compute_gap_data`@`retirement_dashboard_service.py:79` and
  svc:`calculate_gap`@`retirement_gap_calculator.py:37` consume `monthly_pension_income` as an
  input (CONSUMER pass-throughs per 1.7.8, not re-producers).
- **Consumer sites.** route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@
  `retirement.py:301`. Templates: `retirement/dashboard.html` (118),
  `retirement/_gap_analysis.html` (15, 22). JS: `retirement_gap_chart.js` (data-pension attr at
  `_gap_analysis.html:81-85` -- display-only, E-17).
- **Primary path.** `calculate_benefit`@`pension_calculator.py:31`; the retirement-gap services
  are consumers that thread the monthly figure into the gap calc. **Not UNKNOWN.**
- **Multi-path flag.** 1.7.4 single-path. Phase 3 verifies `retirement_gap_calculator.py:37` and
  `compute_gap_data`@`retirement_dashboard_service.py:79` pass through the SAME
  `pension_benefit_monthly` value `retirement/dashboard.html:118` renders (no recompute).
- **Prior-audit linkage.** PA-30 (partially-remediated): same directional-assertion gap as
  `pension_benefit_annual`.

---

## Concept: year_summary_jan1_balance

- **Intended definition.** The Jan 1 anchor of the year-end net-worth trend: total assets minus
  liabilities at the first monthly endpoint of the calendar year. It is the `jan1` field of
  `_compute_net_worth` (cross-reference P2-a `net_worth` `02_concepts.md:386-426`, NOT restated).
  Source: `_compute_net_worth`@`year_end_summary_service.py:689` (P2-a cataloged).
- **Units / type.** Decimal money (2dp).
- **Producer sites.** svc:`_compute_net_worth`@`year_end_summary_service.py:689` -- sole producer
  (the `jan1` endpoint of the 12-month trend; `delta = dec31 - jan1`@`:746`). Same producer and
  same per-account dispatch as P2-a `net_worth`/`account_balance`.
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Templates: `analytics/_year_end.html`
  (`data.net_worth.jan1`).
- **Primary path.** `_compute_net_worth`@`year_end_summary_service.py:689` (the canonical
  net-worth producer per P2-a). Per-account-type primary is clear; the cross-account dispatcher
  is the same `_get_account_balance_map` vs `_compute_account_projections` question P2-a filed as
  **Q-15**. **Not UNKNOWN** (P2-a established net_worth has a clear per-account-type primary;
  only the dispatcher is Q-15). Cross-link Q-15; **no duplicate.**
- **Multi-path flag: PHASE 3 REQUIRED.** Inherits P2-a `net_worth`'s PHASE 3 REQUIRED. Specific
  pair: the Jan 1 endpoint via `_get_account_balance_map`/`_build_account_data`@
  `year_end_summary_service.py:750` (net-worth dispatch, inside `_compute_net_worth`@`:689`)
  **vs** the same account's Jan-1 balance via `_compute_account_projections`@
  `savings_dashboard_service.py:294` (savings-progress dispatch) -- W-152/W-159 require identical
  paths; Q-15.
- **Prior-audit linkage.** PA-26 (open): chart-data service has no value verification (the
  net-worth trend feeds `chart_year_end.js`). Cross-link A-06 only indirectly (net worth is not
  the mortgage-interest pipeline).

---

## Concept: year_summary_dec31_balance

- **Intended definition.** The Dec 31 endpoint of the year-end net-worth trend (the `dec31`
  field of `_compute_net_worth`; `delta = dec31 - jan1`). Cross-reference P2-a `net_worth`, NOT
  restated. Source: `_compute_net_worth`@`year_end_summary_service.py:689`.
- **Units / type.** Decimal money (2dp).
- **Producer sites.** svc:`_compute_net_worth`@`year_end_summary_service.py:689` -- sole producer
  (`dec31` endpoint). Identical producer/dispatch to `year_summary_jan1_balance`.
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Templates:
  `analytics/_year_end.html` (`data.net_worth.dec31`).
- **Primary path.** `_compute_net_worth`@`year_end_summary_service.py:689`; per-account-type
  primary clear, cross-account dispatcher = **Q-15**. **Not UNKNOWN.** Cross-link Q-15.
- **Multi-path flag: PHASE 3 REQUIRED.** Inherits P2-a `net_worth`. Specific pairs (W-159/W-152
  net_worth_amort): Dec 31 net-worth investment balance MUST equal Dec 31 savings-progress
  investment balance -- `_get_account_balance_map`@`year_end_summary_service.py:750` (net-worth,
  inside `_compute_net_worth`@`:689`) **vs** `_project_investment_for_year` (savings progress,
  via `_compute_savings_progress`@`year_end_summary_service.py:887`) for the same account; Q-15.
- **Prior-audit linkage.** PA-26 (open): same chart-value-verification gap as
  `year_summary_jan1_balance`.

---

## Concept: year_summary_principal_paid

- **Intended definition.** Principal paid down on each debt account during the calendar year:
  `jan1_balance - dec31_balance`, where each is the amortization-schedule balance at Dec 31 of
  the prior year and of the target year (so a Jan 1 payment is not double-counted in the start).
  Source: `_compute_debt_progress` docstring + body `year_end_summary_service.py:824-882` (read
  in full); `principal_paid = jan1_bal - dec31_bal`@`:871`.
- **Units / type.** Decimal money (raw subtraction of two `Numeric(12,2)` schedule balances;
  display-bound; not separately quantized -- the two operands are already 2dp).
- **Producer sites.** svc:`_compute_debt_progress`@`year_end_summary_service.py:824` -- sole
  producer (the `jan1 - dec31` subtraction; KEEP per 1.7.8 -- genuine arithmetic). It calls
  `_balance_from_schedule_at_date`@`:1465-1469` (the P1-f-relocated ARM anchor, enclosing def
  `_generate_debt_schedules`@`:1421`) -- that helper is a CONSUMER (A-04 anchor read), the
  subtraction here is the producer.
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Templates:
  `analytics/_year_end.html` (debt section, `year_summary_principal_paid`).
- **Primary path.** `_compute_debt_progress`@`year_end_summary_service.py:824`, anchored on the
  amortization schedule (A-04 dual policy: ARM uses stored `current_principal`, fixed uses
  engine-walked -- cross-link P2-b `loan_principal_real`/`loan_principal_stored`, symptom #3
  missing-update-path). Single producer; **not UNKNOWN.**
- **Multi-path flag: PHASE 3 REQUIRED** (analytical frame: aggregation-layer producer of a
  loan-derived figure). Pair: `_compute_debt_progress`@`year_end_summary_service.py:824`
  (`jan1-dec31` schedule deltas @`:871`) **vs** the loan dashboard's `principal_paid_per_period`
  from `generate_schedule`@`amortization_engine.py:326` (and
  `calculate_balances_with_amortization`@`balance_calculator.py:176`) summed over the same year;
  cross-link P2-b `principal_paid_per_period` -- the year-end principal-paid total must equal the
  per-period schedule sum for the same loan.
- **Prior-audit linkage.** A-06 (year-end mortgage-interest pipeline) is the sibling aggregation
  (`_compute_mortgage_interest`@`:380`); `year_summary_principal_paid` is the principal analogue
  on the same `_generate_debt_schedules` source -- Phase 3 verifies the schedule is the
  A-06-preprocessed one. PA-12/PA-27 (debt/amortization directional-only assertions): the
  schedule deltas feeding this are precision-unverified.

---

## Concept: year_summary_growth

- **Intended definition.** Token-overloaded year-end "growth/return" aggregate (flagged
  discrepancy, NOT reconciled). Distinct quantities under one token:
  - **YG1 investment growth/return for the year** -- `_compute_savings_progress`@
    `year_end_summary_service.py:887` -> `_project_investment_for_year` `growth_total`.
  - **YG2 interest-account growth for the year** -- `_compute_interest_for_year`@`:1207`
    (+ `_compute_pre_anchor_interest`), threads `interest_projection.calculate_interest`
    (cross-link `apy_interest`).
  - **YG3 mortgage interest paid for the year** -- `_compute_mortgage_interest`@`:380` (a
    debt-side "interest growth"; A-06 pipeline).
  Source: the three function docstrings/bodies (`:887`, `:1207`, `:380`); read-confirmed dispatch
  in `_compute_savings_progress`@`:887-960` (investment vs interest vs plain branches).
- **Units / type.** Decimal money (2dp); the engines quantize HALF_UP (`growth_engine`,
  `interest_projection`); the year-end sums are raw aggregates of already-2dp values.
- **Producer sites.** svc:`_compute_savings_progress`@`year_end_summary_service.py:887` (YG1
  dispatch -> `_project_investment_for_year`); svc:`_compute_interest_for_year`@`:1207` (YG2);
  svc:`_compute_mortgage_interest`@`:380` (YG3). Raw 1.7.3 also lists `_compute_debt_progress`@
  `:824` under `year_summary_growth`; that produces `year_summary_principal_paid` (principal, not
  growth) -- reclassified: w.r.t. `year_summary_growth` it is NOT a producer (1.7.8 rule; the
  growth-token attribution there is over-broad). All three real producers are HIGH-RISK
  aggregation-layer KEEPs (none in the six P1-f relocations).
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Templates:
  `analytics/_year_end.html` (savings + debt sections).
- **Primary path.** Per sub-concept (token overload -> per-quantity owner, `chart_balance_series`
  treatment): YG1 -> `_project_investment_for_year` (via `_compute_savings_progress`@`:887`),
  which is an aggregation-layer consumer of `growth_engine` -- canonical engine is
  `growth_engine.project_balance`; YG2 -> `_compute_interest_for_year`@`:1207` (engine
  `interest_projection.calculate_interest`); YG3 -> `_compute_mortgage_interest`@`:380` (engine
  `amortization_engine` schedule, A-06). Each has a clear canonical engine; **not UNKNOWN**, but
  the **token overload is a DEFINITION discrepancy Phase 3 must split.**
- **Multi-path flag: PHASE 3 REQUIRED** (analytical frame: every YG producer is
  aggregation-layer). Pairs: YG1 `_project_investment_for_year` (via `_compute_savings_progress`@
  `year_end_summary_service.py:887`) annual total **vs** `growth_engine.project_balance`@
  `growth_engine.py:164` per-period growth sum over the year (W-151/W-159, cross-link `growth`
  G4 / P2-a `net_worth`, Q-15); YG2 `_compute_interest_for_year`@`year_end_summary_service.py:1207`
  **vs** the sum of `calculate_interest`@`interest_projection.py:49` per period (PA-19,
  cross-link `apy_interest`); YG3 `_compute_mortgage_interest`@`year_end_summary_service.py:380`
  **vs** the loan schedule interest rows from `generate_schedule`@`amortization_engine.py:326`
  (A-06; cross-link P2-b `interest_paid_per_period`/`total_interest`). DEFINITION split:
  YG1/YG2/YG3 must not be summed into one "growth" without separation.
- **Prior-audit linkage.** A-06 (YG3 pipeline). PA-06/PA-17/PA-19 (YG2 interest precision /
  leap-year, cross-link `apy_interest`). PA-29 (YG1 growth-engine directional-only assertions).

---

## Concept: year_summary_employer_total

- **Intended definition.** Total employer retirement contributions credited to an investment
  account during the calendar year: the per-period `employer_contribution`
  (`growth_engine.calculate_employer_contribution`) summed over the year's periods, surfaced as
  the `employer_contributions` field of `_compute_savings_progress`'s per-account dict. Source:
  `_compute_savings_progress` docstring + body `year_end_summary_service.py:887-960` (read:
  investment branch -> `_project_investment_for_year(...) -> employer_total`; non-investment
  branches set `employer_total = ZERO`@`:953`).
- **Units / type.** Decimal money (2dp); sum of per-period HALF_UP-quantized
  `employer_contribution` values.
- **Producer sites.** svc:`_compute_savings_progress`@`year_end_summary_service.py:887` (sole
  surfacing producer; delegates per-period employer math to
  `growth_engine.calculate_employer_contribution` via `_project_investment_for_year`). The
  per-period engine is the canonical `employer_contribution` producer (cross-link that entry).
- **Consumer sites.** route:`year_end_tab`@`analytics.py:171`. Templates:
  `analytics/_year_end.html` (savings section).
- **Primary path.** `_compute_savings_progress`@`year_end_summary_service.py:887` for the annual
  total, canonically derived from `growth_engine.calculate_employer_contribution`@
  `growth_engine.py:91` per period. **Not UNKNOWN.**
- **Multi-path flag: PHASE 3 REQUIRED** (analytical frame: aggregation-layer total of a
  multi-consumer engine concept). Pair: `year_summary_employer_total` annual figure from
  `_compute_savings_progress`@`year_end_summary_service.py:887` (via
  `_project_investment_for_year`) **vs** the sum of per-period
  `growth_engine.calculate_employer_contribution`@`growth_engine.py:91` over the same year for
  the same account (must reconcile); cross-link `employer_contribution`, `growth` YG1, Q-15
  (same dispatcher question for the investment balance the employer total accompanies).
- **Prior-audit linkage.** PA-04/PA-05 (open): the employer/assumed-return magic-number
  fallbacks feed `_project_investment_for_year`. PA-29 (partially-remediated): growth-engine
  directional-only assertions cover the employer-match branches summed here.

### Phase 2 token completeness checklist

Maps every audit-plan Appendix A controlled-vocabulary token AND every inventory 1.7.2
vocabulary addition to the P2 family session that cataloged it. P2-d owns this (last family
session) so P2-reconcile's section-2 completeness gate is mechanical.

Appendix A tokens (41 + the `loan_principal_displayed` orphan = 42; 1.7.1 count):

| Token | Cataloged by | Note |
| --- | --- | --- |
| `checking_balance` | P2-a | |
| `account_balance` | P2-a | Q-15 |
| `projected_end_balance` | P2-a | |
| `period_subtotal` | P2-a | UNKNOWN (Q-10) |
| `chart_balance_series` | P2-a | |
| `net_worth` | P2-a | |
| `savings_total` | P2-a | |
| `debt_total` | P2-a | UNKNOWN (Q-15) |
| `loan_principal_real` | P2-b | |
| `loan_principal_stored` | P2-b | |
| `loan_principal_displayed` | P2-b | UNKNOWN (Q-11); Appendix-A orphan resolved by P2-b |
| `monthly_payment` | P2-b | 16-site count (A-09) |
| `principal_paid_per_period` | P2-b | |
| `interest_paid_per_period` | P2-b | |
| `escrow_per_period` | P2-b | |
| `total_interest` | P2-b | |
| `interest_saved` | P2-b | |
| `months_saved` | P2-b | |
| `payoff_date` | P2-b | |
| `loan_remaining_months` | P2-b | (1.7.2 addition, folded by P2-b) |
| `dti_ratio` | P2-b (debt side) + P2-c (income denominator) | seam closed in P2-c `paycheck_gross` |
| `paycheck_gross` | P2-c | |
| `paycheck_net` | P2-c | |
| `taxable_income` | P2-c | |
| `federal_tax` | P2-c | |
| `state_tax` | P2-c | |
| `fica` | P2-c | |
| `pre_tax_deduction` | P2-c | |
| `post_tax_deduction` | P2-c | |
| `apy_interest` | P2-d | |
| `growth` | P2-d | token-overloaded (G1-G4) |
| `employer_contribution` | P2-d | 1.7.3 over-attribution corrected |
| `contribution_limit_remaining` | P2-d | |
| `ytd_contributions` | P2-d | |
| `transfer_amount` | P2-d | + budget.transfers table |
| `effective_amount` | P2-d | + consolidated bypass table |
| `goal_progress` | P2-d | token-overloaded (GP1/GP2, Q-08) |
| `emergency_fund_coverage_months` | P2-d | |
| `net_worth` / aggregate year endpoints | see `year_summary_jan1_balance`/`dec31` (P2-d) | cross-link P2-a |
| `chart_balance_series` (charts) | P2-a | |
| `year_summary_jan1_balance` | P2-d | |
| `year_summary_dec31_balance` | P2-d | |
| `year_summary_principal_paid` | P2-d | |
| `year_summary_growth` | P2-d | token-overloaded (YG1-YG3) |
| `year_summary_employer_total` | P2-d | |

1.7.2 vocabulary additions (9):

| 1.7.2 token | Cataloged by | Disposition |
| --- | --- | --- |
| `pension_benefit_annual` | P2-d | own section |
| `pension_benefit_monthly` | P2-d | own section |
| `loan_remaining_months` | P2-b | own section (folded by P2-b) |
| `cash_runway_days` | P2-d | own section; fold-into-`emergency_fund_coverage_months` decided NO |
| `entry_sum_total` | **ORPHAN** | no entry/transaction family session; see below |
| `entry_remaining` | **ORPHAN** | no entry/transaction family session; see below |
| `paycheck_breakdown` | P2-c | folded as alias of the 8 paycheck tokens (`02_concepts.md:1293-1305`) |
| `chart_date_labels` | P2-a (chart family) | parallel to `chart_balance_series`; display-only |
| `transfer_amount_computed` | P2-d | own section (route-resident, no producer) |

**Orphan tokens for P2-reconcile** (financial concepts a page renders, in no P2-a/b/c/d family
section -- P2-reconcile must either catalog them or formally rule them out of scope):

- **`entry_sum_total`** -- producer `compute_entry_sums`@`entry_service.py:348` /
  `build_entry_sums_dict`@`:371` / `compute_actual_from_entries`@`:428`; consumers
  `grid/_transaction_cell.html` (21, 43), `grid/_transaction_entries.html` (136),
  `companion/_transaction_card.html` (18), `dashboard/_bill_row.html` (33); aggregate
  `func.sum(TransactionEntry.amount)`@`year_end_summary_service.py:519`. Multi-path
  (1.7.3 `*`). Reason orphaned: it is an entry/transaction-tracking token; P2-a/b/c each
  explicitly deferred the entry family and no session was scoped to own it. It intersects
  `effective_amount` (settle workflow feeds the property) -- cross-linked there, not cataloged.
- **`entry_remaining`** -- producer `compute_remaining`@`entry_service.py:405` /
  `build_entry_sums_dict`@`:371`; consumers `grid/_transaction_cell.html` (33-34),
  `grid/_transaction_entries.html` (136), `grid/_mobile_grid.html` (103),
  `dashboard/_bill_row.html` (33), `companion/_transaction_card.html` (40, 42, 44).
  Multi-path; **Q-08 anchor** (settled txn estimated-vs-actual base). Reason orphaned: same
  entry-family gap. Cross-linked from `goal_progress` GP2 (the entry-progress flavor) and the
  `effective_amount` bypass table (`_transaction_cell.html:21` Jinja `estimated_amount -
  es.total`), but not standalone-cataloged.

These two are the ONLY orphans. Every other Appendix-A token and 1.7.2 addition maps to a P2
session above. P2-reconcile's section-2 gate is therefore: (a) confirm the 3 cumulative
`PRIMARY PATH: UNKNOWN` (`period_subtotal` Q-10, `debt_total` Q-15, `loan_principal_displayed`
Q-11) each have a governing filed question (they do); (b) decide whether `entry_sum_total` /
`entry_remaining` get a reconcile-session catalog entry or a formal scope exclusion (they are
Q-08-governed and user-facing, so a catalog entry is recommended); (c) no other token is
unmapped.

---

# Phase 2 reconciliation

Session P2-reconcile (2026-05-15), the FINAL Phase 2 session. This section is the completeness
and consistency GATE; it does NOT modify P2-a/b/c/d content (verified: only this section appended
below the P2-d completeness checklist). It catalogs the two orphans, then runs Gates A-F and the
a-e verification. Gate verdicts up front:

| Gate | Subject | Verdict |
| --- | --- | --- |
| A | Orphan completeness (entry_sum_total, entry_remaining) + completeness re-derivation | PASS |
| B | Producer-attribution spot-check (12 entries, 1.7.8 rule) | PASS (12/12) |
| C | Catalog vs inventory 1.7.4 two-way diff | PASS (0 MISS-class; 12 escalation/override findings recorded) |
| D | A-01 / A-05 auditor-verification caveat propagation | PASS (corrections recorded for Phase 3; no P2 rewrite) |
| E | Consolidated Phase-3 registers (E1-E4) | PASS (delivered below) |
| F | Cross-family seam closure | PASS (4 seams; F1 source-verified) |

Verification a-e all hold (see "Verification before completion"). **Phase 2 is declared complete
at the end of this section; Phase 3 may begin.**

---

## Gate A -- Orphan completeness

P2-d flagged `entry_sum_total` and `entry_remaining` as orphans (entry/transaction-tracking
tokens; no P2 family session owned an entry family). They are user-facing financial figures
(`grid/_transaction_cell.html`, `dashboard/_bill_row.html`, companion cards) and Q-08-governed.
Per the session demand a catalog entry is produced (not a scope exclusion). Source spot-checked
by Read: `entry_service.py:340-447` (compute_entry_sums, build_entry_sums_dict, compute_remaining,
compute_actual_from_entries) and `dashboard_service.py:203-246` (`_entry_progress_fields`).

### Concept: entry_sum_total

- **Intended definition.** The aggregate of a single entry-tracked (envelope) transaction's
  `TransactionEntry` rows: the `(sum_debit, sum_credit)` pair and the combined `total = debit +
  credit`, plus a `count`. Represents how much of the transaction's budgeted amount has been
  recorded as actually spent across its sub-entries. Source (read in full):
  `compute_entry_sums`@`entry_service.py:348-368` (`sum_credit += e.amount` if `e.is_credit`
  else `sum_debit += e.amount`); `build_entry_sums_dict`@`entry_service.py:371-402`
  (`{"debit","credit","total": debit+credit,"count"}`@`:396-401`);
  `compute_actual_from_entries`@`entry_service.py:428-446` (`sum(e.amount for e in entries)` --
  the settle-time facet, identical arithmetic to `total`). Inventory 1.7.2 (P1-c addition);
  1.7.3 marks it `*`.
- **Units / type.** Decimal money; pure pre-quantized sums of `TransactionEntry.amount`
  (`Numeric(12,2)`); not separately re-quantized (operands already 2dp).
- **Producer sites (1.7.8-filtered).**
  - svc:`compute_entry_sums`@`entry_service.py:348` -- canonical pure debit/credit summation
    (genuine `+=` arithmetic; pure function, no DB).
  - svc:`build_entry_sums_dict`@`entry_service.py:371` -- batch wrapper; derives `total = debit
    + credit`@`:399` (genuine arithmetic; the `entry_sum_total` value the grid/cell consume).
  - svc:`compute_actual_from_entries`@`entry_service.py:428` -- `sum(e.amount ...)`@`:446`;
    same total, surfaced as `actual_amount` at settle (cross-link `effective_amount` tier 3 and
    `transaction_service.settle_from_entries`@`:153`).
  - Producer (1.5 model): `TransactionEntry.amount`@`transaction_entry.py:73`.
  - Aggregate (1.6): SQL `func.sum(TransactionEntry.amount)`@`year_end_summary_service.py:519`
    -- an INDEPENDENT SQL re-derivation of the same sum (the parallel path Phase 3 must check).
  - **P1-f reconciliation.** `_entry_progress_fields`@`dashboard_service.py:203` computes
    `total = debit + credit`@`:238` after calling `compute_entry_sums`@`:237` -- it is a
    genuine inline re-surfacing for the dashboard bill row (P1-f KEEP list confirms `:203`
    `total = debit + credit` @238 is genuine arithmetic); w.r.t. `entry_sum_total` it
    re-derives via the canonical `compute_entry_sums`, so it is a delegating CONSUMER-surface,
    not an independent producer.
- **Consumer sites.** route:`get_cell`@`transactions.py:244` (via `build_entry_sums_dict`@`:88`);
  route:`list_entries`@`entries.py:200`; route:`index`@`companion.py:80`;
  route:`page`@`dashboard.py:40` / `mark_paid`@`dashboard.py:54` (via `_entry_progress_fields`).
  Templates: `grid/_transaction_cell.html` (21, 43), `grid/_transaction_entries.html` (136),
  `companion/_transaction_card.html` (18), `dashboard/_bill_row.html` (33). Aggregate consumer:
  `_compute_entry_breakdowns`@`year_end_summary_service.py:475` (the `:519` SQL sum).
- **Primary path.** `compute_entry_sums`@`entry_service.py:348` is the canonical pure
  summation; `build_entry_sums_dict`@`:371` the canonical batch surface; both owned by
  `entry_service`. `compute_actual_from_entries`@`:428` is the settle-time facet of the same
  arithmetic. **Not UNKNOWN** (single-module owner; Q-08 governs the *consumption* base, not
  this sum's production). No new Q (the estimated-vs-actual base ambiguity is Q-08, already
  filed; the SQL-vs-Python parity is a Phase-3 verification, not a "what is intended").
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.3 `*`; 1.7.4 Tier-2 (3 producers, 3 routes, 4
  templates, 1 SQL aggregate). Exact pairs:
  - Python `build_entry_sums_dict`/`compute_entry_sums`@`entry_service.py:371,348`
    (`debit+credit`) **vs** SQL `func.sum(TransactionEntry.amount)`@
    `year_end_summary_service.py:519` (+ the credit `case(...)` at `:520-528`) for the same
    transaction's entries -- the 1.7.4-named "envelope SQL sum matches per-template entry sum"
    check. Drift class: SCOPE_DRIFT / SILENT_DRIFT candidate (SQL sum may not split debit/credit
    the way the Python pair does; verify the credit `case` reproduces `compute_entry_sums`'
    `is_credit` partition).
  - `compute_actual_from_entries`@`entry_service.py:428` (settle writes `actual_amount`) **vs**
    the displayed `entry_total` from `build_entry_sums_dict` for a settled envelope txn -- must
    be byte-identical (both are `sum(e.amount)`); cross-link `effective_amount` tier 3 and Q-14
    (`mark_paid` vs `mark_done` settle divergence).
- **Prior-audit linkage.** None specific. Cross-link the `effective_amount` consolidated bypass
  table (`02_concepts.md` effective_amount entry): the entry-sum helpers are the
  "entries sub-transactional" AGREE-candidate rows.

### Concept: entry_remaining

- **Intended definition.** The unspent budget of a single entry-tracked transaction:
  `estimated_amount - sum(all entry amounts)` (negative = overspent). Uses the sum of ALL
  entries (debit + credit) because remaining measures budget consumption, not checking impact.
  Source (read in full): `compute_remaining`@`entry_service.py:405-425`
  (`total_spent = sum(e.amount for e in entries)`@`:424`; `return estimated_amount -
  total_spent`@`:425`); consumed by `_entry_progress_fields`@`dashboard_service.py:239`
  (`remaining = compute_remaining(txn.estimated_amount, txn.entries)`) and `entry_over_budget =
  total > txn.estimated_amount`@`:245`. Inventory 1.7.2 (P1-c); 1.7.3 marks it `*`.
- **Units / type.** Decimal money (2dp; difference of two `Numeric(12,2)` values, not
  re-quantized).
- **Producer sites (1.7.8-filtered).**
  - svc:`compute_remaining`@`entry_service.py:405` -- **the sole genuine producer**
    (`estimated_amount - total_spent` subtraction; pure function).
  - **P1-f / 1.7.8 reconciliation (producer over-attribution corrected).** Raw 1.7.3 lists
    `build_entry_sums_dict`@`entry_service.py:371` as an `entry_remaining` producer. Source
    read (`entry_service.py:392-402`): `build_entry_sums_dict` returns
    `{"debit","credit","total","count"}` -- it has **no `remaining` key and performs no
    `estimated_amount` subtraction**. It is NOT an `entry_remaining` producer (it produces
    `entry_sum_total`). 1.7.3's listing is producer over-attribution per the 1.7.8 operational
    rule; corrected here. `_entry_progress_fields`@`dashboard_service.py:203` (1.7.3 annotated
    "(consumes)") DELEGATES the remaining computation to `compute_remaining`@`:239` -> CONSUMER.
    `_build_entry_data`@`companion.py:28-64` and `_render_entry_list`@`entries.py:83-120`
    (1.7.3 annotated "(consumes)") -> CONSUMERS. Genuine producer reduces to the single
    `compute_remaining`.
  - Producer (1.5 model inputs): `Transaction.estimated_amount`@`transaction.py:158`;
    `TransactionEntry.amount`@`transaction_entry.py:73`.
- **Consumer sites.** route:`list_entries`@`entries.py:200`; route:`get_cell`@
  `transactions.py:244`; route:`page`@`dashboard.py:40` / `mark_paid`@`dashboard.py:54`
  (Q-14, via `_entry_progress_fields`); route:`index`@`companion.py:80`. Templates:
  `grid/_transaction_cell.html` (33-34; **and the Jinja arithmetic `remaining = t.estimated_amount
  - es.total`@`:21`** -- a coding-standards "templates do not compute" violation, in the
  `effective_amount` bypass table), `grid/_transaction_entries.html` (136),
  `grid/_mobile_grid.html` (103; Jinja arithmetic @`:96,183`), `dashboard/_bill_row.html` (33),
  `companion/_transaction_card.html` (40, 42, 44).
- **Primary path.** `compute_remaining`@`entry_service.py:405` is the sole canonical producer
  (single owner). The **base argument is Q-08-governed**: `compute_remaining` always receives
  `txn.estimated_amount` (`dashboard_service.py:239`), never `actual_amount`, even for a
  settled (done) envelope txn -- this is exactly the Q-08 ambiguity (A-08 *proposed*, pending
  developer confirmation). The producer is unambiguous; the *semantic* (which base for a
  settled txn) is Q-08. **Not a new PRIMARY PATH: UNKNOWN** (the function is canonical; Q-08
  governs the base and is already filed -- analogous to how a clear producer can still be
  semantically gated). No new Q (Q-08 is the governing question; the Jinja-arithmetic mirrors
  are a coding-standards finding already in the `effective_amount` bypass table, not a
  "what is intended" ambiguity, so Q-16 is NOT raised).
- **Multi-path flag: PHASE 3 REQUIRED.** 1.7.3 `*`; 1.7.4 Tier-2 (5 producers per raw 1.7.3 /
  **1 after 1.7.8 correction**, 4 routes, 5 templates). Exact pairs:
  - `compute_remaining`@`entry_service.py:405` (Python `estimated_amount - sum(entries)`)
    **vs** the Jinja `remaining = t.estimated_amount - es.total`@`grid/_transaction_cell.html:21`
    and `grid/_mobile_grid.html:96,183` (template recomputation) -- must be identical; the
    template path omits the `compute_remaining` "ALL entries" semantic if `es.total` differs
    from `sum(e.amount)`. Drift class: SILENT_DRIFT (template arithmetic) + coding-standards
    violation.
  - Q-08 anchor: `compute_remaining(estimated_amount, ...)` and
    `entry_over_budget = total > estimated_amount`@`dashboard_service.py:245` **vs** the
    interpretation where a settled (done, `actual_amount` set) txn should re-anchor on
    `actual_amount` (A-08 interpretation 2). Phase 3 records AGREE (interp 1) or DIVERGE
    (interp 2) per the developer's Q-08 answer. **BLOCKED on Q-08** (see E3).
- **Prior-audit linkage.** None specific. Cross-link `goal_progress` GP2 (the entry-progress
  percentage shares this base), `effective_amount` bypass table (`_transaction_cell.html:21`
  Jinja row), and Q-14 (`mark_paid`/`mark_done` settle path feeds the same display).

### Gate A independent completeness re-derivation

Mechanical grep of `02_concepts.md` for `^## Concept: <token>` over every audit-plan Appendix A
controlled-vocabulary token (42, incl. the `loan_principal_displayed` orphan resolved by P2-b)
and every inventory 1.7.2 vocabulary addition (9). Result:

- **42/42 Appendix A tokens** have exactly one `## Concept:` section (grep count = 1 each).
- **1.7.2 additions:** `pension_benefit_annual`, `pension_benefit_monthly`,
  `loan_remaining_months`, `cash_runway_days`, `transfer_amount_computed` each have a section
  (own-section disposition); `entry_sum_total` and `entry_remaining` were 0 and are cataloged
  by Gate A above; `paycheck_breakdown` (grep 0) is the documented **alias** of the 8 paycheck
  component tokens (`02_concepts.md:1293-1305`, P2-c) -- every field maps to a PHASE 3 REQUIRED
  component token, so no standalone section is required (formal disposition, not orphan);
  `chart_date_labels` (grep 0) is a **display-only string label series** parallel to
  `chart_balance_series` (P2-a chart family), not a money figure -- formally out of the
  money-concept catalog scope (disposition, not orphan).
- **Zero remaining uncataloged financial-figure tokens** after Gate A. The two flagged orphans
  are now cataloged with the full section-2 schema; `paycheck_breakdown`/`chart_date_labels`
  are formally dispositioned (alias / display-only-non-money) rather than orphaned.

**Gate A verdict: PASS.** Both orphans cataloged (Q-08 cross-linked, no new Q); completeness
map re-derived independently; zero uncataloged tokens.

---

## Gate B -- Producer-attribution consistency spot-check

12 producer-list entries sampled across all four family sections (>=3 per family), each Read at
the cited `file:line` by P2-reconcile (independent of the P2-a/b/c/d QC notes) and checked
against the 1.7.8 rule (PRODUCER only if it computes/derives the concept value; compare/read/
delegate/assemble = CONSUMER).

| # | Family | Concept | Cited producer `file:line` | Read verdict |
| --- | --- | --- | --- | --- |
| 1 | P2-a | checking_balance | `calculate_balances`@`balance_calculator.py:35` | PASS -- returns `(balances OrderedDict, stale_warning)`; anchor-forward roll. Genuine producer. |
| 2 | P2-a | net_worth | `_compute_net_worth`@`year_end_summary_service.py:689` | PASS -- sums all account balances at 12 monthly endpoints, liabilities negative. Genuine aggregator/producer. |
| 3 | P2-a | debt_total | `_compute_debt_summary`@`savings_dashboard_service.py:802` (body 835-864) | PASS -- `total_debt += principal`@`:855` (`Decimal(str(lp.current_principal))`@`:840`); `total_monthly += (monthly_pi+monthly_escrow).quantize(HALF_UP)`@`:851-856`. Genuine. |
| 4 | P2-b | monthly_payment | `calculate_monthly_payment`@`amortization_engine.py:178` | PASS -- annuity `principal*(monthly_rate*factor)/(factor-1)`@`:196`, quantize HALF_UP @`:192/197`. Genuine (definition). |
| 5 | P2-b | total_interest / payoff_date | `_derive_summary_metrics`@`amortization_engine.py:622` | PASS -- `sum(row.interest ...)`@`:642-644`, `payoff_date = schedule[-1].payment_date`@`:645`. Genuine. |
| 6 | P2-b | escrow_per_period | `calculate_monthly_escrow`@`escrow_calculator.py:14` | PASS -- sums active components `annual/12` (+ optional inflation). Genuine. |
| 7 | P2-c | paycheck_gross / taxable_income | `calculate_paycheck`@`paycheck_calculator.py:92` (body 125-159) | PASS -- `gross_biweekly=(_apply_raises/pp).quantize(HALF_UP)`@`:133-135`; `taxable=gross-total_pre_tax`@`:155`. Genuine. |
| 8 | P2-c | fica | `calculate_fica`@`tax_calculator.py:274` | PASS -- SS cap enforced @`:300-306`, Medicare @`:309`; returns `{ss,medicare,total}`. Genuine. |
| 9 | P2-c | pre/post_tax_deduction | `_calculate_deductions`@`paycheck_calculator.py:403` | PASS -- timing-parameterized deduction calc (flat/pct/inflation). Genuine parameterized producer. |
| 10 | P2-d | apy_interest | `calculate_interest`@`interest_projection.py:49` | PASS -- per-period interest engine (actual/365); signature + docstring confirm; body arithmetic @`:89-90,114` (P2-d QC source-confirmed). Genuine. |
| 11 | P2-d | employer_contribution | `calculate_employer_contribution`@`growth_engine.py:91` | PASS -- flat `(gross*pct).quantize(HALF_UP)`@`:114`, match `min(emp, gross*cap)*match`@`:117-125`. Sole genuine producer. |
| 12 | P2-d | pension_benefit_annual/monthly | `calculate_benefit`@`pension_calculator.py:31` (body 55-69) | PASS -- `annual=(mult*yos*high_avg).quantize(HALF_UP)`@`:61-63`; `monthly=annual/12`@`:65-67`. Sole genuine producer. |

**Pass rate: 12/12 (100%); 0 failures.** The Gate-B threshold for FAIL is 2+ of 12 mis-attributed
(catalog re-inherited the P1-f over-attribution). 0 failed. The catalog's per-session 1.7.8
filtering (six P1-f relocations applied, raw-1.7.3 over-attributions corrected per concept) holds
under independent re-read.

**Gate B verdict: PASS.**

---

## Gate C -- Catalog vs inventory 1.7.4 reconciliation

Phase 3 consumes the catalog (audit-plan 10.3: Phase 3 reads `00`,`01`,`02`) plus E1 below as
its comparison worklist; 1.7.4 is the mechanical `*`-derived list. Two-way diff. A mismatch is
**MISS-class** only if it would cause Phase 3 to SKIP a required comparison; an
escalation/override where the catalog does MORE than 1.7.4 is recorded but is not MISS-class
(the catalog and E1 carry it, so Phase 3 does not skip it).

### Direction 1 -- every catalog "PHASE 3 REQUIRED" concept appears in 1.7.4

39 catalog concepts are marked PHASE 3 REQUIRED (8 are single-path with a scoped internal-
consistency verify only: `loan_remaining_months`, `contribution_limit_remaining`,
`ytd_contributions`, `emergency_fund_coverage_months`, `cash_runway_days`,
`transfer_amount_computed`, `pension_benefit_annual`, `pension_benefit_monthly`). Of the 39, 27
map cleanly to a 1.7.4 Tier-1/Tier-2 row. The 12 below are **catalog escalations/overrides of a
1.7.4 single-path (or absent) classification** -- Phase 3 must use the catalog/E1, NOT 1.7.4's
narrower class, for these:

| Concept | 1.7.4 says | Catalog says | Why (escalation basis) | MISS-class? |
| --- | --- | --- | --- | --- |
| `checking_balance` | single-path (alias note) | PHASE 3 REQUIRED | 1.7.3 `*`; 1.7.4's own note "Phase 3 must verify this aliasing is intentional"; symptom #1 | No (1.7.4 note itself asks for it; E1 carries it) |
| `net_worth` | single-path | PHASE 3 REQUIRED (PLAN_DRIFT) | single token producer but W-152/W-159/W-366 require identical net-worth vs savings-progress paths; Q-15 dispatcher | No (catalog+E1 carry; would be MISS if only 1.7.4 used) |
| `months_saved` | single-path | PHASE 3 REQUIRED | **P2-b explicit override**: >1 producer (amortization vs debt_strategy) + definitional fork (schedule-len vs refinance break-even) | No (override documented; E1 carries) |
| `state_tax` | single-path | PHASE 3 REQUIRED | **P2-c explicit override**: >=3 producers (calculate_state_tax, calculate_paycheck de-annualize, apply_calibration), 5 routes | No (override documented; E1 carries) |
| `pre_tax_deduction` | single-path | PHASE 3 REQUIRED (scoped) | ordering invariant + Q-13 percentage-base divergence | No (catalog+E1 carry) |
| `post_tax_deduction` | single-path | PHASE 3 REQUIRED (scoped) | ordering invariant (post-tax never reduces taxable) | No (catalog+E1 carry) |
| `year_summary_jan1_balance` | single-path | PHASE 3 REQUIRED | inherits `net_worth` (W-152/W-159, Q-15 dispatcher) | No (catalog+E1 carry) |
| `year_summary_dec31_balance` | single-path | PHASE 3 REQUIRED | inherits `net_worth`; W-159 Dec31 equality | No (catalog+E1 carry) |
| `year_summary_principal_paid` | single-path | PHASE 3 REQUIRED | analytical frame: aggregation-layer; year-end delta vs per-period schedule sum | No (catalog+E1 carry) |
| `year_summary_growth` | single-path | PHASE 3 REQUIRED | token-overloaded YG1-YG3; aggregation-layer | No (catalog+E1 carry) |
| `year_summary_employer_total` | single-path | PHASE 3 REQUIRED | analytical frame: aggregation-layer total of a multi-consumer engine concept | No (catalog+E1 carry) |
| `loan_principal_displayed` | **absent from 1.7.4** (1.7.3 orphan row all `--`) | PHASE 3 REQUIRED + UNKNOWN (Q-11) | P2-b resolved the Appendix-A orphan; symptom #3/#5 load-bearing | No **only because** catalog+E1 carry it; would be a hard MISS if Phase 3 used 1.7.4 alone |

All 12 are escalations/overrides where the catalog does strictly MORE than 1.7.4. Because Phase 3
reads `02_concepts.md` (audit-plan 10.3) and E1 (below) enumerates every one of them with
concrete pairs, **none causes Phase 3 to skip a comparison** -- zero MISS-class. The two P2
overrides the gate explicitly named (`months_saved`/P2-b, `state_tax`/P2-c) are reflected here
and in E1.

### Direction 2 -- every 1.7.4 multi-path entry has a catalog "PHASE 3 REQUIRED" concept

1.7.4 Tier-1 (4: account_balance, effective_amount, monthly_payment, period_subtotal) and
Tier-2 (26) -- 30 multi-path concepts. All map to a catalog PHASE 3 REQUIRED concept EXCEPT:

| 1.7.4 multi-path entry | Catalog status | Resolution | MISS-class? |
| --- | --- | --- | --- |
| `entry_remaining` | no P2-a/b/c/d section | **Gate A catalogs it** (PHASE 3 REQUIRED) | No (Gate A resolves) |
| `entry_sum_total` | no P2-a/b/c/d section | **Gate A catalogs it** (PHASE 3 REQUIRED) | No (Gate A resolves) |
| `paycheck_breakdown` | no section (alias) | P2-c documented alias of 8 paycheck tokens (all PHASE 3 REQUIRED) + `employer_contribution` (PHASE 3 REQUIRED) -- the comparison is the union of the component registers, NOT a 9th | No (every field is a PHASE 3 REQUIRED component) |

All other 27 Tier-1/Tier-2 entries have a catalog concept marked PHASE 3 REQUIRED (verified
against the awk extraction of every concept's Multi-path flag line). After Gate A + the alias
mapping, **no 1.7.4 multi-path concept lacks catalog Phase-3 coverage** -- zero MISS-class in
Direction 2.

**Gate C verdict: PASS.** Two-way diff complete; 12 Direction-1 escalation/override findings and
3 Direction-2 resolutions recorded; **zero MISS-class mismatches** (every escalation/override/
orphan/alias is carried by the catalog + the E1 register, so Phase 3 -- which reads the catalog,
not 1.7.4 in isolation -- does not skip any comparison). The single-path overrides
(`months_saved` P2-b, `state_tax` P2-c, plus `net_worth`/`year_summary_*` PLAN_DRIFT escalations
and the `loan_principal_displayed` orphan resolution) are reflected so 1.7.4 and the catalog
agree operationally via E1.

---

## Gate D -- Auditor-verification caveat propagation (A-01, A-05)

Per `09_open_questions.md` (2026-05-15 verification notes): **A-01 is PARTIALLY ACCURATE** (the
ROUND_HALF_UP rule IS the convention, but the "every monetary boundary" framing is not literally
true -- 24 `.quantize()` calls omit `rounding=ROUND_HALF_UP` (default banker's), 1 intentional
`ROUND_CEILING`, 3 Jinja arithmetic templates, 1 JS file, no centralized `round_money()`);
**A-05 is PARTIAL** (8 enumerated ARM sites understate the total -- 16 call sites, 8 fixed-rate
ELSE branches differ, `debt_strategy.py:127` is the 16th, missed by A-05 and Q-09).

### A-05 propagation -- CORRECT

- `monthly_payment` (catalog `02_concepts.md:601-712`): cites "A-05 (developer + verification)"
  and **adopts the verification's 16-site correction** ("Phase 3 must verify all 16 sites (not
  the inventory's 14)"; full 16-site provenance table; `debt_strategy.py:127` = site 16,
  "missed by A-05 and Q-09; see A-09"). Cites the verification verdict, NOT the raw A-05
  8-site answer. **Correct propagation.**
- A-05-inheriting concepts (`total_interest`, `interest_saved`, `payoff_date`,
  `principal_paid_per_period`, `escrow_per_period` PITI, `loan_principal_real`,
  `loan_principal_stored`, `year_summary_principal_paid`): each cross-links the
  `monthly_payment` 16-site table / A-05 explicitly ("A-05's ARM-input divergence propagates";
  "inherits symptom #2/#4"). They defer to the verification-aware `monthly_payment` entry.
  **Correct propagation.** No correction needed.

### A-01 propagation -- PARTIALLY PROPAGATED; corrections recorded for Phase 3

The A-01 24-omission-site list (`09_open_questions.md:42-48`) is:
`investment_projection.py:93,96,159`; `savings_dashboard_service.py:266,872,873`;
`retirement_dashboard_service.py:197,211,214,240,390`;
`investment.py:131,223,226,319,458,535,538,580,585,586,589,670`; `loan.py:968`; plus the
intentional `ROUND_CEILING` at `savings_goal_service.py:462-463`.

- **Correctly propagated:** `paycheck_gross` -- explicitly flags
  `savings_dashboard_service.py:263-266` as in "the A-01 verification's 24-call violation list,
  `09_open_questions.md:47`" and classifies it a Phase-3 DIVERGE (DEFINITION_DRIFT raise
  omission + ROUNDING_DRIFT mode). `emergency_fund_coverage_months` / `ytd_contributions` /
  P2-d analytical-frame -- correctly scope A-01 as **money-scoped** (the 0.1-precision
  coverage ratio and raw-sum-into-display are *not* A-01 violations) consistent with the
  verification's refined scope. `effective_amount` bypass table -- the raw-read sites are
  Phase-3 verdict-candidates, consistent.

- **Corrections needed (recorded for Phase 3; NOT rewritten into P2-a/b/c/d per session demand).**
  These entries assert engine-boundary "A-01-clean (verified by Read at [engine])" which is
  true for the *engine producers* but the A-01 PARTIALLY-ACCURATE verdict means their
  **aggregation/route-layer** sites in the 24-list are NOT clean. Phase 3 must attach an
  A-01-verification ROUNDING_DRIFT caveat at these sites:

  | Catalog entry | 24-list sites in its consumer/aggregation chain | Required Phase-3 caveat |
  | --- | --- | --- |
  | `growth` | `retirement_dashboard_service.py:197,211,214,240,390`; `investment.py:131,223,226,319,458,535,538,580,585,586,589`; `savings_dashboard_service.py:872,873` | engine `growth_engine`/`interest_projection` is A-01-clean; the retirement/investment-route aggregation layer is in the 24-list -- ROUNDING_DRIFT candidate |
  | `employer_contribution` | `investment.py:*` route-layer sites; retirement aggregation | sole engine producer `growth_engine.py:91` is A-01-clean; the investment-route consumer surface is in the 24-list |
  | `contribution_limit_remaining` / `ytd_contributions` | `investment_projection.py:93,96,159`; `investment.py:670` | the route-resident `limit - ytd` and the YTD sum sites are in the 24-list (banker's default) |
  | `transfer_amount_computed` | `investment.py:670` (create_contribution_transfer prefill); `loan.py:968` region (payment prefill) | route-resident prefill quantize omits ROUND_HALF_UP |
  | `escrow_per_period` / loan display | `loan.py:968` | a loan-route monetary quantize in the 24-list |
  | `goal_progress` GP1 | `savings_goal_service.py:462-463` (`_compute_required_monthly` `ROUND_CEILING`) | **intentional** per the function docstring `:438` (user contributes "at least enough"); record as documented-intentional A-01 exception, NOT a drift |

**Gate D verdict: PASS.** A-05 propagation is correct (monthly_payment + inheritors cite the
verification's 16-site verdict). A-01 propagation is correct where cited (paycheck_gross,
money-scoping); the six aggregation/route-layer gaps above are recorded as Phase-3 caveats (the
session demand forbids silently rewriting P2-a/b/c/d -- they are listed here for Phase 3, which
is the correct disposition). No gate-failing omission: every A-01/A-05-leaning *primary-path
justification* already cites the verification verdict, not the raw developer answer.

---

## Gate E -- Consolidated registers for Phase 3

### E1 -- PHASE 3 REQUIRED comparison register (Phase 3's literal worklist)

Exhaustive and self-contained: every multi-path concept, its concrete producer `file:line`
pairs, and the drift class to test. Pairs are reproduced from each concept's Multi-path flag
bullet (cross-link the concept entry for full context). Single-path concepts with a scoped
internal verify are listed at the end. "DC" = drift class candidate
(SILENT/DEFINITION/SOURCE/ROUNDING/SCOPE/PLAN).

| Concept | Primary comparison pair(s) `file:line` | DC | Blocked? |
| --- | --- | --- | --- |
| `account_balance` | `calculate_balances`@`balance_calculator.py:35` via `grid.balance_row`@`grid.py:393` vs via `accounts.checking_detail`@`accounts.py:1425` vs `_compute_account_projections`@`savings_dashboard_service.py:294` vs `_get_account_balance_map`/`_build_account_data`@`year_end_summary_service.py:750` vs loan `get_loan_projection`@`amortization_engine.py:864` vs `params.current_principal`@`loan/dashboard.html:104` vs investment `growth_engine.project_balance` | SOURCE/SCOPE/PLAN | Q-15 (dispatcher) |
| `checking_balance` | Pair A grid vs `/accounts` (`calculate_balances` via `grid.py:393` vs `accounts.py:1425`); Pair B grid vs `/savings` (`balance_calculator.py:35` vs `_compute_account_projections`@`savings_dashboard_service.py:294`); Pair C grid subtotal (`grid.py:263-279`, effective_amount) vs balance row (`_sum_remaining`/`_sum_all`@`balance_calculator.py:389,422`, `_entry_aware_amount`@`:292`) | SILENT/SCOPE | symptom #1 |
| `projected_end_balance` | `calculate_balances`@`balance_calculator.py:35` via `grid.py:393` vs `accounts.py:1425`; vs `_compute_account_projections`@`savings_dashboard_service.py:294` (`:352`); loan vs `loan/dashboard.html:104` (Q-11) | SOURCE/SCOPE | E-04; symptom #1/#5 |
| `period_subtotal` | `grid.index`@`grid.py:263-279` (effective_amount expense) vs `_sum_remaining`/`_sum_all`@`balance_calculator.py:389,422` (`_entry_aware_amount`@`:292`); vs `_compute_spending_comparison`@`dashboard_service.py` (settled-only); vs `obligations.summary`@`obligations.py:331-408` vs `compute_committed_monthly`@`savings_goal_service.py:287` | DEFINITION/SILENT | **UNKNOWN Q-10** |
| `chart_balance_series` | per-domain engine series vs rendered series (no JS recompute, E-17); HYSA `calculate_balances_with_interest`@`balance_calculator.py:112` (`grid.balance_row`@`grid.py:393`) vs `projected_end_balance` via `accounts.checking_detail`@`accounts.py:1425` | SILENT | -- |
| `net_worth` | `_get_account_balance_map` (investment branch) inside `_compute_net_worth`@`year_end_summary_service.py:689` vs `_compute_savings_progress`@`:887` (W-159); `_get_account_balance_map` (loan) vs `_compute_account_projections`@`savings_dashboard_service.py:294` | PLAN/SOURCE | Q-15 |
| `savings_total` | `_compute_account_projections`@`savings_dashboard_service.py:294` vs `compute_gap_data`@`retirement_dashboard_service.py:79` vs `_compute_savings_progress`@`year_end_summary_service.py:887`; W-159 Dec31 equality vs `_compute_net_worth`@`:689` | SOURCE/PLAN | Q-15 |
| `debt_total` | `_compute_debt_summary`@`savings_dashboard_service.py:802` (stored `current_principal`@`:840`) vs loan-liability in `_compute_net_worth`@`year_end_summary_service.py:689` (amortization-derived) vs `calculate_strategy`@`debt_strategy_service.py:521`; internal: `:840` stored vs `ad["monthly_payment"]`/`proj.current_balance` engine base | SOURCE/DEFINITION | **UNKNOWN Q-15** |
| `monthly_payment` | all **16** call sites (table @`02_concepts.md:627-645`): ARM-stable sites 7/9/11/14 byte-identical; site 4 (rate-history) vs site 7 (stored) inside fixed-rate window (C-04); site 16 `debt_strategy.py:127` vs site 8 `:957`; discriminator seam sites 1-2/5-6 (`using_contractual`) vs 7-16 (`is_arm`) | SILENT/DEFINITION | symptom #2/#4; Q-09 (debt_strategy.py:127 intent) |
| `loan_principal_real` | `get_loan_projection.current_balance`@`amortization_engine.py:980-984` (fixed walk) vs `_compute_real_principal`@`debt_strategy.py:175-184` (independent replay); ARM `:978` vs stored `loan/dashboard.html:104` | SOURCE | A-04; symptom #3/#5; Q-15 |
| `loan_principal_stored` | `LoanParams.current_principal`@`loan_params.py:54` (rendered `loan/dashboard.html:104`) vs `get_loan_projection.current_balance`@`amortization_engine.py:980-984` (fixed engine-walked) | SOURCE | Q-11; Phase 4 owns AUTHORITATIVE/CACHED |
| `loan_principal_displayed` | `loan/dashboard.html:104` (STORED `params.current_principal`, `loan.py:557`) vs `loan/_refinance_results.html:69-70` (`proj.current_balance`@`loan.py:1087`) vs `debt_strategy/dashboard.html` (`_compute_real_principal`@`debt_strategy.py:147`) | SOURCE/DEFINITION | **UNKNOWN Q-11** |
| `principal_paid_per_period` | `generate_schedule`@`amortization_engine.py:326` per-row principal vs `calculate_balances_with_amortization`@`balance_calculator.py:176` `principal_by_period` vs `_compute_debt_progress`@`year_end_summary_service.py:824` jan1-dec31 delta | SILENT/SCOPE | inherits monthly_payment (A-05) |
| `interest_paid_per_period` | `generate_schedule`@`amortization_engine.py:517` raw per-row vs `_compute_mortgage_interest`@`year_end_summary_service.py:380` (A-06-preprocessed, calendar-year) | SCOPE/DEFINITION | A-06; C-05 |
| `escrow_per_period` | `calculate_monthly_escrow`@`escrow_calculator.py:14` (display) vs escrow subtracted in `prepare_payments_for_engine`@`loan_payment_service.py:305-318` (A-06); `loan/_escrow_list.html:37` Jinja arithmetic | SCOPE/SILENT | A-06 |
| `total_interest` | `_derive_summary_metrics`@`amortization_engine.py:622` (life-of-loan) vs `_compute_mortgage_interest`@`year_end_summary_service.py:380` (calendar-year, A-06) vs `calculate_strategy`@`debt_strategy_service.py:521` | DEFINITION (by design) | A-06; C-05; inherits A-05 |
| `interest_saved` | `calculate_summary`@`amortization_engine.py:649` vs `calculate_strategy`@`debt_strategy_service.py:521` for the same single loan + same extra | SILENT | inherits A-05 |
| `months_saved` | `calculate_summary`@`amortization_engine.py:739` vs `calculate_strategy`@`debt_strategy_service.py:521` vs refinance `break_even_months`@`loan.py:1027` (W-242, distinct formula) | DEFINITION | **P2-b override of 1.7.4 single-path** |
| `payoff_date` | `_derive_summary_metrics`@`amortization_engine.py:622` vs `calculate_strategy`@`debt_strategy_service.py:521`; W-239 transfer-template end_date == displayed payoff | SILENT/PLAN | inherits A-05 |
| `dti_ratio` | `dashboard/_debt_summary.html` (via `dashboard_service._get_debt_summary`@`:533` delegate) vs `savings/dashboard.html` (`compute_dashboard_data`@`savings_dashboard_service.py:61`, division @`:173-176`); Q-12 mortgage double-count | SILENT | Q-12; Q-15 (co-displayed total_debt base) |
| `paycheck_gross` | `calculate_paycheck`@`paycheck_calculator.py:133-135` (HALF_UP, post-raises) vs `savings_dashboard_service.py:263-266` (raw salary, banker's -- dti base); vs `_get_cumulative_wages`@`paycheck_calculator.py:499-501`; vs `pension_calculator.project_salaries_by_year`@`:78` | DEFINITION+ROUNDING | A-01 (266 in 24-list) |
| `paycheck_net` | `calculate_paycheck` net @`paycheck_calculator.py:223-231` vs section-7 recompute from component tokens; bracket vs calibrated (labeled intentional); `recurrence_engine._get_transaction_amount`@`:720` | ROUNDING (terminal quantize) | -- |
| `taxable_income` | `calculate_paycheck`@`paycheck_calculator.py:155` (display) vs `salary.calibrate_preview` inline @`salary.py:1095` (Q-13); vs `calculate_federal_withholding`@`tax_calculator.py:112-120` (internal, labeled diff); legacy `calculate_federal_tax`@`:233` (dead) | DEFINITION | Q-13 |
| `federal_tax` | `calculate_federal_withholding`@`tax_calculator.py:35` (bracket) vs `apply_calibration`@`calibration_service.py:133` (calibrated, labeled intentional); year-end annual vs 26-period sum (PA-24); Q-13 inline | DEFINITION/SCOPE | Q-13; PA-24 |
| `state_tax` | `calculate_state_tax`@`tax_calculator.py:266` -> `calculate_paycheck` double-quantize @`paycheck_calculator.py:202-204` vs `apply_calibration`@`calibration_service.py:136` single quantize; year-end vs 26-period (PA-24) | ROUNDING/SCOPE | **P2-c override of 1.7.4 single-path** |
| `fica` | `calculate_fica`@`tax_calculator.py:300-306` (SS cap enforced) vs `apply_calibration`@`calibration_service.py:139-144` (NO cap); partial-crossing period; year-end vs 26-period incl. cap crossover (PA-24) | DEFINITION | PA-21 confirmation |
| `pre_tax_deduction` | ordering: `paycheck_calculator.py:155` (taxable after pre-tax) vs steps 6-7 @`:159-214`; pct base `:440` (profile gross) vs `salary.calibrate_preview` @`salary.py:1095` (actual gross, Q-13) | DEFINITION | Q-13 |
| `post_tax_deduction` | ordering: `paycheck_calculator.py:217-220` (step 8 after taxes) vs `:230` (subtracted only in net); never subtracted from `taxable_biweekly`@`:155` | (invariant check) | -- |
| `apy_interest` | `accounts/interest_detail.html` (`accounts.interest_detail`@`accounts.py:1299`) vs `savings/dashboard.html` (137,140); `_compute_interest_for_year`@`year_end_summary_service.py:1207` vs per-period `calculate_interest`@`interest_projection.py:49` sum (PA-19) | SILENT/SCOPE | PA-06 (leap-year, documented) |
| `growth` | G1 `growth_engine.project_balance`@`growth_engine.py:164` vs `_project_retirement_accounts`@`retirement_dashboard_service.py:338`; G4 `_compute_savings_progress`@`year_end_summary_service.py:887` vs growth_engine per-period sum (W-159); G3 (pct) must NOT be compared with G1/G2/G4 (money) | DEFINITION (token overload)/PLAN | Q-15; A-01 caveat (Gate D) |
| `employer_contribution` | `investment.py:187` (dashboard card) vs `growth_engine.project_balance`@`growth_engine.py:265` (chart); per-period vs annual `year_summary_employer_total`@`year_end_summary_service.py:887` | SILENT/SCOPE | A-01 caveat (Gate D) |
| `transfer_amount` | stored `Transfer.amount` (cell render) vs `_compute_transfers_summary`@`year_end_summary_service.py:636` (year-end per-dest, `t.amount`@`:679`) vs shadow `Transaction.effective_amount` the balance calc sums (Invariant 3); double-count cross-check | SILENT/DEFINITION | -- |
| `effective_amount` | the ~43-site consolidated bypass table (`02_concepts.md` effective_amount entry): every direct `actual_amount`/`estimated_amount` read vs the 4-tier `Transaction.effective_amount`@`transaction.py:221-245`; highest-risk = hand-rolled mirrors (`budget_variance_service.py:390-393`, `credit_workflow.py:229`, `_transaction_cell.html:17`, `_mobile_grid.html:92,179`) | SILENT/DEFINITION | Q-08 (entry-progress rows); Q-14 (write paths) |
| `goal_progress` | GP2 `_entry_progress_fields`@`dashboard_service.py:203` vs `companion.py:53-56` inline pct (same quantity, 2 impls); GP1 `calculate_required_contribution`@`savings_goal_service.py:109` across /savings/dashboard/retirement; GP1 vs GP2 not numerically comparable | DEFINITION (token overload) | Q-08 (GP2 base) |
| `entry_sum_total` | Python `build_entry_sums_dict`/`compute_entry_sums`@`entry_service.py:371,348` vs SQL `func.sum(TransactionEntry.amount)`@`year_end_summary_service.py:519` (+credit `case` `:520-528`); `compute_actual_from_entries`@`:428` vs displayed `entry_total` | SCOPE/SILENT | Gate A new; cross-link Q-14 |
| `entry_remaining` | `compute_remaining`@`entry_service.py:405` vs Jinja `t.estimated_amount - es.total`@`grid/_transaction_cell.html:21` / `_mobile_grid.html:96,183`; estimated-vs-actual base | SILENT (template arith) | **Gate A new; BLOCKED Q-08** |
| `year_summary_jan1_balance` | Jan-1 endpoint via `_get_account_balance_map`/`_build_account_data`@`year_end_summary_service.py:750` (inside `_compute_net_worth`@`:689`) vs `_compute_account_projections`@`savings_dashboard_service.py:294` | PLAN/SOURCE | Q-15 |
| `year_summary_dec31_balance` | Dec-31 net-worth investment via `_get_account_balance_map`@`year_end_summary_service.py:750` vs `_project_investment_for_year` via `_compute_savings_progress`@`:887` (W-159 equality) | PLAN/SOURCE | Q-15 |
| `year_summary_principal_paid` | `_compute_debt_progress`@`year_end_summary_service.py:824` (jan1-dec31 delta @`:871`) vs `principal_paid_per_period` from `generate_schedule`@`amortization_engine.py:326` summed over the year (A-06-preprocessed schedule) | SCOPE | A-06; inherits A-05 |
| `year_summary_growth` | YG1 `_compute_savings_progress`@`year_end_summary_service.py:887` vs growth_engine per-period (W-159); YG2 `_compute_interest_for_year`@`:1207` vs `calculate_interest`@`interest_projection.py:49` sum (PA-19); YG3 `_compute_mortgage_interest`@`:380` vs `generate_schedule`@`amortization_engine.py:326` (A-06); YG1/YG2/YG3 not summable as one | DEFINITION (token overload)/SCOPE | A-06; Q-15 |
| `year_summary_employer_total` | annual `_compute_savings_progress`@`year_end_summary_service.py:887` (via `_project_investment_for_year`) vs per-period `growth_engine.calculate_employer_contribution`@`growth_engine.py:91` sum over the year | SCOPE | Q-15; A-01 caveat |

Single-path concepts -- Phase 3 performs a scoped **internal-consistency verify only** (no
cross-producer pair): `loan_remaining_months` (every ARM site derives `n` from
`calculate_remaining_months`@`amortization_engine.py:128`, `as_of=today`; the shared input that
makes the monthly_payment invariant tractable); `contribution_limit_remaining` (route `limit -
ytd`@`investment.py:173-181` vs `calculate_investment_inputs`@`investment_projection.py:100`
output, no recompute drift); `ytd_contributions` (estimated_amount bypass contract-safe --
upstream `status.excludes_from_balance` honored, `investment_projection.py:153,187`);
`emergency_fund_coverage_months` (months/paychecks/years internally consistent; 26/12 factor
@`savings_goal_service.py:168`); `cash_runway_days` (its `current_balance` base ==
`checking_balance` on the same dashboard); `transfer_amount_computed` (prefill ==
`monthly_payment`+`escrow_per_period` / `limit/26`); `pension_benefit_annual`/`_monthly`
(feed-consistency from `project_salaries_by_year`@`pension_calculator.py:78`; pass-through to
retirement-gap with no recompute).

### E2 -- Definition-discrepancy register (flagged, not reconciled -- Phase 3 input)

| Concept | Competing definitions | Citations |
| --- | --- | --- |
| `period_subtotal` | D1 grid display (effective_amount expense, Projected) / D2 balance-calc internal (`_entry_aware_amount` expense) / D3 per-domain analytics (settled-only, abs-expense, monthly-equiv) | `grid.py:263-279`; `_sum_remaining`/`_sum_all`@`balance_calculator.py:389,422`,`:292`; `year_end_summary_service.py:414`, `budget_variance_service.py:99`, `spending_trend_service.py:97`, `savings_goal_service.py:287` |
| `total_interest` | D1 life-of-loan (sum all schedule rows) / D2 calendar-year mortgage (A-06-preprocessed, `payment_date.year==year`) | `_derive_summary_metrics`@`amortization_engine.py:642-644`; `_compute_mortgage_interest`@`year_end_summary_service.py:380-406`; C-05 (`00_priors.md:648-651`); W-293/W-362 |
| `taxable_income` | D1 display (`gross - pre_tax`, floor 0) / D2 federal-internal (`- pre_tax - std_ded`, annualized) / D3 state-internal (`annual_gross - std_ded`) / D4 legacy (`annual_gross - std_ded`, NO pre_tax; **dead, 0 consumers**) | `paycheck_calculator.py:155-157`; `tax_calculator.py:112-120`; `:263`; legacy `:233` (`grep` zero `app/` consumers) |
| `growth` | G1 investment growth (money) / G2 reverse growth (money) / G3 spending-trend pct (rate) / G4 year-end return/interest aggregates (money) -- token overload, mixed units | `growth_engine.py:164,297`; `spending_trend_service.py:97,470`; `year_end_summary_service.py:887,1207,380,824` |
| `year_summary_growth` | YG1 investment growth / YG2 interest-account growth / YG3 mortgage interest paid -- one token, three quantities, NOT summable | `_compute_savings_progress`@`year_end_summary_service.py:887`; `_compute_interest_for_year`@`:1207`; `_compute_mortgage_interest`@`:380` |
| `goal_progress` | GP1 savings-goal completion (money + ETA) / GP2 entry-tracked spend pct (percentage, float at `companion.py:54`) -- mixed units | `savings_goal_service.py:21,109,331`; `dashboard_service.py:203` (`:238`); `companion.py:53-56` |
| `debt_total` | stored `LoanParams.current_principal` base (`_compute_debt_summary`) vs amortization-derived liability (`_compute_net_worth`) -- differ for fixed-rate w/ confirmed payments (A-04) | `savings_dashboard_service.py:840,855`; `year_end_summary_service.py:689`; A-04 |
| `loan_principal_displayed` | STORED column (`loan/dashboard.html:104`) vs engine-real (`proj.current_balance`, refinance/debt-strategy) -- same loan, different base | `loan.py:557`; `loan.py:1087`; `debt_strategy.py:147`; A-04 / Q-11 |
| `monthly_payment` | one canonical formula, but `using_contractual` (sites 1-2/5-6, `original_principal/term_months`) vs `is_arm`-discriminated (sites 7-16, `current_principal/stored rate`) -- a discriminator-type fork, not a formula fork | `02_concepts.md:627-645` 16-site table; C-04; A-05 |
| `fica` | bracket path enforces SS wage-base cap vs calibrated path applies `gross*rate` with NO cap | `tax_calculator.py:300-306` vs `calibration_service.py:139-144`; PA-21 |
| `federal_tax`/`state_tax` | bracket-based vs calibrated (`taxable*effective_rate`) -- DIFFER by design when calibration active (labeled intentional, NOT drift) | `tax_calculator.py:35`/`:240` vs `calibration_service.py:133`/`:136` |
| `interest_paid_per_period` | raw per-row schedule interest vs A-06-preprocessed (escrow-subtracted + biweekly-redistributed) -- same formula, different payment inputs | `amortization_engine.py:517` vs `year_end_summary_service.py:380` (A-06) |

### E3 -- Blocked register (what developer input unblocks each)

**3 PRIMARY PATH: UNKNOWN concepts** (independently re-derived: exactly 3 of 47, see
Verification d):

| Concept | Governing Q | Unblocks when developer... |
| --- | --- | --- |
| `period_subtotal` | Q-10 | states whether the per-period subtotal is a grid display detail or a shared financial concept (and, if shared, picks `effective_amount` vs `_entry_aware_amount` as canonical expense). |
| `debt_total` | Q-15 | names the canonical aggregate-debt base: savings-dashboard stored `current_principal` vs year-end amortization-real (`_get_account_balance_map`). |
| `loan_principal_displayed` | Q-11 | states which principal a user-facing page MUST show (stored vs engine-real) so the dashboard card vs refinance prefill reconcile. |

**Concepts gated on a PENDING proposed answer (Q-08..Q-15 are "A-NN proposed, pending developer
confirmation" -- NOT resolved):**

| Concept(s) | Pending Q | What it gates |
| --- | --- | --- |
| `entry_remaining`, `goal_progress` GP2, `effective_amount` (Q-08 bypass rows `dashboard_service.py:239,245`) | Q-08 | AGREE (estimated base) vs DIVERGE (re-anchor on actual_amount for settled txn). A-08 proposed. |
| `monthly_payment` (and inheritors `total_interest`/`interest_saved`/`payoff_date`/`principal_paid_per_period`) | Q-09 | `debt_strategy.py:127` unconditional ARM-formula intent on every loan; the 16-site count (A-09 proposed = 16). |
| `period_subtotal` (obligations path), `dti_ratio`, `emergency_fund_coverage_months` (26/12 factor) | Q-12 | obligations route-resident aggregation as SRP; mortgage double-count; 26/12 duplication. A-12 proposed. |
| `taxable_income`, `federal_tax`, `pre_tax_deduction` | Q-13 | calibrate_preview inline `gross - total_pre_tax`@`salary.py:1095` pct-base bias. A-13 proposed. |
| `effective_amount` (write rows `dashboard.py:128`/`transactions.py:614`), `entry_sum_total` | Q-14 | `mark_paid` vs `mark_done` settle-divergence (`actual_amount` write, envelope auto-settle). A-14 proposed. |
| `account_balance`, `savings_total`, `debt_total`, `net_worth`, `year_summary_jan1/dec31/growth/employer_total`, `loan_principal_real` | Q-15 | which per-account dispatcher (`_compute_account_projections` vs `_get_account_balance_map`) is canonical; whether net_worth_amort W-152 is "code catches up" or "plan superseded". |

Phase 3 may proceed on the non-blocked concepts; blocked rows record verdict AGREE/DIVERGE/
UNKNOWN conditionally and the developer's Q answers finalize them.

### E4 -- Prior-audit (PA-*) linkage rollup

Per in-scope PA-* (`00_priors.md:658-694`), what Phase 2 evidence now says (for Phase 8
prioritization). "Concrete" = Phase 2 located the live code; "test-gap" = Phase 7.

| PA | Priors status | Phase-2 evidence (concept @ site) | Now |
| --- | --- | --- | --- |
| PA-01 | open | `growth` G3: `UserSettings.trend_alert_threshold`@`user.py:246` feeds `spending_trend_service` threshold-flag | concrete (schema range; Phase 8) |
| PA-02 | open | `state_tax`/`fica`/`federal_tax`: `StateTaxConfig.flat_rate`/`FicaConfig.*`/`TaxBracket.rate` `Numeric(5,4)` (P2-c names PA-02 PRIMARY for state_tax/fica) | concrete (Phase 8) |
| PA-03 | open | `checking_balance` consumer route `grid.balance_row`@`grid.py:393` (None-deref) | concrete (Phase 3/8) |
| PA-04 | **open (priors)** | `growth`: `compute_slider_defaults`@`retirement_dashboard_service.py:257` **docstring (Read-confirmed) claims Decimal+zero-SWR-explicit = REMEDIATED**, priors still marks open | **needs Phase-3 BODY verification at `:257`** -- verify-by-code, not docstring |
| PA-05 | open | `growth`/`employer_contribution`: `_DEFAULT_SWR_PCT`/`_DEFAULT_RETURN_PCT` named in `compute_slider_defaults` docstring -- candidate remediation | Phase 3 verifies source citations |
| PA-06 | open | `apy_interest`: `DAYS_IN_YEAR=Decimal("365")`@`interest_projection.py:44` **Read-confirmed LIVE**; documented accepted simplification @docstring `:7-34` | **concrete & live** (documented-accepted; Phase 3/8) |
| PA-07 | open | `paycheck_gross`/`paycheck_net`: canonical gross @`paycheck_calculator.py:133-135` IS F-127; off-engine `:266` compounds it w/ A-01 mode | concrete (Phase 3/8) |
| PA-08 | open | `transfer_amount`: `carry_forward_unpaid`@`carry_forward_service.py:291` lacks `scenario_id` | concrete (Phase 3/8) |
| PA-09 | partially-remediated | `checking_balance`/`projected_end_balance`: Projected-only exclusion verified at `_sum_remaining`/`_sum_all` gate (A-02/W-091/W-092) | concrete (symptom #1; Phase 3/5) |
| PA-10/11 | open | `checking_balance` penny-accuracy/idempotency | test-gap (Phase 7) |
| PA-12 | partially-remediated | `loan_principal_real`/`principal_paid_per_period` debt-balance directional-only | test-gap (Phase 7) |
| PA-13/14/15/16 | open | `monthly_payment`/`loan_principal_*` sad/boundary/SM/negative | test-gap (Phase 7) |
| PA-17 | partially-remediated | `apy_interest` no exact HYSA interest | test-gap (Phase 7) |
| PA-18 | open | `apy_interest` invalid `compounding_frequency` (`else: return ZERO`@`interest_projection.py:111-112`) | test-gap (Phase 7) |
| PA-19 | unclear | `apy_interest`: the `_compute_interest_for_year` vs per-period-sum pair IS the uncovered 26-period invariant | test-gap (Phase 7; pair in E1) |
| PA-20 | partially-remediated | `paycheck_net` IS the "no full-year net-pay sum test" gap (direct) | test-gap (Phase 7) |
| PA-21 | open | `fica`: cap ENFORCED bracket path @`tax_calculator.py:300-306`, **NOT** calibration path @`calibration_service.py:139-144` -- P2-c **confirms AND extends** PA-21 | **concrete** (Phase 3 DEFINITION_DRIFT + Phase 7) |
| PA-22 | open | `paycheck_gross`/`net`/`federal_tax` zero/negative salary, `pp=0` (`or 26` guards) | test-gap (Phase 7) |
| PA-23 | partially-remediated | `federal_tax` (PRIMARY)/`state_tax`/`fica`/`taxable_income` Pub 15-T exact-value | test-gap (Phase 7) |
| PA-24 | open | `federal_tax`/`state_tax`/`fica`/`paycheck_net` year-end 26-period vs annual -- analytical-frame PHASE 3 REQUIRED | concrete (Phase 3) + test-gap |
| PA-25 | open | `transfer_amount`: recurrence producers (`transfer_recurrence.py:43` etc.) boundary | test-gap (Phase 7) |
| PA-26 | open | `chart_balance_series`/`year_summary_jan1/dec31` chart value-verification | test-gap (Phase 7) |
| PA-27 | partially-remediated | `interest_saved`/`months_saved`/`year_summary_principal_paid` directional | test-gap (Phase 7) |
| PA-28 | open | `loan_remaining_months`: producer `calculate_remaining_months`@`amortization_engine.py:128` zero coverage | test-gap (Phase 7) |
| PA-29 | partially-remediated | `growth`/`employer_contribution`/`year_summary_growth`/`_employer_total` directional | test-gap (Phase 7) |
| PA-30 | partially-remediated | `pension_benefit_annual`/`_monthly` directional | test-gap (Phase 7) |

**Gate E verdict: PASS** (E1-E4 delivered; E1 is exhaustive and self-contained).

---

## Gate F -- Seam closure verification

| Seam | Status | Evidence |
| --- | --- | --- |
| **F1 -- `dti_ratio` income denominator** (P2-c reported CLOSED -- **verified at source, not trusted**) | **CLOSED** | Read `savings_dashboard_service.py:160-179` and `:260-266`. Confirmed: `dti = total_monthly_payments / gross_monthly * 100`@`:173-176`; `gross_monthly = (gross_biweekly*26/12).quantize(_TWO_PLACES,HALF_UP)`@`:170-172`; `gross_biweekly = params["salary_gross_biweekly"]`@`:168`; the base is the **off-engine** `salary_gross_biweekly = (Decimal(str(annual_salary)) / (pay_periods or 26)).quantize(Decimal("0.01"))`@`:263-266` -- raw `annual_salary` (NO `_apply_raises`), **default banker's `ROUND_HALF_EVEN`** (A-01 24-list site `:266`). P2-c's characterization (raise omission + rounding-mode) is accurate. Debt side `_compute_debt_summary`@`:835-864` verified in Gate B. The seam between P2-b (debt numerator) and P2-c (`paycheck_gross` denominator) resolves; both sides agree on the divergence. |
| **F2 -- `net_worth`/`savings_total`/`debt_total` vs `year_summary_*`** | **CLOSED** (discrepancy documented + Q-15-governed) | `year_summary_jan1/dec31` share the SAME producer `_compute_net_worth`@`year_end_summary_service.py:689` as P2-a `net_worth` (consistent by construction). `savings_total` vs `net_worth` investment Dec31: W-159 equality flagged PHASE 3 (E1). `debt_total` vs `net_worth` loan liability: explicitly flagged DEFINITION discrepancy (stored vs amortization base, A-04) + UNKNOWN Q-15. Cross-links resolve; the two sides agree the discrepancy exists and is Q-15-governed. |
| **F3 -- `effective_amount` vs `period_subtotal`/`checking_balance`** | **CLOSED** (Phase 3 input documented) | `period_subtotal` D2 and `checking_balance` (`_sum_remaining`/`_sum_all`) use `_entry_aware_amount`@`balance_calculator.py:292` -- a bypass row in the `effective_amount` consolidated table (verdict-candidate AGREE-intentional). Both sides agree the divergence is the expense formula (`effective_amount` vs `_entry_aware_amount`), not the Projected-only filter (A-02/W-091/W-092 verified). period_subtotal Pair C and the effective_amount bypass table reference each other. |
| **F4 -- `transfer_amount` vs shadow handling** | **CLOSED** (Invariant 5 verified) | `effective_amount` entry + the "every `budget.transfers` read classified" table: Invariant 5 HOLDS (grep-confirmed zero `query(Transfer)`/`from app.models.transfer` in `balance_calculator.py`; docstring `:17-19`). The single money aggregation over `budget.transfers` is the year-end `_compute_transfers_summary`@`year_end_summary_service.py:636` display total, flagged for the Phase-3 double-count cross-check vs the shadow-`Transaction`-based net_worth/account_balance (Invariant 3). Cross-links resolve. |

**Gate F verdict: PASS.** All four cross-family seams resolve; F1 source-verified (not trusted);
F2-F4 documented discrepancies are Q-15-governed or flagged Phase-3 inputs with both sides
agreeing on the definition.

---

## Verification before completion (a-e)

- **(a) Independent grep -- zero uncataloged Appendix-A/1.7.2 tokens.** Per-token
  `grep -c "^## Concept: <token>"` over all 42 Appendix-A tokens = 1 each; 1.7.2 own-section
  tokens = 1 each; `entry_sum_total`/`entry_remaining` were 0 and are now cataloged by Gate A;
  `paycheck_breakdown`/`chart_date_labels` formally dispositioned (alias / display-only-non-
  money). **Zero uncataloged financial-figure tokens. HOLDS.**
- **(b) Gate B 12-entry spot-check table present with verdicts; pass rate stated.** Table
  above; **12/12 PASS, 0 failures** (FAIL threshold 2+). **HOLDS.**
- **(c) effective_amount bypass count independently re-derived.**
  `grep -rn "actual_amount\|estimated_amount" app/ | wc -l` = **170** raw textual references
  (services 72, routes 34, templates 35, models 17, **static/js 0**). The catalog's **~43** is
  the semantic **bypass-SITE** count, not the raw textual count: 170 collapses to ~43 once the
  canonical property body (`transaction.py:221-245`, multi-line), the 1.5 column/CHECK
  definitions (17 model refs are inputs, not bypasses), the form-input `value="{{ ...
  }}"` bindings (raw-column edit by design -- catalog explicitly excludes), multi-ref-per-line
  collapse, and the WRITE/READ split (Q-14 writes counted separately) are removed. Per-layer
  ordering corroborates (services 72>>templates 35>routes 34>>**JS 0**); **JS=0 exactly
  matches** the catalog's "JS: NONE / 0 sites" (independent grep excluding
  `effective_month/year/inflation_effective` = 0). The ~43 (25 svc + 5 route + ~13 tmpl + 0 JS)
  is a defensible, documented reduction. **No material discrepancy. HOLDS.**
- **(d) Cumulative PRIMARY PATH: UNKNOWN recount.** Independent grep of the literal verdict line
  `**Primary path.** `PRIMARY PATH: UNKNOWN`` = exactly **3** concept verdicts: `period_subtotal`
  (`:323`, Q-10), `debt_total` (`:511`, Q-15), `loan_principal_displayed` (`:882`, Q-11). The
  other string occurrences (`:8` schema header; `:1352`/`:1970`/`:1980` session tallies;
  `:2859` checklist) are prose, not verdicts (awk mis-attributed them to the nearest preceding
  concept -- `dti_ratio`/`post_tax_deduction`/`year_summary_employer_total` actual verdicts are
  "not UNKNOWN"/"PRIMARY PATH known"). **= 3 of 47; matches P2-d's cumulative tally; each has a
  governing filed Q. HOLDS.**
- **(e) Gate C two-way diff present with zero MISS-class mismatches.** Both direction tables
  above; 12 Direction-1 escalations/overrides + 3 Direction-2 resolutions, **all carried by the
  catalog + E1 register**; zero MISS-class. **HOLDS.**

---

## Phase 2 complete

Gates A-F all PASS; verification a-e all hold.

- **Concept count: 47 + 2 = 49.** P2-a(8) + P2-b(13) + P2-c(8) + P2-d(18) = 47 family-cataloged
  concepts; + 2 Gate-A orphan catalogs (`entry_sum_total`, `entry_remaining`). Every audit-plan
  Appendix-A token (42) and every actionable inventory-1.7.2 addition is mapped; `paycheck_breakdown`
  (alias) and `chart_date_labels` (display-only non-money) are formally dispositioned.
- **3 blocked (PRIMARY PATH: UNKNOWN) concepts:** `period_subtotal` (Q-10), `debt_total`
  (Q-15), `loan_principal_displayed` (Q-11) -- each has a governing filed question. A wider set
  of concepts is gated on PENDING proposed answers Q-08..Q-15 (E3) -- Phase 3 records those
  conditionally.
- **Producer attribution** independently re-verified (Gate B 12/12); the 1.7.8 over-attribution
  corrections from P2-a/b/c/d hold; Gate A corrected one further 1.7.3 over-attribution
  (`build_entry_sums_dict` is not an `entry_remaining` producer).
- **Phase 3's worklist is E1** (self-contained), supplemented by E2 (definition discrepancies),
  E3 (blocked), E4 (PA-* rollup), and the Gate D A-01/A-05 caveats. 1.7.4 alone is insufficient
  for Phase 3 (12 catalog escalations/overrides); the catalog + E1 supersede it operationally.

This statement supersedes the P2-d marker comment ("Do not mark Phase 2 complete here; that is
P2-reconcile's gate"). P2-a/b/c/d content is unmodified. **Phase 3 may begin** (required
reading per audit-plan 10.3: `00_priors.md`, `01_inventory.md`, `02_concepts.md` -- the latter
including this reconciliation section and its E1 register).

--- Q-answer reconciliation (2026-05-18) ---

Trigger: every Q raised by Phases 0-4 was adjudicated by the developer
2026-05-18 and recorded as behavioral expectations E-18..E-28 in
`00_priors.md` 0.3 (with A-NN in `09_open_questions.md`). This section
reconciles the Phase-2 primary-path verdicts that were `PRIMARY PATH: UNKNOWN`
pending those answers. Additive only: no P2 entry text is rewritten; the
original `UNKNOWN` verdicts stand as the point-in-time record and are
superseded here.

Trust-but-verify: resolutions are re-derived independently from the locked
E-18..E-28 / A-NN, not inherited from any prior gate. The concept
producer/consumer FACTS recorded in P2 are unchanged (no code re-proof);
only the previously-blocked "which producer is canonical" axis is now set.

Completeness: `02_concepts.md` self-certifies "exactly 3 of 47" concepts at
`PRIMARY PATH: UNKNOWN` (gate (d) / cumulative tally). All 3 reconciled below
-> count parity, complete.

| Concept (P2 line) | Was | Now -> canonical primary path | Governing |
|---|---|---|---|
| `period_subtotal` (~323) | UNKNOWN (Q-10) | RESOLVED -- one service producer; expense base `_entry_aware_amount` shared with `balance_calculator`; subtotal.net reconciles with the running-balance delta by construction | A-10 -> E-25 |
| `debt_total` (~511) | UNKNOWN (Q-15) | RESOLVED -- stored-`current_principal` base RETIRED; aggregate derives from the single loan-state resolver; W-152 becomes "code must catch up to E-18" | A-15 -> E-18 |
| `loan_principal_displayed` (~882) | UNKNOWN (Q-11) | RESOLVED -- no authoritative stored `current_principal`; every page reads the one resolver, equal by construction for the same loan-on-date | A-11/A-22 -> E-18 |

`entry_remaining` for a settled txn (Q-08) is explicitly NOT a 4th
`PRIMARY PATH: UNKNOWN` (P2 confirms the function is canonical); it is an
estimated-vs-actual base axis handled in `03_consistency.md` F-028 / F-056
and resolved by A-08 -> E-21. No new P2 concept is affected.

**Phase 5 may consume `02_concepts.md` with all primary paths resolved.**
