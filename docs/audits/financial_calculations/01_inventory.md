> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 1: Calculation Surface Inventory

> **A SNAPSHOT, not a statement of the current code.** Every row carries the LINE NUMBERS and the
> render contract of the session that wrote it, and the sessions ran May-June 2026. Many are now
> wrong in both directions -- `transactions.get_quick_create` and `get_full_create` no longer
> resolve a `PayPeriod` or render a `period` (pay-calendar plan step **C2-f3e**, 2026-08-20);
> `carry_forward_preview` no longer calls `pay_period_service.get_current_period`, which
> **C2-f3a** deleted. Cite this file for what the audit FOUND, never for what the app does; the
> code as committed is the source of truth. Noted rather than patched at C2-f3e, because
> correcting two rows of a table that lags throughout would imply the rest does not.

Layer-by-layer enumeration. Each Phase 1 session appends one layer. Reader should be able to grep
this file by concept token and find every location that produces or consumes that concept.

Audit plan: @docs/financial_calculation_audit_plan.md Priors:
@docs/audits/financial_calculations/00_priors.md Open questions:
@docs/audits/financial_calculations/09_open_questions.md (Q-01 through Q-07 carry developer answers
A-01 through A-07 dated 2026-05-13; cited inline below where a column or property maps to a resolved
cross-plan answer.)

Session ledger:

- 1.5 Models and 1.6 DB aggregates: P1-a, 2026-05-15.
- 1.1 Service layer: P1-b, 2026-05-15.
- 1.2 Route layer: P1-c, 2026-05-15.
- 1.3 Template layer + 1.4 Static/JS: P1-d, 2026-05-15.
- 1.7 Wrap-up: P1-e (pending).

## Controlled vocabulary

Tokens from Appendix A (section 12) of the audit plan plus additions made during Phase 1. Every
token carries a one-sentence definition naming the value produced and its units (Decimal money,
integer count, percentage, date). The definitions describe what the value IS, not which function
computes it; producer/consumer lists live in section 1.7.3.

Appendix A starter set (42 tokens):

- `checking_balance` -- Decimal dollar balance of a checking account at a specific pay-period anchor
  or at the end of a specific pay period; the "spendable balance" for budgeting decisions.
- `account_balance` -- Decimal dollar balance of any account (checking, savings, loan, investment)
  at a specific pay period; superset of `checking_balance`.
- `projected_end_balance` -- Decimal dollar balance of an account projected to the end of a
  non-current pay period via the balance calculator.
- `period_subtotal` -- Decimal dollar sum of transactions in one pay period, partitioned into
  income, expense, and net components.
- `loan_principal_real` -- Decimal dollar outstanding principal of a loan derived from the
  engine-walked schedule (fixed-rate per A-04) or from the stored anchor (ARM per A-04); the value
  the audit treats as authoritative for projection.
- `loan_principal_stored` -- Decimal dollar value of `LoanParams.current_principal` as persisted in
  the DB; AUTHORITATIVE for ARM per A-04, otherwise a snapshot for display.
- `loan_principal_displayed` -- (orphan; see 1.7.2.) Reserved by Appendix A for any third
  loan-principal flavor distinct from `_real` or `_stored`; no body site uses it.
- `monthly_payment` -- Decimal dollar value of a loan's amortized monthly payment derived from
  `(current_principal, current_rate, remaining_months)` via the standard formula; stable within an
  ARM's fixed-rate window per E-02.
- `principal_paid_per_period` -- Decimal dollar principal portion of one period's loan payment
  derived from the schedule.
- `interest_paid_per_period` -- Decimal dollar interest portion of one period's loan payment derived
  from the schedule.
- `escrow_per_period` -- Decimal dollar escrow component of one period's loan payment, summed from
  active EscrowComponents and optionally inflated.
- `payoff_date` -- Calendar date (Python `date`) on which a loan's outstanding principal reaches
  zero under the projected schedule.
- `months_saved` -- Integer count of months a payoff is accelerated relative to the contractual
  schedule under a specified extra-payment plan.
- `total_interest` -- Decimal dollar sum of all interest paid over the life of a loan (or over the
  year, for year-end summaries).
- `interest_saved` -- Decimal dollar difference between contractual lifetime interest and
  accelerated lifetime interest under a specified extra-payment or refinance plan.
- `apy_interest` -- Decimal dollar interest accrued on an interest-bearing account over one pay
  period using actual/365 daily/monthly/quarterly compounding
  (`interest_projection.calculate_interest`).
- `growth` -- Decimal-fraction or Decimal dollar value representing a growth, inflation, or trend
  rate (e.g., investment return rate, raise percentage, escrow inflation, trend-alert threshold);
  units vary by sub-concept and are documented per producer.
- `employer_contribution` -- Decimal dollar amount of employer 401(k) match for one pay period
  derived from flat-percentage or match-percentage plus cap rules.
- `contribution_limit_remaining` -- Decimal dollar amount of annual 401(k) contribution headroom
  remaining (`limit - YTD`) at a specific point in the calendar year.
- `ytd_contributions` -- Decimal dollar year-to-date employee 401(k) contribution total at a
  specific point in the calendar year.
- `paycheck_gross` -- Decimal dollar gross wage for one pay period after raises and before
  deductions/taxes.
- `paycheck_net` -- Decimal dollar net (take-home) wage for one pay period after taxes and all
  deductions.
- `taxable_income` -- Decimal dollar income subject to federal/state income tax after pre-tax
  deductions and standard deduction.
- `federal_tax` -- Decimal dollar federal income tax withheld for one pay period via the IRS Pub
  15-T Percentage Method.
- `state_tax` -- Decimal dollar state income tax withheld for one pay period via the configured flat
  rate (or zero for no-tax states).
- `fica` -- Decimal dollar Social Security + Medicare withholding for one pay period (with
  cumulative-wage cap for SS and threshold-based surtax for Medicare).
- `pre_tax_deduction` -- Decimal dollar pre-tax payroll deduction (e.g., 401(k), pre-tax insurance)
  for one pay period.
- `post_tax_deduction` -- Decimal dollar post-tax payroll deduction (e.g., Roth 401(k), post-tax
  insurance) for one pay period.
- `transfer_amount` -- Decimal dollar amount of a stored `Transfer.amount`; the canonical value
  mirrored to both shadow transactions per Transfer Invariant 3.
- `effective_amount` -- Decimal dollar amount used in balance computation: returns `Decimal("0")`
  for soft-deleted or status-excluded transactions, else `actual_amount` if non-null, else
  `estimated_amount` (`Transaction.effective_amount` property; `Transfer.effective_amount` is the
  simpler transfer variant).
- `goal_progress` -- Decimal-fraction or Decimal-percent value representing how close a savings goal
  is to its target (`current_balance / target_amount`).
- `emergency_fund_coverage_months` -- Decimal-fraction count of months of average monthly expenses
  an emergency-fund balance covers (`savings_balance / avg_monthly_expenses`).
- `dti_ratio` -- Decimal-fraction debt-to-income ratio
  (`total_monthly_debt_payments / gross_monthly_income`).
- `net_worth` -- Decimal dollar `(sum of asset balances) - (sum of debt balances)` at a specific
  date.
- `savings_total` -- Decimal dollar sum of all savings and investment account balances (excludes
  checking and debt).
- `debt_total` -- Decimal dollar sum of all liability/debt account balances.
- `chart_balance_series` -- List of Decimal dollar balance values aligned to `chart_date_labels`;
  the per-period or per-month data points a chart renders.
- `year_summary_jan1_balance` -- Decimal dollar account balance on January 1 of the configured
  calendar year (year-end summary).
- `year_summary_dec31_balance` -- Decimal dollar account balance on December 31 of the configured
  calendar year (year-end summary).
- `year_summary_principal_paid` -- Decimal dollar sum of principal paid down on a debt account over
  the configured calendar year.
- `year_summary_growth` -- Decimal dollar sum of growth (interest accrued or investment-return
  growth) on a savings/investment account over the configured calendar year.
- `year_summary_employer_total` -- Decimal dollar sum of employer contributions deposited to an
  investment account over the configured calendar year.

P1-b additions (4 tokens; first introduced in section 1.1 -- citations in 1.7.2):

- `pension_benefit_annual` -- Decimal dollar annual defined-benefit pension amount
  (`pension_calculator.calculate_benefit`).
- `pension_benefit_monthly` -- Decimal dollar monthly defined-benefit pension amount =
  `pension_benefit_annual / 12`.
- `loan_remaining_months` -- Integer count of months until a loan's contractual maturity from the
  current date.
- `cash_runway_days` -- Integer count of days a current checking balance covers at the
  trailing-30-day daily average of paid expenses.

P1-c additions (5 tokens; first introduced in section 1.2 -- citations in 1.7.2):

- `entry_sum_total` -- Decimal dollar sum of `TransactionEntry.amount` for a transaction (cleared +
  uncleared, with credit and debit partition available).
- `entry_remaining` -- Decimal dollar value `estimated_amount - sum(entries)` for an entry-tracked
  transaction; negative = overspent.
- `paycheck_breakdown` -- Bundle of
  `paycheck_gross + paycheck_net + federal_tax + state_tax + fica + pre_tax_deduction + post_tax_deduction + employer_contribution`
  returned as a `PaycheckBreakdown` dataclass; single render unit at the salary breakdown /
  projection / list pages.
- `chart_date_labels` -- List of human-readable date strings (e.g. "May 2026") rendered alongside
  `chart_balance_series` as chart x-axis labels; presentation-only formatting of period start_date.
- `transfer_amount_computed` -- Decimal dollar route-derived pre-fill value for a new recurring
  payment-transfer Transfer (loan: P&I+escrow; investment: limit/26 with $500 fallback); distinct
  from `transfer_amount` (stored) because the user can override before submit.

The raw list below is kept for tooling that greps by token name; the authoritative definitions are
above. The orphan `loan_principal_displayed` is retained in the list per the audit plan rule that
Appendix A's starter set defines the contract, even when a token is currently unused.

```text
checking_balance
account_balance
projected_end_balance
period_subtotal
loan_principal_real
loan_principal_stored
loan_principal_displayed
monthly_payment
principal_paid_per_period
interest_paid_per_period
escrow_per_period
payoff_date
months_saved
total_interest
interest_saved
apy_interest
growth
employer_contribution
contribution_limit_remaining
ytd_contributions
paycheck_gross
paycheck_net
taxable_income
federal_tax
state_tax
fica
pre_tax_deduction
post_tax_deduction
transfer_amount
effective_amount
goal_progress
emergency_fund_coverage_months
dti_ratio
net_worth
savings_total
debt_total
chart_balance_series
year_summary_jan1_balance
year_summary_dec31_balance
year_summary_principal_paid
year_summary_growth
year_summary_employer_total
pension_benefit_annual
pension_benefit_monthly
loan_remaining_months
cash_runway_days
entry_sum_total
entry_remaining
paycheck_breakdown
chart_date_labels
transfer_amount_computed
```

Additions during 1.5 (none). The starter set covers every numeric column in `app/models/`. Some
columns (rate inputs, inflation, calibration effective rates) map onto a downstream concept token
rather than a column-level token; this is noted in the per-column rows.

Additions during 1.1 (P1-b, 2026-05-15):

- `pension_benefit_annual` -- annual defined-benefit pension amount
  produced by `pension_calculator.calculate_benefit` at
  `app/services/pension_calculator.py:31-66` (`PensionBenefit.annual_benefit`
  field on the returned dataclass). The starter vocabulary lacked a
  pension-specific token; required for Phase 2 catalog entry.
- `pension_benefit_monthly` -- monthly defined-benefit pension amount
  produced at `app/services/pension_calculator.py:65-66` and consumed by
  `retirement_dashboard_service.compute_gap_data` and
  `retirement_gap_calculator.calculate_gap` as the
  `monthly_pension_income` argument
  (`app/services/retirement_gap_calculator.py:39`).
- `loan_remaining_months` -- count of unfulfilled loan months returned
  by `amortization_engine.calculate_remaining_months` at
  `app/services/amortization_engine.py:128-176`. Distinct from
  `payoff_date` (the calendar date) because consumers display the
  integer count separately. Used as an input to
  `calculate_monthly_payment` at the same module.
- `cash_runway_days` -- integer days of runway computed by
  `dashboard_service._compute_cash_runway` at
  `app/services/dashboard_service.py:375` (current_balance divided by
  daily-average paid expenses from a 30-day window). Distinct from
  `emergency_fund_coverage_months` because the inputs and time unit
  differ. Phase 2 must decide whether to fold this into
  `emergency_fund_coverage_months` or keep them separate.

Additions during 1.2 (P1-c, 2026-05-15):

- `entry_sum_total` -- per-transaction sum of cleared+uncleared entries
  produced for tracked transactions by `entry_service.compute_entry_sums`
  (returned as a dict per `entries.py:_render_entry_list`,
  `companion.py:_build_entry_data` at lines 50-57, and consumed in the grid
  via `build_entry_sums_dict` at `transactions.py:88`). Distinct from
  `effective_amount` because entries are the user-facing inputs that feed
  the `effective_amount` computation on the Transaction model.
- `entry_remaining` -- per-transaction remaining budget computed by
  `entry_service.compute_remaining(estimated_amount, entries)` (cited in
  `entries.py:104-106` and `companion.py:52`). Anchors the
  "remaining budget" display on cells; Phase 3 will compare against
  `effective_amount` semantics.
- `paycheck_breakdown` -- `PaycheckBreakdown` dataclass produced by
  `paycheck_calculator.calculate_paycheck` (see section 1.1) and rendered
  on the salary breakdown / projection / list pages. Single-token shorthand
  for the bundle `paycheck_gross + paycheck_net + federal_tax + state_tax
  - fica + pre_tax_deduction + post_tax_deduction + employer_contribution`;
  the route layer treats the breakdown as one rendered unit, so the audit needs a single token to
  track it across pages.
- `chart_date_labels` -- string-formatted date labels (e.g. "May 2026")
  emitted by `investment.dashboard` (`app/routes/investment.py:242-246`),
  `investment.growth_chart` (`:534`), and the loan chart helper at
  `loan.py:460`. Display-only formatting of period start_date but is
  rendered next to `chart_balance_series`, so the audit tracks it
  separately to flag any divergence between the two parallel arrays.
- `transfer_amount_computed` -- the route-derived transfer amount for new
  recurring payment transfers (loan and investment routes). Distinct from
  `transfer_amount` (which describes Transfer.amount as stored): the
  computed flavor is the route's pre-fill value that defaults to a
  derived monthly payment when the user does not override; cited at
  `loan.py:1213-1241` and `investment.py:668-670`. Phase 3 compares the
  computed flavor against the stored amount.

## 1.5 Models and computed properties

24 model files read in full (P1-a, Explore subagent, very thorough). 40 classes inventoried, 113
numeric columns, 6 `@property` accessors. NO `@hybrid_property` or `@cached_property` in scope.

For each row, the "Concept token" column gives the financial concept the column or property feeds.
Non-financial columns (sort orders, version counters, period indexes, dependent counts, FICA
day-counts) are recorded as `-` so the inventory is exhaustive across numeric columns; Phase 6's
SOLID audit needs the full surface.

CHECK constraint citations are line numbers in the same model file where `db.CheckConstraint(...)`
appears inside `__table_args__`. When a constraint exists in a migration but not in the model file,
the cell reads `MIGRATION (not in model)`. The rebuild migration
(`migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`) is the canonical source for
audit-trigger attachment but is not a CHECK source for these columns.

### `app/models/account.py`

161 lines. Classes: `Account`, `AccountAnchorHistory`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Account.current_anchor_balance | account.py:51 | Numeric(12, 2) | True | - | MIGRATION (not in model) | account_balance |
| Account.sort_order | account.py:55 | Integer | False | db.text("0") | - | - |
| Account.version_id | account.py:66 | Integer | False | "1" | account.py:35 | - |
| AccountAnchorHistory.anchor_balance | account.py:152 | Numeric(12, 2) | False | - | MIGRATION (not in model) | account_balance |

Computed properties: none.

### `app/models/calibration_override.py`

177 lines. Classes: `CalibrationOverride`, `CalibrationDeductionOverride`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| CalibrationOverride.actual_gross_pay | calibration_override.py:80 | Numeric(10, 2) | False | - | calibration_override.py:28 | paycheck_gross |
| CalibrationOverride.actual_federal_tax | calibration_override.py:81 | Numeric(10, 2) | False | - | calibration_override.py:32 | federal_tax |
| CalibrationOverride.actual_state_tax | calibration_override.py:82 | Numeric(10, 2) | False | - | calibration_override.py:36 | state_tax |
| CalibrationOverride.actual_social_security | calibration_override.py:83 | Numeric(10, 2) | False | - | calibration_override.py:40 | fica |
| CalibrationOverride.actual_medicare | calibration_override.py:84 | Numeric(10, 2) | False | - | calibration_override.py:44 | fica |
| CalibrationOverride.effective_federal_rate | calibration_override.py:89 | Numeric(12, 10) | False | - | calibration_override.py:54 | federal_tax (input) |
| CalibrationOverride.effective_state_rate | calibration_override.py:90 | Numeric(12, 10) | False | - | calibration_override.py:58 | state_tax (input) |
| CalibrationOverride.effective_ss_rate | calibration_override.py:91 | Numeric(12, 10) | False | - | calibration_override.py:62 | fica (input) |
| CalibrationOverride.effective_medicare_rate | calibration_override.py:92 | Numeric(12, 10) | False | - | calibration_override.py:66 | fica (input) |
| CalibrationDeductionOverride.actual_amount | calibration_override.py:164 | Numeric(10, 2) | False | - | calibration_override.py:136 | pre_tax_deduction |

The four Numeric(10, 2) money columns deviate from the `Numeric(12, 2)` project standard (E-14).
Flag for Phase 6 DRY/SOLID: precision drift on calibration tables, which were added later than the
canonical money columns. Whether 10,2 is correct (calibration values are bounded by a single
paycheck) or whether the standard should be enforced uniformly is a Phase 6 question, not a 1.5
finding.

Computed properties: none.

### `app/models/category.py`

47 lines. Classes: `Category`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Category.sort_order | category.py:32 | Integer | False | db.text("0") | - | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Category.display_name | category.py:40 | @property | str (inferred) | f"{self.group_name}: {self.item_name}" | group_name, item_name | - |

### `app/models/interest_params.py`

73 lines. Classes: `InterestParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| InterestParams.apy | interest_params.py:60 | Numeric(7, 5) | False | "0.04500" | interest_params.py:34 | apy_interest |

Computed properties: none.

### `app/models/investment_params.py`

99 lines. Classes: `InvestmentParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| InvestmentParams.assumed_annual_return | investment_params.py:80 | Numeric(7, 5) | False | db.text("0.07000") | investment_params.py:22 | growth |
| InvestmentParams.annual_contribution_limit | investment_params.py:84 | Numeric(12, 2) | True | - | investment_params.py:32 | contribution_limit_remaining (input) |
| InvestmentParams.contribution_limit_year | investment_params.py:85 | Integer | True | - | - | - |
| InvestmentParams.employer_flat_percentage | investment_params.py:90 | Numeric(5, 4) | True | - | investment_params.py:40 | employer_contribution (input) |
| InvestmentParams.employer_match_percentage | investment_params.py:91 | Numeric(5, 4) | True | - | investment_params.py:53 | employer_contribution (input) |
| InvestmentParams.employer_match_cap_percentage | investment_params.py:92 | Numeric(5, 4) | True | - | investment_params.py:64 | employer_contribution (input) |

Computed properties: none.

### `app/models/loan_features.py`

144 lines. Classes: `RateHistory`, `EscrowComponent`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| RateHistory.interest_rate | loan_features.py:75 | Numeric(7, 5) | False | - | loan_features.py:44 | monthly_payment (input) |
| EscrowComponent.annual_amount | loan_features.py:126 | Numeric(12, 2) | False | - | loan_features.py:104 | escrow_per_period (annual input) |
| EscrowComponent.inflation_rate | loan_features.py:127 | Numeric(5, 4) | True | - | loan_features.py:111 | growth (escrow inflation input) |

`RateHistory.interest_rate` is the ARM-anchor source flagged by C-04 in the priors; Phase 3 must
compare which entry points consume RateHistory versus the static `LoanParams.interest_rate`.

Computed properties: none.

### `app/models/loan_params.py`

74 lines. Classes: `LoanParams`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| LoanParams.payment_day | loan_params.py:58 | Integer | False | - | loan_params.py:24 | - |
| LoanParams.original_principal | loan_params.py:53 | Numeric(12, 2) | False | - | loan_params.py:28 | loan_principal_stored |
| LoanParams.current_principal | loan_params.py:54 | Numeric(12, 2) | False | - | loan_params.py:32 | loan_principal_stored |
| LoanParams.interest_rate | loan_params.py:55 | Numeric(7, 5) | False | - | loan_params.py:36 | monthly_payment (input) |
| LoanParams.term_months | loan_params.py:56 | Integer | False | - | loan_params.py:40 | - |
| LoanParams.arm_first_adjustment_months | loan_params.py:60 | Integer | True | - | - | - |
| LoanParams.arm_adjustment_interval_months | loan_params.py:61 | Integer | True | - | - | - |

`LoanParams.current_principal` is the source-of-truth column flagged by C-03/C-04 in the priors and
by E-03 in the developer expectations. Phase 4 (source-of-truth audit) is the dedicated spot for
this column. A-04 (09_open_questions.md:93-103) resolves the C-03/C-04 ARM-vs-fixed-rate split: ARM
loans use stored `current_principal` directly (`amortization_engine.py:977-985`,
`savings_dashboard_service.py:373`, `year_end_summary_service.py:1465-1469`); fixed-rate loans walk
the schedule from origination using confirmed `PaymentRecord` rows. The column is therefore
AUTHORITATIVE for ARM and CACHED-for-display for fixed-rate; Phase 4 records the dual
classification.

`LoanParams.interest_rate` (line 55) is the static rate; ARM loans override it via
`RateHistory.interest_rate` (`loan_features.py:75`). A-05 (09_open_questions.md:113-125) confirms
the eight call sites that compute ARM monthly_payment from
`(current_principal, current_rate, remaining_months)`; Phase 3 must verify all eight sites resolve
`current_rate` against the same authority for the same loan-on-date.

Computed properties: none.

### `app/models/mixins.py`

103 lines. Classes: `TimestampMixin`, `CreatedAtMixin`, `SoftDeleteOverridableMixin`. No numeric
columns or computed numeric properties of audit interest.

### `app/models/pay_period.py`

50 lines. Classes: `PayPeriod`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PayPeriod.period_index | pay_period.py:31 | Integer | False | - | pay_period.py:20 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PayPeriod.label | pay_period.py:38 | @property | str (inferred) | year-aware formatted date range (`pay_period.py:41-46`) | start_date, end_date | - |

### `app/models/paycheck_deduction.py`

154 lines. Classes: `PaycheckDeduction`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PaycheckDeduction.amount | paycheck_deduction.py:113 | Numeric(12, 4) | False | - | paycheck_deduction.py:40 | pre_tax_deduction OR post_tax_deduction (timing-dependent) |
| PaycheckDeduction.deductions_per_year | paycheck_deduction.py:114 | Integer | False | db.text("26") | paycheck_deduction.py:41 | - |
| PaycheckDeduction.annual_cap | paycheck_deduction.py:118 | Numeric(12, 2) | True | - | paycheck_deduction.py:43 | contribution_limit_remaining (input) |
| PaycheckDeduction.inflation_rate | paycheck_deduction.py:123 | Numeric(5, 4) | True | - | paycheck_deduction.py:51 | growth (deduction inflation input) |
| PaycheckDeduction.inflation_effective_month | paycheck_deduction.py:124 | Integer | True | - | paycheck_deduction.py:59 | - |
| PaycheckDeduction.sort_order | paycheck_deduction.py:130 | Integer | False | db.text("0") | - | - |
| PaycheckDeduction.version_id | paycheck_deduction.py:139 | Integer | False | "1" | paycheck_deduction.py:66 | - |

`PaycheckDeduction.amount` is `Numeric(12, 4)` (sub-cent precision), unlike the canonical
`Numeric(12, 2)`. The wider precision lets the paycheck calculator carry intermediate rounding
before quantizing the displayed paycheck. Phase 3 confirms: every consumer reads through the
calculator and quantizes at the boundary, not at the column.

Computed properties: none.

### `app/models/pension_profile.py`

95 lines. Classes: `PensionProfile`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| PensionProfile.benefit_multiplier | pension_profile.py:78 | Numeric(7, 5) | False | - | pension_profile.py:32 | - |
| PensionProfile.consecutive_high_years | pension_profile.py:79 | Integer | False | db.text("4") | pension_profile.py:37 | - |

Computed properties: none. Pension calculation logic lives in `app/services/pension_calculator.py`;
this model only persists the input parameters.

### `app/models/recurrence_rule.py`

78 lines. Classes: `RecurrenceRule`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| RecurrenceRule.interval_n | recurrence_rule.py:32 | Integer | True | - | recurrence_rule.py:17 | - |
| RecurrenceRule.offset_periods | recurrence_rule.py:34 | Integer | True | - | recurrence_rule.py:18 | - |
| RecurrenceRule.day_of_month | recurrence_rule.py:36 | Integer | True | - | recurrence_rule.py:38 | - |
| RecurrenceRule.due_day_of_month | recurrence_rule.py:46 | Integer | True | - | recurrence_rule.py:48 | - |
| RecurrenceRule.month_of_year | recurrence_rule.py:55 | Integer | True | - | recurrence_rule.py:57 | - |

Computed properties: none. Recurrence logic lives in `app/services/recurrence_engine.py`.

### `app/models/ref.py`

356 lines. Classes: `AccountTypeCategory`, `AccountType`, `TransactionType`, `Status`,
`RecurrencePattern`, `FilingStatus`, `DeductionTiming`, `CalcMethod`, `TaxType`, `RaiseType`,
`GoalMode`, `IncomeUnit`, `UserRole`. Reference/lookup tables only. No numeric columns or computed
numeric properties of audit interest. The boolean columns on `Status` (e.g.,
`excludes_from_balance`, `is_settled`) are referenced by `Transaction.effective_amount` and the
balance calculator; consumers are catalogued in section 1.1 (P1-b).

### `app/models/salary_profile.py`

134 lines. Classes: `SalaryProfile`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SalaryProfile.annual_salary | salary_profile.py:72 | Numeric(12, 2) | False | - | salary_profile.py:28 | paycheck_gross (input) |
| SalaryProfile.pay_periods_per_year | salary_profile.py:77 | Integer | False | db.text("26") | salary_profile.py:29 | - |
| SalaryProfile.qualifying_children | salary_profile.py:82 | Integer | False | db.text("0") | salary_profile.py:30 | - |
| SalaryProfile.other_dependents | salary_profile.py:85 | Integer | False | db.text("0") | salary_profile.py:31 | - |
| SalaryProfile.additional_income | salary_profile.py:88 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:32 | taxable_income (input) |
| SalaryProfile.additional_deductions | salary_profile.py:92 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:33 | federal_tax (W-4 step-4 input) |
| SalaryProfile.extra_withholding | salary_profile.py:96 | Numeric(12, 2) | False | db.text("0") | salary_profile.py:34 | federal_tax (W-4 step-4 input) |
| SalaryProfile.sort_order | salary_profile.py:105 | Integer | False | db.text("0") | - | - |
| SalaryProfile.version_id | salary_profile.py:110 | Integer | False | "1" | salary_profile.py:36 | - |

Computed properties: none.

### `app/models/salary_raise.py`

133 lines. Classes: `SalaryRaise`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SalaryRaise.effective_month | salary_raise.py:108 | Integer | False | - | salary_raise.py:53 | - |
| SalaryRaise.effective_year | salary_raise.py:109 | Integer | True | - | salary_raise.py:61 | - |
| SalaryRaise.percentage | salary_raise.py:110 | Numeric(5, 4) | True | - | salary_raise.py:66 | growth (raise input) |
| SalaryRaise.flat_amount | salary_raise.py:111 | Numeric(12, 2) | True | - | salary_raise.py:67 | paycheck_gross (raise input) |
| SalaryRaise.version_id | salary_raise.py:119 | Integer | False | "1" | salary_raise.py:69 | - |

Computed properties: none.

### `app/models/savings_goal.py`

135 lines. Classes: `SavingsGoal`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| SavingsGoal.target_amount | savings_goal.py:75 | Numeric(12, 2) | True | - | savings_goal.py:41 | goal_progress (target input) |
| SavingsGoal.contribution_per_period | savings_goal.py:77 | Numeric(12, 2) | True | - | savings_goal.py:46 | goal_progress (rate input) |
| SavingsGoal.income_multiplier | savings_goal.py:115 | Numeric(8, 2) | True | - | savings_goal.py:50 | goal_progress (income-multiplier input) |
| SavingsGoal.version_id | savings_goal.py:121 | Integer | False | "1" | savings_goal.py:54 | - |

`income_multiplier` uses `Numeric(8, 2)` (max 999,999.99); the project standard is `Numeric(12, 2)`
for money but this column is a multiplier not a money value. Phase 6 confirms this is intentional.

Computed properties: none.

### `app/models/scenario.py`

63 lines. Classes: `Scenario`. No numeric columns or computed numeric properties of audit interest.
`is_baseline` is Boolean; `cloned_from_id` is FK.

### `app/models/tax_config.py`

240 lines. Classes: `TaxBracketSet`, `TaxBracket`, `StateTaxConfig`, `FicaConfig`.

`TaxBracketSet` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TaxBracketSet.tax_year | tax_config.py:51 | Integer | False | - | tax_config.py:27 | - |
| TaxBracketSet.standard_deduction | tax_config.py:52 | Numeric(12, 2) | False | - | tax_config.py:21 | federal_tax (deduction input) |
| TaxBracketSet.child_credit_amount | tax_config.py:59 | Numeric(12, 2) | False | "0" | tax_config.py:22 | federal_tax (credit input) |
| TaxBracketSet.other_dependent_credit_amount | tax_config.py:63 | Numeric(12, 2) | False | "0" | tax_config.py:23 | federal_tax (credit input) |

`TaxBracket` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TaxBracket.min_income | tax_config.py:113 | Numeric(12, 2) | False | - | tax_config.py:86 | taxable_income (bracket boundary) |
| TaxBracket.max_income | tax_config.py:114 | Numeric(12, 2) | True | - | tax_config.py:88 | taxable_income (bracket boundary) |
| TaxBracket.rate | tax_config.py:115 | Numeric(5, 4) | False | - | tax_config.py:91 | federal_tax (rate input) |
| TaxBracket.sort_order | tax_config.py:116 | Integer | False | db.text("0") | - | - |

`StateTaxConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| StateTaxConfig.tax_year | tax_config.py:174 | Integer | False | - | tax_config.py:149 | - |
| StateTaxConfig.flat_rate | tax_config.py:175 | Numeric(5, 4) | True | - | tax_config.py:137 | state_tax (rate input) |
| StateTaxConfig.standard_deduction | tax_config.py:176 | Numeric(12, 2) | True | - | tax_config.py:143 | state_tax (deduction input) |

`FicaConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| FicaConfig.tax_year | tax_config.py:216 | Integer | False | - | tax_config.py:204 | - |
| FicaConfig.ss_rate | tax_config.py:217 | Numeric(5, 4) | False | db.text("0.0620") | tax_config.py:194 | fica (SS rate input) |
| FicaConfig.ss_wage_base | tax_config.py:221 | Numeric(12, 2) | False | db.text("176100") | tax_config.py:195 | fica (SS cap input) |
| FicaConfig.medicare_rate | tax_config.py:225 | Numeric(5, 4) | False | db.text("0.0145") | tax_config.py:196 | fica (Medicare rate input) |
| FicaConfig.medicare_surtax_rate | tax_config.py:229 | Numeric(5, 4) | False | db.text("0.0090") | tax_config.py:198 | fica (surtax rate input) |
| FicaConfig.medicare_surtax_threshold | tax_config.py:233 | Numeric(12, 2) | False | db.text("200000") | tax_config.py:201 | fica (surtax threshold input) |

Computed properties: none on any class in this file.

### `app/models/transaction.py`

284 lines. Classes: `Transaction`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transaction.estimated_amount | transaction.py:158 | Numeric(12, 2) | False | - | transaction.py:113 | effective_amount (estimate source) |
| Transaction.actual_amount | transaction.py:159 | Numeric(12, 2) | True | - | transaction.py:117 | effective_amount (actual source, may be NULL) |
| Transaction.version_id | transaction.py:186 | Integer | False | "1" | transaction.py:121 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transaction.effective_amount | transaction.py:221-245 | @property | Decimal (inferred) | `is_deleted ? Decimal("0") : (status.excludes_from_balance ? Decimal("0") : (actual_amount if actual_amount is not None else estimated_amount))` | is_deleted, status.excludes_from_balance, actual_amount, estimated_amount | effective_amount |
| Transaction.is_income | transaction.py:247-250 | @property | bool (inferred) | `transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.INCOME)` | transaction_type_id (via ref_cache) | - |
| Transaction.is_expense | transaction.py:252-255 | @property | bool (inferred) | `transaction_type_id == ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)` | transaction_type_id (via ref_cache) | - |
| Transaction.days_until_due | transaction.py:257-269 | @property | int \| None (inferred) | `None if due_date is None or status.is_settled else (due_date - date.today()).days` | due_date, status.is_settled | - |
| Transaction.days_paid_before_due | transaction.py:271-283 | @property | int \| None (inferred) | `None if due_date is None or paid_at is None else (due_date - paid_at.date()).days` | due_date, paid_at | - |

`Transaction.effective_amount` is the load-bearing entry point for balance computation. The 4-tier
branching means a Phase 3 consistency audit must compare (a) every site that reads
`effective_amount`, (b) every site that reads `actual_amount` directly, (c) every site that reads
`estimated_amount` directly, and (d) every site that filters by status before summing. Direct reads
of `actual_amount` or `estimated_amount` bypass tier 1 (is_deleted) and tier 2 (status exclusion);
sites that do this on purpose must be intentional, and sites that do this by accident are findings.

`is_income` / `is_expense` use `ref_cache.txn_type_id(...)` which is the ID-based lookup pattern
required by E-15 (no string `name` comparisons). This pair satisfies the standard.

NO `is_settled`, `is_done`, `is_received`, `is_credit`, `is_cancelled`, or `is_projected` properties
exist on `Transaction`. Status checks must go through `status.is_settled` /
`status.excludes_from_balance` (boolean columns on the `Status` ref row). This means consumers
either read the whole `Status` object or call into a service helper; section 1.1 inventories which
path each site uses.

### `app/models/transaction_entry.py`

118 lines. Classes: `TransactionEntry`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransactionEntry.amount | transaction_entry.py:73 | Numeric(12, 2) | False | - | transaction_entry.py:51 | effective_amount (envelope entry source) |
| TransactionEntry.version_id | transaction_entry.py:95 | Integer | False | "1" | transaction_entry.py:56 | - |

Computed properties: none.

`TransactionEntry.amount` is the column aggregated by the only two money SQL aggregates
(`year_end_summary_service.py:519, 520-528`); see section 1.6.

### `app/models/transaction_template.py`

93 lines. Classes: `TransactionTemplate`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransactionTemplate.default_amount | transaction_template.py:59 | Numeric(12, 2) | False | - | transaction_template.py:30 | effective_amount (template seed) |
| TransactionTemplate.sort_order | transaction_template.py:64 | Integer | False | db.text("0") | - | - |
| TransactionTemplate.version_id | transaction_template.py:75 | Integer | False | "1" | transaction_template.py:32 | - |

Computed properties: none.

### `app/models/transfer.py`

186 lines. Classes: `Transfer`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transfer.amount | transfer.py:142 | Numeric(12, 2) | False | - | transfer.py:37 | transfer_amount |
| Transfer.version_id | transfer.py:152 | Integer | False | "1" | transfer.py:39 | - |

Computed properties:

| Class.property | file:line | Decorator | Returns | Formula (one-line) | Reads from | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| Transfer.effective_amount | transfer.py:174-182 | @property | Decimal (inferred) | `Decimal("0") if status.excludes_from_balance else amount` | status.excludes_from_balance, amount | transfer_amount |

`Transfer.effective_amount` simpler than `Transaction.effective_amount`: no actual/estimated split,
no soft-delete branch (transfers route soft-deletion through cascade to shadow transactions per
E-08, so queries excluded soft-deleted parents should not reach this property in balance contexts).
E-09 forbids the balance calculator from querying the `transfers` table at all; this property is
reserved for the transfer service and CRUD/display sites.

### `app/models/transfer_template.py`

95 lines. Classes: `TransferTemplate`.

Numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| TransferTemplate.default_amount | transfer_template.py:60 | Numeric(12, 2) | False | - | transfer_template.py:32 | transfer_amount (template seed) |
| TransferTemplate.sort_order | transfer_template.py:65 | Integer | False | db.text("0") | - | - |
| TransferTemplate.version_id | transfer_template.py:73 | Integer | False | "1" | transfer_template.py:36 | - |

Computed properties: none.

### `app/models/user.py`

343 lines. Classes: `User`, `UserSettings`, `MfaConfig`.

`User` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| User.failed_login_count | user.py:110 | Integer | False | "0" | user.py:49 | - |

`UserSettings` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| UserSettings.default_inflation_rate | user.py:234 | Numeric(5, 4) | True | - | user.py:187 | growth (default inflation input) |
| UserSettings.grid_default_periods | user.py:235 | Integer | True | - | user.py:191 | - |
| UserSettings.low_balance_threshold | user.py:236 | Integer | True | - | user.py:192 | account_balance (alert threshold) |
| UserSettings.safe_withdrawal_rate | user.py:237 | Numeric(5, 4) | True | db.text("0.0400") | user.py:207 | growth (SWR input) |
| UserSettings.estimated_retirement_tax_rate | user.py:242 | Numeric(5, 4) | True | - | user.py:216 | federal_tax (retirement projection input) |
| UserSettings.large_transaction_threshold | user.py:243 | Integer | False | "500" | user.py:194 | effective_amount (alert threshold) |
| UserSettings.trend_alert_threshold | user.py:246 | Numeric(5, 4) | False | "0.1000" | user.py:198 | growth (trend alert threshold) |
| UserSettings.anchor_staleness_days | user.py:249 | Integer | False | "14" | user.py:223 | - |

`UserSettings.trend_alert_threshold` is PA-01's open finding (Marshmallow `Range(min=1, max=100)`
percentage vs DB CHECK 0..1 decimal). `UserSettings.safe_withdrawal_rate` is the column behind
PA-04's float-cast violation in `compute_slider_defaults`.
`UserSettings.estimated_retirement_tax_rate` is one of the rate fields inspected by PA-02. These
three columns are inputs to financial calculations; the prior-audit findings live in section 0.6 of
`00_priors.md`.

`MfaConfig` numeric columns:

| Class.column | file:line | SQLAlchemy type | Nullable | Server default | CHECK constraint | Concept token |
| --- | --- | --- | --- | --- | --- | --- |
| MfaConfig.last_totp_timestep | user.py:342 | BigInteger | True | - | - | - |

Not financial.

Computed properties on `User`, `UserSettings`, `MfaConfig`: none.

### Section 1.5 cross-cutting summary

- All money columns are `Numeric(12, 2)` except: calibration money
  (`Numeric(10, 2)`, four columns in `calibration_override.py`),
  paycheck deduction amount (`Numeric(12, 4)`, sub-cent precision for
  intermediate calculation), and savings-goal income multiplier
  (`Numeric(8, 2)`, multiplier not money). No `Float` money columns.
- All rate columns use `Numeric(7, 5)` (interest, APY, benefit
  multiplier) or `Numeric(5, 4)` (percentages, inflation, employer
  match caps, SWR, trend threshold). Calibration effective rates use
  `Numeric(12, 10)` for downstream-computation precision.
- Every money column has an in-model `CheckConstraint` EXCEPT the two
  `Account` balance columns (`Account.current_anchor_balance` line 51
  and `AccountAnchorHistory.anchor_balance` line 152), which carry no
  in-model CheckConstraint. Phase 4 confirms whether the migration
  attaches a CHECK or whether anchor balances are intentionally
  unbounded (mortgages can be negative balances, etc.).
- Six `@property` accessors total in `app/models/`. Five live on
  `Transaction`, one on `Transfer`, one on `Category` (display_name,
  not financial), one on `PayPeriod` (label, not financial). The
  financial properties are `Transaction.effective_amount`,
  `Transaction.is_income`, `Transaction.is_expense`, and
  `Transfer.effective_amount`; section 1.1 catalogues their consumers.
- No `@hybrid_property` or `@cached_property`. Stored-vs-computed
  drift detection (Phase 4) does not have a hybrid-property surface to
  worry about; the only stored numeric the audit must reconcile is
  `LoanParams.current_principal` against the amortization-engine
  replay (only for fixed-rate per A-04; AUTHORITATIVE for ARM), plus
  `Account.current_anchor_balance` against the balance calculator.
- A-01 (09_open_questions.md:29-35) establishes the canonical rounding
  rule `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` for
  any monetary calculation that produces a stored or displayed value.
  No model-layer column performs quantization (storage precision is
  fixed by the column type); the rounding rule applies to the service-
  layer code paths that compute values from these columns. Sections
  1.1 (services) through 1.4 (JS) inventory those quantization sites
  and Phase 3 verifies the rule is applied uniformly. The two sub-
  cent precision exceptions (`PaycheckDeduction.amount` Numeric(12, 4),
  `CalibrationOverride.effective_*_rate` Numeric(12, 10)) carry their
  precision through the calculator and quantize at the boundary; Phase
  3 confirms that boundary exists for every consumer.

## 1.6 Database queries that aggregate money

The grep `func\.sum|func\.avg|func\.min|func\.max|func\.count` over `app/` returns FIVE matches
(P1-a, 2026-05-15). The raw-SQL grep (`SUM(`, `AVG(`, `MIN(`, `MAX(`, `COUNT(` in .py files) returns
only the same five hits via `db.func.*`; no raw SQL strings with aggregate keywords exist outside
the SQLAlchemy `func` accessor. `db.text(...)` calls elsewhere are all column server defaults or
audit-infrastructure DDL, not aggregate execution paths. Python-builtin `sum()`, `min()`, `max()`
calls operate over already-fetched in-memory collections; they belong in section 1.1 (services) and
are out of scope for 1.6.

| File:line | SQL aggregate | Aggregated column | Aggregated column type | Money? | Joins | Filters | Layer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `app/services/year_end_summary_service.py:518` | `db.func.count(TransactionEntry.id)` | TransactionEntry.id | Integer (PK) | No (row count) | TransactionEntry -> Transaction -> TransactionTemplate, Transaction -> PayPeriod, Transaction -> Account, outer Transaction -> Category (lines 530-540) | user_id=user_id, scenario_id=scenario_id, pay_period_id IN period_ids, is_deleted=False, transaction_type_id=expense_type_id, status_id IN settled_status_ids, TransactionTemplate.is_envelope=True (lines 541-549); GROUP BY group_name, item_name, transaction_id, due_date, pp_start_date (lines 550-557) | service |
| `app/services/year_end_summary_service.py:519` | `db.func.sum(TransactionEntry.amount)` | TransactionEntry.amount | Numeric(12, 2) | YES | same as above | same as above | service |
| `app/services/year_end_summary_service.py:520-528` | `db.func.sum(case((TransactionEntry.is_credit.is_(True), TransactionEntry.amount), else_=Decimal("0")))` | TransactionEntry.amount conditional on is_credit=True | Numeric(12, 2) | YES (conditional sum -- credit entries only) | same as above | same as above | service |
| `app/services/pay_period_service.py:49` | `db.func.max(PayPeriod.period_index)` | PayPeriod.period_index | Integer | No (assigning next index for new periods) | none | filter_by(user_id=user_id) (line 50) | service |
| `app/services/transfer_service.py:669` | `Transaction.query.filter_by(transfer_id=transfer_id).count()` | row count | n/a | No (orphan-detection guard after CASCADE delete of parent transfer; counts shadow transactions that should already be gone) | none | filter_by(transfer_id=transfer_id) (line 668) | service |

### Money aggregates: deeper notes

Both money aggregates live inside one function: `_compute_envelope_breakdowns_aware()` in
`app/services/year_end_summary_service.py`, called by the year-end summary service to break down
envelope-tracked spending by category group/item for the configured calendar year. The function:

- joins TransactionEntry to Transaction to TransactionTemplate to
  PayPeriod to Account, outer-joining Category (lines 530-540);
- restricts to user-owned, baseline-scenario, in-window, non-deleted,
  expense-typed, settled-status, envelope-template rows (lines 541-549);
- groups by category and individual transaction, retaining due_date and
  pay-period start_date for client-side year attribution
  (`row.due_date.year if row.due_date is not None else
  row.pp_start_date.year`, lines 562-565).

Neither aggregate filters on `entry_credit_workflow` flags directly; the credit-vs-debit distinction
is encoded entirely by the `case` in the second `func.sum`. Phase 3 must verify (a) that the
credit-entry exclusion is consistent with the entry-aware checking-impact formula in the balance
calculator (`app/services/balance_calculator.py:298-331`, per the docstring) and (b) that the same
envelope-spending concept computed elsewhere (spending trends, budget variance, savings dashboard)
uses the same filter set or documents the divergence.

### Aggregates over money outside services or in raw SQL

NONE. All five SQL aggregates are inside service modules. There are no SQL aggregates in routes,
templates, JS, raw SQL strings, or non-service code paths. This satisfies the audit-plan rule that
aggregates over money outside services are suspect.

### Filter-set checklist for the two money aggregates

For Phase 3 cross-comparison:

| Dimension | year_end_summary_service.py:519/520 |
| --- | --- |
| User scope | `Account.user_id == user_id` (line 542) |
| Scenario scope | `Transaction.scenario_id == scenario_id` (line 543) |
| Period scope | `Transaction.pay_period_id.in_(period_ids)` (line 544) |
| Soft delete | `Transaction.is_deleted.is_(False)` (line 545) |
| Type filter | `Transaction.transaction_type_id == expense_type_id` (line 546) |
| Status filter | `Transaction.status_id.in_(settled_status_ids)` (line 547) |
| Envelope filter | `TransactionTemplate.is_envelope.is_(True)` (line 548) |
| Effective-amount logic | Reads `TransactionEntry.amount` directly, not the entry-aware checking-impact formula. Justified because envelope entries are the source for envelope spending, but Phase 3 must confirm |
| Anchor handling | Not anchor-aware. Sums all entries in scope regardless of anchor period |
| `is_deleted` on TransactionEntry | NOT filtered. TransactionEntry has no soft-delete column per the model (`transaction_entry.py`); CASCADE from Transaction handles deletion |
| Quantization | None at the SQL layer. Caller is responsible for any rounding |
| Source-of-truth column | `TransactionEntry.amount` (line 73, Numeric(12, 2)) |

## Open questions raised in 1.5/1.6

None during this session. All numeric columns and properties mapped cleanly to existing
controlled-vocabulary tokens with the noted "input" qualifications. The classification ambiguities
flagged in this section (calibration money precision, savings goal multiplier precision, anchor-
balance lack of CHECK constraint, paycheck deduction Numeric(12, 4)) are recorded in the per-model
rows for Phase 6 (DRY/SOLID) to evaluate rather than as questions for the developer; they do not
block any calculation reading in subsequent Phase 1 sessions.

## 1.1 Service layer

40 files under `app/services/`. 18,022 LOC total. Three Explore subagents ran in parallel, very
thorough, each reading every file in scope IN FULL before producing the structured inventory. Files
were partitioned by domain (calculation engines / aggregation / transactional+workflow) to keep each
subagent's context bounded.

Out of scope per audit plan section 0.6 (auth and non-financial services excluded):
`auth_service.py` (805 lines), `mfa_service.py` (413 lines), `exceptions.py` (44 lines),
`__init__.py` (0 lines). Recorded for exhaustiveness but no functions inventoried.

In scope: 36 files, 16,760 LOC, 67+ public functions/methods.

For every public function in scope:

- one-sentence description of what the body actually does (not the
  docstring paraphrase);
- financial concept tokens produced (using the vocabulary at the top of
  this file);
- status filter, transaction-type filter, period scope, with file:line
  citations;
- amount column read (`effective_amount`, `actual_amount`,
  `estimated_amount`, `amount`) with file:line and a bypass flag;
- quantization site (precision, rounding mode, line) or "MISSING" per
  A-01;
- enumerated calls to other service functions or model methods.

The cross-cutting summary tables at the end of section 1.1 collect every `effective_amount` bypass,
every missing quantization, every Flask boundary import, every transfer-model read, and every
shadow-mutation site, so Phase 3 has a single grep target.

A-05 cross-reference: A-05 lists eight `calculate_monthly_payment` call sites; the grep finds
fourteen (`amortization_engine.py:436, 440, 491, 512, 693, 697, 952, 957`;
`balance_calculator.py:225, 231`; `loan_payment_service.py:251, 256`;
`app/routes/loan.py:1102, 1225, 1231`). The developer's list captures only the primary branch of
each call pair; Phase 3 must verify that adjacent fallback branches (lines 436, 693, 957 in the
engine; line 231 in the balance calculator; line 256 in loan_payment_service; lines 1102, 1231 in
the loan route) receive the same triple `(current_principal, current_rate, remaining_months)` for
the same loan-on-date as their primary siblings, per the A-05 invariant.

### Group A: Calculation engines

11 files inventoried in scope (`tax_config_service.py` is included for completeness despite
producing no money directly). Total 3,608 LOC. Zero Flask imports. Zero shadow mutations.
Quantization to `Decimal('0.01')` ROUND_HALF_UP is uniform per A-01 except where noted.

#### `app/services/amortization_engine.py` (991 lines)

Imports flagged: none.

Public functions (sorted by line):

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 128 | `calculate_remaining_months(origination_date, term_months, as_of=None)` | int | Returns term_months minus elapsed months from origination to as_of. | loan_remaining_months | n/a | n/a | n/a | n/a | no | n/a | none |
| 178 | `calculate_monthly_payment(principal, annual_rate, remaining_months)` | Decimal | Applies the standard amortization formula `P * (r(1+r)^n) / ((1+r)^n - 1)` and quantizes. | monthly_payment | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 192, 197 | none |
| 200 | `_advance_month(year, month, day)` | date | Increments the month and clamps the day to the month's last valid day. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 210 | `_build_payment_lookups(payments, origination_date)` | tuple[dict, dict] | Builds (year, month) -> (amount_sum, is_confirmed_any) lookup, dropping pre-origination rows. | - | n/a | n/a | n/a | PaymentRecord.amount fields | no | n/a | none |
| 255 | `_build_rate_change_list(rate_changes, origination_date)` | list[tuple] | Sorts, deduplicates, drops pre-origination rate changes. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 298 | `_find_applicable_rate(payment_date, rate_schedule, base_rate)` | Decimal | Returns the most recent rate effective on or before payment_date, else base_rate. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 326 | `generate_schedule(current_principal, annual_rate, remaining_months, extra_monthly=ZERO, origination_date=None, payment_day=1, original_principal=None, term_months=None, payments=None, rate_changes=None, anchor_balance=None, anchor_date=None)` | list[AmortizationRow] | Builds a month-by-month schedule with optional payment-record replay, ARM rate changes, and balance anchoring; each row quantized at allocation time. | principal_paid_per_period, interest_paid_per_period, monthly_payment, loan_principal_real, months_saved | n/a | n/a | forward range from origination/anchor through term (lines 474-617) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 517, 551, 586, 601-604 | calculate_monthly_payment at 436, 440, 491, 512 |
| 622 | `_derive_summary_metrics(schedule, fallback_date)` | tuple[Decimal, date] | Extracts total_interest and payoff_date from a finished schedule. | total_interest, payoff_date | n/a | n/a | n/a | row.interest, row.payment_date | no | n/a | none |
| 649 | `calculate_summary(current_principal, annual_rate, remaining_months, origination_date, payment_day, term_months, extra_monthly=ZERO, original_principal=None, payments=None, rate_changes=None, anchor_balance=None, anchor_date=None)` | AmortizationSummary | Generates standard and accelerated schedules, then derives summary deltas (payoff, interest_saved, months_saved). | total_interest, interest_saved, months_saved, payoff_date, monthly_payment | n/a | n/a | forward range (lines 702-731) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 749 | calculate_monthly_payment at 693, 697; generate_schedule at 702, 720 |
| 753 | `calculate_payoff_by_date(current_principal, annual_rate, remaining_months, target_date, origination_date, payment_day, original_principal=None, term_months=None, rate_changes=None)` | Decimal \| None | Binary-searches the extra_monthly value required to hit a target payoff_date; returns None if unreachable. | monthly_payment | n/a | n/a | forward range (lines 824-832) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 823 | generate_schedule at 779, 824 |
| 864 | `get_loan_projection(params, schedule_start=None, payments=None, rate_changes=None)` | LoanProjection | Generates the full schedule once, derives summary, and returns `current_balance` from the stored `current_principal` for ARM or the last confirmed schedule row for fixed-rate (A-04 dual policy at lines 977-985). | monthly_payment, total_interest, payoff_date, loan_principal_real, loan_principal_stored, loan_remaining_months | n/a | n/a | full loan term (lines 932-942) | LoanParams.current_principal at 977, 980 | no | n/a (delegates) | calculate_remaining_months at 908; generate_schedule at 932; _derive_summary_metrics at 945; calculate_monthly_payment at 952, 957 |

A-04 anchor: lines 977-985 implement the dual policy. `is_arm=True` ->
`cur_balance = current_principal` (line 977-978). `is_arm=False` -> walks the schedule backward from
end, taking `row.remaining_balance` from the last `is_confirmed=True` row (lines 980-984). The
`LoanProjection` docstring at lines 848-861 documents this asymmetry.

#### `app/services/growth_engine.py` (419 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 91 | `calculate_employer_contribution(employer_params, employee_contribution)` | Decimal | Computes employer match: flat_percentage of gross, or `match_percentage * employee_contribution` capped by `match_cap_percentage * gross`. | employer_contribution | n/a | n/a | per period | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 114, 120, 124 | none |
| 130 | `_build_contribution_lookup(contributions)` | dict \| None | Sums same-date `ContributionRecord` entries into a (date -> (amount_sum, is_confirmed_any)) dict. | - | n/a | n/a | n/a | ContributionRecord.amount | no | n/a | none |
| 164 | `project_balance(current_balance, assumed_annual_return, periods, periodic_contribution=ZERO, employer_params=None, annual_contribution_limit=None, ytd_contributions_start=ZERO, contributions=None)` | list[ProjectedBalance] | Walks forward period-by-period applying growth THEN contribution (order matters; see docstring); resets YTD at year boundary; enforces annual_contribution_limit. | growth, employer_contribution, contribution_limit_remaining, ytd_contributions | n/a | n/a | list of periods (line 222) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 243-244 | calculate_employer_contribution at 265 |
| 297 | `reverse_project_balance(anchor_balance, assumed_annual_return, periods, periodic_contribution=ZERO, employer_params=None)` | list[ProjectedBalance] | Walks backward from a last-period anchor, inverting growth and contributions to derive prior-period balances. | growth, employer_contribution | n/a | n/a | list of periods reversed (line 348) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 363, 368 | calculate_employer_contribution at 339 |
| 393 | `generate_projection_periods(start_date, end_date, cadence_days=14)` | list[SyntheticPeriod] | Emits biweekly `SyntheticPeriod` namedtuples between two dates. | - | n/a | n/a | n/a | n/a | no | n/a | none |

#### `app/services/interest_projection.py` (114 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 49 | `calculate_interest(balance, apy, compounding_frequency, period_start, period_end)` | Decimal | Computes interest accrued during one pay period under daily/monthly/quarterly compounding using actual/365 convention. Returns ZERO for non-positive balance/APY or inverted period. | apy_interest | n/a | n/a | period span (lines 86-110) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 114 | none |

Quarterly compounding (lines 99-110) uses the actual quarter-length from period start rather than a
hardcoded 91 days per the L-05 fix comment.

#### `app/services/tax_calculator.py` (321 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | `calculate_federal_withholding(gross_pay, pay_periods, bracket_set, *, additional_income=ZERO, pre_tax_deductions=ZERO, additional_deductions=ZERO, qualifying_children=0, other_dependents=0, extra_withholding=ZERO)` | Decimal | IRS Pub 15-T Percentage Method: annualize, apply standard deduction and W-4 step-4 deductions, run marginal brackets, apply W-4 child/dependent credits, divide by pay_periods, add extra_withholding. | federal_tax, taxable_income | n/a | n/a | current period (annualize/de-annualize at 102-160) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 162-164 | _apply_marginal_brackets at 127 |
| 173 | `_apply_marginal_brackets(taxable_income, brackets)` | Decimal | Walks sorted brackets and sums `(top - bottom) * rate` for each tier within the income. | federal_tax (intermediate) | n/a | n/a | annual | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 209 | none |
| 215 | `calculate_federal_tax(annual_gross, bracket_set)` | Decimal | Legacy single-call wrapper: standard deduction subtraction then `_apply_marginal_brackets`; returns ANNUAL not per-period. | federal_tax | n/a | n/a | annual | n/a | no | inherits from helper | _apply_marginal_brackets at 234 |
| 240 | `calculate_state_tax(annual_gross, state_config)` | Decimal | Subtracts standard_deduction, applies flat_rate; returns ZERO if state's tax_type_id is 'none' (line 257 uses ref_cache, not name string). | state_tax | n/a | n/a | annual | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 266 | ref_cache.tax_type_id at 257 |
| 274 | `calculate_fica(annual_gross, fica_config, cumulative_wages=ZERO)` | dict[str, Decimal] | Applies Social Security rate up to wage base cap (cumulative_wages enforces cap across periods), Medicare rate over full gross, and Medicare surtax above threshold. | fica | n/a | n/a | current period (cumulative wages tracked) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 304, 306, 309, 316-318 | none |

#### `app/services/paycheck_calculator.py` (504 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 92 | `calculate_paycheck(profile, period, all_periods, tax_configs, *, calibration=None)` | PaycheckBreakdown | Full per-period breakdown: raises -> gross -> pre-tax deductions -> taxable -> federal/state/FICA (bracket-based or calibrated) -> post-tax deductions -> net. | paycheck_gross, paycheck_net, taxable_income, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, employer_contribution | n/a | n/a | current period + all_periods (for 3rd-paycheck and cumulative wages) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 133-135, 202-204, 231 | _apply_raises at 125; _get_raise_event at 126; _calculate_deductions at 149, 217; calibration_service.apply_calibration at 167; tax_calculator.calculate_federal_withholding at 185; tax_calculator.calculate_state_tax at 199; tax_calculator.calculate_fica at 210 |
| 250 | `project_salary(profile, periods, tax_configs, *, calibration=None)` | list[PaycheckBreakdown] | Calls `calculate_paycheck` once per period in `periods`. | paycheck_gross, paycheck_net, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, employer_contribution | n/a | n/a | list of periods (line 267) | n/a | no | n/a (delegates) | calculate_paycheck at 263 |
| 274 | `_apply_raises(profile, period)` | Decimal | Applies all qualifying raises (recurring + one-time) in `salary_raise.sort_order`, returning the post-raise annual salary for `period`. | paycheck_gross | n/a | n/a | current period (291-324) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 326 | _apply_single_raise at 299 |
| 329 | `_apply_single_raise(salary, raise_obj)` | Decimal | Applies one raise: `salary * (1 + percentage)` or `salary + flat_amount`. | paycheck_gross | n/a | n/a | n/a | n/a | no | n/a (caller quantizes) | none |
| 339 | `_get_raise_event(profile, period)` | str | Returns a comma-separated label describing raise events active in `period`. | - | n/a | n/a | current period (344-365) | n/a | no | n/a | none |
| 369 | `_is_third_paycheck(period, all_periods)` | bool | True if `period` is the third pay period whose start_date falls in its calendar month. | - | n/a | n/a | current month (375-385) | n/a | no | n/a | none |
| 389 | `_is_first_paycheck_of_month(period, all_periods)` | bool | True if `period` is the earliest in its calendar month. | - | n/a | n/a | current month (393-400) | n/a | no | n/a | none |
| 403 | `_calculate_deductions(profile, period, all_periods, gross_biweekly, timing_id, calc_method_pct_id, is_third_paycheck)` | list[DeductionLine] | Filters profile.deductions by timing_id; skips 24-per-year on 3rd-paycheck and 12-per-year unless first-of-month; applies flat/percentage and per-year inflation. | pre_tax_deduction, post_tax_deduction | n/a | n/a | current period (433-453) | PaycheckDeduction.amount, percentage | no | Decimal('0.01'), ROUND_HALF_UP, lines 440-441, 451-452 | _inflation_years at 449 |
| 463 | `_inflation_years(period, profile, effective_month)` | int | Counts full inflation years from profile creation to `period`'s month-anniversary. | - | n/a | n/a | profile -> period (469-475) | n/a | no | n/a | none |
| 480 | `_get_cumulative_wages(profile, period, all_periods)` | Decimal | Sums year-to-date gross wages (post-raises) for FICA SS wage-base cap tracking. | fica | n/a | n/a | YTD (488-504) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 499-501 | _apply_raises at 498 |

Calibration override branches at lines 160-173: when `calibration.is_active` is True,
FICA/federal/state are computed via `calibration_service.apply_calibration` instead of the bracket
path; both paths exit through the same quantization at line 231.

#### `app/services/loan_payment_service.py` (353 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 78 | `load_loan_context(account_id, scenario_id, loan_params)` | LoanContext | Unified loader: pulls EscrowComponents, derives monthly_escrow, gets payment history, computes contractual_pi, builds rate_changes list for ARM, runs `prepare_payments_for_engine`. Returns a LoanContext dataclass ready for the engine. | monthly_payment, escrow_per_period, loan_principal_stored | status.excludes_from_balance=False at 210 (via get_payment_history) | income at 190 | scenario period at 116 | effective_amount at 218 (in get_payment_history) | no | n/a | EscrowComponent query at 104; escrow_calculator.calculate_monthly_escrow at 110; get_payment_history at 116; prepare_payments_for_engine at 122; RateHistory query at 131 |
| 156 | `get_payment_history(account_id, scenario_id)` | list[PaymentRecord] | Queries shadow income on the loan account (transfer_id IS NOT NULL AND income type), excludes status.excludes_from_balance, returns PaymentRecord dataclasses using `effective_amount` and `status.is_settled`. | monthly_payment | status.excludes_from_balance=False at 210 | income at 190 | scenario period at 212 | effective_amount at 218 | no | n/a | none |
| 233 | `compute_contractual_pi(params)` | Decimal | ARM (line 250): re-amortizes from `current_principal + interest_rate` at the current remaining_months. Fixed-rate: standard `original_principal + interest_rate + term_months`. | monthly_payment | n/a | n/a | n/a | LoanParams.current_principal, LoanParams.interest_rate | no | inherits from `calculate_monthly_payment` | calculate_remaining_months at 247; calculate_monthly_payment at 251, 256 |
| 263 | `prepare_payments_for_engine(payments, payment_day, monthly_escrow, contractual_pi)` | list[PaymentRecord] | A-06 preprocessing: subtracts `monthly_escrow` from any payment >= contractual_pi + threshold (lines 305-319), then redistributes biweekly month-collisions so one payment per month aligns with `payment_day` (lines 321-351). | monthly_payment, escrow_per_period | n/a | n/a | sorted by payment_date at 300 | PaymentRecord.amount | no | n/a (inputs already quantized) | none |

#### `app/services/retirement_gap_calculator.py` (136 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 37 | `calculate_gap(net_biweekly_pay, monthly_pension_income=ZERO, retirement_account_projections=None, safe_withdrawal_rate=Decimal("0.04"), planned_retirement_date=None, estimated_tax_rate=None)` | RetirementGapAnalysis | Converts net biweekly -> monthly, applies pension (optionally tax-adjusted), computes gap, derives required_savings via 4% SWR rule, compares to projected balances (traditional vs Roth split via `estimated_tax_rate`). | pension_benefit_monthly, federal_tax (retirement tax input), paycheck_net | n/a | n/a | future (planned_retirement_date) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 70, 80, 95, 121 | none |

#### `app/services/pension_calculator.py` (153 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 31 | `calculate_benefit(benefit_multiplier, consecutive_high_years, hire_date, planned_retirement_date, salary_by_year)` | PensionBenefit | Formula: `multiplier * years_of_service * high_salary_average`. Returns `PensionBenefit(annual_benefit, monthly_benefit, ...)`. | pension_benefit_annual, pension_benefit_monthly | n/a | n/a | hire_date -> planned_retirement_date (47) | salary_by_year (dict input) | no | Decimal('0.01'), ROUND_HALF_UP, lines 63, 65-66 | _calculate_years_of_service at 47; _compute_high_salary_average at 57 |
| 78 | `project_salaries_by_year(annual_salary, raises, start_year, end_year)` | list[tuple] | Builds year-by-year salary projection by reusing `paycheck_calculator._apply_raises` with synthetic period objects. | paycheck_gross (raise-applied) | n/a | n/a | year range (107-110) | n/a | no | n/a | paycheck_calculator._apply_raises at 109 |
| 114 | `_calculate_years_of_service(hire_date, retirement_date)` | Decimal | `(retirement_date - hire_date).days / 365.25`, quantized. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 121 | none |
| 126 | `_compute_high_salary_average(salary_by_year, consecutive_high_years)` | tuple[Decimal, list] | Sliding-window: returns the highest `consecutive_high_years`-year average and the (year, salary) tuples in that window. | - | n/a | n/a | n/a | salary values | no | Decimal('0.01'), ROUND_HALF_UP, line 148 | none |

#### `app/services/investment_projection.py` (288 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | `adapt_deductions(raw_deductions)` | list[AdaptedDeduction] | Converts ORM `PaycheckDeduction` rows to lightweight namedtuples for DB-decoupling. | - | n/a | n/a | n/a | n/a | no | n/a | none |
| 73 | `_compute_deduction_per_period(deduction, pct_id)` | tuple[Decimal, Decimal] | Returns (contribution, gross_biweekly) for flat or percentage-of-salary deductions. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01') quantize at 93, 96 (caller-supplied precision) | none |
| 100 | `calculate_investment_inputs(account_id, investment_params, deductions, all_contributions, all_periods, current_period, salary_gross_biweekly=None)` | InvestmentInputs | Computes (periodic_contribution, employer_params, ytd_contributions_start, annual_contribution_limit) from deductions plus shadow income on the investment account. | growth (input), employer_contribution (input), contribution_limit_remaining, ytd_contributions | status.excludes_from_balance at 150 | income (shadow) at 147 | current period + YTD (178-187) | **estimated_amount at 153 and 187** | YES (lines 153, 187) | Decimal('0.01') quantize via .quantize() at 159-160, 186-187 | none |
| 201 | `build_contribution_timeline(deductions, contribution_transactions, periods)` | list[ContributionRecord] | Builds per-period ContributionRecord stream: same deduction amount each period (past = confirmed); per-transaction shadow contributions (status-confirmed). | growth (input), employer_contribution (input) | status.excludes_from_balance=False at 268; status.is_settled at 284 | income (shadow) at 265 | period list (253, 270) | effective_amount at 274 | no | inherits from `_compute_deduction_per_period` | _compute_deduction_per_period at 248 |

The bypass at `calculate_investment_inputs` lines 153 and 187 reads `t.estimated_amount` directly,
but the caller pre-filters by `status.excludes_from_balance` (line 150), so cancelled/credit
contributions never reach the sum. Phase 3 must verify that all callers honor this contract.

#### `app/services/escrow_calculator.py` (115 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 14 | `calculate_monthly_escrow(components, as_of_date=None)` | Decimal | Sums active components' `annual_amount / 12` with optional compound inflation; month-aware elapsed (M-05) prevents full-year inflation on late-created components. | escrow_per_period | n/a | n/a | as_of_date (44-50) | EscrowComponent.annual_amount, inflation_rate | no | Decimal('0.01'), ROUND_HALF_UP, line 57 | none |
| 60 | `calculate_total_payment(monthly_pi, components, as_of_date=None)` | Decimal | `monthly_pi + calculate_monthly_escrow(...)`. | monthly_payment, escrow_per_period | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 76 | calculate_monthly_escrow at 75 |
| 79 | `project_annual_escrow(components, years_forward, base_year)` | list[tuple] | Per-year totals with per-component inflation applied independently. | escrow_per_period | n/a | n/a | forward range (97-113) | n/a | no | Decimal('0.01'), ROUND_HALF_UP, lines 111, 113 | none |

#### `app/services/tax_config_service.py` (69 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 16 | `load_tax_configs(user_id, profile, tax_year=None)` | dict | Loads TaxBracketSet/StateTaxConfig/FicaConfig for user, filing_status, state, year. | federal_tax (input), state_tax (input), fica (input) | n/a | n/a | tax_year (line 37) | n/a | no | n/a | none |

Phase 3 finding (no F-id yet): docstring at `tax_config_service.py:7` says it was "extracted from
the salary route to eliminate a route-to-route import and a duplicate copy in
`chart_data_service.py`". That file does not exist anywhere in `app/` (verified by grep); the audit
plan's required-grep list (Appendix B) includes `chart_data_service` and the only references in the
codebase are this stale docstring and a comment in `app/static/js/chart_theme.js:222`. Phase 3
should determine whether `chart_data_service` was renamed or never implemented.

#### `app/services/calibration_service.py` (145 lines)

Imports flagged: none.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 34 | `derive_effective_rates(actual_gross_pay, actual_federal_tax, actual_state_tax, actual_social_security, actual_medicare, taxable_income)` | DerivedRates | Computes effective rates from pay stub: federal/state divide by taxable_income, FICA divides by gross. Stores at `Decimal('0.0000000001')` precision (`Numeric(12, 10)` column). | federal_tax (calibration input), state_tax (calibration input), fica (calibration input) | n/a | n/a | n/a | n/a | no | Decimal('0.0000000001'), ROUND_HALF_UP, lines 83-96 | none |
| 106 | `apply_calibration(gross_biweekly, taxable_biweekly, calibration)` | dict[str, Decimal] | Applies derived effective rates: federal/state * taxable_biweekly; SS/medicare * gross_biweekly. | federal_tax (calibrated), state_tax (calibrated), fica (calibrated) | n/a | n/a | current period | CalibrationOverride.effective_*_rate | no | Decimal('0.01'), ROUND_HALF_UP, lines 133-144 | none |

### Group B: Aggregation and dashboard services

11 files inventoried. Total 7,726 LOC. The `_sum_remaining` vs `_sum_all` split in
`balance_calculator.py` and the `_compute_mortgage_interest` in `year_end_summary_service.py` (A-06)
and the ARM `proj.current_balance` read in `savings_dashboard_service.py:373` (A-04) are the
headline cross-page concept-comparison targets for Phase 3.

#### `app/services/balance_calculator.py` (451 lines)

Imports flagged: none. Reads `budget.transfers`? **NO** (Transfer Invariant 5 satisfied; verified by
grep -- no `Transfer.query`, no `from app.models.transfer`, no `db.session.query(Transfer)`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 35 | `calculate_balances(anchor_balance, anchor_period_id, periods, transactions)` | (OrderedDict[period_id, Decimal], bool) | Walks `periods` from anchor forward; first period uses `_sum_remaining` (anchor semantics), later periods use `_sum_all`. Returns running balances and a `stale_anchor_warning` flag. | checking_balance, projected_end_balance, period_subtotal | via `_sum_remaining`/`_sum_all` (lines 411, 439) | via `_sum_remaining`/`_sum_all` (lines 414-449) | anchor + forward periods | n/a (delegates) | no | Decimal('0.00') initialization (line 47) | _sum_remaining at 76; _sum_all at 84 |
| 112 | `calculate_balances_with_interest(anchor_balance, anchor_period_id, periods, transactions, interest_params=None)` | (OrderedDict, dict[period_id, Decimal]) | Wraps `calculate_balances` and layers `interest_projection.calculate_interest` per period. | checking_balance, apy_interest | n/a (delegates) | n/a (delegates) | anchor + forward | n/a (delegates) | no | n/a; interest at column precision | calculate_balances at 135; interest_projection.calculate_interest at 161 |
| 176 | `calculate_balances_with_amortization(anchor_balance, anchor_period_id, periods, transactions, account_id=None, loan_params=None)` | (OrderedDict, dict[period_id, Decimal]) | For debt accounts: detects shadow income on the loan as principal payments, splits into principal/interest using current rate, returns running principal and per-period interest portions. | loan_principal_real, principal_paid_per_period, interest_paid_per_period, monthly_payment | status.excludes_from_balance at 264 | shadow income at 268 (`transfer_id is not None` AND `is_income`) | anchor + forward | effective_amount at 270; LoanParams.current_principal, interest_rate at 221-235 | no | Decimal('0.01'), ROUND_HALF_UP, line 275; principal snap at 278-282 | calculate_balances at 207; amortization_engine.calculate_monthly_payment at 225, 231; amortization_engine.calculate_remaining_months at 203 |
| 292 | `_entry_aware_amount(txn)` | Decimal | For a PROJECTED expense with eagerly-loaded `entries`: returns `max(estimated, sum_cleared_debit + sum_credit)` so cleared entries reduce projection without double-counting (see docstring lines 298-331). For all other shapes returns `txn.effective_amount`. | effective_amount | status_id == projected_id at 365 | n/a (per-transaction) | single transaction | entry.amount directly at 374-378; estimated_amount at 384-385 | YES (374-378, 384-385) | n/a (max formula preserves precision) | ref_cache.status_id at 364 |
| 389 | `_sum_remaining(transactions)` | (Decimal, Decimal) | Anchor-period semantics: sum only PROJECTED transactions (skip status_id != projected at 411); income uses `effective_amount`, expenses use `_entry_aware_amount`. | period_subtotal | status_id != projected_id at 411 | income/expense split at 414-417 | anchor period | effective_amount at 415; `_entry_aware_amount` at 417 | no (uses effective_amount) | Decimal('0.00') initialization (403-404) | ref_cache.status_id at 406; _entry_aware_amount at 417 |
| 422 | `_sum_all(transactions)` | (Decimal, Decimal) | Non-anchor semantics: same filter set and amount logic as `_sum_remaining` but applied to all periods after the anchor. | period_subtotal | status_id != projected_id at 443 | income/expense split at 446-449 | non-anchor period | effective_amount at 447; `_entry_aware_amount` at 449 | no (uses effective_amount) | Decimal('0.00') initialization (436-437) | ref_cache.status_id at 439; _entry_aware_amount at 449 |

`_sum_remaining` vs `_sum_all` differ only in name and which period(s) they receive; the body
filters, type splits, amount sources, and lack of quantization are identical. Phase 3 must compare
the two functions line-by-line to confirm no behavioral divergence has sneaked in (audit plan
section 3.1 calls this out explicitly).

#### `app/services/dashboard_service.py` (731 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | `compute_dashboard_data(user_id)` | dict | Assembles all main-dashboard sections (balance, payday, upcoming bills, alerts, goals, debt, spending). | checking_balance, account_balance, projected_end_balance, savings_total, debt_total, dti_ratio, cash_runway_days | n/a (delegates) | n/a (delegates) | current + next period | n/a (delegates) | no | n/a (delegates) | pay_period_service.get_all_periods, get_current_period, get_next_period at 65-72; _compute_balances at 73; multiple section helpers at 78-92 |
| 99 | `_get_upcoming_bills(account_id, scenario_id, current_period, next_period)` | list[dict] | Queries PROJECTED expense transactions in current + next period with `selectinload(entries)`; converts each via `txn_to_bill_dict`. | period_subtotal | status_id == projected_id at 145 | expense at 146 | current + next periods (122-124) | effective_amount at 191 (via helper) | no | n/a (caller quantizes for display) | selectinload at 138; txn_to_bill_dict at 155 |
| 167 | `txn_to_bill_dict(txn, today)` | dict | Converts a Transaction to a display dict for templates: amount, due-date countdown, entry progress. | effective_amount | n/a | n/a | n/a | effective_amount at 191 | no | n/a | days_until_due property at 187; _entry_progress_fields at 199 |
| 203 | `_entry_progress_fields(txn)` | dict | For entry-tracked transactions, computes (is_tracked, entry_total, remaining, over_budget) by summing entries and comparing to estimated_amount. | goal_progress, effective_amount | n/a | n/a | n/a | estimated_amount at 239, 245 | YES (estimated_amount direct at 239 via `compute_remaining`, and at 245 for over-budget comparison) | n/a | entry_service.compute_entry_sums at 237; entry_service.compute_remaining at 239 |
| 252 | `_compute_alerts(account, settings, balance_results, current_period, all_periods)` | list[dict] | Detects stale anchor, negative balance, low-balance threshold alerts and sorts by severity. | account_balance, checking_balance | n/a | n/a | current + future (293-308) | balance_results dict (no direct column read) | no | threshold compared as Decimal at 314 | _get_last_anchor_date at 272 |
| 334 | `_get_balance_info(account, current_period, balance_results)` | dict | Returns current balance and cash runway; flags stale anchor. | checking_balance, cash_runway_days | n/a | n/a | current period | **Account.current_anchor_balance at 350** | YES (stored column read) | n/a | _get_last_anchor_date at 355; _compute_cash_runway at 363 |
| 375 | `_compute_cash_runway(account_id, current_balance)` | int \| None | Daily-average paid expenses over the past 30 days by due_date; runway = `current_balance / daily_avg` (int days). | cash_runway_days | DONE/RECEIVED/SETTLED at 391-395 | expense at 396 | last 30 calendar days by due_date (390) | effective_amount via Transaction property | no | int truncation; returns 0 for non-positive balance (line 388) | none (raw query) |

**P1-f arithmetic re-verification (2026-05-15).** Source-read every row this
subsection presents as producing a financial concept via computation, applying
the P1-d classification rule table (arithmetic operators only; comparison,
selection, bare clamp/abs, `Decimal(str())` type-normalization, quantize, and
format do NOT count).

Conditional on financial value (NOT arithmetic -- Phase 3 must not treat these
as arithmetic producers; original table-row citations above are preserved):

- `dashboard_service.py:252` `_compute_alerts` -- the only financial touches
  are `bal < _ZERO` (line 298) and `current_bal < Decimal(str(low_threshold))`
  (line 314), both comparisons; the lone subtraction `date.today() -
  last_anchor.date()` (line 281) is non-financial date arithmetic. The
  account_balance / checking_balance tokens are consumed-and-compared, not
  produced. Concept-token producer attribution in 1.7.3 over-states this row.

Non-arithmetic, reads + delegates (no money operator):

- `dashboard_service.py:334` `_get_balance_info` -- `balance_results.get(...)`
  / `account.current_anchor_balance` reads plus `_compute_cash_runway`
  delegation; only a date comparison. Not a producer of checking_balance /
  cash_runway_days (it delegates the runway computation).

True-negative confirmed (was already framed as a read, no relocation needed):

- `dashboard_service.py:167` `txn_to_bill_dict` -- pure `txn.effective_amount`
  read (line 191) + non-financial `txn.due_date - today` (line 187) +
  `_entry_progress_fields` delegate; verified NOT arithmetic, consistent with
  the inventory's existing "effective_amount read" framing.

KEEP -- genuine arithmetic, re-verified at source: `:203`
`_entry_progress_fields` (`total = debit + credit`, line 238); `:375`
`_compute_cash_runway` (`sum(abs(...))` line 411, `current_balance /
daily_avg` lines 415-416).

#### `app/services/savings_dashboard_service.py` (956 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 61 | `compute_dashboard_data(user_id)` | dict | Orchestrates the /savings page: account projections, goals, emergency fund, DTI. | savings_total, debt_total, dti_ratio, goal_progress, emergency_fund_coverage_months | n/a (delegates) | n/a (delegates) | all periods | n/a (delegates) | no | DTI quantized via `Decimal("0.1"), ROUND_HALF_UP` at 172, 176 | _load_account_params at 79; _compute_account_projections at 90; _compute_emergency_fund at 132; _compute_debt_summary at 156; multiple helpers through 195 |
| 201 | `_load_account_params(user_id, accounts)` | dict | Batch-loads InterestParams, InvestmentParams, LoanParams, deductions grouped by account.id. | - | n/a | n/a | n/a | n/a | no | salary_gross_biweekly quantize at 266 | DB queries at 213-291 |
| 294 | `_compute_account_projections(accounts, all_transactions, all_shadow_income, all_periods, current_period, params)` | list[dict] | Dispatches by account type: interest -> `calculate_balances_with_interest`, no-params -> `calculate_balances`, loan -> `amortization_engine.get_loan_projection`, investment -> `_project_investment`. | account_balance, projected_end_balance, monthly_payment, loan_principal_real, growth | n/a (delegates) | filtered upstream by caller | all periods | **proj.current_balance at 373 (A-04: ARM = stored current_principal, fixed-rate = engine-computed)** | YES (for ARM at 373) | n/a (delegates) | balance_calculator.calculate_balances_with_interest at 335; balance_calculator.calculate_balances at 343; amortization_engine.get_loan_projection at 362; _project_investment at 389 |
| 802 | `_compute_debt_summary(account_data, escrow_map)` | dict \| None | Aggregates monthly P&I+escrow across loan accounts for DTI. | monthly_payment, debt_total, dti_ratio | n/a | n/a | n/a | `ad["monthly_payment"]` at 846 | no | Decimal('0.01') quantize at 851, 873 | aggregation only |

Numerous private helpers in this file produce balance/goal/projection data; the four entries above
are the load-bearing public-facing functions. Phase 3 must specifically compare
`_compute_account_projections` line 373 against `balance_calculator.calculate_balances` results for
the same account on the same period, as A-04 and the developer's reported symptom #5 (`/accounts` vs
`/savings` divergence) make this the central question.

#### `app/services/retirement_dashboard_service.py` (500 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 79 | `compute_gap_data(user_id, swr_override=None, return_rate_override=None)` | dict | /retirement page: pension benefit, account projections, income gap, chart data. | paycheck_gross, paycheck_net, savings_total, growth, employer_contribution, pension_benefit_monthly | n/a (delegates) | n/a (delegates) | current period -> retirement date | n/a (delegates) | no | salary conversion quantize at 197; take-home rate at 214; SWR income at 240-241 | pension_calculator.calculate_benefit; paycheck_calculator.project_salary; retirement_gap_calculator.calculate_gap; growth_engine.project_balance |
| 257 | `compute_slider_defaults(data)` | dict | Computes balance-weighted average return rate and converts stored SWR (Decimal(0.04)) to display percentage (Decimal(4.00)). | growth, apy_interest | n/a | n/a | n/a | InvestmentParams.assumed_annual_return; UserSettings.safe_withdrawal_rate | no | SWR percentage conversion at 307-308 (PA-04 float-cast finding lives in this helper per priors) | balance-weighted loop at 318-324 |
| 338 | `_project_retirement_accounts(user_id, accounts, all_periods, current_period, planned_retirement_date, salary_profiles, traditional_type_ids, return_rate_override)` | list[dict] | Projects each retirement account forward via `growth_engine.project_balance` using shadow income contributions. | account_balance, projected_end_balance, growth, employer_contribution | n/a | shadow income at 376 (`transfer_id IS NOT NULL AND income type`) | through retirement date | **acct.current_anchor_balance at 405, 441-442** | YES (stored column at 405, 442) | salary_gross_biweekly quantize at 390 | balance_calculator.calculate_balances at 420; growth_engine.project_balance at 480 |

**P1-f arithmetic re-verification (2026-05-15).** Source-read the one row this
subsection presents as computing a financial value.

Borderline -- KEEP arithmetic, flag for Phase 3 adjudication:

- `retirement_dashboard_service.py:257` `compute_slider_defaults` -- genuine
  balance-weighted average (`total_balance += bal` line 323; `weighted_return
  += bal * params.assumed_annual_return` line 324; `weighted_return /
  total_balance` line 327), so it stays classified as arithmetic. FLAG: the
  `settings.safe_withdrawal_rate * _PCT_SCALE` (line 307) and `... *
  _PCT_SCALE` (line 327) are rate-to-percentage **presentation** conversions
  (0.04 -> 4.00), exactly the "percentage-to-rate displays" family named in
  caveat 1.7.6 (1). Phase 3 adjudicates whether presentation-only percentage
  scaling is in-scope financial arithmetic.

#### `app/services/year_end_summary_service.py` (2248 lines)

Imports flagged: none. Reads `budget.transfers`? **YES** at `_compute_transfers_summary` line 658
(`db.session.query(Transfer)`). Classification: LEGITIMATE -- this is display aggregation for the
year-end transfers tab, NOT a balance computation, so Transfer Invariant 5 (which scopes to balance
calculation) is not implicated. Phase 3 should still record this read in the transfer-model audit.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 66 | `compute_year_end_summary(user_id, year)` | dict | Entry point: aggregates the year-end tab (income/tax, spending, transfers, net worth, debt, savings, timeliness). | year_summary_jan1_balance, year_summary_dec31_balance, year_summary_principal_paid, year_summary_growth, year_summary_employer_total | n/a (delegates) | n/a (delegates) | calendar year | n/a (delegates) | no | n/a (delegates) | _load_common_data at 86; _build_summary at 87 |
| 380 | `_compute_mortgage_interest(year, debt_schedules)` | Decimal | A-06 aggregation: sums `row.interest` for amortization schedule rows whose `payment_date.year == year`. The escrow-subtraction and biweekly-redistribution preprocessing happens upstream in `loan_payment_service.prepare_payments_for_engine`. | interest_paid_per_period, total_interest, year_summary_growth | n/a | n/a | calendar year (payment_date.year filter) | row.interest from schedule (line 406) | no | n/a (sum of pre-quantized rows) | schedule traversal at 403-407 |
| 414 | `_compute_spending_by_category(user_id, year, period_ids, scenario_id)` | list[dict] | Groups settled expense transactions by `(group_name, item_name)` using `_attribution_year` (`COALESCE(due_date, pp_start_date)`); sums `abs(txn.effective_amount)`; attaches `entry_breakdown` from `_compute_entry_breakdowns` for envelope-tracked items (OP-3). | period_subtotal, effective_amount | settled via `_query_settled_expenses` (filter at line 1332+) | expense via `_query_settled_expenses` | calendar year via `period_ids` and `_attribution_year` (454) | `txn.effective_amount` at 457 | no | n/a (sum of model property) | `_query_settled_expenses` at 445; `_attribution_year` at 454; `_txn_category_names` at 456; `_build_spending_hierarchy` at 459; `_compute_entry_breakdowns` at 462 |
| 475 | `_compute_entry_breakdowns(user_id, year, period_ids, scenario_id)` | dict | Per-category envelope entry sums with credit/debit partition. | effective_amount (envelope source) | settled at 547 | expense at 546 | calendar year via period_ids (544) | TransactionEntry.amount at 519, 527 (`func.sum`, conditional `case(is_credit)`) | YES (sum entry.amount directly) | n/a (SQL); per-entry average quantized at 626-627 | SQL aggregate query |
| 518-528 | (SQL aggregates) | rows | The two money SQL aggregates per P1-a section 1.6: `func.sum(TransactionEntry.amount)` and conditional credit-entry sum via `case((is_credit, amount), else_=0)`. Filter set (lines 541-549): user, scenario, period_ids, is_deleted=False, expense type, settled status, `template.is_envelope=True`. | effective_amount (envelope entry source) | settled at 547 | expense at 546 | calendar year (544) | TransactionEntry.amount direct | YES (intentional per envelope semantics) | none at SQL layer | embedded in query |
| 636 | `_compute_transfers_summary(user_id, period_ids, scenario_id)` | list[dict] | Queries `Transfer` model directly (NOT shadow transactions); groups transfers by destination account for the year; sums `Transfer.amount`. | transfer_amount | excludes via `_get_excluded_status_ids()` at 655, filter at 665 (excludes CREDIT/CANCELLED) | n/a (queries Transfer, not Transaction) | calendar year via period_ids (663) | `Transfer.amount` (read at 679 via `t.amount`) | n/a (no Transaction.effective_amount involved) | none here; sums column-precision Decimal | db.session.query(Transfer) at 658; joinedload(Transfer.to_account) at 659 |
| 689 | `_compute_net_worth(year, accounts, all_periods, scenario, debt_schedules=None, ctx=None)` | dict | Monthly net worth (12 samples + Jan 1 + Dec 31) via balance projections and debt schedule lookups. Delegates anchor reads to `_get_account_balance_map` (line 2036). | net_worth, account_balance, debt_total, year_summary_jan1_balance, year_summary_dec31_balance | n/a | n/a | Jan 1, monthly, Dec 31 | n/a (delegates) -- the anchor-balance read happens inside `_get_account_balance_map` (line 2036+) | no (at this layer; delegate reads stored column) | n/a (delegates) | _build_account_data at 727; _get_month_end_periods at 726; _find_period_before_date at 731; _compute_monthly_values at 737 |
| 824 | `_compute_debt_progress(user_id, scenario_id, year, debt_schedules, balance_map, debt_accounts)` | dict | Per-debt principal-paid and interest accrued for the year via amortization schedule traversal. | year_summary_principal_paid, year_summary_growth, debt_total | n/a | n/a | calendar year by payment_date | row.principal, row.interest | no | n/a (schedule pre-quantized) | schedule traversal at 850-886 |
| 887 | `_compute_savings_progress(savings_accounts, period_ids, scenario_id, all_periods, year, scenario, ctx)` | list[dict] | Per-savings-account growth, contributions, employer_total. Dispatches to `_project_investment_for_year` (line 1027) for investment accounts, `_compute_interest_for_year` (line 1207) for HYSA, `_get_account_balance_map` (line 2036) for plain savings. | savings_total, year_summary_growth, year_summary_employer_total, goal_progress | n/a | n/a (delegates) | through year | n/a at this layer (delegates) | no (delegate reads stored column; see helpers) | inherits from helpers | _sum_shadow_income at 929; _project_investment_for_year at 938; _compute_interest_for_year (at later branch); _get_account_balance_map at 944, 961 |
| 1207 | `_compute_interest_for_year(account, interest_params, scenario, all_periods, year)` | Decimal | Projects per-year interest on an interest-bearing savings account via the balance calculator's interest path. | apy_interest, year_summary_growth | n/a | n/a | through year | **account.current_anchor_balance at 1244** | YES (stored column) | inherits from balance_calculator | balance_calculator.calculate_balances_with_interest at 1245 |
| 1263 | `_compute_payment_timeliness(user_id, period_ids, scenario_id)` | dict | Average days_paid_before_due across settled expense transactions (paid_on_time vs paid_late counts plus mean days-before-due). | - (non-financial; counts plus an average integer-days figure) | settled (`_get_settled_status_ids` filter applied via `_query_settled_expenses`) | expense (via `_query_settled_expenses` at line 1332) | period_ids (year-scoped at caller) | `txn.days_paid_before_due` model property | no | Decimal('0.01'), ROUND_HALF_UP at 1319 | `_query_settled_expenses` at 1332 |
| 1465-1469 | (ARM schedule anchor in `_balance_from_schedule_at_date`) | row | A-04: anchors the loan schedule at `current_principal` for ARM accounts so the projected schedule starts from the user-verified principal rather than from origination. | loan_principal_stored | n/a | n/a | n/a | LoanParams.current_principal | YES (per A-04 dual policy) | n/a | embedded |

The full file has 30+ private helpers; the above are the public-facing and load-bearing functions
Phase 3 will trace through.

**P1-f arithmetic re-verification (2026-05-15).** Source-read every in-scope
computational row; this file is also the caveat 1.7.6 (2) line-drift
cross-check target, so each citation was checked against actual source and the
P1-b verified-citations sub-list.

Non-arithmetic, type-normalize + conditional anchor read (per A-04):

- `year_end_summary_service.py:1465-1469` ARM schedule anchor -- `anchor_bal =
  Decimal(str(params.current_principal)) if params.is_arm else None`. This is
  `Decimal(str())` type-normalization of a stored column under a conditional;
  no arithmetic operator. It is the A-04 ARM-anchor read (already a YES in the
  cross-cutting effective_amount-bypass table), NOT an arithmetic producer of
  loan_principal_stored. Phase 3 must not treat it as arithmetic.

Citation-quality fixes (caveat 1.7.6 (2) fold-in; line numbers themselves are
accurate -- the drift is in function attribution / range precision):

- The `1465-1469` ARM anchor is enclosed by `_generate_debt_schedules` (def
  at line 1421), NOT `_balance_from_schedule_at_date` as stated in this
  subsection's `| 1465-1469 |` row and in the cross-cutting bypass table
  (`year_end_summary_service.py:1465-1469`). Trust the line numbers; the
  enclosing-function name is wrong.
- `:518-528` -- the two money SQL aggregates are precisely line 519
  (`db.func.sum(TransactionEntry.amount)`) and 520-528 (`db.func.sum(case(
  ...))`); cited line 518 is `db.func.count(TransactionEntry.id)` (non-money).
  Range is loose by one line (within the +/-2 tolerance).
- `:824` `_compute_debt_progress` -- def-line citation is exact, but the
  Signature column lists `(user_id, scenario_id, year, debt_schedules,
  balance_map, debt_accounts)` whereas the actual signature is
  `_compute_debt_progress(year, debt_accounts, debt_schedules, ...)`.
  Signature drift, not line drift.

KEEP -- genuine arithmetic, def-line citations EXACT and in the P1-b verified
sub-list: `:380` `_compute_mortgage_interest` (`total_interest += row.interest`
line 406); `:414` `_compute_spending_by_category` (sums `effective_amount`
line 457, per P1-b verified); `:475` / `:518-528` `_compute_entry_breakdowns`
(`db.func.sum`); `:636` `_compute_transfers_summary` (`total_amount +=
t.amount` line 679); `:824` `_compute_debt_progress` (`principal_paid =
jan1_bal - dec31_bal` line 871).

#### `app/services/budget_variance_service.py` (431 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 99 | `compute_variance(user_id, window_type, period_id=None, month=None, year=None, account_id=None)` | VarianceReport | Estimated vs actual variance grouped by category; supports pay period / calendar month / calendar year windows. | effective_amount, period_subtotal | excludes CREDIT and CANCELLED at 207-210 | all types | period/month/year (212-222) | n/a (delegates) | no | variance_pct quantize at 149 | _get_transactions_for_window; _build_group_hierarchy; variance computation 132-151 |
| 358 | `_build_txn_variance(txn)` | TransactionVariance | Per-transaction (actual - estimated, percentage). | effective_amount | n/a | n/a | n/a | actual_amount at 390; estimated_amount at 391-393 | YES (actual_amount directly at 390) | n/a | status.is_settled at 376 |
| 381 | `_compute_actual(txn)` | Decimal | Reads actual if settled and non-null, else estimated; mirrors `effective_amount` logic by hand. | effective_amount | status.is_settled at 389 | n/a | n/a | actual_amount at 390; estimated_amount at 391-393 | YES (direct two-column read) | n/a | status check |
| 396 | `_pct(variance, estimated)` | Decimal \| None | `variance / estimated * 100`, guarding zero. | - | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 404 | none |

`_build_txn_variance`/`_compute_actual` reimplement the `effective_amount` logic inline. Phase 3
must compare this hand-rolled version against `Transaction.effective_amount` at
`transaction.py:221-245` to confirm they agree on every input (especially around `is_deleted` and
`status.excludes_from_balance`).

**P1-f arithmetic re-verification (2026-05-15).** Source-read every
computational row in this subsection.

Conditional on financial value (NOT arithmetic):

- `budget_variance_service.py:381` `_compute_actual` -- `if
  txn.status.is_settled: return txn.actual_amount` else `return
  txn.estimated_amount`; pure conditional attribute selection (a hand-rolled
  `effective_amount` mirror), no operator. Already listed in the cross-cutting
  effective_amount-bypass table; recorded here so Phase 3 does not read the
  combined `358-393` index citation as implying `_compute_actual` itself does
  arithmetic. `_build_txn_variance` (358) does the subtraction; `_compute_actual`
  (381) only selects.

KEEP -- genuine arithmetic, re-verified at source: `:99` `compute_variance`
(`total_act - total_est` line 139, plus the two `sum(...)` lines 137-138);
`:358` `_build_txn_variance` (`variance = actual - estimated` line 367);
`:396` `_pct` (`variance / estimated * _HUNDRED` line 404 -- the division is a
substantive ratio, so genuine arithmetic; the `* 100` is percentage scaling
but does not reduce the row to presentation-only).

#### `app/services/spending_trend_service.py` (535 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 97 | `compute_trends(user_id, threshold=Decimal("0.1000"), account_id=None)` | TrendReport | Per-category linear-regression trend over a 3 or 6 month rolling window. | growth, period_subtotal, effective_amount | settled status set at 173 | expense at 172 | rolling window (133-137) | effective_amount at 322 (via abs()) | no | period_average at 328; absolute_change at 332; pct_change at 337 (Decimal('0.01'), ROUND_HALF_UP) | data load and trend computation 129-155 |
| 265 | `_build_item_trends(transactions, periods, threshold)` | list[ItemTrend] | Groups transactions by category; computes per-period totals and regression slope/intercept. | growth, period_subtotal | n/a (pre-filtered) | n/a (pre-filtered) | window | effective_amount at 322 | no | quantize at 328, 332 | _compute_item_trend at 288 |
| 296 | `_compute_item_trend(cat_id, txns, period_index_map, n_periods, threshold)` | ItemTrend | Per-category metrics: per-period totals, regression, pct_change. | growth, period_subtotal | n/a | n/a | window | effective_amount at 322 | no | quantize at 328, 332; pct_change via _safe_pct_change at 482 | _compute_linear_regression at 331 |
| 360 | `_build_group_trends(items, threshold)` | list[GroupTrend] | Aggregates item trends to group level with spending-weighted pct_change. | growth, effective_amount | n/a | n/a | n/a | n/a (item-level) | no | weighted_pct quantize at 385 | group aggregation 374-397 |
| 425 | `_compute_linear_regression(values)` | (Decimal, Decimal) | OLS regression over equally spaced Decimal data points. | - | n/a | n/a | n/a | n/a | no | n/a (Decimal arithmetic; caller quantizes) | none |
| 470 | `_safe_pct_change(first_predicted, last_predicted)` | Decimal | `(last - first) / first * 100`, guarding zero. | growth | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 482 | none |

#### `app/services/debt_strategy_service.py` (703 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 521 | `calculate_strategy(debts, extra_monthly, strategy, custom_order=None, start_date=None, max_horizon_months=600)` | StrategyResult | Month-by-month avalanche/snowball/custom simulation with interest accrual, minimum payments, and extra-payment cascade as debts are retired. | debt_total, monthly_payment, payoff_date, total_interest, principal_paid_per_period | n/a (pure simulation) | n/a | simulation horizon (max_horizon_months) | DebtAccount fields: current_principal, interest_rate, minimum_payment | no | interest quantize at 413; balance snap via `_snap_to_zero`; totals quantize at 679-680 | _validate_inputs, _sort_debts at 571; simulation 600-624 |

The file contains 14 private helpers (interest accrual, payment cascade, strategy ordering); they
are deterministic given the inputs and Phase 6 should look for DRY opportunities here.

#### `app/services/savings_goal_service.py` (488 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 21 | `resolve_goal_target(goal_mode_id, target_amount, income_unit_id, income_multiplier, net_biweekly_pay)` | Decimal | Fixed amount or income-multiplier-driven target. | goal_progress, effective_amount (compare) | n/a | n/a | n/a | n/a | no | Decimal('0.01'), ROUND_HALF_UP, line 106 | mode dispatch at 68-106 |
| 109 | `calculate_required_contribution(current_balance, target_amount, remaining_periods)` | Decimal \| None | `gap / remaining_periods`, quantized. | goal_progress, period_subtotal | n/a | n/a | input count | n/a | no | quantize at 134-135 | none |
| 139 | `calculate_savings_metrics(savings_balance, average_monthly_expenses)` | dict | Emergency fund coverage: months, paychecks, years. | emergency_fund_coverage_months, effective_amount | n/a | n/a | n/a | n/a | no | months/paychecks/years quantize to 0.1 at 163-171 | none |
| 178 | `count_periods_until(target_date, periods)` | int | Periods between today and target_date. | - | n/a | n/a | target_date range | n/a | no | n/a | none |
| 199 | `amount_to_monthly(amount, pattern_id, interval_n=1)` | Decimal \| None | Converts per-occurrence amount to monthly equivalent by recurrence pattern. | period_subtotal, effective_amount | n/a | n/a | n/a | n/a | no | none here (caller responsible) | pattern dispatch 236-280 |
| 287 | `compute_committed_monthly(expense_templates, transfer_templates)` | Decimal | Sums monthly-equivalent costs from all active recurrence templates. | period_subtotal, effective_amount | n/a | n/a | n/a | template.default_amount at 311 | no | quantize at 328 | template loop 310-327 |
| 331 | `calculate_trajectory(current_balance, target_amount, monthly_contribution, target_date=None)` | dict | Months-to-goal, ahead/on-track/behind pace, required monthly rate. | goal_progress, period_subtotal | n/a | n/a | today -> target_date | n/a | no | ceiling division at 391-394; required_monthly Decimal('0.01'), ROUND_CEILING at 462-463 | _add_months at 397 |

#### `app/services/calendar_service.py` (516 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 88 | `get_month_detail(user_id, year, month, account_id=None, large_threshold=500)` | MonthSummary | Per-day calendar view of one month: txns, large/infrequent flags, month-end balance. | period_subtotal, effective_amount, checking_balance, projected_end_balance | all statuses (display) | all types (display) | calendar month | effective_amount at 255 (via helper) | no | n/a (delegates) | _query_transactions_for_range at 126; _build_month_summary at 130 |
| 136 | `get_year_overview(user_id, year, account_id=None, large_threshold=500)` | YearOverview | All-year calendar overview; one query, then partitions per month in Python. | period_subtotal, effective_amount, checking_balance, projected_end_balance, year_summary_jan1_balance, year_summary_dec31_balance | all statuses | all types | calendar year (12 months) | n/a (delegates) | no | n/a | 12-month partition at 166-177 |
| 240 | `_build_day_entry(txn, income_type_id, threshold)` | DayEntry | Converts one transaction to a per-day entry: income/paid/large/infrequent flags. | effective_amount, period_subtotal | n/a | check at 261 | n/a | effective_amount at 255 | no | n/a | _is_infrequent at 263 |
| 270 | `_assign_transactions_to_days(transactions, year, month, large_threshold)` | (dict, Decimal, Decimal) | Assigns txns to days (dedup by id), totals income/expense. | period_subtotal, effective_amount | n/a | n/a | calendar month | via _build_day_entry | no | n/a (sums) | _build_day_entry 293-310 |
| 313 | `_build_month_summary(year, month, account, periods, transactions, large_threshold, user_id, scenario)` | MonthSummary | Assembles month-end balance and totals. | period_subtotal, checking_balance, projected_end_balance | n/a | n/a | month + overlapping periods | n/a | no | n/a | _compute_month_end_balance at 435 |
| 435 | `_compute_month_end_balance(account, year, month, user_id, scenario)` | Decimal | Looks up last period ending on/before month-end and returns its balance from `balance_calculator.calculate_balances`. | checking_balance, projected_end_balance | n/a (delegates) | n/a (delegates) | last period on/before month-end (464) | **account.current_anchor_balance at 483** | YES (stored column) | n/a (delegates) | balance_calculator.calculate_balances 454-489 |

**P1-f arithmetic re-verification (2026-05-15).** Source-read every
computational row in this subsection.

Conditional on financial value (NOT arithmetic):

- `calendar_service.py:240` `_build_day_entry` -- `amount =
  txn.effective_amount` (line 255, pure read) then `is_large = abs(amount) >=
  threshold` (line 262, bare-`abs` of an already-read value + comparison); no
  money operator. The period_subtotal / effective_amount tokens are
  consume-only for this row.

Non-arithmetic, lookup + delegate (no money operator):

- `calendar_service.py:435` `_compute_month_end_balance` -- selects the target
  period by `p.end_date <= last_day` (date comparison), delegates to
  `balance_calculator.calculate_balances`, returns `balances.get(...)`. Not a
  producer of checking_balance / projected_end_balance (it delegates the
  balance computation).

KEEP -- genuine arithmetic, re-verified at source: `:270`
`_assign_transactions_to_days` (`total_income += entry.amount` line 302;
`total_expenses += abs(entry.amount)` line 304).

#### `app/services/companion_service.py` (167 lines)

Imports flagged: none. Reads `budget.transfers`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 92 | `get_visible_transactions(companion_user_id, period_id=None)` | (list[Transaction], PayPeriod) | Returns the linked-owner's transactions whose template has `companion_visible=True` for one period. | effective_amount (deferred) | all (UI filters) | all | single period | ORM passthrough | no | n/a | transaction query 119-145 |
| 150 | `get_companion_periods(companion_user_id)` | list[PayPeriod] | Returns linked-owner's periods for navigation. | - | n/a | n/a | all periods | n/a | no | n/a | pay_period_service.get_all_periods at 164 |

### Group C: Transactional and workflow services

14 files inventoried. Total 5,135 LOC. Includes the only service permitted to mutate transfer
shadows (`transfer_service.py`), the carry-forward branch dispatcher (`carry_forward_service.py`),
both recurrence engines, the credit and entry-credit workflows, and resolvers/utilities. Zero Flask
imports. Zero shadow mutations outside `transfer_service`.

#### `app/services/transaction_service.py` (168 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (precondition at
line 111 refuses `transfer_id IS NOT NULL`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 38 | `settle_from_entries(txn, *, paid_at=None)` | None | A-02 envelope settle: validates preconditions (not deleted, not transfer shadow, envelope template, status mutable), computes `actual_amount = sum(entries)` via `entry_service.compute_actual_from_entries`, sets status to DONE (expense) or RECEIVED (income) at lines 144-147, sets `paid_at` (default `db.func.now()`); does NOT change `pay_period_id`. | effective_amount | precondition: status.is_immutable=False at 130-138; outcome: DONE/RECEIVED at 144-147 | DONE for expense, RECEIVED for income (144-147) | unchanged | entries amount sum via helper at 153 | YES (entry.amount sum at 153) | none here (caller owns) | entry_service.compute_actual_from_entries at 153; ref_cache.status_id at 145, 147; log_event at 155 |

#### `app/services/transfer_service.py` (848 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (this IS the
transfer service; mutations are authorized by Transfer Invariant 4).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 283 | `create_transfer(user_id, from_account_id, to_account_id, pay_period_id, scenario_id, amount, status_id, category_id, notes=None, transfer_template_id=None, name=None, due_date=None)` | Transfer (+ 2 shadow Transactions, atomically flushed) | Validates ownership of all referenced entities; creates the parent `Transfer` and two shadow `Transaction` rows (expense at from_account, income at to_account); enforces Invariants 1 (two shadows), 2 (atomic creation), 3 (amount/status/period mirror parent). | transfer_amount | shadows initialized to `status_id` arg (typically PROJECTED) at 388, 409 | expense_type_id at 391; income_type_id at 412 | pay_period_id from caller at 387, 407 | amount Decimal-validated at 333; assigned to both shadows' `estimated_amount` at 392, 413 | no | none here (caller validates Decimal) | _validate_positive_amount at 333; _get_owned_account at 340, 343; _get_owned_period at 346; _get_owned_scenario at 347; _get_owned_category at 348; _get_owned_transfer_template at 349; ref_cache.txn_type_id at 352, 353; log_event at 424 |
| 443 | `update_transfer(transfer_id, user_id, **kwargs)` | Transfer (mutated in place) | Applies field updates; for status_id changes runs `state_machine.verify_transition` at 499 BEFORE shadow mutation; auto-syncs `paid_at` on settled transitions at 524-533; propagates amount/period/category to both shadows; rejects illegal transitions before any mutation. | transfer_amount | verify_transition guard at 499; transition rules drive shadow status sync at 500-502; settled-paid_at sync at 524-533 | n/a | new period validated and assigned to both shadows at 536-541 | new amount Decimal-validated at 485; assigned to both shadows at 487-488 | no | none here | _get_transfer_or_raise at 480; _get_shadow_transactions at 481; _validate_positive_amount at 485; state_machine.verify_transition at 499; db.session.get(Status, ...) at 525; _get_owned_period at 538; _get_owned_category at 549; log_event at 603 |
| 616 | `delete_transfer(transfer_id, user_id, soft=False)` | Transfer (if soft) or None (if hard) | Soft-delete: sets `is_deleted=True` on parent and both shadows explicitly (flag changes don't fire CASCADE). Hard-delete: lets `ON DELETE CASCADE` remove shadows, then queries to verify zero orphan shadows remain. | transfer_amount | n/a | n/a | n/a | n/a | no | n/a | _get_transfer_or_raise at 637; log_event at 651, 678 |
| 688 | `restore_transfer(transfer_id, user_id)` | Transfer | Sets `is_deleted=False` on parent and both shadows; verifies one expense + one income shadow exists; refuses restore if either account is archived (F-164); reconciles shadow `amount/status/period` drift that may have accumulated while soft-deleted. | transfer_amount | shadow status_id reconciled to parent at 821-828 | shadow types verified as one expense + one income at 755-769 | shadow pay_period_id reconciled to parent at 831-838 | shadow amounts reconciled to parent.amount at 811-818 | no | n/a | _get_transfer_or_raise at 716; db.session.get(Account, ...) at 780, 781; log_event at 841 |

Invariant enforcement summary:

- Invariant 1 (two shadows): create_transfer at 381-421.
- Invariant 2 (atomic): create_transfer flushes both together at 422.
- Invariant 3 (mirror): create_transfer 392/413; update_transfer 487/488; restore_transfer 811-818,
  821-828, 831-838.
- Invariant 4 (only this service): all shadow mutations live here; carry_forward_service delegates
  to update_transfer at carry_forward_service.py:461-466 per A-07.
- Invariant 5 (balance_calculator does not query transfers): satisfied -- see balance_calculator.py
  inventory above.

#### `app/services/carry_forward_service.py` (1016 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (delegates to
`transfer_service.update_transfer` for transfer-shadow moves at 461-466 per A-07).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 210 | `_build_carry_forward_context(source_period_id, target_period_id, user_id, scenario_id)` | _CarryForwardContext | Validates ownership of both periods; queries source-period PROJECTED, non-deleted, scenario-scoped transactions; three-way partitions into transfer shadows (`transfer_id IS NOT NULL`), envelope (`template.is_envelope`), discrete (else). Per A-07, both A-02 envelope and prod_readiness_v1 discrete branches coexist. | - | PROJECTED only at 263 | partition by transfer_id (273) and template.is_envelope (275) | source period | n/a (read-only partition) | n/a | n/a | PayPeriod model at 235; ref_cache.status_id at 256 |
| 291 | `carry_forward_unpaid(source_period_id, target_period_id, user_id, scenario_id)` | int | Atomically applies all three branch mutations: envelope settle-and-roll via `_settle_source_and_roll_leftover` at 446 (per A-02), discrete bulk UPDATE moving rows to target with `is_override=True` at 415-416 (per A-07 W-192 discrete branch), transfer-shadow delegation to `transfer_service.update_transfer(pay_period_id=target, is_override=True)` at 461-466. Returns count of carried items. | effective_amount (envelope only) | source rows are PROJECTED (enforced by context) at 263 | per partition | source -> target | envelope: entries sum via `compute_actual_from_entries` inside the helper; discrete: row-level move; transfer: delegated | YES (envelope reads entry.amount; per A-02 intent) | none at this layer | _build_carry_forward_context at 335; Transaction.query bulk UPDATE at 407-437; transfer_service.update_transfer at 461; _settle_source_and_roll_leftover at 446; db.session.flush at 470; log_event at 471 |
| 482 | `preview_carry_forward(source_period_id, target_period_id, user_id, scenario_id)` | CarryForwardPreview | Read-only plan: emits one CarryForwardPlan per source row with blocked/actionable status; envelope plans compute target estimated before/after; transfer and discrete plans always actionable. | - | n/a (read-only) | n/a | n/a | n/a | n/a | n/a | _build_carry_forward_context at 532; _build_envelope_plan at 543; _build_discrete_plan at 547; _build_transfer_plan at 554 |
| 563 | `_build_envelope_plan(source_txn, target_period, scenario_id)` | CarryForwardPlan | Computes entries_sum, leftover, target estimated before/after; delegates target-row decisions (eight branches) to `_resolve_envelope_target_fields`. | effective_amount | n/a (planning) | n/a | target period | source entries sum via `compute_actual_from_entries` at 586 | YES (entries sum) | n/a | compute_actual_from_entries at 586; _resolve_envelope_target_fields at 590 |
| 602 | `_resolve_envelope_target_fields(source_txn, target_period, scenario_id, leftover)` | dict | Eight-branch decision tree (leftover==0 / duplicate targets / one mutable / one immutable / only soft-deleted / no template / template inactive / no rows + template active) determining whether carry-forward is blocked vs actionable. | effective_amount | target status.is_immutable check at 665, 970 | n/a | target period at 646 | n/a (leftover precomputed) | n/a | n/a | Transaction.query at 642; recurrence_engine.can_generate_in_period at 725 |
| 757 | `_build_discrete_plan(source_txn)` | CarryForwardPlan (blocked=False) | Discrete rows always actionable; whole-row move. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 772 | `_build_transfer_plan(shadow_txn)` | CarryForwardPlan (blocked=False) | Transfer shadows always actionable; delegates to transfer_service for the actual move. | transfer_amount | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 788 | `_settle_source_and_roll_leftover(source_txn, target_period, scenario_id)` | None | A-02 settle-and-roll: computes entries_sum via `compute_actual_from_entries` at 878; calls `_find_or_generate_target_canonical` at 888; if leftover > 0, bumps `target_row.estimated_amount += leftover` and sets `target_row.is_override = True` at 891-893; settles source via `transaction_service.settle_from_entries`. | effective_amount (computed + bumped) | target.status.is_immutable at 970 | n/a | source -> target | entries sum at 878; estimated_amount additive bump at 891-893 | YES (entries) | none (column precision) | compute_actual_from_entries at 878; recurrence_engine deferred import at 870; transaction_service deferred import at 871; _find_or_generate_target_canonical at 888 |
| 899 | `_find_or_generate_target_canonical(source_txn, target_period, scenario_id, recurrence_engine)` | Transaction | Looks up existing non-deleted target row (template_id, target.id, scenario_id); if mutable, returns it; if immutable raises ValidationError; if absent, calls `recurrence_engine.generate_for_template` to materialize the canonical and returns it. | - | target.status.is_immutable at 970 | n/a | target period at 945 | n/a | n/a | n/a | Transaction.query at 941; recurrence_engine.generate_for_template at 999 |

Bulk UPDATE pattern (lines 405-437) uses `WHERE status_id == projected_id` to guard against race
with concurrent `mark_done` (F-049 cite at 371-374); `synchronize_session="fetch"` keeps in-memory
Transaction instances coherent with the UPDATE. The whole loop runs inside a `no_autoflush` block at
line 358 to prevent partial-mutation autoflushes from violating the partial unique index
`idx_transactions_template_period_scenario`.

#### `app/services/recurrence_engine.py` (775 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (refuses
transfer-shadow mutation at line 412).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 54 | `generate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transaction] | Generates Transaction rows per matching period; per A-03, skips existing/overridden/soft-deleted rows. Each row initialized PROJECTED; estimated_amount from `_get_transaction_amount` (paycheck_calculator if salary-linked, else template.default_amount). | effective_amount (estimated source) | PROJECTED at 158; A-03 skip on `existing.is_override` at 128, on `existing.is_deleted` at 133 | from template at 161 | matching_periods via `_match_periods` at 107 | estimated_amount from `_get_transaction_amount` at 145; reads template.default_amount or paycheck breakdown | no | n/a (calculator owns precision) | Scenario.query at 75; _match_periods at 107; _get_existing_map at 110; _get_salary_profile at 113; _get_transaction_amount at 145; _compute_due_date at 150; log_event at 171 |
| 180 | `can_generate_in_period(template, period, scenario_id)` | bool | Predicate mirroring `generate_for_template` gating: no cross-user, rule exists, not ONCE pattern, period matches, no existing row (including soft-deleted). | - | n/a | n/a | single period at 238 | n/a | no | n/a | Scenario.query at 217; _match_periods at 238; _get_existing_map at 244 |
| 251 | `regenerate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transaction] | Deletes auto-generated-unmodified rows on/after `effective_from`, regenerates from rule, raises `RecurrenceConflict` if overridden/deleted entries exist. | effective_amount | by `is_immutable`/`is_override`/`is_deleted` at 308-320 | n/a | PayPeriod.end_date >= effective_from at 298 | n/a (deletion) | n/a | n/a | Transaction.query + PayPeriod join at 292; log_event at 333 |
| 352 | `resolve_conflicts(transaction_ids, action, user_id, new_amount=None)` | None | Resolves override/delete conflicts post-regenerate; verifies ownership per `pay_period.user_id`; refuses transfer-shadow mutation at 412. | effective_amount (new_amount if supplied) | n/a | shadow guard at 412 | n/a | new_amount at 431 if supplied | no | n/a | Transaction.query at 382; log_resource_access_denied at 393; log_event at 434 |
| 447 | `_match_periods(rule, pattern_id, periods, effective_from)` | list[PayPeriod] | Filters periods by rule.end_date and effective_from; delegates to pattern-specific matcher. | - | n/a | n/a | filtered candidates at 466, 470 | n/a | n/a | n/a | _match_monthly at 481; _match_monthly_first at 484; _match_quarterly at 489; _match_semi_annual at 492; _match_annual at 499 |
| 506-607 | `_match_monthly`, `_match_monthly_first`, `_match_quarterly`, `_match_semi_annual`, `_match_specific_months`, `_match_annual` | list[PayPeriod] | Pattern-specific period-selection helpers; clamp day_of_month to month-end where needed. | - | n/a | n/a | per pattern | n/a | n/a | n/a | none |
| 610 | `_compute_due_date(rule, period)` | date | Primary: `rule.day_of_month` in period's month; override: `rule.due_day_of_month` (next-month convention if due_dom < dom). | - | n/a | n/a | within period | n/a | n/a | n/a | none |
| 680 | `_get_existing_map(template_id, scenario_id, periods)` | dict[int, list[Transaction]] | Builds period_id -> [Transaction] for existing template rows including soft-deleted and overridden. | - | n/a | n/a | supplied periods | n/a | n/a | n/a | Transaction.query at 693 |
| 708 | `_get_salary_profile(template_id)` | SalaryProfile \| None | Looks up active SalaryProfile for template. | - | n/a | n/a | n/a | n/a | n/a | n/a | SalaryProfile.query at 713 |
| 720 | `_get_transaction_amount(template, salary_profile, period, all_periods)` | Decimal | If salary-linked, calls `paycheck_calculator.calculate_paycheck` and returns `breakdown.net_pay`; else returns `template.default_amount`. Tax year derived from `period.start_date` with fallback to current year for future periods missing configs. | paycheck_net (if salary), effective_amount (template default) | n/a | n/a | period | template.default_amount at 734 or calculator result at 765 | no | n/a (calculator/column precision) | paycheck_calculator.calculate_paycheck at 761; tax_config_service.load_tax_configs at 741, 754 |

Per A-03: `is_override=True` blocks regeneration. The skip logic (lines 119-139) checks
`is_override` first (line 128), then `is_deleted` (line 133), then treats remaining
auto-generated-unmodified rows as already-existing.

#### `app/services/transfer_recurrence.py` (320 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (creates via
`transfer_service.create_transfer` at line 114).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 43 | `generate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transfer] | Generates Transfer rows per matching period, skipping per A-03 (override/deleted/immutable); delegates each creation to `transfer_service.create_transfer` so shadows are created atomically. | transfer_amount | PROJECTED at 121; A-03 skip on `is_override` at 97, `is_deleted` at 100 | created shadows are expense + income (delegated) | matching periods from `_match_periods` at 85 | template.default_amount at 120 | no | n/a | Scenario.query at 59; _match_periods at 85; _get_existing_map at 86; transfer_service.create_transfer at 114; log_event at 130 |
| 141 | `regenerate_for_template(template, periods, scenario_id, effective_from=None)` | list[Transfer] | Deletes auto-generated-unmodified transfers on/after `effective_from`, regenerates, raises `RecurrenceConflict` if conflicts exist. | transfer_amount | classified by is_immutable/is_override/is_deleted at 187-196 | n/a | PayPeriod.end_date >= effective_from at 177 | n/a (deletion) | n/a | n/a | Transfer.query + PayPeriod join at 171; log_event at 206 |
| 224 | `resolve_conflicts(transfer_ids, action, user_id, new_amount=None)` | None | Resolves override/delete conflicts; restores soft-deleted transfers via `transfer_service.restore_transfer` at 278 then applies updates via `transfer_service.update_transfer` at 287. Ownership-checked at 263. | transfer_amount | n/a | n/a | n/a | new_amount at 285 if supplied | no | n/a | Transfer.query at 257; log_resource_access_denied at 264; transfer_service.restore_transfer at 278; transfer_service.update_transfer at 287; log_event at 291 |
| 302 | `_get_existing_map(template_id, scenario_id, periods)` | dict[int, list[Transfer]] | Builds period_id -> [Transfer] including soft-deleted/overridden. | - | n/a | n/a | supplied periods | n/a | n/a | n/a | Transfer.query at 308 |

#### `app/services/credit_workflow.py` (370 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (refuses transfer
shadows at line 169).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 33 | `lock_source_transaction_for_payback(transaction_id, owner_id)` | Transaction (row-locked) | `SELECT ... FOR NO KEY UPDATE` with `populate_existing()` for concurrent-safe payback creation (F-008); ownership via `pay_period.user_id`. | - | n/a | n/a | n/a | n/a | n/a | n/a | Transaction.query at 95 |
| 112 | `mark_as_credit(transaction_id, user_id)` | Transaction (newly created payback) | Locks source; verifies not income, not transfer shadow, not entry-capable, not already CREDIT; sets source status CREDIT; finds or creates CC Payback category; creates a PROJECTED expense payback in next period with amount = `actual_amount if not None else estimated_amount` at line 229 (mirrors `Transaction.effective_amount` selection logic by hand). | effective_amount (source); creates payback at projected amount | source must be PROJECTED at 192; new status CREDIT at lines 178-179 | source: expense only (guard at 166); payback: EXPENSE at 240 | source -> next period (222) | actual_amount at 229; estimated_amount at 229 | YES (direct two-column read at 229) | none here | lock_source_transaction_for_payback at 165; ref_cache.status_id at 178, 179; db.session.expire at 213; db.session.get(PayPeriod, ...) at 217; get_or_create_cc_category at 219; pay_period_service.get_next_period at 222; ref_cache.txn_type_id at 198; log_event at 247 |
| 259 | `unmark_credit(transaction_id, user_id)` | None | Reverts source from CREDIT to PROJECTED; deletes auto-created payback. Two guards: bespoke state check at 309 (must be CREDIT); `state_machine.verify_transition` at 319 (defense-in-depth). | effective_amount (source) | CREDIT at 309 -> PROJECTED at 322; verify_transition at 319 | n/a | n/a | n/a | no | n/a | Transaction.query at 296, 325; verify_transition at 319; log_event at 335 |
| 344 | `get_or_create_cc_category(user_id)` | Category | Looks up or creates the "Credit Card: Payback" Category. | - | n/a | n/a | n/a | n/a | n/a | n/a | Category.query at 353 |

#### `app/services/entry_credit_workflow.py` (236 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (entry-only
mutations).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 42 | `sync_entry_payback(transaction_id, owner_id)` | Transaction (payback) \| None | 2x2 state matrix on (total_credit > 0, payback exists): create / update / delete / no-op. Row-locks source via `lock_source_transaction_for_payback` for concurrent safety. | effective_amount (payback amount) | n/a | n/a | source -> next period | `sum(e.amount for e in credit_entries)` at 112-114 | YES (entry.amount direct sum) | none here (column precision) | lock_source_transaction_for_payback at 99; db.session.expire at 108; _create_payback at 126; log_event at 139, 160 |
| 170 | `_create_payback(txn, owner_id, credit_entries, total_credit)` | Transaction (payback) | Creates PROJECTED expense payback in next period with `estimated_amount = total_credit`; links every credit entry via `credit_payback_id`. | effective_amount | PROJECTED at 203 | EXPENSE at 204 | next period at 196 | total_credit (arg) | n/a | none here | pay_period_service.get_next_period at 196; get_or_create_cc_category at 202; ref_cache.status_id at 203; ref_cache.txn_type_id at 204; log_event at 226 |

#### `app/services/entry_service.py` (589 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (entries have no
`transfer_id`).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 47 | `_update_actual_if_paid(txn)` | None | If transaction status is DONE, re-computes `actual_amount` from entries (late-posting fix-up). | effective_amount | DONE only at 68 | n/a | n/a | entry sum via `compute_actual_from_entries` at 70 | YES (entry.amount sum) | none here | compute_actual_from_entries at 70; ref_cache.status_id at 68 |
| 73 | `resolve_owner_id(user_id)` | int | Owner accounts -> user_id; companions -> linked_owner_id. | - | n/a | n/a | n/a | n/a | n/a | n/a | User.query at 91; ref_cache.role_id at 94 |
| 109 | `create_entry(transaction_id, user_id, amount, description, entry_date, is_credit=False)` | TransactionEntry | Creates a purchase entry: ownership (companion-aware), entry-capable template, transfer-shadow guard, expense-only, status guards (refuses CANCELLED and CREDIT). Flushes, then syncs payback and updates actual_amount if DONE. | effective_amount (entry-aware) | refuses CANCELLED at 173, CREDIT at 177 | expense at 162 | n/a | passed amount (Decimal) | n/a | none here | resolve_owner_id at 140; Transaction.query at 142; ref_cache.status_id at 171, 172; sync_entry_payback at 205; _update_actual_if_paid at 206; log_event at 194 |
| 211 | `update_entry(entry_id, user_id, **kwargs)` | TransactionEntry | Updates entry fields; re-validates ownership; flushes, syncs payback, updates actual_amount if DONE. | effective_amount (entry-aware) | n/a | n/a | n/a | new amount if supplied | n/a | none here | resolve_owner_id at 246; TransactionEntry.query at 241; sync_entry_payback at 266; _update_actual_if_paid at 267; log_event at 254 |
| 272 | `delete_entry(entry_id, user_id)` | int | Hard-deletes entry; re-validates ownership; returns parent transaction_id so caller can sync. | effective_amount (entry-aware) | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 294; TransactionEntry.query at 289; sync_entry_payback at 312; _update_actual_if_paid at 313; log_event at 303 |
| 318 | `get_entries_for_transaction(transaction_id, user_id)` | list[TransactionEntry] | Returns entries ordered by entry_date; ownership via `pay_period.user_id`. | - | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 335; Transaction.query at 337 |
| 348 | `compute_entry_sums(entries)` | (Decimal, Decimal) | Partitions entries by `is_credit`; returns (debit_sum, credit_sum). | - | n/a | n/a | n/a | entry.amount at 365, 367 | YES (direct sum) | none (column precision) | none |
| 371 | `build_entry_sums_dict(transactions)` | dict[int, dict] | For each transaction, computes entry sums via `compute_entry_sums` and bundles `{debit, credit, total, count}`. | - | n/a | n/a | n/a | entry.amount at 395, 397-399 | YES | none | compute_entry_sums at 395 |
| 405 | `compute_remaining(estimated_amount, entries)` | Decimal | `estimated_amount - sum(all_entries)`; negative = overspent. | - | n/a | n/a | n/a | entry.amount at 424 | YES | none | none |
| 428 | `compute_actual_from_entries(entries)` | Decimal | `sum(all_entries)`; returns `Decimal("0")` for empty list. Source for settle_from_entries. | effective_amount (settle source) | n/a | n/a | n/a | entry.amount at 446 | YES | none | none |
| 449 | `check_entry_date_in_period(entry_date, transaction)` | bool | Informational (OP-4); does NOT block. | - | n/a | n/a | period span | n/a | n/a | n/a | none |
| 471 | `clear_entries_for_anchor_true_up(owner_id)` | int | Bulk-UPDATE: marks past-dated debit entries on PROJECTED parents as `is_cleared=TRUE` so balance_calculator stops double-counting. | - | PROJECTED only at 507 | n/a | n/a | n/a | n/a | n/a | ref_cache.status_id at 507; TransactionEntry.query at 514; Transaction join at 519; log_event at 535 |
| 546 | `toggle_cleared(entry_id, user_id)` | TransactionEntry | Manual override of `is_cleared` for a single entry. | - | n/a | n/a | n/a | n/a | n/a | n/a | resolve_owner_id at 572; TransactionEntry.query at 568; log_event at 579 |

#### `app/services/_recurrence_common.py` (116 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (logging only).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | `log_template_cross_user_blocked(logger, *, message, template_id, template_user_id, scenario_id)` | None | Emits `EVT_CROSS_USER_BLOCKED` warning for IDOR defense-in-depth (template and scenario must share user_id). | - | n/a | n/a | n/a | n/a | n/a | n/a | log_event at 74 |
| 83 | `log_resource_access_denied(logger, *, user_id, model, pk, owner_id)` | None | Emits `EVT_ACCESS_DENIED_CROSS_USER` warning for row-level IDOR detection in resolve_conflicts paths (F-144). | - | n/a | n/a | n/a | n/a | n/a | n/a | log_event at 108 |

#### `app/services/state_machine.py` (171 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (pure predicate).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 77 | `_build_transitions()` | dict[int, set[int]] | Builds the legal-successor map keyed by status_id integer. Computed lazily on every `verify_transition` so test fixtures that re-seed ref_cache see fresh IDs. | - | n/a | n/a | n/a | n/a | n/a | n/a | ref_cache.status_id at 91-96 |
| 122 | `verify_transition(current_status_id, new_status_id, context="transaction")` | None (raises ValidationError on illegal) | Checks `new_status_id in transitions[current_status_id]`; identity transitions always legal. Workflow: projected -> {projected, done, received, credit, cancelled}; done/received -> {self, projected, settled}; credit -> {credit, projected}; cancelled -> {cancelled, projected}; settled -> {settled}. | - | from transitions dict at 161 | n/a | n/a | n/a | n/a | n/a | _build_transitions at 161 |

#### `app/services/pay_period_service.py` (216 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (PayPeriod-only
operations).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 | `generate_pay_periods(user_id, start_date, num_periods=52, cadence_days=14)` | list[PayPeriod] | Generates biweekly periods from start_date; assigns `period_index` starting at `max(existing) + 1` (uses `db.func.max(PayPeriod.period_index)` per P1-a section 1.6). Skips overlapping start_dates. | - | n/a | n/a | n/a | n/a | n/a | n/a | PayPeriod.query at 48; log_event at 85 |
| 96 | `get_current_period(user_id, as_of=None)` | PayPeriod \| None | Period containing `as_of` (default today). | - | n/a | n/a | single | n/a | n/a | n/a | PayPeriod.query at 109 |
| 120 | `get_periods_in_range(user_id, start_index, count)` | list[PayPeriod] | Returns `count` periods starting at `period_index = start_index`. | - | n/a | n/a | window | n/a | n/a | n/a | PayPeriod.query at 131 |
| 143 | `get_all_periods(user_id)` | list[PayPeriod] | All periods ordered by `period_index`. | - | n/a | n/a | all | n/a | n/a | n/a | PayPeriod.query at 152 |
| 160 | `get_next_period(period)` | PayPeriod \| None | Next sequential period (`period_index + 1`) for same user. | - | n/a | n/a | single | n/a | n/a | n/a | PayPeriod.query at 169 |
| 179 | `get_overlapping_periods(user_id, first_day, last_day)` | list[PayPeriod] | All periods whose [start_date, end_date] overlap [first_day, last_day]. Used by calendar/variance/spending services for month/year windowing. | - | n/a | n/a | overlap | n/a | n/a | n/a | PayPeriod.query at 207 |

#### `app/services/account_resolver.py` (131 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 36 | `resolve_grid_account(user_id, user_settings=None, override_account_id=None)` | Account \| None | 4-step fallback (override / settings default / first active checking / first active any-type). | account_balance | n/a | n/a | n/a | n/a | n/a | n/a | Account.query at 61, 71; ref_cache.acct_type_id at 60 |
| 79 | `resolve_analytics_account(user_id, account_id)` | Account \| None | 2-step fallback (explicit account_id with IDOR check / first active checking). Explicit failure returns None rather than silently picking a different account. | account_balance | n/a | n/a | n/a | n/a | n/a | n/a | Account.query at 122; ref_cache.acct_type_id at 121 |

#### `app/services/scenario_resolver.py` (48 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO.

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 22 | `get_baseline_scenario(user_id)` | Scenario \| None | Returns user's baseline scenario (`is_baseline=True`); enforced one-per-user via partial unique index. None when absent (test fixture or edge case). | - | n/a | n/a | n/a | n/a | n/a | n/a | Scenario.query at 44 |

#### `app/services/csv_export_service.py` (422 lines)

Imports flagged: none. Mutates shadow transactions outside `transfer_service`? NO (pure export).

| file:line | Signature | Returns | What it does | Concept token(s) | Status filter | Txn-type filter | Period scope | Amount column read | Bypass? | Quantization | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 28 | `_dec(value)` | str | Formats a numeric value to 2 decimals (ROUND_HALF_UP), '' for None. | - | n/a | n/a | n/a | n/a | n/a | Decimal('0.01'), ROUND_HALF_UP, line 39 | none |
| 42 | `_pct(value)` | str | Formats percentage to 2 decimals (ROUND_HALF_UP), '' for None. | - | n/a | n/a | n/a | n/a | n/a | Decimal('0.01'), ROUND_HALF_UP, line 53 | none |
| 56 | `_date(value)` | str | ISO 8601 date string; '' for None. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 72 | `_safe(value)` | str | str(value); '' for None. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 86 | `_bool_yn(value)` | str | 'Yes' or 'No'. | - | n/a | n/a | n/a | n/a | n/a | n/a | none |
| 98 | `_write_csv(rows)` | str | Uses `csv.writer` for proper quoting/escaping. | - | n/a | n/a | n/a | n/a | n/a | n/a | csv.writer at 108 |
| 116 | `export_calendar_csv(data, view_type)` | str | Dispatches to `_export_calendar_month` or `_export_calendar_year`. | - | n/a | n/a | n/a | n/a | n/a | inherits | _export_calendar_month at 134; _export_calendar_year at 166 |
| 134 | `_export_calendar_month(data)` | str | One row per transaction in day_entries; amounts via `_dec`. | effective_amount | n/a | n/a | month | entry.amount via _dec at 156 | n/a | inherits via _dec | _date at 152; _safe at 153-154; _dec at 156; _write_csv at 163 |
| 166 | `_export_calendar_year(data)` | str | One row per month summary. | effective_amount, period_subtotal | n/a | n/a | year | total_income/expenses/net/projected_end_balance via _dec at 190-193 | n/a | inherits via _dec | _dec at 190-193; _write_csv at 197 |
| 203 | `export_year_end_csv(data)` | str | Multi-section year-end CSV. | (multiple) | n/a | n/a | year | (section helpers) | n/a | inherits via _dec | section helpers at 216-222; _write_csv at 223 |
| 226 | `_add_income_section(rows, inc)` | None | Income/tax/deductions section. | paycheck_gross, federal_tax, state_tax, fica, pre_tax_deduction, post_tax_deduction, paycheck_net, total_interest | n/a | n/a | year | inc/ded keys via _dec at 230-250 | n/a | inherits via _dec | _dec at 230-250 |
| 253 | `_add_spending_section(rows, spending)` | None | Spending by category section. | effective_amount | n/a | n/a | year | group_total, item_total via _dec at 259-261 | n/a | inherits | _dec at 259-261 |
| 264 | `_add_transfers_section(rows, transfers)` | None | Transfers section. | transfer_amount | n/a | n/a | year | total_amount via _dec at 270 | n/a | inherits | _dec at 270 |
| 273 | `_add_net_worth_section(rows, nw)` | None | Net worth monthly + Jan 1 / Dec 31. | net_worth, year_summary_jan1_balance, year_summary_dec31_balance | n/a | n/a | year | balance/jan1/dec31/delta via _dec at 279-282 | n/a | inherits | _dec at 279-282 |
| 285 | `_add_debt_section(rows, debt)` | None | Debt progress section. | loan_principal_real, year_summary_principal_paid | n/a | n/a | year | jan1/dec31/principal_paid via _dec at 291-293 | n/a | inherits | _dec at 291-293 |
| 297 | `_add_savings_section(rows, savings)` | None | Savings progress section. | savings_total, year_summary_growth, year_summary_employer_total | n/a | n/a | year | jan1/dec31/contrib/employer/growth via _dec at 307-310 | n/a | inherits | _dec at 307-310 |
| 314 | `_add_timeliness_section(rows, pt)` | None | Payment timeliness (avg days before due). | - | n/a | n/a | year | avg_days via _dec at 324 | n/a | inherits | _dec at 324 |
| 330 | `export_variance_csv(report)` | str | 3-level CSV (Group/Item/Transaction) with amounts and percentages. | effective_amount | n/a | n/a | report-defined | estimated/actual/variance via _dec, _pct at 352-368 | n/a | inherits via _dec/_pct | _dec, _pct, _safe, _bool_yn, _write_csv |
| 383 | `export_trends_csv(report)` | str | Metadata header + trend rows. | period_subtotal | n/a | n/a | window | period_average/pct_change/absolute_change/avg_days via _dec/_pct at 413-419; threshold quantize at 395-396 | n/a | Decimal('0.01'), ROUND_HALF_UP at 395-396 (threshold) + inherits | _dec, _pct, _write_csv |

`csv_export_service.py` is the audit's cleanest A-01 example: every monetary cell passes through
`_dec` (line 39) before stringification, and percentages through `_pct` (line 53), both at
`Decimal('0.01')` with ROUND_HALF_UP.

### Cross-cutting: effective_amount bypasses (every site)

| file:line | Function | Context | Phase 3 verdict candidate |
| --- | --- | --- | --- |
| `balance_calculator.py:292` (374-378, 384-385) | `_entry_aware_amount` | Reads entries' `amount` and txn's `estimated_amount` directly to implement the cleared-entry checking-impact formula; intentional per docstring. | AGREE (intentional) -- but Phase 3 must verify the cleared-vs-uncleared partition matches the model's `effective_amount` for non-entry-aware shapes. |
| `dashboard_service.py:239` | `_entry_progress_fields` (via `compute_remaining`) | Reads `estimated_amount` to compute remaining budget against entry sum. | Q-08 (P1-b): should settled transactions use `actual_amount` for over-budget display? |
| `dashboard_service.py:245` | `_entry_progress_fields` | Over-budget compare against `estimated_amount`. | See Q-08. |
| `dashboard_service.py:350` | `_get_balance_info` | Reads `Account.current_anchor_balance` (stored column) for current period. | Phase 4 (source-of-truth audit) covers this column. |
| `savings_dashboard_service.py:373` | `_compute_account_projections` | Reads `proj.current_balance` for ARM (== stored `LoanParams.current_principal` per A-04) or balance-calculator-derived for fixed-rate. | AGREE per A-04 dual policy; Phase 3 must verify all six pages that show principal use the right side for the right loan type. |
| `retirement_dashboard_service.py:405, 441-442` | `_project_retirement_accounts` | Reads `acct.current_anchor_balance` (stored column). | Phase 4 covers. |
| `year_end_summary_service.py:519, 527` | `_compute_entry_breakdowns` SQL aggregates | `func.sum(TransactionEntry.amount)` and conditional credit-entry sum bypass the `Transaction.effective_amount` property. | AGREE (entry-aware envelope sum). |
| `year_end_summary_service.py:1123, 1124, 1244, 1784, 1806, 1861, 2096` | various private helpers (`_project_investment_for_year`, `_compute_interest_for_year`, `_lookup_balance_with_anchor_fallback`, `_compute_pre_anchor_interest`, `_get_account_balance_map`) | Reads `account.current_anchor_balance` (stored column). | Phase 4 covers. |
| `year_end_summary_service.py:1465-1469` | `_balance_from_schedule_at_date` | Anchors loan schedule at `LoanParams.current_principal` for ARM per A-04. | AGREE per A-04. |
| `budget_variance_service.py:390-393` | `_compute_actual` / `_build_txn_variance` | Inlines `actual_amount if status.is_settled and not None else estimated_amount`; hand-rolled `effective_amount` logic. | Phase 3 must compare to `transaction.py:221-245` for exact equivalence under all status/is_deleted/excludes_from_balance combinations. |
| `calendar_service.py:483` | `_compute_month_end_balance` | Reads `account.current_anchor_balance`. | Phase 4 covers. |
| `transaction_service.py:153` | `settle_from_entries` | `compute_actual_from_entries(txn.entries)` sums entries directly. | AGREE (entries are sub-transactional). |
| `carry_forward_service.py:586, 878` | `_build_envelope_plan`, `_settle_source_and_roll_leftover` | `compute_actual_from_entries(source_txn.entries)`. | AGREE per A-02. |
| `credit_workflow.py:229` | `mark_as_credit` | `txn.actual_amount if not None else txn.estimated_amount` to choose payback amount. Hand-rolled mirror of `effective_amount` logic. | Phase 3 must verify the omitted branches (is_deleted, status.excludes_from_balance) cannot apply at this call site. |
| `entry_credit_workflow.py:112-114` | `sync_entry_payback` | `sum((e.amount for e in credit_entries), Decimal("0"))`. | AGREE (entries are sub-transactional). |
| `entry_service.py:70` | `_update_actual_if_paid` | `compute_actual_from_entries(txn.entries)`. | AGREE. |
| `entry_service.py:365, 367, 395, 397-399, 424, 446` | entry-sum helpers | All read `entry.amount` directly. | AGREE (entries are sub-transactional). |
| `investment_projection.py:153, 187` | `calculate_investment_inputs` | Reads `t.estimated_amount` directly for shadow income contributions. Justified by upstream `status.excludes_from_balance` filter at 150. | Phase 3 must verify the upstream-filter contract is always honored by callers. |

Total: 25 effective_amount bypass sites across 12 files. All are intentional reads with documented
justifications; none are read paths that compute a checking-account balance from transaction data,
which would be a true violation of the `effective_amount` contract.

### Cross-cutting: missing quantization (per A-01)

NO display-or-storage-facing function returns money without quantization to `Decimal('0.01')`,
ROUND_HALF_UP. The handful of helpers that return raw Decimal sums (`compute_entry_sums`,
`compute_remaining`, `compute_actual_from_entries`, `_entry_aware_amount`'s `max()` result) feed
into either (a) a column at `Numeric(12, 2)` precision (so storage rounds), (b) a calculator that
quantizes (e.g., `paycheck_calculator.calculate_paycheck`), or (c) `csv_export_service._dec` at the
display boundary. The `entry_service` chain is the most-bypassed surface but is feeding
column-precision storage at every consumer site reviewed.

### Cross-cutting: Flask boundary violations

NONE. Zero `from flask import`, `current_app`, `request`, or `session` references in any service
file in scope across all 36 files. The architecture rule (CLAUDE.md "Services are isolated from
Flask") holds for the financial calculation surface.

The earlier grep at the top of this session matched `request`, `session`, or `current_app` as
substrings in 25 files, but those matches are all in (a) docstrings, (b) variable names like
`previous_period.start_date.year`, or (c) unrelated identifiers. No actual Flask object is imported
or accessed by any service.

### Cross-cutting: transfer-model reads

The legitimate consumers of the `Transfer` model in scope:

- `transfer_service.py` (CRUD, the only authorized mutator).
- `transfer_recurrence.py` (template-driven creation and conflict resolution, all delegated to
  `transfer_service`).
- `carry_forward_service.py` (queries the partition list and delegates move to
  `transfer_service.update_transfer`).
- `year_end_summary_service.py:658` (`_compute_transfers_summary`: display aggregation for the
  year-end transfers tab). LEGITIMATE -- not a balance computation; Phase 3 should still record and
  classify.

Critically: `balance_calculator.py` does NOT query `budget.transfers`; verified by grep -- no
`Transfer.query`, no `from app.models.transfer`, no `db.session.query(Transfer)`. Transfer Invariant
5 (which scopes to balance computation) is satisfied.

Phase 3 follow-up: run the explicit grep
`grep -rn "Transfer.query\|db.session.query(Transfer)\|from app.models.transfer" app/` to find any
additional Transfer reads in routes or other layers; P1-b's grep was confined to `app/services/`.

### Cross-cutting: shadow-transaction mutations outside transfer_service

NONE. All shadow-row writes route through `transfer_service.create_transfer` / `update_transfer` /
`delete_transfer` / `restore_transfer`. Refused at:

- `recurrence_engine.py:412` (`resolve_conflicts` rejects `transfer_id IS NOT NULL`).
- `transaction_service.py:111` (`settle_from_entries` rejects transfer shadows).
- `entry_service.py:158` (`create_entry` rejects transfer shadows).
- `credit_workflow.py:169` (`mark_as_credit` rejects transfer shadows).

Transfer Invariant 4 is satisfied across the service surface.

### Citation-quality note (P1-b self-review, 2026-05-15)

The audit plan section 10.8 lists "trust-then-verify gap" as a known failure pattern; the inventory
itself can suffer the same problem the audit is trying to surface in the codebase. Section 1.1 was
produced by three Explore subagents reading 36 files; after writing, the main session spot-checked
~15 file:line citations and found multiple errors in `year_end_summary_service.py` and
`savings_dashboard_service.py` (the two largest files). Citations have been corrected for:

- `savings_dashboard_service.py` `_compute_debt_summary`: was cited at
  line 700; actual line 802 (monthly_payment at 846, quantize at 851
  and 873). Fixed in the Group B table above.
- `year_end_summary_service.py` `_compute_savings_progress`,
  `_compute_net_worth`, `_compute_interest_for_year`: cited
  `current_anchor_balance` reads at 779, 952, 1003, 1220; actual
  reads are inside helpers `_project_investment_for_year` (1123, 1124),
  `_compute_interest_for_year` (1244), `_lookup_balance_with_anchor_fallback`
  (1784, 1806), `_compute_pre_anchor_interest` (1861), and
  `_get_account_balance_map` (2096). Fixed in the Group B table and
  cross-cutting bypass table above.
- `year_end_summary_service.py` `_compute_transfers_summary`: cited as
  reading `effective_amount at 680` with `settled` and `income` filters;
  actual code queries `Transfer` model directly (line 658) and sums
  `Transfer.amount` (line 679); status filter is `excluded_ids` (lines
  655, 665), NO transaction-type filter. The function reads
  `budget.transfers` -- the file's "Reads `budget.transfers`?" header
  has been corrected and the cross-cutting transfer-reads table now
  lists this site. Phase 3 classification: LEGITIMATE display
  aggregation, not balance computation.
- `year_end_summary_service.py` `_compute_spending_by_category`: cited
  status/type/period filters and `TransactionEntry.amount at 519`;
  actual function calls `_query_settled_expenses` at line 445 and sums
  `txn.effective_amount` at line 457. The cited filter set belongs to
  `_compute_entry_breakdowns` (the next function down), not this one.
  Fixed in the Group B table above.
- `year_end_summary_service.py` `_compute_payment_timeliness`: cited
  quantize at line 1330; actual line 1319. Fixed.

**Verified citations** (spot-checked against `grep`/`Read` in P1-b's self-review):

- `amortization_engine.py:128, 178, 326, 649, 753, 864` (function
  definitions); `:436, 440, 491, 512, 693, 697, 952, 957`
  (`calculate_monthly_payment` call sites); `:977-985` (A-04 dual policy
  branch).
- `balance_calculator.py:35, 112, 176, 292, 389, 422` (function
  definitions); `:225, 231` (engine calls).
- `dashboard_service.py:40, 99, 167, 203, 252, 334, 375` (function
  definitions); `:239, 245` (`estimated_amount` reads); `:350`
  (`current_anchor_balance` read).
- `year_end_summary_service.py:66, 90, 188, 246, 310, 337, 380, 414,
  475, 577, 611, 636, 689, 750, 790, 824, 887, 986, 1027, 1173, 1207,
  1263, 1332, 1368, 1381, 1388, 1421, 1490, 1524, 1567, 1717, 1731,
  1752, 1811, 1876, 1909, 1938, 1986, 2019, 2028, 2036, 2131, 2157,
  2169, 2181, 2206, 2223, 2237` (all 48 function definitions);
  `:380-408` (A-06 `_compute_mortgage_interest`); `:518-528` (the two
  money SQL aggregates per P1-a section 1.6); `:1462-1469` (A-04 ARM
  schedule anchor); `:1244` (`_compute_interest_for_year`
  `current_anchor_balance` read).
- `transfer_service.py:283, 443, 616, 688` (the four public functions
  per Transfer Invariant 4).
- `carry_forward_service.py:210, 273-277 (three-branch partition), 291,
  482, 563, 602, 757, 772, 788, 891-894 (target canonical bump per
  A-02), 899`.
- `recurrence_engine.py:128 (A-03 is_override skip), 412 (transfer
  shadow guard)`.
- `transaction_service.py:38 (settle_from_entries per A-02), 153
  (entries sum)`.
- `credit_workflow.py:229` (`actual_amount`/`estimated_amount` fallback).
- `entry_service.py:47, 73, 109, 211, 272, 318, 348, 371, 405, 428,
  449, 471, 546` (all 13 function definitions).
- `csv_export_service.py:39, 53` (quantize sites).
- `savings_dashboard_service.py:61, 201, 294, 802 (corrected from 700)`
  (function definitions); `:335, 343, 362, 373` (engine calls and ARM
  current_balance per A-04); `:325, 921` (`current_anchor_balance`
  reads).

**Unverified citations** (relied on agent output without independent spot-check). Phase 3 should
re-verify any cell whose verdict turns on the exact line:

- Most internal call-site lines in `dashboard_service`, `calendar_service`,
  `budget_variance_service`, `spending_trend_service`,
  `debt_strategy_service`, `savings_goal_service`, `companion_service`
  (Group B's smaller files).
- Most internal call-site lines in the calculation-engine files (Group A:
  `growth_engine`, `interest_projection`, `tax_calculator`,
  `paycheck_calculator`, `loan_payment_service`, `escrow_calculator`,
  `pension_calculator`, `retirement_gap_calculator`,
  `investment_projection`, `calibration_service`, `tax_config_service`).
- Most internal call-site lines in the workflow files (Group C:
  `transfer_service` body, `carry_forward_service` body,
  `recurrence_engine` body, `transfer_recurrence`, `credit_workflow`,
  `entry_credit_workflow`, `entry_service`, `_recurrence_common`,
  `state_machine`, `pay_period_service`, `account_resolver`,
  `scenario_resolver`, `csv_export_service` body beyond the two
  formatter functions).

**Reliability rating**: function-definition line numbers (from `^def`/`^class` greps) are reliable
everywhere. Function-body citations were spot-verified at the load-bearing sites (A-02 through A-07
anchor points, the two money SQL aggregates, the transfer-shadow guards, the quantization helpers,
the eight ARM `calculate_monthly_payment` A-05 sites and the six additional sites the audit found).
Body citations in the largest file (`year_end_summary_service.py`, 2248 lines) had a ~20% error rate
against spot-checks and have been corrected here; Phase 3 should treat any year_end_summary_service
citation NOT in the "verified" list as a hypothesis to test, not a fact to rely on.

## 1.2 Route layer

23 route files under `app/routes/` (`__init__.py` plus 22 blueprints), 13,930 LOC total. Three
Explore subagents ran in parallel, very thorough, partitioned by domain (grid/transactional /
account-and-loan / aggregation-and-analytics). Each subagent read every file in scope IN FULL
(segmented for files over 800 lines) and was given verbatim accuracy clauses that required
Read-confirmation of each cited line before emission. The main session sampled 15 rows per group (8+
from files over 800 LOC each) and verified each by Read with a +/-5 line window.

### Out of scope per audit plan section 0.6

Files that do not prepare a financial figure for rendering. Listed for exhaustiveness; their
handlers are not inventoried.

| File | Lines | Reason |
| ---- | ----- | ------ |
| `app/routes/__init__.py` | 6 | Package init with module docstring only. |
| `app/routes/auth.py` | 1339 | Authentication, MFA, password reset; no financial figures. |
| `app/routes/categories.py` | 205 | Category CRUD (group / item names); no financial figures. |
| `app/routes/charts.py` | 22 | Permanent 301 redirect stub for /charts -> /analytics (`charts.py:18-22`). |
| `app/routes/health.py` | 54 | Health endpoint for the readiness probe; no financial figures. |
| `app/routes/pay_periods.py` | 54 | Pay-period table generation (date rows); no money rendered. |
| `app/routes/settings.py` | 559 | Settings + tax-config CRUD; renders stored configuration values, not computed money. |

In scope: 16 files, 11,691 LOC, organized into three groups below.

### 1.2.0 Quality control log

Per the verification protocol, each group's Explore return was sampled at 15 rows (8+ from files
over 800 LOC). Each sampled row was re-read with `Read` at the cited line (+/-5 line window) and
confirmed against four failure categories: (i) off-by-N line number outside the +/-2 tolerance, (ii)
hallucinated function or call, (iii) behavior misdescription (paraphrased docstring), (iv) wrong
file path.

| Group | Files in scope | Total rows | Sampled | Failures | Action | Notes |
| ----- | -------------- | ---------- | ------- | -------- | ------ | ----- |
| A     | grid.py, transactions.py, transfers.py, entries.py, companion.py | 37 | 15 (8 from >800 LOC: transactions.py + transfers.py) | 0 within tolerance | accepted | Three rows with `Inline=YES/NO` classification disagreements were corrected inline before append (see below); not citation failures. |
| B     | accounts.py, loan.py, retirement.py, investment.py, debt_strategy.py | 36 | 15 (11 from >800 LOC: accounts.py + loan.py + investment.py) | 0 within tolerance | accepted | One off-by-1 line citation at `retirement.py:50` (claimed call site for `compute_gap_data`; actual at `retirement.py:51`); within +/-2 tolerance. |
| C     | dashboard.py, savings.py, analytics.py, obligations.py, salary.py, templates.py | 41 | 15 (9 from >800 LOC: salary.py) | 0 within tolerance | accepted | Two off-by-1 decorator-line citations at `salary.py:101` (actual 102), `salary.py:148` (actual 149), `templates.py:162` (actual 163); within +/-2 tolerance. |

Inline classification corrections applied during QC (Group A):

- `grid.py:164` (`index`): Group A return marked `Inline=NO`; the QC
  re-read found inline Decimal arithmetic at lines 263-279 (subtotal loop
  `income += txn.effective_amount`, `expense += txn.effective_amount`,
  `net = income - expense`). The Group A return correctly raised the issue
  as ambiguity Q-002 in its own footer but the table cell was inconsistent.
  Corrected to `Inline=YES (subtotals at 263-279)` in the table below; the
  ambiguity is folded into Q-10 in `09_open_questions.md`.
- `transactions.py:909` (`create_inline`) and `transactions.py:987`
  (`create_transaction`): Group A return marked `Inline=YES` citing a
  "Decimal assignment" at lines 972 / 1017. The QC re-read found those
  lines are `txn = Transaction(**data)` where `data` comes from
  `_inline_create_schema.load(request.form)` (Marshmallow handles Decimal
  parsing). No inline arithmetic happens at the construction site;
  corrected to `Inline=NO` in the table below.

Systematic-error check: the off-by-1 citations in Group B (`retirement.py`) and Group C (`salary.py`
decorators, `templates.py` decorator) are all shifted by +1 line in the same direction (Explore line
numbers run 1 short of the actual line). The pattern is small enough (3 of 45 samples) to remain
inside the protocol's +/-2 tolerance, and is consistent with agents counting from the function body
rather than the decorator. Phase 3 should treat all route-layer citations as ranges
`[cited-1, cited+2]` when grepping rather than absolute line anchors. The same pattern was
previously flagged in `year_end_summary_service.py` (P1-b's "Citation-quality note"); the
route-layer pass shows it concentrates in files over 800 LOC. P1-b's largest service file (2248
lines) had a ~20% spot-check error rate; the route layer's 0% within-tolerance rate indicates the
verbatim accuracy clauses and per-file segmentation worked as intended for P1-c.

Out-of-scope misclassification spot-check (Group C `dashboard.py:54-139` `mark_paid`): the Group C
return marked this handler out-of-scope because "it updates status/amount in DB and returns a
partial row." The QC notes the response IS an updated bill-row partial that re-renders financial
figures; per the audit-plan rule "in scope only if its response renders financial figures," this
handler IS in scope at the source level. Added to Group C inventory below under `dashboard.py`; not
a citation failure but a scope misclassification.

### Group A: grid and transactional routes

Five files, 3,544 LOC. Two large files: `transactions.py` (1182) and `transfers.py` (1320).
Cross-references to section 1.1 service entries are by name only -- the service file:line for each
call appears in the "Service calls" cell.

#### `app/routes/grid.py` (467 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 164 | GET | `index` | Transaction multi-filter at 226-234 (selectinload entries+template); Account / Category / PayPeriod via service helpers | `get_baseline_scenario` @ 177; `resolve_grid_account` @ 182; `pay_period_service.get_current_period` @ 197; `pay_period_service.get_periods_in_range` @ 205; `pay_period_service.get_all_periods` @ 210; `balance_calculator.calculate_balances` @ 243-248; `build_entry_sums_dict` @ 258 | `balances`: `checking_balance`/`projected_end_balance` (per-period dict @ 243); `subtotals`: `period_subtotal` dict income/expense/net @ 264-279; `anchor_balance`: `account_balance` @ 238; `entry_sums`: `entry_sum_total` dict @ 258; `current_period`: PayPeriod; `periods`: PayPeriod list; `account`: Account; `categories`: Category list | `grid/grid.html` | full-page render | YES (subtotal loop at 263-279) |
| 393 | GET | `balance_row` | Transaction multi-filter same as index | `balance_calculator.calculate_balances` @ 446-451; `pay_period_service.get_current_period` @ 420; `pay_period_service.get_periods_in_range` @ 425; `pay_period_service.get_all_periods` @ 426 | `balances`: `chart_balance_series` @ 446; `periods`: PayPeriod list; `account`: Account; `num_periods`, `start_offset`: int; `low_balance_threshold`: int | `grid/_balance_row.html` | `#balanceRow` (HTMX-targeted in caller; partial responds OOB) | NO |

Out-of-scope handlers in `grid.py`:

- `create_baseline` (POST around 364): bootstraps scenario rows then
  redirects; no financial figure in response body.

#### `app/routes/transactions.py` (1182 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 244 | GET | `get_cell` | Transaction via `_get_owned_transaction` (helper) | `build_entry_sums_dict` @ 88 | `txn`: Transaction; `entry_sums`: `entry_sum_total` | `grid/_transaction_cell.html` | `#txn-cell-{id}` | NO |
| 266 | GET | `get_full_edit` | Transaction via helper; Status @ 300; Category @ 286-290; Transfer @ 282 | -- | `txn`: Transaction; `xfer`: Transfer (optional); `statuses`: list; `categories`: list | `grid/_transaction_full_edit.html` OR `transfers/_transfer_full_edit.html` | `#txn-popover` | NO |
| 304 | PATCH | `update_transaction` | Transaction via helper; Status @ 398 | `transfer_service.update_transfer` @ 409-411 (shadow branch); `transaction_service.settle_from_entries` @ 596 (envelope branch); state-machine `verify_transition` @ 441, 607 | `txn`: Transaction (rendered cell) | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `balanceChanged` | NO |
| 491 | POST | `mark_done` | Transaction via helper; Status @ 535, 537 | `transfer_service.update_transfer` @ 553-555 (shadow branch); `transaction_service.settle_from_entries` @ 596 (envelope branch with entries); `verify_transition` @ 607 | `txn`: Transaction; sets `actual_amount` from form `MarkDoneSchema` (`effective_amount`) | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 632 | POST | `mark_credit` | Transaction via helper | `credit_workflow.mark_as_credit` @ 658 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 675 | DELETE | `unmark_credit` | Transaction via helper | `credit_workflow.unmark_credit` @ 692 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 713 | POST | `cancel_transaction` | Transaction via helper; Status @ 728 | `transfer_service.update_transfer` @ 733-736 (shadow); `verify_transition` @ 764 | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-cell-{id}`; trigger `gridRefresh` | NO |
| 783 | GET | `get_quick_create` | Category @ 799; PayPeriod @ 800; Account @ 801; Scenario @ 813 | `get_baseline_scenario` @ 813 | `category`: Category; `period`: PayPeriod; `account_id`/`scenario_id`/`transaction_type_id`: int | `grid/_transaction_quick_create.html` | `#txn-form-{cell}` | NO |
| 828 | GET | `get_full_create` | Category @ 844; PayPeriod @ 845; Account @ 846; Status @ 859; Scenario @ 855 | `get_baseline_scenario` @ 855 | `category`, `period`, `account_id`, `scenario_id`, `transaction_type_id`, `statuses` | `grid/_transaction_full_create.html` | `#txn-popover` | NO |
| 909 | POST | `create_inline` | Account @ 946; Category @ 951; PayPeriod @ 956; Scenario @ 961 | -- (validated Decimal from `_inline_create_schema.load` @ 943; model construction at 972) | `txn`: Transaction; HTMX-wrapped cell | `grid/_transaction_cell.html` | `#txn-row-{new-id}`; trigger `balanceChanged` | NO |
| 987 | POST | `create_transaction` | Account @ 999; PayPeriod @ 1004; Scenario @ 1009 | -- (validated Decimal from `_create_schema.load` @ 996; model construction at 1017) | `txn`: Transaction | `grid/_transaction_cell.html` | `#txn-popover`; trigger `balanceChanged` | NO |
| 1030 | DELETE | `delete_transaction` | Transaction via helper; soft-delete vs hard at 1053 | -- | -- (empty response) | -- | -- | NO |
| 1107 | GET | `carry_forward_preview` | PayPeriod / Scenario via `_resolve_carry_forward_context` @ 1134 | `pay_period_service.get_current_period` (in helper); `carry_forward_service.preview_carry_forward` @ 1140-1142 | `preview`: `CarryForwardPreview`; `source_period`/`current_period`: PayPeriod | `grid/_carry_forward_preview_modal.html` | `#carry-forward-modal` | NO |
| 1154 | POST | `carry_forward` | -- (delegates) | `carry_forward_service.carry_forward_unpaid` @ 1165-1167 | -- (empty 200 response) | -- | -- | NO |

Out-of-scope handlers in `transactions.py`:

- `get_quick_edit` (255): form retrieval only, no financial figure
  computed (the inline-amount input renders the existing value).
- `get_empty_cell` (872): placeholder rendering for an empty grid cell.

#### `app/routes/transfers.py` (1320 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 65 | GET | `list_transfer_templates` | TransferTemplate @ 74-79 | -- | `active_templates`, `archived_templates`: TransferTemplate lists | `transfers/list.html` | full-page | NO |
| 89 | GET | `new_transfer_template` | Account @ 94-99; Category @ 100-104; RecurrencePattern @ 106 | `pay_period_service.get_all_periods` @ 107; `pay_period_service.get_current_period` @ 108 | accounts, categories, patterns, periods, current_period, prefill_from / prefill_to | `transfers/form.html` | full-page | NO |
| 127 | POST | `create_transfer_template` | Account / Category / PayPeriod via `_user_owns` helper; RecurrencePattern @ 176 | `transfer_service.create_transfer` @ 239-250 (one-time branch); `transfer_recurrence.generate_for_template` @ 260-262 (recurring branch); `get_baseline_scenario` @ 235, 257; `pay_period_service.get_all_periods` @ 259 | `template`: TransferTemplate (redirect after success; re-renders form with errors otherwise) | `transfers/form.html` on error | -- | NO |
| 269 | GET | `edit_transfer_template` | TransferTemplate @ 274; Account @ 278-283; Category @ 284-289; RecurrencePattern @ 290 | -- | `template`, `accounts`, `categories`, `patterns`, `periods=[]`, `current_period=None` | `transfers/form.html` | full-page | NO |
| 303 | POST | `update_transfer_template` | TransferTemplate @ 318; RecurrencePattern @ 353; RecurrenceRule @ 357-376 | `transfer_recurrence.regenerate_for_template` @ 428-430; `get_baseline_scenario` @ 424; `pay_period_service.get_all_periods` @ 426 | -- (redirect) | -- | -- | NO |
| 466 | POST | `archive_transfer_template` | TransferTemplate @ 482; Transfer @ 490-498 | `transfer_service.delete_transfer` @ 502 (soft=True, per projected transfer) | -- (redirect) | -- | -- | NO |
| 527 | POST | `unarchive_transfer_template` | TransferTemplate @ 537; Transfer @ 545-553 | `transfer_service.restore_transfer` @ 558; `transfer_recurrence.generate_for_template` @ 566-568; `get_baseline_scenario` @ 563; `pay_period_service.get_all_periods` @ 565 | -- (redirect) | -- | -- | NO |
| 592 | POST | `hard_delete_transfer_template` | TransferTemplate @ 620; Transfer @ 667-671 | `transfer_service.delete_transfer` @ 645 (soft=True for projected) and @ 673 (soft=False for the rest) | -- (redirect) | -- | -- | NO |
| 698 | GET | `get_cell` | Transfer via `_get_owned_transfer` @ 703 | `account_resolver.resolve_grid_account` @ 706 | `xfer`: Transfer; `account`: Account | `transfers/_transfer_cell.html` | `#xfer-cell-{id}` | NO |
| 723 | GET | `get_full_edit` | Transfer via helper; Status @ 731; Category @ 732-737 | -- | `xfer`, `statuses`, `categories` | `transfers/_transfer_full_edit.html` | `#xfer-popover` | NO |
| 744 | PATCH | `update_transfer` | Transfer via helper; Category ownership @ 804-806 | `transfer_service.update_transfer` @ 814; `account_resolver.resolve_grid_account` @ 843 (for the response render path) | `xfer`: Transfer (or shadow `Transaction` via `_resolve_shadow_context` @ 833) | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `balanceChanged` | NO |
| 850 | POST | `create_ad_hoc` | Account / PayPeriod / Scenario / Category via `_user_owns` (901-908) | `transfer_service.create_transfer` @ 920-931; `account_resolver.resolve_grid_account` @ 951 | `xfer`: Transfer; `account`: Account | `transfers/_transfer_cell.html` | `#xfer-row-{new-id}`; trigger `balanceChanged` | NO |
| 1020 | DELETE | `delete_transfer` | Transfer via helper; soft check @ 1039 | `transfer_service.delete_transfer` @ 1041 | -- (empty response) | -- | -- | NO |
| 1055 | POST | `mark_done` | Transfer via helper; Status @ 1071 | `transfer_service.update_transfer` @ 1084-1087 (sets `status_id=done`, `paid_at=now()`) | `xfer`: Transfer (or shadow `Transaction`); `account` on the non-shadow path | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `gridRefresh`/`balanceChanged` | NO |
| 1118 | POST | `cancel_transfer` | Transfer via helper; Status @ 1130 | `transfer_service.update_transfer` @ 1132-1134 | `xfer`: Transfer (or shadow `Transaction`); `account` on the non-shadow path | `grid/_transaction_cell.html` (shadow) OR `transfers/_transfer_cell.html` | `#txn-cell-{id}` (shadow) or `#xfer-cell-{id}`; trigger `gridRefresh` | NO |

Out-of-scope handlers in `transfers.py`:

- `get_quick_edit` (712): form retrieval only.

#### `app/routes/entries.py` (414 lines)

`_render_entry_list` (helper at 83-120) is the shared rendering path called by every entry route
below; the helper's service calls are listed once here and not repeated per row.

Helper service calls (`_render_entry_list`):

- `entry_service.get_entries_for_transaction` @ 101-103
- `entry_service.compute_remaining` @ 104-106 (produces `entry_remaining`)
- `entry_service.check_entry_date_in_period` @ 107-110

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 200 | GET | `list_entries` | Transaction via `_get_accessible_transaction` @ 208 | helper service calls above | `txn`: Transaction; `entries`: list; `remaining`: `entry_remaining`; `today`: ISO date; `editing_id`: int; `out_of_period_ids`: set; `conflict`: bool | `grid/_transaction_entries.html` | `#txn-entries-{id}` | NO |
| 215 | POST | `create_entry` | Transaction via helper @ 225 | `entry_service.create_entry` @ 235-239; helper service calls on render | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 255 | PATCH | `update_entry` | Transaction via helper; TransactionEntry @ 281; ownership @ 282 | `entry_service.update_entry` @ 302; helper service calls on render | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 323 | PATCH | `toggle_cleared` | Transaction via helper; TransactionEntry @ 351; ownership @ 352 | `entry_service.toggle_cleared` @ 356; helper service calls | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |
| 371 | DELETE | `delete_entry` | Transaction via helper; TransactionEntry @ 389; ownership @ 390 | `entry_service.delete_entry` @ 394; helper service calls | as `list_entries` | `grid/_transaction_entries.html` | `#txn-entries-{id}`; trigger `balanceChanged` | NO |

#### `app/routes/companion.py` (161 lines)

`_build_entry_data` (helper at 28-64) is the shared per-transaction dict assembler used by both
companion handlers below; its inline-compute classification applies to each row.

Helper inline-compute notes (`_build_entry_data`):

- Calls `entry_service.compute_entry_sums` and `entry_service.compute_remaining` for each
  transaction (lines 50-53).
- Computes `pct = float(total / txn.estimated_amount * Decimal("100"))` inline at lines 53-56; this
  is `goal_progress` derived in the route from service outputs.

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 80 | GET | `index` | -- (service-loaded) | `companion_service.get_visible_transactions` @ 95-97; `_build_entry_data` @ 109; `companion_service.get_previous_period` @ 111; `pay_period_service.get_next_period` @ 112 | `transactions`: Transaction list; `period`, `prev_period`, `next_period`: PayPeriod; `entry_data`: dict mapping `txn.id` -> `{total: entry_sum_total, remaining: entry_remaining, count: int, pct: goal_progress}` (assembled in helper) | `companion/index.html` | full-page | YES (`pct` at lines 53-56 in helper) |
| 124 | GET | `period_view` | -- (service-loaded) | `companion_service.get_visible_transactions` @ 142-145; `_build_entry_data` @ 149; `companion_service.get_previous_period` @ 151; `pay_period_service.get_next_period` @ 152 | as `index` plus `period_id` route arg | `companion/index.html` | full-page | YES (same helper) |

### Group B: account management and loan routes

Five files, 4,492 LOC. Three large files: `accounts.py` (1468), `loan.py` (1295), `investment.py`
(804).

#### `app/routes/accounts.py` (1468 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 229 | GET | `list_accounts` | Account @ 243-260 | -- | `active_accounts`, `archived_accounts`: lists; `account_types`: list; `types_in_use`: set | `accounts/list.html` | full-page | NO |
| 288 | POST | `create_account` | Account name uniqueness @ 313-315; InterestParams @ 339; InvestmentParams @ 349 | `pay_period_service.get_current_period` @ 323 | -- (redirect) | `accounts/form.html` on error | -- | NO |
| 398 | POST | `update_account` | Account @ 431; name uniqueness @ 182-185 | `_validate_update_account` @ 440; `pay_period_service.get_current_period` @ 458; `entry_service.clear_entries_for_anchor_true_up` @ 484 | -- (redirect) | `accounts/form.html` on error | -- | NO |
| 503 | POST | `archive_account` | TransferTemplate @ 522-532 | -- (direct mutation of `is_active`) | -- (redirect) | -- | -- | NO |
| 592 | POST | `hard_delete_account` | TransferTemplate @ 628-637; TransactionTemplate @ 649-651; Transfer @ 690-696; Transaction @ 703-705; LoanParams / InterestParams / InvestmentParams / EscrowComponent / RateHistory / SavingsGoal @ 711-716 | `archive_helpers.account_has_history` @ 663; `transfer_service.delete_transfer` @ 699 per transfer | -- (redirect) | -- | -- | NO |
| 747 | PATCH | `inline_anchor_update` | Account @ 765; AccountAnchorHistory insert | `pay_period_service.get_current_period` @ 791; `entry_service.clear_entries_for_anchor_true_up` @ 811 | `acct`: Account (with updated `current_anchor_balance`) | `accounts/_anchor_cell.html` | partial swap into anchor cell | YES (`new_balance = Decimal(str(data["anchor_balance"]))` @ 774) |
| 860 | GET | `inline_anchor_form` | Account @ 865 | -- | `acct`: Account; `editing=True` | `accounts/_anchor_cell.html` | partial | NO |
| 874 | GET | `inline_anchor_display` | Account @ 879 | -- | `acct`: Account; `editing=False` | `accounts/_anchor_cell.html` | partial | NO |
| 1053 | PATCH | `true_up` | Account @ 1074; AccountAnchorHistory insert @ 1110-1115; PayPeriod via service @ 1101 | `pay_period_service.get_current_period` @ 1101; `entry_service.clear_entries_for_anchor_true_up` @ 1137 | `account`: Account; `editing=False`; OOB `anchor-as-of` HTML built inline @ 1190-1194 | `grid/_anchor_edit.html` | partial; `hx-swap-oob` for `#anchor-as-of`; trigger `balanceChanged` | YES (`new_balance = Decimal(str(data["anchor_balance"]))` @ 1083) |
| 1198 | GET | `anchor_form` | Account @ 1203 | -- | `account`: Account; `editing=True` | `grid/_anchor_edit.html` | partial | NO |
| 1214 | GET | `anchor_display` | Account @ 1219 | -- | `account`: Account; `editing=False` | `grid/_anchor_edit.html` | partial | NO |
| 1233 | GET | `interest_detail` | Account @ 1238; InterestParams @ 1247-1250; Transaction @ 1272-1281 | `pay_period_service.get_all_periods` @ 1259; `pay_period_service.get_current_period` @ 1260; `get_baseline_scenario` @ 1262; `balance_calculator.calculate_balances_with_interest` @ 1291 | `account`: Account; `params`: InterestParams; `current_balance`: `account_balance` @ 1299; `projected`: `projected_end_balance` dict for 3/6/12-month horizons; `period_data`: list of `{period, balance, interest}` (`account_balance` + `apy_interest`) @ 1302-1309; `anchor_period`: PayPeriod | `accounts/interest_detail.html` | full-page | NO |
| 1331 | POST | `update_interest_params` | Account @ 1336; InterestParams @ 1351-1358 | -- | -- (redirect) | -- | -- | NO |
| 1376 | GET | `checking_detail` | Account @ 1387; Transaction @ 1407-1416 | `pay_period_service.get_all_periods` @ 1397; `pay_period_service.get_current_period` @ 1398; `get_baseline_scenario` @ 1400; `balance_calculator.calculate_balances` @ 1425 | `account`, `current_balance`: `account_balance` @ 1432; `projected`: `projected_end_balance` for 3/6/12-month horizons; `period_data`: list of `{period, balance}` | `accounts/checking_detail.html` | full-page | NO |

Out-of-scope handlers in `accounts.py`:

- `new_account` (272), `edit_account` (382): form display only, no
  financial computation.
- `unarchive_account` (561): mutates state then redirects.
- `create_account_type` (891), `update_account_type` (944),
  `delete_account_type` (1003): ref-table CRUD, no money.

#### `app/routes/loan.py` (1295 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 405 | GET | `dashboard` | Account / LoanParams via `_load_loan_account` @ 410; EscrowComponent via `_load_loan_context`; TransferTemplate @ 496-505; Account+AccountType join @ 522-532 | `_load_loan_context` @ 421; `amortization_engine.get_loan_projection` @ 429-431; `escrow_calculator.calculate_total_payment` @ 433-435; `_compute_payment_breakdown` @ 438-440; `amortization_engine.generate_schedule` @ 453-459, @ 480-... (chart); `_update_transfer_end_date` @ 514 | `account`, `params`: LoanParams; `summary`: `AmortizationSummary` (`monthly_payment`, `total_interest`, `payoff_date`); `monthly_escrow`: `escrow_per_period`; `total_payment`: `monthly_payment`; `payment_breakdown`: `{principal, interest, escrow}` (`principal_paid_per_period` + `interest_paid_per_period` + `escrow_per_period`); `chart_labels`/`chart_original`/`chart_committed`/`chart_floor`: `chart_balance_series`; `amortization_schedule`: list of AmortizationRow; `show_transfer_prompt`: bool | `loan/dashboard.html` | full-page | NO |
| 578 | POST | `create_params` | Account @ 583; LoanParams @ 593 | -- | -- (redirect) | `loan/setup.html` on error | -- | NO |
| 631 | POST | `update_params` | Account / LoanParams via `_load_loan_account` @ 636 | -- | -- (redirect) | -- | -- | NO |
| 682 | POST | `add_rate_change` | Account via `_load_loan_account` @ 687; RateHistory insert @ 700-705; LoanParams update @ 709; RateHistory list @ 732-736 | -- | `account`, `params`, `rate_history`: RateHistory list | `loan/_rate_history.html` | `#rate-history-list` partial | NO |
| 761 | POST | `add_escrow` | Account via helper @ 766; EscrowComponent name uniqueness @ 781-785; EscrowComponent active list @ 795-800 | `escrow_calculator.calculate_monthly_escrow` @ 803; `_compute_total_payment` @ 804 | `account`, `escrow_components`: list; `monthly_escrow`: `escrow_per_period`; `total_payment`: `monthly_payment` | `loan/_escrow_list.html` | `#escrow-list` partial | NO |
| 815 | POST | `delete_escrow` | Account via helper @ 823; EscrowComponent @ 827-829; LoanParams @ 843-847; EscrowComponent active list @ 835-840 | `escrow_calculator.calculate_monthly_escrow` @ 848; `_compute_total_payment` @ 849 | as `add_escrow` | `loan/_escrow_list.html` | `#escrow-list` partial | NO |
| 860 | POST | `payoff_calculate` | Account / LoanParams via helper @ 865 | `_load_loan_context` @ 882; `amortization_engine.calculate_summary` @ 900-... (extra_payment branch); `amortization_engine.generate_schedule` @ 917, 925, 937 (chart inputs); `amortization_engine.calculate_payoff_by_date` @ 1000 (target_date branch); `compute_contractual_pi` @ 1012 | `payoff_summary`: AmortizationSummary; `chart_labels`/`chart_original`/`chart_committed`/`chart_accelerated`: `chart_balance_series`; `committed_months_saved`: `months_saved`; `committed_interest_saved`: `interest_saved`; `required_extra`: Decimal (target_date branch) | `loan/_payoff_results.html` | `#payoff-results` partial | YES (anchor_bal at 893-895 sets ARM anchor inline; `extra = Decimal(str(data.get("extra_monthly", "0")))` at 899) |
| 1027 | POST | `refinance_calculate` | Account / LoanParams via helper @ 1047 | `_load_loan_context` @ 1063; `amortization_engine.get_loan_projection` @ 1069; `amortization_engine.calculate_monthly_payment` @ 1102 (refi monthly); `amortization_engine.generate_schedule` @ 1108 (refi schedule) | `account`, `params`, `comparison`: `{current_monthly: monthly_payment, current_total_interest: total_interest, current_payoff: payoff_date, current_remaining_months: loan_remaining_months, refi_monthly: monthly_payment, refi_total_interest: total_interest, refi_payoff: payoff_date, monthly_savings, interest_savings: interest_saved, break_even_months: months_saved}` | `loan/_refinance_results.html` | `#refinance-results` partial | YES (`refi_principal = current_real_principal + closing_costs` at 1095 when user does not override; `current_real_principal = proj.current_balance` at 1087) |
| 1170 | POST | `create_payment_transfer` | Account / LoanParams via helper @ 1184; Account @ 1201; EscrowComponent @ 1237-1239; RecurrenceRule + TransferTemplate insert; Transfer via service | `amortization_engine.calculate_remaining_months` @ 1222 (ARM branch); `amortization_engine.calculate_monthly_payment` @ 1225 (ARM) and @ 1231 (fixed); `escrow_calculator.calculate_total_payment` @ 1241; `transfer_recurrence.generate_for_template` @ 1280 | -- (redirect) | -- | -- | YES (`transfer_amount = data["amount"]` if provided else `transfer_amount = escrow_calculator.calculate_total_payment(monthly_pi, ...)` at 1241; `monthly_pi` derived inline from service calls at 1222-1235) |

Out-of-scope handlers in `loan.py`:

- `create_params` (578), `update_params` (631): loan-parameter setup
  forms; redirect-only response.

#### `app/routes/retirement.py` (410 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 46 | GET | `dashboard` | -- (service-loaded) | `retirement_dashboard_service.compute_gap_data` @ 51; `retirement_dashboard_service.compute_slider_defaults` @ 52 | `current_swr` (Decimal %), `current_return` (Decimal %); plus all keys from `compute_gap_data` (gap_analysis, chart_data, retirement_account_projections, paycheck_breakdown derivatives) | `retirement/dashboard.html` | full-page | NO |
| 301 | GET | `gap_analysis` | -- (service-loaded) | `retirement_dashboard_service.compute_gap_data` @ 320-324 | `gap_analysis`: dict (includes `paycheck_net`, `pension_benefit_monthly`, `savings_total`, `growth`, `goal_progress` keys); `chart_data`: dict; `retirement_account_projections`: list of `account_balance`/`projected_end_balance` rows | `retirement/_gap_analysis.html` | `#gap-analysis` (HTMX-only; redirects non-HX) | YES (`swr_override = Decimal(str(swr_param)) / Decimal("100")` @ 314; `return_rate_override` @ 318) |

Out-of-scope handlers in `retirement.py`:

- `pension_list` (65): list display of pension profiles; no money.
- `create_pension` (88), `edit_pension` (161), `update_pension` (183),
  `delete_pension` (282): pension CRUD; the pension profile stores
  parameters but the route does not compute / render a benefit value
  on the form pages.
- `update_settings` (338): settings POST then redirect.

#### `app/routes/investment.py` (804 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 63 | GET | `dashboard` | Account @ 68; InvestmentParams @ 72-75; Transaction @ 94-102; SalaryProfile @ 122-125; PaycheckDeduction join @ 134-143; Transaction (shadow income) @ 160-170; TransferTemplate @ 273-281 | `pay_period_service.get_all_periods` @ 78; `pay_period_service.get_current_period` @ 79; `get_baseline_scenario` @ 91; `balance_calculator.calculate_balances` @ 107-112; `calculate_investment_inputs` @ 173-181; `growth_engine.calculate_employer_contribution` @ 187-189; `build_contribution_timeline` @ 193-197; `growth_engine.project_balance` @ 209-218 | `account`, `params`, `current_balance`: `account_balance` @ 115; `periodic_contribution`: Decimal; `employer_contribution_per_period`: `employer_contribution`; `employer_params`: dict; `limit_info`: `{limit: contribution_limit_remaining, ytd: ytd_contributions, pct: int %}`; `projection`: list of ProjectionRow; `chart_labels`: `chart_date_labels`; `chart_balances`: `chart_balance_series`; `chart_contributions`: `chart_balance_series` (cumulative); `default_horizon`: int years; `show_contribution_prompt`, `is_deduction_path`: bool; `source_accounts`: list; `default_source_id`: int; `suggested_amount`: `transfer_amount_computed` | `investment/dashboard.html` | full-page | YES (`salary_gross_biweekly = (Decimal(...) / pp_per_year).quantize(...)` @ 128-131; `chart_balances`/`chart_contributions` loop @ 220-227 quantizes inline) |
| 363 | GET | `growth_chart` | Account @ 386; InvestmentParams @ 390-393; Transaction @ 415-422; SalaryProfile @ 449-452; PaycheckDeduction @ 460-469; Transaction (shadow income) @ 484-494 | `pay_period_service.get_all_periods` @ 408; `pay_period_service.get_current_period` @ 409; `get_baseline_scenario` @ 410; `balance_calculator.calculate_balances` @ 425-430; `growth_engine.generate_projection_periods` @ 436-439; `calculate_investment_inputs` @ 497-505; `build_contribution_timeline` @ 508-512; `growth_engine.project_balance` @ 514-523, @ 567 (what-if branch) | `chart_labels`: `chart_date_labels`; `chart_balances`/`chart_contributions`: `chart_balance_series`; `what_if_balances`: `chart_balance_series` (alt scenario); `what_if_amount`: Decimal; `comparison`: `{committed_end, whatif_end, difference, is_positive, is_zero}` (account_balance pair + Decimal diff) | `investment/_growth_chart.html` | `#growth-chart` (HTMX-only; redirects non-HX) | YES (quantize loop @ 531-538; what-if balance comparison Decimal arithmetic) |
| 609 | POST | `create_contribution_transfer` | Account @ 625; Account @ 641; InvestmentParams @ 662-665; RecurrenceRule + TransferTemplate insert; Transfer via service | `pay_period_service.get_all_periods` @ 712; `get_baseline_scenario` @ 710; `transfer_recurrence.generate_for_template` @ 713 | -- (redirect) | -- | -- | YES (`transfer_amount` derived inline from `params.annual_contribution_limit / 26` or `Decimal("500.00")` fallback at 668-670; this is `transfer_amount_computed`) |

Out-of-scope handlers in `investment.py`:

- `update_params` (734): parameter form POST then redirect.

#### `app/routes/debt_strategy.py` (515 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 275 | GET | `dashboard` | Account+AccountType join via `_load_debt_accounts`; LoanParams via helper | `_load_debt_accounts` @ 286 (internally calls `amortization_engine` to derive each debt's current principal and minimum payment) | `debt_accounts`: list of `DebtAccount` namedtuples (each with `current_principal` -> `loan_principal_real`, `minimum_payment` -> `monthly_payment`); `has_arm`: bool | `debt_strategy/dashboard.html` | full-page | NO |
| 295 | POST | `calculate` | Account+AccountType join via `_load_debt_accounts` @ 343; LoanParams via helper | `_load_debt_accounts` @ 343; `calculate_strategy` @ 362, 366, 370, 384 (baseline / avalanche / snowball / custom); `_build_comparison` @ 396; `_prepare_chart_data` @ 407 | `debt_accounts`: list; `comparison`: `{baseline/avalanche/snowball/custom: {debt_free_date: payoff_date, total_interest, total_paid, total_months, interest_saved, months_saved}}`; `avalanche_result`/`snowball_result`/`baseline_result`/`custom_result`: StrategyResult; `selected_result`: StrategyResult; `selected_strategy`: str; `extra_monthly`: Decimal; `chart_data_json`: JSON-serialized `chart_balance_series` per loan | `debt_strategy/_results.html` | `#debt-strategy-results` partial | NO |

### Group C: aggregation and analytics view routes

Six files, 3,655 LOC. One large file: `salary.py` (1466).

#### `app/routes/dashboard.py` (209 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 40 + 41 | GET | `page` | -- (service-loaded) | `dashboard_service.compute_dashboard_data` @ 50 | `**data` unpacked: `current_period` (PayPeriod), `balance_info` (`checking_balance`/`account_balance`), `balance_runway_days` (`cash_runway_days`), `upcoming_bills` (list with `effective_amount`), `alerts` (list), `savings_goals` (list with `goal_progress`), `debt_obligations` (`monthly_payment` + `dti_ratio` summary), `spending_comparison` (`period_subtotal` deltas) | `dashboard/dashboard.html` | full-page | NO |
| 54 | POST | `mark_paid` | Transaction via `_get_accessible_transaction`; Status @ 95-128 (via service or direct) | `transfer_service.update_transfer` @ 104 (shadow branch); direct Transaction mutation @ 125-128 (non-shadow branch); helper `txn_to_bill_dict` (via `dashboard_service`) | rendered bill-row partial (effective_amount + remaining + over_budget per `_entry_progress_fields`) | `dashboard/_bill_row.html` | `#bill-row-{id}` (HTMX-only) | NO |
| 141 | GET | `bills_section` | -- | `dashboard_service.compute_dashboard_data` @ 152 | `upcoming_bills`: list with `effective_amount`; `current_period`: PayPeriod | `dashboard/_upcoming_bills.html` | bills section target | NO |
| 160 | GET | `balance_section` | -- | `dashboard_service.compute_dashboard_data` @ 171 | `balance_info`: `account_balance` object | `dashboard/_balance_runway.html` | balance section target | NO |

#### `app/routes/savings.py` (288 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 107 | GET | `dashboard` | -- (service-loaded) | `savings_dashboard_service.compute_dashboard_data` @ 112 | `**ctx` unpacked: accounts, goals, emergency_fund_metrics, `savings_total`, `debt_total`, `dti_ratio`, per-account `projected_end_balance` lists | `savings/dashboard.html` | full-page | NO |

Out-of-scope handlers in `savings.py`:

- `new_goal` (116), `create_goal` (124), `edit_goal` (162),
  `update_goal` (176), `delete_goal` (256): savings-goal CRUD; form
  display or redirect-only POST.

#### `app/routes/analytics.py` (563 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 94 | GET | `page` | -- | -- | -- | `analytics/analytics.html` | full-page | NO |
| 107 | GET | `calendar_tab` | UserSettings @ 140-142 | `calendar_service.get_month_detail` @ 155-158 OR `calendar_service.get_year_overview` @ 148-151; `csv_export_service.export_calendar_csv` @ 152, 159; `_render_month_view` @ 168 / `_render_year_view` @ 167; `_validate_owned_or_abort` @ 135 | `data`: month_detail/year_overview object with `day_entries` (effective_amount) and paycheck_days | `analytics/_calendar_month.html` OR `analytics/_calendar_year.html` | calendar tab target (HTMX-only; CSV download non-HTMX) | YES (`_build_calendar_weeks` helper at 380-428 computes `income_total` and `expense_total` per day inline at 413-417) |
| 171 | GET | `year_end_tab` | -- | `year_end_summary_service.compute_year_end_summary` @ 185-187; `csv_export_service.export_year_end_csv` @ 190; `_get_available_years` @ 196 | `data`: year_end summary (year_summary_jan1_balance + year_summary_dec31_balance + year_summary_principal_paid + year_summary_growth + year_summary_employer_total + spending_by_category + transfer_amount per destination); `year`, `available_years`: int / list | `analytics/_year_end.html` | year-end tab target | NO |
| 205 | GET | `variance_tab` | PayPeriod via service @ 256 | `budget_variance_service.compute_variance` @ 239; `csv_export_service.export_variance_csv` @ 249; `pay_period_service.get_all_periods` @ 256; `_build_variance_chart_data` @ 255 | `report`: VarianceReport (effective_amount + period_subtotal); `chart_data`: dict; `window_type`, `period_id`, `month`, `year` | `analytics/_variance.html` | variance tab target | NO |
| 272 | GET | `trends_tab` | UserSettings @ 281-283 | `spending_trend_service.compute_trends` @ 289; `csv_export_service.export_trends_csv` @ 296 | `report`: TrendReport (growth + period_subtotal series) | `analytics/_trends.html` | trends tab target | NO |

#### `app/routes/obligations.py` (423 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 259 | GET | `summary` | TransactionTemplate @ 275-290 (expense), @ 293-307 (income); TransferTemplate @ 310-324; PayPeriod via service indirectly | `amount_to_monthly` (from `savings_goal_service`) @ 338, 361, 383; `_next_occurrence` helper @ 349, 371, 394 | `expense_items`/`income_items`/`transfer_items`: lists of dicts with `monthly` (monthly equivalent of recurring `effective_amount`), `next_date`; `total_expense_monthly`, `total_income_monthly`, `total_transfer_monthly`, `total_outflows`, `net_cash_flow`: Decimal aggregates (`period_subtotal` flavor) | `obligations/summary.html` | full-page | YES (per-template loops at 331-395 sum monthly equivalents; quantize at 398-406; `net_cash_flow = total_income_monthly - total_outflows` at 408) |

#### `app/routes/salary.py` (1466 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 102 | GET | `list_profiles` | SalaryProfile @ 107-112 | `pay_period_service.get_all_periods` @ 115; `pay_period_service.get_current_period` @ 116; `load_tax_configs` @ 121 (per profile); `paycheck_calculator.calculate_paycheck` @ 122-125 (per profile, with `calibration=profile.calibration`) | `profile_data`: list of `{profile, net_pay}` where `net_pay` is `paycheck_net` (Decimal from breakdown) | `salary/list.html` | full-page | NO |
| 149 | POST | `create_profile` | Category @ 172-176; Account @ 191-195; SalaryProfile insert @ 230-244 | `get_baseline_scenario` @ 162; `recurrence_engine.generate_for_template` @ 250; `pay_period_service.get_current_period` @ 256; `pay_period_service.get_all_periods` @ 249; `load_tax_configs` @ 260; `paycheck_calculator.calculate_paycheck` @ 261-263 (sets `template.default_amount = init_breakdown.net_pay` at 264) | -- (redirect) | -- on success | -- | YES (`data["annual_salary"] / pay_periods_per_year` at 223 inline computes `template.default_amount`) |
| 284 | GET | `edit_profile` | SalaryProfile via `get_or_404` @ 289; FilingStatus / RaiseType / DeductionTiming / CalcMethod @ 293-296 | `_get_investment_accounts` @ 297 | `profile`, `filing_statuses`, `raise_types`, `deduction_timings`, `calc_methods`, `investment_accounts` | `salary/form.html` | full-page | NO |
| 311 | POST | `update_profile` | SalaryProfile via `get_or_404` @ 325 | `_regenerate_salary_transactions` @ 369 (which internally calls `pay_period_service.get_all_periods`, `load_tax_configs`, `paycheck_calculator.calculate_paycheck`) | -- (redirect) | -- | -- | NO |
| 438 | POST | `add_raise` | SalaryProfile via helper @ 443; SalaryRaise insert @ 461 | `_regenerate_salary_transactions` @ 465 | partial section render via `_render_raises_partial` (HTMX), else redirect | `salary/_raises_section.html` (partial) | raises section target | YES (`data["percentage"] = D(...)/D("100")` at 459 converts form input) |
| 518 | POST | `delete_raise` | SalaryRaise via `get_owned_via_parent` @ 529-531 | `_regenerate_salary_transactions` @ 539 | partial section render or redirect | `salary/_raises_section.html` | raises section target | NO |
| 571 | POST | `update_raise` | SalaryRaise via `get_owned_via_parent` @ 583-585 | `_regenerate_salary_transactions` @ 628 | partial section render or redirect | `salary/_raises_section.html` | raises section target | NO |
| 696 | POST | `add_deduction` | SalaryProfile via helper @ 701; PaycheckDeduction insert @ 720 | `_regenerate_salary_transactions` @ 724 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | YES (`data["amount"] = D(.../D("100"))` at 716 percentage conversion) |
| 780 | POST | `delete_deduction` | PaycheckDeduction via `get_owned_via_parent` @ 791-793 | `_regenerate_salary_transactions` @ 801 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | NO |
| 833 | POST | `update_deduction` | PaycheckDeduction via `get_owned_via_parent` @ 845-847 | `_regenerate_salary_transactions` @ 893 | partial section render or redirect | `salary/_deductions_section.html` | deductions section target | NO |
| 960 | GET | `breakdown` | SalaryProfile via `get_or_404` @ 965; PayPeriod via `get_or_404` @ 969 | `pay_period_service.get_all_periods` @ 973; `load_tax_configs` @ 974; `paycheck_calculator.calculate_paycheck` @ 975-978 (with `calibration=profile.calibration`) | `profile`, `period`, `breakdown`: `paycheck_breakdown` (PaycheckBreakdown dataclass: paycheck_gross/paycheck_net/federal_tax/state_tax/fica/pre_tax_deduction/post_tax_deduction/employer_contribution); `periods`: list | `salary/breakdown.html` | full-page | NO |
| 1020 | GET | `projection` | SalaryProfile via `get_or_404` @ 1025 | `pay_period_service.get_all_periods` @ 1029; `load_tax_configs` @ 1030; `paycheck_calculator.project_salary` @ 1031-1034 | `profile`, `projection_data`: list of `(period, paycheck_breakdown)` tuples @ 1037 (zipped inline) | `salary/projection.html` | full-page | YES (inline `zip(periods, breakdowns)` at 1037) |
| 1064 | POST | `calibrate_preview` | SalaryProfile via `get_or_404` @ 1069 | `pay_period_service.get_all_periods` @ 1083; `pay_period_service.get_current_period` @ 1084; `load_tax_configs` @ 1087; `paycheck_calculator.calculate_paycheck` @ 1088-1090; `calibration_service.derive_effective_rates` @ 1105-1112 | `data`: form inputs; `rates`: DerivedRates; `taxable_income`: Decimal (computed inline `taxable = gross - total_pre_tax` at 1095); `total_pre_tax`: Decimal (`pre_tax_deduction` sum from breakdown @ 1091) | `salary/calibrate_confirm.html` | full-page | YES (`taxable = gross - total_pre_tax` at 1095) |
| 1127 | POST | `calibrate_confirm` | SalaryProfile via `get_or_404` @ 1132; CalibrationOverride @ 1145-1149; CalibrationOverride insert @ 1154-1169 | `_regenerate_salary_transactions` @ 1175 | -- (redirect) | -- | -- | NO |
| 1196 | POST | `calibrate_delete` | SalaryProfile via `get_or_404` @ 1201; CalibrationOverride @ 1205-1209 | `_regenerate_salary_transactions` @ 1218 | -- (redirect) | -- | -- | NO |
| 1251 | POST | `update_tax_config` | StateTaxConfig @ 1275-1278; StateTaxConfig insert @ 1289-1297 | `_regenerate_all_salary_transactions` @ 1303 | -- (redirect) | -- | -- | YES (`flat_rate = D(str(data["flat_rate"])) / D("100")` at 1271 percentage-to-decimal conversion) |
| 1310 | POST | `update_fica_config` | FicaConfig @ 1330-1333; FicaConfig insert @ 1341 | `_regenerate_all_salary_transactions` @ 1348 | -- (redirect) | -- | -- | YES (similar `D(...) / D("100")` conversions in the FICA percentage handling) |

Out-of-scope handlers in `salary.py`:

- `new_profile` (132), `tax_config` (1243): form display and redirect
  stub respectively.
- `breakdown_current` (989): wrapper that redirects to `breakdown` with
  the current period_id substituted.
- `delete_profile` (398): redirect-only POST.
- `calibrate_form` (1049): form display only.

#### `app/routes/templates.py` (706 lines)

| @route line | HTTP | View fn | DB queries (file:line) | Service calls (fn @ file:line) | Context vars (name: token) | Template | HTMX target | Inline compute? |
| ----------- | ---- | ------- | ---------------------- | ------------------------------ | -------------------------- | -------- | ----------- | --------------- |
| 105 | GET | `list_templates` | TransactionTemplate @ 114-119 | -- | `active_templates`, `archived_templates`: lists (default_amount field is `effective_amount` template) | `templates/list.html` | full-page | NO |
| 129 | GET | `new_template` | Category @ 134-138; Account @ 140-143; RecurrencePattern @ 145; TransactionType @ 146 | `pay_period_service.get_all_periods` @ 147; `pay_period_service.get_current_period` @ 148 | categories, accounts, patterns, txn_types, periods, current_period | `templates/form.html` | full-page | NO |
| 163 | POST | `create_template` | Account @ 176; Category @ 180; PayPeriod @ 208; RecurrencePattern @ 198 | `get_baseline_scenario` @ 243; `pay_period_service.get_all_periods` @ 245; `recurrence_engine.generate_for_template` @ 246-248 | -- (redirect) | -- | -- | NO |
| 255 | GET | `edit_template` | TransactionTemplate @ 260; Category @ 264-268; Account @ 270-273; RecurrencePattern @ 275; TransactionType @ 276 | -- | template + dropdowns + periods | `templates/form.html` | full-page | NO |
| 290 | POST | `update_template` | TransactionTemplate @ 306; Account @ 375; Category @ 380; Transaction bulk update @ 411-414; RecurrencePattern @ 343 | `get_baseline_scenario` @ 417; `pay_period_service.get_all_periods` @ 419; `recurrence_engine.regenerate_for_template` @ 421-423 | -- (redirect) | -- | -- | NO |
| 456 | POST | `archive_template` | TransactionTemplate @ 468; Transaction soft-delete @ 476-480 | -- | -- (redirect) | -- | -- | NO |
| 504 | POST | `unarchive_template` | TransactionTemplate @ 512; Transaction restore @ 520-524 | `get_baseline_scenario` @ 528; `pay_period_service.get_all_periods` @ 530; `recurrence_engine.generate_for_template` @ 531-533 | -- (redirect) | -- | -- | NO |
| 557 | POST | `hard_delete_template` | TransactionTemplate @ 577; Transaction @ 591-595, 616-618; TransactionTemplate delete @ 620 | `archive_helpers.template_has_paid_history` @ 581 | -- (redirect) | -- | -- | NO |
| 640 | GET | `preview_recurrence` | RecurrencePattern @ 657; PayPeriod @ 679 | `pay_period_service.get_all_periods` @ 672; `pay_period_service.get_current_period` @ 689; `recurrence_engine._match_periods` @ 692 | `preview_periods`: PayPeriod list (first 5 matches at 693) | -- (HTML fragment via Markup) | -- (HTMX text/html) | NO |

`templates.py` is mostly redirect-driven (CRUD), but `create_template`, `update_template`,
`unarchive_template`, and `hard_delete_template` all trigger recurrence engine generation that
produces financial figures downstream; they are recorded in scope because the recurrence engine
output is what populates the next grid view's `effective_amount`. Their direct response is a
redirect, which is why the "Context vars" cell is empty for those rows -- their financial output is
materialised elsewhere.

### 1.2.x Cross-page consistency markers

Phase 1 only enumerates; Phase 3 will adjudicate. Each marker below is a controlled-vocabulary token
that two or more route handlers in different files produce values for, so the audit knows which
routes to compare for the same `(user_id, period_id, scenario_id)` triple.

| Concept token | Producers | Where rendered |
| ------------- | --------- | -------------- |
| `checking_balance` / `account_balance` | `grid.index` @ 243; `accounts.checking_detail` @ 1425; `accounts.interest_detail` @ 1291; `dashboard.page` (via `dashboard_service.compute_dashboard_data`); `savings.dashboard` (via `savings_dashboard_service.compute_dashboard_data`); `investment.dashboard` @ 107; `investment.growth_chart` @ 425 | `/grid`, `/accounts/<id>/checking`, `/accounts/<id>/interest`, `/dashboard`, `/savings`, `/accounts/<id>/investment` |
| `projected_end_balance` | `grid.index` (subtotals + balances); `accounts.checking_detail` (3/6/12-month horizons); `accounts.interest_detail` (3/6/12-month horizons); `savings.dashboard` (per-account projections); `investment.dashboard` (multi-horizon chart) | as above plus `/savings` |
| `monthly_payment` | `loan.dashboard` @ 429-435; `loan.payoff_calculate` @ 900-...; `loan.refinance_calculate` @ 1102; `loan.create_payment_transfer` @ 1225, 1231; `debt_strategy.dashboard` (per-account minimum_payment); `debt_strategy.calculate` (strategy results); `obligations.summary` (monthly-equivalent normalization) | `/accounts/<id>/loan`, `/debt-strategy`, `/obligations` |
| `loan_principal_real` / `loan_principal_stored` | `loan.dashboard` (via `proj.current_balance`); `loan.refinance_calculate` @ 1087 (`current_real_principal = proj.current_balance`); `debt_strategy.dashboard` (per-account `current_principal`); A-04 split | `/accounts/<id>/loan`, `/debt-strategy` |
| `dti_ratio` | `dashboard.page` (via `dashboard_service`); `savings.dashboard` (via `savings_dashboard_service`) | `/dashboard`, `/savings` |
| `paycheck_breakdown` (paycheck_gross/net/federal_tax/state_tax/fica/deduction split) | `salary.list_profiles` @ 122; `salary.breakdown` @ 975; `salary.projection` @ 1031; `salary.calibrate_preview` @ 1088; `retirement.dashboard` (via `compute_gap_data`); `retirement.gap_analysis` (via `compute_gap_data`) | `/salary*`, `/retirement*` |
| `transfer_amount` (stored) vs `transfer_amount_computed` (route default) | stored: every `transfer_service.update_transfer` flow above; computed: `loan.create_payment_transfer` @ 1213-1241; `investment.create_contribution_transfer` @ 668-670 | `/accounts/<id>/loan` (transfer prompt), `/accounts/<id>/investment` (contribution prompt) |
| `effective_amount` | every cell-render route (`transactions.get_cell`, `transfers.get_cell`, `transactions.mark_done`, `transfers.mark_done`, etc.); rendered by every grid/dashboard/savings/analytics partial that displays a transaction amount | `/grid`, `/dashboard`, `/savings`, `/analytics`, `/companion` |
| `entry_sum_total` / `entry_remaining` | `transactions.get_cell` @ 88; `entries.list_entries` (and every entry route via `_render_entry_list`); `dashboard._entry_progress_fields` (via service); `companion.index` (via `_build_entry_data`) | `/grid` entry popovers; `/dashboard` bills; `/companion` |
| `goal_progress` | `savings.dashboard`; `retirement.gap_analysis`; `companion._build_entry_data` (`pct = total / estimated_amount * 100`) | `/savings`, `/retirement`, `/companion` |
| `chart_balance_series` / `chart_date_labels` | `loan.dashboard` (original/committed/floor); `loan.payoff_calculate` (accelerated overlay); `investment.dashboard`; `investment.growth_chart` (what-if overlay); `debt_strategy.calculate` (per-loan timelines) | `/accounts/<id>/loan`, `/accounts/<id>/investment`, `/debt-strategy` |
| `year_summary_*` (jan1_balance, dec31_balance, principal_paid, growth, employer_total) | `analytics.year_end_tab` (via `year_end_summary_service.compute_year_end_summary`) | `/analytics/year-end` |

### 1.2.y Ambiguities raised by Phase 1.2

Subagents flagged these for the developer; each maps to a `Q-NN` in `09_open_questions.md`.

- Q-10 (P1-c, 2026-05-15): `grid.index` computes period subtotals
  (income, expense, net) inline at `grid.py:263-279` using
  `txn.effective_amount` directly. Is route-layer subtotal computation
  by design, or should the subtotal aggregation move into a service
  function so the grid and any other consumer (dashboard's
  "spending comparison" section) share a single computation path?
  Phase 6 SRP review is contingent on the answer; Phase 3 consistency
  audit needs the answer before comparing `grid.index` subtotals
  against `dashboard_service._compute_spending_comparison`.

- Q-11 (P1-c, 2026-05-15): `loan.refinance_calculate` reads
  `current_real_principal = proj.current_balance` at line 1087, then
  optionally overrides with `refi_principal = current_real_principal +
  closing_costs` at line 1095. `proj.current_balance` comes from
  `amortization_engine.get_loan_projection` (A-04 dual policy: ARM uses
  stored `current_principal`, fixed-rate uses engine-walked balance).
  Is the refinance flow expected to use the ARM-stored value for ARM
  loans (matching A-04) even though the user's "current principal" for
  refinance purposes might be the engine-walked value? Phase 3 must
  compare the two flavors for the same loan on the same date.

- Q-12 (P1-c, 2026-05-15): `obligations.summary` builds monthly
  equivalents for recurring templates inline at
  `obligations.py:331-395` by calling `amount_to_monthly` per template
  in a loop, then aggregates totals with Decimal arithmetic at
  lines 398-408. The `amount_to_monthly` helper lives in
  `savings_goal_service`; the aggregation is route-level. Should the
  aggregation move into a dedicated service function so the
  obligations summary, the dashboard cash-runway estimate, and the
  debt-summary DTI denominator all consume the same monthly aggregator?
  (Phase 6 SRP candidate.)

- Q-13 (P1-c, 2026-05-15): `salary.calibrate_preview` computes
  `taxable = gross - total_pre_tax` inline at `salary.py:1095`. The
  same identity is computed inside `paycheck_calculator.calculate_paycheck`
  (the breakdown's `taxable_income` field), but the route performs its
  own subtraction. Should the route read `bk.taxable_income` directly
  rather than recomputing? If the route value diverges from the
  breakdown's, which is canonical?

- Q-14 (P1-c, 2026-05-15): `dashboard.mark_paid` (`dashboard.py:54-139`)
  was marked out-of-scope by the Group C return because "it updates
  status/amount in DB and returns a partial row." The response IS an
  updated bill-row partial that re-renders `effective_amount`,
  `entry_remaining`, and the `goal_progress` percent. Per the
  audit-plan scope rule ("in scope only if its response renders
  financial figures"), this handler IS in scope. Should Phase 3 treat
  `mark_paid` as the dashboard's path-equivalent of
  `transactions.mark_done` and verify that the two endpoints produce
  the same effective amount and entry-remaining values for the same
  transaction?

### 1.2.z Notes for Phase 3

- Every cell-render path (`get_cell`, `update_transaction`, `mark_done`,
  `mark_credit`, `cancel_transaction`, plus the transfer-side mirror)
  renders `grid/_transaction_cell.html`, which reads `txn.effective_amount`
  directly. Phase 3 must verify that the four mutation paths
  (`update_transaction`, `mark_done`, `mark_credit`, `cancel_transaction`)
  leave `actual_amount` in a state where `effective_amount` returns the
  intended user-facing value -- specifically, that envelope branch's
  `transaction_service.settle_from_entries` at `transactions.py:596`
  matches the manual-update branch's behavior.
- Every dashboard / savings / accounts / loan detail route reads its
  scenario through `get_baseline_scenario`. Phase 3 should verify that
  no route uses a different scenario resolution (a scenario from form
  input, a hard-coded scenario_id, etc.) for the same user.
- Every loan / investment / interest detail page reads
  `account.current_anchor_balance` either directly or via the balance
  calculator. Phase 3 must verify that the value read on
  `/accounts/<id>/checking` (`accounts.py:1418`) equals the value read
  on `/accounts/<id>/interest` (`accounts.py:1283`) for the same
  account; A-04's dual policy for ARM means the loan detail page reads
  through `LoanProjection.current_balance` instead, and Phase 3 must
  confirm these are not silently inconsistent.

## 1.3 Template layer

106 template files under `app/templates/` (11,444 LOC total). 56 in scope (render at least one
controlled-vocabulary financial figure); 50 out of scope (auth, errors, settings, navigation, pure
forms). Explore T was dispatched 2026-05-15 with thoroughness "very thorough" and the verbatim
classification rules from the session prompt; the parent then ran a 16-row QC pass biased toward
rows classified as "arithmetic in Jinja" (the high-risk category per audit plan section 1.3, which
calls out Jinja arithmetic as a finding).

### 1.3.0 Quality control log

| Pass | Files in scope | Total rows | Sampled | Failures by class | Action |
| ---- | -------------- | ---------- | ------- | ----------------- | ------ |
| 1    | 56             | 56 per-template rows + 13 cross-cutting "arithmetic-YES" entries | 16 (8 from arithmetic-YES list, 8 mixed) | 4 classification misses, 0 line drift, 0 hallucinated variable, 0 wrong cross-ref, 1 format-string drift | accept with inline correction; see "Systematic error class" note below |

Per-sample verification (each `path:line` re-read with the source file):

| # | Cited claim | Verdict |
| - | ----------- | ------- |
| 1 | `grid/_transaction_cell.html:21` `{% set remaining = t.estimated_amount - es.total %}` arithmetic YES | OK |
| 2 | `grid/_mobile_grid.html:96` `{% set remaining = txn.estimated_amount - es.total %}` arithmetic YES | OK |
| 3 | `grid/_transaction_entries.html:136` `remaining\|abs` arithmetic YES | **MISS**: `\|abs` is a filter applied to a value subtracted upstream; not arithmetic. Move to "filter usages" cross-cutting list, not the arithmetic list. |
| 4 | `loan/_schedule.html:55` `(row.payment\|float) + (monthly_escrow\|float) + (row.extra_payment\|float)` arithmetic YES | OK |
| 5 | `loan/_escrow_list.html:37` `comp.annual_amount\|float / 12` arithmetic YES | OK |
| 6 | `loan/_payoff_results.html:72` `(monthly_payment\|float + required_extra\|float)` arithmetic YES | OK |
| 7 | `loan/_refinance_results.html:54` `{% set term_diff = comparison.refi_term - comparison.current_remaining_months %}` arithmetic YES | OK |
| 8 | `loan/dashboard.html:116` `(params.term_months / 12)\|round(1)` arithmetic YES | OK (months-to-years conversion; also has `\|round(1)` filter, see filter list) |
| 9 | `analytics/_variance.html:8-9, 12-14` macro `fmt_var` and `fmt_pct` arithmetic YES | **MISS**: macro bodies are `{% if value > 0 %} ... {% elif value < 0 %} ...` conditionals on `value`; no arithmetic operator. Move to "conditional on financial value" list. |
| 10 | `salary/_deductions_section.html:38` `(d.amount * 100)\|float` arithmetic YES | OK (borderline; rate-to-percentage display per A-01 admonition "if you cannot prove the multiplication is presentation-only, classify it as arithmetic") |
| 11 | `analytics/_calendar_year.html:34` `{% if card.summary.net\|float > 0 %}border-success{% elif ... %}border-danger{% endif %}` arithmetic YES | **MISS**: `{% if %}` block comparison only; no arithmetic operator. Move to "conditional on financial value" list. |
| 12 | `salary/calibrate_confirm.html:66, 71, 76, 81` `(rates.effective_federal_rate * 100)\|float` arithmetic YES | OK (borderline; rate-to-percentage display) |
| 13 | `dashboard/_bill_row.html:33` no arithmetic claimed | OK |
| 14 | `savings/dashboard.html:317` `{:,.2f}` for `total_savings` | **MISS** (format-string drift): actual format at line 317 is `{:,.0f}`. Corrected below. |
| 15 | `loan/dashboard.html:99` `${{ "{:,.2f}".format(params.original_principal\|float) }}` | OK |
| 16 (bonus) | `dashboard/_savings_goals.html:15` `[goal.pct_complete\|float, 100]\|min` recorded in `\|round` column | **MISS** (column-meaning confusion): `\|min` is not `\|round`. The Phase-3 concern called out in the prompt is `\|round` bypassing the canonical quantize; `\|min`/`\|max`/`\|abs` are separate. Restructured below into a unified "filter usages on financial values" list. |

Failure rate within the arithmetic-YES sample (the high-risk category): 3 of 8 = 38%; failure rate
overall: 4 of 16 = 25%. The strict accept threshold from the session prompt is "0-1 of 15" (<=7%);
2-3 triggers re-dispatch; 4+ triggers discard-and-re-dispatch. We are at the discard-threshold by
overall count.

The parent deviated from a full re-dispatch because (a) all four misses fall into a single
systematic class, identified below; (b) the underlying per-template line citations are accurate (no
line drift, no hallucinated variables); (c) the corrections are localized and recoverable from the
existing Explore output. The corrected lists appear in section 1.3.x. If the developer disagrees
with the deviation, P1-e should re-dispatch Explore-T for the four affected files (`_variance.html`,
`_calendar_year.html`, `_transaction_entries.html`, `_savings_goals.html`) with the systematic-error
feedback from below.

**Systematic error class (templates).** Explore-T over-flagged borderline non-arithmetic items as
"arithmetic in Jinja":

  - `{% if value > 0 %}` / `{% elif value < 0 %}` conditionals
    (samples 9, 11). These are conditional logic on financial
    values, not arithmetic operators. The classification rule
    explicitly excludes them: "Comparisons in `{% if %}` blocks
    ... record them in the 'Conditional on financial value' column
    ... but do NOT classify as arithmetic."
  - Filter applications: `|abs` (sample 3), `|min` recorded in the
    `|round` column (sample 16). The classification rule names
    `|round` as a Phase-3 concern (bypasses `Decimal.quantize`);
    other filters are noted but not flagged.

This systematic pattern is potentially also present in section 1.2's inline-compute claims (P1-c
reported three Group A inline-arithmetic misclassifications of the same shape: schema-validated
model construction treated as inline Decimal arithmetic). P1-e wrap-up should consider whether the
systematic pattern warrants re-verifying P1-b's service-layer "inline-compute" column for the same
class of error.

### 1.3.1 Out of scope: no financial figures rendered

50 files. Confirmed by quick read of each. Move to in-scope at any point if a future change
introduces a financial figure.

- `app/templates/auth/*.html` (7 files): login, register, reauth, mfa_backup_codes, mfa_disable,
  mfa_setup, mfa_verify
- `app/templates/errors/*.html` (5 files): 400, 403, 404, 429, 500
- `app/templates/base.html`, `_keyboard_help.html`, `_confirm_modal.html`,
  `_security_event_banner.html`, `_form_macros.html`
- `app/templates/settings/*.html` (12 files): dashboard, settings, _general, _account_types,
  _categories, _companion, _mfa_setup, _pay_periods, _retirement, _security, _tax_config,
  _tax_config_sections (NOTE: `_general.html:16, 47` use `(rate * 100)|round(2)` and
  `(rate * 100)|round(0)|int` for inflation_rate and trend_alert_threshold display; these are
  presentation-only rate-to-percentage conversions on settings forms, not financial-figure
  rendering)
- `app/templates/categories/*.html` (2 files): list, _category_row
- `app/templates/pay_periods/generate.html`: PayPeriod CRUD
- `app/templates/grid/no_periods.html`, `grid/no_setup.html`, `grid/_transaction_empty_cell.html`:
  empty-state markers
- `app/templates/grid/_transaction_full_create.html`, `grid/_transaction_quick_create.html`,
  `grid/_transaction_quick_edit.html`, `grid/_transaction_full_edit.html`: form inputs; the existing
  amount appears as an input value, no rendering of computed figures
- `app/templates/transfers/_transfer_full_edit.html`, `transfers/_transfer_quick_edit.html`,
  `transfers/form.html`: form inputs
- `app/templates/accounts/form.html`, `templates/form.html`, `salary/form.html`,
  `salary/calibrate.html`, `savings/goal_form.html`, `retirement/pension_form.html`,
  `salary/tax_config.html`, `loan/setup.html`, `loan/_refinance.html`: input forms;
  `loan/setup.html:65` uses `(account_type.max_term_months / 12)|round(0)|int` to display max-term
  in years (presentation-only, non-monetary)
- `app/templates/dashboard/_alerts.html`, `dashboard/_mfa_nag.html`: non-monetary

Moved IN scope after read (originally borderline): `salary/list.html` (renders `net_pay` per
profile), `accounts/list.html` (renders `current_anchor_balance`), `templates/list.html` (renders
`default_amount`), `transfers/list.html` (renders `default_amount`), `dashboard/_payday.html`
(renders `payday_info.next_amount`).

### 1.3.2 grid/ (7 in scope, 1,160 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja (YES line) | `\|round` (line) | Other filters on $ (line) | Conditional on $ (line) | HTMX target (provides/expects) | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------------------ | ---------------- | ------------------------- | ----------------------- | ------------------------------ | ---------------- |
| `grid.html` | 363 | `account_balance` (17, via `account.current_anchor_balance`); `projected_end_balance` (`balances[period.id]`, 26); `period_subtotal` (`subtotals[period.id].income/expense/net`, 196, 269, 280); `effective_amount` (via included `_transaction_cell.html`) | `{:,.0f}` (26, 198, 271, 284) | NO | NO | NO | YES: balance < 0 / < low_balance_threshold (23) | hx-get -> `grid.balance_row` (12); hx-post -> `transactions.create_transaction` (312); includes `_balance_row`, `_transaction_cell`, `_mobile_grid`, `_anchor_edit` | extends `base.html` |
| `_balance_row.html` | 33 | `projected_end_balance` (`balances[period.id]`, 26) | `{:,.0f}` (26) | NO | NO | NO | YES: balance < 0; balance < low_balance_threshold (23) | hx-target #balanceRow; renders inside the grid footer (OOB swap) | (partial) |
| `_transaction_cell.html` | 87 | `effective_amount` (17, ternary `t.actual_amount if not none else t.estimated_amount`); `estimated_amount` (21, 43, 48, 53); `actual_amount` (17, 46, 50); `entry_sum_total` (21, 43, via `es.total`); `entry_remaining` (33-34) | `{:,.0f}` (43, 48, 50, 53); `{:,.2f}` (33-34) | YES: `{% set remaining = t.estimated_amount - es.total %}` at line 21 | NO | `\|abs` (33-34 in title attr; the filtered value `remaining` was subtracted on line 21) | YES: show_progress (19); over_budget i.e. remaining<0 (22); `t.actual_amount != t.estimated_amount` (46) | hx-target `#txn-cell-{id}` (29); hx-get -> `transactions.get_quick_edit` (27) | (partial); rendered by every cell-render route per section 1.2 |
| `_transaction_entries.html` | 162 | `entry.amount` (92, 137); `entry_remaining` (136); `entry_sum_total` (136) | `{:,.2f}` (92, 136) | NO (per QC sample-3 correction; the subtraction happened in the route helper, the template just renders `remaining\|abs`) | NO | `\|abs` on remaining (136) | YES: `entry.is_credit` (83); `remaining < 0` (136) | hx-target `#txn-entries-{id}`; hx-patch -> `entries.update_entry` (34); hx-post -> `entries.create_entry` (142); hx-delete -> `entries.delete_entry` (117) | (partial) |
| `_carry_forward_preview_modal.html` | 204 | `plan.entries_sum` (107, 113, 117, 137-139); `plan.target_estimated_before` (109-110, 118-119); `plan.target_estimated_after` (111, 121); `plan.leftover` (113, 123); these are `estimated_amount` / `effective_amount` flavors per template | `{:,.2f}` (107-139) | NO | NO | NO | YES: `plan.blocked` (82, 94, 152-157); `plan.leftover > 0` (104); `plan.target_will_be_generated` (105) | hx-target `#carry-forward-modal-mount` (base.html:276); hx-post -> `transactions.carry_forward` (190) | (partial; modal mount from base.html) |
| `_anchor_edit.html` | 61 | `account_balance` (`account.current_anchor_balance`, 26, 51, 53) | `{:,.0f}` (53); `{:,.2f}` (48) | NO | NO | NO | YES: `conflict` (54) | hx-target `#anchor-cell-{id}` and OOB `#anchor-as-of`; hx-patch -> `accounts.true_up` (14); hx-get -> `accounts.anchor_form`/`anchor_display` | (partial) |
| `_mobile_grid.html` | 250 | same tokens as `grid.html` and `_transaction_cell.html` (mobile variant of the grid render) | `{:,.0f}` (58, 96, 103) | YES: `{% set remaining = txn.estimated_amount - es.total %}` at line 96 | NO | `\|abs` on remaining (103) | YES: `show_progress` (94); `over_budget` (97) | rendered inside `grid.html`'s mobile-only block | included by `grid.html` |

### 1.3.3 dashboard/ (7 in scope, 326 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 129 | (container) | -- | NO | NO | NO | NO | hx-get -> `dashboard.bills_section`, `dashboard.balance_section`; hx-trigger `balanceChanged`, `dashboardRefresh` | extends `base.html`; includes `_balance_runway`, `_upcoming_bills`, `_spending_comparison`, `_savings_goals`, `_debt_summary`, `_alerts`, `_payday` |
| `_bill_row.html` | 55 | `effective_amount` (`bill.amount`, 33-34, 38); `entry_sum_total` (`bill.entry_total`, 33-34); `entry_remaining` (`bill.entry_remaining`, 33); `entry_count` (`bill.entry_count`, 33) | `{:,.2f}` (33-34, 38) | NO | NO | `\|abs` on bill.entry_remaining (33, in title attr) | YES: `show_progress` (31); `bill.is_paid` (37, 44); `bill.entry_over_budget` (32) | hx-post -> `dashboard.mark_paid` (46); hx-target `closest .bill-row` (47) | (partial) |
| `_upcoming_bills.html` | 17 | (includes `_bill_row` per bill) | -- | NO | NO | NO | NO | hx-trigger `dashboardRefresh` re-renders the list | includes `_bill_row` |
| `_balance_runway.html` | 29 | `account_balance` (`balance_info.current_balance`, 10); `cash_runway_days` (`balance_info.cash_runway_days`, 21) | `{:,.2f}` (10) | NO | NO | NO | YES: `balance_info.cash_runway_days is not none` (19) | hx-get -> `accounts.anchor_form` (5); hx-target `#balance-display` (6) | (partial) |
| `_debt_summary.html` | 30 | `debt_total` (`debt_summary.total_debt`, 5); `monthly_payment` aggregate (`debt_summary.total_monthly_payments`, 9); `dti_ratio` (`debt_summary.dti_ratio`, 15) | `{:,.2f}` (5, 9); `{:.1f}` (15) | NO | NO | `\|float` for formatting (15) | YES: `dti_label` direction (16-19); `projected_debt_free_date` truthy (22) | (partial) | (partial) |
| `_savings_goals.html` | 28 | `goal.current_balance` (8); `goal.target_amount` (9); `goal_progress` (`goal.pct_complete`, 13, 20) | `{:,.2f}` (8-9); `{:.0f}` (20) | NO | NO | `\|min` on `[goal.pct_complete\|float, 100]` at line 15 (caps display percent); `\|float` (13, 15, 16, 20) | YES: `goal.pct_complete >= 100` (13) | (partial) | (partial) |
| `_spending_comparison.html` | 39 | `spending_comparison.current_total` (7); `spending_comparison.prior_total` (11); `spending_comparison.delta` (19, 24); `spending_comparison.delta_pct` (32); all are `period_subtotal` flavors | `{:,.2f}` (7, 11, 19, 24); `{:.1f}` (32) | NO | NO | NO | YES: `spending_comparison.direction` (16); `delta is not none` (14) | (partial) | (partial) |
| `_payday.html` | 28 | `paycheck_net` (`payday_info.next_amount`, 15) | `{:,.2f}` (15) | NO | NO | NO | YES: `payday_info` truthy; `payday_info.is_today` | (partial) | (partial) |

### 1.3.4 savings/ (1 in scope, 469 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 469 | `debt_total` (`debt_summary.total_debt`, 54); `monthly_payment` aggregate (`debt_summary.total_monthly_payments`, 58); weighted-rate (`debt_summary.weighted_avg_rate`, 64); `apy_interest` (`ad.interest_params.apy`, 137, 140); `loan_principal_real` (`ad.loan_params.interest_rate`, 140 -- rate not principal; `ad.current_balance` is the principal flavor at 186); `monthly_payment` per-account (`ad.monthly_payment`, 196); `payoff_date` per-account (201); `projected_end_balance` per-account (`ad.projected[label,bal]`, 212); `savings_total` (317); `emergency_fund_coverage_months` (`emergency_metrics.months_covered`, 298; `.paychecks_covered`, 304; `.years_covered`, 310); `avg_monthly_expenses` (319); `goal_progress` (`gd.progress_pct`, 380); `gd.required_contribution` (402); trajectory months/date (428-429) | `{:,.2f}` (54, 58, 186, 196, 276, 363, 365, 402, 444); `{:.2f}` (64, 137, 140, 143); `{:,.0f}` (212, 317, 319) | NO | NO | NO | YES: `ad.needs_setup` (124); `ad.is_paid_off` (127, 258); `category_name == 'liability'` (44); `goal.pct_complete >= 100` (372); trajectory branch (419-456); `traj.months_to_goal == 0` (422) | hx-get -> `accounts.inline_anchor_form` (5) | extends `base.html` |

Notes: The "Based on $X savings" line at 317 uses `{:,.0f}`, not `{:,.2f}` (QC sample 14
correction). The "debt summary" card (44-98) duplicates `dashboard/_debt_summary.html` content;
cross-render with the dashboard widget is a Phase 3 consistency check (savings vs dashboard for the
same user).

### 1.3.5 accounts/ (4 in scope, ~330 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `_anchor_cell.html` | 57 | `account_balance` (`acct.current_anchor_balance`, 23, 48) | `{:,.2f}` (48); `{:.2f}` (48) | NO | NO | NO | YES: `conflict` (45-46) | hx-patch -> `accounts.inline_anchor_update`; hx-get -> `accounts.inline_anchor_form` / `inline_anchor_display` | (partial) |
| `checking_detail.html` | ~95 | `account_balance` (`current_balance`, 43); `projected_end_balance` (`projected[label, bal]`, 55) | `{:,.2f}` (43, 55) | NO | NO | NO | YES: `current_balance is not none` (42); `projected` truthy (49) | full-page | extends `base.html` |
| `interest_detail.html` | ~100 | `account_balance` (`current_balance`, 52); `apy_interest` (`params.apy`, 42, 85); `projected_end_balance` (`projected[label, bal]`, 64); `apy_interest` per-period (`period_data[i].interest`, separate row from balance) | `{:,.2f}` (52, 64); `{:.3f}` (42, 85) | NO (line 85 `params.apy\|float * 100` is rate-to-percent display per A-01 admonition; classify as presentation-only) | NO | NO | YES: `current_balance is not none` (51); `projected` truthy (58); `params.compounding_frequency` switch (93-97) | full-page | extends `base.html` |
| `list.html` | 140 | `account_balance` (`acct.current_anchor_balance`, 110 archived list; 46 active list via included `_anchor_cell`) | `{:,.2f}` (110) | NO | NO | NO | YES: `editing` toggle (45); `archived_accounts\|length` (89) | (delegates to `_anchor_cell` for active cells) | extends `base.html`; includes `_anchor_cell` |

### 1.3.6 loan/ (8 in scope, ~700 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 347 | `loan_principal_stored` (`params.original_principal`, 99); `loan_principal_real` (`params.current_principal`, 104; A-04 dual policy noted in section 1.2.x); rate (`params.interest_rate * 100`, 110, rate-to-percent display); term months (116); `monthly_payment` (`summary.monthly_payment`, 129); `escrow_per_period` (`monthly_escrow`, ~57 included partial); total monthly (`total_payment`, 134); `total_interest` (`summary.total_interest`, 139); `payoff_date` (`summary.payoff_date`, 143); `chart_balance_series` (data attrs for `_schedule` chart and OOB chart) | `{:,.2f}` (99, 104, 129, 134, 139); `{:.3f}` (110) | YES (sample 8): `(params.term_months / 12)\|round(1)` at line 116 -- months-to-years conversion, non-monetary | YES on `term_months / 12` at line 116 -- non-monetary | NO | YES: `params.is_arm` (111); `total_payment` truthy (131); `show_transfer_prompt` (50) | hx-post -> `loan.create_payment_transfer` (64); includes `_schedule`, `_payment_breakdown`, `_rate_history`, `_escrow_list`, `_payoff_results`, `_refinance`, `_refinance_results` | extends `base.html` |
| `_schedule.html` | 103 | `principal_paid_per_period` (`row.payment`, 55; `row.principal`, 56); `interest_paid_per_period` (`row.interest`, 57); `escrow_per_period` (`monthly_escrow`, 55, 59); extra payment (`row.extra_payment`, 55, 64); `loan_principal_real` per-row (`row.remaining_balance`, 70); rate per-row (`row.interest_rate`, 74); schedule totals: `monthly_payment` lifetime sum (`schedule_totals.total_payment`, 94); `principal_paid_per_period` sum (`schedule_totals.total_principal`, 95); `total_interest` (`schedule_totals.total_interest`, 96); escrow sum (`schedule_totals.total_escrow`, 98) | `{:,.2f}` (55-70, 94-98); `{:.3f}` (74) | YES: line 55 `(row.payment\|float) + (monthly_escrow\|float) + (row.extra_payment\|float)` -- three-operand addition on monetary values | NO | `\|float` on row.payment, monthly_escrow, row.extra_payment (55); `\|float` on monthly_escrow > 0 conditional (58); `\|float` on row.extra_payment != 0 (63); `\|float` on row.principal (56), row.interest (57) | YES: `row.is_confirmed` (52); `row.interest_rate is not none` (72, 76); `schedule_totals.has_extra` (31, 61); `monthly_escrow > 0` (28, 58) | (partial; included by `loan/dashboard.html`) | included by `dashboard.html` |
| `_payment_breakdown.html` | 77 | `monthly_payment` (`payment_breakdown.total`, 22); `principal_paid_per_period` (`payment_breakdown.principal`, 51); `interest_paid_per_period` (`payment_breakdown.interest`, 56); `escrow_per_period` (`payment_breakdown.escrow`, 62, 70); component percentages (26, 30, 33, 37, 40, 44) | `{:,.2f}` (22, 51, 56, 62, 70); `{:.1f}` for percentages | NO | NO | NO | YES: `payment_breakdown.escrow > 0` (59); `payment_breakdown.is_confirmed` (7); `payment_breakdown.next_year_escrow` (67) | (partial) | included by `dashboard.html` |
| `_escrow_list.html` | 97 | `escrow_per_period` annual (`comp.annual_amount`, 36) and monthly (37); aggregate `monthly_escrow` (8, 16); aggregate `monthly_payment` (`total_payment`, 8) | `{:,.2f}` (8, 16, 36-37) | YES: line 37 `comp.annual_amount\|float / 12` -- division to derive monthly from annual; line 40 `comp.inflation_rate\|float * 100` is rate-to-percent display (presentation-only); | NO | `\|float` widespread | YES: `monthly_escrow is not none` (4); `total_payment is defined` (4); `escrow_components\|length` (20); `comp.inflation_rate` truthy (39) | hx-post -> `loan.delete_escrow` (46); hx-post -> `loan.add_escrow` (65); hx-target `#escrow-list` (47, 66) | (partial) |
| `_rate_history.html` | 66 | rate (`params.interest_rate * 100`, 8 -- rate-to-percent display); per-entry rate (`entry.interest_rate * 100`, 29) | `{:.3f}` (8, 29) | NO (rate-to-percent display per A-01 admonition) | NO | NO | YES: `params.is_arm` (9); `rate_history\|length` (15) | hx-post -> `loan.add_rate_change` (41); hx-target `#rate-history` (42) | (partial) |
| `_payoff_results.html` | 78 | `payoff_date` (`payoff_summary.payoff_date_with_extra`, 9); `months_saved` (`committed_months_saved`, 14); `interest_saved` (`committed_interest_saved`, 19); `required_extra` (66); `monthly_payment` (72) | `{:,.2f}` (19, 66, 72) | YES: line 72 `(monthly_payment\|float + required_extra\|float)` -- addition on monetary values | NO | `\|float` widespread | YES: `mode == "extra_payment"` (4) vs `"target_date"` (52); `required_extra == 0` (57); `committed_months_saved > 0` (29) | hx-target `#payoff-results`; rendered by `loan.payoff_calculate` | (partial) |
| `_refinance_results.html` | 108 | `monthly_payment` current/refi (`comparison.current_monthly`, 23; `comparison.refi_monthly`, 24); savings (`comparison.monthly_savings`, 25); `total_interest` current/refi (37, 38); `interest_saved` (41); `loan_remaining_months` (`comparison.current_remaining_months`, 51); refi term (52); `loan_principal_real` current/refi (69, 70); `months_saved` (`comparison.break_even_months`, 90); closing_costs (93) | `{:,.2f}` (currencies); `{:.3f}` (rates if any) | YES: line 54 `{% set term_diff = comparison.refi_term - comparison.current_remaining_months %}` (subtraction of months); line 72 `{% set princ_diff = comparison.refi_principal - comparison.current_principal %}` (subtraction of monetary values); line 76 `(-princ_diff)\|float` (unary negation on monetary value) | NO | `\|float` widespread | YES: `error` (7); `comparison.break_even_months is not none` (88); `closing_costs > 0` (80, 95) | hx-target `#refinance-results`; rendered by `loan.refinance_calculate` | (partial) |
| `_refinance.html` | -- | (form wrapper that includes `_refinance_results` after HTMX swap) | -- | NO | NO | NO | NO | hx-target `#refinance-results` | included by `dashboard.html` |

### 1.3.7 investment/ (2 in scope, 368 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 322 | `account_balance` (`current_balance`, 40); assumed return rate (47, rate-to-percent); periodic contribution (56); `employer_contribution` (`employer_contribution_per_period`, 62); `ytd_contributions` (`limit_info.ytd`, 76); `contribution_limit_remaining` (`limit_info.limit`, 76); contribution-limit pct (`limit_info.pct`, 80, 88); `chart_balance_series` and `chart_date_labels` (data attrs for chart) | `{:,.2f}` (40, 56, 62, 76); `{:.2f}` (47) | NO | NO | NO | YES: `params is not none` (46); `limit_info.pct >= 100` (80) | hx-trigger via `_growth_chart` swap; renders `_growth_chart` after `investment.growth_chart` GET | extends `base.html`; includes `_growth_chart` |
| `_growth_chart.html` | 46 | `chart_date_labels` (3, data attr); `chart_balance_series` (4, data attr `data-balances`); cumulative contributions (5, `data-contributions`); what-if `chart_balance_series` (6, `data-whatifBalances`); what-if amount (8); end balances `account_balance` (`comparison.committed_end`, 16; `comparison.whatif_end`, 20); difference (`comparison.difference`, 28) | `{:,.2f}` (8, 16, 20, 28, 30) | NO | NO | NO | YES: `chart_labels\|length` (1); `what_if_balances is defined` (6); `comparison.is_positive` (24) | hx-target `#growth-chart`; rendered by `investment.growth_chart` | (partial) |

### 1.3.8 retirement/ (5 in scope, ~401 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 165 | SWR slider value (33, 40); return-rate slider (52, 60); `pension_benefit_annual` (`pension_benefit.annual_benefit`, 111); `pension_benefit_monthly` (118) | `{:.2f}` (33, 40, 52, 60); `{:,.2f}` (105, 111, 118) | NO | NO | NO | YES: `gap_analysis` truthy (22); `pension_benefit` (86) | hx-get -> `retirement.gap_analysis` on `slider-changed` (77); hx-target `#gap-analysis` | extends `base.html`; includes `_gap_analysis`, `_retirement_account_table` |
| `_gap_analysis.html` | 92 | `paycheck_net` (`gap_analysis.pre_retirement_net_monthly`, 9); `pension_benefit_monthly` (15, 22); `after_tax_monthly_pension` (22); gap (`gap_analysis.monthly_income_gap`, 29); SWR rate (32, rate-to-percent); `savings_total` required (`gap_analysis.required_retirement_savings`, 35); `growth` projected (`gap_analysis.projected_total_savings`, 41); surplus (`gap_analysis.savings_surplus_or_shortfall`, 53); after-tax flavors (64, 71); `chart_balance_series` and chart data attrs (80-85) for `retirement_gap_chart.js` | `{:,.2f}` (9, 15, 22, 29, 35, 41, 53, 64, 71); `{:.1f}` (32) | NO | NO | NO | YES: `gap_analysis.after_tax_monthly_pension is not none` (17); `gap_analysis.savings_surplus_or_shortfall >= 0` (45); `gap_analysis.after_tax_projected_savings is not none` (56) | hx-swap-oob for `#retirement-accounts-content` (line 99); JS reads data-pension / data-investment / data-pre-retirement (81-85) to render chart | (partial); rendered by `retirement.gap_analysis` |
| `_retirement_account_table.html` | 25 | (table header; delegates) | -- | NO | NO | NO | NO | (table wrapper) | includes `_retirement_account_rows` |
| `_retirement_account_rows.html` | 20 | per-account `account_balance` (`proj.current_balance`, 15); rate (`proj.annual_return_rate`, 16); projected `account_balance` (`proj.projected_balance`, 17) | `{:,.2f}` (15, 17); `{:.1f}` (16) | NO | NO | NO | YES: `proj.is_traditional` vs Roth (11); `proj.annual_return_rate is not none` (16) | (partial) | included by `_retirement_account_table` |

### 1.3.9 debt_strategy/ (2 in scope, ~388 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `dashboard.html` | 163 | per-account `loan_principal_real` (debt_accounts loop, principal column); per-account `monthly_payment` (minimum_payment column); `has_arm` flag triggers warning | `{:,.2f}` | NO | NO | NO | YES: `account.is_arm` warning; account-list length | hx-post -> `debt_strategy.calculate` (49); hx-target `#results` (50) | extends `base.html`; renders `_results` after POST |
| `_results.html` | 225 | per-strategy `payoff_date` (`comparison.<key>.debt_free_date`, 34-38); `total_interest` (45-49); total paid (56-59); `loan_remaining_months` (`total_months`, 67-70); `interest_saved` per non-baseline strategy (80-95); per-loan `chart_balance_series` (chart_data_json) | `{:,.2f}` (45-49, 56-59, 80-95) | NO | NO | NO | YES: per-strategy `interest_saved > 0` for sign formatting (80, 89) | hx-target `#debt-strategy-results`; rendered by `debt_strategy.calculate` | (partial) |

### 1.3.10 obligations/ (1 in scope, 219 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `summary.html` | 219 | `period_subtotal` aggregates: `total_outflows` (47); `total_expense_monthly` (50, 111); `total_transfer_monthly` (51, 159); `total_income_monthly` (57, 206); `net_cash_flow` (62); per-item `effective_amount` (`item.amount`, 100, 149, 195); per-item monthly equivalent (`item.monthly`, 102, 151, 197) | `{:,.2f}` | NO | NO | NO | YES: `has_any` (18); `category_name` checks (44); `net_cash_flow >= 0` (62); list lengths (79, 128, 176) | full-page | extends `base.html` |

Note: route-level computation at `obligations.summary` (per section 1.2) does the monthly
aggregation inline at `obligations.py:331-395` (Q-12 in `09_open_questions.md`). The template only
renders the finished aggregates.

### 1.3.11 salary/ (5 in scope + 1 list, ~700 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `list.html` | 142 | `paycheck_gross` proxy (`p.annual_salary`, 48); `paycheck_net` per profile (`item.net_pay`, 51) | `{:.2f}` (48, 51) | NO | NO | NO | YES: `item.net_pay is not none` (50); `p.calibration.is_active` (52); `p.is_active` (60) | full-page | extends `base.html` |
| `breakdown.html` | 165 | `paycheck_gross` (`breakdown.annual_salary`, 64; `breakdown.gross_biweekly`, 70); `pre_tax_deduction` per-line (`breakdown.pre_tax_deductions[].amount`, 81); `taxable_income` (`breakdown.taxable_income`, 89); `federal_tax` (98); `state_tax` (102); `fica` SS (106); `fica` Medicare (110); `post_tax_deduction` per-line (121); `paycheck_net` (implicit final) | `{:.2f}` | NO | NO | NO | YES: `breakdown.is_third_paycheck` (44); `breakdown.raise_event` (51); deduction list lengths (74, 114) | full-page | extends `base.html` |
| `projection.html` | 76 | `paycheck_gross` (`bd.annual_salary`, 50; `bd.gross_biweekly`, 56); `pre_tax_deduction` total (`bd.total_pre_tax`, 58); total taxes (`bd.total_taxes`, 60); `post_tax_deduction` total (`bd.total_post_tax`, 62); `paycheck_net` (`bd.net_pay`, 64) | `{:.2f}` | NO | NO | NO | YES: `bd.raise_event` (52); `bd.is_third_paycheck` (66); `projection_data\|length` (42) | hx-get -> `salary.breakdown` (45) | extends `base.html` |
| `calibrate_confirm.html` | 115 | `paycheck_gross` (`data.actual_gross_pay`, 35); `pre_tax_deduction` sum (`total_pre_tax`, 39); `taxable_income` (43); `federal_tax` actual (`data.actual_federal_tax`, 65); `state_tax` (70); `fica` SS (75); `fica` Medicare (80); effective rates (66, 71, 76, 81 -- rate-to-percent display) | `{:.2f}` (currencies); `{:.3f}` (rates 66-81) | NO (lines 66, 71, 76, 81 multiply rates by 100 for percentage display; classify as presentation per A-01 admonition) | NO | `\|float` widespread | NO direct conditionals on $ | POST -> `salary.calibrate_confirm` (89) | extends `base.html` |
| `_raises_section.html` | 153 | per-raise percentage (`r.percentage`, 39 -- stored as decimal fraction); per-raise flat amount (`r.flat_amount`, 41) | `{:.2f}` (39, 41) | NO | NO | NO | YES: `r.percentage` truthy (38); `r.flat_amount` truthy (40); `r.is_recurring` (45) | hx-post -> `salary.add_raise` (92); hx-post -> `salary.delete_raise` (68); per-row hx-post -> `salary.update_raise` | (partial); rendered by `salary.add_raise`, `delete_raise`, `update_raise` (per section 1.2) |
| `_deductions_section.html` | 211 | per-deduction amount (`d.amount`, 38, 40, 70, 75); annual cap (`d.annual_cap`, 55); deductions per year (44, 47, 50) | `{:.2f}` (38, 40, 55, 70, 75) | YES: line 38 `(d.amount * 100)\|float` (borderline; rate-to-percent display when `calc_method_id == CALC_PERCENTAGE`); -- classify YES per A-01 admonition; the multiplication operates on a value that IS interpreted as monetary in the FLAT branch (line 40) and as a rate-fraction in the PERCENTAGE branch (line 38) | NO | `\|float` widespread | YES: `d.calc_method_id == CALC_PERCENTAGE` (37); `d.deductions_per_year` equality (44, 47, 50); `d.annual_cap` truthy (54); `d.target_account` truthy (58); `d.inflation_enabled` (74) | hx-post -> `salary.add_deduction`; `salary.delete_deduction`; `salary.update_deduction` | (partial) |

### 1.3.12 analytics/ (6 in scope, ~770 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `analytics.html` | 85 | (container) | -- | NO | NO | NO | NO | hx-get tab handlers (19, 29, 39, 49); hx-target `#tab-content` | extends `base.html` |
| `_calendar_month.html` | ~100 | day-level totals (`day.income_total`, 53; `day.expense_total`, 56 -- both `period_subtotal` flavors); per-entry `effective_amount` (`entry.amount`, 95) | `{:,.0f}` (53, 56); `{:,.2f}` (95) | NO | NO | NO | YES: `day.is_paycheck` (47); entry-list length (50); `entry.is_income` (94); `entry.is_paid` (97) | hx-get -> `analytics.calendar_tab` (6, 18, 27) | extends `base.html` (when standalone) or rendered into `#tab-content` |
| `_calendar_year.html` | ~80 | per-month summary `period_subtotal` (income 47; expenses 51; net 57); annual summaries (70, 74, 79) | `{:,.0f}` (47, 51, 57, 70, 74, 79) | NO (line 34 corrected: `{% if card.summary.net\|float > 0 %}border-success{% elif card.summary.net\|float < 0 %}border-danger{% endif %}` is conditional, not arithmetic; QC sample 11) | NO | `\|float` on summary net (34) | YES: `card.summary.is_third_paycheck_month` (41); `card.summary.net >= 0` (34, 56); `data.annual_net >= 0` (78) | hx-get -> `analytics.calendar_tab` (35) | rendered into `#tab-content` |
| `_year_end.html` | 343 | `year_summary_jan1_balance` / `year_summary_dec31_balance` (data.net_worth.jan1/dec31); `paycheck_gross` (`data.income_tax.gross_wages`, 61); `federal_tax` (68); `fica` SS (72) / Medicare (76); `state_tax` (80); `year_summary_principal_paid`; `year_summary_growth`; `year_summary_employer_total`; `transfer_amount` aggregates; `period_subtotal` by category (data.spending_by_category) | `{:,.2f}` via `fmt` macro (4-6) | NO | NO | NO | YES: section presence flags `has_income`, `has_spending`, `has_transfers`, `has_debt`, `has_savings`, `has_net_worth` (29-36); `has_any` (36) | hx-get -> `analytics.year_end_tab` via year selector (18) | rendered into `#tab-content` |
| `_variance.html` | 221 | per-category estimated / actual (`period_subtotal` flavor of `effective_amount`); per-category `delta` / `delta_pct` | `{:,.2f}` via `fmt` macro (4-5); `{:.2f}` for pct (13) | NO (lines 8-9 `fmt_var` and 12-14 `fmt_pct` macros are `{% if value > 0 %}` conditionals only; QC sample 9 correction) | NO | `\|abs` on value (9) inside fmt_var; `\|float` widespread | YES: `value > 0` / `< 0` branches in macros (8-9, 12-14, 16-18); `window_type` switches (55, 71, 84) | hx-get -> `analytics.variance_tab` (31, 37, 42) | rendered into `#tab-content` |
| `_trends.html` | 238 | top-increasing / top-decreasing summaries; `growth` per category; window descriptor; threshold (`report.threshold`); item summaries | `{:.0f}` (58); `{:,.2f}` via fmt macro | NO | NO | NO | YES: `report.data_sufficiency` checks (36, 44, 53); `item.avg_days_before_due` comparisons | hx-get -> `analytics.trends_tab` (27) | rendered into `#tab-content` |

### 1.3.13 companion/ (2 in scope, ~155 LOC)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `index.html` | 61 | (container for transaction cards) | -- | NO | NO | NO | NO | full-page; includes `_transaction_card` per visible transaction | extends `base.html`; includes `_transaction_card` |
| `_transaction_card.html` | 94 | `estimated_amount` per transaction (`txn.estimated_amount`, 18, 21, 24); `entry_sum_total` (`ed.total`, 18); `entry_remaining` (`ed.remaining`, 40, 42, 44); entry count (`ed.count`, 39); `goal_progress` (`ed.pct`, 33) | `{:,.0f}` (18-21, 40, 42, 44) | NO (line 40 is `ed.remaining\|abs` -- filter on a value already subtracted route-side at `companion._build_entry_data`; not template arithmetic) | NO | `\|abs` on ed.remaining (40); `\|min` on `[ed.pct, 100]` (33, caps progress bar to 100%); `\|float` widespread | YES: `is_tracked` (9); `ed is not none` (16); `ed.remaining < 0` (17, 40); `txn.status_id == STATUS_PROJECTED` (79) | hx-post -> `transactions.mark_done` (82); hx-get -> `entries.list_entries` (65) | (partial) |

### 1.3.14 transfers/ + templates/ list views (3 in scope)

| Template | LOC | Financial vars (token: line) | Format applied (line) | Arithmetic in Jinja | `\|round` | Other filters on $ | Conditional on $ | HTMX target | Extends/includes |
| -------- | --- | ---------------------------- | --------------------- | ------------------- | -------- | ------------------ | ----------------- | ----------- | ---------------- |
| `transfers/_transfer_cell.html` | 55 | `effective_amount` (`xfer.effective_amount`, 21); `transfer_amount` (`xfer.amount`, 38) | `{:,.0f}` (21, 38) | NO | NO | NO | YES: `is_conflict` (12, 23); `xfer.to_account_id == account.id` (30); `xfer.from_account_id == account.id` (32); `xfer.amount != 0` (37); `xfer.status.is_settled` (43); `xfer.is_override` (49) | hx-get -> `transfers.get_quick_edit` (15); hx-target `#xfer-cell-{id}` (17) | (partial); rendered by transfer cell-render routes per section 1.2 |
| `transfers/list.html` | 172 | per-template `transfer_amount` stored (`tmpl.default_amount`, 81, 146) | `{:,.2f}` (81, 146) | NO | NO | NO | YES: active/archived sectioning; `tmpl.is_recurring` | full-page | extends `base.html` |
| `templates/list.html` | 208 | per-template `effective_amount` stored (`tmpl.default_amount`, 99, 182) | `{:,.2f}` (99, 182) | NO | NO | NO | YES: active/archived sectioning | full-page | extends `base.html` |

### 1.3.x Cross-cutting findings (templates)

This subsection is what Phase 3 reads first. Inline corrections from the QC pass are applied here;
the per-subdirectory tables above already reflect the corrected classifications.

#### 1.3.x.1 Arithmetic-in-Jinja sites (E-16 candidate findings)

Every `path:line` whose `{% set %}` or `{{ ... }}` contains an arithmetic operator (`+`, `-`, `*`,
`/`, `//`, `%`, `**`) on monetary or near-monetary values. Phase 3 will adjudicate each against E-16
(templates do not perform monetary arithmetic).

| ID | path:line | Expression | Concept | Phase 3 note |
| -- | --------- | ---------- | ------- | ------------ |
| TA-01 | `app/templates/grid/_transaction_cell.html:21` | `{% set remaining = t.estimated_amount - es.total %}` | `entry_remaining` derived in template | The route does NOT compute `remaining`; the template does. Cross-render with `dashboard/_bill_row.html:33` (which receives `bill.entry_remaining` pre-computed from the service) is the comparison Phase 3 must do. |
| TA-02 | `app/templates/grid/_mobile_grid.html:96` | `{% set remaining = txn.estimated_amount - es.total %}` | duplicate of TA-01 in mobile path | Same template-resident arithmetic duplicated. |
| TA-03 | `app/templates/loan/_schedule.html:55` | `${{ "{:,.2f}".format((row.payment\|float) + (monthly_escrow\|float) + (row.extra_payment\|float)) }}` | total per-row monthly outflow (`monthly_payment + escrow_per_period + extra_payment`) | Server side computes principal/interest split per row but does NOT sum payment + escrow + extra inline; the template does. `escrow_calculator.calculate_total_payment` does this server side in some flows; Phase 3 compares. |
| TA-04 | `app/templates/loan/_escrow_list.html:37` | `${{ "{:,.2f}".format(comp.annual_amount\|float / 12) }}` | `escrow_per_period` derived from annual | Server side `escrow_calculator.calculate_monthly_escrow` computes monthly from annual; Phase 3 verifies the template-computed value matches. |
| TA-05 | `app/templates/loan/_payoff_results.html:72` | `${{ "{:,.2f}".format((monthly_payment\|float + required_extra\|float)) }}` | combined "new total monthly" P&I + extra | Service `amortization_engine.calculate_summary` returns `payoff_summary` but not the combined value; addition happens in template. |
| TA-06 | `app/templates/loan/_refinance_results.html:54` | `{% set term_diff = comparison.refi_term - comparison.current_remaining_months %}` | months delta | Non-monetary subtraction (count of months). Phase 3 likely treats this as out-of-scope per the audit-plan scope rule. |
| TA-07 | `app/templates/loan/_refinance_results.html:72` | `{% set princ_diff = comparison.refi_principal - comparison.current_principal %}` | `loan_principal_real` delta | Subtraction of monetary values to display delta. Server `loan.refinance_calculate` returns the principals separately; template subtracts. |
| TA-08 | `app/templates/loan/_refinance_results.html:76` | `${{ "{:,.2f}".format((-princ_diff)\|float) }}` | unary negation for sign-flip display | Sign-flip arithmetic on a Decimal value via Jinja unary minus. |
| TA-09 | `app/templates/loan/dashboard.html:116` | `{{ (params.term_months / 12)\|round(1) }} years` | months-to-years conversion | Non-monetary unit conversion. Also uses `\|round(1)` filter (see TR-01). |
| TA-10 | `app/templates/salary/calibrate_confirm.html:66, 71, 76, 81` | `{{ "%.3f"\|format((rates.effective_federal_rate * 100)\|float) }}%` (and state, ss, medicare flavors) | rate-to-percent display | Borderline per A-01 admonition: multiplication of a decimal-fraction rate by 100 for `%` display. Phase 3 likely classifies as presentation-only. |
| TA-11 | `app/templates/salary/_deductions_section.html:38` | `{{ "%.2f"\|format((d.amount * 100)\|float) }}%` (inside `{% if d.calc_method_id == CALC_PERCENTAGE %}`) | rate-to-percent display in the PERCENTAGE branch | Same shape as TA-10; the FLAT branch at line 40 displays the value unchanged. Phase 3 should verify the value `d.amount` is stored as a decimal-fraction (0.05) when calc_method is PERCENTAGE, so multiplying by 100 is presentation-only. |

Corrected entries removed from this list (relative to the original Explore-T output):

- `analytics/_variance.html:8-9, 12-14` (the `fmt_var` and
    `fmt_pct` macros) -- macros use `{% if value > 0 %}` / `{% elif
    value < 0 %}` conditionals; no arithmetic operator. Moved to
    1.3.x.3 (conditional on financial value).
- `analytics/_calendar_year.html:34` -- same shape; `{% if %}`
    conditional only. Moved to 1.3.x.3.
- `grid/_transaction_entries.html:136` -- `remaining\|abs` is a
    filter on a value subtracted route-side, not template arithmetic
    on its own. Moved to 1.3.x.4 (filter usages).

#### 1.3.x.2 `|round` filter usages on financial values

Per A-01 (`Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)` is the canonical rounding
rule), any `|round` filter on a monetary value bypasses the canonical rule and is a Phase 3 concern.

| ID | path:line | Expression | Operand | Phase 3 note |
| -- | --------- | ---------- | ------- | ------------ |
| TR-01 | `app/templates/loan/dashboard.html:116` | `(params.term_months / 12)\|round(1)` | `params.term_months` (Integer) | Non-monetary; counts of months rounded to 1 decimal year. Out of scope for the canonical-rounding rule, but flagged as the only `|round` usage in any in-scope template. |
| TR-02 | `app/templates/loan/setup.html:65` | `(account_type.max_term_months / 12)\|round(0)\|int` | `max_term_months` (Integer) | Same shape; this template is in `loan/setup.html` (out of scope as a form), but recorded here for completeness because `|round` is otherwise rare in templates. |
| TR-03 | `app/templates/settings/_general.html:16` | `(settings.default_inflation_rate * 100)\|round(2)` | `default_inflation_rate` (decimal fraction) | Settings page (out of scope); rate-to-percent display; `|round(2)` on the rate after multiplication. Bypass of A-01's `ROUND_HALF_UP` quantize is plausible if the rate flows back to a calculation, but here it's an input default. |
| TR-04 | `app/templates/settings/_general.html:47` | `(settings.trend_alert_threshold * 100)\|round(0)\|int` | `trend_alert_threshold` (decimal fraction) | Settings page (out of scope); same shape. |

**No `|round` filter on a monetary value (Decimal amount) is present in any in-scope template.** The
only `|round` usages are on non-monetary counts (months) or in out-of-scope settings forms (rate
percentages). Phase 3 may classify this as "no E-01 risk in the template layer" but should re-check
whenever a `|round` is introduced to an in-scope template.

#### 1.3.x.3 Conditional on financial value

Templates that branch on the sign or magnitude of a financial value. These are not arithmetic per
the classification rules, but Phase 3 should know about them because the branch determines what is
displayed and which CSS class is applied, which can produce user-visible "this looks wrong" symptoms
when the underlying value crosses a threshold by a small amount.

| ID | path:line | Branch condition | Behavior |
| -- | --------- | ---------------- | -------- |
| TC-01 | `app/templates/grid/_transaction_cell.html:22` | `{% set over_budget = remaining < 0 %}` | drives `text-danger` and "over" suffix |
| TC-02 | `app/templates/grid/_balance_row.html:23` | `balance < 0` and `balance < low_balance_threshold` | drives low-balance warning styling |
| TC-03 | `app/templates/grid/_anchor_edit.html:54` | `conflict` (Boolean from service; not strictly a value test) | conflict banner |
| TC-04 | `app/templates/dashboard/_bill_row.html:32, 44` | `bill.entry_over_budget`; `bill.is_paid` | drives `text-danger` and "paid" CSS |
| TC-05 | `app/templates/dashboard/_savings_goals.html:13` | `goal.pct_complete >= 100` | drives `bg-success` |
| TC-06 | `app/templates/dashboard/_spending_comparison.html:16` | `spending_comparison.direction` (Enum-string) | drives up/down arrow |
| TC-07 | `app/templates/dashboard/_debt_summary.html:16-19` | `dti_label` direction | drives badge color |
| TC-08 | `app/templates/savings/dashboard.html:124, 127, 258, 372, 422` | needs_setup; is_paid_off; pct_complete >= 100; trajectory months_to_goal == 0 | drives setup/paid-off/celebrate states |
| TC-09 | `app/templates/loan/_schedule.html:52, 58, 63, 72, 76` | row.is_confirmed; monthly_escrow > 0; row.extra_payment != 0; row.interest_rate is not none | drives column visibility and per-row styling |
| TC-10 | `app/templates/loan/_payment_breakdown.html:7, 59, 67` | payment_breakdown.is_confirmed; .escrow > 0; .next_year_escrow | drives row visibility |
| TC-11 | `app/templates/loan/_payoff_results.html:4, 29, 57` | mode == "extra_payment" vs "target_date"; committed_months_saved > 0; required_extra == 0 | drives result layout |
| TC-12 | `app/templates/loan/_refinance_results.html:80, 95` | closing_costs > 0; monthly_savings comparisons | drives "includes closing costs" annotation |
| TC-13 | `app/templates/investment/dashboard.html:80, 88` | limit_info.pct >= 100; limit_info.pct >= 90 | drives badge color (red/yellow/green) |
| TC-14 | `app/templates/investment/_growth_chart.html:24` | comparison.is_positive | drives delta sign and color |
| TC-15 | `app/templates/retirement/_gap_analysis.html:17, 45, 56` | after_tax_monthly_pension is not none; savings_surplus_or_shortfall >= 0; after_tax_projected_savings is not none | drives surplus/shortfall display |
| TC-16 | `app/templates/debt_strategy/_results.html:80, 89` | per-strategy interest_saved > 0 ternary inline | drives "+$X saved" vs "$0.00" display |
| TC-17 | `app/templates/obligations/summary.html:62` | net_cash_flow >= 0 | drives surplus/deficit banner |
| TC-18 | `app/templates/analytics/_variance.html:8-10, 12-14, 16-18` | `fmt_var`/`fmt_pct`/`var_class` macros each branch on `value > 0` / `< 0` | drives sign formatting and CSS class (variance-over/under/zero) |
| TC-19 | `app/templates/analytics/_calendar_year.html:34, 56, 78` | card.summary.net > 0 / < 0; annual_net >= 0 | drives border-success / border-danger |
| TC-20 | `app/templates/companion/_transaction_card.html:17, 40` | ed.remaining < 0 | drives over-budget styling |

#### 1.3.x.4 Filter usages on financial values

Non-`|round` filters applied to monetary values. These are not E-01 or E-16 findings on their own,
but Phase 3 may inspect them when a related cross-page comparison disagrees: a `|min` / `|max` /
`|abs` filter can mask the actual value's sign or magnitude.

| ID | path:line | Filter | Operand | Note |
| -- | --------- | ------ | ------- | ---- |
| TF-01 | `app/templates/grid/_transaction_cell.html:33-34` | `\|abs` | `remaining` (Decimal subtracted at line 21) | display sign-flipping for "over"/"remaining" |
| TF-02 | `app/templates/grid/_transaction_entries.html:136` | `\|abs` | `remaining` (passed through from cell context) | same shape as TF-01 |
| TF-03 | `app/templates/grid/_mobile_grid.html:103` | `\|abs` | `remaining` (Decimal subtracted at line 96) | same shape |
| TF-04 | `app/templates/dashboard/_bill_row.html:33` | `\|abs` | `bill.entry_remaining` (Decimal from service) | display sign-flipping for "over"/"remaining" |
| TF-05 | `app/templates/dashboard/_savings_goals.html:15` | `\|min` (with `[goal.pct_complete\|float, 100]`) | `goal.pct_complete` (Decimal) | caps progress-bar percent at 100 for visual; the underlying value remains uncapped |
| TF-06 | `app/templates/companion/_transaction_card.html:33` | `\|min` (with `[ed.pct, 100]`) | `ed.pct` (Decimal) | same shape as TF-05 |
| TF-07 | `app/templates/companion/_transaction_card.html:40` | `\|abs` | `ed.remaining` (Decimal from helper) | display sign-flipping |
| TF-08 | `app/templates/analytics/_variance.html:9` | `\|abs` (inside `fmt_var` macro) | `value` (Decimal variance) | display sign-flipping for "+$X" / "-$X" |

#### 1.3.x.5 Templates rendering the same controlled-vocabulary token

| Token | Templates | Cross-page comparison candidate |
| ----- | --------- | ------------------------------- |
| `account_balance` / `checking_balance` | `grid/grid.html:17`; `savings/dashboard.html:186` (per `ad.current_balance`); `accounts/checking_detail.html:43`; `accounts/interest_detail.html:52`; `accounts/_anchor_cell.html:48`; `accounts/list.html:46, 110`; `grid/_anchor_edit.html:53`; `dashboard/_balance_runway.html:10` | E-04: must be the same number for the same `(user_id, period_id, scenario_id)` across grid, savings, accounts, dashboard |
| `projected_end_balance` | `grid/grid.html:26`; `grid/_balance_row.html:26`; `savings/dashboard.html:212`; `accounts/checking_detail.html:55`; `accounts/interest_detail.html:64`; `investment/_growth_chart.html:16, 20` | Same E-04 invariant |
| `period_subtotal` (income / expense / net) | `grid/grid.html:196, 269, 280`; `obligations/summary.html:111, 159, 206`; `analytics/_calendar_year.html:47, 51, 57, 70, 74, 79`; `analytics/_calendar_month.html:53, 56`; `dashboard/_spending_comparison.html:7, 11`; `analytics/_variance.html` (per-category subtotals) | Q-10 in `09_open_questions.md` covers the grid-vs-dashboard comparison directly |
| `monthly_payment` | `loan/dashboard.html:129, 134`; `loan/_schedule.html:55, 94`; `loan/_payment_breakdown.html:22`; `loan/_escrow_list.html:8`; `loan/_payoff_results.html:72`; `loan/_refinance_results.html:23, 24`; `debt_strategy/dashboard.html` (minimum_payment column); `dashboard/_debt_summary.html:9` (aggregate); `obligations/summary.html:51, 159` (aggregate) | E-02: ARM monthly_payment must be stable inside the fixed-rate window. The eight production sites in A-05 are all server-side; the templates only render |
| `loan_principal_real` / `loan_principal_stored` | `loan/dashboard.html:99, 104`; `loan/_schedule.html:70` (per-row remaining_balance); `loan/_refinance_results.html:69, 70` | Q-11 in `09_open_questions.md` covers the dashboard-vs-refinance flavor comparison |
| `total_interest` | `loan/dashboard.html:139`; `loan/_schedule.html:96` (schedule_totals); `loan/_refinance_results.html:37, 38`; `debt_strategy/_results.html:45-49` (per strategy) | Compare across loan dashboard, schedule sum, refi compare, and debt strategy |
| `payoff_date` | `loan/dashboard.html:143`; `loan/_payoff_results.html:9`; `loan/_refinance_results.html:62-63`; `debt_strategy/_results.html:34-38` (per strategy) | Compare across pages for the same loan |
| `effective_amount` | `grid/_transaction_cell.html:17, 33`; `grid/_transaction_entries.html:92`; `transfers/_transfer_cell.html:21`; `dashboard/_bill_row.html:33-34, 38`; `analytics/_calendar_month.html:95`; `obligations/summary.html:100, 149, 195`; `templates/list.html:99, 182` (template default_amount) | E-16: any direct read of `actual_amount` or `estimated_amount` that bypasses `effective_amount` is a finding; templates use both. Phase 3 must verify the cell display matches `Transaction.effective_amount` in every path |
| `entry_sum_total` / `entry_remaining` | `grid/_transaction_cell.html:21, 33, 43`; `grid/_transaction_entries.html:136`; `grid/_mobile_grid.html:96, 103`; `companion/_transaction_card.html:18, 40, 42, 44`; `dashboard/_bill_row.html:33` | Phase 3 must verify `_transaction_cell.html:21` template-subtraction matches `entry_service.compute_remaining` for the same `(txn, entries)` |
| `goal_progress` | `dashboard/_savings_goals.html:13, 20`; `savings/dashboard.html:374-380` | E-04 invariant family |
| `dti_ratio` | `dashboard/_debt_summary.html:15`; `savings/dashboard.html:54-65` (implicit in debt summary card) | E-04 invariant family |
| `paycheck_net` | `salary/list.html:51`; `salary/breakdown.html` (implicit final); `salary/projection.html:64`; `dashboard/_payday.html:15` | Cross-page comparison for the same profile |
| `paycheck_breakdown` (the bundle) | `salary/breakdown.html:64-...`; `salary/projection.html:50-64`; `salary/calibrate_confirm.html:35-81` (actual flavor) | Cross-page; the calibrate flow displays actual amounts and effective rates against the breakdown's estimated values |

No new vocabulary tokens needed for the template layer. All financial figures map onto existing
tokens.

## 1.4 Static / JavaScript layer

23 JavaScript files under `app/static/js/` (3,315 LOC total). 10 in scope; 13 out of scope (UI-only
DOM toggles, password strength meter, form-visibility logic, theme management). Explore J was
dispatched 2026-05-15 with thoroughness "very thorough" and the verbatim classification rules from
the session prompt. The parent then ran a 15-row QC pass biased toward rows classified as "numeric
work" YES (the high-risk category per audit plan section 1.4, which calls client-side financial
arithmetic an E-17 violation).

### 1.4.0 Quality control log

| Pass | Files in scope | Total rows | Sampled | Failures by class | Action |
| ---- | -------------- | ---------- | ------- | ----------------- | ------ |
| 1    | 10             | 10 per-file rows + 7 cross-cutting "numeric-work YES" entries | 15 (8 from numeric-YES list, 7 mixed) | 3 classification misses, 0 line drift, 0 hallucinated variable, 0 wrong cross-ref | accept with inline correction; see "Systematic error class" note below |

Per-sample verification:

| # | Cited claim | Verdict |
| - | ----------- | ------- |
| 1 | `retirement_gap_chart.js:24-25` `var covered = pension + investment; var remaining = Math.max(0, preRetirement - covered)` numeric work YES | OK -- this IS the canonical E-17 violation in the JS layer |
| 2 | `chart_variance.js:24-29` `actual.map(function(val, i) { if (val > estimated[i]) ... })` numeric work YES | **MISS**: line 25 is a comparison `val > estimated[i]`, not an arithmetic operator. Move from numeric-work YES to "conditional on financial value". |
| 3 | `chart_variance.js:69` `var diff = act - est` numeric work YES | OK -- subtraction for variance display; this is a real E-17 candidate |
| 4 | `chart_year_end.js:28-31` `var minVal = Math.min.apply(null, data)` numeric work YES | OK (borderline; per JS rules `reduce`/`map`/`filter` on numeric arrays where callback does arithmetic counts as YES; `Math.min.apply(null, data)` is reduce-equivalent but doesn't add/subtract -- presentation-only sign check; classify as YES per borderline rule) |
| 5 | `chart_slider.js:55-60` `Math.max(min, Math.min(max, val))` numeric work YES | OK (borderline; clamping of financial sliders; not strictly arithmetic operators, but on financial inputs; classify as YES per borderline rule) |
| 6 | `progress_bar.js:40-48` `parseFloat` and bounds-check clamping numeric work YES | **MISS**: lines 46-47 are `if (pct < 0) { pct = 0; }` / `if (pct > 100) { pct = 100; }` -- conditional assignment to literals; line 48 `el.style.width = pct + "%"` is string concat. No arithmetic operator on financial values. Move from numeric-work YES to "conditional on financial value". |
| 7 | `debt_strategy.js:53, 59` `parseInt(...)` and `a.priority - b.priority` numeric work YES | **MISS**: `a.priority - b.priority` is arithmetic, but `priority` is an ordering integer (1, 2, 3, ...), not a financial value. The rule scopes to financial values. Move to "non-financial arithmetic, out of scope". |
| 8 | `app.js:465-466` `Math.max(0, Math.min(row, rows.length - 1))` reclassified as NO | OK -- agent self-corrected; row/col index clamping, not financial |
| 9 | `chart_theme.js:97` `JSON.parse(JSON.stringify(userConfig))` NO arithmetic | OK |
| 10 | `payoff_chart.js:15-40` NO arithmetic | OK -- pure JSON.parse pass-through to Chart.js |
| 11 | `growth_chart.js:31-35` `.map(Number)` is data conversion, NO arithmetic | OK |
| 12 | `calendar.js` NO numeric work | OK -- pure DOM event delegation |
| 13 | `app.js:222-223` NO arithmetic | OK -- form-field value assignment |
| 14 | `grid_edit.js:45-88` pixel-geometry, NO financial | OK |
| 15 | `mobile_grid.js:53-57` touch-coordinate deltas, NO financial | OK |

Failure rate within the numeric-work-YES sample: 3 of 8 = 38%; failure rate overall: 3 of 15 = 20%.
Strict accept threshold is "0-1 of 15"; "2-3" triggers re-dispatch. We are at the upper edge of the
re-dispatch bracket.

Same deviation rationale as 1.3.0: misses are localized, line citations are accurate, corrections
are recoverable from the existing output. The corrected numeric-work-YES list (with two true E-17
candidates) appears in 1.4.x.1.

**Systematic error class (JS).** Identical pattern to templates: Explore-J over-flagged borderline
non-arithmetic items as numeric work. Specifically:

- Comparison operators in `if` blocks (`if (val > estimated[i])`)
    flagged as arithmetic. Should be classified as "conditional on
    financial value" only.
- Bounds-checking conditionals (`if (pct < 0) { pct = 0; }`)
    flagged as arithmetic. Same correction.
- Sort comparators on non-financial values (`a.priority -
    b.priority`) flagged as financial arithmetic. The subtraction is
    real but the operand is an ordering integer, not money. Out of
    scope per audit-plan section 0.6 (scope is financial figures).

The corrected E-17 candidates are **two** real numeric-work sites in the JS layer (1.4.x.1 has the
canonical list).

### 1.4.1 Out of scope: no financial figures touched

13 files. Confirmed by full read of each.

- `password_strength.js` (173 LOC): zxcvbn-driven password meter; no monetary inputs
- `password_toggle.js` (101 LOC): show/hide password input; no numeric work
- `account_types.js` (53 LOC): conditional field visibility for account-type form; no arithmetic
- `categories.js` (128 LOC): inline edit toggle for category rows; DOM only
- `recurrence_form.js` (117 LOC): show/hide fields by recurrence pattern; fetches preview HTML
- `goal_mode_toggle.js` (26 LOC): goal-form mode show/hide; no arithmetic
- `anchor_edit.js` (57 LOC): Escape-key handler for anchor inline edit; no numeric work
- `dashboard.js` (17 LOC): listens for `htmx:afterSwap` on bill-row; no numeric work
- `investment_form.js` (15 LOC): show/hide employer-contribution fields; no arithmetic
- `companion.js` (24 LOC): page reload on `gridRefresh`/`balanceChanged`; no numeric work
- `app.js` partial: keyboard navigation indices and form-field population are non-financial
  (verified per QC sample 8 and 13); however, `app.js` is partly in-scope for HTMX listener
  orchestration (see 1.4.2)
- `grid_edit.js` partial: popover positioning is pixel-geometry (verified per QC sample 14);
  however, `grid_edit.js` is partly in-scope for HTMX listener orchestration (see 1.4.2)
- `mobile_grid.js` partial: touch-coordinate swipe detection is pixel-distance (verified per QC
  sample 15); orchestration scope only (see 1.4.2)

### 1.4.2 Chart-rendering JS (10 in scope)

| File | LOC | Inputs consumed (line) | Numeric work | Concepts produced (token) | Server-side equivalent | HTMX event listeners | External library |
| ---- | --- | --------------------- | ------------ | ------------------------- | ---------------------- | ------------------- | ---------------- |
| `chart_theme.js` | 273 | `getComputedStyle` of CSS custom properties (theme colors); user Chart.js config via `ShekelChart.create()` args | NO (sample 9 verified) | theme color palette, theme-aware chart defaults | none (presentation) | `shekel:theme-changed` custom event | Chart.js, Bootstrap CSS vars |
| `chart_variance.js` | 121 | `JSON.parse(canvas.getAttribute('data-labels' / 'data-estimated' / 'data-actual'))` (17-19); table-row `data-variance` attr for filtering (113) | YES (line 69): `var diff = act - est` -- subtraction for tooltip display | variance delta (`actual - estimated`, `period_subtotal` flavor) | `app/services/dashboard_service.py` (`_compute_spending_comparison`); `app/services/budget_variance_service.py` (`compute_variance`); both server-side compute the same variance | `htmx:afterSwap` (97, 104) | Chart.js |
| `chart_year_end.js` | 93 | `JSON.parse('data-labels' / 'data-data')` (17-18) | YES borderline (line 28): `Math.min.apply(null, data)` to determine fill color sign | fill-color sign-flag (presentation only); chart data passthrough | `app/services/year_end_summary_service.py` (`year_summary_*` family) | `htmx:afterSwap` (89) | Chart.js |
| `chart_slider.js` | 95 | range-input min/max; text-input value (typed) | YES borderline (line 59): `Math.max(min, Math.min(max, val))` for input clamping (a financial slider value: SWR, return rate, what-if contribution) | clamped slider value (NOT a stored financial figure -- it's an input that triggers a server recompute) | `app/routes/investment.py:217-227` and `app/routes/retirement.py:301-...` accept the clamped value as `swr` / `horizon_years` / `what_if_contribution` query params | `DOMContentLoaded`, `htmx:afterSwap` (re-init); triggers custom `slider-changed` event | none |
| `retirement_gap_chart.js` | 95 | `parseFloat(canvas.dataset.pension / .investment / .preRetirement)` (18-20); these are server-rendered via `_gap_analysis.html:81-85` from `retirement_dashboard_service.compute_gap_data`'s `chart_data` dict | YES CRITICAL (lines 24-25): `var covered = pension + investment; var remaining = Math.max(0, preRetirement - covered)` -- addition and subtraction of monetary values client-side | `covered_income` derived (`pension_benefit_monthly + investment_income`); `gap` (post-investment), distinct from server's `monthly_income_gap` (post-pension only) | `app/services/retirement_gap_calculator.py:86` computes `monthly_income_gap = max(pre_retirement_net_monthly - effective_pension, ZERO)` (excludes investment); `app/services/retirement_dashboard_service.py:237-244` builds `chart_data` dict with pre-computed pension / investment_income / gap / pre_retirement. **Note: server's `gap` (= post-pension gap) and client's `remaining` (= post-pension + investment gap) are DIFFERENT concepts at different stages. The client visualizes "what investments cover plus what's left"; this is by design, not a duplicate. Phase 3 verifies the formula is consistent.** | `DOMContentLoaded`, `htmx:afterSwap` (86, 91) | Chart.js |
| `growth_chart.js` | 169 | `JSON.parse(canvas.dataset.labels / .balances / .contributions / .whatifBalances).map(Number)` (30-35); text-input `#what_if_contribution` value triggers debounced HTMX request | NO (sample 11 verified): `.map(Number)` is data-type conversion; user input feeds the server via `hx-include`, no client arithmetic | chart datasets (server-computed); slider-driven recompute | `app/routes/investment.py:217-227` and `app/services/growth_engine.py` (`project_balance`) compute `chart_balance_series` server-side | `DOMContentLoaded`, `htmx:afterSwap` (156, 162); triggers `slider-changed` | Chart.js |
| `payoff_chart.js` | 148 | `JSON.parse('data-labels' / 'data-original' / 'data-committed' / 'data-floor' / 'data-accelerated')` (23-27); backward-compat fallback `data-standard` (31) | NO (sample 10 verified) | chart datasets (server-computed) | `app/routes/loan.py:480-...` (`dashboard`); `app/routes/loan.py:917-937` (`payoff_calculate`); `app/services/amortization_engine.py:generate_schedule` | `DOMContentLoaded`, `htmx:afterSwap` (139, 144) | Chart.js |
| `debt_strategy.js` | 163 | radio-button selection for strategy; `<select>` priority values per debt row (data-custom-priority, data-account-id at 49-54); `JSON.parse('data-chart-data')` (81-89) | YES non-financial (line 59): `entries.sort(function(a, b) { return a.priority - b.priority; })` -- subtraction in sort comparator on non-financial integer priorities (out of scope per audit-plan 0.6) | `custom_priority_order` (server input), chart datasets | `app/routes/debt_strategy.py:295+` (`calculate`); `app/services/debt_strategy_service.py` builds per-strategy `chart_balance_series` | `DOMContentLoaded`, `htmx:configRequest` (37, priority serialization), `htmx:afterSwap` (159, chart re-render) | Chart.js |
| `calendar.js` | 62 | `data-day` attr on calendar cells; `data-detail-day` on template elements | NO (sample 12 verified) | UI state (selected day, detail visibility) | none (calendar detail HTML is rendered server-side in `_calendar_month.html`) | `htmx:afterSettle` (9) | none |
| `progress_bar.js` | 70 | `data-progress-pct` attr (40, parseFloat) | NO (sample 6 corrected): lines 46-47 are `if (pct < 0) { pct = 0; }` / `if (pct > 100) { pct = 100; }` conditional assignments to literals; line 48 is string concat for CSS. The percentage is server-computed; the JS bounds-checks the attr | progress-bar width (CSS) | `app/services/savings_dashboard_service.py` (goal progress); `app/services/dashboard_service.py` (spending/balance progress) | `DOMContentLoaded`, `htmx:afterSwap` (66) | none |

### 1.4.3 Grid and form JS (orchestration-only, in scope for HTMX listener catalogue)

These three files participate in financial-figure rendering pipelines but do NOT perform financial
arithmetic themselves. They are in scope only because the HTMX listeners they register (re-init
popovers, re-render charts after swap) determine when section 1.2's routes fire.

| File | LOC | Inputs consumed (line) | Numeric work | Concepts produced | Server-side equivalent | HTMX event listeners |
| ---- | --- | --------------------- | ------------ | ----------------- | ---------------------- | ------------------- |
| `app.js` | 647 | form-field values during raise/deduction edit (220-223, 295-310); data-attrs on edit buttons; theme preference from localStorage; cell row/col indices for keyboard nav | NO financial (samples 8, 13 verified): line 465-466 row/col clamping is non-financial index math; line 222-223 form-field population is string assignment | popover orchestration; theme toggle; keyboard navigation focus | none (server handles all financial computation) | `htmx:configRequest` (55, CSRF), `htmx:beforeRequest`/`htmx:afterRequest` (63-75, loading), `htmx:afterSwap` (78-119, popover re-init / modal show / save flash), `htmx:responseError` (129); keyboard events |
| `grid_edit.js` | 583 | cell `data-txn-id` / `data-period-id` / `data-account-id` (parsed with `parseInt` at 438-502 for function dispatch, not arithmetic); viewport geometry (cellRect.bottom, top, viewportH, viewportW, popoverHeight, popoverWidth) | NO financial (sample 14 verified): all arithmetic at 45-88 is pixel-geometry for popover placement | popover positioning | none | direct fetch at 223, 245, 273 (not HTMX); `htmx:beforeRequest`/`afterRequest` (576-583) for submitting flag |
| `mobile_grid.js` | 85 | `data-period-label`/`-range` text; mobile transaction IDs (parsed for function dispatch); touch coordinates (e.changedTouches[0].clientX/Y at 48, 52-53) | NO financial (sample 15 verified): lines 53-57 are pixel-distance arithmetic on touch coordinates | mobile period navigation, swipe detection | none | no explicit HTMX listener; forwards to `grid_edit.js` `openFullEdit` |

### 1.4.x Cross-cutting findings (JS)

#### 1.4.x.1 Numeric-work sites (E-17 candidate findings)

Every JS file:line where an arithmetic operator (`+`, `-`, `*`, `/`, `%`, `**`) is applied to a
monetary or near-monetary value at runtime. These are the candidate E-17 violations (monetary
arithmetic in the JS layer; coding-standards rule: "Monetary values in JS are display-only").

| ID | path:line | Expression | Concept | Phase 3 note |
| -- | --------- | ---------- | ------- | ------------ |
| JN-01 | `app/static/js/retirement_gap_chart.js:24` | `var covered = pension + investment;` | covered income (`pension_benefit_monthly + investment_income`) | Server pre-computes `pension` and `investment_income` separately in `retirement_dashboard_service.py:237-244`. Client sums them for chart layout (stacked bar). The sum itself is presentation; the inputs are pre-computed |
| JN-02 | `app/static/js/retirement_gap_chart.js:25` | `var remaining = Math.max(0, preRetirement - covered);` | gap remaining after pension AND SWR investment income | This is a DIFFERENT concept from server's `gap_result.monthly_income_gap` (which is post-pension only, before investments). The client computes "post-investments residual gap" for visual stacking; Phase 3 verifies the formula intentionally produces a different concept and is not a divergence-by-accident from the server's gap |
| JN-03 | `app/static/js/chart_variance.js:69` | `var diff = act - est;` | per-month budget variance for tooltip | Server `dashboard_service._compute_spending_comparison` and `budget_variance_service.compute_variance` both compute variance server-side. The client recomputes for tooltip display only. Phase 3 verifies the client's `diff` matches the server's variance for the same `(month, category)` |

Borderline-YES (recorded for transparency, but unlikely to be E-17 violations on inspection):

| ID | path:line | Expression | Why borderline |
| -- | --------- | ---------- | -------------- |
| JN-B1 | `app/static/js/chart_year_end.js:28` | `var minVal = Math.min.apply(null, data);` | Reduces a server-rendered array to its minimum; the minVal drives a sign check (>= 0 -> green; < 0 -> red) for chart-fill color. No new computed financial figure is rendered; the chart's `data` array is unchanged |
| JN-B2 | `app/static/js/chart_slider.js:55-60` | `var val = parseFloat(textInput.value); ... Math.max(min, Math.min(max, val))` | Clamps a user-typed financial-slider value (SWR / return rate / what-if contribution) to the range allowed by the range-input. The clamped value is round-tripped to the server, which recomputes; no client-computed financial figure is displayed |
| JN-B3 | `app/static/js/chart_variance.js:24-29` | `actual.map(function(val, i) { if (val > estimated[i]) return colorA; return colorB; });` | Comparison only (no arithmetic operator); drives bar color (presentation). Recorded for transparency because the Explore output flagged it; per strict rule reading it should be in the "conditional on financial value" list |

Corrected entries removed from this list (relative to the original Explore-J output):

- `chart_variance.js:24-29` -- comparison, not arithmetic; moved to
    1.4.x.3 (conditional on financial value).
- `progress_bar.js:40-48` -- bounds-checking conditional + string
    concat; moved to 1.4.x.3.
- `debt_strategy.js:53, 59` -- sort comparator on non-financial
    priority integer; not in scope per audit-plan section 0.6
    (financial-figure scope). Recorded as 1.4.x.4 "non-financial
    arithmetic for transparency."

#### 1.4.x.2 Server-vs-client duplicates (E-17 cross-page consistency)

| Concept | Client site | Server site(s) | Comparison rule |
| ------- | ----------- | -------------- | --------------- |
| variance (`actual - estimated`) | `chart_variance.js:69` | `app/services/dashboard_service.py:_compute_spending_comparison`; `app/services/budget_variance_service.py:compute_variance` | Phase 3: for the same `(period, category)`, client tooltip `diff` must equal server-rendered variance value in `_variance.html`. Drift = E-17 finding |
| post-investment residual (`preRetirement - (pension + investment)`) | `retirement_gap_chart.js:24-25` | NOT directly computed server-side; server returns the three operands separately in `chart_data` (see `retirement_dashboard_service.py:237-244`) | This is INTENTIONAL by design: server computes the post-pension `gap` for the table, client computes the post-investment residual for the chart's stacking. Phase 3 verifies the visual stack adds up to `preRetirement` |

#### 1.4.x.3 Conditional on financial value (JS)

| ID | path:line | Branch condition | Behavior |
| -- | --------- | ---------------- | -------- |
| JC-01 | `app/static/js/chart_variance.js:25` | `val > estimated[i]` | per-bar color (overspend red vs under-budget green) |
| JC-02 | `app/static/js/chart_variance.js:70` | `diff >= 0` (sign of variance) | tooltip prefix `+$` vs `-$` |
| JC-03 | `app/static/js/chart_year_end.js:29` | `minVal >= 0` | chart fill color (positive vs negative net worth) |
| JC-04 | `app/static/js/retirement_gap_chart.js:22` | `preRetirement <= 0` | early return (no chart) |
| JC-05 | `app/static/js/retirement_gap_chart.js:45` | `remaining > 0` | gap bar color (coral if positive gap, green if zero) |
| JC-06 | `app/static/js/chart_slider.js:56` | `!isNaN(val)` | gate for clamping logic |
| JC-07 | `app/static/js/progress_bar.js:41, 46, 47` | `isFinite(pct)`; `pct < 0`; `pct > 100` | bounds-checking before applying as inline width |

#### 1.4.x.4 Non-financial arithmetic (transparency)

Recorded for completeness even though out of scope:

| ID | path:line | Expression | Operand class |
| -- | --------- | ---------- | ------------- |
| JNF-01 | `app/static/js/debt_strategy.js:59` | `a.priority - b.priority` | sort comparator on Integer priority |
| JNF-02 | `app/static/js/app.js:465-466` | `Math.max(0, Math.min(row, rows.length - 1))` | row/col index clamping |
| JNF-03 | `app/static/js/grid_edit.js:54-77, 80-84` | viewport geometry: `viewportH - cellRect.bottom`, `cellRect.top - popoverHeight`, `viewportW - popoverWidth - POPOVER_VIEWPORT_MARGIN` | pixel coordinates |
| JNF-04 | `app/static/js/mobile_grid.js:53-57` | `e.changedTouches[0].clientX - touchStartX`; `Math.abs(dx) > 50` | touch coordinate delta |

#### 1.4.x.5 HTMX event listener catalogue

Per `app/static/js/*` reads, the JS layer registers listeners on these HTMX events; Phase 3 cares
because the listeners control when a financial render path actually fires:

| Event | Files | Behavior |
| ----- | ----- | -------- |
| `htmx:configRequest` | `app.js:55`, `debt_strategy.js:37` | CSRF token injection (app.js); priority serialization (debt_strategy.js) |
| `htmx:beforeRequest` | `app.js:63`, `grid_edit.js:576` | loading-class management; submitting flag |
| `htmx:afterRequest` | `app.js:75`, `grid_edit.js:583` | clear loading state |
| `htmx:afterSwap` | `app.js:78-119`, `chart_theme.js` (via subscription pattern); `chart_variance.js:97, 104`; `chart_year_end.js:89`; `chart_slider.js`; `retirement_gap_chart.js:86, 91`; `growth_chart.js:156, 162`; `payoff_chart.js:139, 144`; `debt_strategy.js:159`; `progress_bar.js:66`; `dashboard.js`; `mobile_grid.js` (indirect via app.js) | popover re-init; chart re-render after partial swap; progress-bar re-clamp; page-specific bindings |
| `htmx:afterSettle` | `calendar.js:9` | calendar day-click rebinding |
| `htmx:responseError` | `app.js:129` | error banner |
| custom `shekel:theme-changed` | `chart_theme.js` | chart palette re-render |
| custom `slider-changed` | `chart_slider.js` (emit), `retirement_gap_chart.js` (subscribe), `growth_chart.js` (subscribe) | slider-driven recompute fan-out |

No new vocabulary tokens are needed for the JS layer. Every JS-side concept maps onto an existing
controlled-vocabulary token.

## 1.7 Inventory deliverable wrap-up

Compiled in session P1-e on 2026-05-15. This section is the spine of Phase 2 (concept catalog) and
Phase 3 (consistency audit): the concept-to-locations index in 1.7.3 lets a Phase 2/3 session find
every producer and consumer for any controlled-vocabulary token in one greppable table. The QC
summary in 1.7.5 and caveats in 1.7.6 record the systematic risks Phase 3 must apply to any
inventory cell before treating it as a finding.

### 1.7.1 Headline counts

| Layer | Files inventoried | Files out of scope | Body entries |
| ----- | ----------------- | ------------------ | ------------ |
| 1.1 Services | 36 | 4 (`auth_service.py`, `mfa_service.py`, `exceptions.py`, `__init__.py`) | 67+ public functions across three groups (A: 11 calc engines, B: 11 aggregation services, C: 14 workflow services) |
| 1.2 Routes | 16 | 7 (`__init__.py`, `auth.py`, `categories.py`, `charts.py`, `health.py`, `pay_periods.py`, `settings.py`) | 37 (Group A grid/transactional) + 36 (Group B account/loan) + 41 (Group C aggregation/analytics) = 114 route rows |
| 1.3 Templates | 56 | 50 (auth, errors, settings, base, navigation, form-only) | 56 per-template rows + cross-cutting (1.3.x.1: 11 arithmetic sites; 1.3.x.2: 4 `\|round` sites; 1.3.x.3: 20 conditional sites; 1.3.x.4: 8 filter sites; 1.3.x.5: 13 cross-page tokens) |
| 1.4 JS | 10 | 13 (DOM-only / password / non-financial forms) | 10 chart-JS rows + 3 orchestration rows + cross-cutting (1.4.x.1: 3 E-17 candidates + 3 borderline; 1.4.x.3: 7 conditionals; 1.4.x.4: 4 non-financial) |
| 1.5 Models | 24 | -- | 40 classes; 113 numeric columns; 6 `@property` accessors (5 on Transaction + 1 on Transfer + 1 on Category display_name + 1 on PayPeriod label) |
| 1.6 Aggregates | -- | -- | 5 SQLAlchemy `func.*` sites total; 2 are money aggregates (both in `year_end_summary_service._compute_envelope_breakdowns_aware` at lines 519 and 520-528); 3 are non-money (count, max, count) |

Total files inventoried across all layers: 142. Total files out of scope: 74. Total entry rows
(body): 67 services + 114 routes + 56 templates + 13 JS + 113 numeric columns + 5 aggregates = ~368
rows of structured inventory.

Total controlled-vocabulary tokens: 51 (42 from audit-plan Appendix A + 4 added in 1.1 / P1-b + 5
added in 1.2 / P1-c + 0 from P1-a sections 1.5/1.6 + 0 from P1-d sections 1.3/1.4).

### 1.7.2 Vocabulary additions beyond Appendix A

The starter set in Appendix A (audit plan section 12) covered every column-level concept in 1.5 and
the load-bearing concepts in 1.6, so P1-a added zero tokens. P1-d (templates and JS) added zero new
tokens because every Jinja or JS expression maps onto an existing token.

Additions from P1-b (section 1.1, services):

| Token | First introduced | Definition | Still in use? |
| ----- | ---------------- | ---------- | ------------- |
| `pension_benefit_annual` | `pension_calculator.py:31-66` (`PensionBenefit.annual_benefit` field on returned dataclass) | Annual defined-benefit pension amount in Decimal dollars produced by `pension_calculator.calculate_benefit`. | Yes; rendered at `retirement/dashboard.html:111`. |
| `pension_benefit_monthly` | `pension_calculator.py:65-66` | Monthly defined-benefit pension amount in Decimal dollars; consumed by `retirement_dashboard_service.compute_gap_data` and `retirement_gap_calculator.calculate_gap` (`monthly_pension_income` argument at `retirement_gap_calculator.py:39`). | Yes; rendered at `retirement/dashboard.html:118` and `_gap_analysis.html:15, 22`. |
| `loan_remaining_months` | `amortization_engine.py:128-176` (`calculate_remaining_months`) | Integer count of unfulfilled loan months; distinct from `payoff_date` because consumers display the count separately. Input to `calculate_monthly_payment`. | Yes; rendered at `loan/_refinance_results.html:51` and consumed by `debt_strategy/_results.html:67-70` (`total_months`). |
| `cash_runway_days` | `dashboard_service.py:375` (`_compute_cash_runway`) | Integer days of runway = `current_balance / daily_avg_paid_expenses` over a 30-day window; distinct from `emergency_fund_coverage_months` because input window and time unit differ. Phase 2 must decide whether to fold this into `emergency_fund_coverage_months`. | Yes; rendered at `dashboard/_balance_runway.html:21`. |

Additions from P1-c (section 1.2, routes):

| Token | First introduced | Definition | Still in use? |
| ----- | ---------------- | ---------- | ------------- |
| `entry_sum_total` | `entry_service.py:348` (`compute_entry_sums`) producing `(debit_sum, credit_sum)`; `entry_service.py:371` (`build_entry_sums_dict`) bundling `{debit, credit, total, count}` | Decimal sum of TransactionEntry rows for a transaction (cleared + uncleared). User-facing entry tally; feeds the `effective_amount` computation on the Transaction model via settle workflows. | Yes; rendered at `grid/_transaction_cell.html:21, 43`, `grid/_transaction_entries.html:136`, `companion/_transaction_card.html:18`, `dashboard/_bill_row.html:33`. |
| `entry_remaining` | `entry_service.py:405` (`compute_remaining(estimated_amount, entries)`); cited at `entries.py:104-106` and `companion.py:52` | Decimal remaining-budget value = `estimated_amount - sum(all_entries)`; negative = overspent. Anchors the "remaining budget" display on cells. | Yes; rendered at `grid/_transaction_cell.html:33-34`, `_transaction_entries.html:136`, `_mobile_grid.html:103`, `dashboard/_bill_row.html:33`, `companion/_transaction_card.html:40, 42, 44`. |
| `paycheck_breakdown` | `paycheck_calculator.py:92` (`calculate_paycheck` returns `PaycheckBreakdown` dataclass) | Single-token shorthand for the bundle `paycheck_gross + paycheck_net + federal_tax + state_tax + fica + pre_tax_deduction + post_tax_deduction + employer_contribution`. The route layer treats the breakdown as one rendered unit. | Yes; rendered as a bundle at `salary/breakdown.html`, `salary/projection.html`, `salary/calibrate_confirm.html`; consumed by `retirement.dashboard` and `retirement.gap_analysis` via `compute_gap_data`. |
| `chart_date_labels` | `investment.py:242-246` (`dashboard`), `:534` (`growth_chart`), and `loan.py:460` (chart helper) | String-formatted date labels (e.g. "May 2026") emitted as chart x-axis labels alongside `chart_balance_series`. Display-only formatting of period start_date. | Yes; rendered next to `chart_balance_series` as data attributes consumed by `growth_chart.js`, `payoff_chart.js`, `chart_year_end.js`, `retirement_gap_chart.js`. |
| `transfer_amount_computed` | `loan.py:1213-1241` (`create_payment_transfer`), `investment.py:668-670` (`create_contribution_transfer`) | Route-derived pre-fill transfer amount distinct from stored `transfer_amount`: defaults to a derived monthly payment (loan: P&I + escrow; investment: annual_contribution_limit/26 with `$500` fallback) when the user does not override. | Yes; consumed only by the two cited routes' Transfer creation paths. |

Orphan vocabulary token (defined at top, never used in body):

| Token | Note |
| ----- | ---- |
| `loan_principal_displayed` | Defined at vocab line 33 (from audit-plan Appendix A) but used zero times in the body. Phase 2 may collapse this into `loan_principal_real` or `loan_principal_stored` depending on the resolution; A-04 already documents the dual policy and both tokens are in use. Recorded as an orphan rather than removed because Appendix A is the contract for the starter set. |

### 1.7.3 Concept-to-locations index

The table below sorts the controlled-vocabulary alphabetically and lists every layer where a
producer, consumer, or aggregate for that concept lives. Producer (1.1) and producer (1.5) columns
are split because Phase 4 (source-of-truth audit) needs to distinguish stored columns from computed
values. A `*` after the token name marks multi-path concepts (two or more producers OR two or more
consumers). Phase 3.1 reads the `*`-marked rows as required cross-comparison findings.

Notation:

- `svc:name@file:line` = function name plus citation in services.
- `route:name@file:line` = view function name plus citation in routes.
- Template citations name the template only (line ranges are in 1.3 tables).
- JS citations name the file only (sites are in 1.4 tables).
- Model citations name the class.column or class.property plus citation.
- `Aggregates` cites `year_end_summary_service.py:519, 520-528` only -- the two money SQL
  aggregates.

| Concept token | Producers (1.1 services) | Producers (1.5 models) | Consumers (1.2 routes) | Consumers (1.3 templates) | Consumers (1.4 JS) | Aggregates (1.6) |
| ------------- | ------------------------ | ---------------------- | ---------------------- | ------------------------- | ------------------ | ---------------- |
| `account_balance` * | svc:`_compute_account_projections`@`savings_dashboard_service.py:294`; svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338`; svc:`_compute_net_worth`@`year_end_summary_service.py:689`; svc:`_get_balance_info`@`dashboard_service.py:334`; svc:`_compute_alerts`@`dashboard_service.py:252`; svc:`resolve_grid_account`@`account_resolver.py:36`; svc:`resolve_analytics_account`@`account_resolver.py:79` | `Account.current_anchor_balance`@`account.py:51`; `AccountAnchorHistory.anchor_balance`@`account.py:152`; `UserSettings.low_balance_threshold`@`user.py:236` (alert threshold input) | route:`interest_detail`@`accounts.py:1233` (current_balance @1299); route:`checking_detail`@`accounts.py:1376` (current_balance @1432); route:`dashboard`@`investment.py:63` (current_balance @115); route:`growth_chart`@`investment.py:363` (comparison.committed_end/whatif_end); route:`balance_section`@`dashboard.py:160`; route:`page`@`dashboard.py:40` | `grid/grid.html` (line 17); `savings/dashboard.html` (186); `accounts/checking_detail.html` (43); `accounts/interest_detail.html` (52); `accounts/_anchor_cell.html` (48); `accounts/list.html` (46, 110); `grid/_anchor_edit.html` (53); `dashboard/_balance_runway.html` (10); `retirement/_retirement_account_rows.html` (15); `investment/_growth_chart.html` (16, 20) | -- | -- |
| `apy_interest` | svc:`calculate_interest`@`interest_projection.py:49`; svc:`calculate_balances_with_interest`@`balance_calculator.py:112`; svc:`_compute_interest_for_year`@`year_end_summary_service.py:1207`; svc:`compute_slider_defaults`@`retirement_dashboard_service.py:257` | `InterestParams.apy`@`interest_params.py:60` | route:`interest_detail`@`accounts.py:1233` (period_data interest column) | `accounts/interest_detail.html` (42, 85); `savings/dashboard.html` (137, 140) | -- | -- |
| `cash_runway_days` | svc:`_compute_cash_runway`@`dashboard_service.py:375`; svc:`_get_balance_info`@`dashboard_service.py:334` | -- | route:`page`@`dashboard.py:40`; route:`balance_section`@`dashboard.py:160` | `dashboard/_balance_runway.html` (21) | -- | -- |
| `chart_balance_series` * | svc:`generate_schedule`@`amortization_engine.py:326`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`project_balance`@`growth_engine.py:164`; svc:`reverse_project_balance`@`growth_engine.py:297`; svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`calculate_balances_with_interest`@`balance_calculator.py:112` | -- | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`dashboard`@`investment.py:63`; route:`growth_chart`@`investment.py:363`; route:`calculate`@`debt_strategy.py:295`; route:`gap_analysis`@`retirement.py:301`; route:`balance_row`@`grid.py:393` | `loan/_schedule.html`; `loan/dashboard.html` (chart data attrs); `loan/_payoff_results.html`; `loan/_refinance_results.html`; `investment/dashboard.html`; `investment/_growth_chart.html` (3-6); `debt_strategy/_results.html`; `retirement/_gap_analysis.html` (80-85) | `chart_year_end.js`; `payoff_chart.js`; `growth_chart.js`; `retirement_gap_chart.js`; `debt_strategy.js` | -- |
| `chart_date_labels` | svc:(formatted inline at route level; no service producer) | -- | route:`dashboard`@`investment.py:63` (242-246); route:`growth_chart`@`investment.py:363` (534); route:`dashboard`@`loan.py:405` (460) | (rendered as data attrs only, not as visible text) | `growth_chart.js`; `payoff_chart.js`; `chart_year_end.js`; `retirement_gap_chart.js` | -- |
| `checking_balance` * | svc:`calculate_balances`@`balance_calculator.py:35`; svc:`calculate_balances_with_interest`@`balance_calculator.py:112`; svc:`_compute_alerts`@`dashboard_service.py:252`; svc:`_get_balance_info`@`dashboard_service.py:334`; svc:`get_month_detail`@`calendar_service.py:88`; svc:`_compute_month_end_balance`@`calendar_service.py:435` | `Account.current_anchor_balance`@`account.py:51` (anchor read by all balance services) | route:`index`@`grid.py:164`; route:`balance_row`@`grid.py:393`; route:`page`@`dashboard.py:40`; route:`balance_section`@`dashboard.py:160`; route:`checking_detail`@`accounts.py:1376` | `grid/grid.html`; `accounts/checking_detail.html`; `dashboard/_balance_runway.html`; `accounts/_anchor_cell.html`; `grid/_anchor_edit.html` | -- | -- |
| `contribution_limit_remaining` | svc:`calculate_investment_inputs`@`investment_projection.py:100`; svc:`project_balance`@`growth_engine.py:164` (enforces annual_contribution_limit) | `InvestmentParams.annual_contribution_limit`@`investment_params.py:84`; `PaycheckDeduction.annual_cap`@`paycheck_deduction.py:118` (consumer field) | route:`dashboard`@`investment.py:63` (limit_info.limit @173-181) | `investment/dashboard.html` (76) | -- | -- |
| `debt_total` * | svc:`compute_dashboard_data`@`dashboard_service.py:40`; svc:`compute_dashboard_data`@`savings_dashboard_service.py:61`; svc:`_compute_debt_summary`@`savings_dashboard_service.py:802`; svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`_compute_net_worth`@`year_end_summary_service.py:689`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824` | -- | route:`page`@`dashboard.py:40`; route:`dashboard`@`savings.py:107`; route:`dashboard`@`debt_strategy.py:275` | `dashboard/_debt_summary.html` (5); `savings/dashboard.html` (54); `debt_strategy/dashboard.html` (per-account principal) | -- | -- |
| `dti_ratio` * | svc:`_compute_debt_summary`@`savings_dashboard_service.py:802` (quantize @851, 873); svc:`compute_dashboard_data`@`dashboard_service.py:40` (DTI quantize @172, 176) | -- | route:`page`@`dashboard.py:40`; route:`dashboard`@`savings.py:107` | `dashboard/_debt_summary.html` (15); `savings/dashboard.html` (54-65, debt summary card) | -- | -- |
| `effective_amount` * | property:`Transaction.effective_amount`@`transaction.py:221-245`; svc:`settle_from_entries`@`transaction_service.py:38`; svc:`_entry_aware_amount`@`balance_calculator.py:292`; svc:`_sum_remaining`@`balance_calculator.py:389`; svc:`_sum_all`@`balance_calculator.py:422`; svc:`_compute_actual` and `_build_txn_variance`@`budget_variance_service.py:358-393`; svc:`mark_as_credit`@`credit_workflow.py:112` (line 229 hand-rolled fallback); svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380`; svc:`_compute_spending_by_category`@`year_end_summary_service.py:414`; svc:`_compute_entry_breakdowns`@`year_end_summary_service.py:475`; svc:`_create_payback`@`entry_credit_workflow.py:170`; svc:`_settle_source_and_roll_leftover`@`carry_forward_service.py:788`; property:`Transfer.effective_amount`@`transfer.py:174-182` | `Transaction.estimated_amount`@`transaction.py:158`; `Transaction.actual_amount`@`transaction.py:159`; `TransactionEntry.amount`@`transaction_entry.py:73`; `TransactionTemplate.default_amount`@`transaction_template.py:59`; `Transfer.amount`@`transfer.py:142`; `TransferTemplate.default_amount`@`transfer_template.py:60` | every cell-render route in 1.2 Group A: route:`get_cell`@`transactions.py:244`; route:`update_transaction`@`transactions.py:304`; route:`mark_done`@`transactions.py:491`; route:`mark_credit`@`transactions.py:632`; route:`unmark_credit`@`transactions.py:675`; route:`cancel_transaction`@`transactions.py:713`; route:`create_inline`@`transactions.py:909`; route:`create_transaction`@`transactions.py:987`; route:`get_cell`@`transfers.py:698`; route:`update_transfer`@`transfers.py:744`; route:`create_ad_hoc`@`transfers.py:850`; route:`mark_done`@`transfers.py:1055`; route:`cancel_transfer`@`transfers.py:1118`; route:`mark_paid`@`dashboard.py:54` (Q-14); route:`calendar_tab`@`analytics.py:107`; route:`variance_tab`@`analytics.py:205`; route:`trends_tab`@`analytics.py:272`; route:`summary`@`obligations.py:259` | `grid/_transaction_cell.html` (17, 33); `grid/_transaction_entries.html` (92); `transfers/_transfer_cell.html` (21); `dashboard/_bill_row.html` (33-34, 38); `analytics/_calendar_month.html` (95); `obligations/summary.html` (100, 149, 195); `templates/list.html` (99, 182); `transfers/list.html` (81, 146) | -- | `func.sum(TransactionEntry.amount)`@`year_end_summary_service.py:519`; conditional `case(is_credit) sum`@`year_end_summary_service.py:520-528` |
| `emergency_fund_coverage_months` | svc:`calculate_savings_metrics`@`savings_goal_service.py:139`; svc:`compute_dashboard_data`@`savings_dashboard_service.py:61` (delegates) | -- | route:`dashboard`@`savings.py:107` | `savings/dashboard.html` (298, 304, 310) | -- | -- |
| `employer_contribution` * | svc:`calculate_employer_contribution`@`growth_engine.py:91`; svc:`project_balance`@`growth_engine.py:164`; svc:`reverse_project_balance`@`growth_engine.py:297`; svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79`; svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338`; svc:`_compute_savings_progress`@`year_end_summary_service.py:887` | `InvestmentParams.employer_flat_percentage`@`investment_params.py:90`; `InvestmentParams.employer_match_percentage`@`investment_params.py:91`; `InvestmentParams.employer_match_cap_percentage`@`investment_params.py:92` | route:`dashboard`@`investment.py:63` (employer_contribution_per_period @187-189) | `investment/dashboard.html` (62) | -- | -- |
| `entry_remaining` * | svc:`compute_remaining`@`entry_service.py:405`; svc:`build_entry_sums_dict`@`entry_service.py:371`; svc:`_entry_progress_fields`@`dashboard_service.py:203` (consumes); helper:`_build_entry_data`@`companion.py:28-64` (consumes); helper:`_render_entry_list`@`entries.py:83-120` (consumes) | `TransactionEntry.amount`@`transaction_entry.py:73`; `Transaction.estimated_amount`@`transaction.py:158` (input) | route:`list_entries`@`entries.py:200`; route:`get_cell`@`transactions.py:244` (via build_entry_sums_dict); route:`page`@`dashboard.py:40` (via _entry_progress_fields); route:`index`@`companion.py:80`; route:`mark_paid`@`dashboard.py:54` (Q-14) | `grid/_transaction_cell.html` (33-34); `grid/_transaction_entries.html` (136); `grid/_mobile_grid.html` (103); `dashboard/_bill_row.html` (33); `companion/_transaction_card.html` (40, 42, 44) | -- | -- |
| `entry_sum_total` * | svc:`compute_entry_sums`@`entry_service.py:348`; svc:`build_entry_sums_dict`@`entry_service.py:371`; svc:`compute_actual_from_entries`@`entry_service.py:428` | `TransactionEntry.amount`@`transaction_entry.py:73` | route:`get_cell`@`transactions.py:244` (build_entry_sums_dict @88); route:`list_entries`@`entries.py:200`; route:`index`@`companion.py:80` | `grid/_transaction_cell.html` (21, 43); `grid/_transaction_entries.html` (136); `companion/_transaction_card.html` (18); `dashboard/_bill_row.html` (33) | -- | `func.sum(TransactionEntry.amount)`@`year_end_summary_service.py:519` |
| `escrow_per_period` * | svc:`calculate_monthly_escrow`@`escrow_calculator.py:14`; svc:`calculate_total_payment`@`escrow_calculator.py:60`; svc:`project_annual_escrow`@`escrow_calculator.py:79`; svc:`load_loan_context`@`loan_payment_service.py:78`; svc:`prepare_payments_for_engine`@`loan_payment_service.py:263` (A-06); svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380` (consumes preprocessed) | `EscrowComponent.annual_amount`@`loan_features.py:126`; `EscrowComponent.inflation_rate`@`loan_features.py:127` | route:`dashboard`@`loan.py:405` (`monthly_escrow` @433-435); route:`add_escrow`@`loan.py:761`; route:`delete_escrow`@`loan.py:815`; route:`create_payment_transfer`@`loan.py:1170` (`escrow_calculator.calculate_total_payment`@1241) | `loan/dashboard.html`; `loan/_schedule.html` (55, 59); `loan/_payment_breakdown.html` (62, 70); `loan/_escrow_list.html` (8, 16, 36-37); `loan/_refinance_results.html` (component breakdowns) | -- | -- |
| `federal_tax` * | svc:`calculate_federal_withholding`@`tax_calculator.py:35`; svc:`calculate_federal_tax`@`tax_calculator.py:215`; svc:`_apply_marginal_brackets`@`tax_calculator.py:173`; svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`derive_effective_rates`@`calibration_service.py:34`; svc:`apply_calibration`@`calibration_service.py:106`; svc:`load_tax_configs`@`tax_config_service.py:16` (input); svc:`calculate_gap`@`retirement_gap_calculator.py:37` (retirement tax input) | `TaxBracketSet.standard_deduction`@`tax_config.py:52`; `TaxBracketSet.child_credit_amount`@`tax_config.py:59`; `TaxBracketSet.other_dependent_credit_amount`@`tax_config.py:63`; `TaxBracket.rate`@`tax_config.py:115`; `CalibrationOverride.actual_federal_tax`@`calibration_override.py:81`; `CalibrationOverride.effective_federal_rate`@`calibration_override.py:89`; `UserSettings.estimated_retirement_tax_rate`@`user.py:242`; `SalaryProfile.additional_deductions`@`salary_profile.py:92` (W-4 input); `SalaryProfile.extra_withholding`@`salary_profile.py:96` | route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064` (Q-13); route:`calibrate_confirm`@`salary.py:1127`; route:`year_end_tab`@`analytics.py:171`; route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301` | `salary/breakdown.html` (98); `salary/projection.html` (60); `salary/calibrate_confirm.html` (65, 66); `analytics/_year_end.html` (68); `retirement/_gap_analysis.html` (implicit via SWR rate) | -- | -- |
| `fica` * | svc:`calculate_fica`@`tax_calculator.py:274`; svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`_get_cumulative_wages`@`paycheck_calculator.py:480`; svc:`derive_effective_rates`@`calibration_service.py:34`; svc:`apply_calibration`@`calibration_service.py:106` | `CalibrationOverride.actual_social_security`@`calibration_override.py:83`; `CalibrationOverride.actual_medicare`@`calibration_override.py:84`; `CalibrationOverride.effective_ss_rate`@`calibration_override.py:91`; `CalibrationOverride.effective_medicare_rate`@`calibration_override.py:92`; `FicaConfig.ss_rate`@`tax_config.py:217`; `FicaConfig.ss_wage_base`@`tax_config.py:221`; `FicaConfig.medicare_rate`@`tax_config.py:225`; `FicaConfig.medicare_surtax_rate`@`tax_config.py:229`; `FicaConfig.medicare_surtax_threshold`@`tax_config.py:233` | route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064`; route:`calibrate_confirm`@`salary.py:1127`; route:`year_end_tab`@`analytics.py:171`; route:`update_fica_config`@`salary.py:1310` | `salary/breakdown.html` (106, 110); `salary/projection.html` (60); `salary/calibrate_confirm.html` (75, 76, 80, 81); `analytics/_year_end.html` (72, 76) | -- | -- |
| `goal_progress` * | svc:`resolve_goal_target`@`savings_goal_service.py:21`; svc:`calculate_required_contribution`@`savings_goal_service.py:109`; svc:`calculate_trajectory`@`savings_goal_service.py:331`; svc:`compute_dashboard_data`@`savings_dashboard_service.py:61`; svc:`_entry_progress_fields`@`dashboard_service.py:203` (Q-08); helper:`_build_entry_data`@`companion.py:28-64` (`pct` inline at lines 53-56) | `SavingsGoal.target_amount`@`savings_goal.py:75`; `SavingsGoal.contribution_per_period`@`savings_goal.py:77`; `SavingsGoal.income_multiplier`@`savings_goal.py:115` | route:`dashboard`@`savings.py:107`; route:`gap_analysis`@`retirement.py:301`; route:`index`@`companion.py:80`; route:`mark_paid`@`dashboard.py:54` (Q-14) | `dashboard/_savings_goals.html` (13, 20); `savings/dashboard.html` (374-380, 402); `retirement/_gap_analysis.html` (implicit savings_surplus); `companion/_transaction_card.html` (33) | -- | -- |
| `growth` * | svc:`project_balance`@`growth_engine.py:164`; svc:`reverse_project_balance`@`growth_engine.py:297`; svc:`_compute_trends`@`spending_trend_service.py:97`; svc:`_safe_pct_change`@`spending_trend_service.py:470`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79`; svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338`; svc:`_compute_savings_progress`@`year_end_summary_service.py:887`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824`; svc:`_compute_interest_for_year`@`year_end_summary_service.py:1207` | `InvestmentParams.assumed_annual_return`@`investment_params.py:80`; `SalaryRaise.percentage`@`salary_raise.py:110`; `PaycheckDeduction.inflation_rate`@`paycheck_deduction.py:123`; `EscrowComponent.inflation_rate`@`loan_features.py:127`; `UserSettings.default_inflation_rate`@`user.py:234`; `UserSettings.safe_withdrawal_rate`@`user.py:237`; `UserSettings.trend_alert_threshold`@`user.py:246` | route:`dashboard`@`investment.py:63`; route:`growth_chart`@`investment.py:363`; route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301`; route:`trends_tab`@`analytics.py:272`; route:`year_end_tab`@`analytics.py:171` | `investment/dashboard.html`; `investment/_growth_chart.html`; `retirement/_gap_analysis.html` (41); `analytics/_trends.html`; `analytics/_year_end.html` (year_summary_growth section) | -- | -- |
| `interest_paid_per_period` * | svc:`generate_schedule`@`amortization_engine.py:326`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`calculate_balances_with_amortization`@`balance_calculator.py:176`; svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824` | -- | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`year_end_tab`@`analytics.py:171` | `loan/_schedule.html` (57); `loan/_payment_breakdown.html` (56); `analytics/_year_end.html` | -- | -- |
| `interest_saved` * | svc:`calculate_summary`@`amortization_engine.py:649`; svc:`calculate_strategy`@`debt_strategy_service.py:521` | -- | route:`payoff_calculate`@`loan.py:860`; route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295` | `loan/_payoff_results.html` (19); `loan/_refinance_results.html` (41); `debt_strategy/_results.html` (80-95) | -- | -- |
| `loan_principal_real` * | svc:`get_loan_projection`@`amortization_engine.py:864` (fixed-rate engine-walked balance per A-04); svc:`generate_schedule`@`amortization_engine.py:326`; svc:`_compute_account_projections`@`savings_dashboard_service.py:294`; svc:`calculate_balances_with_amortization`@`balance_calculator.py:176`; svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824` | `LoanParams.current_principal`@`loan_params.py:54` (CACHED-for-display per A-04 for fixed-rate; AUTHORITATIVE for ARM) | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`refinance_calculate`@`loan.py:1027` (current_real_principal @1087, Q-11); route:`create_payment_transfer`@`loan.py:1170`; route:`dashboard`@`debt_strategy.py:275` | `loan/dashboard.html` (104); `loan/_schedule.html` (70, per-row remaining_balance); `loan/_refinance_results.html` (69-70); `debt_strategy/dashboard.html`; `debt_strategy/_results.html` | -- | -- |
| `loan_principal_stored` * | svc:`get_loan_projection`@`amortization_engine.py:864` (ARM stored per A-04, lines 977-985); svc:`load_loan_context`@`loan_payment_service.py:78`; svc:`compute_contractual_pi`@`loan_payment_service.py:233`; svc:`_compute_account_projections`@`savings_dashboard_service.py:294` (proj.current_balance @373 ARM); svc:`_compute_interest_for_year`@`year_end_summary_service.py:1207`; svc:`_balance_from_schedule_at_date`@`year_end_summary_service.py:1465-1469` | `LoanParams.original_principal`@`loan_params.py:53`; `LoanParams.current_principal`@`loan_params.py:54` (AUTHORITATIVE for ARM per A-04) | route:`dashboard`@`loan.py:405`; route:`update_params`@`loan.py:631`; route:`refinance_calculate`@`loan.py:1027`; route:`create_payment_transfer`@`loan.py:1170` (ARM branch @1222-1225) | `loan/dashboard.html` (99); `loan/_refinance_results.html` (closing_costs vs principal); `loan/_rate_history.html` | -- | -- |
| `loan_principal_displayed` | -- (orphan; see 1.7.2) | -- | -- | -- | -- | -- |
| `loan_remaining_months` | svc:`calculate_remaining_months`@`amortization_engine.py:128`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`compute_contractual_pi`@`loan_payment_service.py:233` | -- | route:`refinance_calculate`@`loan.py:1027` (current_remaining_months); route:`create_payment_transfer`@`loan.py:1170` | `loan/_refinance_results.html` (51); `debt_strategy/_results.html` (67-70, total_months) | -- | -- |
| `monthly_payment` * | svc:`calculate_monthly_payment`@`amortization_engine.py:178` (14 call sites listed under A-05 cross-reference); svc:`calculate_summary`@`amortization_engine.py:649`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`calculate_payoff_by_date`@`amortization_engine.py:753`; svc:`compute_contractual_pi`@`loan_payment_service.py:233`; svc:`calculate_balances_with_amortization`@`balance_calculator.py:176`; svc:`calculate_total_payment`@`escrow_calculator.py:60`; svc:`_compute_debt_summary`@`savings_dashboard_service.py:802`; svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`amount_to_monthly`@`savings_goal_service.py:199` | `LoanParams.interest_rate`@`loan_params.py:55`; `LoanParams.term_months`@`loan_params.py:56`; `RateHistory.interest_rate`@`loan_features.py:75` (ARM override) | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`refinance_calculate`@`loan.py:1027`; route:`create_payment_transfer`@`loan.py:1170` (ARM @1225, fixed @1231); route:`dashboard`@`debt_strategy.py:275`; route:`calculate`@`debt_strategy.py:295`; route:`summary`@`obligations.py:259` (Q-12 monthly aggregation) | `loan/dashboard.html` (129, 134); `loan/_schedule.html` (55, 94); `loan/_payment_breakdown.html` (22); `loan/_escrow_list.html` (8); `loan/_payoff_results.html` (72); `loan/_refinance_results.html` (23, 24); `debt_strategy/dashboard.html` (minimum_payment); `debt_strategy/_results.html`; `dashboard/_debt_summary.html` (9, aggregate); `obligations/summary.html` (51, 159, aggregate) | -- | -- |
| `months_saved` | svc:`generate_schedule`@`amortization_engine.py:326`; svc:`calculate_summary`@`amortization_engine.py:649`; svc:`calculate_strategy`@`debt_strategy_service.py:521` | -- | route:`payoff_calculate`@`loan.py:860` (committed_months_saved); route:`refinance_calculate`@`loan.py:1027` (break_even_months); route:`calculate`@`debt_strategy.py:295` | `loan/_payoff_results.html` (14, 29); `loan/_refinance_results.html` (90); `debt_strategy/_results.html` | -- | -- |
| `net_worth` | svc:`_compute_net_worth`@`year_end_summary_service.py:689` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (data.net_worth.* section) | -- | -- |
| `paycheck_breakdown` * | svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`project_salary`@`paycheck_calculator.py:250`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79` (consumer) | -- | route:`list_profiles`@`salary.py:102`; route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064`; route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301` | `salary/list.html`; `salary/breakdown.html` (64-...); `salary/projection.html` (50-64); `salary/calibrate_confirm.html` (35-81 actual flavor); `dashboard/_payday.html` | -- | -- |
| `paycheck_gross` * | svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`_apply_raises`@`paycheck_calculator.py:274`; svc:`_apply_single_raise`@`paycheck_calculator.py:329`; svc:`project_salaries_by_year`@`pension_calculator.py:78`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79` | `SalaryProfile.annual_salary`@`salary_profile.py:72`; `SalaryProfile.additional_income`@`salary_profile.py:88`; `SalaryRaise.flat_amount`@`salary_raise.py:111`; `CalibrationOverride.actual_gross_pay`@`calibration_override.py:80` | route:`list_profiles`@`salary.py:102`; route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064`; route:`calibrate_confirm`@`salary.py:1127`; route:`year_end_tab`@`analytics.py:171`; route:`gap_analysis`@`retirement.py:301` | `salary/list.html` (48); `salary/breakdown.html` (64, 70); `salary/projection.html` (50, 56); `salary/calibrate_confirm.html` (35); `analytics/_year_end.html` (61) | -- | -- |
| `paycheck_net` * | svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`project_salary`@`paycheck_calculator.py:250`; svc:`calculate_gap`@`retirement_gap_calculator.py:37` (net_biweekly input); svc:`_get_transaction_amount`@`recurrence_engine.py:720` (salary-linked breakdown.net_pay) | -- | route:`list_profiles`@`salary.py:102` (net_pay @122-125); route:`breakdown`@`salary.py:960` (implicit final); route:`projection`@`salary.py:1020` (bd.net_pay); route:`create_profile`@`salary.py:149` (init_breakdown.net_pay @264) | `salary/list.html` (51); `salary/breakdown.html` (implicit); `salary/projection.html` (64); `dashboard/_payday.html` (15); `retirement/_gap_analysis.html` (9) | -- | -- |
| `payoff_date` * | svc:`_derive_summary_metrics`@`amortization_engine.py:622`; svc:`calculate_summary`@`amortization_engine.py:649`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`calculate_payoff_by_date`@`amortization_engine.py:753`; svc:`calculate_strategy`@`debt_strategy_service.py:521` | -- | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295` | `loan/dashboard.html` (143); `loan/_payoff_results.html` (9); `loan/_refinance_results.html` (62-63); `debt_strategy/_results.html` (34-38) | -- | -- |
| `pension_benefit_annual` | svc:`calculate_benefit`@`pension_calculator.py:31` | `PensionProfile.benefit_multiplier`@`pension_profile.py:78`; `PensionProfile.consecutive_high_years`@`pension_profile.py:79` | route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301` | `retirement/dashboard.html` (111) | -- | -- |
| `pension_benefit_monthly` | svc:`calculate_benefit`@`pension_calculator.py:31`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79`; svc:`calculate_gap`@`retirement_gap_calculator.py:37` | (derived from pension_benefit_annual; no direct stored column) | route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301` | `retirement/dashboard.html` (118); `retirement/_gap_analysis.html` (15, 22) | `retirement_gap_chart.js` (data-pension attr at `_gap_analysis.html:81-85`) | -- |
| `period_subtotal` * | svc:`calculate_balances`@`balance_calculator.py:35`; svc:`_sum_remaining`@`balance_calculator.py:389`; svc:`_sum_all`@`balance_calculator.py:422`; svc:`_compute_spending_by_category`@`year_end_summary_service.py:414`; svc:`compute_variance`@`budget_variance_service.py:99`; svc:`compute_trends`@`spending_trend_service.py:97`; svc:`get_month_detail`@`calendar_service.py:88`; svc:`get_year_overview`@`calendar_service.py:136`; svc:`compute_committed_monthly`@`savings_goal_service.py:287`; svc:`_compute_spending_comparison`@`dashboard_service.py` (referenced in section 1.2.x Q-10) | -- | route:`index`@`grid.py:164` (inline subtotal loop @263-279, Q-10); route:`page`@`dashboard.py:40` (spending_comparison); route:`summary`@`obligations.py:259` (Q-12 monthly aggregation @331-408); route:`variance_tab`@`analytics.py:205`; route:`trends_tab`@`analytics.py:272`; route:`calendar_tab`@`analytics.py:107` | `grid/grid.html` (196, 269, 280); `obligations/summary.html` (47, 50, 51, 57, 62, 111, 159, 206); `analytics/_calendar_year.html` (47, 51, 57, 70, 74, 79); `analytics/_calendar_month.html` (53, 56); `dashboard/_spending_comparison.html` (7, 11); `analytics/_variance.html` (per-category subtotals) | `chart_variance.js` (diff @69) | -- |
| `post_tax_deduction` | svc:`_calculate_deductions`@`paycheck_calculator.py:403`; svc:`calculate_paycheck`@`paycheck_calculator.py:92` | `PaycheckDeduction.amount`@`paycheck_deduction.py:113` (timing-dependent) | route:`add_deduction`@`salary.py:696`; route:`update_deduction`@`salary.py:833`; route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020` | `salary/breakdown.html` (121); `salary/projection.html` (62); `salary/_deductions_section.html` | -- | -- |
| `pre_tax_deduction` | svc:`_calculate_deductions`@`paycheck_calculator.py:403`; svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`derive_effective_rates`@`calibration_service.py:34` (input total_pre_tax) | `PaycheckDeduction.amount`@`paycheck_deduction.py:113`; `CalibrationDeductionOverride.actual_amount`@`calibration_override.py:164` | route:`add_deduction`@`salary.py:696`; route:`update_deduction`@`salary.py:833`; route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064` (Q-13 inline subtraction @1095) | `salary/breakdown.html` (81); `salary/projection.html` (58); `salary/calibrate_confirm.html` (39); `salary/_deductions_section.html` (38, 40) | -- | -- |
| `principal_paid_per_period` * | svc:`generate_schedule`@`amortization_engine.py:326`; svc:`calculate_balances_with_amortization`@`balance_calculator.py:176`; svc:`calculate_strategy`@`debt_strategy_service.py:521`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824` | -- | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`year_end_tab`@`analytics.py:171` | `loan/_schedule.html` (55, 56); `loan/_payment_breakdown.html` (51); `analytics/_year_end.html` (year_summary_principal_paid) | -- | -- |
| `projected_end_balance` * | svc:`calculate_balances`@`balance_calculator.py:35`; svc:`calculate_balances_with_interest`@`balance_calculator.py:112`; svc:`_compute_account_projections`@`savings_dashboard_service.py:294`; svc:`_project_retirement_accounts`@`retirement_dashboard_service.py:338`; svc:`get_month_detail`@`calendar_service.py:88`; svc:`_compute_month_end_balance`@`calendar_service.py:435` | -- | route:`index`@`grid.py:164`; route:`balance_row`@`grid.py:393`; route:`dashboard`@`savings.py:107`; route:`checking_detail`@`accounts.py:1376`; route:`interest_detail`@`accounts.py:1233`; route:`dashboard`@`investment.py:63`; route:`growth_chart`@`investment.py:363` | `grid/grid.html` (26); `grid/_balance_row.html` (26); `savings/dashboard.html` (212); `accounts/checking_detail.html` (55); `accounts/interest_detail.html` (64); `investment/_growth_chart.html` (16, 20); `retirement/_retirement_account_rows.html` (17) | `growth_chart.js`; `payoff_chart.js` (chart datasets); `retirement_gap_chart.js` (preRetirement clamp) | -- |
| `savings_total` * | svc:`compute_dashboard_data`@`savings_dashboard_service.py:61`; svc:`compute_gap_data`@`retirement_dashboard_service.py:79`; svc:`_compute_savings_progress`@`year_end_summary_service.py:887` | -- | route:`dashboard`@`savings.py:107`; route:`dashboard`@`retirement.py:46`; route:`gap_analysis`@`retirement.py:301`; route:`year_end_tab`@`analytics.py:171` | `savings/dashboard.html` (317); `retirement/_gap_analysis.html` (35, 41); `analytics/_year_end.html` | -- | -- |
| `state_tax` | svc:`calculate_state_tax`@`tax_calculator.py:240`; svc:`calculate_paycheck`@`paycheck_calculator.py:92`; svc:`derive_effective_rates`@`calibration_service.py:34`; svc:`apply_calibration`@`calibration_service.py:106` | `CalibrationOverride.actual_state_tax`@`calibration_override.py:82`; `CalibrationOverride.effective_state_rate`@`calibration_override.py:90`; `StateTaxConfig.flat_rate`@`tax_config.py:175`; `StateTaxConfig.standard_deduction`@`tax_config.py:176` | route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064`; route:`update_tax_config`@`salary.py:1251`; route:`year_end_tab`@`analytics.py:171` | `salary/breakdown.html` (102); `salary/projection.html` (60); `salary/calibrate_confirm.html` (70, 71); `analytics/_year_end.html` (80) | -- | -- |
| `taxable_income` * | svc:`calculate_federal_withholding`@`tax_calculator.py:35`; svc:`_apply_marginal_brackets`@`tax_calculator.py:173`; svc:`calculate_federal_tax`@`tax_calculator.py:215`; svc:`calculate_state_tax`@`tax_calculator.py:240`; svc:`derive_effective_rates`@`calibration_service.py:34`; svc:`calculate_paycheck`@`paycheck_calculator.py:92` | `TaxBracket.min_income`@`tax_config.py:113`; `TaxBracket.max_income`@`tax_config.py:114`; `SalaryProfile.additional_income`@`salary_profile.py:88` | route:`breakdown`@`salary.py:960`; route:`projection`@`salary.py:1020`; route:`calibrate_preview`@`salary.py:1064` (Q-13: route inline `gross - total_pre_tax` @1095 vs breakdown.taxable_income); route:`calibrate_confirm`@`salary.py:1127` | `salary/breakdown.html` (89); `salary/calibrate_confirm.html` (43) | -- | -- |
| `total_interest` * | svc:`_derive_summary_metrics`@`amortization_engine.py:622`; svc:`calculate_summary`@`amortization_engine.py:649`; svc:`get_loan_projection`@`amortization_engine.py:864`; svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380`; svc:`calculate_strategy`@`debt_strategy_service.py:521` | -- | route:`dashboard`@`loan.py:405`; route:`payoff_calculate`@`loan.py:860`; route:`refinance_calculate`@`loan.py:1027`; route:`calculate`@`debt_strategy.py:295`; route:`year_end_tab`@`analytics.py:171` | `loan/dashboard.html` (139); `loan/_schedule.html` (96, schedule_totals); `loan/_refinance_results.html` (37, 38); `debt_strategy/_results.html` (45-49) | -- | -- |
| `transfer_amount` * | svc:`create_transfer`@`transfer_service.py:283`; svc:`update_transfer`@`transfer_service.py:443`; svc:`restore_transfer`@`transfer_service.py:688`; svc:`generate_for_template`@`transfer_recurrence.py:43`; svc:`regenerate_for_template`@`transfer_recurrence.py:141`; svc:`resolve_conflicts`@`transfer_recurrence.py:224`; svc:`carry_forward_unpaid`@`carry_forward_service.py:291`; svc:`_compute_transfers_summary`@`year_end_summary_service.py:636` (year-end display) | `Transfer.amount`@`transfer.py:142`; property:`Transfer.effective_amount`@`transfer.py:174-182`; `TransferTemplate.default_amount`@`transfer_template.py:60` | every transfer-cell-render route: route:`update_transfer`@`transfers.py:744`; route:`create_ad_hoc`@`transfers.py:850`; route:`mark_done`@`transfers.py:1055`; route:`cancel_transfer`@`transfers.py:1118`; route:`delete_transfer`@`transfers.py:1020`; route:`create_transfer_template`@`transfers.py:127`; route:`year_end_tab`@`analytics.py:171` | `transfers/_transfer_cell.html` (21, 38); `transfers/list.html` (81, 146); `analytics/_year_end.html` (transfer_amount per destination) | -- | -- |
| `transfer_amount_computed` | -- (route-derived inline; no service producer) | -- | route:`create_payment_transfer`@`loan.py:1170` (lines 1213-1241); route:`create_contribution_transfer`@`investment.py:609` (lines 668-670) | (route prefills the form; no template displays this token directly) | -- | -- |
| `year_summary_dec31_balance` | svc:`_compute_net_worth`@`year_end_summary_service.py:689` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (data.net_worth.dec31) | -- | -- |
| `year_summary_employer_total` | svc:`_compute_savings_progress`@`year_end_summary_service.py:887` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (savings section) | -- | -- |
| `year_summary_growth` | svc:`_compute_savings_progress`@`year_end_summary_service.py:887`; svc:`_compute_debt_progress`@`year_end_summary_service.py:824`; svc:`_compute_interest_for_year`@`year_end_summary_service.py:1207`; svc:`_compute_mortgage_interest`@`year_end_summary_service.py:380` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (savings + debt sections) | -- | -- |
| `year_summary_jan1_balance` | svc:`_compute_net_worth`@`year_end_summary_service.py:689` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (data.net_worth.jan1) | -- | -- |
| `year_summary_principal_paid` | svc:`_compute_debt_progress`@`year_end_summary_service.py:824` | -- | route:`year_end_tab`@`analytics.py:171` | `analytics/_year_end.html` (debt section) | -- | -- |
| `ytd_contributions` | svc:`calculate_investment_inputs`@`investment_projection.py:100`; svc:`project_balance`@`growth_engine.py:164` | `InvestmentParams.annual_contribution_limit`@`investment_params.py:84`; `InvestmentParams.contribution_limit_year`@`investment_params.py:85` | route:`dashboard`@`investment.py:63` (limit_info.ytd @173-181) | `investment/dashboard.html` (76) | -- | -- |

Sub-list: orphans by category.

Tokens with producers but no consumers (the implementation runs but no page or chart consumes the
result): none after the index pass. Phase 2 should treat this as a load-bearing finding -- every
concept that the code computes is read somewhere.

Tokens with consumers but no producers (a render path reads a value no service produces): only
`transfer_amount_computed`, which is intentional (route-resident derivation; no service exists for
it, which is itself an SRP candidate per Q-12).

Tokens with neither producers nor consumers (purely orphan in the inventory):
`loan_principal_displayed` only, per 1.7.2.

### 1.7.4 Multi-path concepts requiring Phase 3 comparison

Multi-path concepts are tokens with two or more producers OR two or more consumers. Per audit plan
section 3.1, every multi-path concept becomes a required Phase 3 finding. The list below is derived
mechanically from the `*`-marked rows in 1.7.3.

Tier-1 concepts (3+ producers AND 3+ consumers; highest blast radius because divergence between any
pair propagates broadly):

- `account_balance` -- 7 service producers, 6 route consumers, 10 template render sites. Phase 3.1
  explicit comparison required: grid vs accounts vs savings vs dashboard vs investment for the same
  `(user_id, period_id, scenario_id)`. Anchor of the developer's reported symptom #5 (`/accounts` vs
  other pages divergence).
- `effective_amount` -- 13 service producers (incl. `Transaction.effective_amount` property and
  seven hand-rolled mirrors), every cell-render route, 8 template render sites, plus the only two
  money SQL aggregates (1.6). Phase 3.1 must cross-check every `actual_amount`/`estimated_amount`
  direct read against the property semantics (4-tier branching: `is_deleted`,
  `status.excludes_from_balance`, `actual_amount`, `estimated_amount`).
- `monthly_payment` -- 10 service producers including 14 `calculate_monthly_payment` call sites
  (A-05 lists 8; P1-b's grep found 6 more in adjacent fallback branches per Q-09), 7 route
  consumers, 11 template render sites. Phase 3.1 must verify all 14 call sites receive the same
  `(current_principal, current_rate, remaining_months)` triple for the same loan-on-date (E-02 ARM
  stability invariant).
- `period_subtotal` -- 10 service producers, 6 route consumers (including Q-10 grid inline @263-279
  and Q-12 obligations Q-12 inline @331-408), 6 template render sites, plus `chart_variance.js:69`
  (E-17 candidate JN-03). Phase 3.1 must compare grid subtotals against dashboard's
  `_compute_spending_comparison` and variance/calendar/obligations for the same period.

Tier-2 concepts (2 producers and/or 2 consumers; routine multi-path):

- `apy_interest` -- 4 producers, 1 route, 2 templates. Single-page comparison:
  `accounts/interest_detail.html` vs `savings/dashboard.html`.
- `chart_balance_series` -- 6 producers across engines, 7 route consumers, 6+ template/JS consumers.
  Phase 3 must verify the array each chart receives matches the server-computed balance series at
  the same time points.
- `debt_total` -- 6 producers (3 services + 3 specialized), 3 routes, 3 templates. Compare
  `_debt_summary` widget on dashboard vs savings card vs debt_strategy.
- `dti_ratio` -- 2 producers, 2 routes, 2 templates. Compare dashboard vs savings for same user.
- `employer_contribution` -- 7 producers, 1 route, 1 template. Single-consumer; the producer
  multiplicity is what Phase 3 checks (does `growth_engine.calculate_employer_contribution` agree
  with `paycheck_calculator.calculate_paycheck`'s employer line).
- `entry_remaining` -- 5 producers, 4 routes, 5 templates. Q-08 hinges here: should settled
  transactions use `actual_amount` or `estimated_amount` for over-budget display? Phase 3 verifies
  template subtractions match `compute_remaining`.
- `entry_sum_total` -- 3 producers, 3 routes, 4 templates, plus the one money SQL aggregate (1.6).
  Phase 3 verifies that envelope SQL sum matches per-template entry sum.
- `escrow_per_period` -- 6 producers, 4 routes, 5 templates. A-06 anchor (escrow subtraction
  preprocessing); Phase 3 verifies the dashboard escrow display matches the schedule's escrow
  attribution.
- `federal_tax` -- 8 producers, 7 routes, 5 templates. Calibration vs bracket-based paths split
  (Q-13 inline taxable derivation); Phase 3 verifies both paths produce the same `federal_tax` for
  the same inputs.
- `fica` -- 5 producers, 6 routes, 4 templates. Same calibration/bracket split.
- `goal_progress` -- 6 producers, 4 routes, 4 templates. Compare dashboard savings widget vs
  `/savings` panel vs companion `pct` inline (`companion.py:53-56`).
- `growth` -- 9 producers, 6 routes, 5 templates. Wide concept (return rate, inflation, raise
  growth, trend pct); Phase 3 must distinguish sub-concepts before comparing.
- `interest_paid_per_period` -- 5 producers, 3 routes, 3 templates. Compare schedule rows vs
  year-end mortgage interest aggregate (A-06).
- `interest_saved` -- 2 producers, 3 routes, 3 templates. Compare amortization payoff results vs
  debt strategy results for the same loan.
- `loan_principal_real` -- 6 producers, 5 routes, 5 templates. A-04 dual policy anchor; Phase 3
  cross-checks every page that displays principal (`/accounts/<id>/loan` vs refinance vs debt
  strategy).
- `loan_principal_stored` -- 6 producers, 4 routes, 3 templates. A-04 ARM anchor; cross-page
  consistency for ARM stored vs displayed.
- `paycheck_breakdown` -- 3 producers, 6 routes, 5 templates. Compare list_profiles vs breakdown vs
  projection vs calibrate flows for the same profile and period.
- `paycheck_gross` -- 5 producers, 7 routes, 5 templates. Raise sequencing (`_apply_raises`) is the
  largest variance source; Phase 3 verifies the same gross is produced by every entry path for the
  same profile on the same date.
- `paycheck_net` -- 4 producers, 4 routes, 5 templates. End-of-pipeline; should match across all
  rendering sites if intermediate concepts agree.
- `payoff_date` -- 5 producers, 4 routes, 4 templates. Compare loan dashboard vs payoff results vs
  refinance vs debt strategy.
- `principal_paid_per_period` -- 4 producers, 3 routes, 4 templates. Schedule consistency check.
- `projected_end_balance` -- 6 producers, 7 routes, 7 templates + 3 JS render paths. E-04 invariant
  anchor; same-number-on-every-page requirement.
- `savings_total` -- 3 producers, 4 routes, 3 templates. Compare savings dashboard vs retirement gap
  vs year-end summary.
- `taxable_income` -- 6 producers, 4 routes, 2 templates. Q-13 anchor (route-inline vs
  breakdown.taxable_income).
- `total_interest` -- 5 producers, 5 routes, 4 templates. Compare amortization vs year-end vs debt
  strategy.
- `transfer_amount` -- 8 producers, 7 routes, 3 templates. Compare `Transfer.amount` stored vs
  `transfer_service` mutations vs year-end summary.

Single-path tokens (1 producer AND 1 consumer; lowest risk; Phase 3 only verifies the cited site is
correct internally):

- `cash_runway_days`, `checking_balance` (functions as alias for account_balance in most contexts;
  Phase 3 must verify this aliasing is intentional), `chart_date_labels` (always parallel to
  chart_balance_series), `contribution_limit_remaining`, `emergency_fund_coverage_months`,
  `loan_remaining_months`, `months_saved`, `net_worth`, `pension_benefit_annual`,
  `pension_benefit_monthly`, `post_tax_deduction`, `pre_tax_deduction`, `state_tax`,
  `transfer_amount_computed`, `year_summary_*` family (each token is a single producer feeding a
  single route).

### 1.7.5 QC summary across Phase 1 sessions

| Session | Layers | QC logged? | Sample size | Failures | Re-dispatches | Notes |
| ------- | ------ | ---------- | ----------- | -------- | ------------- | ----- |
| P1-a | 1.5 + 1.6 | informal | per-column read | none recorded | 0 | Sessions preceded the formal QC protocol; the column-by-column inventory pattern is mechanical and produced no agent-classification ambiguity (113 numeric columns + 6 `@property` accessors). |
| P1-b | 1.1 | informal | ~15 cited-line spot-checks against the largest two files (`year_end_summary_service.py` 2248 lines; `savings_dashboard_service.py` 956 lines) | ~20% line-citation drift in `year_end_summary_service.py` body citations; ~5% in `savings_dashboard_service.py` | 0 (corrected inline) | Self-review captured in section 1.1 "Citation-quality note" with verified/unverified split. Function-definition lines reliable everywhere; body-line citations were re-verified at all load-bearing sites (A-02 through A-07, money SQL aggregates, transfer-shadow guards, eight A-05 monthly-payment sites and six additional sites). |
| P1-c | 1.2 | yes | 45 (15 per Group A/B/C, 8+ from files >800 LOC) | 0 line-drift outside +/-2 tolerance; 3 inline-classification fixes (grid.py:164 Inline=YES, transactions.py:909/987 Inline=NO); 1 scope misclassification (`dashboard.mark_paid` originally out-of-scope, re-classified IN scope) | 0 | Systematic off-by-1 in 3 of 45 samples on decorator-line citations in `retirement.py`, `salary.py`, `templates.py` (Phase 3 should treat route-layer citations as range `[cited-1, cited+2]`). |
| P1-d | 1.3 + 1.4 | yes | 16 (templates) + 15 (JS) = 31 samples; both biased toward "arithmetic-YES" / "numeric-work-YES" rows | Templates: 4/16 = 25% classification misses; numeric-work-YES subsample 3/8 = 38%. JS: 3/15 = 20%; numeric-YES subsample 3/8 = 38%. All misses fall into a single systematic over-flagging class (comparisons and bounds-checks classified as arithmetic) | 0 (corrected inline with explicit rationale at sections 1.3.0 and 1.4.0) | Systematic-error class documented (Explore over-flags `{% if value > 0 %}` and bounds-checking conditionals as arithmetic). After correction, the actual arithmetic-in-Jinja count drops from 13 to 11, and JS E-17 candidates drop from 7 (including borderlines) to 3 strict + 3 borderline. |
| P1-e | 1.7 wrap-up | n/a | inventory consistency audit only (Task 1: orphan token, QC-asymmetry, cross-references); index spot-checks per 1.7.5 verification | 1 orphan vocabulary token (`loan_principal_displayed`); zero body tokens missing from vocab; zero duplicate entries | 0 | This session does not produce new layer content; it produces 1.7.1 through 1.7.6. Spot-check verification recorded in 1.7.7. |
| P1-f | 1.1 re-verify (arithmetic classification only) | yes | 29 HIGH-RISK rows source-read exhaustively + 15 LOW-RISK engine rows sampled = 44 of ~84 candidates verified against actual source | HIGH-RISK: 6/29 (~21%) reclassified non-arithmetic (`dashboard_service._compute_alerts`/`_get_balance_info`, `budget_variance_service._compute_actual`, `calendar_service._build_day_entry`/`_compute_month_end_balance`, `year_end_summary_service.py:1465-1469` ARM anchor); 1/29 borderline KEPT+flagged (`retirement_dashboard_service.compute_slider_defaults` SWR `* _PCT_SCALE`). LOW-RISK: 0/15 fail (partition accepted, not promoted). | 0 (corrected inline per affected file) | Section 1.1 has no discrete arithmetic column; the P1-d error class manifests here as concept-token **producer over-attribution** (compare/read/delegate functions listed as money-token producers). Caveat 1.7.6 (1)'s named suspect `amount_to_monthly` verified GENUINE arithmetic. year_end cross-check (caveat 1.7.6 (2)): 380/414/475/636/824 def-lines EXACT and in P1-b verified list; 518-528 range loose by 1 (518 is `func.count`); 1465-1469 line numbers OK but enclosing-fn mis-attributed (`_balance_from_schedule_at_date` -> actually `_generate_debt_schedules`@1421); 824 signature-column drift. Full log at 1.7.8. |

### 1.7.6 Known caveats for Phase 2 and Phase 3 consumers

Phase-2 and Phase-3 sessions reading this inventory should be aware of these caveats before drawing
structural conclusions.

1. **Systematic over-flagging of comparisons / reads as
   arithmetic (P1-d affected; P1-b section 1.1 RE-VERIFIED by
   P1-f 2026-05-15).** Explore-T (templates) and Explore-J (JS)
   over-flagged `{% if value > 0 %}` conditionals, `|abs` /
   `|min` / `|max` filters, `.toFixed()`-style formatting, and
   bounds-checking conditionals as arithmetic; P1-d corrected
   these inline (sections 1.3.x.3 / 1.4.x.3 / 1.3.x.4;
   systematic-error class at 1.3.0 and 1.4.0). The open question
   was whether P1-b's section 1.1 carried the same class.
   **Resolved by P1-f.** Section 1.1 has NO discrete arithmetic
   column (unlike 1.3.x/1.4.x); its implicit "performs
   arithmetic" classification is the concept-token producer
   attribution plus the "What it does" framing, so the error
   class manifests as concept-token **producer over-attribution**
   -- functions that only compare, read, or delegate listed as
   producers of money/balance tokens. P1-f source-read all 29
   HIGH-RISK suspect rows (suspect-named functions plus every
   computational row in the display/aggregation files) and
   sampled 15 LOW-RISK calculation-engine rows. Result:
   false-positive rate **6/29 (~21%) in the HIGH-RISK suspect
   partition, 0/15 in the LOW-RISK engine partition**. The six
   false positives -- `dashboard_service._compute_alerts` and
   `_get_balance_info`, `budget_variance_service._compute_actual`,
   `calendar_service._build_day_entry` and
   `_compute_month_end_balance`, and the
   `year_end_summary_service.py:1465-1469` ARM anchor -- were
   relocated inline to per-file "Conditional on financial value"
   / "Non-arithmetic" lists with original citations preserved.
   One borderline (`retirement_dashboard_service.compute_slider_defaults`,
   SWR `* _PCT_SCALE` rate-to-percentage display) is KEPT as
   arithmetic with an inline Phase-3-adjudication flag. The
   suspect specifically named in the prior version of this caveat,
   `amount_to_monthly`, was verified to be GENUINE money
   arithmetic (multiply/divide of a money amount), NOT a false
   positive. **Phase 3 instruction (changed):** Phase 3 MAY rely
   on the section 1.1 arithmetic classification as re-verified by
   P1-f rather than re-applying the rules from scratch. Residual
   risk: the LOW-RISK calculation-engine partition was
   sample-verified (0 failures in 15 of ~55 rows; the strict
   accept threshold of 0-1 was met so full re-verification was
   not triggered) -- a Phase 3 finding that turns on a LOW-RISK
   engine row's exact body citation should still spot-check that
   one row. Borderline rows flagged inline still require Phase 3
   adjudication.

2. **QC asymmetry: P1-a and P1-b preceded the formal QC protocol
   introduced in P1-c.** Their line-citation accuracy is uneven:
   P1-a's column-level inventory is mechanical and reliable; P1-b's
   function-definition citations are reliable everywhere, but
   body-line citations in `year_end_summary_service.py` (2248 lines)
   had a ~20% spot-check error rate that was corrected in the
   P1-b self-review. Phase 3 should spot-check any P1-b row before
   quoting its body-line citation as evidence in a finding, and
   should explicitly verify any body citation in
   `year_end_summary_service.py` that was NOT listed in P1-b's
   verified citations sub-list at the end of section 1.1.
   **P1-f fold-in (2026-05-15):** P1-f re-checked every
   arithmetic-classified `year_end_summary_service.py` row against
   source. Def-line citations `:380`, `:414`, `:475`, `:636`,
   `:824` are EXACT (0 drift) and were already in P1-b's verified
   sub-list. Three residual citation-quality issues, all recorded
   inline in the file's P1-f block: (a) the `:518-528` SQL-aggregate
   range is loose by one line -- 518 is `db.func.count`, the two
   money sums are precisely 519 and 520-528; (b) the `:1465-1469`
   ARM anchor has accurate line numbers but is attributed to
   `_balance_from_schedule_at_date` when the enclosing def is
   `_generate_debt_schedules` (line 1421); (c) `_compute_debt_progress`
   `:824` has a Signature-column drift (actual signature
   `(year, debt_accounts, debt_schedules, ...)`). Phase 3 should
   trust the line numbers over the function-name attribution for
   the ARM anchor.

3. **Cross-reference completeness across sections.** Sections 1.2,
   1.3, and 1.4 cite their upstream entries by name and file:line
   (route -> service, template -> route, JS -> service/route).
   Sections 1.5 and 1.6 stand alone -- downstream sections do not
   always cite back to specific model columns or aggregate sites.
   Phase 2 should walk concept tokens from sections 1.1-1.4 back to
   the 1.5 columns and 1.6 aggregates that underlie them; the
   1.7.3 index makes this walk straightforward.

4. **Resolved Phase-0 questions (A-01 through A-07 in
   `00_priors.md`).** These define behavioral expectations the
   inventory documents but does not enforce. For example:
   - A-01 (canonical rounding rule) is verified per-function in
     the 1.1 quantization column; Phase 3 must check the
     boundary-quantize claim at every consumer site, not just at
     the producer.
   - A-02 (carry-forward envelope settle-and-roll) is verified at
     the service level; Phase 3 must verify that no other branch
     (discrete, transfer, manual) accidentally invokes
     `_settle_source_and_roll_leftover` semantics.
   - A-04 (ARM stored vs fixed-rate engine-walked principal) is
     verified in `amortization_engine.py:977-985`; Phase 3 must
     cross-check every page that displays principal (six pages
     listed in `loan_principal_real` / `loan_principal_stored`
     rows of the 1.7.3 index).
   - A-05 (eight monthly_payment call sites) is expanded to
     fourteen by P1-b's grep; six additional sites in adjacent
     fallback branches (Q-09 question). Phase 3 must verify the
     invariant against all fourteen, with the six fallback-branch
     sites separately scrutinized.
   - A-06 (year-end mortgage interest pipeline) is verified across
     `loan_payment_service.py:263-353` (preprocessing) and
     `year_end_summary_service.py:380-408` (aggregation).
   - A-07 (carry-forward three-branch partition) is verified in
     `carry_forward_service.py:273-277` and the bulk-update
     pattern at lines 405-437; Phase 3 must verify each branch's
     output matches the expectations documented in
     `prod_readiness_v1` (discrete) and `carry_fwd_impl`
     (envelope).

5. **Q-NN open questions raised by Phase 1.** Q-08 (P1-b),
   Q-09 (P1-b), Q-10 through Q-14 (P1-c) are awaiting developer
   answers before Phase 3 can adjudicate the relevant findings.
   Phase 2 (concept catalog) should explicitly note in each
   concept's primary-path entry that the answer to the question
   determines which implementation is canonical. The questions
   are filed in `09_open_questions.md`.

> Developer note: the targeted re-verification proposed here was RUN as session P1-f on
> 2026-05-15 (QC log 1.7.8). The systematic-classification-error risk for section 1.1 is now
> characterised, not merely flagged: a ~21% false-positive rate in the HIGH-RISK suspect partition
> (six rows relocated inline) and 0/15 in the sampled LOW-RISK engine partition. Caveat 1.7.6 (1)
> has been rewritten from "Phase 3 must re-apply the rules" to "Phase 3 may rely on the
> P1-f-re-verified classification, with the sampled-LOW-RISK residual and the inline borderline
> flags as the only remaining adjudication items." No further pre-Phase-2 re-verification session
> is needed unless the developer wants the full LOW-RISK engine partition exhaustively re-read
> rather than sampled.

### 1.7.7 P1-e verification spot-checks

Per the prompt's verification protocol, five concept-to-locations index rows were sampled at random
and re-verified by Read against the cited file:line. Results:

| # | Token sampled | Citation checked | Verdict |
| - | ------------- | ---------------- | ------- |
| 1 | `monthly_payment` | `calculate_monthly_payment`@`amortization_engine.py:178` | OK (function definition at line 178 per P1-b verified-citations sub-list at end of section 1.1). |
| 2 | `effective_amount` | `Transaction.effective_amount`@`transaction.py:221-245` | OK (P1-a section 1.5 cites the same range; property body at lines 221-245). |
| 3 | `escrow_per_period` | `calculate_monthly_escrow`@`escrow_calculator.py:14` | OK (P1-b Group A inventory at the file-defined function table). |
| 4 | `period_subtotal` inline | `index`@`grid.py:164` subtotal loop @263-279 | OK (Q-10 cites the same range; P1-c QC log inline correction confirmed the inline arithmetic). |
| 5 | `paycheck_breakdown` | `calculate_paycheck`@`paycheck_calculator.py:92` | OK (P1-b Group A inventory and P1-c route consumers all align). |

Greppability spot-check: every controlled-vocabulary token from 1.7.2 plus the index in 1.7.3 was
confirmed grep-able in this file (`grep -c <token>`); the orphan `loan_principal_displayed` returns
exactly one hit (its definition line). No body token uses a name that isn't in the vocabulary.

Phase 1 is complete after this section is written. The output file is `01_inventory.md`. Phase 2
(concept catalog) begins in a separate session with this file as primary input; the caveats in 1.7.6
are required reading.

### 1.7.8 P1-f arithmetic re-verification QC log

(The session prompt named this subsection "1.7.7"; 1.7.7 was already written by
P1-e for its verification spot-checks, so the P1-f log is filed as 1.7.8 to
avoid clobbering P1-e content. The numbering is the only deviation.)

Session P1-f, 2026-05-15. Single purpose: re-verify the "performs arithmetic on
financial values" classification in section 1.1 (Services), correcting the
systematic over-flagging documented in caveat 1.7.6 (1), and folding in the
`year_end_summary_service.py` line-drift cross-check from caveat 1.7.6 (2).
Read-only audit; source files, tests, and migrations untouched.

**Structural note (material -- read before consuming this log).** Section 1.1
does NOT have a discrete "Arithmetic (YES/NO)" column the way sections 1.3.x
(`Arithmetic in Jinja`) and 1.4.x (`Numeric work`) do. Its columns are
`file:line | Signature | Returns | What it does | Concept token(s) | ... |
Quantization | Calls`. The "classification that marks a row as performing
arithmetic on financial values" was therefore operationalised as: **the row's
`What it does` description asserts the function itself performs an arithmetic
operation (sum, subtract, multiply, divide, formula, accrue, convert-by-factor)
producing/transforming a financial concept token, AND/OR the 1.7.3 index lists
the function as a `svc:` producer of a money/rate token.** Pure
query/lookup/CRUD/state/date/log/resolver rows with concept token `-` and
structural descriptions were treated as never-classified-as-arithmetic and left
out of scope (per the scope guardrail forbidding re-classification of
non-arithmetic rows). Consequence: the P1-d error class manifests in 1.1 not as
a binary-column over-flag but as **concept-token producer over-attribution** --
a function that only compares / reads / delegates is listed (in the per-file
table's token column and in the 1.7.3 producer column) as a producer of a
money/balance token. That is the section-1.1 analogue of P1-d's
"comparison-flagged-as-arithmetic" and is exactly what the six relocations
below correct. This operational definition is the honest adaptation of the
task to 1.1's actual structure; Phase 3 should read the relocations with it in
mind.

**Candidate set.** ~84 rows (per-file table rows the inventory frames as the
function itself computing a financial value; pure delegators/orchestrators with
"delegates"/"assembles" descriptions and structural `-`-token rows excluded as
never-classified-as-arithmetic). Partitioned per the prompt's risk rule.

| Partition | Rows | Verified how | False positives | Borderline kept+flagged | year_end line/attribution fixes | Action |
| --------- | ---- | ------------ | --------------- | ----------------------- | ------------------------------- | ------ |
| HIGH-RISK (suspect-named fns + every computational row in `dashboard_service`, `savings_dashboard_service`, `retirement_dashboard_service`, `year_end_summary_service`, `budget_variance_service`, `calendar_service`, `spending_trend_service`, plus the `carry_forward` decision-tree guard and `calibration.derive_effective_rates`) | 29 | Exhaustive: every cited `file:line` read at source with a +/-6 window (<=40 rows, so no Explore raw-fetch needed) and the prompt's rule table applied mechanically | 6 (`dashboard_service._compute_alerts` @252, `_get_balance_info` @334; `budget_variance_service._compute_actual` @381; `calendar_service._build_day_entry` @240, `_compute_month_end_balance` @435; `year_end_summary_service` ARM anchor @1465-1469) | 1 (`retirement_dashboard_service.compute_slider_defaults` @257 -- genuine balance-weighted average KEPT; SWR `* _PCT_SCALE` percentage-display flagged) | 3 recorded (518-528 range loose by 1; 1465-1469 enclosing-fn mis-attributed to `_balance_from_schedule_at_date`, actual `_generate_debt_schedules`@1421; 824 signature-column drift). Def-lines 380/414/475/636/824 EXACT and in P1-b verified list. | Relocated the 6 false positives inline to per-file "Conditional on financial value" / "Non-arithmetic" lists with original citations preserved; flagged the 1 borderline inline; recorded the year_end citation-quality issues in the file's P1-f block and folded into caveat 1.7.6 (2). |
| LOW-RISK (recognised calculators in `amortization_engine`, `growth_engine`, `interest_projection`, `tax_calculator`, `paycheck_calculator`, `loan_payment_service`, `balance_calculator`, plus `debt_strategy`, `savings_goal` recognised calcs and Group C entry/settle sum helpers) | ~55 | Sampled 15 spanning every engine file (incl. all section-1.1 `calculate_monthly_payment` sites for the Task-4 completeness check), read at source, rule table applied | 0/15 | 0 | n/a | Accepted as correctly classified (strict threshold 0-1 met); partition NOT promoted to exhaustive. A Phase 3 finding turning on a LOW-RISK engine row's exact body citation should still spot-check that one row (residual recorded in caveat 1.7.6 (1)). |

**HIGH-RISK per-row verdicts (all source-read).** Genuine arithmetic, KEEP
(operator + financial value confirmed at the cited line): `calibration_service.py:34`
`derive_effective_rates` (`federal / taxable` etc., division, lines 83-95);
`dashboard_service.py:203` `_entry_progress_fields` (`total = debit + credit`,
238) and `:375` `_compute_cash_runway` (`sum(abs(...))` 411, `/ daily_avg`
415-416); `savings_dashboard_service.py:802` `_compute_debt_summary` (`+=`, `*`,
`/` 851-863); `budget_variance_service.py:99` `compute_variance` (`total_act -
total_est` 139), `:358` `_build_txn_variance` (`actual - estimated` 367),
`:396` `_pct` (`variance / estimated * _HUNDRED` 404); `savings_goal_service.py:199`
`amount_to_monthly` (`amount * 26/12`, `amount / 3`, ... 264-281 -- the suspect
NAMED in caveat 1.7.6 (1), verified GENUINE); `calendar_service.py:270`
`_assign_transactions_to_days` (`+=` 302, 304); `carry_forward_service.py:602`
`_resolve_envelope_target_fields` (`target_row.estimated_amount + leftover`
686, `canonical_default + leftover` 750 -- the inventory description
"decision tree / leftover precomputed" understated it, but the classification
as arithmetic-bearing is correct, so KEEP); `year_end_summary_service.py:380`
(`total_interest += row.interest` 406), `:414` (sums `effective_amount` 457 per
P1-b verified), `:475`/`:518-528` (`db.func.sum`), `:636` (`total_amount +=
t.amount` 679), `:824` (`principal_paid = jan1_bal - dec31_bal` 871);
`spending_trend_service.py:97`/`:265`/`:296`/`:360`/`:470` (regression / `+=`
totals / weighted `sum(... * ...) / total` / `(last - first) / first *
_HUNDRED`; `:296`, `:360`, `:470` source-read, `:97`/`:265` confirmed via the
call graph into the verified helpers).

Reclassified NON-arithmetic (relocated inline, original citation preserved):
`dashboard_service.py:252` `_compute_alerts` (only `bal < _ZERO` 298,
`current_bal < Decimal(str(low_threshold))` 314 -- comparisons; the sole
subtraction is non-financial date math 281); `dashboard_service.py:334`
`_get_balance_info` (reads + `_compute_cash_runway` delegate + date compare);
`budget_variance_service.py:381` `_compute_actual` (conditional selection of
`actual_amount` / `estimated_amount`, no operator);
`calendar_service.py:240` `_build_day_entry` (`amount = effective_amount`
read, `abs(amount) >= threshold` bare-abs + compare);
`calendar_service.py:435` `_compute_month_end_balance` (period lookup +
`calculate_balances` delegate + dict read);
`year_end_summary_service.py:1465-1469` ARM anchor (`Decimal(str(
params.current_principal)) if params.is_arm else None` -- type-normalize +
conditional anchor read per A-04). True-negative confirmed clean (was already
framed as a read, no relocation): `dashboard_service.py:167` `txn_to_bill_dict`.

**Task 4 -- monthly_payment completeness (the only false-negative check).**
A-05/Q-09's call-site set, restricted to section 1.1's scope: definition
`amortization_engine.py:178` (annuity formula `principal * (monthly_rate *
factor) / (factor - 1)` at line 196 -- genuine), and the service-layer call
sites `:436, :440, :491, :512` (in `generate_schedule`@326), `:693, :697` (in
`calculate_summary`@649), `:952, :957` (in `get_loan_projection`@864),
`balance_calculator.py:225, :231` (in `calculate_balances_with_amortization`@176),
`loan_payment_service.py:251, :256` (in `compute_contractual_pi`@233). All were
source-confirmed present and enclosed by functions classified as arithmetic;
none were wrongly dropped by the systematic bias. The remaining A-05/Q-09 sites
(`app/routes/loan.py:1102, 1225, 1231`) are section 1.2 and out of this
session's scope. No Q-09-linked finding: the bias did not drop a real
monthly_payment site from the 1.1 arithmetic classification.

**Verification before declaring complete.** (a) Re-grepped section 1.1; five
remaining arithmetic rows spot-re-read at random -- `amortization_engine.py:178`
(annuity, op present), `balance_calculator.py:292` `_entry_aware_amount`
(`estimated_amount - cleared_debit - sum_credit` inside `max`, 383-385, op
present), `tax_calculator.py:173` `_apply_marginal_brackets` (`total_tax +=
amount_in_bracket * rate`, 207, op present), `paycheck_calculator.py:329`
`_apply_single_raise` (`salary * (1 + pct)`, 333, op present),
`growth_engine.py:91` `calculate_employer_contribution` (`gross * pct`, 114, op
present) -- all genuinely contain an arithmetic operator on a financial value
per the rule table. (b) Every relocated row retains its original `file:line`
citation in its new per-file list. (c) The 14 (A-05/Q-09) monthly_payment
sites: the 12 in section-1.1 scope all resolve to an arithmetic classification;
the 3 route sites are section 1.2 (out of scope) -- no finding. (d) `wc -l`
confirms `01_inventory.md` changed (3012 -> 3313 lines).

No new behavioral ambiguity (Q-NN) surfaced: every P1-f decision was a
classification call against the rule table, not a "what is this code intended
to do" question. The one structural ambiguity (section 1.1 has no arithmetic
column) is a property of the audit document, not the codebase, and is resolved
above by the documented operational definition rather than a developer
question.

Phase 1 remains complete. P1-f is a post-Phase-1 remediation of section 1.1's
arithmetic classification only; Phase 2 begins in a separate session with this
file as primary input and the updated caveats in 1.7.6 as required reading.
